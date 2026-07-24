"""
models/work_log.py
-------------------
Daily work log entries submitted by an intern: what they worked on,
how many hours, and their self-reported progress percentage. Powers
the "Current Progress" figure on the Intern Dashboard.
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class DailyWorkLog(db.Model):
    """A single day's work log entry."""

    __tablename__ = "daily_work_logs"

    id = db.Column(db.Integer, primary_key=True)

    intern_id = db.Column(db.Integer, db.ForeignKey("interns.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)

    log_date = db.Column(db.Date, nullable=False)
    description = db.Column(db.Text, nullable=False)
    hours_worked = db.Column(db.Float, nullable=False)

    # Intern's self-reported overall project completion at the time of
    # this entry (0-100). The most recent value drives the dashboard's
    # "Current Progress" bar.
    progress_percent = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=now_pkt)

    # Relationships
    intern = db.relationship("Intern", backref="work_logs")
    project = db.relationship("Project", backref="work_logs")

    def __repr__(self):
        return f"<DailyWorkLog Intern#{self.intern_id} {self.log_date}>"
