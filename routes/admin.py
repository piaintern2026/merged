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

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_file
from flask_login import login_required, current_user

from extensions import db
from models import (
    User,
    Department,
    SubDepartment,
    ProjectManager,
    Intern,
    Project,
    Attendance,
    Evaluation,
    Leave,
    InternRotation,
    FinalReport,
    AuditLog,
    Notification,
    SystemSetting,
    EmailLog,
)
from utils import (
    PIA_CITIES,
    hr_city_scope,
    hr_module_visible,
    roles_required,
    paginate_query,
    save_profile_picture,
    delete_profile_picture,
    log_action,
    notify_user,
    DEFAULT_USER_PASSWORD,
)
from services.email_service import (
    send_staff_account_email,
    send_test_email,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ----------------------------------------------------------------------
# 1. Global Search
# ----------------------------------------------------------------------
@admin_bp.route("/search")
@login_required
@roles_required("Admin")
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
                db.or_(db.func.lower(Intern.full_name).ilike(like), Intern.cnic.ilike(like), Intern.university.ilike(like))
            )
            .limit(25)
            .all()
        )
        pms = (
            ProjectManager.query.filter(
                db.or_(db.func.lower(ProjectManager.full_name).ilike(like), ProjectManager.p_number.ilike(like))
            )
            .limit(25)
            .all()
        )
        departments = Department.query.filter(db.func.lower(Department.name).ilike(like)).limit(25).all()
        projects = Project.query.filter(db.func.lower(Project.title).ilike(like)).limit(25).all()

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
@roles_required("Admin")
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
    users = User.query.order_by(db.func.lower(User.username)).all()

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
@roles_required("Admin")
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

    settings_list = (
        SystemSetting.query.filter(~SystemSetting.key.like("mail_%"))
        .order_by(db.func.lower(SystemSetting.label))
        .all()
    )
    return render_template("admin/settings.html", settings_list=settings_list)


# ----------------------------------------------------------------------
# 4. Profile Management (HR's own account)
# ----------------------------------------------------------------------
@admin_bp.route("/profile", methods=["GET", "POST"])
@login_required
@roles_required("Station HR", "Admin")
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
            db.func.lower(User.username) == username, User.id != current_user.id
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
@roles_required("Admin")
def statistics():
    """High-level KPI counts across every module, plus real DB-backed
    breakdowns (departments, cities, attendance, leave, projects,
    rotations, monthly interns) for the Analytics section."""
    from datetime import date
    import calendar as _cal

    attendance_records = Attendance.query.all()
    total_attendance = len(attendance_records)
    present_count = sum(1 for r in attendance_records if r.status in Attendance.ATTENDED_STATUSES)
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
        "active_departments": Department.query.count(),
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

    # ------------------------------------------------------------------
    # Analytics breakdowns - all computed from live DB rows, no dummy/
    # static numbers anywhere below.
    # ------------------------------------------------------------------

    # Interns per department
    dept_rows = (
        db.session.query(db.func.lower(Department.name), db.func.count(Intern.id))
        .outerjoin(Intern, Intern.department_id == Department.id)
        .group_by(Department.id, db.func.lower(Department.name))
        .order_by(db.func.lower(Department.name))
        .all()
    )
    departments_chart = [{"label": name, "count": count} for name, count in dept_rows]

    # Interns per city - city is free-text on Intern, so group in
    # Python to normalise case/whitespace instead of relying on the
    # DB collation.
    city_counts = {}
    for (city,) in db.session.query(Intern.station).all():
        key = (city or "Unspecified").strip() or "Unspecified"
        city_counts[key] = city_counts.get(key, 0) + 1
    cities_chart = [{"label": city, "count": count} for city, count in city_counts.items()]

    # Attendance breakdown by status
    attendance_status_counts = {}
    for record in attendance_records:
        attendance_status_counts[record.status] = (
            attendance_status_counts.get(record.status, 0) + 1
        )
    attendance_chart = [{"label": s, "count": c} for s, c in attendance_status_counts.items()]

    # Leave requests by status
    leave_rows = (
        db.session.query(Leave.status, db.func.count(Leave.id))
        .group_by(Leave.status)
        .all()
    )
    leave_chart = [{"label": status, "count": count} for status, count in leave_rows]

    # Projects by status (reuse project_status_counts, already computed)
    projects_chart = [{"label": status, "count": count} for status, count in project_status_counts.items()]

    # Rotations per department (which department received the most
    # rotated-in interns)
    rotation_rows = (
        db.session.query(db.func.lower(Department.name), db.func.count(InternRotation.id))
        .join(InternRotation, InternRotation.to_department_id == Department.id)
        .group_by(Department.id, db.func.lower(Department.name))
        .order_by(db.func.lower(Department.name))
        .all()
    )
    rotations_chart = [{"label": name, "count": count} for name, count in rotation_rows]

    # New interns onboarded per month, last 12 months
    today = date.today()
    month_labels = []
    month_keys = []
    year, month = today.year, today.month
    for _ in range(12):
        month_labels.append(f"{_cal.month_abbr[month]} {year}")
        month_keys.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    month_labels.reverse()
    month_keys.reverse()

    monthly_counts = {key: 0 for key in month_keys}
    start_bound = date(month_keys[0][0], month_keys[0][1], 1)
    for (start_dt,) in db.session.query(Intern.internship_start_date).filter(
        Intern.internship_start_date >= start_bound
    ).all():
        key = (start_dt.year, start_dt.month)
        if key in monthly_counts:
            monthly_counts[key] += 1

    monthly_interns_chart = [
        {"label": month_labels[i], "count": monthly_counts[key]}
        for i, key in enumerate(month_keys)
    ]

    charts = {
        "departments": departments_chart,
        "cities": cities_chart,
        "attendance": attendance_chart,
        "leave": leave_chart,
        "projects": projects_chart,
        "rotations": rotations_chart,
        "monthly_interns": monthly_interns_chart,
    }

    return render_template("admin/statistics.html", stats=stats, charts=charts)


