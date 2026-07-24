"""
routes/dashboard.py
--------------------
Main landing page after login. Shows summary cards and recent activity
for HR, and assigned interns/projects/deadlines for a Project
Manager (the "Manager Dashboard" of Module 2).
"""

from datetime import date
from utils import today_pkt

from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import func

from extensions import db
from models import Department, ProjectManager, Intern, Project, Attendance, Leave
from utils import current_pm_profile, PIA_CITIES

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@dashboard_bp.route("/")
@login_required
def index():
    """Render the role-aware dashboard with key statistics."""

    if current_user.role in ("HR", "Super Admin"):
        # Summary statistics shown as dashboard cards.
        stats = {
            "total_departments": Department.query.count(),
            "active_departments": Department.query.filter_by(status="Active").count(),
            "total_project_managers": ProjectManager.query.count(),
            "active_project_managers": ProjectManager.query.filter_by(is_active_flag=True).count(),
            "total_interns": Intern.query.count(),
            "total_projects": Project.query.count(),
            "projects_pending": Project.query.filter_by(status="Pending").count(),
            "projects_completed": Project.query.filter_by(status="Completed").count(),
            "pending_leaves": Leave.query.filter_by(status="Pending").count(),
        }

        # Recent activity feed: latest additions across the system.
        recent_pms = ProjectManager.query.order_by(ProjectManager.created_at.desc()).limit(5).all()
        recent_interns = Intern.query.order_by(Intern.created_at.desc()).limit(5).all()
        recent_projects = Project.query.order_by(Project.created_at.desc()).limit(5).all()

        # City-based Management: total interns per city (dynamic, computed
        # fresh on every request -- no hardcoded counts) and per department,
        # used to render the "Interns by City" / "Interns by Department"
        # breakdown widgets and their charts on the dashboard.
        city_rows = dict(
            db.session.query(Intern.city, func.count(Intern.id))
            .group_by(Intern.city)
            .all()
        )
        # Always show every known PIA city, even ones with zero interns.
        interns_by_city = [{"label": city, "count": city_rows.get(city, 0)} for city in PIA_CITIES]

        dept_rows = (
            db.session.query(Department.name, func.count(Intern.id))
            .outerjoin(Intern, Intern.department_id == Department.id)
            .group_by(Department.id, Department.name)
            .order_by(Department.name)
            .all()
        )
        interns_by_department = [{"label": name, "count": count} for name, count in dept_rows]

        return render_template(
            "dashboard.html",
            stats=stats,
            recent_pms=recent_pms,
            recent_interns=recent_interns,
            recent_projects=recent_projects,
            interns_by_city=interns_by_city,
            interns_by_department=interns_by_department,
        )

    if current_user.role == "Project Manager":
        pm = current_pm_profile()
        if pm is None:
            # Profile missing/misconfigured -- show an empty-state dashboard.
            return render_template(
                "dashboard.html", pm_projects=[], pm_interns=[], upcoming_deadlines=[], pending_leaves=0
            )

        pm_projects = Project.query.filter_by(assigned_manager_id=pm.id).order_by(
            Project.deadline.asc()
        ).all()

        # Distinct interns currently assigned to this manager's projects.
        intern_ids = {p.assigned_intern_id for p in pm_projects if p.assigned_intern_id}
        pm_interns = (
            Intern.query.filter(Intern.id.in_(intern_ids)).order_by(Intern.full_name).all()
            if intern_ids
            else []
        )

        # Active (not finished) projects, sorted by nearest deadline first.
        upcoming_deadlines = [
            p for p in pm_projects if p.status not in ("Completed", "Approved")
        ]

        # Pending leave requests for interns currently assigned to this PM.
        assigned_intern_ids = {
            intern.id for intern in Intern.query.all()
            if intern.current_manager and intern.current_manager.id == pm.id
        }
        pending_leaves = (
            Leave.query.filter(
                Leave.intern_id.in_(assigned_intern_ids), Leave.status == "Pending"
            ).count()
            if assigned_intern_ids
            else 0
        )

        return render_template(
            "dashboard.html",
            pm_projects=pm_projects,
            pm_interns=pm_interns,
            upcoming_deadlines=upcoming_deadlines,
            pending_leaves=pending_leaves,
            today=today_pkt(),
        )

    if current_user.role == "Intern":
        # The full Intern Dashboard lives in the Intern Portal (Module 3).
        return redirect(url_for("intern_portal.dashboard"))

    # Fallback for any other/unrecognised role.
    return render_template("dashboard.html")
