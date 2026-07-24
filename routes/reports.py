"""
routes/reports.py
------------------
Module 4: Reports. HR can generate five report types (Attendance,
Evaluation, Intern Progress, Department Summary, Project Summary),
each downloadable as PDF (ReportLab) or Excel (OpenPyXL). All routes
are thin: they gather data with SQLAlchemy and delegate the actual
document building to services/pdf_reports.py and services/excel_reports.py.
"""

from flask import Blueprint, render_template, send_file, flash, redirect, url_for, request, current_app
from flask_login import login_required

from extensions import db
from sqlalchemy import func

from models import Attendance, Evaluation, Intern, Department, Project, DailyWorkLog, ProjectSubmission
from utils import roles_required, PIA_CITIES
from services import pdf_reports, excel_reports

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _city_department_matrix() -> dict:
    """Build the dynamic City x Department Matrix: columns = PIA cities,
    rows = departments, cell = number of interns in that department who
    are based in that city. Computed fresh from the database on every
    call (a single grouped query, no caching, no hardcoded numbers), so
    it automatically reflects any intern that has been added, edited,
    deleted, transferred, or rotated by the time this is called.
    """
    departments = Department.query.order_by(Department.name).all()

    # One grouped query: (department_name, city, count) for every
    # department/city combination that actually has interns.
    rows = (
        db.session.query(Department.name, Intern.city, func.count(Intern.id))
        .join(Intern, Intern.department_id == Department.id)
        .group_by(Department.name, Intern.city)
        .all()
    )
    counts = {}
    for dept_name, city, count in rows:
        counts[(dept_name, city)] = count

    matrix = []
    city_totals = {city: 0 for city in PIA_CITIES}
    grand_total = 0
    for dept in departments:
        row_cells = []
        row_total = 0
        for city in PIA_CITIES:
            value = counts.get((dept.name, city), 0)
            row_cells.append(value)
            row_total += value
            city_totals[city] += value
        matrix.append({"department": dept.name, "cells": row_cells, "row_total": row_total})
        grand_total += row_total

    return {
        "cities": PIA_CITIES,
        "matrix": matrix,
        "city_totals": [city_totals[c] for c in PIA_CITIES],
        "grand_total": grand_total,
    }


def _department_summary_rows() -> list:
    """Build the per-department aggregate rows shared by both the PDF
    and Excel Department Summary reports."""
    rows = []
    for dept in Department.query.order_by(Department.name).all():
        intern_ids = [i.id for i in dept.interns]
        evaluations = (
            Evaluation.query.filter(Evaluation.intern_id.in_(intern_ids)).all()
            if intern_ids
            else []
        )
        avg_score = (
            round(sum(e.percentage for e in evaluations) / len(evaluations), 1)
            if evaluations
            else None
        )
        rows.append(
            {
                "name": dept.name,
                "status": dept.status,
                "pm_count": len(dept.project_managers),
                "intern_count": len(dept.interns),
                "project_count": len(dept.projects),
                "avg_score": avg_score,
            }
        )
    return rows


# ----------------------------------------------------------------------
# Landing page
# ----------------------------------------------------------------------
@reports_bp.route("/")
@login_required
@roles_required("HR")
def index():
    """Report center: pick a report type and download format."""
    interns = Intern.query.order_by(Intern.full_name).all()
    city_dept_matrix = _city_department_matrix()
    return render_template("reports/index.html", interns=interns, city_dept_matrix=city_dept_matrix)


# ----------------------------------------------------------------------
# 1. Attendance Report
# ----------------------------------------------------------------------
@reports_bp.route("/attendance/<fmt>")
@login_required
@roles_required("HR")
def attendance_report(fmt):
    records = Attendance.query.order_by(Attendance.date.desc()).all()
    if not records:
        flash("There is no attendance data to report yet.", "warning")
        return redirect(url_for("reports.index"))

    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_attendance_pdf(records)
            return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                              download_name="attendance_report.pdf")
        elif fmt == "excel":
            buffer = excel_reports.build_attendance_excel(records)
            return send_file(
                buffer,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name="attendance_report.xlsx",
            )
    except Exception:
        current_app.logger.exception("Failed to generate attendance report (%s).", fmt)
        flash("Could not generate the report due to a system error. Please try again.", "danger")
        return redirect(url_for("reports.index"))
    flash("Unknown report format requested.", "danger")
    return redirect(url_for("reports.index"))


# ----------------------------------------------------------------------
# 2. Evaluation Report
# ----------------------------------------------------------------------
@reports_bp.route("/evaluation/<fmt>")
@login_required
@roles_required("HR")
def evaluation_report(fmt):
    evaluations = Evaluation.query.order_by(Evaluation.created_at.desc()).all()
    if not evaluations:
        flash("There are no evaluations to report yet.", "warning")
        return redirect(url_for("reports.index"))

    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_evaluation_pdf(evaluations)
            return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                              download_name="evaluation_report.pdf")
        elif fmt == "excel":
            buffer = excel_reports.build_evaluation_excel(evaluations)
            return send_file(
                buffer,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name="evaluation_report.xlsx",
            )
    except Exception:
        current_app.logger.exception("Failed to generate evaluation report (%s).", fmt)
        flash("Could not generate the report due to a system error. Please try again.", "danger")
        return redirect(url_for("reports.index"))
    flash("Unknown report format requested.", "danger")
    return redirect(url_for("reports.index"))