# ----------------------------------------------------------------------
# 6. User & Role Management (Admin only)
# ----------------------------------------------------------------------
# HR and Intern/Project Manager accounts already have their own
# dedicated CRUD elsewhere (they need extra profile fields such as
# department, CNIC, university, etc. -- routes/intern.py and
# routes/project_manager.py). This section gives the Admin a
# single place to see every login account in the system and to
# create/edit/delete the accounts that have no separate profile
# record (HR and Admin), plus change any account's role.
MANAGEABLE_ROLES = ("Admin", "Station HR")

# Admin accounts can no longer be created from the UI -- only via
# direct database/manual creation. "Station HR" is the only role selectable when
# adding a brand-new account. Existing Admin accounts are untouched
# and can still be edited (see edit_user, which allows keeping the
# "Admin" role for an account that already has it).
CREATABLE_ROLES = ("Station HR",)


@admin_bp.route("/users")
@login_required
@roles_required("Admin")
def list_users():
    """List every user account in the system, with search, role/status
    filters and pagination. Optionally filtered to a single role via
    ?role=HR (used by the "HR Management" sidebar link so Admin
    can jump straight to HR accounts specifically)."""
    role_filter = request.args.get("role", "")
    status_filter = request.args.get("status", "")
    search_text = request.args.get("q", "").strip()

    query = User.query.filter(User.role != "Admin")

    # Temporary demo switch (Config.HIDE_HR_MODULE): keep Station HR
    # accounts out of this list entirely while HR is hidden, and don't
    # let a direct/old link to ?role=Station HR reveal them either.
    # No data is touched -- the accounts still exist and can log in.
    if not hr_module_visible():
        query = query.filter(User.role != "Station HR")
        if role_filter == "Station HR":
            role_filter = ""

    if role_filter:
        query = query.filter_by(role=role_filter)
    if status_filter == "active":
        query = query.filter_by(is_active_account=True, is_locked=False)
    elif status_filter == "inactive":
        query = query.filter_by(is_active_account=False)
    elif status_filter == "locked":
        query = query.filter_by(is_locked=True)
    if search_text:
        like = f"%{search_text}%"
        query = query.filter(
            db.or_(User.full_name.ilike(like), db.func.lower(User.username).ilike(like), User.email.ilike(like))
        )

    query = query.order_by(User.created_at.desc())
    page = request.args.get("page", 1, type=int)
    pagination = paginate_query(query, page)

    all_roles_query = db.session.query(User.role).filter(User.role != "Admin")
    if not hr_module_visible():
        all_roles_query = all_roles_query.filter(User.role != "Station HR")
    all_roles = [row[0] for row in all_roles_query.distinct().all()]

    return render_template(
        "admin/users.html",
        users=pagination.items,
        pagination=pagination,
        manageable_roles=MANAGEABLE_ROLES,
        role_filter=role_filter,
        status_filter=status_filter,
        search_text=search_text,
        all_roles=all_roles,
    )


