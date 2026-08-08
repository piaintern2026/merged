"""
utils.py
--------
Shared helper functions and decorators used across multiple route
modules. Centralising these avoids code duplication (DRY principle).
"""

import os
import re
import uuid
from datetime import datetime, date, timezone
from functools import wraps
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import current_app, flash, redirect, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename

# ----------------------------------------------------------------------
# Pakistan Standard Time (PKT, UTC+5, no daylight saving) helpers.
# The whole application standardises on PKT for every timestamp it
# shows or stores -- attendance, clock-in/out, task/project
# submissions, audit logs, notifications, etc. -- so there is a single
# source of truth instead of a mix of server-local and UTC times.
# ----------------------------------------------------------------------
PKT = ZoneInfo("Asia/Karachi")

# ----------------------------------------------------------------------
# City-based Management: the fixed set of station cities that both
# Interns and Project Managers can be assigned to. Kept as a single
# source of truth here so forms, validation and reports never drift
# out of sync with one another.
# ----------------------------------------------------------------------
PIA_CITIES = sorted(
    [
        "Karachi",
        "Sukkur",
        "Faisalabad",
        "Multan",
        "Rawalpindi",
        "Lahore",
        "Islamabad",
        "Peshawar",
        "Quetta",
    ],
    key=str.lower,
)

# ----------------------------------------------------------------------
# Department Management: the fixed Department -> Division/Section
# hierarchy used everywhere a department is picked (Intern
# Registration, Project Creation, Project Assignment, Filters, Reports,
# "Add Department" form, etc). This dict is the single source of truth:
# - its keys are the only valid top-level Department names
# - 
# models.department.Department / models.department.SubDepartment rows
# are seeded from this at startup (see app.py:run_schema_migrations),
# giving normalized, foreign-key-backed tables instead of a flat list.
# Departments with no sub-departments (e.g. Internal Audit) map to an
# empty list.
# ----------------------------------------------------------------------
DEPARTMENT_HIERARCHY = {
    "Ground Operations": [
        "Passenger Handling",
        "Ramp Services",
        "Security & Vigilance",
        "Food Service",
        "Facilities Management",
    ],
    "Flight Operations": [
        "Pilot Crew Training",
        "Pilot Standard Inspection",
        "Technical Operations",
        "Planning & Scheduling",
        "Central Controlling",
        "Fuel Control",
        "South Operations Training",
    ],
    "Commercial": [
        "Revenue Management",
        "Scheduling",
        "Passenger Sales",
        "Industry Affairs",
        "Cargo",
        "Digital Marketing",
    ],
    "Engineering & Maintenance": [
        "MOC",
        "PPOH",
        "Airworthiness Management",
        "CMD",
        "Line Maintenance",
        "Base Maintenance",
        "EBD",
    ],
    "Finance": [
        "Fund Management",
        "Revenue Accounting",
        "Accounting",
        "Budgeting",
    ],
    "Corporate": [
        "Economic Planning & Analytics",
        "Fleet Planning",
    ],
    "Human Resource": [
        "Organisation Development",
        "HRM",
        "Admin & Discipline",
        "Welfare & IR",
        "Flight Services",
        "Medical Services",
    ],
    "Information Technology": [
        "IT Ops",
        "Digital Systems",
        "IT Infrastructure",
    ],
    "Training & Development": [
        "PTC",
    ],
    "Supply Chain": [
        "Procurement",
        "Logistics",
        "Contract Management",
    ],
    "Internal Audit": [],
    "Maullaly": [],
}

# Backward-compatible alias: the top-level department names, in the
# same order/spelling used by the "Add Department" dropdown before the
# Department -> Division/Section hierarchy existed. Anything that used
# to import PIA_DEPARTMENTS keeps working unchanged.
PIA_DEPARTMENTS = sorted(DEPARTMENT_HIERARCHY.keys(), key=str.lower)

# ----------------------------------------------------------------------
# Short city codes used purely for display (dropdowns, PM/department
# labels). These are cosmetic only -- the underlying database values
# (Department.city / ProjectManager.city) always stay the full PIA_CITIES
# name; nothing here is stored, so existing data/queries/filters that key
# off the full city name are completely unaffected.
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# Shared validation patterns for intern registration (manual form +
# Excel import). Centralised here so both paths always apply the exact
# same rules.
# ----------------------------------------------------------------------
# Standard Pakistani CNIC, with or without dashes: 13 digits, optionally
# grouped 5-7-1 (e.g. "42101-1234567-1" or "4210112345671").
# ----------------------------------------------------------------------
# Default password assigned when an administrator manually creates a
# user account (Super Admin/Station HR via routes/admin.py, or the
# linked login account created alongside an Intern/Project Manager
# profile) without typing a specific password. It is always hashed
# the same way as any other password (see models.user.User.set_password)
# before being stored, and the user can change it after logging in via
# the existing "Change Password" flow -- this is purely a convenience
# default, not a special code path.
# ----------------------------------------------------------------------
DEFAULT_USER_PASSWORD = "12345678"

