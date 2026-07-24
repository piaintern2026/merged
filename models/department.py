"""
models/department.py
---------------------
Represents a PIA department (e.g. IT, Finance, Engineering) that
Project Managers and Interns belong to.
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class Department(db.Model):
    """Department master table."""

    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)

    # City this department operates in (PIA has interns/departments
    # spread across multiple cities). Nullable at the DB level -- so
    # that adding this column never breaks existing rows created before
    # this field existed -- but required by the "Add/Edit Department"
    # forms in routes/department.py for any new or edited record.
    city = db.Column(db.String(80), nullable=True)

    # 'Active' or 'Inactive'
    status = db.Column(db.String(20), nullable=False, default="Active")

    created_at = db.Column(db.DateTime, default=now_pkt)

    # Relationships (defined with lazy select, back-populated for convenience)
    project_managers = db.relationship("ProjectManager", backref="department", lazy=True)
    interns = db.relationship("Intern", backref="department", lazy=True)

    def __repr__(self):
        return f"<Department {self.name}>"
