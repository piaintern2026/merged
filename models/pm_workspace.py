"""
models/pm_workspace.py
------------------------
New models supporting the Project Manager workspace features that were
missing from the PM module:

  - ProjectMilestone: milestones within a project, used to compute
    overall project completion and to drive the visual timeline.
These are additive tables only -- nothing here touches or duplicates
the existing Project, Intern, or ProjectManager models.
"""

from utils import now_pkt, today_pkt

from extensions import db


class ProjectMilestone(db.Model):
    """A single milestone/checkpoint within a project's timeline."""

    __tablename__ = "project_milestones"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)

    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    due_date = db.Column(db.Date, nullable=False)

    # One of: Pending, In Progress, Completed
    status = db.Column(db.String(20), nullable=False, default="Pending")
    completed_at = db.Column(db.DateTime, nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("project_managers.id"), nullable=True)

    # Soft-delete flag: milestones are never hard-deleted so the project
    # timeline/history stays intact; a PM/Super Admin disables one instead.
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=now_pkt)
    updated_at = db.Column(db.DateTime, default=now_pkt, onupdate=now_pkt)

    project = db.relationship(
        "Project",
        backref=db.backref(
            "milestones", lazy=True, cascade="all, delete-orphan",
            order_by="ProjectMilestone.due_date",
        ),
    )
    created_by = db.relationship("ProjectManager")

    STATUSES = ["Pending", "In Progress", "Completed"]

    @property
    def is_completed(self) -> bool:
        return self.status == "Completed"

    @property
    def is_overdue(self) -> bool:
        return self.status != "Completed" and self.due_date < today_pkt()

    def __repr__(self):
        return f"<ProjectMilestone {self.title} ({self.status})>"