CNIC_RE = re.compile(r"^\d{5}-?\d{7}-?\d{1}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Pakistani mobile numbers: optional +92/0092/0 prefix, then 3XXXXXXXXX.
PHONE_RE = re.compile(r"^(\+92|0092|0)?3\d{9}$")


def normalize_cnic(value: str) -> str:
    """Return the CNIC formatted as NNNNN-NNNNNNN-N (the standard
    display format), regardless of whether dashes were provided."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 13:
        return (value or "").strip()
    return f"{digits[0:5]}-{digits[5:12]}-{digits[12]}"


def normalize_phone(value: str) -> str:
    """Collapse stray spaces/dashes in a phone number for storage."""
    return re.sub(r"[\s-]", "", (value or "").strip())


CITY_CODES = {
    "Karachi": "KHI",
    "Sukkur": "SKZ",
    "Faisalabad": "FSD",
    "Multan": "MUX",
    "Lahore": "LHE",
    "Islamabad": "ISB",
    "Peshawar": "PEW",
    "Quetta": "UET",
    "Rawalpindi": "RWP",


}


def city_code(city: str | None) -> str:
    """Return the short display code for a PIA city, or the original
    value unchanged if it isn't one of the known cities (never hides
    unrecognised/legacy data)."""
    if not city:
        return ""
    return CITY_CODES.get(city, city)


def department_display(dept) -> str:
    """Display label for a Department: 'Department-City' (e.g. 'IT-LHE').
    Purely cosmetic -- used in dropdowns/forms/reports; the stored
    Department.name/city values are never changed."""
    if not dept:
        return "—"
    code = city_code(getattr(dept, "city", None))
    return f"{dept.name}-{code}" if code else dept.name


def pm_display(pm) -> str:
    """Display label for a Project Manager: 'Name-City-Department'
    (e.g. 'Fariz-LHE-IT'). Purely cosmetic -- used in dropdowns/forms/
    filters/reports; the stored ProjectManager fields are never changed."""
    if not pm:
        return "—"
    parts = [pm.full_name]
    code = city_code(getattr(pm, "city", None))
    if code:
        parts.append(code)
    dept = getattr(pm, "department", None)
    if dept is not None:
        parts.append(dept.name)
    return "-".join(parts)


def now_pkt() -> datetime:
    """Current naive datetime representing Pakistan Standard Time wall-clock.

    Naive (no tzinfo) on purpose: the DB columns are plain DateTime
    columns, so storing a naive PKT value keeps every timestamp
    consistently in PKT regardless of the server's own timezone,
    without SQLite silently dropping/mismatching offset info.
    """
    return datetime.now(PKT).replace(tzinfo=None)


def today_pkt() -> date:
    """Current date in Pakistan Standard Time."""
    return now_pkt().date()


def to_pkt(dt):
    """Convert a stored datetime to a PKT-aware datetime for display.

    Handles datetimes that already carry tzinfo (e.g. legacy UTC-aware
    values) as well as naive ones -- naive values are assumed to
    already be PKT wall-clock (the storage convention used across the
    app), UNLESS they look like they were saved before this fix, in
    which case callers should use the value as-is since it is already
    PKT after migration.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(PKT)
    return dt.replace(tzinfo=PKT)


def format_pkt(dt, fmt: str = "%d %b %Y, %I:%M %p"):
    """Format a datetime as a PKT string, e.g. '17 Jul 2026, 03:45 PM'."""
    dt = to_pkt(dt)
    if dt is None:
        return "-"
    return dt.strftime(fmt)


def allowed_image(filename: str) -> bool:
    """Check whether the uploaded file has an allowed image extension."""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]
    )


def save_profile_picture(file_storage) -> str | None:
    """
    Save an uploaded profile picture to the upload folder with a unique
    filename and return the stored filename (or None if no valid file
    was provided).
    """
    if not file_storage or file_storage.filename == "":
        return None

    if not allowed_image(file_storage.filename):
        raise ValueError("Invalid image format. Allowed: png, jpg, jpeg, gif.")

    original_name = secure_filename(file_storage.filename)
    extension = original_name.rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{extension}"

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_folder, exist_ok=True)
    file_storage.save(os.path.join(upload_folder, unique_name))

    return unique_name


def delete_profile_picture(filename: str) -> None:
    """Delete a previously uploaded profile picture from disk, if present."""
    if not filename:
        return
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass  # Non-fatal: leftover file, safe to ignore.


def paginate_query(query, page: int, per_page: int | None = None):
    """
    Paginate a SQLAlchemy query using Flask-SQLAlchemy's built-in
    paginate(), with error_out disabled so an out-of-range page number
    degrades gracefully (empty page) instead of raising a 404.
    Defaults to Config.ITEMS_PER_PAGE when per_page isn't specified.
    Returns a Pagination object exposing .items, .pages, .page, etc.
    """
    if per_page is None:
        per_page = current_app.config.get("ITEMS_PER_PAGE", 20)
    return query.paginate(page=page, per_page=per_page, error_out=False)


