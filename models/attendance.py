"""
models/attendance.py
---------------------
Records daily attendance for an Intern, marked by their Project
Manager. One row per intern per date (enforced by a unique
constraint) so attendance cannot be duplicated for the same day.

Clock-in/out times only make sense for a physically present intern:
Absent and Leave days have no clock-in, so `time` (and `time_out`) are
nullable and simply left blank for those statuses.

Rows with status "Leave" may be created/updated automatically by the
Leave Management module when a Project Manager approves a leave
request (see services/leave_attendance.py). `source_leave_id` marks a
row as PM-approval-managed; `pre_leave_*` snapshots the row's prior
state (if any existed) so the original attendance can be restored if
the approval is later reversed.
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class Attendance(db.Model):
    """Attendance record table."""

    __tablename__ = "attendance"
    __table_args__ = (
        db.UniqueConstraint("intern_id", "date", name="uq_attendance_intern_date"),
    )

    id = db.Column(db.Integer, primary_key=True)

    intern_id = db.Column(db.Integer, db.ForeignKey("interns.id"), nullable=False)
    marked_by_id = db.Column(db.Integer, db.ForeignKey("project_managers.id"), nullable=False)

    date = db.Column(db.Date, nullable=False)

    # Nullable: only Present/Late require a clock-in time. Absent and
    # Leave days simply have no time recorded.
    time = db.Column(db.Time, nullable=True)

    # Clock-out time for the day, set separately from the clock-in
    # (`time`) value via the Clock Out action. Nullable because it is
    # only populated once the Project Manager clocks the intern out
    # (and never applies to Absent/Leave).
    time_out = db.Column(db.Time, nullable=True)

    # One of: Present, Absent, Leave, Late
    status = db.Column(db.String(20), nullable=False, default="Present")
    remarks = db.Column(db.String(255), nullable=True)

    # Set when this row was created/overwritten by an Approved Leave
    # request rather than marked directly by a PM. Cleared again if the
    # approval is reversed (rejected/cancelled) and the original row is
    # restored rather than deleted.
    source_leave_id = db.Column(db.Integer, db.ForeignKey("leaves.id"), nullable=True)

    # Snapshot of the row's state immediately before an approved Leave
    # overwrote it, so it can be restored if the leave is later
    # rejected/cancelled. NULL when the row didn't exist before (in
    # which case reversal deletes the row instead of restoring it).
    pre_leave_status = db.Column(db.String(20), nullable=True)
    pre_leave_time = db.Column(db.Time, nullable=True)
    pre_leave_time_out = db.Column(db.Time, nullable=True)
    pre_leave_remarks = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=now_pkt)

    # Relationships
    intern = db.relationship("Intern", backref="attendance_records")
    marked_by = db.relationship("ProjectManager", backref="attendance_marked")
    source_leave = db.relationship("Leave", backref="attendance_entries")

    STATUSES = ["Present", "Absent", "Leave", "Late"]

    # Statuses that require a clock-in time; Absent/Leave never do.
    TIME_REQUIRED_STATUSES = ["Present", "Late"]

    @property
    def is_leave_managed(self) -> bool:
        """True if this row is currently owned by an Approved leave
        request and should not be hand-edited outside the Leave
        Management workflow."""
        return self.source_leave_id is not None

    def __repr__(self):
        return f"<Attendance {self.intern_id} {self.date} {self.status}>"
