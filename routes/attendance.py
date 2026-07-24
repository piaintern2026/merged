"""
routes/attendance.py
---------------------
Attendance Module: Project Managers mark daily attendance for the
interns assigned to their projects. HR can view attendance across
every intern and generate filtered reports.
"""

from datetime import datetime, date
from utils import now_pkt, today_pkt

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Attendance, Intern, Department
from utils import roles_required, current_pm_profile, log_action

attendance_bp = Blueprint("attendance", __name__, url_prefix="/attendance")


def _parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_time(value: str):
    return datetime.strptime(value, "%H:%M").time()


def _interns_for_pm(pm):
    """Return the list of interns a Project Manager is allowed to mark
    attendance for.

    Previously this only returned interns who were assigned to one of
    the PM's projects, which meant an intern with no project assignment
    could never have their attendance marked. Attendance tracking is
    independent of project assignment, so every active intern should be
    eligible, regardless of whether they currently have a project."""
    return (
        Intern.query.join(Intern.user)
        .filter_by(is_active_account=True)
        .order_by(Intern.full_name)
        .all()
    )


# ----------------------------------------------------------------------
# Project Manager: mark attendance
# ----------------------------------------------------------------------
@attendance_bp.route("/mark", methods=["GET", "POST"])
@login_required
@roles_required("Project Manager")
def mark_attendance():
    """Mark attendance for an intern assigned to the current PM."""
    pm = current_pm_profile()
    if pm is None:
        flash("Your Project Manager profile could not be found.", "danger")
        return redirect(url_for("dashboard.index"))

    interns = _interns_for_pm(pm)

    if request.method == "POST":
        intern_id = request.form.get("intern_id")
        date_raw = request.form.get("date", "")
        time_raw = request.form.get("time", "")
        status = request.form.get("status", "Present")
        remarks = request.form.get("remarks", "").strip()

        errors = []
        if not intern_id:
            errors.append("Please select an intern.")
        elif int(intern_id) not in [i.id for i in interns]:
            errors.append("You may only mark attendance for interns assigned to you.")
        if not date_raw:
            errors.append("Date is required.")
        # Absent/Leave have no clock-in -- only Present/Late require a time.
        time_required = status in Attendance.TIME_REQUIRED_STATUSES
        if time_required and not time_raw:
            errors.append("Time is required.")
        if status not in Attendance.STATUSES:
            errors.append("Invalid attendance status.")

        record_date = record_time = None
        if not errors:
            try:
                record_date = _parse_date(date_raw)
                record_time = _parse_time(time_raw) if (time_required and time_raw) else None
            except ValueError:
                errors.append("Invalid date or time format.")

        # Re-check for an existing row right before creating one. Explicitly
        # scoped to this exact intern_id + date pair -- the same pair the
        # unique constraint on Attendance enforces -- so a record for a
        # different intern or a different date can never be mistaken for
        # "already marked" here. This is a *fresh* query (not something
        # decided earlier in the request), so a record created by another
        # action moments ago is still caught, and a day with nothing
        # recorded yet always falls through to record creation below.
        if not errors and record_date:
            existing = Attendance.query.filter(
                Attendance.intern_id == int(intern_id),
                Attendance.date == record_date,
            ).first()
            if existing is not None:
                if existing.is_leave_managed:
                    # Not a manually-marked record at all -- it was
                    # auto-created by an approved Leave request. Don't
                    # report this as "already marked" (nothing was marked
                    # by a PM), point them at the correct workflow instead.
                    errors.append(
                        "This intern is on approved Leave for this date. To mark them "
                        "Absent instead, reject/cancel the Leave request first."
                    )
                else:
                    # A genuine Present/Absent/Late record already exists.
                    errors.append(
                        "Attendance is already marked for this intern on this date. "
                        "Edit it from the attendance list instead."
                    )

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "attendance/mark.html",
                interns=interns,
                statuses=Attendance.STATUSES,
                form=request.form,
            )

        try:
            record = Attendance(
                intern_id=int(intern_id),
                marked_by_id=pm.id,
                date=record_date,
                time=record_time,
                status=status,
                remarks=remarks,
            )
            db.session.add(record)
            db.session.flush()
            log_action(
                action="CREATE",
                description=f"Marked attendance ({status}) for intern #{intern_id} on {record_date}.",
                target_type="Attendance",
                target_id=record.id,
            )
            db.session.commit()
            flash("Attendance recorded successfully.", "success")
            return redirect(url_for("attendance.mark_attendance"))
        except IntegrityError:
            db.session.rollback()
            flash("Attendance for this intern/date already exists.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to mark attendance for intern #%s.", intern_id)
            flash("Could not record attendance due to a system error. Please try again.", "danger")

    return render_template(
        "attendance/mark.html", interns=interns, statuses=Attendance.STATUSES, form=None
    )