def roles_required(*roles):
    """
    Decorator factory implementing role-based access control.
    Usage: @roles_required("Station HR")

    "Super Admin" always passes this check regardless of which roles
    are listed -- it has complete administrative control over every
    module in the system, so any route protected by this decorator is
    implicitly reachable by a Super Admin without needing to list
    "Super Admin" at every call site.
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.role == "Super Admin":
                return view_func(*args, **kwargs)
            if current_user.role not in roles:
                flash("You do not have permission to access that page.", "danger")
                return redirect(url_for("dashboard.index"))
            return view_func(*args, **kwargs)

        return wrapped_view

    return decorator


def current_pm_profile():
    """
    Return the ProjectManager profile linked to the currently logged-in
    user, or None if the user isn't a Project Manager. Centralising this
    lookup avoids repeating current_user.project_manager_profile checks
    across every Project/Attendance route.
    """
    if not current_user.is_authenticated or current_user.role != "Project Manager":
        return None
    return current_user.project_manager_profile


def current_intern_profile():
    """
    Return the Intern profile linked to the currently logged-in user,
    or None if the user isn't an Intern. Mirrors current_pm_profile()
    for the Intern Portal (Module 3).
    """
    if not current_user.is_authenticated or current_user.role != "Intern":
        return None
    return current_user.intern_profile


# ---------------------------------------------------------------------
# Document uploads (Intern Portal: Project Submission + Final
# Internship Report attachments). Actual storage lives in
# services/file_storage.py (Vercel Blob in production, local disk in
# dev) -- these are thin re-exports so existing `from utils import ...`
# call sites across the portal routes don't need to change.
# ---------------------------------------------------------------------
from services.file_storage import (  # noqa: E402
    allowed_document,
    save_submission_file,
    delete_submission_file,
    resolve_file_url,
)


def is_valid_submission_link(link: str) -> bool:
    """
    Loosely check that *something* link-shaped was pasted in. Per
    product decision, submission links are no longer rejected for
    format (Station HR/PM reviewers, not the app, are the ones who
    judge whether a submitted link is acceptable) -- this only guards
    against completely empty/whitespace-only input and absurd lengths
    that would break the database column.
    """
    if not link:
        return False
    link = link.strip()
    return bool(link) and len(link) <= 2048


def safe_link(link: str | None) -> str:
    """
    Return a link that's always safe to drop into an <a href> for
    "Check Submission Link" style buttons/rows. Submission links are no
    longer validated for format on submit (any pasted link is
    accepted), but we still refuse to ever render a non-http(s) scheme
    (javascript:, data:, etc.) into an href -- that's an XSS guard, not
    a content-format restriction, so it stays even though the
    submission-time validation was removed. A link that doesn't parse
    as absolute http(s) falls back to '#' with no navigation.
    """
    if not link:
        return "#"
    link = link.strip()
    try:
        parsed = urlparse(link)
    except ValueError:
        return "#"
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return link
    return "#"


def display_role(role: str | None) -> str:
    """
    Frontend-only label for a user's role.
    """
    if role == "Station HR":
        return "Station HR"
    return role


def current_hr_city():
    """
    Return the city the currently logged-in Station HR user is scoped
    to, or None if the current user isn't a Station HR (Super Admin and
    every other role are never city-restricted).
    """
    if not current_user.is_authenticated or current_user.role != "Station HR":
        return None
    return current_user.city


def hr_city_scope():
    """
    Return (is_city_scoped, city) for the current user. Super Admin is
    never scoped (sees every city); a Station HR is scoped to their own
    `city`. A legacy Station HR account created before city scoping
    existed (city left NULL) is treated as unscoped -- i.e. it keeps
    seeing every city exactly as it did before, so existing deployments
    upgrading to city-based access control aren't suddenly locked out
    of data they could already see. Centralises the "what am I allowed
    to see" check so every Intern/Project/Attendance/Report/Analytics
    query can filter consistently instead of re-deriving this logic
    per route.
    """
    if current_user.is_authenticated and current_user.role == "Station HR" and current_user.city:
        return True, current_user.city
    return False, None


# ---------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------
def notify_user(
    user_id: int, message: str, icon: str = "bi-bell", notification_type: str = "General"
) -> None:
    """
    Create a notification for the given user. Import is deferred to
    avoid a circular import between utils.py and models/notification.py
    at module load time.
    """
    from extensions import db
    from models import Notification

    db.session.add(
        Notification(
            user_id=user_id, message=message, icon=icon, notification_type=notification_type
        )
    )


# ---------------------------------------------------------------------
# Audit Log (Module 5: Admin Features)
# ---------------------------------------------------------------------
def log_action(action: str, description: str, target_type: str | None = None,
                target_id: int | None = None) -> None:
    """
    Record an entry in the audit trail for the currently logged-in
    user. Called from route handlers right before/after a mutating
    db.session.commit() so every create/update/delete of a significant
    record is traceable. Import is deferred for the same circular-
    import reason as notify_user().
    """
    from extensions import db
    from models import AuditLog

    actor_id = current_user.id if current_user.is_authenticated else None
    db.session.add(
        AuditLog(
            user_id=actor_id,
            action=action,
            description=description,
            target_type=target_type,
            target_id=target_id,
        )
    )
