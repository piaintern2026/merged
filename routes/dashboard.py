"""
routes/dashboard.py
--------------------
Main landing page after login. Shows summary cards and recent activity
for HR, and assigned interns/projects/deadlines for a Project
Manager (the "Manager Dashboard" of Module 2).
"""

from datetime import date, timedelta
from utils import today_pkt

from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import func

from extensions import db
from models import (
    Department,
    ProjectManager,
    Intern,
    Project,
    Attendance,
    Leave,
    InternRotation,
    ProjectSubmission,
    PMEvaluation,
    ProjectMilestone,
)
from utils import current_pm_profile, PIA_CITIES, hr_city_scope

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@dashboard_bp.route("/")
@login_required
def index():
    """Render the role-aware dashboard with key statistics."""

    if current_user.role in ("Station HR", "Admin"):
        is_city_scoped, hr_city = hr_city_scope()

        # Module 2 automation: opportunistically sweep for any intern
        # whose last internship day has been reached every time an
        # HR/Admin dashboard loads (in addition to the login-time check
        # in routes/auth.py and the `flask complete-expired-internships`
        # cron command) -- so a deployment with no external scheduler
        # still keeps internship statuses current without HR having to
        # do anything.
        from services.intern_lifecycle import complete_expired_internships

        complete_expired_internships()

        # Base querysets, scoped to the Station HR's assigned city when
        # applicable (Admin always sees the whole organisation).
        interns_qs = Intern.query
        departments_qs = Department.query
        pms_qs = ProjectManager.query
        projects_qs = Project.query
        attendance_qs = Attendance.query
        rotations_qs = InternRotation.query
        if is_city_scoped:
            interns_qs = interns_qs.filter(Intern.station == hr_city)
            departments_qs = departments_qs.filter(Department.city == hr_city)
            pms_qs = pms_qs.filter(ProjectManager.city == hr_city)
            projects_qs = projects_qs.join(Department).filter(Department.city == hr_city)
            attendance_qs = attendance_qs.join(Intern).filter(Intern.station == hr_city)
            rotations_qs = rotations_qs.join(Intern).filter(Intern.station == hr_city)

        # Summary statistics shown as dashboard cards.
        total_interns = interns_qs.count()
        active_interns = sum(
            1 for i in interns_qs.all() if i.effective_status == "Active"
        )

        # Attendance %: days the intern actually showed up (Present or
        # Late) out of every attendance record ever marked. Late is
        # still an attended day -- only Absent/Leave should pull the
        # rate down.
        total_attendance_records = attendance_qs.count()
        present_records = attendance_qs.filter(
            Attendance.status.in_(Attendance.ATTENDED_STATUSES)
        ).count()
        attendance_percentage = (
            round((present_records / total_attendance_records) * 100, 1)
            if total_attendance_records
            else 0
        )

        # Rotations This Week: rotations that started in the last 7 days.
        week_ago = today_pkt() - timedelta(days=7)
        rotations_this_week = rotations_qs.filter(
            InternRotation.start_date >= week_ago
        ).count()

        # Upcoming Deadlines: active projects due within the next 7 days.
        deadline_window = today_pkt() + timedelta(days=7)
        upcoming_deadlines_count = projects_qs.filter(
            Project.deadline <= deadline_window,
            Project.deadline >= today_pkt(),
            Project.status.notin_(["Completed", "Approved"]),
        ).count()

        stats = {
            "total_departments": departments_qs.count(),
            "active_departments": departments_qs.filter(Department.is_active.is_(True)).count(),
            "total_project_managers": pms_qs.count(),
            "active_project_managers": pms_qs.filter_by(is_active_flag=True).count(),
            "total_interns": total_interns,
            "active_interns": active_interns,
            "total_projects": projects_qs.count(),
            "projects_pending": projects_qs.filter(Project.status == "Pending").count(),
            "projects_completed": projects_qs.filter(Project.status == "Completed").count(),
            "pending_leaves": (
                Leave.query.join(Intern).filter(Intern.station == hr_city, Leave.status == "Pending").count()
                if is_city_scoped
                else Leave.query.filter_by(status="Pending").count()
            ),
            "attendance_percentage": attendance_percentage,
            "rotations_this_week": rotations_this_week,
            "upcoming_deadlines_count": upcoming_deadlines_count,
        }

        # Recent activity feed: latest additions across the system (or,
        # for a Station HR, just their own city).
        recent_pms = pms_qs.order_by(ProjectManager.created_at.desc()).limit(5).all()
        recent_interns = interns_qs.order_by(Intern.created_at.desc()).limit(5).all()
        recent_projects = projects_qs.order_by(Project.created_at.desc()).limit(5).all()

        # City-based Management: total interns per city (dynamic, computed
        # fresh on every request -- no hardcoded counts) and per department,
        # used to render the "Interns by City" / "Interns by Department"
        # breakdown widgets and their charts on the dashboard.
        city_rows = dict(
            db.session.query(Intern.station, func.count(Intern.id))
            .group_by(Intern.station)
            .all()
        )
        # Admin sees every known city (even zero-intern ones); a
        # Station HR only ever sees their own assigned city.
        cities_for_widget = [hr_city] if is_city_scoped and hr_city else PIA_CITIES
        interns_by_city = [{"label": city, "count": city_rows.get(city, 0)} for city in cities_for_widget]

        dept_rows_query = (
            db.session.query(db.func.lower(Department.name), func.count(Intern.id))
            .outerjoin(Intern, Intern.department_id == Department.id)
        )
        if is_city_scoped:
            dept_rows_query = dept_rows_query.filter(Department.city == hr_city)
        dept_rows = dept_rows_query.group_by(Department.id, db.func.lower(Department.name)).order_by(db.func.lower(Department.name)).all()
        interns_by_department = [{"label": name, "count": count} for name, count in dept_rows]

        # Project Status: how many projects sit in each stage of the
        # pipeline right now (Pending/Working/Submitted/... Completed).
        project_status_query = db.session.query(Project.status, func.count(Project.id))
        if is_city_scoped:
            project_status_query = project_status_query.join(
                Department, Project.department_id == Department.id
            ).filter(Department.city == hr_city)
        project_status_rows = dict(project_status_query.group_by(Project.status).all())
        project_status = [
            {"label": s, "count": project_status_rows.get(s, 0)} for s in Project.STATUSES
        ]

        # Monthly Registered Interns: intern sign-ups for each of the last
        # 6 calendar months, computed from real Intern.created_at values.
        month_labels, month_starts = [], []
        today = today_pkt()
        cursor = date(today.year, today.month, 1)
        for _ in range(6):
            month_starts.append(cursor)
            month_labels.append(cursor.strftime("%b %Y"))
            # Step back one month.
            prev_month_end = cursor - timedelta(days=1)
            cursor = date(prev_month_end.year, prev_month_end.month, 1)
        month_starts.reverse()
        month_labels.reverse()

        monthly_counts = []
        for i, start in enumerate(month_starts):
            if i + 1 < len(month_starts):
                end = month_starts[i + 1]
            else:
                # Last bucket runs through the end of the current month.
                if start.month == 12:
                    end = date(start.year + 1, 1, 1)
                else:
                    end = date(start.year, start.month + 1, 1)
            month_query = Intern.query.filter(
                Intern.created_at >= start, Intern.created_at < end
            )
            if is_city_scoped:
                month_query = month_query.filter(Intern.station == hr_city)
            count = month_query.count()
            monthly_counts.append(count)
        monthly_registered_interns = [
            {"label": lbl, "count": cnt} for lbl, cnt in zip(month_labels, monthly_counts)
        ]

        # Attendance Statistics: breakdown of every attendance record ever
        # marked, by status (Present/Absent/Leave/etc).
        attendance_stats_query = db.session.query(Attendance.status, func.count(Attendance.id))
        if is_city_scoped:
            attendance_stats_query = attendance_stats_query.join(Intern).filter(Intern.station == hr_city)
        attendance_rows = dict(attendance_stats_query.group_by(Attendance.status).all())
        attendance_stats = [
            {"label": status, "count": cnt} for status, cnt in attendance_rows.items()
        ]

        # Rotation Status: active (currently ongoing) vs completed rotations.
        active_rotations = rotations_qs.filter(InternRotation.end_date.is_(None)).count()
        completed_rotations = rotations_qs.filter(InternRotation.end_date.isnot(None)).count()
        rotation_status = [
            {"label": "Active", "count": active_rotations},
            {"label": "Completed", "count": completed_rotations},
        ]

        # Approval Center: everything genuinely awaiting action right now.
        # (This app's data model only has a real "pending" workflow state
        # for Leaves and Project Submissions -- Evaluations, Rotations and
        # Reports are recorded immediately with no approval step, so they
        # are intentionally left out rather than shown as fake pending
        # counts.)
        pending_leaves_query = Leave.query.filter_by(status="Pending")
        if is_city_scoped:
            pending_leaves_query = pending_leaves_query.join(Intern).filter(Intern.station == hr_city)
        pending_leaves_list = pending_leaves_query.order_by(Leave.created_at.desc()).limit(10).all()

        pending_project_submissions = (
            projects_qs.filter(Project.status == "Submitted")
            .order_by(Project.updated_at.desc())
            .limit(10)
            .all()
        )

        return render_template(
            "dashboard.html",
            stats=stats,
            recent_pms=recent_pms,
            recent_interns=recent_interns,
            recent_projects=recent_projects,
            interns_by_city=interns_by_city,
            interns_by_department=interns_by_department,
            project_status=project_status,
            monthly_registered_interns=monthly_registered_interns,
            attendance_stats=attendance_stats,
            rotation_status=rotation_status,
            pending_leaves_list=pending_leaves_list,
            pending_project_submissions=pending_project_submissions,
        )

    if current_user.role == "Project Manager":
        pm = current_pm_profile()
        if pm is None:
            # Profile missing/misconfigured -- show an empty-state dashboard.
            return render_template(
                "dashboard.html", pm_projects=[], pm_interns=[], upcoming_deadlines=[], pending_leaves=0
            )

        today = today_pkt()
        week_end = today + timedelta(days=7)

        pm_projects = Project.query.filter_by(assigned_manager_id=pm.id).order_by(
            Project.deadline.asc()
        ).all()
        pm_project_ids = [p.id for p in pm_projects]

        # Interns "assigned" to this PM: the union of (a) interns on one of
        # this PM's projects and (b) interns whose *current* rotation/
        # manager link points at this PM (set via SA/HR's Rotate/Assign
        # Intern action, Intern.current_manager). This is exactly the same
        # union `_scoped_interns()` in routes/pm_extra.py uses -- the
        # same helper backing Rotation History, Reports, and every other
        # PM-scoped intern list -- so the dashboard card, its "assigned
        # interns" list, and the Interns page can never disagree, and a
        # newly-assigned intern shows up immediately without needing a
        # project first.
        from routes.pm_extra import _scoped_interns

        pm_interns = _scoped_interns(pm, pm_projects)
        all_scoped_intern_ids = {i.id for i in pm_interns}

        # Active (not finished) projects, sorted by nearest deadline first.
        upcoming_deadlines = [
            p for p in pm_projects if p.status not in ("Completed", "Approved")
        ]
        projects_due_this_week = [
            p for p in upcoming_deadlines if today <= p.deadline <= week_end
        ]

        # ---- KPI cards -------------------------------------------------
        pending_submissions_count = (
            ProjectSubmission.query.filter(
                ProjectSubmission.project_id.in_(pm_project_ids),
                ProjectSubmission.pm_status == "Pending",
            ).count()
            if pm_project_ids
            else 0
        )

        completed_projects = sum(1 for p in pm_projects if p.status in ("Completed", "Approved"))
        completion_rate = (
            round((completed_projects / len(pm_projects)) * 100, 1) if pm_projects else 0
        )

        pending_evaluations_count = max(len(pm_interns) - PMEvaluation.query.filter(
            PMEvaluation.project_manager_id == pm.id, PMEvaluation.is_finalized.is_(True)
        ).count(), 0) if pm_interns else 0

        pm_attendance_q = Attendance.query.filter(Attendance.intern_id.in_(all_scoped_intern_ids)) \
            if all_scoped_intern_ids else None
        total_att = pm_attendance_q.count() if pm_attendance_q is not None else 0
        present_att = (
            pm_attendance_q.filter(Attendance.status.in_(Attendance.ATTENDED_STATUSES)).count()
            if pm_attendance_q is not None else 0
        )
        pm_attendance_percentage = round((present_att / total_att) * 100, 1) if total_att else 0

        pending_leaves = (
            Leave.query.filter(
                Leave.intern_id.in_(all_scoped_intern_ids), Leave.status == "Pending"
            ).count()
            if all_scoped_intern_ids
            else 0
        )

        stats = {
            "assigned_interns": len(pm_interns),
            "assigned_projects": len(pm_projects),
            "pending_submissions": pending_submissions_count,
            "due_this_week": len(projects_due_this_week),
            "pending_evaluations": pending_evaluations_count,
            "attendance_percentage": pm_attendance_percentage,
            "completion_rate": completion_rate,
            "upcoming_deadlines_count": len(upcoming_deadlines),
            "pending_leaves": pending_leaves,
        }

        # ---- Charts ------------------------------------------------------
        proj_status_rows = {}
        for p in pm_projects:
            proj_status_rows[p.status] = proj_status_rows.get(p.status, 0) + 1
        pm_project_status = [
            {"label": s, "count": proj_status_rows.get(s, 0)} for s in Project.STATUSES
        ]

        pm_attendance_rows = dict(
            db.session.query(Attendance.status, func.count(Attendance.id))
            .filter(Attendance.intern_id.in_(all_scoped_intern_ids))
            .group_by(Attendance.status)
            .all()
        ) if all_scoped_intern_ids else {}
        pm_attendance_stats = [
            {"label": s, "count": pm_attendance_rows.get(s, 0)} for s in Attendance.STATUSES
        ]

        submission_rows = dict(
            db.session.query(ProjectSubmission.pm_status, func.count(ProjectSubmission.id))
            .filter(ProjectSubmission.project_id.in_(pm_project_ids))
            .group_by(ProjectSubmission.pm_status)
            .all()
        ) if pm_project_ids else {}
        pm_submission_status = [
            {"label": s, "count": submission_rows.get(s, 0)} for s in ProjectSubmission.STATUSES
        ]

        # Monthly Project Progress: projects marked Completed/Approved per
        # month for the last 6 months (based on updated_at), for this PM.
        month_labels, month_starts = [], []
        cursor = date(today.year, today.month, 1)
        for _ in range(6):
            month_starts.append(cursor)
            month_labels.append(cursor.strftime("%b %Y"))
            prev_month_end = cursor - timedelta(days=1)
            cursor = date(prev_month_end.year, prev_month_end.month, 1)
        month_starts.reverse()
        month_labels.reverse()

        monthly_progress_counts = []
        for i, start in enumerate(month_starts):
            end = month_starts[i + 1] if i + 1 < len(month_starts) else (
                date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
            )
            cnt = Project.query.filter(
                Project.assigned_manager_id == pm.id,
                Project.status.in_(["Completed", "Approved"]),
                Project.updated_at >= start,
                Project.updated_at < end,
            ).count()
            monthly_progress_counts.append(cnt)
        pm_monthly_progress = [
            {"label": lbl, "count": cnt} for lbl, cnt in zip(month_labels, monthly_progress_counts)
        ]

        # ---- Widgets -------------------------------------------------
        overdue_projects = [p for p in pm_projects if p.is_overdue()]
        recent_submissions = (
            ProjectSubmission.query.filter(ProjectSubmission.project_id.in_(pm_project_ids))
            .order_by(ProjectSubmission.submitted_at.desc())
            .limit(6)
            .all()
            if pm_project_ids
            else []
        )
        pending_leaves_list = (
            Leave.query.filter(
                Leave.intern_id.in_(all_scoped_intern_ids), Leave.status == "Pending"
            ).order_by(Leave.created_at.desc()).limit(6).all()
            if all_scoped_intern_ids
            else []
        )
        nearest_deadlines = sorted(upcoming_deadlines, key=lambda p: p.deadline)[:6]

        # ---- New widgets: Milestones, Notes --------
        upcoming_milestones = (
            ProjectMilestone.query.filter(
                ProjectMilestone.project_id.in_(pm_project_ids),
                ProjectMilestone.status != "Completed",
            )
            .order_by(ProjectMilestone.due_date.asc())
            .limit(6)
            .all()
            if pm_project_ids
            else []
        )

        return render_template(
            "dashboard.html",
            pm_projects=pm_projects,
            pm_interns=pm_interns,
            upcoming_deadlines=upcoming_deadlines,
            pending_leaves=pending_leaves,
            today=today,
            pm_stats=stats,
            pm_project_status=pm_project_status,
            pm_attendance_stats=pm_attendance_stats,
            pm_submission_status=pm_submission_status,
            pm_monthly_progress=pm_monthly_progress,
            overdue_projects=overdue_projects,
            recent_submissions=recent_submissions,
            pending_leaves_list=pending_leaves_list,
            nearest_deadlines=nearest_deadlines,
            upcoming_milestones=upcoming_milestones,
        )

    if current_user.role == "Intern":
        # The full Intern Dashboard lives in the Intern Portal (Module 3).
        return redirect(url_for("intern_portal.dashboard"))

    # Fallback for any other/unrecognised role.
    return render_template("dashboard.html")
