"""
services/email_service.py
--------------------------
Reusable Email Notification Service for the Intern Onboarding Portal,
built on Flask-Mail.

Design principles (do not break these when extending):
  1. Emails are ALWAYS triggered *after* a successful db.session.commit()
     in the calling route - never before, so a failed transaction never
     produces a misleading email.
  2. A failure to send an email NEVER raises out to the caller and
     NEVER interrupts the request/response cycle. Every failure is
     caught and written to the app logger (`current_app.logger.error`)
     so HR/DevOps can see it in the logs without the intern-facing
     request failing.
  3. Emails are sent on a background thread (with the Flask app
     context pushed) so a slow/unavailable SMTP server never makes the
     user wait on a page load.
  4. All SMTP credentials/config come from environment variables via
     config.py - nothing is hard-coded here.
  5. Every email is a responsive, on-brand HTML template rendered
     with Jinja (templates/emails/*.html), extending
     templates/emails/base_email.html for a consistent look.

Usage from routes (always call AFTER db.session.commit()):

    from services.email_service import send_welcome_email
    send_welcome_email(user=user, intern=intern, raw_password=password)

No route should ever construct a flask_mail.Message directly - always
go through one of the public `send_*` functions below so behaviour
(logging, async dispatch, template rendering) stays consistent.
"""

from __future__ import annotations

import re
import smtplib
import threading
import unicodedata
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import date, datetime, timezone
from utils import today_pkt, now_pkt

from flask import current_app, render_template

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


# ---------------------------------------------------------------------
# Header sanitization
# ---------------------------------------------------------------------
# Whitespace-ish Unicode characters that regularly sneak into names /
# subjects when text is copy-pasted from Word, Outlook, or a PDF
# (non-breaking space \xa0, thin space, zero-width space, etc). These
# all get collapsed to a normal ASCII space rather than dropped, so
# words don't get glued together.
_WEIRD_SPACES_RE = re.compile(
    "[\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\u200b\ufeff]"
)


def _clean_header_text(value) -> str:
    """
    Make a value safe to drop into an email header (Subject, From
    display-name, To display-name, etc).

    1. Coerce to str.
    2. Normalize Unicode (NFKC) so look-alike characters collapse to
       their canonical form.
    3. Replace non-breaking spaces and other Unicode whitespace with a
       normal ASCII space (this is what was triggering
       `'ascii' codec can't encode character '\\xa0'` from smtplib,
       which encodes headers as plain ASCII unless they're wrapped in
       an email.header.Header).
    4. Collapse repeated whitespace and strip the ends.

    NOTE: this does not strip other non-ASCII characters (e.g. accented
    names) - those are legitimate and are instead safely encoded via
    `_encode_header`. Only whitespace-like characters are normalized,
    since those are what break plain ASCII headers unnoticed.
    """
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = _WEIRD_SPACES_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _encode_header(value) -> Header:
    """
    Wrap a cleaned header value in an `email.header.Header` so it is
    always UTF-8 encoded (RFC 2047) rather than relying on smtplib's
    default plain-ASCII encoding. Safe for both pure-ASCII and
    non-ASCII text.
    """
    text = _clean_header_text(value)
    try:
        text.encode("ascii")
        return Header(text, "ascii")
    except UnicodeEncodeError:
        return Header(text, "utf-8")


def _encode_address(name, email_addr) -> str:
    """Build a `"Name" <email>` address header value that is safe to
    encode, using RFC 2047 encoding for the display name when needed."""
    clean_name = _clean_header_text(name)
    clean_email = _clean_header_text(email_addr)
    if not clean_name:
        return clean_email
    return formataddr((str(_encode_header(clean_name)), clean_email))