@admin_bp.route("/users/add", methods=["GET", "POST"])
@login_required
@roles_required("Admin")
def add_user():
    """Create a new HR or Admin account."""
    # Temporary demo switch (Config.HIDE_HR_MODULE): this route only
    # ever creates "Station HR" accounts (CREATABLE_ROLES), so while
    # HR is hidden for a demo there is nothing legitimate for it to
    # do -- block it rather than leaving an unlisted-but-reachable
    # HR-account-creation page. No data/route/permission is removed,
    # this just declines the request while the flag is on.
    if not hr_module_visible():
        flash("Station HR management is temporarily unavailable.", "warning")
        return redirect(url_for("admin.list_users"))
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip() or email  # fallback: email becomes username
        role = request.form.get("role", "").strip()
        password = request.form.get("password", "")
        city = request.form.get("city", "").strip()

        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if not email:
            errors.append("Email is required.")
        if not username:
            errors.append("Username is required.")
        if role not in CREATABLE_ROLES:
            errors.append("Please select a valid role. Admin accounts cannot be created from the UI.")
        # Optional: if left blank, the account gets the standard default
        # password (see utils.DEFAULT_USER_PASSWORD) instead of forcing
        # the admin to type one. If they do type one, it still has to
        # meet the normal minimum length.
        if password and len(password) < 8:
            errors.append("Password must be at least 8 characters long.")
        if role == "Station HR" and not city:
            errors.append("City is required for Station HR accounts.")
        elif role == "Station HR" and city not in PIA_CITIES:
            errors.append("Please select a valid city.")

        if User.query.filter_by(email=email).first():
            errors.append("Another user already uses this email.")
        if User.query.filter_by(username=username).first():
            errors.append("Another user already uses this username.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "admin/user_form.html", user=None, form=request.form, manageable_roles=CREATABLE_ROLES,
                pia_cities=PIA_CITIES,
            )

        try:
            new_user = User(
                full_name=full_name,
                email=email,
                username=username,
                role=role,
                city=city if role == "Station HR" else None,
            )
            final_password = password or DEFAULT_USER_PASSWORD
            new_user.set_password(final_password)
            db.session.add(new_user)
            db.session.flush()
            notify_user(
                new_user.id,
                f"Welcome, {full_name}! Your {role} account has been created.",
                icon="bi-person-check",
                notification_type="General",
            )
            log_action(
                action="CREATE",
                description=f"Created {role} account '{username}'.",
                target_type="User",
            )
            db.session.commit()
            send_staff_account_email(new_user, final_password, created_by=current_user.display_name())
            flash(f"Account '{username}' created successfully.", "success")
            return redirect(url_for("admin.list_users"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to create user account.")
            flash("Could not create the account due to a system error. Please try again.", "danger")

    return render_template(
        "admin/user_form.html", user=None, form=None, manageable_roles=CREATABLE_ROLES,
        pia_cities=PIA_CITIES,
    )


@admin_bp.route("/users/edit/<int:user_id>", methods=["GET", "POST"])
@login_required
@roles_required("Admin")
def edit_user(user_id):
    """Edit an HR or Admin account's details, password, or role."""
    user = User.query.get_or_404(user_id)
    if user.role not in MANAGEABLE_ROLES:
        flash(
            "This account has its own management page (Interns/Project Managers).",
            "info",
        )
        return redirect(url_for("admin.list_users"))

    # Admin can no longer be assigned from the UI. If this account
    # is already an Admin, its role dropdown still offers "Super
    # Admin" so the account isn't forced to change -- but an HR account
    # being edited only ever offers "Station HR", so nobody can be promoted to
    # Admin through this form.
    editable_roles = MANAGEABLE_ROLES if user.role == "Admin" else CREATABLE_ROLES

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        role = request.form.get("role", "").strip()
        password = request.form.get("password", "")
        city = request.form.get("city", "").strip()

        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if not email:
            errors.append("Email is required.")
        if not username:
            errors.append("Username is required.")
        if role not in editable_roles:
            errors.append("Please select a valid role.")
        if password and len(password) < 8:
            errors.append("New password must be at least 8 characters long.")
        if user.id == current_user.id and role != user.role:
            errors.append("You cannot change your own role.")
        if role == "Station HR" and not city:
            errors.append("City is required for Station HR accounts.")
        elif role == "Station HR" and city not in PIA_CITIES:
            errors.append("Please select a valid city.")

        if User.query.filter(User.email == email, User.id != user.id).first():
            errors.append("Another user already uses this email.")
        if User.query.filter(db.func.lower(User.username) == username, User.id != user.id).first():
            errors.append("Another user already uses this username.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "admin/user_form.html", user=user, form=request.form, manageable_roles=editable_roles,
                pia_cities=PIA_CITIES,
            )

        try:
            user.full_name = full_name
            user.email = email
            user.username = username
            user.role = role
            user.city = city if role == "Station HR" else None
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
        "admin/user_form.html", user=user, form=None, manageable_roles=editable_roles,
        pia_cities=PIA_CITIES,
    )


@admin_bp.route("/users/reset-password/<int:user_id>", methods=["POST"])
@login_required
@roles_required("Admin")
def reset_user_password(user_id):
    """Reset any user's password to a value the Admin sets, and
    require them to change it on next login."""
    user = User.query.get_or_404(user_id)
    new_password = request.form.get("new_password", "")

    if not new_password or len(new_password) < 8:
        flash("New password must be at least 8 characters long.", "danger")
        return redirect(url_for("admin.list_users"))

    try:
        user.set_password(new_password)
        user.force_password_reset = True
        log_action(
            action="UPDATE",
            description=f"Reset password for account '{user.username}'.",
            target_type="User",
            target_id=user.id,
        )
        db.session.commit()
        send_staff_account_email(user, new_password, created_by=f"{current_user.display_name()} (password reset)")
        flash(f"Password for '{user.username}' has been reset. They must set a new password at next login.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to reset password for user #%s.", user.id)
        flash("Could not reset the password due to a system error. Please try again.", "danger")

    return redirect(url_for("admin.list_users"))


@admin_bp.route("/users/toggle-active/<int:user_id>", methods=["POST"])
@login_required
@roles_required("Admin")
def toggle_active(user_id):
    """Activate or deactivate any user's account."""
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot deactivate your own account while logged in.", "danger")
        return redirect(url_for("admin.list_users"))

    try:
        user.is_active_account = not user.is_active_account
        if user.role == "Project Manager" and user.project_manager_profile:
            user.project_manager_profile.is_active_flag = user.is_active_account
        state = "activated" if user.is_active_account else "deactivated"
        log_action(
            action="UPDATE",
            description=f"Account '{user.username}' was {state}.",
            target_type="User",
            target_id=user.id,
        )
        db.session.commit()
        flash(f"Account '{user.username}' has been {state}.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to toggle active status for user #%s.", user.id)
        flash("Could not update account status due to a system error. Please try again.", "danger")

    return redirect(url_for("admin.list_users"))


@admin_bp.route("/users/toggle-lock/<int:user_id>", methods=["POST"])
@login_required
@roles_required("Admin")
def toggle_lock(user_id):
    """Lock or unlock any user's account (blocks login while locked)."""
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot lock your own account while logged in.", "danger")
        return redirect(url_for("admin.list_users"))

    try:
        user.is_locked = not user.is_locked
        if not user.is_locked:
            user.failed_login_attempts = 0
        state = "locked" if user.is_locked else "unlocked"
        log_action(
            action="UPDATE",
            description=f"Account '{user.username}' was {state}.",
            target_type="User",
            target_id=user.id,
        )
        db.session.commit()
        flash(f"Account '{user.username}' has been {state}.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to toggle lock status for user #%s.", user.id)
        flash("Could not update lock status due to a system error. Please try again.", "danger")

    return redirect(url_for("admin.list_users"))


@admin_bp.route("/users/force-password-reset/<int:user_id>", methods=["POST"])
@login_required
@roles_required("Admin")
def force_password_reset_flag(user_id):
    """Flag an account so the user must change their password at next login."""
    user = User.query.get_or_404(user_id)

    try:
        user.force_password_reset = True
        log_action(
            action="UPDATE",
            description=f"Account '{user.username}' flagged for forced password reset.",
            target_type="User",
            target_id=user.id,
        )
        db.session.commit()
        flash(f"'{user.username}' will be required to reset their password at next login.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to flag forced password reset for user #%s.", user.id)
        flash("Could not update the account due to a system error. Please try again.", "danger")

    return redirect(url_for("admin.list_users"))





# ----------------------------------------------------------------------
# 7. Internship Progress Tracking (Admin only)
# ----------------------------------------------------------------------
def _intern_progress_row(intern: Intern) -> dict:
    """Compute every Internship Progress data point for one intern from
    real records: attendance %, evaluation score, rotation history,
    completion %, days remaining and final report status."""
    from routes.rotation import _department_segments
    from utils import today_pkt

    today = today_pkt()
    segments = _department_segments(intern)

    current_segment = segments[-1] if segments else None
    previous_departments = [
        seg["department"].name for seg in segments[:-1] if seg.get("department")
    ]

    # Attendance %
    attendance_records = Attendance.query.filter_by(intern_id=intern.id).all()
    total_attendance = len(attendance_records)
    present_count = sum(1 for r in attendance_records if r.status in Attendance.ATTENDED_STATUSES)
    attendance_pct = round((present_count / total_attendance) * 100, 1) if total_attendance else 0

    # Evaluation score (average of all PM + HR evaluations, out of 10 -> shown as %)
    evaluations = Evaluation.query.filter_by(intern_id=intern.id).all()
    if evaluations:
        avg_score = sum(
            (e.technical_skills + e.communication + e.discipline + e.learning + e.teamwork + e.attendance_score) / 6
            for e in evaluations
        ) / len(evaluations)
        evaluation_pct = round(avg_score * 10, 1)  # 1-10 scale -> percentage
    else:
        evaluation_pct = None

    # Projects
    all_projects = Project.query.filter(Project.interns.any(id=intern.id)).all()
    completed_projects = [p for p in all_projects if p.status in ("Completed", "Approved")]
    current_project = next((p for p in all_projects if p.status not in ("Completed", "Approved")), None)

    # Internship completion % based on elapsed time.
    total_days = max((intern.internship_end_date - intern.internship_start_date).days, 1)
    elapsed_days = max((min(today, intern.internship_end_date) - intern.internship_start_date).days, 0)
    completion_pct = round(min(elapsed_days / total_days, 1.0) * 100, 1)
    days_remaining = max((intern.internship_end_date - today).days, 0)

    final_report = FinalReport.query.filter_by(intern_id=intern.id).first()

    return {
        "intern": intern,
        "registration_date": intern.created_at,
        "current_department": current_segment["department"] if current_segment else intern.department,
        "previous_departments": previous_departments,
        "current_project": current_project,
        "completed_projects_count": len(completed_projects),
        "total_projects_count": len(all_projects),
        "attendance_pct": attendance_pct,
        "evaluation_pct": evaluation_pct,
        "rotations_count": max(len(segments) - 1, 0) if segments else 0,
        "completion_pct": completion_pct,
        "days_remaining": days_remaining,
        "final_report_status": "Submitted" if final_report else "Not Submitted",
    }


@admin_bp.route("/internship-progress")
@login_required
@roles_required("Station HR", "Admin")
def internship_progress():
    """Cross-intern Internship Progress Tracking board: registration date,
    current/previous departments, current & completed projects, attendance
    %, evaluation score, rotation count, completion % and days remaining
    -- all computed live from real records, shown with progress bars.
    A Station HR only sees interns whose station/city matches their own;
    Admin sees every city with no restriction."""
    department_id = request.args.get("department_id", type=int)
    sub_department_id = request.args.get("sub_department_id", type=int)
    search_text = request.args.get("q", "").strip()
    is_city_scoped, hr_city = hr_city_scope()

    query = Intern.query
    if is_city_scoped:
        query = query.filter(Intern.station == hr_city)
    if department_id:
        query = query.filter_by(department_id=department_id)
    if sub_department_id:
        query = query.filter_by(sub_department_id=sub_department_id)
    if search_text:
        like = f"%{search_text}%"
        query = query.filter(db.func.lower(Intern.full_name).ilike(like))

    interns = query.order_by(db.func.lower(Intern.full_name)).all()
    rows = [_intern_progress_row(i) for i in interns]

    departments = Department.query.order_by(db.func.lower(Department.name)).all()
    if is_city_scoped:
        departments = [d for d in departments if d.city == hr_city]
    sub_departments = SubDepartment.query.order_by(db.func.lower(SubDepartment.name)).all()
    if department_id:
        sub_departments = [s for s in sub_departments if s.department_id == department_id]

    return render_template(
        "admin/internship_progress.html",
        rows=rows,
        departments=departments,
        department_id=department_id,
        sub_departments=sub_departments,
        sub_department_id=sub_department_id,
        search_text=search_text,
        hr_city=hr_city,
    )


# ----------------------------------------------------------------------
# 8. Workforce Insights (Admin / HR) -- merges the former
#    "Department Comparison" and "City Statistics" widgets into one
#    enterprise analytics section, per Module 5 consolidation.
# ----------------------------------------------------------------------
@admin_bp.route("/workforce-insights")
@login_required
@roles_required("Station HR", "Admin")
def workforce_insights():
    """Combined organisation analytics: per-department performance
    comparison plus full city-based analytics (total interns, department
    distribution, attendance, project statistics, active/completed
    interns, etc.) in a single view. Admin sees every city with no
    restriction; a Station HR only ever sees analytics for their own
    assigned city -- all figures computed live from real records."""
    from utils import today_pkt, PIA_CITIES

    today = today_pkt()
    is_city_scoped, hr_city = hr_city_scope()

    departments = Department.query.order_by(db.func.lower(Department.name)).all()
    if is_city_scoped:
        departments = [d for d in departments if d.city == hr_city]
    dept_rows = []

    for dept in departments:
        dept_interns = Intern.query.filter_by(department_id=dept.id).all()
        total_interns = len(dept_interns)
        active_interns = sum(
            1 for i in dept_interns if i.internship_start_date <= today <= i.internship_end_date
        )

        dept_projects = Project.query.filter_by(department_id=dept.id).all()
        completed_projects = sum(1 for p in dept_projects if p.status in ("Completed", "Approved"))
        pending_projects = sum(1 for p in dept_projects if p.status in ("Pending", "Working", "Submitted"))

        intern_ids = [i.id for i in dept_interns]
        if intern_ids:
            attendance_records = Attendance.query.filter(Attendance.intern_id.in_(intern_ids)).all()
            total_att = len(attendance_records)
            present_att = sum(1 for r in attendance_records if r.status in Attendance.ATTENDED_STATUSES)
            avg_attendance = round((present_att / total_att) * 100, 1) if total_att else 0

            evaluations = Evaluation.query.filter(Evaluation.intern_id.in_(intern_ids)).all()
            if evaluations:
                avg_evaluation = round(
                    sum(
                        (e.technical_skills + e.communication + e.discipline + e.learning + e.teamwork + e.attendance_score) / 6
                        for e in evaluations
                    ) / len(evaluations) * 10,
                    1,
                )
            else:
                avg_evaluation = 0

            completed_internships = sum(1 for i in dept_interns if i.internship_end_date < today)
            completion_rate = round((completed_internships / total_interns) * 100, 1) if total_interns else 0
        else:
            avg_attendance = 0
            avg_evaluation = 0
            completion_rate = 0

        rotations_completed = InternRotation.query.filter(
            InternRotation.to_department_id == dept.id, InternRotation.end_date.isnot(None)
        ).count()

        # Division/Section breakdown -- interns/projects within this
        # department split out by Division/Section so HR can see which
        # specific sub-teams are driving the department's numbers,
        # not just a department-only total.
        sub_dept_breakdown = []
        for sub in sorted(dept.sub_departments, key=lambda s: s.name.lower()):
            sub_interns = [i for i in dept_interns if i.sub_department_id == sub.id]
            sub_projects = [p for p in dept_projects if p.sub_department_id == sub.id]
            sub_dept_breakdown.append(
                {
                    "sub_department": sub,
                    "total_interns": len(sub_interns),
                    "active_interns": sum(
                        1 for i in sub_interns if i.internship_start_date <= today <= i.internship_end_date
                    ),
                    "total_projects": len(sub_projects),
                }
            )

        dept_rows.append(
            {
                "department": dept,
                "total_interns": total_interns,
                "active_interns": active_interns,
                "completed_projects": completed_projects,
                "pending_projects": pending_projects,
                "avg_attendance": avg_attendance,
                "avg_evaluation": avg_evaluation,
                "completion_rate": completion_rate,
                "rotations_completed": rotations_completed,
                "sub_departments": sub_dept_breakdown,
            }
        )

    intern_city_rows = dict(
        db.session.query(Intern.station, db.func.count(Intern.id)).group_by(Intern.station).all()
    )
    pm_city_rows = dict(
        db.session.query(ProjectManager.city, db.func.count(ProjectManager.id))
        .group_by(ProjectManager.city)
        .all()
    )

    cities_in_scope = [hr_city] if is_city_scoped and hr_city else PIA_CITIES
    interns_by_city = [{"label": c, "count": intern_city_rows.get(c, 0)} for c in cities_in_scope]
    pms_by_city = [{"label": c, "count": pm_city_rows.get(c, 0)} for c in cities_in_scope]

    # ------------------------------------------------------------------
    # Full city-based analytics: for each city in scope, compute total
    # interns, department distribution, attendance, project statistics
    # and active/completed interns -- exactly like dept_rows above but
    # keyed by city instead of department. Admin gets every city
    # with no restriction; a Station HR only ever gets their own city.
    # ------------------------------------------------------------------
    city_rows = []
    for city in cities_in_scope:
        city_interns = Intern.query.filter_by(station=city).all()
        total_city_interns = len(city_interns)
        active_city_interns = sum(
            1 for i in city_interns if i.internship_start_date <= today <= i.internship_end_date
        )
        completed_city_interns = sum(1 for i in city_interns if i.internship_end_date < today)

        dept_distribution = {}
        for i in city_interns:
            dept_name = i.department.name if i.department else "Unassigned"
            dept_distribution[dept_name] = dept_distribution.get(dept_name, 0) + 1

        city_intern_ids = [i.id for i in city_interns]
        city_dept_ids = [d.id for d in Department.query.filter_by(city=city).all()]
        city_projects = (
            Project.query.filter(Project.department_id.in_(city_dept_ids)).all()
            if city_dept_ids
            else []
        )
        completed_city_projects = sum(1 for p in city_projects if p.status in ("Completed", "Approved"))
        pending_city_projects = sum(1 for p in city_projects if p.status in ("Pending", "Working", "Submitted"))

        if city_intern_ids:
            city_attendance = Attendance.query.filter(Attendance.intern_id.in_(city_intern_ids)).all()
            total_city_att = len(city_attendance)
            present_city_att = sum(1 for r in city_attendance if r.status in Attendance.ATTENDED_STATUSES)
            avg_city_attendance = round((present_city_att / total_city_att) * 100, 1) if total_city_att else 0
        else:
            avg_city_attendance = 0

        city_rows.append(
            {
                "city": city,
                "total_interns": total_city_interns,
                "active_interns": active_city_interns,
                "completed_interns": completed_city_interns,
                "department_distribution": sorted(dept_distribution.items()),
                "total_projects": len(city_projects),
                "completed_projects": completed_city_projects,
                "pending_projects": pending_city_projects,
                "avg_attendance": avg_city_attendance,
                "pm_count": pm_city_rows.get(city, 0),
            }
        )

    return render_template(
        "admin/workforce_insights.html",
        dept_rows=dept_rows,
        interns_by_city=interns_by_city,
        pms_by_city=pms_by_city,
        city_rows=city_rows,
        is_city_scoped=is_city_scoped,
        hr_city=hr_city,
    )


# ----------------------------------------------------------------------
# 10. Database Backup (Admin only) -- part of the Quick Actions panel
# ----------------------------------------------------------------------
@admin_bp.route("/backup-database")
@login_required
@roles_required("Admin")
def backup_database():
    """Download a snapshot of the live database. SQLite deployments get
    a direct copy of the .db file; other engines (e.g. Postgres/Neon in
    production) aren't file-backed, so we point the Admin to the
    hosting provider's own backup/export tools instead of pretending to
    produce a real dump we can't safely generate here."""
    db_uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if not db_uri.startswith("sqlite:///"):
        flash(
            "This database runs on a managed hosting engine (not SQLite). "
            "Use your hosting provider's built-in backup/export tools for a full database backup.",
            "info",
        )
        return redirect(url_for("dashboard.index"))

    db_path = db_uri.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        flash("Could not locate the database file to back up.", "danger")
        return redirect(url_for("dashboard.index"))

    log_action(action="READ", description="Downloaded a database backup.", target_type="System")
    db.session.commit()

    from datetime import datetime as _dt

    timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        db_path, as_attachment=True, download_name=f"pia_database_backup_{timestamp}.db"
    )


