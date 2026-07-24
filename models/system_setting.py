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
            "Pakistan International Airlines",
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

    def __repr__(self):
        return f"<SystemSetting {self.key}={self.value!r}>"
