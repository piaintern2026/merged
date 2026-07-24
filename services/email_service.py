"""
services/email_service.py
--------------------------
Reusable Email Notification Service for the PIA Intern Management
System, built on Flask-Mail.

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
  5. Every email is a responsive, PIA-branded HTML template rendered
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

import threading
from datetime import date, datetime, timezone
from utils import today_pkt, now_pkt

from flask import current_app, render_template
from flask_mail import Message
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from extensions import mail


# ---------------------------------------------------------------------
# Core send primitive
# ---------------------------------------------------------------------
def _deliver(app, subject: str, recipients: list[str], html_body: str) -> None:
    """
    Actually hand the message to Flask-Mail. Runs inside a background
    thread with the app context pushed. All exceptions are caught and
    logged - this function must never raise into the caller thread.
    """
    with app.app_context():
        try:
            recipients = [r for r in recipients if r]
            if not recipients:
                app.logger.warning("email_service: no valid recipients for '%s' - skipped.", subject)
                return
            msg = Message(subject=subject, recipients=recipients, html=html_body)
            mail.send(msg)
            app.logger.info("email_service: sent '%s' to %s", subject, ", ".join(recipients))
        except Exception:  # noqa: BLE001 - deliberately broad: emails must never crash the app
            app.logger.exception("email_service: FAILED to send '%s' to %s", subject, recipients)


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
            target=_deliver, args=(app, subject, recipients, html_body), daemon=True
        )
        thread.start()
    else:
        _deliver(app, subject, recipients, html_body)


# ---------------------------------------------------------------------
# Recipient helpers
# ---------------------------------------------------------------------
def get_hr_recipients() -> list[str]:
    """
    Return the email address(es) that should receive HR notifications:
    every active user with role 'HR', plus the configured
    hr_contact_email system setting as a fallback/CC-style address.
    """
    from models import User, SystemSetting

    emails = {
        u.email for u in User.query.filter_by(role="HR", is_active_account=True).all()
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
    return current_app.config.get("APP_BASE_URL", "http://localhost:5000").rstrip("/")


# ---------------------------------------------------------------------
# 1. Intern registration - welcome email with login credentials
# ---------------------------------------------------------------------
def send_welcome_email(user, intern, raw_password: str) -> None:
    """Send the welcome/credentials email to a newly registered intern."""
    from flask import url_for

    with current_app.app_context():
        login_url = f"{_base_url()}{url_for('auth.login')}"

    send_email(
        subject="Welcome to PIA - Your Intern Account Has Been Created",
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
            "priority": project.priority,
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
        subject="Reset Your PIA Intern Management System Password",
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
        subject=f"Your PIA Account Has Been {'Activated' if is_active else 'Deactivated'}",
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
        subject="Congratulations on Completing Your PIA Internship!",
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
        if project.intern and project.intern.user:
            recipients_profiles.append((project.intern.user.email, project.intern.full_name))
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
