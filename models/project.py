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


# ----------------------------------------------------------------------
# Association table for the many-to-many Project <-> Intern assignment.
# A project can have multiple interns working on it, and an intern can
# be assigned to multiple projects; the composite primary key on
# (project_id, intern_id) stops the same intern from ever being
# assigned to the same project twice.
# ----------------------------------------------------------------------
project_interns = db.Table(
    "project_interns",
    db.Column("project_id", db.Integer, db.ForeignKey("projects.id"), primary_key=True),
    db.Column("intern_id", db.Integer, db.ForeignKey("interns.id"), primary_key=True),
    db.Column("assigned_at", db.DateTime, default=now_pkt),
)


class Project(db.Model):
    """Project master table."""

    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String(150), nullable=False)
    # Required: every project must describe what the work actually is.
    description = db.Column(db.Text, nullable=False)

    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)
    sub_department_id = db.Column(
        db.Integer, db.ForeignKey("sub_departments.id"), nullable=True
    )
    sub_department = db.relationship("SubDepartment", foreign_keys=[sub_department_id])

    # Assignment: a project is led by one Project Manager (nullable so
    # HR can create a project first and assign a manager afterwards).
    assigned_manager_id = db.Column(
        db.Integer, db.ForeignKey("project_managers.id"), nullable=True
    )

    # DEPRECATED: leftover from the old one-intern-per-project design.
    # No longer read or written anywhere -- the `interns` many-to-many
    # relationship (backed by `project_interns`) is the source of truth
    # now. Kept here (as nullable) only so the column stays mapped and
    # the app.py startup migration can safely loosen its old NOT NULL
    # constraint instead of the column silently blocking every insert.
    assigned_intern_id = db.Column(
        db.Integer, db.ForeignKey("interns.id"), nullable=True
    )

    # Many-to-many: a project can have multiple interns working on it,
    # and an intern can work on multiple projects.
    interns = db.relationship(
        "Intern",
        secondary=project_interns,
        backref=db.backref("assigned_projects", lazy=True),
        lazy=True,
    )

    start_date = db.Column(db.Date, nullable=False)
    deadline = db.Column(db.Date, nullable=False)

    # One of: Pending, Working, Submitted, Approved, Rejected, Completed
    status = db.Column(db.String(20), nullable=False, default="Pending")

    # Soft-delete flag. Projects are never permanently deleted (they may
    # be linked to submissions, milestones, work logs, evaluations and
    # reports); HR/Super Admin instead disable a project to hide it from
    # active workflows while preserving all history/relationships.
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=now_pkt)
    updated_at = db.Column(
        db.DateTime,
        default=now_pkt,
        onupdate=now_pkt,
    )

    # Relationships
    department = db.relationship("Department", backref="projects")
    manager = db.relationship("ProjectManager", backref="projects")

    # Allowed values, used by routes/templates for validation & dropdowns
    STATUSES = ["Pending", "Working", "Submitted", "Approved", "Rejected", "Completed"]

    def has_intern(self, intern_id) -> bool:
        """True if the given intern is already assigned to this project."""
        try:
            intern_id = int(intern_id)
        except (TypeError, ValueError):
            return False
        return any(i.id == intern_id for i in self.interns)

    # ------------------------------------------------------------------
    # Forward-only status workflow
    # ------------------------------------------------------------------
    # Projects must always progress forward through their lifecycle and
    # can never be moved back to an earlier stage:
    #   Pending -> Working -> Submitted -> (Approved | Rejected) -> Completed
    # Approved/Rejected sit at the same "under review outcome" stage (HR
    # can correct a review decision between the two), but neither can
    # ever drop back to Pending/Working/Submitted, and once a project is
    # Completed its status is locked permanently.
    STATUS_RANK = {
        "Pending": 0,
        "Working": 1,
        "Submitted": 2,
        "Approved": 3,
        "Rejected": 3,
        "Completed": 4,
    }

    def can_transition_to(self, new_status: str) -> bool:
        """True if moving from the current status to ``new_status`` is a
        legal forward (or same-stage) move under the forward-only workflow."""
        if new_status not in self.STATUS_RANK:
            return False
        if self.status == "Completed":
            # Completed is final -- no further changes of any kind.
            return new_status == "Completed"
        return self.STATUS_RANK[new_status] >= self.STATUS_RANK.get(self.status, 0)

    def is_overdue(self) -> bool:
        """True if the deadline has passed and the project isn't finished."""
        from utils import today_pkt

        return self.deadline < today_pkt() and self.status not in ("Completed", "Approved")

    @property
    def completion_percentage(self) -> int:
        """Overall project completion, auto-calculated from milestones.

        If the project has milestones, completion is the share of
        milestones marked Completed. If it has none yet, fall back to
        the project's own status (100% once Completed/Approved, else 0)
        so the progress bar still means something before milestones
        are added.
        """
        milestones = [m for m in self.milestones if m.is_active]
        if not milestones:
            return 100 if self.status in ("Completed", "Approved") else 0
        completed = sum(1 for m in milestones if m.status == "Completed")
        return round((completed / len(milestones)) * 100)

    def __repr__(self):
        return f"<Project {self.title} ({self.status})>"
