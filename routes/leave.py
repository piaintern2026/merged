"""
routes/leave.py
----------------
Leave Management.

  * Intern    - submit a leave request and track its status. No
                approval authority whatsoever.
  * Project Manager - the sole approver. Can view, approve, reject,
                and comment on leave requests for interns currently
                assigned to them (Intern.current_manager), and can
                reverse a decision (reject/cancel an Approved request).
  * HR     - visibility into every leave record and report. Approves
                or rejects a request only as a fallback, when the
                intern involved has no Project Manager currently
                assigned; otherwise no approve/reject/comment actions
                are exposed to HR.

Approving a leave automatically creates/updates Attendance rows for
every date in its range, marked "Leave" with no clock-in time.
Reversing an approval (reject or cancel after Approved) automatically
restores or removes those Attendance rows. See
services/leave_attendance.py for the sync/revert logic.
"""

from datetime import datetime
from utils import now_pkt

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user

from extensions import db
from models import Leave, Intern, Department, SubDepartment, Attendance
from utils import roles_required, current_intern_profile, current_pm_profile, notify_user, log_action
from services.leave_attendance import sync_attendance_for_leave, revert_attendance_for_leave
from services.email_service import send_leave_status_email

leave_bp = Blueprint("leave", __name__, url_prefix="/leave")


def _parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def _get_manageable_leave_or_none(leave_id, pm):
    """Fetch a leave request, returning None if it doesn't exist or
    the current PM isn't its assigned approver."""
    leave_request = Leave.query.get(leave_id)
    if leave_request is None:
        return None
    if not leave_request.is_manageable_by(pm):
        return None
    return leave_request


def _get_hr_manageable_leave_or_none(leave_id):
    """Fetch a leave request for the HR fallback flow, returning None
    if it doesn't exist or the intern currently has a PM assigned
    (in which case approval authority belongs solely to that PM)."""
    leave_request = Leave.query.get(leave_id)
    if leave_request is None:
        return None
    if not leave_request.is_manageable_by_hr():
        return None
    return leave_request


def _leave_action_redirect():
    """PMs land back on their Leave Management screen; HR (acting on
    the fallback flow) lands back on the read-mostly Leave Records
    screen where the fallback actions are exposed."""
    return url_for("leave.manage_leaves") if current_user.role == "Project Manager" else url_for("leave.leave_records")


