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
from models import Project, Department, ProjectManager, Intern, ProjectSubmission
from utils import roles_required, current_pm_profile, notify_user, log_action, now_pkt
from services.email_service import send_project_assignment_email

project_bp = Blueprint("project", __name__, url_prefix="/projects")


def _parse_date(value: str):
    """Parse an HTML date input (YYYY-MM-DD) into a date object."""
    return datetime.strptime(value, "%Y-%m-%d").date()


# ----------------------------------------------------------------------
# Listing (role-aware: HR sees all, Project Manager sees only their own)
# ----------------------------------------------------------------------
@project_bp.route("/")
@login_required
@roles_required("HR", "Project Manager")
def list_projects():
    """Show projects. HR sees everything with optional filters; a
    Project Manager only sees projects assigned to them."""



    query = Project.query

    if current_user.role == "Project Manager":
        pm = current_pm_profile()
        if pm is None:
            flash("Your Project Manager profile could not be found.", "danger")
            return redirect(url_for("dashboard.index"))
        query = query.filter_by(assigned_manager_id=pm.id)
        # PM can only hand tasks to interns in their own department
        interns = Intern.query.filter_by(department_id=pm.department_id).order_by(
            Intern.full_name
        ).all()
    else:
        # HR-only filters via query string
        status = request.args.get("status")
        department_id = request.args.get("department_id")
        priority = request.args.get("priority")

        if status:
            query = query.filter_by(status=status)
        if department_id:
            query = query.filter_by(department_id=department_id)
        if priority:
            query = query.filter_by(priority=priority)

        interns = Intern.query.order_by(Intern.full_name).all()

    projects = query.order_by(Project.deadline.asc()).all()
    departments = Department.query.order_by(Department.name).all()

    return render_template(
        "projects/list.html",
        projects=projects,
        departments=departments,
        interns=interns,          # <-- new
        statuses=Project.STATUSES,
        priorities=Project.PRIORITIES,
        filters=request.args,
    )

# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------
@project_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required("HR", "Project Manager")
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

    if is_pm and pm is None:
        flash("Your Project Manager profile could not be found.", "danger")
        return redirect(url_for("dashboard.index"))

    if is_pm:
        departments = Department.query.filter_by(id=pm.department_id).all()
        managers = [pm]
        interns = Intern.query.filter_by(department_id=pm.department_id).order_by(
            Intern.full_name
        ).all()
    else:
        departments = Department.query.filter_by(status="Active").order_by(Department.name).all()
        managers = ProjectManager.query.filter_by(is_active_flag=True).order_by(
            ProjectManager.full_name
        ).all()
        interns = Intern.query.order_by(Intern.full_name).all()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        priority = request.form.get("priority", "Medium")
        status = request.form.get("status", "Pending")
        start_date_raw = request.form.get("start_date", "")
        deadline_raw = request.form.get("deadline", "")

        if is_pm:
            department_id = pm.department_id
            manager_id = pm.id
            intern_id = request.form.get("assigned_intern_id") or None
        else:
            department_id = request.form.get("department_id")
            manager_id = request.form.get("assigned_manager_id") or None
            intern_id = request.form.get("assigned_intern_id") or None

        errors = []
        if not title:
            errors.append("Project title is required.")
        if not department_id:
            errors.append("Department is required.")
        if priority not in Project.PRIORITIES:
            errors.append("Invalid priority selected.")
        if is_pm:
            if status not in {"Pending", "Working", "Submitted"}:
                errors.append("Invalid status selected.")
        elif status not in Project.STATUSES:
            errors.append("Invalid status selected.")
        if not start_date_raw or not deadline_raw:
            errors.append("Start date and deadline are required.")

        # PMs may only hand the task to an intern in their own department.
        if is_pm and intern_id:
            intern_ok = Intern.query.filter_by(
                id=int(intern_id), department_id=pm.department_id
            ).first()
            if intern_ok is None:
                errors.append("That intern isn't in your department.")

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
                priorities=Project.PRIORITIES,
                form=request.form,
                is_pm=is_pm,
            )

        try:
            project = Project(
                title=title,
                description=description,
                department_id=int(department_id),
                assigned_manager_id=int(manager_id) if manager_id else None,
                assigned_intern_id=int(intern_id) if intern_id else None,
                priority=priority,
                status=status,
                start_date=start_date,
                deadline=deadline,
            )
            db.session.add(project)
            db.session.flush()
            log_action(
                action="CREATE",
                description=f"Created project '{title}'.",
                target_type="Project",
                target_id=project.id,
            )
            db.session.commit()

            if project.assigned_intern_id:
                assigned_intern = Intern.query.get(project.assigned_intern_id)
                if assigned_intern:
                    assigner = pm.full_name if is_pm else "HR"
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
        priorities=Project.PRIORITIES,
        form=None,
        is_pm=is_pm,
    )


