"""
routes/pm_extra.py
--------------------
Project Manager-scoped Reports, Analytics, and Calendar
modules. Everything here is read/scoped strictly to the interns and
projects assigned to the logged-in Project Manager, and reuses the
existing PDF/Excel report builders, notification, and email services
so there is no duplicated document-generation logic.
"""

from flask import (
    Blueprint, render_template, redirect, url_for, flash, request,
    send_file, current_app,
)
from flask_login import login_required
from sqlalchemy import func

from extensions import db
from models import (
    Intern, Project, Attendance, Leave, Evaluation, PMEvaluation,
    ProjectSubmission,
    ProjectMilestone, InternRotation,
)
from utils import roles_required, current_pm_profile, today_pkt
from services import pdf_reports, excel_reports

pm_extra_bp = Blueprint("pm_extra", __name__, url_prefix="/pm")


# ----------------------------------------------------------------------
# Shared scoping helpers
# ----------------------------------------------------------------------
def _scoped_projects(pm):
    return Project.query.filter_by(assigned_manager_id=pm.id).order_by(Project.deadline.asc()).all()


def _scoped_interns(pm, projects=None):
    projects = projects if projects is not None else _scoped_projects(pm)
    intern_ids = {i.id for p in projects for i in p.interns}
    rotation_ids = {
        i.id for i in Intern.query.all() if i.current_manager and i.current_manager.id == pm.id
    }
    all_ids = intern_ids | rotation_ids
    return (
        Intern.query.filter(Intern.id.in_(all_ids)).order_by(db.func.lower(Intern.full_name)).all()
        if all_ids
        else []
    )


# ========================================================================
# REPORTS
# ========================================================================
@pm_extra_bp.route("/reports")
@login_required
@roles_required("Project Manager")
def reports_index():
    pm = current_pm_profile()
    if pm is None:
        flash("Your Project Manager profile could not be found.", "danger")
        return redirect(url_for("dashboard.index"))
    interns = _scoped_interns(pm)
    return render_template("pm_extra/reports.html", interns=interns)


@pm_extra_bp.route("/reports/attendance/<fmt>")
@login_required
@roles_required("Project Manager")
def report_attendance(fmt):
    pm = current_pm_profile()
    interns = _scoped_interns(pm)
    intern_ids = [i.id for i in interns]
    records = (
        Attendance.query.filter(Attendance.intern_id.in_(intern_ids))
        .order_by(Attendance.date.desc()).all()
        if intern_ids else []
    )
    if not records:
        flash("There is no attendance data for your interns yet.", "warning")
        return redirect(url_for("pm_extra.reports_index"))
    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_attendance_pdf(records)
            return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                              download_name="my_team_attendance_report.pdf")
        elif fmt == "excel":
            buffer = excel_reports.build_attendance_excel(records)
            return send_file(
                buffer,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True, download_name="my_team_attendance_report.xlsx",
            )
    except Exception:
        current_app.logger.exception("Failed to generate PM attendance report (%s).", fmt)
        flash("Could not generate the report due to a system error. Please try again.", "danger")
        return redirect(url_for("pm_extra.reports_index"))
    flash("Unknown report format requested.", "danger")
    return redirect(url_for("pm_extra.reports_index"))


@pm_extra_bp.route("/reports/evaluation/<fmt>")
@login_required
@roles_required("Project Manager")
def report_evaluation(fmt):
    pm = current_pm_profile()
    interns = _scoped_interns(pm)
    intern_ids = [i.id for i in interns]
    evaluations = (
        Evaluation.query.filter(Evaluation.intern_id.in_(intern_ids))
        .order_by(Evaluation.created_at.desc()).all()
        if intern_ids else []
    )
    if not evaluations:
        flash("There are no evaluations for your interns yet.", "warning")
        return redirect(url_for("pm_extra.reports_index"))
    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_evaluation_pdf(evaluations)
            return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                              download_name="my_team_evaluation_report.pdf")
        elif fmt == "excel":
            buffer = excel_reports.build_evaluation_excel(evaluations)
            return send_file(
                buffer,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True, download_name="my_team_evaluation_report.xlsx",
            )
    except Exception:
        current_app.logger.exception("Failed to generate PM evaluation report (%s).", fmt)
        flash("Could not generate the report due to a system error. Please try again.", "danger")
        return redirect(url_for("pm_extra.reports_index"))
    flash("Unknown report format requested.", "danger")
    return redirect(url_for("pm_extra.reports_index"))