# ----------------------------------------------------------------------
# 3. Intern Progress Report (requires ?intern_id=)
# ----------------------------------------------------------------------
@reports_bp.route("/intern-progress/<fmt>")
@login_required
@roles_required("HR")
def intern_progress_report(fmt):
    intern_id = request.args.get("intern_id")
    if not intern_id:
        flash("Please select an intern to generate a progress report.", "danger")
        return redirect(url_for("reports.index"))

    intern = Intern.query.get_or_404(intern_id)

    attendance_records = Attendance.query.filter_by(intern_id=intern.id).all()
    total_attendance = len(attendance_records)
    present_count = sum(1 for r in attendance_records if r.status == "Present")
    attendance_percentage = (
        round((present_count / total_attendance) * 100, 1) if total_attendance else 0
    )

    work_logs = (
        DailyWorkLog.query.filter_by(intern_id=intern.id)
        .order_by(DailyWorkLog.log_date.desc())
        .limit(15)
        .all()
    )
    evaluations = (
        Evaluation.query.filter_by(intern_id=intern.id).order_by(Evaluation.created_at.desc()).all()
    )
    submissions = (
        ProjectSubmission.query.filter_by(intern_id=intern.id)
        .order_by(ProjectSubmission.submitted_at.desc())
        .all()
    )

    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_intern_progress_pdf(
                intern, attendance_percentage, work_logs, evaluations, submissions
            )
            return send_file(
                buffer, mimetype="application/pdf", as_attachment=True,
                download_name=f"intern_progress_{intern.full_name.replace(' ', '_')}.pdf",
            )
        elif fmt == "excel":
            buffer = excel_reports.build_intern_progress_excel(
                intern, attendance_percentage, work_logs, evaluations, submissions
            )
            return send_file(
                buffer,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name=f"intern_progress_{intern.full_name.replace(' ', '_')}.xlsx",
            )
    except Exception:
        current_app.logger.exception(
            "Failed to generate intern progress report (%s) for intern #%s.", fmt, intern.id
        )
        flash("Could not generate the report due to a system error. Please try again.", "danger")
        return redirect(url_for("reports.index"))
    flash("Unknown report format requested.", "danger")
    return redirect(url_for("reports.index"))


# ----------------------------------------------------------------------
# 4. Department Summary Report
# ----------------------------------------------------------------------
@reports_bp.route("/department-summary/<fmt>")
@login_required
@roles_required("HR")
def department_summary_report(fmt):
    rows = _department_summary_rows()
    if not rows:
        flash("There are no departments to report yet.", "warning")
        return redirect(url_for("reports.index"))

    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_department_summary_pdf(rows)
            return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                              download_name="department_summary_report.pdf")
        elif fmt == "excel":
            buffer = excel_reports.build_department_summary_excel(rows)
            return send_file(
                buffer,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name="department_summary_report.xlsx",
            )
    except Exception:
        current_app.logger.exception("Failed to generate department summary report (%s).", fmt)
        flash("Could not generate the report due to a system error. Please try again.", "danger")
        return redirect(url_for("reports.index"))
    flash("Unknown report format requested.", "danger")
    return redirect(url_for("reports.index"))


# ----------------------------------------------------------------------
# 5. Project Summary Report
# ----------------------------------------------------------------------
@reports_bp.route("/project-summary/<fmt>")
@login_required
@roles_required("HR")
def project_summary_report(fmt):
    projects = Project.query.order_by(Project.deadline.asc()).all()
    if not projects:
        flash("There are no projects to report yet.", "warning")
        return redirect(url_for("reports.index"))

    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_project_summary_pdf(projects)
            return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                              download_name="project_summary_report.pdf")
        elif fmt == "excel":
            buffer = excel_reports.build_project_summary_excel(projects)
            return send_file(
                buffer,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name="project_summary_report.xlsx",
            )
    except Exception:
        current_app.logger.exception("Failed to generate project summary report (%s).", fmt)
        flash("Could not generate the report due to a system error. Please try again.", "danger")
        return redirect(url_for("reports.index"))
    flash("Unknown report format requested.", "danger")
    return redirect(url_for("reports.index"))


# ----------------------------------------------------------------------
# 6. Station x Department Report (City x Department Matrix)
# ----------------------------------------------------------------------
@reports_bp.route("/station-department/<fmt>")
@login_required
@roles_required("HR")
def station_department_report(fmt):
    """Downloadable PDF/Excel version of the City x Department Matrix
    shown on the Reports index page. Uses the exact same data-building
    function as the on-page table, so the export always matches what
    is currently displayed."""
    matrix_data = _city_department_matrix()
    if not matrix_data["matrix"]:
        flash("There are no departments to report yet.", "warning")
        return redirect(url_for("reports.index"))

    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_station_department_pdf(matrix_data)
            return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                              download_name="station_department_report.pdf")
        elif fmt == "excel":
            buffer = excel_reports.build_station_department_excel(matrix_data)
            return send_file(
                buffer,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name="station_department_report.xlsx",
            )
    except Exception:
        current_app.logger.exception("Failed to generate station/department report (%s).", fmt)
        flash("Could not generate the report due to a system error. Please try again.", "danger")
        return redirect(url_for("reports.index"))
    flash("Unknown report format requested.", "danger")
    return redirect(url_for("reports.index"))
