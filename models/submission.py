"""
models/submission.py
---------------------
A link and/or supporting file an intern submits against their
assigned project -- a Google Drive link, a deployed website URL
(Vercel, Netlify, GitHub Pages, etc.), an uploaded document, or both.
Part of Module 3's Project Submission feature.
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

    # The submitted Google Drive link or deployed website URL. Optional
    # as long as a file is attached instead (see below) -- enforced at
    # the route level, not the database level, since either one alone
    # is a valid submission.
    link = db.Column(db.String(2048), nullable=True)

    # Optional supporting document (PDF/DOC/DOCX/XLS/XLSX/PPT/PPTX),
    # capped at one file per submission. `stored_reference` holds
    # whatever the active storage backend needs to locate the file
    # again -- a full Vercel Blob URL in production, or a bare on-disk
    # filename under SUBMISSIONS_UPLOAD_FOLDER in local dev. See
    # services/file_storage.py.
    stored_reference = db.Column(db.String(1000), nullable=True)
    original_filename = db.Column(db.String(255), nullable=True)

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