@pm_extra_bp.route("/reports/project/<fmt>")
@login_required
@roles_required("Project Manager")
def report_project(fmt):
    pm = current_pm_profile()
    projects = _scoped_projects(pm)
    if not projects:
        flash("You have no assigned projects to report yet.", "warning")
        return redirect(url_for("pm_extra.reports_index"))
    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_project_summary_pdf(projects)
            return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                              download_name="my_projects_report.pdf")
        elif fmt == "excel":
            buffer = excel_reports.build_project_summary_excel(projects)
            return send_file(
                buffer,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True, download_name="my_projects_report.xlsx",
            )
    except Exception:
        current_app.logger.exception("Failed to generate PM project report (%s).", fmt)
        flash("Could not generate the report due to a system error. Please try again.", "danger")
        return redirect(url_for("pm_extra.reports_index"))
    flash("Unknown report format requested.", "danger")
    return redirect(url_for("pm_extra.reports_index"))


@pm_extra_bp.route("/reports/intern-performance/<fmt>")
@login_required
@roles_required("Project Manager")
def report_intern_performance(fmt):
    pm = current_pm_profile()
    interns = _scoped_interns(pm)
    intern_ids = {i.id for i in interns}

    intern_id = request.args.get("intern_id")
    if not intern_id:
        flash("Please select an intern to generate a performance report.", "danger")
        return redirect(url_for("pm_extra.reports_index"))

    intern = Intern.query.get_or_404(intern_id)
    if intern.id not in intern_ids:
        flash("You can only generate reports for interns assigned to you.", "danger")
        return redirect(url_for("pm_extra.reports_index"))

    attendance_records = Attendance.query.filter_by(intern_id=intern.id).all()
    total_attendance = len(attendance_records)
    present_count = sum(1 for r in attendance_records if r.status in Attendance.ATTENDED_STATUSES)
    attendance_percentage = (
        round((present_count / total_attendance) * 100, 1) if total_attendance else 0
    )
    evaluations = (
        Evaluation.query.filter_by(intern_id=intern.id).order_by(Evaluation.created_at.desc()).all()
    )
    submissions = (
        ProjectSubmission.query.filter_by(intern_id=intern.id)
        .order_by(ProjectSubmission.submitted_at.desc()).all()
    )

    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_intern_progress_pdf(
                intern, attendance_percentage, evaluations, submissions
            )
            return send_file(
                buffer, mimetype="application/pdf", as_attachment=True,
                download_name=f"intern_performance_{intern.full_name.replace(' ', '_')}.pdf",
            )
        elif fmt == "excel":
            buffer = excel_reports.build_intern_progress_excel(
                intern, attendance_percentage, evaluations, submissions
            )
            return send_file(
                buffer,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name=f"intern_performance_{intern.full_name.replace(' ', '_')}.xlsx",
            )
    except Exception:
        current_app.logger.exception(
            "Failed to generate PM intern performance report (%s) for intern #%s.", fmt, intern.id
        )
        flash("Could not generate the report due to a system error. Please try again.", "danger")
        return redirect(url_for("pm_extra.reports_index"))
    flash("Unknown report format requested.", "danger")
    return redirect(url_for("pm_extra.reports_index"))


# ========================================================================
# ANALYTICS
# ========================================================================
@pm_extra_bp.route("/analytics")
@login_required
@roles_required("Project Manager")
def analytics():
    pm = current_pm_profile()
    if pm is None:
        flash("Your Project Manager profile could not be found.", "danger")
        return redirect(url_for("dashboard.index"))

    projects = _scoped_projects(pm)
    project_ids = [p.id for p in projects]
    interns = _scoped_interns(pm, projects)
    intern_ids = [i.id for i in interns]

    # Assigned Projects Status
    status_rows = {}
    for p in projects:
        status_rows[p.status] = status_rows.get(p.status, 0) + 1
    project_status = [{"label": s, "count": status_rows.get(s, 0)} for s in Project.STATUSES]

    # Assigned Intern Performance: average Evaluation percentage per intern
    intern_performance = []
    for intern in interns:
        evals = Evaluation.query.filter_by(intern_id=intern.id).all()
        avg = round(sum(e.percentage for e in evals) / len(evals), 1) if evals else 0
        intern_performance.append({"label": intern.full_name, "count": avg})

    # Attendance Percentage (overall, scoped)
    att_rows = dict(
        db.session.query(Attendance.status, func.count(Attendance.id))
        .filter(Attendance.intern_id.in_(intern_ids)).group_by(Attendance.status).all()
    ) if intern_ids else {}
    attendance_breakdown = [{"label": s, "count": att_rows.get(s, 0)} for s in Attendance.STATUSES]
    total_att = sum(att_rows.values())
    present_att = sum(att_rows.get(s, 0) for s in Attendance.ATTENDED_STATUSES)
    attendance_percentage = round((present_att / total_att) * 100, 1) if total_att else 0

    # Submission Rate: submitted projects vs total assigned
    submitted_or_further = sum(
        1 for p in projects if p.status in ("Submitted", "Approved", "Completed", "Rejected")
    )
    submission_rate = round((submitted_or_further / len(projects)) * 100, 1) if projects else 0

    # Deadline Compliance: completed on/before deadline vs completed late
    on_time, late = 0, 0
    for p in projects:
        if p.status in ("Completed", "Approved"):
            if p.updated_at and p.updated_at.date() <= p.deadline:
                on_time += 1
            else:
                late += 1
    deadline_compliance = [{"label": "On Time", "count": on_time}, {"label": "Late", "count": late}]

    # Evaluation Statistics: PM evaluation forms finalized vs pending
    finalized = PMEvaluation.query.filter_by(project_manager_id=pm.id, is_finalized=True).count()
    drafts = PMEvaluation.query.filter_by(project_manager_id=pm.id, is_finalized=False).count()
    evaluation_stats = [{"label": "Finalized", "count": finalized}, {"label": "Draft", "count": drafts}]

    return render_template(
        "pm_extra/analytics.html",
        project_status=project_status,
        intern_performance=intern_performance,
        attendance_breakdown=attendance_breakdown,
        attendance_percentage=attendance_percentage,
        submission_rate=submission_rate,
        deadline_compliance=deadline_compliance,
        evaluation_stats=evaluation_stats,
        projects_count=len(projects),
        interns_count=len(interns),
    )


