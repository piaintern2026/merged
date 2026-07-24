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
from models import ProjectManager, Department, User
from utils import (
    roles_required,
    save_profile_picture,
    delete_profile_picture,
    log_action,
    PIA_CITIES,
    current_pm_profile,
)
from services.email_service import send_account_status_email

pm_bp = Blueprint("project_manager", __name__, url_prefix="/project-managers")


@pm_bp.route("/")
@login_required
@roles_required("Super Admin")
def list_pms():
    """Show all Project Managers."""
    pms = ProjectManager.query.order_by(ProjectManager.created_at.desc()).all()
    return render_template("project_managers/list.html", pms=pms, cities=PIA_CITIES)


@pm_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def add_pm():
    """Add a new Project Manager (creates User + ProjectManager rows)."""
    departments = Department.query.filter_by(status="Active").order_by(Department.name).all()

    if request.method == "POST":
        # Gather form fields
        full_name = request.form.get("full_name", "").strip()
        p_number = request.form.get("p_number", "").strip()
        department_id = request.form.get("department_id")
        city = request.form.get("city", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        designation = request.form.get("designation", "").strip()
        photo = request.files.get("profile_picture")

        # ---- Validation ----
        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if not p_number:
            errors.append("P Number is required.")
        if not department_id:
            errors.append("Department is required.")
        if not city:
            errors.append("City is required.")
        elif city not in PIA_CITIES:
            errors.append("Please select a valid city from the list.")
        if not phone:
            errors.append("Phone is required.")
        if not email:
            errors.append("Email is required.")
        if not username:
            errors.append("Username is required.")
        if not password or len(password) < 8:
            errors.append("Password must be at least 8 characters long.")
        if not designation:
            errors.append("Designation is required.")

        if User.query.filter_by(email=email).first():
            errors.append("A user with this email already exists.")
        if User.query.filter_by(username=username).first():
            errors.append("This username is already taken.")
        if ProjectManager.query.filter_by(p_number=p_number).first():
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
            user.set_password(password)
            db.session.add(user)
            db.session.flush()  # get user.id before commit

            # Create the profile record
            pm = ProjectManager(
                user_id=user.id,
                full_name=full_name,
                p_number=p_number,
                department_id=int(department_id),
                city=city,
                phone=phone,
                designation=designation,
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

            flash(f"Project Manager '{full_name}' added successfully.", "success")
            return redirect(url_for("project_manager.list_pms"))

        except ValueError as ve:
            db.session.rollback()
            flash(str(ve), "danger")
        except IntegrityError:
            db.session.rollback()
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
@roles_required("Super Admin")
def edit_pm(pm_id):
    """Edit an existing Project Manager's profile and account details."""
    pm = ProjectManager.query.get_or_404(pm_id)
    user = pm.user
    departments = Department.query.filter_by(status="Active").order_by(Department.name).all()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        p_number = request.form.get("p_number", "").strip()
        department_id = request.form.get("department_id")
        city = request.form.get("city", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")  # optional on edit
        designation = request.form.get("designation", "").strip()
        photo = request.files.get("profile_picture")

        errors = []
        if not full_name or not p_number or not department_id or not city or not phone:
            errors.append("All required fields must be filled in.")
        if city and city not in PIA_CITIES:
            errors.append("Please select a valid city from the list.")
        if not email or not username or not designation:
            errors.append("Email, username and designation are required.")

        duplicate_email = User.query.filter(User.email == email, User.id != user.id).first()
        if duplicate_email:
            errors.append("Another user already uses this email.")
        duplicate_username = User.query.filter(
            User.username == username, User.id != user.id
        ).first()
        if duplicate_username:
            errors.append("Another user already uses this username.")
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
            pm.p_number = p_number
            pm.department_id = int(department_id)
            pm.city = city
            pm.phone = phone
            pm.designation = designation

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
        except IntegrityError:
            db.session.rollback()
            flash("Could not update Project Manager due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update Project Manager #%s.", pm.id)
            flash("Could not update Project Manager due to a system error. Please try again.", "danger")

    return render_template(
        "project_managers/form.html", pm=pm, departments=departments, cities=PIA_CITIES, form=None
    )


@pm_bp.route("/delete/<int:pm_id>", methods=["POST"])
@login_required
@roles_required("Super Admin")
def delete_pm(pm_id):
    """Delete a Project Manager and their login account."""
    pm = ProjectManager.query.get_or_404(pm_id)
    user = pm.user
    full_name = pm.full_name

    try:
        pm_id_val = pm.id
        delete_profile_picture(user.profile_picture)
        db.session.delete(user)  # cascades to delete pm profile too
        log_action(
            action="DELETE",
            description=f"Deleted Project Manager '{full_name}'.",
            target_type="ProjectManager",
            target_id=pm_id_val,
        )
        db.session.commit()
        flash(f"Project Manager '{full_name}' deleted successfully.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Could not delete Project Manager due to a database error.", "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete Project Manager #%s.", pm_id)
        flash("Could not delete Project Manager due to a system error. Please try again.", "danger")

    return redirect(url_for("project_manager.list_pms"))


@pm_bp.route("/toggle-status/<int:pm_id>", methods=["POST"])
@login_required
@roles_required("Super Admin")
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
