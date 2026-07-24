"""
routes/admin.py
----------------
Module 5: Admin Features, all HR-only. Bundles the five admin
capabilities requested: global Search, filterable/paginated Audit Log,
system Settings, HR Profile Management, and a System Statistics page.
Kept as one blueprint since every route here shares the same
"HR only" gate and is conceptually part of one "Admin" area.
"""

import os

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user

from extensions import db
from models import (
    User,
    Department,
    ProjectManager,
    Intern,
    Project,
    Attendance,
    Evaluation,
    AuditLog,
    Notification,
    SystemSetting,
)
from utils import (
    roles_required,
    paginate_query,
    save_profile_picture,
    delete_profile_picture,
    log_action,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ----------------------------------------------------------------------
# 1. Global Search
# ----------------------------------------------------------------------
@admin_bp.route("/search")
@login_required
@roles_required("Super Admin")
def search():
    """
    Search across Interns, Project Managers, Departments and Projects
    by name/title in a single query box. Each result type is queried
    and grouped separately so the results page can show clear sections.
    """
    query_text = request.args.get("q", "").strip()

    interns = pms = departments = projects = []
    if query_text:
        like = f"%{query_text}%"
        interns = (
            Intern.query.filter(
                db.or_(Intern.full_name.ilike(like), Intern.cnic.ilike(like), Intern.university.ilike(like))
            )
            .limit(25)
            .all()
        )
        pms = (
            ProjectManager.query.filter(
                db.or_(ProjectManager.full_name.ilike(like), ProjectManager.p_number.ilike(like))
            )
            .limit(25)
            .all()
        )
        departments = Department.query.filter(Department.name.ilike(like)).limit(25).all()
        projects = Project.query.filter(Project.title.ilike(like)).limit(25).all()

    total_results = len(interns) + len(pms) + len(departments) + len(projects)

    return render_template(
        "admin/search.html",
        query_text=query_text,
        interns=interns,
        pms=pms,
        departments=departments,
        projects=projects,
        total_results=total_results,
    )


# ----------------------------------------------------------------------
# 2. Audit Log (filters + pagination)
# ----------------------------------------------------------------------
@admin_bp.route("/audit-log")
@login_required
@roles_required("Super Admin")
def audit_log():
    """Paginated, filterable audit trail: who did what and when."""
    query = AuditLog.query

    action = request.args.get("action")
    user_id = request.args.get("user_id")
    target_type = request.args.get("target_type")

    if action:
        query = query.filter_by(action=action)
    if user_id:
        query = query.filter_by(user_id=user_id)
    if target_type:
        query = query.filter_by(target_type=target_type)

    query = query.order_by(AuditLog.created_at.desc())

    page = request.args.get("page", 1, type=int)
    pagination = paginate_query(query, page)

    # Distinct filter option lists, derived from the data itself.
    actions = [row[0] for row in db.session.query(AuditLog.action).distinct().all()]
    target_types = [
        row[0] for row in db.session.query(AuditLog.target_type).distinct().all() if row[0]
    ]
    users = User.query.order_by(User.username).all()

    return render_template(
        "admin/audit_log.html",
        pagination=pagination,
        entries=pagination.items,
        actions=actions,
        target_types=target_types,
        users=users,
        filters=request.args,
    )


# ----------------------------------------------------------------------
# 3. Settings
# ----------------------------------------------------------------------
@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def settings():
    """View and update system-wide key-value settings."""
    if request.method == "POST":
        updated = []
        for setting in SystemSetting.query.all():
            new_value = request.form.get(setting.key)
            if new_value is not None and new_value != setting.value:
                setting.value = new_value
                updated.append(setting.label)

        if updated:
            try:
                log_action(
                    action="UPDATE",
                    description=f"System settings updated: {', '.join(updated)}.",
                    target_type="SystemSetting",
                )
                db.session.commit()
                flash("Settings updated successfully.", "success")
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Failed to update system settings.")
                flash("Could not update settings due to a system error. Please try again.", "danger")
        else:
            flash("No changes were made.", "info")

        return redirect(url_for("admin.settings"))

    settings_list = SystemSetting.query.order_by(SystemSetting.label).all()
    return render_template("admin/settings.html", settings_list=settings_list)


# ----------------------------------------------------------------------
# 4. Profile Management (HR's own account)
# ----------------------------------------------------------------------
@admin_bp.route("/profile", methods=["GET", "POST"])
@login_required
@roles_required("HR")
def profile():
    """Let an HR user edit their own display name, email, username and photo."""
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        photo = request.files.get("profile_picture")

        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if not email:
            errors.append("Email is required.")
        if not username:
            errors.append("Username is required.")

        duplicate_email = User.query.filter(User.email == email, User.id != current_user.id).first()
        if duplicate_email:
            errors.append("Another user already uses this email.")
        duplicate_username = User.query.filter(
            User.username == username, User.id != current_user.id
        ).first()
        if duplicate_username:
            errors.append("Another user already uses this username.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("admin/profile.html")

        try:
            new_picture = save_profile_picture(photo)
            if new_picture:
                delete_profile_picture(current_user.profile_picture)
                current_user.profile_picture = new_picture

            current_user.full_name = full_name
            current_user.email = email
            current_user.username = username
            db.session.commit()
            flash("Profile updated successfully.", "success")
            return redirect(url_for("admin.profile"))
        except ValueError as ve:
            db.session.rollback()
            flash(str(ve), "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Failed to update profile for user #%s.", current_user.id
            )
            flash("Could not update the profile due to a system error. Please try again.", "danger")

    return render_template("admin/profile.html")


# ----------------------------------------------------------------------
# 5. System Statistics
# ----------------------------------------------------------------------
@admin_bp.route("/statistics")
@login_required
@roles_required("Super Admin")
def statistics():
    """High-level KPI counts across every module, plus DB file size."""
    attendance_records = Attendance.query.all()
    total_attendance = len(attendance_records)
    present_count = sum(1 for r in attendance_records if r.status == "Present")
    attendance_rate = (
        round((present_count / total_attendance) * 100, 1) if total_attendance else 0
    )

    project_status_counts = {
        status: Project.query.filter_by(status=status).count() for status in Project.STATUSES
    }

    db_path = current_app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
    db_size_kb = round(os.path.getsize(db_path) / 1024, 1) if os.path.exists(db_path) else 0

    stats = {
        "departments": Department.query.count(),
        "active_departments": Department.query.filter_by(status="Active").count(),
        "project_managers": ProjectManager.query.count(),
        "active_project_managers": ProjectManager.query.filter_by(is_active_flag=True).count(),
        "interns": Intern.query.count(),
        "projects": Project.query.count(),
        "project_status_counts": project_status_counts,
        "evaluations": Evaluation.query.count(),
        "total_attendance": total_attendance,
        "attendance_rate": attendance_rate,
        "notifications_sent": Notification.query.count(),
        "audit_log_entries": AuditLog.query.count(),
        "db_size_kb": db_size_kb,
    }

    return render_template("admin/statistics.html", stats=stats)


# ----------------------------------------------------------------------
# 6. User & Role Management (Super Admin only)
# ----------------------------------------------------------------------
# HR and Intern/Project Manager accounts already have their own
# dedicated CRUD elsewhere (they need extra profile fields such as
# department, CNIC, university, etc. -- routes/intern.py and
# routes/project_manager.py). This section gives the Super Admin a
# single place to see every login account in the system and to
# create/edit/delete the accounts that have no separate profile
# record (HR and Super Admin), plus change any account's role.
MANAGEABLE_ROLES = ("Super Admin", "HR")


@admin_bp.route("/users")
@login_required
@roles_required("Super Admin")
def list_users():
    """List every user account in the system, grouped by role.
    Optionally filtered to a single role via ?role=HR (used by the
    "HR Management" sidebar link so Super Admin can jump straight to
    HR accounts specifically)."""
    role_filter = request.args.get("role")
    query = User.query
    if role_filter:
        query = query.filter_by(role=role_filter)
    users = query.order_by(User.role, User.username).all()
    return render_template(
        "admin/users.html", users=users, manageable_roles=MANAGEABLE_ROLES, role_filter=role_filter
    )


@admin_bp.route("/users/add", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def add_user():
    """Create a new HR or Super Admin account."""
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        role = request.form.get("role", "").strip()
        password = request.form.get("password", "")

        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if not email:
            errors.append("Email is required.")
        if not username:
            errors.append("Username is required.")
        if role not in MANAGEABLE_ROLES:
            errors.append("Please select a valid role.")
        if not password or len(password) < 8:
            errors.append("Password must be at least 8 characters long.")

        if User.query.filter_by(email=email).first():
            errors.append("Another user already uses this email.")
        if User.query.filter_by(username=username).first():
            errors.append("Another user already uses this username.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "admin/user_form.html", user=None, form=request.form, manageable_roles=MANAGEABLE_ROLES
            )

        try:
            new_user = User(
                full_name=full_name, email=email, username=username, role=role
            )
            new_user.set_password(password)
            db.session.add(new_user)
            log_action(
                action="CREATE",
                description=f"Created {role} account '{username}'.",
                target_type="User",
            )
            db.session.commit()
            flash(f"Account '{username}' created successfully.", "success")
            return redirect(url_for("admin.list_users"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to create user account.")
            flash("Could not create the account due to a system error. Please try again.", "danger")

    return render_template(
        "admin/user_form.html", user=None, form=None, manageable_roles=MANAGEABLE_ROLES
    )


@admin_bp.route("/users/edit/<int:user_id>", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def edit_user(user_id):
    """Edit an HR or Super Admin account's details, password, or role."""
    user = User.query.get_or_404(user_id)
    if user.role not in MANAGEABLE_ROLES:
        flash(
            "This account has its own management page (Interns/Project Managers).",
            "info",
        )
        return redirect(url_for("admin.list_users"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        role = request.form.get("role", "").strip()
        password = request.form.get("password", "")

        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if not email:
            errors.append("Email is required.")
        if not username:
            errors.append("Username is required.")
        if role not in MANAGEABLE_ROLES:
            errors.append("Please select a valid role.")
        if password and len(password) < 8:
            errors.append("New password must be at least 8 characters long.")
        if user.id == current_user.id and role != user.role:
            errors.append("You cannot change your own role.")

        if User.query.filter(User.email == email, User.id != user.id).first():
            errors.append("Another user already uses this email.")
        if User.query.filter(User.username == username, User.id != user.id).first():
            errors.append("Another user already uses this username.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "admin/user_form.html", user=user, form=request.form, manageable_roles=MANAGEABLE_ROLES
            )

        try:
            user.full_name = full_name
            user.email = email
            user.username = username
            user.role = role
            if password:
                user.set_password(password)

            log_action(
                action="UPDATE",
                description=f"Updated account '{username}'.",
                target_type="User",
                target_id=user.id,
            )
            db.session.commit()
            flash(f"Account '{username}' updated successfully.", "success")
            return redirect(url_for("admin.list_users"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update user account #%s.", user.id)
            flash("Could not update the account due to a system error. Please try again.", "danger")

    return render_template(
        "admin/user_form.html", user=user, form=None, manageable_roles=MANAGEABLE_ROLES
    )


@admin_bp.route("/users/delete/<int:user_id>", methods=["POST"])
@login_required
@roles_required("Super Admin")
def delete_user(user_id):
    """Delete an HR or Super Admin account (never one's own account)."""
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot delete your own account while logged in.", "danger")
        return redirect(url_for("admin.list_users"))

    if user.role not in MANAGEABLE_ROLES:
        flash("This account must be removed from its own management page.", "danger")
        return redirect(url_for("admin.list_users"))

    try:
        username = user.username
        log_action(
            action="DELETE",
            description=f"Deleted account '{username}'.",
            target_type="User",
            target_id=user.id,
        )
        db.session.delete(user)
        db.session.commit()
        flash(f"Account '{username}' deleted successfully.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete user account #%s.", user_id)
        flash("Could not delete the account due to a system error. Please try again.", "danger")

    return redirect(url_for("admin.list_users"))