# ----------------------------------------------------------------------
# Edit (also used to assign / reassign manager & intern)
# ----------------------------------------------------------------------
@project_bp.route("/edit/<int:project_id>", methods=["GET", "POST"])
@login_required
@roles_required("HR")
def edit_project(project_id):
    """Edit an existing project, including assignment fields."""
    project = Project.query.get_or_404(project_id)
    departments = Department.query.filter_by(status="Active").order_by(Department.name).all()
    managers = ProjectManager.query.filter_by(is_active_flag=True).order_by(
        ProjectManager.full_name
    ).all()
    interns = Intern.query.order_by(Intern.full_name).all()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        department_id = request.form.get("department_id")
        manager_id = request.form.get("assigned_manager_id") or None
        intern_id = request.form.get("assigned_intern_id") or None
        priority = request.form.get("priority", "Medium")
        status = request.form.get("status", "Pending")
        start_date_raw = request.form.get("start_date", "")
        deadline_raw = request.form.get("deadline", "")

        errors = []
        if not title:
            errors.append("Project title is required.")
        if not department_id:
            errors.append("Department is required.")
        if priority not in Project.PRIORITIES:
            errors.append("Invalid priority selected.")
        if status not in Project.STATUSES:
            errors.append("Invalid status selected.")

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
                priorities=Project.PRIORITIES,
                form=request.form,
            )

        try:
            previous_intern_id = project.assigned_intern_id
            previous_status = project.status

            project.title = title
            project.description = description
            project.department_id = int(department_id)
            project.assigned_manager_id = int(manager_id) if manager_id else None
            project.assigned_intern_id = int(intern_id) if intern_id else None
            project.priority = priority
            project.status = status
            project.start_date = start_date
            project.deadline = deadline

            # Notify the intern of a new assignment or a status change.
            if project.assigned_intern_id:
                assigned_intern = Intern.query.get(project.assigned_intern_id)
                if assigned_intern:
                    if project.assigned_intern_id != previous_intern_id:
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

            if (
                project.assigned_intern_id
                and project.assigned_intern_id != previous_intern_id
                and assigned_intern
            ):
                send_project_assignment_email(intern=assigned_intern, project=project, assigned_by="HR")

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
        priorities=Project.PRIORITIES,
        form=None,
    )


# ----------------------------------------------------------------------
# Quick assign (lightweight modal action from the list page)
# ----------------------------------------------------------------------
@project_bp.route("/assign/<int:project_id>", methods=["POST"])
@login_required
@roles_required("HR")
def assign_project(project_id):
    """Quickly assign/reassign a Project Manager and Intern to a project
    without opening the full edit form."""
    project = Project.query.get_or_404(project_id)

    manager_id = request.form.get("assigned_manager_id") or None
    intern_id = request.form.get("assigned_intern_id") or None

    previous_intern_id = project.assigned_intern_id
    new_intern = None

    try:
        project.assigned_manager_id = int(manager_id) if manager_id else None
        project.assigned_intern_id = int(intern_id) if intern_id else None

        # Notify the intern if they are newly assigned to this project.
        if project.assigned_intern_id and project.assigned_intern_id != previous_intern_id:
            new_intern = Intern.query.get(project.assigned_intern_id)
            if new_intern:
                notify_user(
                    new_intern.user_id,
                    f"You have been assigned to project '{project.title}'.",
                    icon="bi-kanban",
                    notification_type="Project Assigned",
                )

        db.session.commit()
        flash(f"Project '{project.title}' assignment updated.", "success")

        if project.assigned_intern_id and project.assigned_intern_id != previous_intern_id and new_intern:
            send_project_assignment_email(intern=new_intern, project=project, assigned_by="HR")
    except IntegrityError:
        db.session.rollback()
        flash("Could not update project assignment due to a database error.", "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to update assignment for project #%s.", project_id)
        flash("Could not update project assignment due to a system error. Please try again.", "danger")

    return redirect(url_for("project.list_projects"))

