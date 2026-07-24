"""
models/project.py
------------------
Represents a project assigned by HR to a Project Manager and (optionally)
an Intern within a Department. Central entity of Module 2's Project
Module.
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class Project(db.Model):
    """Project master table."""

    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)

    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)

    # Assignment: a project is led by one Project Manager and worked on
    # by one Intern (kept simple/1-1 per spec; nullable so HR can create
    # a project first and assign people afterwards).
    assigned_manager_id = db.Column(
        db.Integer, db.ForeignKey("project_managers.id"), nullable=True
    )
    assigned_intern_id = db.Column(db.Integer, db.ForeignKey("interns.id"), nullable=True)

    # 'Low', 'Medium', 'High', 'Critical'
    priority = db.Column(db.String(20), nullable=False, default="Medium")

    start_date = db.Column(db.Date, nullable=False)
    deadline = db.Column(db.Date, nullable=False)

    # One of: Pending, Working, Submitted, Approved, Rejected, Completed
    status = db.Column(db.String(20), nullable=False, default="Pending")

    created_at = db.Column(db.DateTime, default=now_pkt)
    updated_at = db.Column(
        db.DateTime,
        default=now_pkt,
        onupdate=now_pkt,
    )

    # Relationships
    department = db.relationship("Department", backref="projects")
    manager = db.relationship("ProjectManager", backref="projects")
    intern = db.relationship("Intern", backref="projects")

    # Allowed values, used by routes/templates for validation & dropdowns
    PRIORITIES = ["Low", "Medium", "High", "Critical"]
    STATUSES = ["Pending", "Working", "Submitted", "Approved", "Rejected", "Completed"]

    def is_overdue(self) -> bool:
        """True if the deadline has passed and the project isn't finished."""
        from utils import today_pkt

        return self.deadline < today_pkt() and self.status not in ("Completed", "Approved")

    def __repr__(self):
        return f"<Project {self.title} ({self.status})>"
