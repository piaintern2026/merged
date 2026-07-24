"""
routes/rotation.py
-------------------
Enterprise feature: Intern Rotation Management.

- Rotate Intern: moves an intern to a new department/manager/project,
  auto-closes their previous open rotation stint, and creates a
  permanent InternRotation history record.
- Rotation History: browsable/filterable list of every rotation ever
  recorded.
- Intern Timeline: chronological view of one intern's rotations.
- Rotation Dashboard: department-wise time spent, projects completed,
  and total rotations.
- Final Internship Report: consolidated end-to-end report per intern
  (duration, departments served, managers worked under, projects
  completed, time per department, performance ratings, attendance
  summary, rotation history), viewable in-app and downloadable as PDF.
"""

from collections import defaultdict
from datetime import date, datetime
from utils import today_pkt

from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, current_app
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Intern, Department, ProjectManager, Project, InternRotation, Attendance, Evaluation
from utils import roles_required, log_action, notify_user
from services.email_service import send_rotation_email, send_hr_pm_notification_email, get_hr_recipients
from services import pdf_reports

rotation_bp = Blueprint("rotation", __name__, url_prefix="/rotations")


def _parse_date(value: str):
    """Parse an HTML date input (YYYY-MM-DD) into a date object."""
    return datetime.strptime(value, "%Y-%m-%d").date()


# ---------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------
def _department_segments(intern: Intern) -> list:
    """Build a chronological list of every department stint an intern has
    ever had (including the original assignment before their first
    rotation, if any), for use in timelines / final reports / dashboards.
    Each segment is a dict: department, manager, project, start, end,
    duration_days, is_current.
    """
    rotations = sorted(intern.rotations, key=lambda r: (r.start_date, r.id))
    today = today_pkt()
    segments = []

    if rotations:
        first = rotations[0]
        if intern.internship_start_date < first.start_date:
            end = first.start_date
            segments.append(
                {
                    "department": first.from_department,
                    "manager": first.from_manager,
                    "project": None,
                    "start": intern.internship_start_date,
                    "end": end,
                    "duration_days": max((end - intern.internship_start_date).days, 0),
                    "is_current": False,
                }
            )
        for r in rotations:
            end = r.end_date or (
                intern.internship_end_date if intern.internship_end_date < today else today
            )
            start = r.start_date
            duration = max((end - start).days, 0) if r.end_date else r.duration_days
            segments.append(
                {
                    "department": r.to_department,
                    "manager": r.to_manager,
                    "project": r.project,
                    "start": r.start_date,
                    "end": r.end_date,
                    "duration_days": r.duration_days,
                    "is_current": r.is_current,
                    "rotation": r,
                }
            )
    else:
        end = intern.internship_end_date if intern.internship_end_date < today else today
        segments.append(
            {
                "department": intern.department,
                "manager": None,
                "project": None,
                "start": intern.internship_start_date,
                "end": None,
                "duration_days": max((end - intern.internship_start_date).days + 1, 0),
                "is_current": True,
            }
        )
    return segments


def _current_manager(intern: Intern):
    return intern.current_manager


# ---------------------------------------------------------------------
# Rotation History
# ---------------------------------------------------------------------
@rotation_bp.route("/")
@login_required
@roles_required("HR")
def list_rotations():
    """Browsable, filterable log of every rotation ever recorded."""
    department_id = request.args.get("department_id", type=int)
    intern_id = request.args.get("intern_id", type=int)

    query = InternRotation.query
    if department_id:
        query = query.filter(
            db.or_(
                InternRotation.from_department_id == department_id,
                InternRotation.to_department_id == department_id,
            )
        )
    if intern_id:
        query = query.filter_by(intern_id=intern_id)

    rotations = query.order_by(InternRotation.start_date.desc(), InternRotation.id.desc()).all()

    return render_template(
        "rotations/list.html",
        rotations=rotations,
        departments=Department.query.order_by(Department.name).all(),
        interns=Intern.query.order_by(Intern.full_name).all(),
        selected_department=department_id,
        selected_intern=intern_id,
    )


