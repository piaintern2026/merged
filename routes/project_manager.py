"""
routes/project_manager.py
--------------------------
Full CRUD for Project Managers, including creating their linked User
login account, profile picture upload and activate/deactivate toggle.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import ProjectManager, Department, SubDepartment, User
from utils import (
    roles_required,
    save_profile_picture,
    delete_profile_picture,
    log_action,
    PIA_CITIES,
    current_pm_profile,
    DEFAULT_USER_PASSWORD,
)
from services.email_service import send_account_status_email, send_staff_account_email

pm_bp = Blueprint("project_manager", __name__, url_prefix="/project-managers")


@pm_bp.route("/")
@login_required
@roles_required("Station HR", "Admin")
def list_pms():
    """Show all Project Managers. Station HR only sees Project Managers
    within their own assigned city; Admin sees everyone."""
    from utils import hr_city_scope

    is_city_scoped, hr_city = hr_city_scope()
    query = ProjectManager.query
    if is_city_scoped:
        query = query.filter(ProjectManager.city == hr_city)
    pms = query.order_by(ProjectManager.created_at.desc()).all()
    return render_template(
        "project_managers/list.html",
        pms=pms,
        cities=[hr_city] if is_city_scoped and hr_city else PIA_CITIES,
    )


@pm_bp.route("/view/<int:pm_id>")
@login_required
@roles_required("Admin", "Station HR")
def view_pm(pm_id):
    """Show full details of a single Project Manager: profile info,
    department, projects they manage, and evaluations they've submitted."""
    pm = ProjectManager.query.get_or_404(pm_id)

    from utils import hr_city_scope
    is_city_scoped, hr_city = hr_city_scope()
    if is_city_scoped and pm.city != hr_city:
        flash("You do not have permission to view Project Managers outside your assigned city.", "danger")
        return redirect(url_for("project_manager.list_pms"))

    from models import Project, PMEvaluation

    projects = (
        Project.query.filter_by(assigned_manager_id=pm.id)
        .order_by(Project.created_at.desc())
        .all()
    )
    active_projects = [p for p in projects if p.is_active]
    evaluations = (
        PMEvaluation.query.filter_by(project_manager_id=pm.id)
        .order_by(PMEvaluation.evaluation_date.desc())
        .all()
    )

    return render_template(
        "project_managers/view.html",
        pm=pm,
        projects=projects,
        active_projects=active_projects,
        evaluations=evaluations,
    )