# ----------------------------------------------------------------------
# Project Manager: clock out an intern already clocked in today
# ----------------------------------------------------------------------
@attendance_bp.route("/clock-out/<int:attendance_id>", methods=["POST"])
@login_required
@roles_required("Project Manager")
def clock_out(attendance_id):
    """Record the clock-out time for an attendance entry the current PM
    marked earlier today. Mirrors the same-day, once-only rule used for
    clocking in: a record can only be clocked out once."""
    record = Attendance.query.get_or_404(attendance_id)
    pm = current_pm_profile()

    if pm is None or record.marked_by_id != pm.id:
        flash("You can only clock out attendance records you created.", "danger")
        return redirect(url_for("attendance.list_attendance"))

    if record.date != today_pkt():
        flash("You can only clock out an attendance record for today.", "danger")
        return redirect(url_for("attendance.list_attendance"))

    if record.status not in Attendance.TIME_REQUIRED_STATUSES:
        flash("Absent/Leave records have no clock-in and cannot be clocked out.", "danger")
        return redirect(url_for("attendance.list_attendance"))

    if record.time_out is not None:
        flash("This intern has already been clocked out today.", "danger")
        return redirect(url_for("attendance.list_attendance"))

    try:
        record.time_out = now_pkt().time()
        db.session.commit()
        flash("Clock-out time recorded successfully.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to clock out attendance #%s.", attendance_id)
        flash("Could not record the clock-out time due to a system error. Please try again.", "danger")
    return redirect(url_for("attendance.list_attendance"))


# ----------------------------------------------------------------------
# Project Manager: edit their own attendance entry
# ----------------------------------------------------------------------
@attendance_bp.route("/edit/<int:attendance_id>", methods=["GET", "POST"])
@login_required
@roles_required("Project Manager")
def edit_attendance(attendance_id):
    """Edit an attendance record the current PM previously marked."""
    record = Attendance.query.get_or_404(attendance_id)
    pm = current_pm_profile()

    if pm is None or record.marked_by_id != pm.id:
        flash("You can only edit attendance records you created.", "danger")
        return redirect(url_for("attendance.list_attendance"))

    if record.is_leave_managed:
        flash(
            "This record was auto-generated from an approved leave request and can only be "
            "changed by rejecting or cancelling that leave in Leave Management.",
            "danger",
        )
        return redirect(url_for("attendance.list_attendance"))

    interns = _interns_for_pm(pm)

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
# Shared listing: HR sees everyone, PM sees only records they marked
# ----------------------------------------------------------------------
@attendance_bp.route("/")
@login_required
@roles_required("HR", "Project Manager")
def list_attendance():
    """Bootstrap table of attendance records with filters (intern,
    department, status, date range)."""

    query = Attendance.query

    if current_user.role == "Project Manager":
        pm = current_pm_profile()
        if pm is None:
            flash("Your Project Manager profile could not be found.", "danger")
            return redirect(url_for("dashboard.index"))
        query = query.filter_by(marked_by_id=pm.id)

    # ---- Filters (available to both roles) ----
    intern_id = request.args.get("intern_id")
    status = request.args.get("status")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    department_id = request.args.get("department_id")

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
        query = query.join(Intern).filter(Intern.department_id == department_id)

    records = query.order_by(Attendance.date.desc(), Attendance.time.desc()).all()

    interns = Intern.query.order_by(Intern.full_name).all()
    departments = Department.query.order_by(Department.name).all()

    return render_template(
        "attendance/list.html",
        records=records,
        interns=interns,
        departments=departments,
        statuses=Attendance.STATUSES,
        filters=request.args,
        today=today_pkt(),
    )


# ----------------------------------------------------------------------
# HR-only: attendance report with summary counts per intern
# ----------------------------------------------------------------------
@attendance_bp.route("/report")
@login_required
@roles_required("HR")
def attendance_report():
    """Aggregate attendance report: total Present/Absent/Leave/Late per
    intern, with the same filter set as the listing page."""

    intern_id = request.args.get("intern_id")
    department_id = request.args.get("department_id")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    interns_query = Intern.query
    if department_id:
        interns_query = interns_query.filter(Intern.department_id == department_id)
    if intern_id:
        interns_query = interns_query.filter(Intern.id == intern_id)

    interns = interns_query.order_by(Intern.full_name).all()

    report_rows = []
    for intern in interns:
        att_query = Attendance.query.filter_by(intern_id=intern.id)
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

    departments = Department.query.order_by(Department.name).all()
    all_interns = Intern.query.order_by(Intern.full_name).all()

    return render_template(
        "attendance/report.html",
        report_rows=report_rows,
        departments=departments,
        interns=all_interns,
        filters=request.args,
        statuses=Attendance.STATUSES,
    )