# ---------------------------------------------------------------------
# SMTP settings resolution
# ---------------------------------------------------------------------
def _smtp_settings(app) -> dict:
    """
    Resolve the SMTP configuration to use for this send, preferring any
    value a Super Admin has saved on the Email Settings page
    (models.SystemSetting, keys 'mail_*') over the MAIL_* environment
    variables baked into app.config at startup. This lets SMTP
    credentials be changed live from the UI without restarting the app
    or touching the .env file.
    """
    with app.app_context():
        from models import SystemSetting

        overrides = {
            s.key: s.value
            for s in SystemSetting.query.filter(SystemSetting.key.like("mail_%")).all()
        }

    def _bool(v, default=False):
        if v is None:
            return default
        return str(v).strip().lower() in ("true", "1", "yes")

    sender_name = overrides.get("mail_default_sender_name") or app.config.get(
        "MAIL_DEFAULT_SENDER", ("Intern Onboarding Portal", "")
    )[0]
    sender_email = (
        overrides.get("mail_default_sender_email")
        or overrides.get("mail_username")
        or app.config.get("MAIL_DEFAULT_SENDER", ("", "no-reply@piac.com"))[1]
    )

    return {
        "server": overrides.get("mail_server") or app.config.get("MAIL_SERVER"),
        "port": int(overrides.get("mail_port") or app.config.get("MAIL_PORT", 587)),
        "use_tls": _bool(overrides.get("mail_use_tls"), app.config.get("MAIL_USE_TLS", True)),
        "use_ssl": _bool(overrides.get("mail_use_ssl"), app.config.get("MAIL_USE_SSL", False)),
        "username": overrides.get("mail_username") or app.config.get("MAIL_USERNAME"),
        "password": overrides.get("mail_password") or app.config.get("MAIL_PASSWORD"),
        "sender_name": sender_name,
        "sender_email": sender_email,
        "suppress_send": _bool(
            overrides.get("mail_suppress_send"), app.config.get("MAIL_SUPPRESS_SEND", True)
        ),
    }


# ---------------------------------------------------------------------
# Email logging
# ---------------------------------------------------------------------
def _log_attempt(recipient: str, subject: str, template: str | None, status: str, error: str | None = None) -> None:
    """Persist one EmailLog row. Never raises - a logging failure must
    not prevent (or crash) the actual send attempt."""
    try:
        from extensions import db
        from models import EmailLog

        db.session.add(
            EmailLog(
                recipient=recipient,
                subject=subject,
                template=template,
                status=status,
                error_message=error,
            )
        )
        db.session.commit()
    except Exception:  # noqa: BLE001
        try:
            from extensions import db

            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------
# Core send primitive
# ---------------------------------------------------------------------
def _deliver(app, subject: str, recipients: list[str], html_body: str, template: str | None = None) -> None:
    """
    Actually hand the message to the SMTP server (via smtplib, using
    live settings resolved by `_smtp_settings`). Runs inside a
    background thread with the app context pushed. All exceptions are
    caught and logged - this function must never raise into the
    caller thread, and a broken SMTP server must never crash the app.
    """
    with app.app_context():
        recipients = [r for r in recipients if r]
        if not recipients:
            app.logger.warning("email_service: no valid recipients for '%s' - skipped.", subject)
            return

        settings = _smtp_settings(app)

        if settings["suppress_send"]:
            app.logger.info(
                "email_service: SUPPRESSED (test mode) '%s' to %s", subject, ", ".join(recipients)
            )
            for r in recipients:
                _log_attempt(r, subject, template, "Suppressed")
            return

        # Clean + recipients: strip nbsp/odd-whitespace from addresses too
        # (a stray \xa0 pasted into an intern's email field is just as
        # fatal to smtplib's ASCII header encoding as one in a name).
        clean_recipients = [_clean_header_text(r) for r in recipients]

        msg = MIMEMultipart("alternative")
        msg["Subject"] = _encode_header(subject)
        msg["From"] = _encode_address(settings["sender_name"], settings["sender_email"])
        msg["To"] = ", ".join(clean_recipients)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            if settings["use_ssl"]:
                server = smtplib.SMTP_SSL(settings["server"], settings["port"], timeout=15)
            else:
                server = smtplib.SMTP(settings["server"], settings["port"], timeout=15)

            with server:
                if settings["use_tls"] and not settings["use_ssl"]:
                    server.starttls()
                if settings["username"] and settings["password"]:
                    server.login(settings["username"], settings["password"])
                    server.send_message(msg)
            app.logger.info("email_service: sent '%s' to %s", subject, ", ".join(recipients))
            for r in recipients:
                _log_attempt(r, subject, template, "Sent")
        except Exception as exc:  # noqa: BLE001 - deliberately broad: emails must never crash the app
            app.logger.exception("email_service: FAILED to send '%s' to %s", subject, recipients)
            for r in recipients:
                _log_attempt(r, subject, template, "Failed", error=str(exc))


