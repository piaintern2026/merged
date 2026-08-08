"""
models/announcement.py
-----------------------
Lightweight org-wide Announcement feature. A Super Admin or Station HR
user can broadcast a message to a role group (all Interns, all Project
Managers, or everyone); each recipient gets an in-app Notification and,
through the Email Notification System, an email.
"""

from utils import now_pkt

from extensions import db


class Announcement(db.Model):
    """A single broadcast announcement."""

    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)

    # Who it was sent to: 'All', 'Intern', 'Project Manager', 'Station HR'.
    audience = db.Column(db.String(30), nullable=False, default="All")

    posted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    recipient_count = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=now_pkt)

    posted_by = db.relationship("User")

    AUDIENCES = ["All", "Intern", "Project Manager", "Station HR"]

    def __repr__(self):
        return f"<Announcement '{self.title[:30]}' -> {self.audience}>"
