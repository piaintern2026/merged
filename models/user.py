"""
models/user.py
---------------
Core authentication model. Every person who can log in to the system
(HR, Project Manager, Intern) has exactly one row in this table.
Role-specific details live in their own tables (ProjectManager, Intern)
linked back to this table via a one-to-one foreign key, keeping the
schema clean (no unused columns per role) and easy to extend.
"""

from datetime import datetime, timezone
from utils import now_pkt

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db


class User(UserMixin, db.Model):
    """Central authentication + role record."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    # Login credentials
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    # Role-based access control. One of: 'Super Admin', 'HR',
    # 'Project Manager', 'Intern'
    role = db.Column(db.String(30), nullable=False, default="Intern")

    # Display name for accounts that have no dedicated profile table
    # (i.e. HR -- Project Manager/Intern already store full_name on
    # their own profile rows). Nullable so existing rows stay valid.
    full_name = db.Column(db.String(120), nullable=True)

    # Common profile fields
    profile_picture = db.Column(db.String(255), nullable=True)  # relative filename
    is_active_account = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=now_pkt)
    updated_at = db.Column(
        db.DateTime,
        default=now_pkt,
        onupdate=now_pkt,
    )

    # Relationships to role-specific profile tables (one-to-one)
    project_manager_profile = db.relationship(
        "ProjectManager", backref="user", uselist=False, cascade="all, delete-orphan"
    )
    intern_profile = db.relationship(
        "Intern", backref="user", uselist=False, cascade="all, delete-orphan"
    )

    # ------------------------------------------------------------------
    # Password helpers
    # ------------------------------------------------------------------
    def set_password(self, raw_password: str) -> None:
        """Hash and store the given plaintext password."""
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        """Verify a plaintext password against the stored hash."""
        return check_password_hash(self.password_hash, raw_password)

    # Flask-Login uses is_active to block disabled accounts from logging in.
    @property
    def is_active(self):  # noqa: D401 (overrides UserMixin property)
        return self.is_active_account

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------
    def display_name(self) -> str:
        """Return a human friendly name for navbars / greetings."""
        if self.role == "Project Manager" and self.project_manager_profile:
            return self.project_manager_profile.full_name
        if self.role == "Intern" and self.intern_profile:
            return self.intern_profile.full_name
        return self.full_name or self.username

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"