def send_email(
    subject: str,
    recipients: list[str] | str,
    template: str,
    context: dict | None = None,
    async_send: bool = True,
) -> None:
    """
    Render `template` (a path under templates/emails/) with `context`
    and send it to `recipients`. Safe to call from any route: never
    raises, logs failures instead.

    Args:
        subject: Email subject line.
        recipients: A single email address or list of addresses.
        template: Template path, e.g. "emails/welcome_intern.html".
        context: Variables passed to the Jinja template.
        async_send: If True (default), dispatch on a background thread
            so the request isn't blocked by a slow SMTP server.
    """
    if isinstance(recipients, str):
        recipients = [recipients]

    context = dict(context or {})
    context.setdefault("current_year", now_pkt().year)

    app = current_app._get_current_object()

    try:
        html_body = render_template(template, **context)
    except Exception:  # noqa: BLE001
        app.logger.exception("email_service: FAILED to render template '%s'", template)
        return

    if async_send:
        thread = threading.Thread(
            target=_deliver, args=(app, subject, recipients, html_body, template), daemon=True
        )
        thread.start()
    else:
        _deliver(app, subject, recipients, html_body, template)


def send_test_email(recipient: str) -> tuple[bool, str]:
    """
    Send a synchronous test email from the Email Settings page so a
    Super Admin gets immediate success/failure feedback (rather than
    having to go check the logs after an async send). Returns
    (success, message).
    """
    app = current_app._get_current_object()
    settings = _smtp_settings(app)

    if settings["suppress_send"]:
        return False, "Sending is currently suppressed (Test Mode is ON). Turn it off to send real test emails."

    try:
        html_body = render_template(
            "emails/test_email.html",
            recipient=recipient,
            sent_at=now_pkt().strftime("%d %b %Y, %I:%M %p"),
            current_year=now_pkt().year,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"Failed to render test email template: {exc}"

    _deliver(app, "PIA Intern Portal - Test Email", [recipient], html_body, "test_email")

    from models import EmailLog

    last = (
        EmailLog.query.filter_by(recipient=recipient, subject="PIA Intern Portal - Test Email")
        .order_by(EmailLog.id.desc())
        .first()
    )
    if last and last.status == "Sent":
        return True, f"Test email sent successfully to {recipient}."
    if last and last.status == "Failed":
        return False, f"Failed to send test email: {last.error_message}"
    return False, "Could not confirm delivery status - check the Email Logs table below."


# ---------------------------------------------------------------------
# Recipient helpers
# ---------------------------------------------------------------------
def get_hr_recipients() -> list[str]:
    """
    Return the email address(es) that should receive HR notifications:
    every active user with role 'Station HR', plus the configured
    hr_contact_email system setting as a fallback/CC-style address.
    """
    from models import User, SystemSetting

    emails = {
        u.email for u in User.query.filter_by(role="Station HR", is_active_account=True).all()
    }

    setting = SystemSetting.query.filter_by(key="hr_contact_email").first()
    if setting and setting.value:
        emails.add(setting.value)

    return list(emails)


def get_hr_contact_email() -> str:
    """Return the single HR contact email shown in outgoing emails."""
    from models import SystemSetting

    setting = SystemSetting.query.filter_by(key="hr_contact_email").first()
    return (setting.value if setting and setting.value else current_app.config["DEFAULT_HR_EMAIL"])


def _base_url() -> str:
    """
    Resolve the public base URL to use for links inside emails.

    Preference order:
      1. APP_BASE_URL env var / config, if explicitly set to something
         other than the localhost dev default - this is the correct
         value for a deployed environment (e.g. Vercel).
      2. The Host header of the current request, if we're inside a
         request context (e.g. a Super Admin clicking "Create User" on
         the deployed site) - this ensures emails always link to the
         actual deployed domain rather than localhost.
      3. The localhost dev default, as a last resort (e.g. a CLI/cron
         job running outside a request with no APP_BASE_URL set).

    This guarantees emails NEVER link to localhost once the app is
    actually deployed, even if APP_BASE_URL was left unset.
    """
    configured = current_app.config.get("APP_BASE_URL", "http://localhost:5000").rstrip("/")
    is_default_localhost = configured in ("http://localhost:5000", "https://localhost:5000")

    if is_default_localhost:
        try:
            from flask import request as _request

            if _request:
                return _request.host_url.rstrip("/")
        except Exception:  # noqa: BLE001 - no active request context (CLI/cron)
            pass

    return configured


# ---------------------------------------------------------------------
# 1. Intern registration - welcome email with login credentials
# ---------------------------------------------------------------------
def send_welcome_email(user, intern, raw_password: str) -> None:
    """Send the welcome/credentials email to a newly registered intern."""
    from flask import url_for

    with current_app.app_context():
        login_url = f"{_base_url()}{url_for('auth.login')}"

    send_email(
        subject="Welcome to Intern Onboarding Portal - Your Intern Account Has Been Created",
        recipients=user.email,
        template="emails/welcome_intern.html",
        context={
            "full_name": intern.full_name,
            "username": user.username,
            "email": user.email,
            "password": raw_password,
            "department_name": intern.department.name if intern.department else "N/A",
            "start_date": intern.internship_start_date.strftime("%d %b %Y"),
            "end_date": intern.internship_end_date.strftime("%d %b %Y"),
            "login_url": login_url,
            "hr_contact_email": get_hr_contact_email(),
        },
    )


# ---------------------------------------------------------------------
# 2. Project assignment
# ---------------------------------------------------------------------
def send_project_assignment_email(intern, project, assigned_by: str | None = None) -> None:
    """Notify an intern by email that they've been assigned to a project."""
    from flask import url_for

    if not intern or not intern.user:
        return

    with current_app.app_context():
        project_url = f"{_base_url()}{url_for('project.list_projects')}"

    send_email(
        subject=f"New Project Assignment: {project.title}",
        recipients=intern.user.email,
        template="emails/project_assignment.html",
        context={
            "recipient_name": intern.full_name,
            "assigned_by": assigned_by,
            "project_title": project.title,
            "project_description": project.description,
            "department_name": project.department.name if project.department else "N/A",
            "start_date": project.start_date.strftime("%d %b %Y") if project.start_date else "N/A",
            "deadline": project.deadline.strftime("%d %b %Y") if project.deadline else "N/A",
            "project_url": project_url,
        },
    )


# ---------------------------------------------------------------------
# 3. Department rotation
# ---------------------------------------------------------------------
def send_rotation_email(rotation) -> None:
    """Notify an intern by email that they have been rotated."""
    from flask import url_for

    intern = rotation.intern
    if not intern or not intern.user:
        return

    with current_app.app_context():
        timeline_url = f"{_base_url()}{url_for('rotation.timeline', intern_id=intern.id)}"

    send_email(
        subject=f"You Have Been Rotated to {rotation.to_department.name}",
        recipients=intern.user.email,
        template="emails/rotation.html",
        context={
            "intern_name": intern.full_name,
            "from_department": rotation.from_department.name if rotation.from_department else None,
            "to_department": rotation.to_department.name,
            "to_manager": rotation.to_manager.full_name,
            "start_date": rotation.start_date.strftime("%d %b %Y"),
            "reason": rotation.reason,
            "timeline_url": timeline_url,
        },
    )


# ---------------------------------------------------------------------
# 4. Password reset
# ---------------------------------------------------------------------
def _reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="pia-password-reset")