# ---------------------------------------------------------------------
# Intern Timeline
# ---------------------------------------------------------------------
@rotation_bp.route("/timeline/<int:intern_id>")
@login_required
@roles_required("HR")
def timeline(intern_id):
    """Chronological department-rotation timeline for a single intern."""
    intern = Intern.query.get_or_404(intern_id)
    segments = _department_segments(intern)
    return render_template("rotations/timeline.html", intern=intern, segments=segments)


# ---------------------------------------------------------------------
# Rotate Intern
# ---------------------------------------------------------------------
@rotation_bp.route("/rotate/<int:intern_id>", methods=["GET", "POST"])
@login_required
@roles_required("HR")
def rotate_intern(intern_id):
    """Transfer an intern to a new department/manager/project, closing
    out their previous stint and creating a permanent history record."""
    intern = Intern.query.get_or_404(intern_id)
    departments = Department.query.filter_by(status="Active").order_by(Department.name).all()
    managers = ProjectManager.query.filter_by(is_active_flag=True).order_by(ProjectManager.full_name).all()
    projects = Project.query.order_by(Project.title).all()
    current_manager = _current_manager(intern)

    if request.method == "POST":
        to_department_id = request.form.get("to_department_id")
        to_manager_id = request.form.get("to_manager_id")
        project_id = request.form.get("project_id") or None
        start_date_raw = request.form.get("start_date", "")
        reason = request.form.get("reason", "").strip()
        remarks = request.form.get("remarks", "").strip()

        errors = []
        if not to_department_id:
            errors.append("Target department is required.")
        if not to_manager_id:
            errors.append("Target manager is required.")
        if not start_date_raw:
            errors.append("Rotation start date is required.")
        if not reason:
            errors.append("Reason for rotation is required.")

        start_date = None
        if start_date_raw and "Rotation start date is required." not in errors:
            try:
                start_date = _parse_date(start_date_raw)
            except ValueError:
                errors.append("Invalid start date format.")

        if start_date and start_date < intern.internship_start_date:
            errors.append("Rotation start date cannot be before the internship start date.")
        if start_date and intern.internship_end_date and start_date > intern.internship_end_date:
            errors.append("Rotation start date cannot be after the internship end date.")

        if (
            not errors
            and to_department_id
            and to_manager_id
            and int(to_department_id) == intern.department_id
            and current_manager
            and int(to_manager_id) == current_manager.id
        ):
            errors.append(
                "Selected department and manager match the intern's current assignment. "
                "Choose a different department or manager to rotate."
            )

        if to_manager_id and to_department_id and not errors:
            target_manager = ProjectManager.query.get(int(to_manager_id))
            if not target_manager or target_manager.department_id != int(to_department_id):
                errors.append("The selected manager does not belong to the target department.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "rotations/rotate_form.html",
                intern=intern,
                departments=departments,
                managers=managers,
                projects=projects,
                current_manager=current_manager,
                form=request.form,
            )

        try:
            previous_department_id = intern.department_id
            previous_manager = current_manager

            # Close out any still-open rotation stint as of the new start date.
            open_rotation = (
                InternRotation.query.filter_by(intern_id=intern.id, end_date=None)
                .order_by(InternRotation.start_date.desc())
                .first()
            )
            if open_rotation:
                open_rotation.end_date = start_date

            rotation = InternRotation(
                intern_id=intern.id,
                from_department_id=previous_department_id,
                to_department_id=int(to_department_id),
                from_manager_id=previous_manager.id if previous_manager else None,
                to_manager_id=int(to_manager_id),
                project_id=int(project_id) if project_id else None,
                start_date=start_date,
                end_date=None,
                reason=reason,
                remarks=remarks or None,
                rotated_by_id=current_user.id,
            )
            db.session.add(rotation)

            # Keep the intern's live "current department" column in sync.
            intern.department_id = int(to_department_id)

            db.session.flush()

            notify_user(
                user_id=intern.user_id,
                message=(
                    f"You have been rotated to {rotation.to_department.name} "
                    f"under {rotation.to_manager.full_name}."
                ),
                icon="bi-arrow-left-right",
                notification_type="General",
            )
            log_action(
                action="ROTATE",
                description=(
                    f"Rotated intern '{intern.full_name}' from "
                    f"{rotation.from_department.name if rotation.from_department else 'N/A'} "
                    f"to {rotation.to_department.name}."
                ),
                target_type="InternRotation",
                target_id=rotation.id,
            )
            db.session.commit()

            send_rotation_email(rotation)
            send_hr_pm_notification_email(
                recipients=get_hr_recipients(),
                recipient_name="HR Team",
                event_title="Intern Rotation Processed",
                event_message=f"Intern {intern.full_name} has been rotated to a new department.",
                details=[
                    ("Intern", intern.full_name),
                    ("From Department", rotation.from_department.name if rotation.from_department else "N/A"),
                    ("To Department", rotation.to_department.name),
                    ("New Manager", rotation.to_manager.full_name),
                    ("Effective From", rotation.start_date.strftime("%d %b %Y")),
                ],
            )

            flash(f"Intern '{intern.full_name}' rotated successfully.", "success")
            return redirect(url_for("intern.view_intern", intern_id=intern.id))

        except IntegrityError:
            db.session.rollback()
            flash("Could not process the rotation due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to rotate intern #%s.", intern.id)
            flash("Could not process the rotation due to a system error. Please try again.", "danger")

    return render_template(
        "rotations/rotate_form.html",
        intern=intern,
        departments=departments,
        managers=managers,
        projects=projects,
        current_manager=current_manager,
        form=None,
    )