@pm_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required("Admin")
def add_pm():
    """Add a new Project Manager (creates User + ProjectManager rows)."""
    departments = Department.query.filter_by(is_active=True).order_by(db.func.lower(Department.name)).all()

    if request.method == "POST":
        # Gather form fields
        full_name = request.form.get("full_name", "").strip()
        p_number = request.form.get("p_number", "").strip()
        department_id = request.form.get("department_id")
        sub_department_id = request.form.get("sub_department_id") or None
        city = request.form.get("city", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip() or email  # fallback: email becomes username
        password = request.form.get("password", "")
        designation = request.form.get("designation", "").strip()
        photo = request.files.get("profile_picture")

        # ---- Validation ----
        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if not department_id:
            errors.append("Department is required.")
        if not city:
            errors.append("City is required.")
        elif city not in PIA_CITIES:
            errors.append("Please select a valid city from the list.")
        if not email:
            errors.append("Email is required.")
        if not username:
            errors.append("Username is required.")
        # Optional: if left blank, the account gets the standard default
        # password (utils.DEFAULT_USER_PASSWORD). A typed password still
        # has to meet the normal minimum length.
        if password and len(password) < 8:
            errors.append("Password must be at least 8 characters long.")
        # P.No, Phone and Designation are optional -- they can be added
        # or edited later, so no "required" check for them here.

        sub_department = None
        if sub_department_id:
            sub_department = SubDepartment.query.get(sub_department_id)
            if not sub_department or (
                department_id and sub_department.department_id != int(department_id)
            ):
                errors.append("Please select a valid sub department for the chosen department.")
                sub_department = None

        if User.query.filter_by(email=email).first():
            errors.append("A user with this email already exists.")
        if User.query.filter_by(username=username).first():
            errors.append("This username is already taken.")
        if p_number and ProjectManager.query.filter_by(p_number=p_number).first():
            errors.append("This P Number is already registered.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "project_managers/form.html", pm=None, departments=departments, cities=PIA_CITIES, form=request.form
            )

        try:
            # Handle profile picture upload
            picture_filename = save_profile_picture(photo)

            # Create the login account
            user = User(
                email=email,
                username=username,
                role="Project Manager",
                profile_picture=picture_filename,
            )
            final_password = password or DEFAULT_USER_PASSWORD
            user.set_password(final_password)
            db.session.add(user)
            db.session.flush()  # get user.id before commit

            # Create the profile record
            pm = ProjectManager(
                user_id=user.id,
                full_name=full_name,
                p_number=p_number or None,
                department_id=int(department_id),
                sub_department_id=sub_department.id if sub_department else None,
                city=city,
                phone=phone or None,
                designation=designation or None,
            )
            db.session.add(pm)
            db.session.flush()
            log_action(
                action="CREATE",
                description=f"Created Project Manager '{full_name}' (P# {p_number}).",
                target_type="ProjectManager",
                target_id=pm.id,
            )
            db.session.commit()

            send_staff_account_email(user, final_password, created_by=current_user.display_name())

            flash(f"Project Manager '{full_name}' added successfully.", "success")
            return redirect(url_for("project_manager.list_pms"))

        except ValueError as ve:
            db.session.rollback()
            flash(str(ve), "danger")
        except IntegrityError as ie:
            db.session.rollback()
            current_app.logger.exception("IntegrityError creating Project Manager: %s", ie)
            flash("Could not save Project Manager due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to create Project Manager.")
            flash("Could not save Project Manager due to a system error. Please try again.", "danger")

    return render_template(
        "project_managers/form.html", pm=None, departments=departments, cities=PIA_CITIES, form=None
    )


@pm_bp.route("/edit/<int:pm_id>", methods=["GET", "POST"])
@login_required
@roles_required("Admin")
def edit_pm(pm_id):
    """Edit an existing Project Manager's profile and account details."""
    pm = ProjectManager.query.get_or_404(pm_id)
    user = pm.user
    departments = Department.query.order_by(db.func.lower(Department.name)).all()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        p_number = request.form.get("p_number", "").strip()
        department_id = request.form.get("department_id")
        sub_department_id = request.form.get("sub_department_id") or None
        city = request.form.get("city", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")  # optional on edit
        designation = request.form.get("designation", "").strip()
        photo = request.files.get("profile_picture")

        errors = []
        if not full_name or not department_id or not city:
            errors.append("Full name, department and city are required.")
        if city and city not in PIA_CITIES:
            errors.append("Please select a valid city from the list.")
        if not email or not username:
            errors.append("Email and username are required.")
        # P.No, Phone and Designation remain optional on edit too.

        sub_department = None
        if sub_department_id:
            sub_department = SubDepartment.query.get(sub_department_id)
            if not sub_department or (
                department_id and sub_department.department_id != int(department_id)
            ):
                errors.append("Please select a valid sub department for the chosen department.")
                sub_department = None

        duplicate_email = User.query.filter(User.email == email, User.id != user.id).first()
        if duplicate_email:
            errors.append("Another user already uses this email.")
        duplicate_username = User.query.filter(
            db.func.lower(User.username) == username, User.id != user.id
        ).first()
        if duplicate_username:
            errors.append("Another user already uses this username.")
        if p_number:
            duplicate_p = ProjectManager.query.filter(
                ProjectManager.p_number == p_number, ProjectManager.id != pm.id
            ).first()
            if duplicate_p:
                errors.append("This P Number is already registered to another PM.")

        if password and len(password) < 8:
            errors.append("New password must be at least 8 characters long.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "project_managers/form.html", pm=pm, departments=departments, cities=PIA_CITIES, form=request.form
            )

        try:
            # Update profile picture if a new one was provided
            new_picture = save_profile_picture(photo)
            if new_picture:
                delete_profile_picture(user.profile_picture)
                user.profile_picture = new_picture

            user.email = email
            user.username = username
            if password:
                user.set_password(password)

            pm.full_name = full_name
            pm.p_number = p_number or None
            pm.department_id = int(department_id)
            pm.sub_department_id = sub_department.id if sub_department else None
            pm.city = city
            pm.phone = phone or None
            pm.designation = designation or None

            log_action(
                action="UPDATE",
                description=f"Updated Project Manager '{full_name}' (P# {p_number}).",
                target_type="ProjectManager",
                target_id=pm.id,
            )
            db.session.commit()
            flash(f"Project Manager '{full_name}' updated successfully.", "success")
            return redirect(url_for("project_manager.list_pms"))

        except ValueError as ve:
            db.session.rollback()
            flash(str(ve), "danger")
        except IntegrityError as ie:
            db.session.rollback()
            current_app.logger.exception("IntegrityError updating Project Manager #%s: %s", pm.id, ie)
            flash("Could not update Project Manager due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update Project Manager #%s.", pm.id)
            flash("Could not update Project Manager due to a system error. Please try again.", "danger")

    return render_template(
        "project_managers/form.html", pm=pm, departments=departments, cities=PIA_CITIES, form=None
    )


@pm_bp.route("/toggle-status/<int:pm_id>", methods=["POST"])
@login_required
@roles_required("Admin")
def toggle_pm_status(pm_id):
    """Activate or deactivate a Project Manager's account."""
    pm = ProjectManager.query.get_or_404(pm_id)
    try:
        pm.is_active_flag = not pm.is_active_flag
        pm.user.is_active_account = pm.is_active_flag
        state = "activated" if pm.is_active_flag else "deactivated"
        log_action(
            action="UPDATE",
            description=f"Project Manager '{pm.full_name}' {state}.",
            target_type="ProjectManager",
            target_id=pm.id,
        )
        db.session.commit()
        send_account_status_email(user=pm.user, is_active=pm.is_active_flag)
        flash(f"Project Manager '{pm.full_name}' has been {state}.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to toggle status for Project Manager #%s.", pm_id)
        flash("Could not update the Project Manager's status due to a system error. Please try again.", "danger")

    return redirect(url_for("project_manager.list_pms"))


# ----------------------------------------------------------------------
# Project Manager: manage their own profile (self-service)
# ----------------------------------------------------------------------
@pm_bp.route("/my-profile", methods=["GET", "POST"])
@login_required
@roles_required("Project Manager")
def my_profile():
    """Let a logged-in Project Manager edit their own display details.

    HR creates PM accounts with placeholder info (name, email, phone),
    so PMs need a way to correct/personalise this themselves after
    (or any time after) their first login. Only personal-contact fields
    are editable here -- HR-controlled fields (P Number, Department,
    City, Designation, active status) stay off-limits and can only be
    changed by HR via Manage Project Managers. Password changes reuse
    the existing auth.change_password flow (current-password verified
    there), so this page doesn't duplicate that logic.
    """
    pm = current_pm_profile()
    if pm is None:
        flash("Your Project Manager profile could not be found.", "danger")
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        photo = request.files.get("profile_picture")

        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if not email:
            errors.append("Email is required.")
        if not phone:
            errors.append("Phone number is required.")

        duplicate_email = User.query.filter(User.email == email, User.id != current_user.id).first()
        if duplicate_email:
            errors.append("Another user already uses this email.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("project_managers/my_profile.html", pm=pm)

        try:
            new_picture = save_profile_picture(photo)
            if new_picture:
                delete_profile_picture(current_user.profile_picture)
                current_user.profile_picture = new_picture

            current_user.email = email
            pm.full_name = full_name
            pm.phone = phone

            log_action(
                action="UPDATE",
                description=f"Project Manager '{pm.full_name}' updated their own profile.",
                target_type="ProjectManager",
                target_id=pm.id,
            )
            db.session.commit()
            flash("Profile updated successfully.", "success")
            return redirect(url_for("project_manager.my_profile"))
        except ValueError as ve:
            db.session.rollback()
            flash(str(ve), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("Could not update your profile due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update profile for Project Manager #%s.", pm.id)
            flash("Could not update your profile due to a system error. Please try again.", "danger")

    return render_template("project_managers/my_profile.html", pm=pm)
    return redirect(url_for("project_manager.list_pms"))


def _pm_has_linked_records(pm):
    """True if this PM is still referenced anywhere in the system (as
    an assigned manager, evaluator, rotation participant, reviewer,
    etc), in which case it must not be permanently deleted (only
    deactivated) -- deleting it would either orphan those records or
    violate a foreign key constraint."""
    from models import Project, PMEvaluation, InternRotation, ProjectSubmission
    from models.pm_workspace import ProjectMilestone
    from models.evaluation import Evaluation

    if Project.query.filter_by(project_manager_id=pm.id).first():
        return True
    if PMEvaluation.query.filter_by(project_manager_id=pm.id).first():
        return True
    if InternRotation.query.filter(
        db.or_(
            InternRotation.from_manager_id == pm.id,
            InternRotation.to_manager_id == pm.id,
        )
    ).first():
        return True
    if ProjectSubmission.query.filter_by(pm_reviewed_by_id=pm.user_id).first():
        return True
    if ProjectMilestone.query.filter_by(created_by_id=pm.id).first():
        return True
    if Evaluation.query.filter_by(evaluated_by_id=pm.user_id).first():
        return True
    return False


@pm_bp.route("/delete/<int:pm_id>", methods=["POST"])
@login_required
@roles_required("Admin")
def delete_pm(pm_id):
    """Permanently delete a Project Manager (and their linked login
    account). Admin only -- unlike toggle_pm_status (soft
    delete/deactivate), this removes the rows from the database for
    good. Blocked if the PM is still referenced by any Project,
    Evaluation, Rotation, Submission review or Milestone."""
    pm = ProjectManager.query.get_or_404(pm_id)

    if _pm_has_linked_records(pm):
        flash(
            f"Cannot permanently delete '{pm.full_name}': they still have "
            "Projects, Evaluations, Rotations, or other records linked to "
            "them. Deactivate them instead, or reassign those records first.",
            "danger",
        )
        return redirect(url_for("project_manager.list_pms"))

    try:
        full_name = pm.full_name
        user = pm.user
        if user and user.profile_picture:
            delete_profile_picture(user.profile_picture)

        log_action(
            action="DELETE",
            description=f"Permanently deleted Project Manager '{full_name}'.",
            target_type="ProjectManager",
            target_id=pm.id,
        )
        db.session.delete(pm)
        if user:
            db.session.delete(user)
        db.session.commit()
        flash(f"Project Manager '{full_name}' has been permanently deleted.", "success")
    except IntegrityError as e:
        db.session.rollback()
        current_app.logger.exception(
            "Database error while permanently deleting Project Manager #%s: %s",
            pm_id, e,
        )
        flash(
            "Could not permanently delete this Project Manager because "
            "other records still reference them.", "danger",
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to permanently delete Project Manager #%s.", pm_id
        )
        flash(
            "Could not permanently delete the Project Manager due to a "
            "system error. Please try again.", "danger",
        )

    return redirect(url_for("project_manager.list_pms"))