# ----------------------------------------------------------------------
# PM self-service: assign an intern to one of their own projects
# ----------------------------------------------------------------------
@project_bp.route("/assign-intern/<int:project_id>", methods=["POST"])
@login_required
@roles_required("Project Manager")
def assign_intern_to_project(project_id):
    """Allow a Project Manager to assign/reassign the intern working on
    a project that is already assigned to them."""
    project = Project.query.get_or_404(project_id)
    pm = current_pm_profile()

    if pm is None or project.assigned_manager_id != pm.id:
        flash("You can only assign interns on projects assigned to you.", "danger")
        return redirect(url_for("project.list_projects"))

    intern_id = request.form.get("assigned_intern_id") or None
    previous_intern_id = project.assigned_intern_id

    # Keep it scoped: only interns in the PM's own department
    if intern_id:
        intern = Intern.query.filter_by(
            id=int(intern_id), department_id=pm.department_id
        ).first()
        if intern is None:
            flash("That intern isn't in your department.", "danger")
            return redirect(url_for("project.list_projects"))

    try:
        project.assigned_intern_id = int(intern_id) if intern_id else None
        db.session.commit()

        if project.assigned_intern_id and project.assigned_intern_id != previous_intern_id:
            new_intern = Intern.query.get(project.assigned_intern_id)
            if new_intern:
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
@roles_required("HR", "Project Manager")
def view_project(project_id):
    """Project detail page: info + submitted links + approval controls."""
    project = Project.query.get_or_404(project_id)

    if current_user.role == "Project Manager":
        pm = current_pm_profile()
        if pm is None or project.assigned_manager_id != pm.id:
            flash("You can only view projects assigned to you.", "danger")
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
    if overall == "Approved":
        project.status = "Approved"
    elif overall == "Rejected":
        project.status = "Rejected"
    # Otherwise (still Pending on one side) leave the project's own
    # working status alone (e.g. "Submitted").


# ----------------------------------------------------------------------
# HR review of a submitted link (independent of the PM's review)
# ----------------------------------------------------------------------
@project_bp.route("/submissions/<int:submission_id>/hr-review", methods=["POST"])
@login_required
@roles_required("HR")
def hr_review_submission(submission_id):
    """HR approves or rejects a submitted project link, with remarks."""
    submission = ProjectSubmission.query.get_or_404(submission_id)

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
# Delete
# ----------------------------------------------------------------------
@project_bp.route("/delete/<int:project_id>", methods=["POST"])
@login_required
@roles_required("HR")
def delete_project(project_id):
    """Delete a project."""
    project = Project.query.get_or_404(project_id)
    title = project.title

    try:
        project_id_val = project.id
        db.session.delete(project)
        log_action(
            action="DELETE",
            description=f"Deleted project '{title}'.",
            target_type="Project",
            target_id=project_id_val,
        )
        db.session.commit()
        flash(f"Project '{title}' deleted successfully.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Could not delete project due to a database error.", "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete project #%s.", project_id)
        flash("Could not delete project due to a system error. Please try again.", "danger")

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

    try:
        project.status = new_status
        log_action(
            action="UPDATE",
            description=f"Project '{project.title}' status changed to {new_status}.",
            target_type="Project",
            target_id=project.id,
        )

        if project.assigned_intern_id:
            assigned_intern = Intern.query.get(project.assigned_intern_id)
            if assigned_intern:
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