# ========================================================================
# CALENDAR
# ========================================================================
@pm_extra_bp.route("/calendar")
@login_required
@roles_required("Project Manager")
def calendar():
    pm = current_pm_profile()
    if pm is None:
        flash("Your Project Manager profile could not be found.", "danger")
        return redirect(url_for("dashboard.index"))

    projects = _scoped_projects(pm)
    interns = _scoped_interns(pm, projects)
    intern_ids = [i.id for i in interns]

    events = []
    for p in projects:
        events.append({
            "date": p.deadline.isoformat(),
            "type": "Project Deadline",
            "title": p.title,
            "detail": f"{p.department.name} · Status: {p.status}",
            "color": "danger" if p.is_overdue() else "primary",
            "url": url_for("project.view_project", project_id=p.id),
        })

    submissions = (
        ProjectSubmission.query.filter(ProjectSubmission.project_id.in_([p.id for p in projects]))
        .all() if projects else []
    )
    for s in submissions:
        events.append({
            "date": s.submitted_at.date().isoformat(),
            "type": "Submission",
            "title": f"{s.intern.full_name} submitted {s.project.title}",
            "detail": f"PM review: {s.pm_status}",
            "color": "info",
            "url": url_for("project.view_project", project_id=s.project_id),
        })

    pm_evals = PMEvaluation.query.filter_by(project_manager_id=pm.id).all()
    for e in pm_evals:
        events.append({
            "date": e.evaluation_date.isoformat(),
            "type": "Evaluation",
            "title": f"Evaluation: {e.intern.full_name}",
            "detail": "Finalized" if e.is_finalized else "Draft",
            "color": "secondary",
            "url": url_for("pm_evaluation.view_evaluation", evaluation_id=e.id),
        })

    # Project milestones due for any of this PM's projects.
    project_ids = [p.id for p in projects]
    milestones = (
        ProjectMilestone.query.filter(ProjectMilestone.project_id.in_(project_ids)).all()
        if project_ids else []
    )
    for m in milestones:
        events.append({
            "date": m.due_date.isoformat(),
            "type": "Milestone",
            "title": m.title,
            "detail": f"{m.project.title} · {m.status}",
            "color": "success" if m.status == "Completed" else ("danger" if m.is_overdue else "info"),
            "url": url_for("pm_workspace.milestones", project_id=m.project_id),
        })

    # Intern rotations (start dates) for this PM's currently/previously
    # assigned interns.
    rotations = (
        InternRotation.query.filter(InternRotation.to_manager_id == pm.id).all()
    )
    for r in rotations:
        events.append({
            "date": r.start_date.isoformat(),
            "type": "Rotation",
            "title": f"{r.intern.full_name} rotated in",
            "detail": f"{r.to_department.name} · {r.reason}",
            "color": "secondary",
            "url": url_for("pm_workspace.rotation_history"),
        })

    # Submission deadlines: upcoming project deadlines already covered
    # above as "Project Deadline"; here we surface actual submission
    # events distinctly (kept from the existing implementation below).
    leaves = (
        Leave.query.filter(Leave.intern_id.in_(intern_ids)).all() if intern_ids else []
    )
    for lv in leaves:
        events.append({
            "date": lv.start_date.isoformat(),
            "type": "Leave",
            "title": f"{lv.intern.full_name} on leave",
            "detail": f"{lv.start_date.strftime('%d %b')} – {lv.end_date.strftime('%d %b')} · {lv.status}",
            "color": "warning",
            "url": url_for("leave.manage_leaves"),
        })

    today = today_pkt()

    return render_template(
        "pm_extra/calendar.html",
        events=events,
        today=today,
    )
