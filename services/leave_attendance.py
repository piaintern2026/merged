"""
services/leave_attendance.py
------------------------------
Keeps the Attendance table in sync with the lifecycle of an approved
Leave request:

  * approve_leave -> sync_attendance_for_leave() creates or overwrites
    one Attendance row per day in the leave's date range, marking it
    "Leave" (no clock-in time). If a row already existed for that
    intern/date (e.g. the PM had already marked Present that day), its
    prior state is snapshotted onto the row so it can be restored.

  * reject_leave (on a previously Approved request) / cancel_leave ->
    revert_attendance_for_leave() undoes exactly that: rows this leave
    created are deleted, rows it overwrote are restored to their
    snapshotted prior state.

Kept as a small, dependency-free module (no new commits triggered
here -- callers are expected to commit as part of their own
transaction) so the leave routes stay the single place that decides
when to persist.
"""

from datetime import timedelta

from extensions import db
from models import Attendance


def _dates_in_range(start_date, end_date):
    days = (end_date - start_date).days
    for offset in range(days + 1):
        yield start_date + timedelta(days=offset)


def sync_attendance_for_leave(leave) -> None:
    """Create/update one Attendance row per day of an Approved leave,
    marking each as "Leave" with no clock-in time. Safe to call only
    once per approval; existing state is snapshotted so it can be
    restored later. Uses flush() (not commit()) so the caller's
    surrounding transaction stays atomic with the Leave status change.
    """
    pm = leave.assigned_pm
    if pm is None:
        # No assigned PM to attribute the attendance to -- nothing we
        # can safely auto-create; the approval itself is still valid.
        return

    for day in _dates_in_range(leave.start_date, leave.end_date):
        existing = Attendance.query.filter_by(
            intern_id=leave.intern_id, date=day
        ).first()

        if existing is None:
            record = Attendance(
                intern_id=leave.intern_id,
                marked_by_id=pm.id,
                date=day,
                time=None,
                time_out=None,
                status="Leave",
                remarks=f"Auto-marked from approved leave #{leave.id}.",
                source_leave_id=leave.id,
                pre_leave_status=None,
                pre_leave_time=None,
                pre_leave_time_out=None,
                pre_leave_remarks=None,
            )
            db.session.add(record)
        elif existing.source_leave_id != leave.id:
            # Overwriting a manually-marked (or differently-sourced)
            # record -- snapshot its current state first so it can be
            # restored if this leave is later reversed.
            existing.pre_leave_status = existing.status
            existing.pre_leave_time = existing.time
            existing.pre_leave_time_out = existing.time_out
            existing.pre_leave_remarks = existing.remarks

            existing.status = "Leave"
            existing.time = None
            existing.time_out = None
            existing.remarks = f"Auto-marked from approved leave #{leave.id}."
            existing.source_leave_id = leave.id
            existing.marked_by_id = pm.id
        # else: already synced for this exact leave -- nothing to do.

    db.session.flush()


def revert_attendance_for_leave(leave) -> None:
    """Undo sync_attendance_for_leave(): restore or remove every
    Attendance row this leave previously created/overwrote. Called
    when a previously Approved leave is Rejected or Cancelled.
    """
    records = Attendance.query.filter_by(source_leave_id=leave.id).all()

    for record in records:
        if record.pre_leave_status is not None:
            # Restore the state that existed before this leave touched it.
            record.status = record.pre_leave_status
            record.time = record.pre_leave_time
            record.time_out = record.pre_leave_time_out
            record.remarks = record.pre_leave_remarks
            record.source_leave_id = None
            record.pre_leave_status = None
            record.pre_leave_time = None
            record.pre_leave_time_out = None
            record.pre_leave_remarks = None
        else:
            # This row didn't exist before the leave created it.
            db.session.delete(record)

    db.session.flush()
