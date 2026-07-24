"""
models/notification.py
-----------------------
Lightweight in-app notifications shown on a user's dashboard (used by
the Intern Dashboard's Notifications panel in Module 3) and, from
Module 5 onward, a full Notification Center available to every role.
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class Notification(db.Model):
    """A single notification addressed to one user."""

    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    message = db.Column(db.String(255), nullable=False)
    # Bootstrap icon class used to render the notification, e.g. 'bi-kanban'.
    icon = db.Column(db.String(40), nullable=False, default="bi-bell")

    # One of NOTIFICATION_TYPES below. Drives the filter dropdown and
    # badge color in the Module 5 Notification Center.
    notification_type = db.Column(db.String(30), nullable=False, default="General")

    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=now_pkt)

    user = db.relationship("User", backref="notifications")

    # The five categories required by Module 5, plus a catch-all.
    NOTIFICATION_TYPES = [
        "Project Assigned",
        "Attendance Reminder",
        "Project Deadline",
        "Evaluation Complete",
        "General",
    ]

    def __repr__(self):
        return f"<Notification User#{self.user_id} '{self.message[:30]}'>"