# ---------------------------------------------------------------------
# Rotation Dashboard
# ---------------------------------------------------------------------
@rotation_bp.route("/dashboard")
@login_required
@roles_required("HR")
def dashboard():
    """Department-wise time spent, projects completed, and total
    rotations across all interns."""
    departments = Department.query.order_by(Department.name).all()
    all_rotations = InternRotation.query.all()

    dept_time = defaultdict(int)
    dept_rotation_count = defaultdict(int)
    dept_project_ids = defaultdict(set)

    for intern in Intern.query.all():
        for seg in _department_segments(intern):
            if seg["department"]:
                dept_time[seg["department"].id] += seg["duration_days"]
                if seg.get("project"):
                    dept_project_ids[seg["department"].id].add(seg["project"].id)

    for r in all_rotations:
        dept_rotation_count[r.to_department_id] += 1

    rows = []
    for dept in departments:
        rows.append(
            {
                "department": dept,
                "days_spent": dept_time.get(dept.id, 0),
                "projects_completed": len(dept_project_ids.get(dept.id, set())),
                "rotation_count": dept_rotation_count.get(dept.id, 0),
            }
        )
    rows.sort(key=lambda r: r["days_spent"], reverse=True)

    total_rotations = len(all_rotations)
    total_interns_rotated = len({r.intern_id for r in all_rotations})
    most_rotated = (
        max(
            (
                (Intern.query.get(iid), sum(1 for r in all_rotations if r.intern_id == iid))
                for iid in {r.intern_id for r in all_rotations}
            ),
            key=lambda pair: pair[1],
            default=(None, 0),
        )
    )

    chart_data = {
        "labels": [r["department"].name for r in rows],
        "days_spent": [r["days_spent"] for r in rows],
        "rotation_count": [r["rotation_count"] for r in rows],
    }

    return render_template(
        "rotations/dashboard.html",
        rows=rows,
        chart_data=chart_data,
        total_rotations=total_rotations,
        total_interns_rotated=total_interns_rotated,
        most_rotated_intern=most_rotated[0],
        most_rotated_count=most_rotated[1],
    )