# ----------------------------------------------------------------------
# Email Notification System: Admin Email Settings
# ----------------------------------------------------------------------
EMAIL_SETTING_KEYS = [k for k, *_ in SystemSetting.EMAIL_DEFAULTS]


@admin_bp.route("/email-settings", methods=["GET", "POST"])
@login_required
@roles_required("Admin", "Station HR")
def email_settings():
    """Configure SMTP settings, send a test email, and review the
    email delivery log -- all from one Admin-only page."""
    if request.method == "POST":
        updated = []
        for key in EMAIL_SETTING_KEYS:
            setting = SystemSetting.query.filter_by(key=key).first()
            if setting is None:
                continue
            if key == "mail_use_tls" or key == "mail_use_ssl" or key == "mail_suppress_send":
                new_value = "true" if request.form.get(key) == "on" else "false"
            else:
                new_value = request.form.get(key, "").strip()
                if key == "mail_password" and not new_value:
                    # Blank password field means "keep the existing one".
                    continue
            if new_value != setting.value:
                setting.value = new_value
                updated.append(setting.label)

        if updated:
            try:
                log_action(
                    action="UPDATE",
                    description=f"Email settings updated: {', '.join(updated)}.",
                    target_type="SystemSetting",
                )
                db.session.commit()
                flash("Email settings updated successfully.", "success")
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Failed to update email settings.")
                flash("Could not update email settings due to a system error. Please try again.", "danger")
        else:
            flash("No changes were made.", "info")

        return redirect(url_for("admin.email_settings"))

    settings_list = (
        SystemSetting.query.filter(SystemSetting.key.in_(EMAIL_SETTING_KEYS))
        .order_by(SystemSetting.key)
        .all()
    )
    settings_by_key = {s.key: s for s in settings_list}

    logs = (
        EmailLog.query.order_by(EmailLog.created_at.desc()).limit(100).all()
    )

    return render_template(
        "admin/email_settings.html", settings=settings_by_key, logs=logs
    )


@admin_bp.route("/email-settings/test", methods=["POST"])
@login_required
@roles_required("Admin", "Station HR")
def email_settings_test():
    """Send a synchronous test email using the currently saved SMTP
    settings, and report success/failure immediately."""
    recipient = request.form.get("test_recipient", "").strip() or current_user.email
    success, message = send_test_email(recipient)
    flash(message, "success" if success else "danger")
    return redirect(url_for("admin.email_settings"))

