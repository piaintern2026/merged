"""
models/intern.py
-----------------
Role-specific profile data for users whose role is 'Intern'.
Linked one-to-one with the User table via user_id.
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class Intern(db.Model):
    """Intern profile."""

    __tablename__ = "interns"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)

    full_name = db.Column(db.String(120), nullable=False)
    cnic = db.Column(db.String(20), unique=True, nullable=False)
    university = db.Column(db.String(150), nullable=False)

    # Renamed from the original `degree` field to match the Excel
    # import template's "Qualification" column exactly. `degree` is
    # kept below as a read/write property alias so any code that still
    # refers to `intern.degree` (reports, emails, etc.) keeps working
    # unchanged.
    qualification = db.Column(db.String(120), nullable=False)

    # New field from the Excel template -- the intern's field of study
    # (e.g. "Computer Science"), distinct from `qualification` (e.g.
    # "BS") and from `university`.
    major = db.Column(db.String(120), nullable=True)

    # Semester is no longer part of the Excel import template or the
    # registration form, but the column (and any historical data in
    # it) is preserved -- nullable so new rows never have to supply it.
    semester = db.Column(db.String(20), nullable=True)

    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=True)
    sub_department_id = db.Column(
        db.Integer, db.ForeignKey("sub_departments.id"), nullable=True
    )
    sub_department = db.relationship("SubDepartment", foreign_keys=[sub_department_id])

    # Renamed from the original `city` field to match the Excel
    # import template's "Station" column. `city` is kept below as a
    # read/write property alias for backward compatibility.
    station = db.Column(db.String(80), nullable=False)

    phone = db.Column(db.String(20), nullable=False)

    # New field from the Excel template -- the specific placement /
    # posting (e.g. "PIA Head Office - HR Wing"). A matching Department
    # row is looked up-or-created from this text so existing
    # department-based features (Project assignment, Rotation
    # Management, attendance/leave filters) keep working unchanged --
    # see routes/intern.py:_get_or_create_department().
    placement = db.Column(db.String(150), nullable=True)

    # New fields from the Excel template.
    DOCUMENTS_STATUSES = ["Complete", "Pending", "In Progress"]
    CERTIFICATE_STATUSES = ["Pending", "Generated", "Issued"]
    documents_status = db.Column(db.String(20), nullable=False, default="Pending")
    certificate_status = db.Column(db.String(20), nullable=False, default="Pending")

    # Editable via the Intern Portal profile page (Module 3). Nullable
    # because it isn't collected at registration time (Module 1).
    address = db.Column(db.String(255), nullable=True)

    internship_start_date = db.Column(db.Date, nullable=False)
    internship_end_date = db.Column(db.Date, nullable=False)

    # Lifecycle status of the internship itself (distinct from
    # attendance/leave status). One of INTERNSHIP_STATUSES below.
    # 'Active' interns are still within their internship window;
    # 'Completed' means the internship ran its full course; 'Ended'
    # means HR terminated the internship early via the End Internship
    # action. Defaults to 'Active' so existing rows stay valid.
    internship_status = db.Column(db.String(20), nullable=False, default="Active")

    # Free-text reason captured when HR ends an internship early via
    # the End Internship action. Nullable -- only set for early endings.
    end_reason = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=now_pkt)

    # ------------------------------------------------------------------
    # Rotation Management helpers (Intern Rotation Management module)
    # ------------------------------------------------------------------
    @property
    def current_rotation(self):
        """The intern's most recent InternRotation row (if any), i.e.
        their current rotation stint. Rotations are attached via the
        `rotations` backref defined on InternRotation."""
        if not self.rotations:
            return None
        return max(self.rotations, key=lambda r: (r.start_date, r.id))

    @property
    def current_manager(self):
        """The Project Manager currently supervising this intern, derived
        from the most recent rotation record. None if the intern has
        never been rotated yet (no manager assigned via Rotation Management)."""
        rotation = self.current_rotation
        return rotation.to_manager if rotation else None

    # ------------------------------------------------------------------
    # Backward-compatible aliases. `degree`/`city` were renamed to
    # `qualification`/`station` to match the Excel import template, but
    # existing reports/emails/services that still say `intern.degree`
    # or `intern.city` keep working unchanged via these properties.
    # ------------------------------------------------------------------
    @property
    def degree(self):
        return self.qualification

    @degree.setter
    def degree(self, value):
        self.qualification = value

    @property
    def city(self):
        return self.station

    @city.setter
    def city(self, value):
        self.station = value

    INTERNSHIP_STATUSES = ["Active", "Completed", "Ended"]

    @property
    def effective_status(self) -> str:
        """The internship status to actually display: an early-ended
        internship stays 'Ended'; otherwise an internship whose end
        date has passed is 'Completed' even if nobody has flipped the
        stored flag yet; everything else is 'Active'."""
        from utils import today_pkt

        if self.internship_status == "Ended":
            return "Ended"
        if self.internship_end_date and self.internship_end_date < today_pkt():
            return "Completed"
        return "Active"

    @property
    def is_active_internship(self) -> bool:
        return self.effective_status == "Active"

    def __repr__(self):
        return f"<Intern {self.full_name} ({self.cnic})>"
