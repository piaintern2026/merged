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

from models import Attendance, Evaluation, Intern, Department, Project, ProjectSubmission, InternRotation
from utils import roles_required, PIA_CITIES, hr_city_scope
from services import pdf_reports, excel_reports

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _city_department_matrix(cities=None) -> dict:
    """Build the dynamic City x Department Matrix: columns = organization cities,
    rows = departments, cell = number of interns in that department who
    are based in that city. Computed fresh from the database on every
    call (a single grouped query, no caching, no hardcoded numbers), so
    it automatically reflects any intern that has been added, edited,
    deleted, transferred, or rotated by the time this is called.

    `cities` optionally restricts the matrix to a subset of cities
    (used to scope a Station HR to their own assigned city only); when
    omitted, every PIA city is included (Admin's unrestricted view).
    """
    cities = cities or PIA_CITIES
    departments = Department.query.order_by(db.func.lower(Department.name)).all()

    # One grouped query: (department_name, city, count) for every
    # department/city combination that actually has interns.
    rows = (
        db.session.query(db.func.lower(Department.name), Intern.station, func.count(Intern.id))
        .join(Intern, Intern.department_id == Department.id)
        .group_by(db.func.lower(Department.name), Intern.station)
        .all()
    )
    counts = {}
    for dept_name, city, count in rows:
        counts[(dept_name, city)] = count

    matrix = []
    city_totals = {city: 0 for city in cities}
    grand_total = 0
    for dept in departments:
        row_cells = []
        row_total = 0
        for city in cities:
            value = counts.get((dept.name, city), 0)
            row_cells.append(value)
            row_total += value
            city_totals[city] += value
        if row_total == 0 and cities != PIA_CITIES:
            # City-scoped view: skip departments with zero presence in
            # the Station HR's city so the matrix stays relevant to them.
            continue
        matrix.append({"department": dept.name, "cells": row_cells, "row_total": row_total})
        grand_total += row_total

    return {
        "cities": cities,
        "matrix": matrix,
        "city_totals": [city_totals[c] for c in cities],
        "grand_total": grand_total,
    }


def _department_summary_rows(cities=None) -> list:
    """Build the per-department aggregate rows shared by both the PDF
    and Excel Department Summary reports. `cities`, when given, limits
    the rows to departments based in one of those cities (Station HR
    scoping); Admin passes None for every department.

    Each department row also carries a `sub_departments` list with the
    same shape (name/intern_count/project_count) broken down per
    Division/Section, so both the on-screen table and the PDF/Excel
    exports can show the Department -> Division/Section breakdown
    instead of a department-only total.
    """
    dept_query = Department.query.order_by(db.func.lower(Department.name))
    if cities:
        dept_query = dept_query.filter(Department.city.in_(cities))
    rows = []
    for dept in dept_query.all():
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

        sub_dept_rows = []
        for sub in sorted(dept.sub_departments, key=lambda s: s.name):
            sub_interns = [i for i in dept.interns if i.sub_department_id == sub.id]
            sub_projects = [p for p in dept.projects if p.sub_department_id == sub.id]
            sub_dept_rows.append(
                {
                    "name": sub.name,
                    "intern_count": len(sub_interns),
                    "project_count": len(sub_projects),
                }
            )

        # Interns/projects attached to the department but with no
        # Division/Section set (legacy rows or department-only records)
        # are surfaced as their own "Unassigned" bucket so the totals
        # in the breakdown always add up to the department total.
        unassigned_interns = [i for i in dept.interns if not i.sub_department_id]
        unassigned_projects = [p for p in dept.projects if not p.sub_department_id]
        if unassigned_interns or unassigned_projects:
            sub_dept_rows.append(
                {
                    "name": "Unassigned",
                    "intern_count": len(unassigned_interns),
                    "project_count": len(unassigned_projects),
                }
            )

        rows.append(
            {
                "name": dept.name,
                "city": dept.city,
                "pm_count": len(dept.project_managers),
                "intern_count": len(dept.interns),
                "project_count": len(dept.projects),
                "avg_score": avg_score,
                "sub_departments": sub_dept_rows,
            }
        )
    return rows


# ----------------------------------------------------------------------
# Landing page
# ----------------------------------------------------------------------
@reports_bp.route("/")
@login_required
@roles_required("Station HR", "Admin")
def index():
    """Report center: pick a report type and download format. A Station
    HR sees data scoped to their assigned city; Admin sees
    everything with no restriction."""
    is_city_scoped, hr_city = hr_city_scope()
    interns = Intern.query.order_by(db.func.lower(Intern.full_name)).all()
    if is_city_scoped:
        interns = [i for i in interns if i.station == hr_city]
    city_dept_matrix = _city_department_matrix([hr_city] if is_city_scoped and hr_city else None)
    return render_template(
        "reports/index.html", interns=interns, city_dept_matrix=city_dept_matrix,
        is_city_scoped=is_city_scoped, hr_city=hr_city,
    )


