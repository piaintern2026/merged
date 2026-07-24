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
  * HR     - read-only visibility into every leave record and
                report. No approve/reject/comment actions are exposed
                to HR anywhere in this blueprint.

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
from models import Leave, Intern, Department
from utils import roles_required, current_intern_profile, current_pm_profile, notify_user, log_action
from services.leave_attendance import sync_attendance_for_leave, revert_attendance_for_leave

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
            flash("Leave request submitted successfully.", "success")
            return redirect(url_for("leave.my_leaves"))

    leaves = (
        Leave.query.filter_by(intern_id=intern.id)
        .order_by(Leave.created_at.desc())
        .all()
    )

    return render_template(
        "leave/my_leaves.html",
        leaves=leaves,
        leave_types=Leave.LEAVE_TYPES,
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

    # Interns whose current rotation manager is this PM.
    assigned_intern_ids = [
        intern.id for intern in Intern.query.all()
        if intern.current_manager and intern.current_manager.id == pm.id
    ]

    if assigned_intern_ids:
        query = Leave.query.filter(Leave.intern_id.in_(assigned_intern_ids))
    else:
        query = Leave.query.filter(db.false())

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

    return render_template(
        "leave/manage.html",
        leaves=leaves,
        statuses=Leave.STATUSES,
        filters=request.args,
        pending_count=pending_count,
        can_manage=True,
    )


# ----------------------------------------------------------------------
# HR: read-only leave records (no approve/reject/comment)
# ----------------------------------------------------------------------
@leave_bp.route("/records")
@login_required
@roles_required("HR")
def leave_records():
    """Read-only view of every leave request across the organisation,
    with the same filters as the PM's Leave Management screen. HR
    has visibility for reporting purposes only -- no action buttons
    are rendered for this role."""
    status = request.args.get("status", "")
    department_id = request.args.get("department_id", "")

    query = Leave.query
    if status:
        query = query.filter_by(status=status)
    if department_id:
        query = query.join(Intern).filter(Intern.department_id == department_id)

    leaves = query.order_by(
        db.case((Leave.status == "Pending", 0), else_=1), Leave.created_at.desc()
    ).all()

    pending_count = Leave.query.filter_by(status="Pending").count()
    departments = Department.query.order_by(Department.name).all()

    return render_template(
        "leave/records.html",
        leaves=leaves,
        statuses=Leave.STATUSES,
        departments=departments,
        filters=request.args,
        pending_count=pending_count,
        can_manage=False,
    )


@leave_bp.route("/<int:leave_id>/approve", methods=["POST"])
@login_required
@roles_required("Project Manager")
def approve_leave(leave_id):
    """Approve a pending leave request for an intern assigned to the
    current PM, and auto-mark attendance as Leave for every date in
    its range."""
    pm = current_pm_profile()
    leave_request = _get_manageable_leave_or_none(leave_id, pm)
    if leave_request is None:
        flash("You are not authorized to review that leave request.", "danger")
        return redirect(url_for("leave.manage_leaves"))

    if leave_request.status != "Pending":
        flash("Only pending leave requests can be approved.", "danger")
        return redirect(url_for("leave.manage_leaves"))

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
            f"{leave_request.intern.full_name}; attendance marked as Leave.",
            target_type="Leave",
            target_id=leave_request.id,
        )
        db.session.commit()
        flash("Leave request approved and attendance updated.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to approve leave request #%s.", leave_request.id
        )
        flash("Could not approve the leave request due to a system error. Please try again.", "danger")

    return redirect(url_for("leave.manage_leaves"))


@leave_bp.route("/<int:leave_id>/reject", methods=["POST"])
@login_required
@roles_required("Project Manager")
def reject_leave(leave_id):
    """Reject a pending leave request, or reverse a previously Approved
    one. In the latter case, any auto-created attendance for its date
    range is restored/removed."""
    pm = current_pm_profile()
    leave_request = _get_manageable_leave_or_none(leave_id, pm)
    if leave_request is None:
        flash("You are not authorized to review that leave request.", "danger")
        return redirect(url_for("leave.manage_leaves"))

    if leave_request.status not in ("Pending", "Approved"):
        flash("Only pending or approved leave requests can be rejected.", "danger")
        return redirect(url_for("leave.manage_leaves"))

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
        flash("Leave request rejected.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to reject leave request #%s.", leave_request.id
        )
        flash("Could not reject the leave request due to a system error. Please try again.", "danger")

    return redirect(url_for("leave.manage_leaves"))


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
