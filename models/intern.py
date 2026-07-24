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
    degree = db.Column(db.String(120), nullable=False)
    semester = db.Column(db.String(20), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)
    city = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(20), nullable=False)

    # Editable via the Intern Portal profile page (Module 3). Nullable
    # because it isn't collected at registration time (Module 1).
    address = db.Column(db.String(255), nullable=True)

    internship_start_date = db.Column(db.Date, nullable=False)
    internship_end_date = db.Column(db.Date, nullable=False)

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

    def __repr__(self):
        return f"<Intern {self.full_name} ({self.cnic})>"