# ----------------------------------------------------------------------
# 1. Attendance Report
# ----------------------------------------------------------------------
@reports_bp.route("/attendance/<fmt>")
@login_required
@roles_required("Station HR", "Admin")
def attendance_report(fmt):
    is_city_scoped, hr_city = hr_city_scope()
    records_query = Attendance.query.order_by(Attendance.date.desc())
    if is_city_scoped:
        records_query = records_query.join(Intern).filter(Intern.station == hr_city)
    records = records_query.all()
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
@roles_required("Station HR", "Admin")
def evaluation_report(fmt):
    is_city_scoped, hr_city = hr_city_scope()
    evaluations_query = Evaluation.query.order_by(Evaluation.created_at.desc())
    if is_city_scoped:
        evaluations_query = evaluations_query.join(Intern).filter(Intern.station == hr_city)
    evaluations = evaluations_query.all()
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
@roles_required("Station HR", "Admin")
def intern_progress_report(fmt):
    intern_id = request.args.get("intern_id")
    if not intern_id:
        flash("Please select an intern to generate a progress report.", "danger")
        return redirect(url_for("reports.index"))

    intern = Intern.query.get_or_404(intern_id)
    is_city_scoped, hr_city = hr_city_scope()
    if is_city_scoped and intern.station != hr_city:
        flash("You do not have permission to report on interns outside your assigned city.", "danger")
        return redirect(url_for("reports.index"))

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
        .order_by(ProjectSubmission.submitted_at.desc())
        .all()
    )

    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_intern_progress_pdf(
                intern, attendance_percentage, evaluations, submissions
            )
            return send_file(
                buffer, mimetype="application/pdf", as_attachment=True,
                download_name=f"intern_progress_{intern.full_name.replace(' ', '_')}.pdf",
            )
        elif fmt == "excel":
            buffer = excel_reports.build_intern_progress_excel(
                intern, attendance_percentage, evaluations, submissions
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
@roles_required("Station HR", "Admin")
def department_summary_report(fmt):
    is_city_scoped, hr_city = hr_city_scope()
    rows = _department_summary_rows([hr_city] if is_city_scoped and hr_city else None)
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
@roles_required("Station HR", "Admin")
def project_summary_report(fmt):
    is_city_scoped, hr_city = hr_city_scope()
    projects_query = Project.query.order_by(Project.deadline.asc())
    if is_city_scoped:
        projects_query = projects_query.join(Department).filter(Department.city == hr_city)
    projects = projects_query.all()
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
@roles_required("Station HR", "Admin")
def station_department_report(fmt):
    """Downloadable PDF/Excel version of the City x Department Matrix
    shown on the Reports index page. Uses the exact same data-building
    function as the on-page table, so the export always matches what
    is currently displayed."""
    is_city_scoped, hr_city = hr_city_scope()
    matrix_data = _city_department_matrix([hr_city] if is_city_scoped and hr_city else None)
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


# ----------------------------------------------------------------------
# 7. Rotation Report (org-wide)
# ----------------------------------------------------------------------
@reports_bp.route("/rotation/<fmt>")
@login_required
@roles_required("Station HR", "Admin")
def rotation_report(fmt):
    is_city_scoped, hr_city = hr_city_scope()
    rotations_query = InternRotation.query.order_by(
        InternRotation.start_date.desc(), InternRotation.id.desc()
    )
    if is_city_scoped:
        rotations_query = rotations_query.join(Intern).filter(Intern.station == hr_city)
    rotations = rotations_query.all()
    if not rotations:
        flash("There are no rotations to report yet.", "warning")
        return redirect(url_for("reports.index"))

    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_rotation_report_pdf(rotations)
            return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                              download_name="rotation_report.pdf")
        elif fmt == "excel":
            buffer = excel_reports.build_rotation_report_excel(rotations)
            return send_file(
                buffer,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name="rotation_report.xlsx",
            )
    except Exception:
        current_app.logger.exception("Failed to generate rotation report (%s).", fmt)
        flash("Could not generate the report due to a system error. Please try again.", "danger")
        return redirect(url_for("reports.index"))
    flash("Unknown report format requested.", "danger")
    return redirect(url_for("reports.index"))


# ----------------------------------------------------------------------
# 8. Internship Report (org-wide, one row per intern)
# ----------------------------------------------------------------------
@reports_bp.route("/internship/<fmt>")
@login_required
@roles_required("Station HR", "Admin")
def internship_report(fmt):
    is_city_scoped, hr_city = hr_city_scope()
    interns_query = Intern.query.order_by(db.func.lower(Intern.full_name))
    if is_city_scoped:
        interns_query = interns_query.filter(Intern.station == hr_city)
    interns = interns_query.all()
    if not interns:
        flash("There are no interns to report yet.", "warning")
        return redirect(url_for("reports.index"))

    rows = []
    for intern in interns:
        attendance_records = Attendance.query.filter_by(intern_id=intern.id).all()
        total_attendance = len(attendance_records)
        present_count = sum(1 for r in attendance_records if r.status in Attendance.ATTENDED_STATUSES)
        attendance_pct = round((present_count / total_attendance) * 100, 1) if total_attendance else 0

        from utils import today_pkt
        today = today_pkt()
        total_days = max((intern.internship_end_date - intern.internship_start_date).days, 1)
        elapsed_days = max((min(today, intern.internship_end_date) - intern.internship_start_date).days, 0)
        completion_pct = round(min(elapsed_days / total_days, 1.0) * 100, 1)
        days_remaining = max((intern.internship_end_date - today).days, 0)

        rows.append(
            {
                "intern": intern,
                "manager": intern.current_manager,
                "status": intern.effective_status,
                "completion_pct": completion_pct,
                "attendance_pct": attendance_pct,
                "days_remaining": days_remaining,
            }
        )

    try:
        if fmt == "pdf":
            buffer = pdf_reports.build_internship_report_pdf(rows)
            return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                              download_name="internship_report.pdf")
        elif fmt == "excel":
            buffer = excel_reports.build_internship_report_excel(rows)
            return send_file(
                buffer,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name="internship_report.xlsx",
            )
    except Exception:
        current_app.logger.exception("Failed to generate internship report (%s).", fmt)
        flash("Could not generate the report due to a system error. Please try again.", "danger")
        return redirect(url_for("reports.index"))
    flash("Unknown report format requested.", "danger")
    return redirect(url_for("reports.index"))