def generate_password_reset_token(user) -> str:
    """Generate a signed, time-limited password reset token for a user."""
    return _reset_serializer().dumps({"user_id": user.id, "email": user.email})


def verify_password_reset_token(token: str, max_age: int | None = None):
    """
    Verify a password reset token. Returns the payload dict on success,
    or None if the token is invalid/expired.
    """
    max_age = max_age or current_app.config.get("PASSWORD_RESET_TOKEN_MAX_AGE", 3600)
    try:
        return _reset_serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def send_password_reset_email(user) -> None:
    """Send a password reset link email to the given user."""
    from flask import url_for

    token = generate_password_reset_token(user)
    max_age = current_app.config.get("PASSWORD_RESET_TOKEN_MAX_AGE", 3600)

    with current_app.app_context():
        reset_url = f"{_base_url()}{url_for('auth.reset_password', token=token)}"

    send_email(
        subject="Reset Your Intern Onboarding Portal Password",
        recipients=user.email,
        template="emails/password_reset.html",
        context={
            "display_name": user.display_name(),
            "email": user.email,
            "reset_url": reset_url,
            "expires_in_minutes": max_age // 60,
        },
    )


# ---------------------------------------------------------------------
# 5. Account activation / deactivation
# ---------------------------------------------------------------------
def send_account_status_email(user, is_active: bool, reason: str | None = None) -> None:
    """Notify a user by email that their account was activated/deactivated."""
    from flask import url_for

    with current_app.app_context():
        login_url = f"{_base_url()}{url_for('auth.login')}"

    send_email(
        subject=f"Your Intern Onboarding Portal Account Has Been {'Activated' if is_active else 'Deactivated'}",
        recipients=user.email,
        template="emails/account_status.html",
        context={
            "display_name": user.display_name(),
            "is_active": is_active,
            "reason": reason,
            "login_url": login_url,
            "hr_contact_email": get_hr_contact_email(),
        },
    )