# ----------------------------------------------------------------------
# Intern: submit a leave request and view own requests
# ----------------------------------------------------------------------
@leave_bp.route("/", methods=["GET", "POST"])
@login_required
@roles_required("Intern")
def my_leaves():
    """Submit a new leave request and list the intern's own requests
    along with their current status (Pending, Approved, Rejected,
    Cancelled). Interns can only submit and track -- no review
    actions live under this endpoint."""
    intern = current_intern_profile()
    if intern is None:
        flash("Your Intern profile could not be found. Contact HR.", "danger")
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        leave_type = request.form.get("leave_type", "")
        start_raw = request.form.get("start_date", "")
        end_raw = request.form.get("end_date", "")
        reason = request.form.get("reason", "").strip()

        errors = []
        if Leave.used_count_for(intern.id) >= Leave.MAX_LEAVES:
            errors.append(
                f"You have used all {Leave.MAX_LEAVES} leaves allowed during your internship."
            )
        if leave_type not in Leave.LEAVE_TYPES:
            errors.append("Please select a valid leave type.")
        if not start_raw or not end_raw:
            errors.append("Start and end dates are required.")
        if not reason:
            errors.append("Please provide a reason for the leave.")

        start_date = end_date = None
        if not errors:
            try:
                start_date = _parse_date(start_raw)
                end_date = _parse_date(end_raw)
                if end_date < start_date:
                    errors.append("End date cannot be before the start date.")
            except ValueError:
                errors.append("Invalid date format.")

        # Business rule: if attendance has already been marked for this
        # intern on any date within the requested range, the leave
        # request must be rejected outright -- nothing is saved. This is
        # enforced here in the backend (not just hidden/disabled in the
        # UI) so it can't be bypassed by submitting the form directly.
        if not errors and start_date and end_date:
            conflicting_attendance = (
                Attendance.query.filter(
                    Attendance.intern_id == intern.id,
                    Attendance.date >= start_date,
                    Attendance.date <= end_date,
                )
                .first()
            )
            if conflicting_attendance is not None:
                errors.append(
                    "Attendance has already been marked for this date. "
                    "Leave request cannot be submitted."
                )

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            leave_request = Leave(
                intern_id=intern.id,
                leave_type=leave_type,
                start_date=start_date,
                end_date=end_date,
                reason=reason,
            )
            db.session.add(leave_request)
            log_action(
                action="Leave Requested",
                description=f"{intern.full_name} requested {leave_type} leave "
                f"({start_date} to {end_date}).",
                target_type="Leave",
            )

            pm = intern.current_manager
            if pm is not None:
                notify_user(
                    user_id=pm.user_id,
                    message=f"{intern.full_name} submitted a {leave_type} leave request "
                    f"({start_date} to {end_date}) awaiting your review.",
                    icon="bi-calendar2-week",
                    notification_type="General",
                )

            db.session.commit()
            send_leave_status_email(leave_request, "submitted")
            flash("Leave request submitted successfully.", "success")
            return redirect(url_for("leave.my_leaves"))

    from sqlalchemy import extract
    from utils import today_pkt

    available_years = sorted(
        {
            y[0]
            for y in db.session.query(extract("year", Leave.created_at))
            .filter(Leave.intern_id == intern.id)
            .distinct()
            .all()
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

    leaves_query = Leave.query.filter_by(intern_id=intern.id)
    if year:
        leaves_query = leaves_query.filter(extract("year", Leave.created_at) == year)

    leaves = leaves_query.order_by(Leave.created_at.desc()).all()

    leaves_used = Leave.used_count_for(intern.id)

    return render_template(
        "leave/my_leaves.html",
        leaves=leaves,
        leave_types=Leave.LEAVE_TYPES,
        available_years=available_years,
        selected_year=year,
        filters=request.args,
        leaves_used=leaves_used,
        leaves_max=Leave.MAX_LEAVES,
        leaves_remaining=max(Leave.MAX_LEAVES - leaves_used, 0),
    )


# ----------------------------------------------------------------------
# Project Manager: Leave Management -- review requests for assigned interns
# ----------------------------------------------------------------------
@leave_bp.route("/manage")
@login_required
@roles_required("Project Manager")
def manage_leaves():
    """List leave requests only for interns currently assigned to this
    Project Manager, with an optional status filter. This is the only
    place leave requests can be approved, rejected, or commented on."""
    pm = current_pm_profile()
    if pm is None:
        flash("Your Project Manager profile could not be found.", "danger")
        return redirect(url_for("dashboard.index"))

    status = request.args.get("status", "")

    from sqlalchemy import extract
    from utils import today_pkt

    # Interns whose current rotation manager is this PM.
    assigned_intern_ids = [
        intern.id for intern in Intern.query.all()
        if intern.current_manager and intern.current_manager.id == pm.id
    ]

    if assigned_intern_ids:
        base_query = Leave.query.filter(Leave.intern_id.in_(assigned_intern_ids))
    else:
        base_query = Leave.query.filter(db.false())

    available_years = sorted(
        {
            y[0]
            for y in db.session.query(extract("year", Leave.created_at))
            .filter(Leave.intern_id.in_(assigned_intern_ids) if assigned_intern_ids else db.false())
            .distinct()
            .all()
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

    query = base_query
    if year:
        query = query.filter(extract("year", Leave.created_at) == year)

    if status:
        query = query.filter_by(status=status)

    leaves = query.order_by(
        db.case((Leave.status == "Pending", 0), else_=1), Leave.created_at.desc()
    ).all()

    pending_count = (
        Leave.query.filter(
            Leave.intern_id.in_(assigned_intern_ids), Leave.status == "Pending"
        ).count()
        if assigned_intern_ids
        else 0
    )

    effective_filters = request.args.to_dict()
    effective_filters["year"] = str(year)

    return render_template(
        "leave/manage.html",
        leaves=leaves,
        statuses=Leave.STATUSES,
        filters=effective_filters,
        available_years=available_years,
        selected_year=year,
        pending_count=pending_count,
        can_manage=True,
    )


# ----------------------------------------------------------------------
# HR: leave records, with fallback approve/reject when no PM is assigned
# ----------------------------------------------------------------------
@leave_bp.route("/records")
@login_required
@roles_required("Station HR", "Admin")
def leave_records():
    """View of every leave request across the organisation, with the
    same filters as the PM's Leave Management screen. HR has
    visibility for reporting purposes, plus fallback approve/reject
    authority on any request belonging to an intern who currently has
    no assigned Project Manager. As soon as a PM is assigned to that
    intern, approval reverts entirely to the PM and no action buttons
    are shown here for that request."""
    status = request.args.get("status", "")
    department_id = request.args.get("department_id", "")
    sub_department_id = request.args.get("sub_department_id", "")

    from sqlalchemy import extract
    from utils import today_pkt

    available_years = sorted(
        {
            y[0]
            for y in db.session.query(extract("year", Leave.created_at)).distinct().all()
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

    query = Leave.query
    if year:
        query = query.filter(extract("year", Leave.created_at) == year)
    if status:
        query = query.filter_by(status=status)
    if department_id:
        query = query.join(Intern).filter(Intern.department_id == department_id)
    if sub_department_id:
        if not department_id:
            query = query.join(Intern)
        query = query.filter(Intern.sub_department_id == sub_department_id)

    leaves = query.order_by(
        db.case((Leave.status == "Pending", 0), else_=1), Leave.created_at.desc()
    ).all()

    pending_count = Leave.query.filter_by(status="Pending").count()
    departments = Department.query.order_by(db.func.lower(Department.name)).all()
    sub_departments = SubDepartment.query.order_by(db.func.lower(SubDepartment.name)).all()

    effective_filters = request.args.to_dict()
    effective_filters["year"] = str(year)

    return render_template(
        "leave/records.html",
        leaves=leaves,
        statuses=Leave.STATUSES,
        departments=departments,
        sub_departments=sub_departments,
        filters=effective_filters,
        available_years=available_years,
        selected_year=year,
        pending_count=pending_count,
        can_manage=False,
    )


@leave_bp.route("/<int:leave_id>/approve", methods=["POST"])
@login_required
@roles_required("Project Manager", "Station HR", "Admin")
def approve_leave(leave_id):
    """Approve a pending leave request, and auto-mark attendance as
    Leave for every date in its range. Normally only the intern's
    assigned Project Manager may approve; if the intern currently has
    no assigned PM, HR may approve instead (fallback authority)."""
    if current_user.role == "Project Manager":
        pm = current_pm_profile()
        leave_request = _get_manageable_leave_or_none(leave_id, pm)
    else:
        leave_request = _get_hr_manageable_leave_or_none(leave_id)

    if leave_request is None:
        flash("You are not authorized to review that leave request.", "danger")
        return redirect(_leave_action_redirect())

    if leave_request.status != "Pending":
        flash("Only pending leave requests can be approved.", "danger")
        return redirect(_leave_action_redirect())

    remarks = request.form.get("review_remarks", "").strip()

    try:
        leave_request.status = "Approved"
        leave_request.reviewed_by_id = current_user.id
        leave_request.reviewed_at = now_pkt()
        if remarks:
            leave_request.review_remarks = remarks

        sync_attendance_for_leave(leave_request)

        notify_user(
            user_id=leave_request.intern.user_id,
            message=f"Your {leave_request.leave_type} leave request "
            f"({leave_request.start_date} to {leave_request.end_date}) has been approved.",
            icon="bi-calendar-check",
            notification_type="General",
        )
        log_action(
            action="Leave Approved",
            description=f"Approved leave request #{leave_request.id} for "
            f"{leave_request.intern.full_name}"
            + (" (HR fallback -- no PM assigned)" if current_user.role != "Project Manager" else "")
            + "; attendance marked as Leave.",
            target_type="Leave",
            target_id=leave_request.id,
        )
        db.session.commit()
        send_leave_status_email(leave_request, "approved", reviewer_name=current_user.display_name())
        flash("Leave request approved and attendance updated.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to approve leave request #%s.", leave_request.id
        )
        flash("Could not approve the leave request due to a system error. Please try again.", "danger")

    return redirect(_leave_action_redirect())


@leave_bp.route("/<int:leave_id>/reject", methods=["POST"])
@login_required
@roles_required("Project Manager", "Station HR", "Admin")
def reject_leave(leave_id):
    """Reject a pending leave request, or reverse a previously Approved
    one. In the latter case, any auto-created attendance for its date
    range is restored/removed. Normally only the intern's assigned
    Project Manager may reject; if the intern currently has no
    assigned PM, HR may reject instead (fallback authority)."""
    if current_user.role == "Project Manager":
        pm = current_pm_profile()
        leave_request = _get_manageable_leave_or_none(leave_id, pm)
    else:
        leave_request = _get_hr_manageable_leave_or_none(leave_id)

    if leave_request is None:
        flash("You are not authorized to review that leave request.", "danger")
        return redirect(_leave_action_redirect())

    if leave_request.status not in ("Pending", "Approved"):
        flash("Only pending or approved leave requests can be rejected.", "danger")
        return redirect(_leave_action_redirect())

    remarks = request.form.get("review_remarks", "").strip()
    was_approved = leave_request.status == "Approved"

    try:
        if was_approved:
            revert_attendance_for_leave(leave_request)

        leave_request.status = "Rejected"
        leave_request.reviewed_by_id = current_user.id
        leave_request.reviewed_at = now_pkt()
        leave_request.review_remarks = remarks or leave_request.review_remarks

        notify_user(
            user_id=leave_request.intern.user_id,
            message=f"Your {leave_request.leave_type} leave request "
            f"({leave_request.start_date} to {leave_request.end_date}) has been rejected.",
            icon="bi-calendar-x",
            notification_type="General",
        )
        log_action(
            action="Leave Rejected",
            description=(
                f"Rejected previously approved leave request #{leave_request.id} for "
                f"{leave_request.intern.full_name}; attendance reverted."
                if was_approved
                else f"Rejected leave request #{leave_request.id} for {leave_request.intern.full_name}."
            ),
            target_type="Leave",
            target_id=leave_request.id,
        )
        db.session.commit()
        send_leave_status_email(leave_request, "rejected", reviewer_name=current_user.display_name())
        flash("Leave request rejected.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to reject leave request #%s.", leave_request.id
        )
        flash("Could not reject the leave request due to a system error. Please try again.", "danger")

    return redirect(_leave_action_redirect())


@leave_bp.route("/<int:leave_id>/cancel", methods=["POST"])
@login_required
@roles_required("Project Manager")
def cancel_leave(leave_id):
    """Cancel a previously Approved leave request, restoring/removing
    the attendance entries it created."""
    pm = current_pm_profile()
    leave_request = _get_manageable_leave_or_none(leave_id, pm)
    if leave_request is None:
        flash("You are not authorized to review that leave request.", "danger")
        return redirect(url_for("leave.manage_leaves"))

    if leave_request.status != "Approved":
        flash("Only approved leave requests can be cancelled.", "danger")
        return redirect(url_for("leave.manage_leaves"))

    remarks = request.form.get("review_remarks", "").strip()

    try:
        revert_attendance_for_leave(leave_request)

        leave_request.status = "Cancelled"
        leave_request.reviewed_by_id = current_user.id
        leave_request.reviewed_at = now_pkt()
        leave_request.review_remarks = remarks or leave_request.review_remarks

        notify_user(
            user_id=leave_request.intern.user_id,
            message=f"Your approved {leave_request.leave_type} leave "
            f"({leave_request.start_date} to {leave_request.end_date}) has been cancelled.",
            icon="bi-calendar-x",
            notification_type="General",
        )
        log_action(
            action="Leave Cancelled",
            description=f"Cancelled previously approved leave request #{leave_request.id} for "
            f"{leave_request.intern.full_name}; attendance reverted.",
            target_type="Leave",
            target_id=leave_request.id,
        )
        db.session.commit()
        send_leave_status_email(leave_request, "cancelled", reviewer_name=current_user.display_name())
        flash("Leave request cancelled and attendance reverted.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to cancel leave request #%s.", leave_request.id
        )
        flash("Could not cancel the leave request due to a system error. Please try again.", "danger")

    return redirect(url_for("leave.manage_leaves"))


@leave_bp.route("/<int:leave_id>/comment", methods=["POST"])
@login_required
@roles_required("Project Manager")
def comment_leave(leave_id):
    """Add or update a comment on a leave request without changing its
    status. Only the assigned PM may comment."""
    pm = current_pm_profile()
    leave_request = _get_manageable_leave_or_none(leave_id, pm)
    if leave_request is None:
        flash("You are not authorized to comment on that leave request.", "danger")
        return redirect(url_for("leave.manage_leaves"))

    remarks = request.form.get("review_remarks", "").strip()
    if not remarks:
        flash("Please enter a comment.", "danger")
        return redirect(url_for("leave.manage_leaves"))

    leave_request.review_remarks = remarks
    try:
        log_action(
            action="Leave Commented",
            description=f"Commented on leave request #{leave_request.id} for "
            f"{leave_request.intern.full_name}.",
            target_type="Leave",
            target_id=leave_request.id,
        )
        db.session.commit()
        flash("Comment saved.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to save comment on leave request #%s.", leave_id
        )
        flash("Could not save the comment due to a system error. Please try again.", "danger")
    return redirect(url_for("leave.manage_leaves"))
