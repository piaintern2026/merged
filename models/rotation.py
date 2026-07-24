"""
models/rotation.py
-------------------
Enterprise feature: Intern Rotation Management.

Every time an intern is moved from one department/manager to another,
a permanent InternRotation record is created. This is the single
source of truth for:
  - the intern's rotation history / timeline
  - the permanent record of projects completed in each department
    under each manager
  - department-wise time-spent and rotation analytics
  - the Final Internship Report

The intern's *current* department is still tracked on Intern.department_id
(existing column, updated in place on every rotation so nothing else in
the app has to change). The intern's *current* manager is derived as the
`to_manager` of their most recent InternRotation row -- see
`Intern.current_manager` below and `routes/rotation.py`.
"""

from datetime import date, datetime, timezone
from utils import now_pkt, today_pkt

from extensions import db


class InternRotation(db.Model):
    """A single department-to-department rotation record for an intern."""

    __tablename__ = "intern_rotations"

    id = db.Column(db.Integer, primary_key=True)

    intern_id = db.Column(db.Integer, db.ForeignKey("interns.id"), nullable=False, index=True)

    from_department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=True)
    to_department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)

    from_manager_id = db.Column(db.Integer, db.ForeignKey("project_managers.id"), nullable=True)
    to_manager_id = db.Column(db.Integer, db.ForeignKey("project_managers.id"), nullable=False)

    # The project the intern works on during this rotation stint (optional).
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)

    start_date = db.Column(db.Date, nullable=False)
    # Nullable: the *current* / most recent rotation stays open (None)
    # until the intern is rotated again or their internship ends.
    end_date = db.Column(db.Date, nullable=True)

    reason = db.Column(db.String(255), nullable=False)
    remarks = db.Column(db.Text, nullable=True)

    rotated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=now_pkt)

    # Relationships
    intern = db.relationship("Intern", backref=db.backref(
        "rotations", lazy=True, order_by="InternRotation.start_date"
    ))
    from_department = db.relationship("Department", foreign_keys=[from_department_id])
    to_department = db.relationship("Department", foreign_keys=[to_department_id])
    from_manager = db.relationship("ProjectManager", foreign_keys=[from_manager_id])
    to_manager = db.relationship("ProjectManager", foreign_keys=[to_manager_id])
    project = db.relationship("Project", backref="rotations")
    rotated_by = db.relationship("User")

    # ------------------------------------------------------------------
    # Derived / auto-calculated fields
    # ------------------------------------------------------------------
    @property
    def effective_end_date(self):
        """The end date for duration purposes: the stored end_date if the
        rotation has been closed out, otherwise today (still ongoing)."""
        return self.end_date or today_pkt()

    @property
    def duration_days(self) -> int:
        """Auto-calculated duration of this rotation stint, in days
        (inclusive of both the start and end day)."""
        end = self.effective_end_date
        if end < self.start_date:
            return 0
        return (end - self.start_date).days + 1

    @property
    def duration_display(self) -> str:
        """Human-friendly duration string, e.g. '2 mo 5 d' or '18 day(s)'."""
        days = self.duration_days
        months, remaining_days = divmod(days, 30)
        if months and remaining_days:
            return f"{months} mo {remaining_days} d"
        if months:
            return f"{months} mo"
        return f"{days} day(s)"

    @property
    def is_current(self) -> bool:
        """True if this is the intern's currently active rotation stint."""
        return self.end_date is None

    def __repr__(self):
        return (
            f"<InternRotation Intern#{self.intern_id} "
            f"{self.from_department_id}->{self.to_department_id} {self.start_date}>"
        )
