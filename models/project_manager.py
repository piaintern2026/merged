"""
models/project_manager.py
--------------------------
Role-specific profile data for users whose role is 'Project Manager'.
Linked one-to-one with the User table via user_id.
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class ProjectManager(db.Model):
    """Project Manager profile."""

    __tablename__ = "project_managers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)

    full_name = db.Column(db.String(120), nullable=False)
    # P.No, Phone and Designation are all optional at creation time -- a
    # PM can be added with just a name/department/city/login and have
    # these filled in later via edit. Left as NULL (not "") when blank
    # so the unique constraint on p_number never collides between two
    # PMs who both skipped it (NULL != NULL, unlike "" == "").
    p_number = db.Column(db.String(30), unique=True, nullable=True)  # Organization personnel number
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)
    sub_department_id = db.Column(
        db.Integer, db.ForeignKey("sub_departments.id"), nullable=True
    )
    sub_department = db.relationship("SubDepartment", foreign_keys=[sub_department_id])
    city = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    designation = db.Column(db.String(120), nullable=True)

    is_active_flag = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=now_pkt)

    def __repr__(self):
        return f"<ProjectManager {self.full_name} ({self.p_number})>"
