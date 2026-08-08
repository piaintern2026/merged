"""
models/system_setting.py
-------------------------
Module 5: Admin Features - Settings. A simple key-value store for
system-wide configuration editable by HR, so common values don't need
a code change + redeploy to update.
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class SystemSetting(db.Model):
    """A single system setting, addressed by a unique string key."""

    __tablename__ = "system_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=True)
    label = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255), nullable=True)

    updated_at = db.Column(
        db.DateTime,
        default=now_pkt,
        onupdate=now_pkt,
    )

    # Default settings seeded on first run (see app.py:seed_default_settings).
    # (key, label, default_value, description)
    DEFAULTS = [
        (
            "organization_name",
            "Organization Name",
            "Intern Onboarding Portal",
            "Displayed across the application header and generated reports.",
        ),
        (
            "hr_contact_email",
            "HR Contact Email",
            "hr@piac.com",
            "Shown to interns/PMs as the point of contact for support.",
        ),
        (
            "attendance_reminder_enabled",
            "Enable Attendance Reminders",
            "true",
            "Whether HR can send bulk attendance reminder notifications.",
        ),
    ]

    # ------------------------------------------------------------------
    # Email Notification System: SMTP settings, editable from the Super
    # Admin "Email Settings" page (routes/admin.py: email_settings).
    # Seeded from the MAIL_* environment variables on first run so the
    # UI reflects config.py by default; changing a value here overrides
    # the environment variable at send time (see services/email_service
    # ._smtp_settings()) without needing a redeploy.
    # ------------------------------------------------------------------
    EMAIL_DEFAULTS = [
        ("mail_server", "SMTP Server", "smtp.gmail.com", "SMTP host used to send outbound emails."),
        ("mail_port", "SMTP Port", "587", "SMTP port (587 for TLS, 465 for SSL)."),
        ("mail_use_tls", "Use TLS", "true", "Whether to use STARTTLS when connecting."),
        ("mail_use_ssl", "Use SSL", "false", "Whether to use SSL/TLS from the start of the connection."),
        ("mail_username", "SMTP Username", "", "Username/email used to authenticate with the SMTP server."),
        ("mail_password", "SMTP Password", "", "Password or app password used to authenticate with the SMTP server."),
        ("mail_default_sender_name", "Sender Name", "Intern Onboarding Portal", "Display name shown as the email sender."),
        ("mail_default_sender_email", "Sender Email", "", "Email address emails are sent from."),
        ("mail_suppress_send", "Suppress Sending (Test Mode)", "true", "If enabled, emails are rendered and logged but NOT actually sent."),
    ]

    def __repr__(self):
        return f"<SystemSetting {self.key}={self.value!r}>"
