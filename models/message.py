"""
models/message.py
-------------------
Enterprise in-app messaging between an Intern and their Project
Manager. This is a direct, internal-only conversation (no email
integration) -- each row is a single message from one user to
another, keyed by the two participants so a conversation thread can
be reconstructed by querying both directions.

Read/unread status is tracked per-message (`is_read`) and new-message
alerts are surfaced through the existing Notification system (see
`notify_user` in utils.py / models/notification.py) rather than a
second, duplicate notification mechanism.
"""

from utils import now_pkt, today_pkt

from extensions import db


class Message(db.Model):
    """A single direct message between two users (Intern <-> PM)."""

    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)

    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    body = db.Column(db.Text, nullable=False)

    # Stored separately (in addition to created_at) so the schema
    # matches the Date / Time fields the messaging feature calls for.
    sent_date = db.Column(db.Date, nullable=False, default=today_pkt)
    sent_time = db.Column(db.Time, nullable=False, default=lambda: now_pkt().time())

    is_read = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=now_pkt)

    sender = db.relationship("User", foreign_keys=[sender_id], backref="sent_messages")
    receiver = db.relationship("User", foreign_keys=[receiver_id], backref="received_messages")

    def __repr__(self):
        return f"<Message #{self.id} {self.sender_id} -> {self.receiver_id}>"