# ---------------------------------------------------------------------
# 6. Internship completion
# ---------------------------------------------------------------------
def send_internship_completion_email(intern, final_report) -> None:
    """Notify an intern by email that their internship/final report is complete."""
    from flask import url_for

    if not intern or not intern.user:
        return

    with current_app.app_context():
        report_url = f"{_base_url()}{url_for('intern_portal.final_report')}"

    send_email(
        subject="Congratulations on Completing Your Internship!",
        recipients=intern.user.email,
        template="emails/internship_completion.html",
        context={
            "intern_name": intern.full_name,
            "department_name": intern.department.name if intern.department else "N/A",
            "start_date": intern.internship_start_date.strftime("%d %b %Y"),
            "end_date": intern.internship_end_date.strftime("%d %b %Y"),
            "report_title": final_report.title,
            "report_url": report_url,
        },
    )


# ---------------------------------------------------------------------
# 7. Deadline reminders (batch - intended for a scheduled/cron job)
# ---------------------------------------------------------------------
def send_deadline_reminder_emails(reminder_window_days: int = 3) -> int:
    """
    Find all active (non-completed) projects whose deadline is either
    overdue or within `reminder_window_days`, and email the assigned
    intern + their Project Manager a reminder. Intended to be run
    periodically via `flask send-deadline-reminders` (see app.py) from
    an external scheduler/cron - it does not run automatically inside
    a web request.

    Returns the number of reminder emails sent.
    """
    from flask import url_for
    from models import Project

    open_statuses = ("Pending", "Working", "Submitted", "Rejected")
    projects = Project.query.filter(Project.status.in_(open_statuses)).all()

    today = today_pkt()
    sent = 0

    with current_app.app_context():
        project_url = f"{_base_url()}{url_for('project.list_projects')}"

    for project in projects:
        days_remaining = (project.deadline - today).days
        is_overdue = days_remaining < 0
        if not is_overdue and days_remaining > reminder_window_days:
            continue  # not due soon enough yet

        recipients_profiles = []
        for intern in project.interns:
            if intern.user:
                recipients_profiles.append((intern.user.email, intern.full_name))
        if project.manager and project.manager.user:
            recipients_profiles.append((project.manager.user.email, project.manager.full_name))

        for email, name in recipients_profiles:
            send_email(
                subject=(
                    f"[Overdue] {project.title}" if is_overdue else f"[Reminder] {project.title} due soon"
                ),
                recipients=email,
                template="emails/deadline_reminder.html",
                context={
                    "recipient_name": name,
                    "project_title": project.title,
                    "status": project.status,
                    "deadline": project.deadline.strftime("%d %b %Y"),
                    "is_overdue": is_overdue,
                    "days_remaining": max(days_remaining, 0),
                    "project_url": project_url,
                },
                async_send=False,  # CLI command: send synchronously and report a real count
            )
            sent += 1

    return sent


