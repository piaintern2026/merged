"""
models/audit_log.py
--------------------
Module 5: Admin Features - Audit Log. Records who did what and when
across the system (create/update/delete of significant records, plus
security-relevant events like login). Written via utils.log_action()
from within route handlers; read-only from the UI (HR only).
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class AuditLog(db.Model):
    """A single audit trail entry."""

    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)

    # Nullable so system-initiated events (e.g. seeding) can still be logged.
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Short verb-based action code, e.g. 'CREATE', 'UPDATE', 'DELETE', 'LOGIN'.
    action = db.Column(db.String(30), nullable=False)

    # Human-readable description, e.g. "Deleted department 'Finance'".
    description = db.Column(db.String(500), nullable=False)

    # Optional pointer to the affected record, for future drill-down.
    target_type = db.Column(db.String(50), nullable=True)  # e.g. 'Department'
    target_id = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, default=now_pkt)

    user = db.relationship("User", backref="audit_entries")

    def __repr__(self):
        return f"<AuditLog {self.action} by User#{self.user_id}>"