# ---------------------------------------------------------------------
# Final Internship Report
# ---------------------------------------------------------------------
def _final_report_data(intern: Intern) -> dict:
    """Gather every data point required for the Final Internship Report."""
    segments = _department_segments(intern)

    today = today_pkt()
    end_for_duration = min(intern.internship_end_date, today) if intern.internship_end_date else today
    total_days = (end_for_duration - intern.internship_start_date).days + 1

    departments_served = []
    seen_dept_ids = set()
    for seg in segments:
        if seg["department"] and seg["department"].id not in seen_dept_ids:
            departments_served.append(seg["department"])
            seen_dept_ids.add(seg["department"].id)

    managers_worked_under = []
    seen_mgr_ids = set()
    for seg in segments:
        if seg["manager"] and seg["manager"].id not in seen_mgr_ids:
            managers_worked_under.append(seg["manager"])
            seen_mgr_ids.add(seg["manager"].id)

    dept_time = defaultdict(int)
    for seg in segments:
        if seg["department"]:
            dept_time[seg["department"].name] += seg["duration_days"]

    projects_completed = (
        Project.query.filter_by(assigned_intern_id=intern.id)
        .filter(Project.status.in_(["Completed", "Approved"]))
        .order_by(Project.deadline.asc())
        .all()
    )

    evaluations = (
        Evaluation.query.filter_by(intern_id=intern.id)
        .order_by(Evaluation.created_at.desc())
        .all()
    )
    avg_score_pct = (
        round(sum(e.percentage for e in evaluations) / len(evaluations), 1) if evaluations else None
    )

    attendance_records = Attendance.query.filter_by(intern_id=intern.id).all()
    total_attendance = len(attendance_records)
    present_count = sum(1 for r in attendance_records if r.status == "Present")
    absent_count = sum(1 for r in attendance_records if r.status == "Absent")
    leave_count = sum(1 for r in attendance_records if r.status == "Leave")
    late_count = sum(1 for r in attendance_records if r.status == "Late")
    attendance_percentage = (
        round((present_count / total_attendance) * 100, 1) if total_attendance else 0
    )

    rotations = sorted(intern.rotations, key=lambda r: r.start_date)

    return {
        "intern": intern,
        "total_days": total_days,
        "departments_served": departments_served,
        "managers_worked_under": managers_worked_under,
        "dept_time": dict(dept_time),
        "projects_completed": projects_completed,
        "evaluations": evaluations,
        "avg_score_pct": avg_score_pct,
        "attendance_percentage": attendance_percentage,
        "total_attendance": total_attendance,
        "present_count": present_count,
        "absent_count": absent_count,
        "leave_count": leave_count,
        "late_count": late_count,
        "rotations": rotations,
        "segments": segments,
    }


@rotation_bp.route("/final-report/<int:intern_id>")
@login_required
@roles_required("HR")
def final_report(intern_id):
    """View the consolidated Final Internship Report in-app."""
    intern = Intern.query.get_or_404(intern_id)
    data = _final_report_data(intern)
    return render_template("rotations/final_report.html", **data)


@rotation_bp.route("/final-report/<int:intern_id>/pdf")
@login_required
@roles_required("HR")
def final_report_pdf(intern_id):
    """Download the Final Internship Report as a branded PDF."""
    intern = Intern.query.get_or_404(intern_id)
    try:
        data = _final_report_data(intern)
        buffer = pdf_reports.build_intern_final_report_pdf(data)
        return send_file(
            buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"final_internship_report_{intern.full_name.replace(' ', '_')}.pdf",
        )
    except Exception:
        current_app.logger.exception(
            "Failed to generate final report PDF for intern #%s.", intern_id
        )
        flash("Could not generate the PDF report due to a system error. Please try again.", "danger")
        return redirect(url_for("rotation.final_report", intern_id=intern_id))