# ---------------------------------------------------------------------
# 8. Generic HR / Project Manager notifications for important events
# ---------------------------------------------------------------------
def send_hr_pm_notification_email(
    recipients: list[str] | str,
    recipient_name: str,
    event_title: str,
    event_message: str,
    details: list[tuple[str, str]] | None = None,
    action_url: str | None = None,
    action_label: str | None = None,
) -> None:
    """
    Generic notification email for HR / Project Manager audiences,
    reused for any "important event" (new intern registered, rotation
    performed, project completed, etc.) rather than duplicating a new
    template per event type.
    """
    send_email(
        subject=event_title,
        recipients=recipients,
        template="emails/hr_pm_notification.html",
        context={
            "recipient_name": recipient_name,
            "event_title": event_title,
            "event_message": event_message,
            "details": details or [],
            "action_url": action_url,
            "action_label": action_label,
        },
    )


# ---------------------------------------------------------------------
# 9. Staff account creation (Station HR / Project Manager / Super Admin)
#    Interns get send_welcome_email() above; this covers every other
#    role created from the Super Admin "Users" / "Project Managers"
#    screens, with the same username + temporary password pattern.
# ---------------------------------------------------------------------
def send_staff_account_email(user, raw_password: str, created_by: str | None = None) -> None:
    """Send login credentials to a newly created HR / PM / Admin account."""
    from flask import url_for

    with current_app.app_context():
        login_url = f"{_base_url()}{url_for('auth.login')}"

    send_email(
        subject=f"Your {user.role} Account Has Been Created - Intern Onboarding Portal",
        recipients=user.email,
        template="emails/staff_account.html",
        context={
            "full_name": user.display_name(),
            "role": user.role,
            "username": user.username,
            "email": user.email,
            "password": raw_password,
            "created_by": created_by,
            "login_url": login_url,
            "hr_contact_email": get_hr_contact_email(),
        },
    )


# ---------------------------------------------------------------------
# 10. Leave request submitted / approved / rejected / cancelled
# ---------------------------------------------------------------------
def send_leave_status_email(leave, event: str, reviewer_name: str | None = None) -> None:
    """
    Notify by email about a leave request lifecycle event.
    `event` is one of: "submitted" (to the reviewing PM/HR),
    "approved", "rejected", "cancelled" (to the intern).
    """
    from flask import url_for

    intern = leave.intern
    if not intern:
        return

    with current_app.app_context():
        leave_url = f"{_base_url()}{url_for('leave.my_leaves')}"

    if event == "submitted":
        pm = intern.current_manager
        recipients = [pm.user.email] if pm and pm.user else get_hr_recipients()
        if not recipients:
            return
        subject = f"New Leave Request from {intern.full_name} Awaiting Review"
        recipient_name = pm.full_name if pm else "HR Team"
    else:
        if not intern.user:
            return
        recipients = [intern.user.email]
        recipient_name = intern.full_name
        subject = {
            "approved": f"Your {leave.leave_type} Leave Request Has Been Approved",
            "rejected": f"Your {leave.leave_type} Leave Request Has Been Rejected",
            "cancelled": f"Your Approved {leave.leave_type} Leave Has Been Cancelled",
        }.get(event, "Update on Your Leave Request")

    send_email(
        subject=subject,
        recipients=recipients,
        template="emails/leave_status.html",
        context={
            "recipient_name": recipient_name,
            "event": event,
            "intern_name": intern.full_name,
            "leave_type": leave.leave_type,
            "start_date": leave.start_date.strftime("%d %b %Y"),
            "end_date": leave.end_date.strftime("%d %b %Y"),
            "reason": leave.reason,
            "review_remarks": getattr(leave, "review_remarks", None),
            "reviewer_name": reviewer_name,
            "leave_url": leave_url,
        },
    )


