"""
routes/project.py
------------------
Project Module: HR can create, edit, delete and assign projects.
Project Managers can view their assigned projects and update status
on the ones assigned to them.
"""

from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Project, Department, SubDepartment, ProjectManager, Intern, ProjectSubmission
from utils import roles_required, current_pm_profile, notify_user, log_action, now_pkt, hr_city_scope, PIA_CITIES
from services.email_service import send_project_assignment_email

project_bp = Blueprint("project", __name__, url_prefix="/projects")


def _get_or_create_department(name, city=None):
    """Module 1: Manual Department & Station entry on Create/Edit
    Project. Looks up an existing Department by a case-insensitive
    name match (so "IT" and "it" don't create two rows); if none
    exists, creates one with the given name/station and immediately
    flushes it so it has an id to attach the project to, and so it
    shows up in every other Department dropdown from now on (Department
    is the single shared table every picker already reads from).
    """
    name = (name or "").strip()
    department = Department.query.filter(db.func.lower(Department.name) == name.lower()).first()
    if department:
        # Fill in a station for a pre-existing department that didn't
        # have one yet, but never silently overwrite one that's already set.
        if city and not department.city:
            department.city = city
        return department

    department = Department(name=name, city=city or None, is_active=True)
    db.session.add(department)
    db.session.flush()
    return department


