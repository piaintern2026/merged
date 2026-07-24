"""
models/leave.py
----------------
Leave requests submitted by an Intern and reviewed by their assigned
Project Manager (the manager currently supervising the intern, per
Intern.current_manager / Rotation Management). HR has read-only
visibility into every leave record and report but cannot approve,
reject, or comment on requests -- approval authority lives entirely
with the assigned PM.

Each request moves from Pending to Approved, Rejected, or Cancelled.
An Approved request that is later Rejected or Cancelled by the PM
automatically reverses the attendance entry that was auto-created for
it (see services/leave_attendance.py).
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class Leave(db.Model):
    """Intern leave request."""

    __tablename__ = "leaves"

    id = db.Column(db.Integer, primary_key=True)
    intern_id = db.Column(db.Integer, db.ForeignKey("interns.id"), nullable=False)

    # One of: Sick, Casual, Emergency, Other
    leave_type = db.Column(db.String(30), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.Text, nullable=False)

    # One of: Pending, Approved, Rejected, Cancelled
    status = db.Column(db.String(20), nullable=False, default="Pending")

    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    # PM's decision remarks / running comment on this request. Settable
    # by the assigned PM on approve, reject, cancel, or via the
    # standalone "add comment" action.
    review_remarks = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=now_pkt)

    # Relationships
    intern = db.relationship("Intern", backref="leave_requests")
    reviewed_by = db.relationship("User", backref="leave_reviews")

    LEAVE_TYPES = ["Sick", "Casual", "Emergency", "Other"]
    STATUSES = ["Pending", "Approved", "Rejected", "Cancelled"]

    # ------------------------------------------------------------------
    # Authorization helpers
    # ------------------------------------------------------------------
    @property
    def assigned_pm(self):
        """The Project Manager currently supervising this leave's
        intern -- the only user (besides HR read-only) allowed to
        act on this request."""
        return self.intern.current_manager if self.intern else None

    def is_manageable_by(self, pm) -> bool:
        """True if the given ProjectManager profile is this request's
        assigned approver."""
        return pm is not None and self.assigned_pm is not None and self.assigned_pm.id == pm.id

    def __repr__(self):
        return f"<Leave {self.intern_id} {self.start_date}-{self.end_date} {self.status}>"
