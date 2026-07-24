"""
utils.py
--------
Shared helper functions and decorators used across multiple route
modules. Centralising these avoids code duplication (DRY principle).
"""

import os
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
# City-based Management: the fixed set of PIA station cities that both
# Interns and Project Managers can be assigned to. Kept as a single
# source of truth here so forms, validation and reports never drift
# out of sync with one another.
# ----------------------------------------------------------------------
PIA_CITIES = [
    "Karachi",
    "Sukkur",
    "Faisalabad",
    "Multan",
    "Lahore",
    "Rawalpindi/Islamabad",
    "Peshawar",
    "Quetta",
]


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
    Usage: @roles_required("HR")

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
# Document uploads (Intern Portal: Final Internship Report, PDF only)
# ---------------------------------------------------------------------
def allowed_document(filename: str) -> bool:
    """Check whether the uploaded file has an allowed document extension
    (PDF only)."""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in current_app.config["ALLOWED_DOCUMENT_EXTENSIONS"]
    )


def save_submission_file(file_storage, subfolder: str) -> tuple[str, str, str] | None:
    """
    Save an uploaded document into SUBMISSIONS_UPLOAD_FOLDER/<subfolder>
    with a unique on-disk filename, preserving the original filename for
    display. Returns (stored_filename, original_filename, extension) or
    None if no file was provided.
    Raises ValueError if the file type isn't allowed.
    """
    if not file_storage or file_storage.filename == "":
        return None

    if not allowed_document(file_storage.filename):
        raise ValueError("Invalid file type. Only PDF files are allowed.")

    original_name = secure_filename(file_storage.filename)
    extension = original_name.rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{extension}"

    target_folder = os.path.join(current_app.config["SUBMISSIONS_UPLOAD_FOLDER"], subfolder)
    os.makedirs(target_folder, exist_ok=True)
    file_storage.save(os.path.join(target_folder, unique_name))

    return unique_name, original_name, extension


def delete_submission_file(filename: str, subfolder: str) -> None:
    """Delete a previously uploaded submission/report file, if present."""
    if not filename:
        return
    path = os.path.join(current_app.config["SUBMISSIONS_UPLOAD_FOLDER"], subfolder, filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass  # Non-fatal: leftover file, safe to ignore.


def is_valid_submission_link(link: str) -> bool:
    """
    Validate that a project submission link is a well-formed, absolute
    http(s) URL (Google Drive link or deployed website URL such as
    Vercel, Netlify, GitHub Pages, etc.).

    Guards against every input that previously slipped through and
    produced a link the "Check Submission Link" buttons couldn't safely
    open (missing scheme, javascript:/data: URLs, whitespace-only
    values, or absurdly long strings) - any of those, when rendered
    straight into an <a href>, either silently reload the current page
    (looks like the button "does nothing"/freezes) or fail some other
    unhelpful way in the browser.
    """
    if not link:
        return False
    link = link.strip()
    if not link or len(link) > 2048:
        return False
    try:
        parsed = urlparse(link)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def display_role(role: str | None) -> str:
    """
    Frontend-only label for a user's role. The stored/DB role value for
    the HR account is still 'HR' (used unchanged in permission
    checks, decorators, and queries) -- this filter only affects what
    gets rendered on screen, showing it simply as 'HR'.
    """
    if role == "HR":
        return "HR"
    return role


def safe_link(link: str | None) -> str:
    """
    Return a link that's always safe to drop into an <a href> for
    "Check Submission Link" style buttons. If the stored value isn't a
    valid absolute http(s) URL (e.g. legacy rows saved before stricter
    validation existed), fall back to '#' with no navigation instead of
    letting the browser try to resolve a bad value against the current
    page - which is what produced the "frozen"/unresponsive click.
    """
    if link and is_valid_submission_link(link):
        return link.strip()
    return "#"


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