def _parse_date(value: str):
    """Parse an HTML date input (YYYY-MM-DD) into a date object."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_intern_ids(form):
    """Read the (possibly multi-valued) assigned_intern_ids field from a
    submitted form and return a de-duplicated list of ints. Accepts the
    older single-value field name too, for any form/link that still
    posts it."""
    raw_ids = form.getlist("assigned_intern_ids") or form.getlist("assigned_intern_id")
    ids = []
    for raw in raw_ids:
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            iid = int(raw)
        except ValueError:
            continue
        if iid not in ids:
            ids.append(iid)
    return ids


# ----------------------------------------------------------------------
# Listing (role-aware: HR sees all, Project Manager sees only their own)
# ----------------------------------------------------------------------
@project_bp.route("/")
@login_required
@roles_required("Station HR", "Project Manager", "Super Admin")
def list_projects():
    """Show projects. Super Admin sees everything; Station HR sees only
    projects in their assigned city; a Project Manager only sees
    projects assigned to them."""

    query = Project.query
    is_city_scoped, hr_city = hr_city_scope()
    if is_city_scoped:
        query = query.join(Department).filter(Department.city == hr_city)

    year = request.args.get("year", type=int)
    if year:
        from sqlalchemy import extract
        query = query.filter(extract("year", Project.created_at) == year)

    if current_user.role == "Project Manager":
        pm = current_pm_profile()
        if pm is None:
            flash("Your Project Manager profile could not be found.", "danger")
            return redirect(url_for("dashboard.index"))
        query = query.filter_by(assigned_manager_id=pm.id)
        # PM can only hand tasks to interns in their own department
        interns = Intern.query.filter_by(department_id=pm.department_id).order_by(
            db.func.lower(Intern.full_name)
        ).all()
    else:
        # Station HR / Super Admin filters via query string
        status = request.args.get("status")
        department_id = request.args.get("department_id")
        sub_department_id = request.args.get("sub_department_id")

        if status:
            query = query.filter_by(status=status)
        if department_id:
            query = query.filter_by(department_id=department_id)
        if sub_department_id:
            query = query.filter_by(sub_department_id=sub_department_id)

        interns = Intern.query
        if is_city_scoped:
            interns = interns.filter(Intern.station == hr_city)
        interns = interns.order_by(db.func.lower(Intern.full_name)).all()

    projects = query.order_by(Project.created_at.desc()).all()
    departments = Department.query.order_by(db.func.lower(Department.name)).all()
    managers = ProjectManager.query.filter_by(is_active_flag=True).order_by(
        db.func.lower(ProjectManager.full_name)
    ).all()
    if is_city_scoped:
        departments = [d for d in departments if d.city == hr_city]
        managers = [m for m in managers if m.city == hr_city]
    from sqlalchemy import extract
    available_years = sorted(
        {
            y[0]
            for y in db.session.query(extract("year", Project.created_at)).distinct().all()
            if y[0] is not None
        },
        reverse=True,
    )

    return render_template(
        "projects/list.html",
        projects=projects,
        departments=departments,
        interns=interns,          # <-- new
        managers=managers,
        statuses=Project.STATUSES,
        filters=request.args,
        available_years=available_years,
    )

# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------
@project_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required("Station HR", "Project Manager", "Super Admin")
def add_project():
    """Create a new project.

    HR can create a project for any department and assign any Project
    Manager and Intern. A Project Manager can also create a task of
    their own -- it is automatically scoped to their own department and
    assigned to themself as manager, and they may only hand it to an
    intern within their own department.
    """
    is_pm = current_user.role == "Project Manager"
    pm = current_pm_profile() if is_pm else None
    is_city_scoped, hr_city = hr_city_scope()

    if is_pm and pm is None:
        flash("Your Project Manager profile could not be found.", "danger")
        return redirect(url_for("dashboard.index"))

    # Pre-selected City for the City -> Department -> PM -> Interns
    # cascading picker: a city-scoped HR only ever has one city, so it's
    # implicit; otherwise nothing is pre-selected on a fresh Create form.
    selected_city = hr_city if is_city_scoped else None

    if is_pm:
        departments = Department.query.filter_by(id=pm.department_id).all()
        managers = [pm]
        interns = Intern.query.filter_by(department_id=pm.department_id).order_by(
            db.func.lower(Intern.full_name)
        ).all()
    else:
        departments = Department.query.filter_by(is_active=True).order_by(db.func.lower(Department.name)).all()
        managers = ProjectManager.query.filter_by(is_active_flag=True).order_by(
            db.func.lower(ProjectManager.full_name)
        ).all()
        interns = Intern.query.order_by(db.func.lower(Intern.full_name)).all()
        if is_city_scoped:
            departments = [d for d in departments if d.city == hr_city]
            managers = [m for m in managers if m.city == hr_city]
            interns = [i for i in interns if i.station == hr_city]

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        status = request.form.get("status", "Pending")
        start_date_raw = request.form.get("start_date", "")
        deadline_raw = request.form.get("deadline", "")

        sub_department_id = request.form.get("sub_department_id") or None

        # Module 1: Manual Department & Station entry. If HR typed a new
        # department name instead of picking one from the dropdown, that
        # takes priority over any selected department_id.
        new_department_name = request.form.get("new_department_name", "").strip()
        new_department_city = request.form.get("new_department_city", "").strip()

        if is_pm:
            department_id = pm.department_id
            manager_id = pm.id
            intern_ids = _parse_intern_ids(request.form)
        else:
            department_id = request.form.get("department_id")
            manager_id = request.form.get("assigned_manager_id") or None
            intern_ids = _parse_intern_ids(request.form)

        errors = []
        if not title:
            errors.append("Project title is required.")
        if not description:
            errors.append("Project description is required.")

        creating_new_department = not is_pm and bool(new_department_name)
        if creating_new_department:
            # A Station HR is confined to their own city, same as when
            # picking an existing department.
            if is_city_scoped:
                new_department_city = hr_city
            elif new_department_city and new_department_city not in PIA_CITIES:
                errors.append("Please select a valid station from the list, or leave it blank.")
        elif not department_id:
            errors.append("Department is required.")
        elif is_city_scoped:
            dept_obj = Department.query.get(department_id)
            if not dept_obj or dept_obj.city != hr_city:
                errors.append("You can only create projects for your assigned city.")

        sub_department_obj = None
        if sub_department_id and not creating_new_department:
            sub_department_obj = SubDepartment.query.get(sub_department_id)
            if not sub_department_obj or (
                department_id and sub_department_obj.department_id != int(department_id)
            ):
                errors.append("Please select a valid sub department for the chosen department.")
                sub_department_obj = None
        if is_pm:
            if status not in {"Pending", "Working", "Submitted"}:
                errors.append("Invalid status selected.")
        elif status not in Project.STATUSES:
            errors.append("Invalid status selected.")
        if not start_date_raw or not deadline_raw:
            errors.append("Start date and deadline are required.")

        # HR/Super Admin: enforce the City -> Department -> PM cascade
        # server-side too (mirrors project-assignment-cascade.js), so a
        # tampered/bypassed request can't assign a Project Manager who
        # doesn't actually belong to the chosen Department/City.
        manager_obj = None
        if not is_pm and manager_id and not creating_new_department and department_id:
            manager_obj = ProjectManager.query.get(manager_id)
            if not manager_obj:
                errors.append("Selected Project Manager could not be found.")
                manager_obj = None
            else:
                try:
                    dept_id_int = int(department_id)
                except (TypeError, ValueError):
                    dept_id_int = None
                if dept_id_int is not None and manager_obj.department_id != dept_id_int:
                    errors.append("The selected Project Manager does not belong to the chosen Department.")
                elif is_city_scoped and manager_obj.city != hr_city:
                    errors.append("You can only assign Project Managers within your assigned city.")

        # PMs may only hand the task to interns in their own department;
        # HR/Super Admin may only hand it to interns in the chosen
        # Department/City (and, if a Project Manager is picked, to
        # interns eligible for that specific PM).
        selected_interns = []
        if intern_ids:
            selected_interns = Intern.query.filter(Intern.id.in_(intern_ids)).all()
            if len(selected_interns) != len(intern_ids):
                errors.append("One or more selected interns could not be found.")
            if is_pm:
                for intern in selected_interns:
                    if intern.department_id != pm.department_id:
                        errors.append("You can only assign interns from your own department.")
                        break
            elif not creating_new_department and department_id:
                try:
                    dept_id_int = int(department_id)
                except (TypeError, ValueError):
                    dept_id_int = None
                eligible_department_id = manager_obj.department_id if manager_obj else dept_id_int
                eligible_city = manager_obj.city if manager_obj else (hr_city if is_city_scoped else None)
                for intern in selected_interns:
                    if eligible_department_id is not None and intern.department_id != eligible_department_id:
                        errors.append("You can only assign interns from the selected Department/Project Manager.")
                        break
                    if eligible_city and intern.station != eligible_city:
                        errors.append("You can only assign interns from the selected City.")
                        break

        start_date = deadline = None
        if start_date_raw and deadline_raw:
            try:
                start_date = _parse_date(start_date_raw)
                deadline = _parse_date(deadline_raw)
                if deadline < start_date:
                    errors.append("Deadline cannot be before the start date.")
            except ValueError:
                errors.append("Invalid date format provided.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "projects/form.html",
                project=None,
                departments=departments,
                managers=managers,
                interns=interns,
                statuses=Project.STATUSES,
                form=request.form,
                selected_intern_ids=intern_ids,
                is_pm=is_pm,
                pia_cities=PIA_CITIES,
                is_city_scoped=is_city_scoped,
                hr_city=hr_city,
                selected_city=selected_city,
            )

        try:
            if creating_new_department:
                new_department = _get_or_create_department(new_department_name, new_department_city)
                department_id = new_department.id
                log_action(
                    action="CREATE",
                    description=f"Added department '{new_department.name}' via manual entry on Create Project.",
                    target_type="Department",
                    target_id=new_department.id,
                )

            project = Project(
                title=title,
                description=description,
                department_id=int(department_id),
                sub_department_id=sub_department_obj.id if sub_department_obj else None,
                assigned_manager_id=int(manager_id) if manager_id else None,
                status=status,
                start_date=start_date,
                deadline=deadline,
            )
            project.interns = selected_interns
            db.session.add(project)
            db.session.flush()
            log_action(
                action="CREATE",
                description=f"Created project '{title}'.",
                target_type="Project",
                target_id=project.id,
            )
            db.session.commit()

            if selected_interns:
                assigner = pm.full_name if is_pm else "Station HR"
                for assigned_intern in selected_interns:
                    notify_user(
                        assigned_intern.user_id,
                        f"You have been assigned to task '{title}' by {assigner}.",
                        icon="bi-kanban",
                        notification_type="Project Assigned",
                    )
                    db.session.commit()
                    send_project_assignment_email(
                        intern=assigned_intern, project=project, assigned_by=assigner
                    )

            flash(f"Project '{title}' created successfully.", "success")
            return redirect(url_for("project.list_projects"))
        except IntegrityError:
            db.session.rollback()
            flash("Could not create project due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to create project '%s'.", title)
            flash("Could not create project due to a system error. Please try again.", "danger")

    return render_template(
        "projects/form.html",
        project=None,
        departments=departments,
        managers=managers,
        interns=interns,
        statuses=Project.STATUSES,
        form=None,
        selected_intern_ids=[],
        is_pm=is_pm,
        pia_cities=PIA_CITIES,
        is_city_scoped=is_city_scoped,
        hr_city=hr_city,
        selected_city=selected_city,
    )


# ----------------------------------------------------------------------
# Edit (also used to assign / reassign manager & intern)
# ----------------------------------------------------------------------
@project_bp.route("/edit/<int:project_id>", methods=["GET", "POST"])
@login_required
@roles_required("Station HR", "Super Admin")
def edit_project(project_id):
    """Edit an existing project, including assignment fields. A Station
    HR may only edit projects belonging to their assigned city."""
    project = Project.query.get_or_404(project_id)
    is_city_scoped, hr_city = hr_city_scope()
    if is_city_scoped and (not project.department or project.department.city != hr_city):
        flash("You do not have permission to manage projects outside your assigned city.", "danger")
        return redirect(url_for("project.list_projects"))

    departments = Department.query.order_by(db.func.lower(Department.name)).all()
    managers = ProjectManager.query.filter_by(is_active_flag=True).order_by(
        db.func.lower(ProjectManager.full_name)
    ).all()
    interns = Intern.query.order_by(db.func.lower(Intern.full_name)).all()
    if is_city_scoped:
        departments = [d for d in departments if d.city == hr_city]
        managers = [m for m in managers if m.city == hr_city]
        interns = [i for i in interns if i.station == hr_city]

    # Pre-selected City for the cascading picker -- city-scoped HR is
    # implicitly locked to their own city, otherwise pre-fill from the
    # project's current department so re-opening Edit doesn't reset it.
    selected_city = hr_city if is_city_scoped else (project.department.city if project.department else None)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        department_id = request.form.get("department_id")
        sub_department_id = request.form.get("sub_department_id") or None
        manager_id = request.form.get("assigned_manager_id") or None
        intern_ids = _parse_intern_ids(request.form)
        status = request.form.get("status", "Pending")
        start_date_raw = request.form.get("start_date", "")
        deadline_raw = request.form.get("deadline", "")

        # Module 1: Manual Department & Station entry (same as Create Project).
        new_department_name = request.form.get("new_department_name", "").strip()
        new_department_city = request.form.get("new_department_city", "").strip()
        creating_new_department = bool(new_department_name)

        errors = []
        if not title:
            errors.append("Project title is required.")
        if not description:
            errors.append("Project description is required.")

        if creating_new_department:
            if is_city_scoped:
                new_department_city = hr_city
            elif new_department_city and new_department_city not in PIA_CITIES:
                errors.append("Please select a valid station from the list, or leave it blank.")
        elif not department_id:
            errors.append("Department is required.")
        elif is_city_scoped:
            dept_obj = Department.query.get(department_id)
            if not dept_obj or dept_obj.city != hr_city:
                errors.append("You can only assign projects to your assigned city.")

        sub_department_obj = None
        if sub_department_id and not creating_new_department:
            sub_department_obj = SubDepartment.query.get(sub_department_id)
            if not sub_department_obj or (
                department_id and sub_department_obj.department_id != int(department_id)
            ):
                errors.append("Please select a valid sub department for the chosen department.")
                sub_department_obj = None
        if status not in Project.STATUSES:
            errors.append("Invalid status selected.")
        elif status != project.status and not project.can_transition_to(status):
            errors.append(
                f"Cannot change status from '{project.status}' to '{status}'. "
                "Project status can only move forward, and a Completed project "
                "cannot be changed."
            )

        start_date = deadline = None
        if start_date_raw and deadline_raw:
            try:
                start_date = _parse_date(start_date_raw)
                deadline = _parse_date(deadline_raw)
                if deadline < start_date:
                    errors.append("Deadline cannot be before the start date.")
            except ValueError:
                errors.append("Invalid date format provided.")
        else:
            errors.append("Start date and deadline are required.")

        # Enforce the City -> Department -> PM cascade server-side too
        # (mirrors project-assignment-cascade.js), so a tampered/bypassed
        # request can't assign a Project Manager who doesn't actually
        # belong to the chosen Department/City.
        manager_obj = None
        if manager_id and not creating_new_department and department_id:
            manager_obj = ProjectManager.query.get(manager_id)
            if not manager_obj:
                errors.append("Selected Project Manager could not be found.")
                manager_obj = None
            else:
                try:
                    dept_id_int = int(department_id)
                except (TypeError, ValueError):
                    dept_id_int = None
                if dept_id_int is not None and manager_obj.department_id != dept_id_int:
                    errors.append("The selected Project Manager does not belong to the chosen Department.")
                elif is_city_scoped and manager_obj.city != hr_city:
                    errors.append("You can only assign Project Managers within your assigned city.")

        selected_interns = []
        if intern_ids:
            selected_interns = Intern.query.filter(Intern.id.in_(intern_ids)).all()
            if len(selected_interns) != len(intern_ids):
                errors.append("One or more selected interns could not be found.")
            elif not creating_new_department and department_id:
                try:
                    dept_id_int = int(department_id)
                except (TypeError, ValueError):
                    dept_id_int = None
                eligible_department_id = manager_obj.department_id if manager_obj else dept_id_int
                eligible_city = manager_obj.city if manager_obj else (hr_city if is_city_scoped else None)
                for intern in selected_interns:
                    if eligible_department_id is not None and intern.department_id != eligible_department_id:
                        errors.append("You can only assign interns from the selected Department/Project Manager.")
                        break
                    if eligible_city and intern.station != eligible_city:
                        errors.append("You can only assign interns from the selected City.")
                        break

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "projects/form.html",
                project=project,
                departments=departments,
                managers=managers,
                interns=interns,
                statuses=Project.STATUSES,
                form=request.form,
                selected_intern_ids=intern_ids,
                pia_cities=PIA_CITIES,
                is_city_scoped=is_city_scoped,
                hr_city=hr_city,
                selected_city=selected_city,
            )

        try:
            if creating_new_department:
                new_department = _get_or_create_department(new_department_name, new_department_city)
                department_id = new_department.id
                log_action(
                    action="CREATE",
                    description=f"Added department '{new_department.name}' via manual entry on Edit Project.",
                    target_type="Department",
                    target_id=new_department.id,
                )

            previous_intern_ids = {i.id for i in project.interns}
            previous_status = project.status

            project.title = title
            project.description = description
            project.department_id = int(department_id)
            project.sub_department_id = sub_department_obj.id if sub_department_obj else None
            project.assigned_manager_id = int(manager_id) if manager_id else None
            project.interns = selected_interns
            project.status = status
            project.start_date = start_date
            project.deadline = deadline

            # Notify interns of a new assignment or a status change.
            newly_assigned = [i for i in selected_interns if i.id not in previous_intern_ids]
            for assigned_intern in selected_interns:
                if assigned_intern.id not in previous_intern_ids:
                    notify_user(
                        assigned_intern.user_id,
                        f"You have been assigned to project '{title}'.",
                        icon="bi-kanban",
                        notification_type="Project Assigned",
                    )
                elif status != previous_status:
                    notify_user(
                        assigned_intern.user_id,
                        f"Project '{title}' status changed to {status}.",
                        icon="bi-kanban",
                    )

            log_action(
                action="UPDATE",
                description=f"Updated project '{title}'.",
                target_type="Project",
                target_id=project.id,
            )
            db.session.commit()

            for assigned_intern in newly_assigned:
                send_project_assignment_email(intern=assigned_intern, project=project, assigned_by="Station HR")

            flash(f"Project '{title}' updated successfully.", "success")
            return redirect(url_for("project.list_projects"))
        except IntegrityError:
            db.session.rollback()
            flash("Could not update project due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update project #%s.", project.id)
            flash("Could not update project due to a system error. Please try again.", "danger")

    return render_template(
        "projects/form.html",
        project=project,
        departments=departments,
        managers=managers,
        interns=interns,
        statuses=Project.STATUSES,
        form=None,
        selected_intern_ids=[i.id for i in project.interns],
        pia_cities=PIA_CITIES,
        is_city_scoped=is_city_scoped,
        hr_city=hr_city,
        selected_city=selected_city,
    )


# ----------------------------------------------------------------------
# Quick assign (lightweight modal action from the list page)
# ----------------------------------------------------------------------
@project_bp.route("/assign/<int:project_id>", methods=["POST"])
@login_required
@roles_required("Station HR", "Super Admin")
def assign_project(project_id):
    """Quickly assign/reassign a Project Manager and Interns to a project
    without opening the full edit form. A Station HR may only assign
    projects belonging to their assigned city."""
    project = Project.query.get_or_404(project_id)
    is_city_scoped, hr_city = hr_city_scope()
    if is_city_scoped and (not project.department or project.department.city != hr_city):
        flash("You do not have permission to manage projects outside your assigned city.", "danger")
        return redirect(url_for("project.list_projects"))

    manager_id = request.form.get("assigned_manager_id") or None
    intern_ids = _parse_intern_ids(request.form)

    selected_interns = []
    if intern_ids:
        selected_interns = Intern.query.filter(Intern.id.in_(intern_ids)).all()
        if len(selected_interns) != len(intern_ids):
            flash("One or more selected interns could not be found.", "danger")
            return redirect(url_for("project.list_projects"))

    previous_intern_ids = {i.id for i in project.interns}

    try:
        project.assigned_manager_id = int(manager_id) if manager_id else None
        project.interns = selected_interns

        newly_assigned = [i for i in selected_interns if i.id not in previous_intern_ids]
        for new_intern in newly_assigned:
            notify_user(
                new_intern.user_id,
                f"You have been assigned to project '{project.title}'.",
                icon="bi-kanban",
                notification_type="Project Assigned",
            )

        db.session.commit()
        flash(f"Project '{project.title}' assignment updated.", "success")

        for new_intern in newly_assigned:
            send_project_assignment_email(intern=new_intern, project=project, assigned_by="Station HR")
    except IntegrityError:
        db.session.rollback()
        flash("Could not update project assignment due to a database error.", "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to update assignment for project #%s.", project_id)
        flash("Could not update project assignment due to a system error. Please try again.", "danger")

    return redirect(url_for("project.list_projects"))

# ----------------------------------------------------------------------
# PM self-service: assign interns to one of their own projects
# ----------------------------------------------------------------------
@project_bp.route("/assign-intern/<int:project_id>", methods=["POST"])
@login_required
@roles_required("Project Manager")
def assign_intern_to_project(project_id):
    """Allow a Project Manager to assign/reassign the interns working on
    a project that is already assigned to them."""
    project = Project.query.get_or_404(project_id)
    pm = current_pm_profile()

    if pm is None or project.assigned_manager_id != pm.id:
        flash("You can only assign interns on projects assigned to you.", "danger")
        return redirect(url_for("project.list_projects"))

    intern_ids = _parse_intern_ids(request.form)
    previous_intern_ids = {i.id for i in project.interns}

    # Keep it scoped: only interns in the PM's own department
    selected_interns = []
    if intern_ids:
        selected_interns = Intern.query.filter(
            Intern.id.in_(intern_ids), Intern.department_id == pm.department_id
        ).all()
        if len(selected_interns) != len(intern_ids):
            flash("One or more selected interns aren't in your department.", "danger")
            return redirect(url_for("project.list_projects"))

    try:
        project.interns = selected_interns
        db.session.commit()

        newly_assigned = [i for i in selected_interns if i.id not in previous_intern_ids]
        for new_intern in newly_assigned:
            notify_user(
                new_intern.user_id,
                f"You have been assigned to task '{project.title}' by {pm.full_name}.",
                icon="bi-kanban",
                notification_type="Project Assigned",
            )
            db.session.commit()
            send_project_assignment_email(
                intern=new_intern, project=project, assigned_by=pm.full_name
            )

        flash(f"Intern assignment updated for '{project.title}'.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Could not update intern assignment due to a database error.", "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to update intern assignment for project #%s.", project_id)
        flash("Could not update intern assignment due to a system error. Please try again.", "danger")

    return redirect(url_for("project.list_projects"))
# ----------------------------------------------------------------------
# Detail view: shows the project plus every link the intern has
# submitted against it, with independent HR / Project Manager
# approve-or-reject actions (each with its own remarks field).
# ----------------------------------------------------------------------
@project_bp.route("/<int:project_id>")
@login_required
@roles_required("Station HR", "Project Manager", "Super Admin")
def view_project(project_id):
    """Project detail page: info + submitted links + approval controls."""
    project = Project.query.get_or_404(project_id)

    if current_user.role == "Project Manager":
        pm = current_pm_profile()
        if pm is None or project.assigned_manager_id != pm.id:
            flash("You can only view projects assigned to you.", "danger")
            return redirect(url_for("project.list_projects"))
    else:
        is_city_scoped, hr_city = hr_city_scope()
        if is_city_scoped and (not project.department or project.department.city != hr_city):
            flash("You do not have permission to view projects outside your assigned city.", "danger")
            return redirect(url_for("project.list_projects"))

    submissions = (
        ProjectSubmission.query.filter_by(project_id=project.id)
        .order_by(ProjectSubmission.submitted_at.desc())
        .all()
    )

    return render_template(
        "projects/view.html",
        project=project,
        submissions=submissions,
    )


def _sync_project_status_from_submission(submission: ProjectSubmission) -> None:
    """Reflect the combined HR + PM review outcome onto the parent
    project's own status so the rest of the app (lists, filters,
    overdue checks) stays in sync with the approval workflow."""
    project = submission.project
    if project is None:
        return
    overall = submission.overall_status
    if overall in ("Approved", "Rejected") and project.can_transition_to(overall):
        project.status = overall
    # Otherwise (still Pending on one side, or the project has already
    # moved past this stage -- e.g. already Completed) leave the
    # project's own working status alone.


# ----------------------------------------------------------------------
# HR review of a submitted link (independent of the PM's review)
# ----------------------------------------------------------------------
@project_bp.route("/submissions/<int:submission_id>/hr-review", methods=["POST"])
@login_required
@roles_required("Station HR", "Super Admin")
def hr_review_submission(submission_id):
    """Station HR approves or rejects a submitted project link, with
    remarks. A Station HR may only review submissions for projects in
    their assigned city."""
    submission = ProjectSubmission.query.get_or_404(submission_id)
    is_city_scoped, hr_city = hr_city_scope()
    if is_city_scoped and (
        not submission.project or not submission.project.department
        or submission.project.department.city != hr_city
    ):
        flash("You do not have permission to review submissions outside your assigned city.", "danger")
        return redirect(url_for("project.list_projects"))

    decision = request.form.get("decision")
    remarks = request.form.get("remarks", "").strip()

    if decision not in ("Approved", "Rejected"):
        flash("Invalid decision.", "danger")
        return redirect(url_for("project.view_project", project_id=submission.project_id))

    try:
        submission.hr_status = decision
        submission.hr_remarks = remarks or None
        submission.hr_reviewed_by_id = current_user.id
        submission.hr_reviewed_at = now_pkt()

        _sync_project_status_from_submission(submission)

        log_action(
            action="UPDATE",
            description=f"HR {decision.lower()} submission for project '{submission.project.title}'.",
            target_type="ProjectSubmission",
            target_id=submission.id,
        )

        notify_user(
            submission.intern.user_id,
            f"HR {decision.lower()} your submission for '{submission.project.title}'.",
            icon="bi-clipboard-check" if decision == "Approved" else "bi-clipboard-x",
            notification_type="Submission Reviewed",
        )
        db.session.commit()
        flash(f"Submission {decision.lower()} by HR.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to save HR review for submission #%s.", submission_id
        )
        flash("Could not save the review due to a system error. Please try again.", "danger")

    return redirect(url_for("project.view_project", project_id=submission.project_id))


# ----------------------------------------------------------------------
# Project Manager review of a submitted link (independent of HR's review)
# ----------------------------------------------------------------------
@project_bp.route("/submissions/<int:submission_id>/pm-review", methods=["POST"])
@login_required
@roles_required("Project Manager")
def pm_review_submission(submission_id):
    """Project Manager approves or rejects a submitted link, with remarks."""
    submission = ProjectSubmission.query.get_or_404(submission_id)
    pm = current_pm_profile()

    if pm is None or submission.project.assigned_manager_id != pm.id:
        flash("You can only review submissions on projects assigned to you.", "danger")
        return redirect(url_for("project.list_projects"))

    decision = request.form.get("decision")
    remarks = request.form.get("remarks", "").strip()

    if decision not in ("Approved", "Rejected"):
        flash("Invalid decision.", "danger")
        return redirect(url_for("project.view_project", project_id=submission.project_id))

    try:
        submission.pm_status = decision
        submission.pm_remarks = remarks or None
        submission.pm_reviewed_by_id = pm.id
        submission.pm_reviewed_at = now_pkt()

        _sync_project_status_from_submission(submission)

        log_action(
            action="UPDATE",
            description=f"Project Manager {decision.lower()} submission for project '{submission.project.title}'.",
            target_type="ProjectSubmission",
            target_id=submission.id,
        )

        notify_user(
            submission.intern.user_id,
            f"{pm.full_name} {decision.lower()} your submission for '{submission.project.title}'.",
            icon="bi-clipboard-check" if decision == "Approved" else "bi-clipboard-x",
            notification_type="Submission Reviewed",
        )
        db.session.commit()
        flash(f"Submission {decision.lower()}.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to save PM review for submission #%s.", submission_id
        )
        flash("Could not save the review due to a system error. Please try again.", "danger")

    return redirect(url_for("project.view_project", project_id=submission.project_id))


# ----------------------------------------------------------------------
# Disable / Enable (soft delete)
# ----------------------------------------------------------------------
@project_bp.route("/toggle-status/<int:project_id>", methods=["POST"])
@login_required
@roles_required("Station HR", "Super Admin")
def toggle_project_status(project_id):
    """Disable or re-enable a project (soft delete). The project and all
    related submissions, milestones, work logs and evaluations remain in
    the database; a disabled project is simply hidden from active
    assignment workflows and flagged in listings."""
    project = Project.query.get_or_404(project_id)
    is_city_scoped, hr_city = hr_city_scope()
    if is_city_scoped and (not project.department or project.department.city != hr_city):
        flash("You do not have permission to manage projects outside your assigned city.", "danger")
        return redirect(url_for("project.list_projects"))

    try:
        project.is_active = not project.is_active
        state = "enabled" if project.is_active else "disabled"
        log_action(
            action="UPDATE",
            description=f"Project '{project.title}' {state}.",
            target_type="Project",
            target_id=project.id,
        )
        db.session.commit()
        flash(f"Project '{project.title}' has been {state}.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to toggle status for project #%s.", project_id)
        flash("Could not update the project's status due to a system error. Please try again.", "danger")

    return redirect(url_for("project.list_projects"))


# ----------------------------------------------------------------------
# Status update (Project Manager updates progress on their own project)
# ----------------------------------------------------------------------
@project_bp.route("/update-status/<int:project_id>", methods=["POST"])
@login_required
@roles_required("Project Manager")
def update_project_status(project_id):
    """Allow a Project Manager to update the status of a project that is
    assigned to them (e.g. moving it from Working to Submitted)."""
    project = Project.query.get_or_404(project_id)
    pm = current_pm_profile()

    if pm is None or project.assigned_manager_id != pm.id:
        flash("You can only update the status of projects assigned to you.", "danger")
        return redirect(url_for("project.list_projects"))

    new_status = request.form.get("status")
    # Project Managers are restricted from setting HR-only approval statuses.
    allowed_pm_statuses = {"Pending", "Working", "Submitted"}
    if new_status not in allowed_pm_statuses:
        flash("Invalid status selected.", "danger")
        return redirect(url_for("project.list_projects"))

    # Forward-only workflow: status can never move backwards, and a
    # Completed project can never be changed.
    if not project.can_transition_to(new_status):
        flash(
            f"Cannot change status from '{project.status}' to '{new_status}'. "
            "Project status can only move forward.",
            "danger",
        )
        return redirect(url_for("project.list_projects"))

    try:
        project.status = new_status
        log_action(
            action="UPDATE",
            description=f"Project '{project.title}' status changed to {new_status}.",
            target_type="Project",
            target_id=project.id,
        )

        for assigned_intern in project.interns:
            notify_user(
                assigned_intern.user_id,
                f"Project '{project.title}' status changed to {new_status}.",
                icon="bi-kanban",
            )

        db.session.commit()
        flash(f"Project '{project.title}' status updated to {new_status}.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to update status for project #%s.", project_id
        )
        flash("Could not update the project status due to a system error. Please try again.", "danger")

    return redirect(url_for("project.list_projects"))
