"""
models/report.py
-----------------
The single Final Internship Report an intern submits, with an
optional supporting PDF file upload.
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class FinalReport(db.Model):
    """
    The single Final Internship Report an intern submits at the end of
    their internship. One row per intern (unique constraint), editable
    up until HR locks the workflow in a future module.
    """

    __tablename__ = "final_reports"

    id = db.Column(db.Integer, primary_key=True)
    intern_id = db.Column(db.Integer, db.ForeignKey("interns.id"), unique=True, nullable=False)

    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.Text, nullable=False)

    stored_filename = db.Column(db.String(255), nullable=True)
    original_filename = db.Column(db.String(255), nullable=True)

    submitted_at = db.Column(db.DateTime, default=now_pkt)
    updated_at = db.Column(
        db.DateTime,
        default=now_pkt,
        onupdate=now_pkt,
    )

    intern = db.relationship("Intern", backref=db.backref("final_report", uselist=False))

    def __repr__(self):
        return f"<FinalReport Intern#{self.intern_id}>"
