"""
models/email_log.py
--------------------
Email Notification System: a persistent audit trail of every email the
application has attempted to send. Written by services/email_service.py
right before/after each SMTP attempt so a Super Admin can see, from the
Email Settings page, whether notification emails are actually going out
-- without needing shell/log access.

A failed row here NEVER means the triggering request failed: emails are
always sent on a background thread and a delivery failure is caught and
logged here (and via the app logger), never raised back to the user.
"""

from utils import now_pkt

from extensions import db


class EmailLog(db.Model):
    """A single outbound email attempt."""

    __tablename__ = "email_logs"

    id = db.Column(db.Integer, primary_key=True)

    recipient = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    # Dotted "category" of the email, e.g. "welcome_intern",
    # "leave_approved", "attendance_alert" -- lets the log be filtered
    # by event type on the Email Settings page.
    template = db.Column(db.String(120), nullable=True)

    # 'Sent', 'Failed', or 'Suppressed' (MAIL_SUPPRESS_SEND was on, so
    # the email was rendered but intentionally not handed to SMTP).
    status = db.Column(db.String(20), nullable=False, default="Sent")
    error_message = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=now_pkt)

    STATUSES = ["Sent", "Failed", "Suppressed"]

    def __repr__(self):
        return f"<EmailLog {self.status} '{self.subject}' -> {self.recipient}>"
