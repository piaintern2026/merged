"""
models/leave.py
----------------
Leave requests submitted by an Intern and reviewed by their assigned
Project Manager (the manager currently supervising the intern, per
Intern.current_manager / Rotation Management). HR has read-only
visibility into every leave record and report and cannot approve,
reject, or comment on requests while a PM is assigned -- approval
authority lives with the assigned PM. If an intern currently has no
assigned PM, HR gains fallback approval authority for that intern's
requests until a PM is assigned.

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

    # An intern may take at most this many leaves during their entire
    # internship (a total lifetime cap, not per month/week). Requests
    # that are Rejected or Cancelled free up the quota again since they
    # were never actually "used".
    MAX_LEAVES = 3

    # Statuses that count toward the lifetime cap -- a Pending request
    # already reserves a slot (so an intern can't submit 3 requests at
    # once and have all 3 approved), and an Approved one has been used.
    COUNTS_TOWARD_LIMIT_STATUSES = ["Pending", "Approved"]

    @classmethod
    def used_count_for(cls, intern_id) -> int:
        """How many of the intern's lifetime MAX_LEAVES leaves are currently
        used/reserved (Pending + Approved requests)."""
        return cls.query.filter(
            cls.intern_id == intern_id,
            cls.status.in_(cls.COUNTS_TOWARD_LIMIT_STATUSES),
        ).count()

    @classmethod
    def remaining_for(cls, intern_id) -> int:
        return max(cls.MAX_LEAVES - cls.used_count_for(intern_id), 0)

    # ------------------------------------------------------------------
    # Authorization helpers
    # ------------------------------------------------------------------
    @property
    def assigned_pm(self):
        """The Project Manager currently supervising this leave's
        intern -- via Rotation Management if the intern has ever been
        rotated. Used only for display (e.g. Leave Records' "Assigned
        PM" column); authorization itself is handled by
        is_manageable_by()/is_manageable_by_hr() below, which also
        cover interns who were never explicitly rotated."""
        return self.intern.current_manager if self.intern else None

    def _department_pms(self):
        """Active Project Managers in this leave's intern's department
        -- the fallback pool of approvers for an intern who has never
        been through Rotation Management, so their leave requests
        aren't stranded with no PM able to see them."""
        if not self.intern or not self.intern.department:
            return []
        return [pm for pm in self.intern.department.project_managers if pm.is_active_flag]

    def is_manageable_by(self, pm) -> bool:
        """True if the given ProjectManager profile may review this
        request: either they are the intern's rotation-assigned
        manager, or (when the intern has never been rotated) they are
        an active PM in the intern's own department."""
        if pm is None:
            return False
        if self.assigned_pm is not None:
            return self.assigned_pm.id == pm.id
        return any(dept_pm.id == pm.id for dept_pm in self._department_pms())

    def is_manageable_by_hr(self) -> bool:
        """Fallback approval authority: HR may approve/reject a leave
        request only when no Project Manager at all -- rotation-based
        or departmental -- can act on it. As soon as any such PM
        exists, approval belongs to them and this returns False."""
        if self.assigned_pm is not None:
            return False
        return not self._department_pms()

    def __repr__(self):
        return f"<Leave {self.intern_id} {self.start_date}-{self.end_date} {self.status}>"
