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
    p_number = db.Column(db.String(30), unique=True, nullable=False)  # PIA personnel number
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)
    city = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    designation = db.Column(db.String(120), nullable=False)

    is_active_flag = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=now_pkt)

    def __repr__(self):
        return f"<ProjectManager {self.full_name} ({self.p_number})>"
