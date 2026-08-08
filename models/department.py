"""
models/department.py
---------------------
Represents a department (e.g. IT, Finance, Engineering) that
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

    # City this department operates in (the organization has interns/departments
    # spread across multiple cities). Nullable at the DB level -- so
    # that adding this column never breaks existing rows created before
    # this field existed -- but required by the "Add/Edit Department"
    # forms in routes/department.py for any new or edited record.
    # Restricted to the PIA_CITIES dropdown (see utils.py) rather than
    # a free-text field.
    city = db.Column(db.String(80), nullable=True)

    # Soft-delete flag. Departments are never permanently deleted -- HR
    # instead disables one, which hides it from "Add" pickers for new
    # PMs/interns/projects while every existing relationship (PMs,
    # interns, projects already linked to it) stays intact.
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=now_pkt)

    # Relationships (defined with lazy select, back-populated for convenience)
    project_managers = db.relationship("ProjectManager", backref="department", lazy=True)
    interns = db.relationship("Intern", backref="department", lazy=True)
    sub_departments = db.relationship(
        "SubDepartment",
        backref="department",
        lazy=True,
        order_by="SubDepartment.name",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Department {self.name}>"


class SubDepartment(db.Model):
    """Sub-Department master table -- children of a Department, forming
    the Department -> Sub Department hierarchy used by the cascading
    dropdown everywhere a department is picked (Intern Registration,
    Project Creation, Project Assignment, Filters, Reports, etc.).

    Normalized: every sub-department row belongs to exactly one
    Department via ``department_id`` (a real foreign key), so
    Intern/Project/ProjectManager records that store a
    ``sub_department_id`` always resolve back to a consistent,
    single-source-of-truth hierarchy (see utils.DEPARTMENT_HIERARCHY,
    which is what seeds this table).
    """

    __tablename__ = "sub_departments"
    __table_args__ = (
        db.UniqueConstraint("department_id", "name", name="uq_subdept_department_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)

    # Soft-delete flag, mirroring Department.is_active -- a disabled
    # sub-department is hidden from pickers for new records while any
    # existing Intern/Project/ProjectManager rows already linked to it
    # stay intact.
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=now_pkt)

    def __repr__(self):
        return f"<SubDepartment {self.name} (dept={self.department_id})>"
