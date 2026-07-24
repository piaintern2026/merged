"""
models/submission.py
---------------------
A link an intern submits against their assigned project -- either a
Google Drive link or a deployed website URL (Vercel, Netlify, GitHub
Pages, etc.). Part of Module 3's Project Submission feature.
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class ProjectSubmission(db.Model):
    """A single submitted project link (Google Drive or deployed URL).

    Once an intern submits, HR and the Project Manager each review the
    submission independently -- both can Approve or Reject and leave
    remarks. Neither review depends on the other.
    """

    __tablename__ = "project_submissions"

    # Independent per-reviewer decision states.
    STATUSES = ["Pending", "Approved", "Rejected"]

    id = db.Column(db.Integer, primary_key=True)

    intern_id = db.Column(db.Integer, db.ForeignKey("interns.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)

    # The submitted Google Drive link or deployed website URL.
    link = db.Column(db.String(1000), nullable=False)

    notes = db.Column(db.String(500), nullable=True)

    submitted_at = db.Column(db.DateTime, default=now_pkt)

    # ------------------------------------------------------------
    # HR review (independent of the Project Manager's review)
    # ------------------------------------------------------------
    hr_status = db.Column(db.String(20), nullable=False, default="Pending")
    hr_remarks = db.Column(db.String(1000), nullable=True)
    hr_reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    hr_reviewed_at = db.Column(db.DateTime, nullable=True)

    # ------------------------------------------------------------
    # Project Manager review (independent of HR's review)
    # ------------------------------------------------------------
    pm_status = db.Column(db.String(20), nullable=False, default="Pending")
    pm_remarks = db.Column(db.String(1000), nullable=True)
    pm_reviewed_by_id = db.Column(db.Integer, db.ForeignKey("project_managers.id"), nullable=True)
    pm_reviewed_at = db.Column(db.DateTime, nullable=True)

    # Relationships
    intern = db.relationship("Intern", backref="submissions")
    project = db.relationship("Project", backref="submissions")
    hr_reviewed_by = db.relationship("User", foreign_keys=[hr_reviewed_by_id])
    pm_reviewed_by = db.relationship("ProjectManager", foreign_keys=[pm_reviewed_by_id])

    @property
    def overall_status(self) -> str:
        """A single at-a-glance status combining both independent reviews."""
        if self.hr_status == "Rejected" or self.pm_status == "Rejected":
            return "Rejected"
        if self.hr_status == "Approved" and self.pm_status == "Approved":
            return "Approved"
        return "Pending"

    def __repr__(self):
        return f"<ProjectSubmission {self.link} (Intern #{self.intern_id})>"
