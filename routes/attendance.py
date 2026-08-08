"""
routes/attendance.py
---------------------
Attendance Module: an enterprise time clock. Interns clock themselves
in once per day (date/time taken from the server, never user input).
Project Managers have view-only access to attendance records and
reports; only a Super Admin may correct a record. HR can view
attendance across every intern and generate filtered reports.
"""

from datetime import datetime
from utils import today_pkt

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user

from extensions import db
from models import Attendance, Intern, Department, SubDepartment
from utils import roles_required, log_action, hr_city_scope
from services.email_service import send_attendance_alert_email

attendance_bp = Blueprint("attendance", __name__, url_prefix="/attendance")


def _parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_time(value: str):
    return datetime.strptime(value, "%H:%M").time()


# ----------------------------------------------------------------------
# Super Admin only: edit an existing attendance record.
# HR and Project Managers have view-only access to attendance history --
# only a Super Admin may correct a previously marked record.
# ----------------------------------------------------------------------
@attendance_bp.route("/edit/<int:attendance_id>", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def edit_attendance(attendance_id):
    """Edit an existing attendance record. Restricted to Super Admin."""
    record = Attendance.query.get_or_404(attendance_id)

    if record.is_leave_managed:
        flash(
            "This record was auto-generated from an approved leave request and can only be "
            "changed by rejecting or cancelling that leave in Leave Management.",
            "danger",
        )
        return redirect(url_for("attendance.list_attendance"))

    if request.method == "POST":
        time_raw = request.form.get("time", "")
        time_out_raw = request.form.get("time_out", "").strip()
        status = request.form.get("status", "Present")
        remarks = request.form.get("remarks", "").strip()

        errors = []
        time_required = status in Attendance.TIME_REQUIRED_STATUSES
        if time_required and not time_raw:
            errors.append("Time is required.")
        if status not in Attendance.STATUSES:
            errors.append("Invalid attendance status.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("attendance/edit.html", record=record, statuses=Attendance.STATUSES)

        try:
            record.time = _parse_time(time_raw) if (time_required and time_raw) else None
            record.time_out = _parse_time(time_out_raw) if (time_required and time_out_raw) else None
            record.status = status
            record.remarks = remarks
            log_action(
                action="UPDATE",
                description=f"Updated attendance record for intern '{record.intern.full_name}' on {record.date}.",
                target_type="Attendance",
                target_id=record.id,
            )
            db.session.commit()
            flash("Attendance record updated successfully.", "success")
            if status in ("Absent", "Late"):
                send_attendance_alert_email(record.intern, status, record.date, remarks=remarks)
            return redirect(url_for("attendance.list_attendance"))
        except ValueError:
            db.session.rollback()
            flash("Invalid time format.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update attendance #%s.", attendance_id)
            flash("Could not update the attendance record due to a system error. Please try again.", "danger")

    return render_template("attendance/edit.html", record=record, statuses=Attendance.STATUSES)


# ----------------------------------------------------------------------
# Shared listing: HR, Project Manager and Super Admin all get a
# view-only listing of every intern's attendance -- only a Super Admin
# gets Edit access, granted separately in the template. Attendance is
# now intern self-service (Clock In only), so PMs no longer have a
# "records I marked" subset to restrict to.
# ----------------------------------------------------------------------
@attendance_bp.route("/")
@login_required
@roles_required("Station HR", "Project Manager", "Super Admin")
def list_attendance():
    """Bootstrap table of attendance records with filters (intern,
    department, status, date range)."""

    query = Attendance.query
    is_city_scoped, hr_city = hr_city_scope()
    if is_city_scoped:
        query = query.join(Intern).filter(Intern.station == hr_city)

    # ---- Year filter (defaults to the current year when not specified) ----
    from sqlalchemy import extract

    available_years = sorted(
        {
            y[0]
            for y in db.session.query(extract("year", Attendance.date)).distinct().all()
            if y[0] is not None
        },
        reverse=True,
    )
    year_raw = request.args.get("year", "")
    if year_raw == "":
        year = today_pkt().year
    else:
        try:
            year = int(year_raw)
        except ValueError:
            year = today_pkt().year
    if year:
        query = query.filter(extract("year", Attendance.date) == year)

    # ---- Filters (available to both roles) ----
    intern_id = request.args.get("intern_id")
    status = request.args.get("status")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    department_id = request.args.get("department_id")
    sub_department_id = request.args.get("sub_department_id")

    if intern_id:
        query = query.filter(Attendance.intern_id == intern_id)
    if status:
        query = query.filter(Attendance.status == status)
    if date_from:
        try:
            query = query.filter(Attendance.date >= _parse_date(date_from))
        except ValueError:
            flash("Invalid 'from' date supplied; filter ignored.", "warning")
    if date_to:
        try:
            query = query.filter(Attendance.date <= _parse_date(date_to))
        except ValueError:
            flash("Invalid 'to' date supplied; filter ignored.", "warning")
    if department_id:
        if is_city_scoped:
            query = query.filter(Intern.department_id == department_id)
        else:
            query = query.join(Intern).filter(Intern.department_id == department_id)
    if sub_department_id:
        if is_city_scoped or department_id:
            query = query.filter(Intern.sub_department_id == sub_department_id)
        else:
            query = query.join(Intern).filter(Intern.sub_department_id == sub_department_id)

    records = query.order_by(Attendance.date.desc(), Attendance.time.desc()).all()

    interns = Intern.query.order_by(db.func.lower(Intern.full_name)).all()
    departments = Department.query.order_by(db.func.lower(Department.name)).all()
    sub_departments = SubDepartment.query.order_by(db.func.lower(SubDepartment.name)).all()
    if is_city_scoped:
        interns = [i for i in interns if i.station == hr_city]
        departments = [d for d in departments if d.city == hr_city]

    # Reflect the (possibly defaulted) year back into the filters dict
    # the template renders from, so the Year dropdown shows the correct
    # selection even on a first visit with no ?year= in the URL.
    effective_filters = request.args.to_dict()
    effective_filters["year"] = str(year)

    return render_template(
        "attendance/list.html",
        records=records,
        interns=interns,
        departments=departments,
        sub_departments=sub_departments,
        statuses=Attendance.STATUSES,
        filters=effective_filters,
        available_years=available_years,
        selected_year=year,
        today=today_pkt(),
    )


# ----------------------------------------------------------------------
# HR-only: attendance report with summary counts per intern
# ----------------------------------------------------------------------
@attendance_bp.route("/report")
@login_required
@roles_required("Station HR", "Project Manager", "Super Admin")
def attendance_report():
    """Aggregate attendance report: total Present/Absent/Leave/Late per
    intern, with the same filter set as the listing page."""

    intern_id = request.args.get("intern_id")
    department_id = request.args.get("department_id")
    sub_department_id = request.args.get("sub_department_id")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    is_city_scoped, hr_city = hr_city_scope()

    from sqlalchemy import extract

    available_years = sorted(
        {
            y[0]
            for y in db.session.query(extract("year", Attendance.date)).distinct().all()
            if y[0] is not None
        },
        reverse=True,
    )
    year_raw = request.args.get("year", "")
    if year_raw == "":
        year = today_pkt().year
    else:
        try:
            year = int(year_raw)
        except ValueError:
            year = today_pkt().year

    interns_query = Intern.query
    if is_city_scoped:
        interns_query = interns_query.filter(Intern.station == hr_city)
    if department_id:
        interns_query = interns_query.filter(Intern.department_id == department_id)
    if sub_department_id:
        interns_query = interns_query.filter(Intern.sub_department_id == sub_department_id)
    if intern_id:
        interns_query = interns_query.filter(Intern.id == intern_id)

    interns = interns_query.order_by(db.func.lower(Intern.full_name)).all()

    report_rows = []
    for intern in interns:
        att_query = Attendance.query.filter_by(intern_id=intern.id)
        if year:
            att_query = att_query.filter(extract("year", Attendance.date) == year)
        if date_from:
            try:
                att_query = att_query.filter(Attendance.date >= _parse_date(date_from))
            except ValueError:
                pass
        if date_to:
            try:
                att_query = att_query.filter(Attendance.date <= _parse_date(date_to))
            except ValueError:
                pass

        records = att_query.all()
        summary = {status: 0 for status in Attendance.STATUSES}
        for r in records:
            summary[r.status] = summary.get(r.status, 0) + 1

        report_rows.append(
            {
                "intern": intern,
                "total": len(records),
                "summary": summary,
            }
        )

    departments = Department.query.order_by(db.func.lower(Department.name)).all()
    sub_departments = SubDepartment.query.order_by(db.func.lower(SubDepartment.name)).all()
    all_interns = Intern.query.order_by(db.func.lower(Intern.full_name)).all()
    if is_city_scoped:
        departments = [d for d in departments if d.city == hr_city]
        all_interns = [i for i in all_interns if i.station == hr_city]

    effective_filters = request.args.to_dict()
    effective_filters["year"] = str(year)

    return render_template(
        "attendance/report.html",
        report_rows=report_rows,
        departments=departments,
        sub_departments=sub_departments,
        interns=all_interns,
        filters=effective_filters,
        available_years=available_years,
        selected_year=year,
        statuses=Attendance.STATUSES,
    )