# ---------------------------------------------------------------------
# 11. Attendance alerts (Late / Absent)
# ---------------------------------------------------------------------
def send_attendance_alert_email(intern, attendance_status: str, attendance_date, remarks: str | None = None) -> None:
    """Notify an intern (and CC their Project Manager) about a Late or
    Absent attendance mark."""
    from flask import url_for

    if not intern or not intern.user or attendance_status not in ("Late", "Absent"):
        return

    with current_app.app_context():
        attendance_url = f"{_base_url()}{url_for('attendance.list_attendance')}"

    recipients = [intern.user.email]
    pm = intern.current_manager
    if pm and pm.user:
        recipients.append(pm.user.email)

    send_email(
        subject=f"Attendance Alert: Marked {attendance_status} on {attendance_date.strftime('%d %b %Y')}",
        recipients=recipients,
        template="emails/attendance_alert.html",
        context={
            "intern_name": intern.full_name,
            "status": attendance_status,
            "attendance_date": attendance_date.strftime("%d %b %Y"),
            "remarks": remarks,
            "attendance_url": attendance_url,
        },
    )


# ---------------------------------------------------------------------
# 12. Report submission (project submissions & final report)
# ---------------------------------------------------------------------
def send_report_submission_email(recipient_email: str, recipient_name: str, intern_name: str, report_title: str, report_type: str, action_url: str | None = None) -> None:
    """Notify a Project Manager / HR that an intern submitted a report
    or deliverable and it's awaiting review."""
    with current_app.app_context():
        default_url = f"{_base_url()}"

    send_email(
        subject=f"New {report_type} Submitted by {intern_name}",
        recipients=recipient_email,
        template="emails/report_submission.html",
        context={
            "recipient_name": recipient_name,
            "intern_name": intern_name,
            "report_title": report_title,
            "report_type": report_type,
            "action_url": action_url or default_url,
        },
    )


# ---------------------------------------------------------------------
# 13. Evaluation completed
# ---------------------------------------------------------------------
def send_evaluation_email(intern, evaluation, evaluated_by_name: str | None = None) -> None:
    """Notify an intern by email that a new evaluation has been recorded."""
    from flask import url_for

    if not intern or not intern.user:
        return

    with current_app.app_context():
        eval_url = f"{_base_url()}{url_for('dashboard.index')}"

    avg_score = round(
        (
            evaluation.technical_skills
            + evaluation.communication
            + evaluation.discipline
            + evaluation.learning
            + evaluation.teamwork
            + evaluation.attendance_score
        )
        / 6,
        1,
    )

    send_email(
        subject=f"New {evaluation.evaluation_type} Evaluation Recorded",
        recipients=intern.user.email,
        template="emails/evaluation_complete.html",
        context={
            "intern_name": intern.full_name,
            "evaluation_type": evaluation.evaluation_type,
            "evaluated_by": evaluated_by_name,
            "average_score": avg_score,
            "remarks": evaluation.remarks,
            "eval_url": eval_url,
        },
    )


# ---------------------------------------------------------------------
# 14. Announcements (broadcast)
# ---------------------------------------------------------------------
def send_announcement_email(recipients: list[str], title: str, body: str, posted_by: str | None = None) -> None:
    """Broadcast an announcement email to a list of recipients."""
    with current_app.app_context():
        dashboard_url = f"{_base_url()}"

    send_email(
        subject=f"Announcement: {title}",
        recipients=recipients,
        template="emails/announcement.html",
        context={
            "title": title,
            "body": body,
            "posted_by": posted_by,
            "dashboard_url": dashboard_url,
        },
    )
