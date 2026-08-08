"""
routes/intern_portal.py
------------------------
Module 3: the Intern Portal. Every route here is scoped to the
logged-in intern's own data (an intern can never see or edit another
intern's records) using @roles_required("Intern") plus the shared
current_intern_profile() lookup.
"""

from datetime import datetime
from utils import today_pkt

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_file
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    Attendance,
    Evaluation,
    Feedback,
    FinalReport,
    Notification,
    PMEvaluation,
    Project,
    ProjectSubmission,
)
from models.pm_workspace import ProjectMilestone
from utils import (
    roles_required,
    current_intern_profile,
    save_profile_picture,
    delete_profile_picture,
    save_submission_file,
    delete_submission_file,
    resolve_file_url,
    now_pkt,
)
from services.email_service import (
    send_internship_completion_email,
    send_hr_pm_notification_email,
    get_hr_recipients,
    send_attendance_alert_email,
    send_report_submission_email,
)
from services.pdf_reports import build_intern_profile_pdf

intern_portal_bp = Blueprint("intern_portal", __name__, url_prefix="/portal")


def _parse_date(value: str):
    """Parse an HTML date input (YYYY-MM-DD) into a date object."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def _require_intern():
    """
    Shared guard used at the top of every portal route: resolves the
    current intern's profile or redirects with a flash message if it
    can't be found (e.g. a misconfigured account).
    """
    intern = current_intern_profile()
    if intern is None:
        flash("Your Intern profile could not be found. Contact HR.", "danger")
        return None
    return intern


# ----------------------------------------------------------------------
# Intern Dashboard
# ----------------------------------------------------------------------
@intern_portal_bp.route("/dashboard")
@login_required
@roles_required("Intern")
def dashboard():
    """
    Intern Dashboard: assigned project, assigned manager, department,
    attendance percentage, current progress and notifications.
    """
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    # An intern can now be linked to multiple projects at once.
    assigned_projects = (
        Project.query.filter(Project.interns.any(id=intern.id))
        .order_by(Project.created_at.desc())
        .all()
    )
    # "Primary" project for the milestone/progress widgets below: the
    # most recently created project that isn't finished yet, falling
    # back to the most recent project overall.
    assigned_project = next(
        (p for p in assigned_projects if p.status not in ("Completed", "Approved")),
        assigned_projects[0] if assigned_projects else None,
    )

    # Attendance percentage + Present/Absent/Leave breakdown.
    attendance_records = Attendance.query.filter_by(intern_id=intern.id).all()
    total_attendance = len(attendance_records)
    present_count = sum(1 for r in attendance_records if r.status in Attendance.ATTENDED_STATUSES)
    absent_count = sum(1 for r in attendance_records if r.status == "Absent")
    leave_count = sum(1 for r in attendance_records if r.status == "Leave")
    attendance_percentage = (
        round((present_count / total_attendance) * 100, 1) if total_attendance else 0
    )

    # Project progress: milestone-based completion of the assigned project.
    project_progress = assigned_project.completion_percentage if assigned_project else 0

    # Current progress (dashboard "Current Progress" figure): mirrors the
    # milestone-based project progress now that Daily Work Log entries
    # (the previous source of this figure) have been removed.
    current_progress = project_progress

    # Days Remaining: days left until internship_end_date (0 if already over).
    today = today_pkt()
    days_remaining = max((intern.internship_end_date - today).days, 0)
    total_span = (intern.internship_end_date - intern.internship_start_date).days
    elapsed = (today - intern.internship_start_date).days
    internship_progress_percent = (
        max(0, min(100, round((elapsed / total_span) * 100))) if total_span > 0 else 0
    )

    # Next Rotation Date: derived from the intern's current (open-ended)
    # rotation stint -- there's no separately scheduled "next rotation"
    # date stored anywhere, so we surface the current rotation's start
    # date as a reference point, or None if the intern hasn't rotated yet.
    current_rotation = intern.current_rotation
    next_rotation_date = current_rotation.start_date if current_rotation else None

    # Pending Tasks: incomplete milestones across every assigned project.
    pending_tasks = []
    if assigned_projects:
        pending_tasks = (
            ProjectMilestone.query.filter(
                ProjectMilestone.project_id.in_([p.id for p in assigned_projects]),
                ProjectMilestone.status != "Completed",
            )
            .order_by(ProjectMilestone.due_date)
            .all()
        )

    # Upcoming Deadlines: every unfinished project's deadline plus any
    # incomplete milestone due dates, soonest first, next 5.
    upcoming_deadlines = []
    for p in assigned_projects:
        if p.status not in ("Completed", "Approved"):
            upcoming_deadlines.append({"label": p.title, "date": p.deadline, "kind": "Project"})
    for m in pending_tasks:
        upcoming_deadlines.append({"label": m.title, "date": m.due_date, "kind": "Milestone"})
    upcoming_deadlines.sort(key=lambda d: d["date"])
    upcoming_deadlines = upcoming_deadlines[:5]

    # Today's attendance record, if any -- drives the Clock In button's
    # state (shows "Clocked In" instead of the button once used today).
    today_attendance = Attendance.query.filter_by(intern_id=intern.id, date=today_pkt()).first()

    # Notifications: most recent 10, newest first.
    notifications = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(10)
        .all()
    )
    unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()

    return render_template(
        "portal/dashboard.html",
        intern=intern,
        assigned_project=assigned_project,
        assigned_projects=assigned_projects,
        attendance_percentage=attendance_percentage,
        total_attendance=total_attendance,
        present_count=present_count,
        absent_count=absent_count,
        leave_count=leave_count,
        current_progress=current_progress,
        project_progress=project_progress,
        days_remaining=days_remaining,
        internship_progress_percent=internship_progress_percent,
        next_rotation_date=next_rotation_date,
        pending_tasks=pending_tasks,
        upcoming_deadlines=upcoming_deadlines,
        notifications=notifications,
        unread_count=unread_count,
        today_attendance=today_attendance,
    )


# ----------------------------------------------------------------------
# Attendance: intern self-service Clock In (time-clock style).
# Date and time are always taken from the server; the intern never
# supplies or edits them. One Clock In per day, enforced both by an
# explicit pre-check and by the Attendance table's unique constraint.
# ----------------------------------------------------------------------
@intern_portal_bp.route("/attendance/clock-in", methods=["POST"])
@login_required
@roles_required("Intern")
def clock_in():
    """Record today's Clock In for the current intern. No date, time,
    or status is accepted from the request -- everything is derived
    server-side, so the intern cannot back- or post-date attendance."""
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    today = today_pkt()

    existing = Attendance.query.filter_by(intern_id=intern.id, date=today).first()
    if existing is not None:
        if existing.is_leave_managed:
            flash("You are on approved Leave today, so Clock In is not available.", "info")
        else:
            flash("You have already clocked in today.", "info")
        return redirect(url_for("intern_portal.dashboard"))

    try:
        checkin_time = now_pkt().time()
        record = Attendance(
            intern_id=intern.id,
            date=today,
            time=checkin_time,
            status=Attendance.status_for_checkin(checkin_time),
        )
        db.session.add(record)
        db.session.commit()
        if record.status == "Late":
            flash("Clocked in successfully -- marked Late (after 09:30 AM).", "warning")
            send_attendance_alert_email(intern, "Late", today)
        else:
            flash("Clocked in successfully. Have a productive day!", "success")
    except IntegrityError:
        db.session.rollback()
        flash("You have already clocked in today.", "info")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to clock in intern #%s.", intern.id)
        flash("Could not record your Clock In due to a system error. Please try again.", "danger")

    return redirect(url_for("intern_portal.dashboard"))


# ----------------------------------------------------------------------
# Attendance: intern self-service Clock Out. Requires an active
# Clock In for today with no Clock Out recorded yet; the time is
# always taken from the server, never supplied by the intern.
# ----------------------------------------------------------------------
@intern_portal_bp.route("/attendance/clock-out", methods=["POST"])
@login_required
@roles_required("Intern")
def clock_out():
    """Record today's Clock Out for the current intern. Requires an
    existing, non-leave-managed Clock In for today with no Clock Out
    already recorded."""
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    today = today_pkt()

    existing = Attendance.query.filter_by(intern_id=intern.id, date=today).first()

    if existing is None or existing.is_leave_managed or existing.time is None:
        flash("You must clock in before you can clock out.", "danger")
        return redirect(url_for("intern_portal.dashboard"))

    if existing.time_out is not None:
        flash("You have already clocked out today.", "info")
        return redirect(url_for("intern_portal.dashboard"))

    try:
        existing.time_out = now_pkt().time()
        db.session.commit()
        flash("Clocked out successfully. See you tomorrow!", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to clock out intern #%s.", intern.id)
        flash("Could not record your Clock Out due to a system error. Please try again.", "danger")

    return redirect(url_for("intern_portal.dashboard"))


@intern_portal_bp.route("/notifications/mark-read", methods=["POST"])
@login_required
@roles_required("Intern")
def mark_notifications_read():
    """Mark all of the current intern's notifications as read."""
    try:
        Notification.query.filter_by(user_id=current_user.id, is_read=False).update(
            {"is_read": True}
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to mark notifications read for user #%s.", current_user.id
        )
    return redirect(url_for("intern_portal.dashboard"))


# ----------------------------------------------------------------------
# Project Submission
# ----------------------------------------------------------------------
@intern_portal_bp.route("/submissions", methods=["GET", "POST"])
@login_required
@roles_required("Intern")
def submissions():
    """Submit and list project links (Google Drive link or a deployed
    website URL such as Vercel, Netlify, GitHub Pages, etc.)."""
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    assigned_projects = (
        Project.query.filter(Project.interns.any(id=intern.id))
        .order_by(Project.created_at.desc())
        .all()
    )
    assigned_project = assigned_projects[0] if assigned_projects else None

    if request.method == "POST":
        if not assigned_projects:
            flash("You do not have an assigned project to submit a link against.", "danger")
            return redirect(url_for("intern_portal.submissions"))

        project_id_raw = request.form.get("project_id")
        submit_project = None
        if project_id_raw:
            submit_project = next(
                (p for p in assigned_projects if p.id == int(project_id_raw)), None
            )
        if submit_project is None:
            submit_project = assigned_project

        notes = request.form.get("notes", "").strip()
        link = request.form.get("link", "").strip()
        file_storage = request.files.get("file")

        try:
            if not link and (not file_storage or file_storage.filename == ""):
                flash("Please provide a link, attach a file, or both.", "danger")
                return redirect(url_for("intern_portal.submissions"))

            # save_submission_file() only ever looks at the single
            # <input type="file" name="file"> field (not "multiple"),
            # so this already enforces the "max 1 file per submission"
            # rule at the form level; the backend just persists exactly
            # what was posted.
            saved = save_submission_file(file_storage, subfolder="submissions")
            stored_reference, original_filename = saved if saved else (None, None)

            submission = ProjectSubmission(
                intern_id=intern.id,
                project_id=submit_project.id,
                link=link or None,
                stored_reference=stored_reference,
                original_filename=original_filename,
                notes=notes,
            )
            db.session.add(submission)
            db.session.commit()
            flash("Link submitted successfully.", "success")
            if submit_project.manager and submit_project.manager.user and submit_project.manager.user.email:
                send_report_submission_email(
                    recipient_email=submit_project.manager.user.email,
                    recipient_name=submit_project.manager.full_name,
                    intern_name=intern.full_name,
                    report_title=submit_project.title,
                    report_type="Project Submission",
                    action_url=url_for("project.view_project", project_id=submit_project.id, _external=False),
                )
            return redirect(url_for("intern_portal.submissions"))
        except ValueError as ve:
            db.session.rollback()
            flash(str(ve), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("Could not save your submission due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to save submission for intern #%s.", intern.id)
            flash("Could not save your submission due to a system error. Please try again.", "danger")

    from sqlalchemy import extract

    available_years = sorted(
        {
            y[0]
            for y in db.session.query(extract("year", ProjectSubmission.submitted_at))
            .filter(ProjectSubmission.intern_id == intern.id)
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

    submissions_query = ProjectSubmission.query.filter_by(intern_id=intern.id)
    if year:
        submissions_query = submissions_query.filter(
            extract("year", ProjectSubmission.submitted_at) == year
        )
    my_submissions = submissions_query.order_by(ProjectSubmission.submitted_at.desc()).all()

    return render_template(
        "portal/submissions.html",
        assigned_project=assigned_project,
        assigned_projects=assigned_projects,
        submissions=my_submissions,
        available_years=available_years,
        selected_year=year,
        filters=request.args,
    )


@intern_portal_bp.route("/submissions/delete/<int:submission_id>", methods=["POST"])
@login_required
@roles_required("Intern")
def delete_submission(submission_id):
    """Remove a submission the intern previously uploaded. Only allowed
    while it's still Pending on both sides -- once HR or the Project
    Manager has reviewed it, it becomes part of the project's audit
    trail and can no longer be removed by the intern."""
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    submission = ProjectSubmission.query.get_or_404(submission_id)
    if submission.intern_id != intern.id:
        flash("You can only delete your own submissions.", "danger")
        return redirect(url_for("intern_portal.submissions"))

    if submission.hr_status != "Pending" or submission.pm_status != "Pending":
        flash(
            "This submission has already been reviewed and can no longer be removed.",
            "danger",
        )
        return redirect(url_for("intern_portal.submissions"))

    try:
        stored_reference = submission.stored_reference
        db.session.delete(submission)
        db.session.commit()
        delete_submission_file(stored_reference, "submissions")
        flash("Submission deleted.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete submission #%s.", submission_id)
        flash("Could not delete the submission due to a system error. Please try again.", "danger")
    return redirect(url_for("intern_portal.submissions"))


# ----------------------------------------------------------------------
# Final Internship Report
# ----------------------------------------------------------------------
@intern_portal_bp.route("/final-report", methods=["GET", "POST"])
@login_required
@roles_required("Intern")
def final_report():
    """Create or update the single Final Internship Report."""
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    existing_report = FinalReport.query.filter_by(intern_id=intern.id).first()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        summary = request.form.get("summary", "").strip()
        link = request.form.get("link", "").strip()
        file_storage = request.files.get("file")

        errors = []
        if not title:
            errors.append("Report title is required.")
        if not summary:
            errors.append("Report summary is required.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("portal/final_report.html", report=existing_report)

        try:
            saved = save_submission_file(file_storage, subfolder="final_reports")
            is_new_report = existing_report is None

            if existing_report:
                if saved:
                    delete_submission_file(existing_report.stored_filename, "final_reports")
                    existing_report.stored_filename, existing_report.original_filename = (
                        saved[0],
                        saved[1],
                    )
                existing_report.title = title
                existing_report.summary = summary
                existing_report.link = link or None
                flash("Final Internship Report updated successfully.", "success")
            else:
                stored_filename, original_filename = (saved[0], saved[1]) if saved else (None, None)
                existing_report = FinalReport(
                    intern_id=intern.id,
                    title=title,
                    summary=summary,
                    link=link or None,
                    stored_filename=stored_filename,
                    original_filename=original_filename,
                )
                db.session.add(existing_report)
                flash("Final Internship Report submitted successfully.", "success")

            db.session.commit()

            if is_new_report:
                send_internship_completion_email(intern=intern, final_report=existing_report)
                send_hr_pm_notification_email(
                    recipients=get_hr_recipients(),
                    recipient_name="HR Team",
                    event_title="Internship Final Report Submitted",
                    event_message=(
                        f"{intern.full_name} has submitted their Final Internship Report, "
                        "marking the completion of their internship."
                    ),
                    details=[
                        ("Intern", intern.full_name),
                        ("Department", intern.department.name if intern.department else "N/A"),
                        ("Report Title", title),
                    ],
                )

            return redirect(url_for("intern_portal.final_report"))
        except ValueError as ve:
            db.session.rollback()
            flash(str(ve), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("Could not save your final report due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to save final report for intern #%s.", intern.id)
            flash("Could not save your final report due to a system error. Please try again.", "danger")

    return render_template("portal/final_report.html", report=existing_report)


# ----------------------------------------------------------------------
# Intern Profile
# ----------------------------------------------------------------------
@intern_portal_bp.route("/profile", methods=["GET", "POST"])
@login_required
@roles_required("Intern")
def profile():
    """View and edit editable profile fields: phone, address, photo."""
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        photo = request.files.get("profile_picture")

        errors = []
        if not phone:
            errors.append("Phone number is required.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("portal/profile.html", intern=intern)

        try:
            new_picture = save_profile_picture(photo)
            if new_picture:
                delete_profile_picture(current_user.profile_picture)
                current_user.profile_picture = new_picture

            intern.phone = phone
            intern.address = address
            db.session.commit()
            flash("Profile updated successfully.", "success")
            return redirect(url_for("intern_portal.profile"))
        except ValueError as ve:
            db.session.rollback()
            flash(str(ve), "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update profile for intern #%s.", intern.id)
            flash("Could not update the profile due to a system error. Please try again.", "danger")

    return render_template("portal/profile.html", intern=intern)


@intern_portal_bp.route("/profile/download")
@login_required
@roles_required("Intern")
def download_profile():
    """Download a PDF of the intern's own profile (personal, academic
    and internship assignment details)."""
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    buffer = build_intern_profile_pdf(intern)
    filename = f"internship-profile-{intern.full_name.replace(' ', '_')}.pdf"
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


# ----------------------------------------------------------------------
# Feedback Module
# ----------------------------------------------------------------------
@intern_portal_bp.route("/feedback", methods=["GET", "POST"])
@login_required
@roles_required("Intern")
def feedback():
    """Submit or update the Intern Exit Feedback Form."""
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    existing_feedback = Feedback.query.filter_by(intern_id=intern.id).first()

    if request.method == "POST":
        errors = []

        # Section A / B / C -- 1-5 rating fields
        rating_values = {}
        for field in Feedback.RATING_FIELDS:
            raw = request.form.get(field, "")
            try:
                value = int(raw)
                if value not in Feedback.RATING_CHOICES:
                    raise ValueError
                rating_values[field] = value
            except (TypeError, ValueError):
                errors.append("Please answer every statement in Sections A, B and C.")
                break

        # Section D -- competency choices
        competency_values = {}
        for field in Feedback.COMPETENCY_FIELDS:
            value = request.form.get(field, "").strip()
            if value not in Feedback.COMPETENCY_CHOICES:
                errors.append("Please answer every competency in Section D.")
                break
            competency_values[field] = value

        # Section E -- open feedback
        valuable_learning = request.form.get("valuable_learning", "").strip()
        program_suggestions = request.form.get("program_suggestions", "").strip()
        if not valuable_learning:
            errors.append("Please share the most valuable learning experience.")

        # Overall Assessment
        overall_experience_rating = request.form.get("overall_experience_rating", "").strip()
        if overall_experience_rating not in Feedback.OVERALL_RATING_CHOICES:
            errors.append("Please rate your overall internship experience.")

        recommend_program = request.form.get("recommend_program", "").strip()
        if recommend_program not in Feedback.RECOMMEND_CHOICES:
            errors.append("Please indicate whether you would recommend the program.")

        future_employment_interest = request.form.get("future_employment_interest", "").strip()
        if future_employment_interest not in Feedback.FUTURE_EMPLOYMENT_CHOICES:
            errors.append("Please indicate your interest in future employment with PIACL.")

        if errors:
            # Deduplicate while preserving order
            for e in dict.fromkeys(errors):
                flash(e, "danger")
            return render_template(
                "portal/feedback.html",
                feedback=existing_feedback,
                form=request.form,
                intern_profile=intern,
                feedback_competency_choices=Feedback.COMPETENCY_CHOICES,
            )

        field_values = {
            **rating_values,
            **competency_values,
            "valuable_learning": valuable_learning,
            "program_suggestions": program_suggestions,
            "overall_experience_rating": overall_experience_rating,
            "recommend_program": recommend_program,
            "future_employment_interest": future_employment_interest,
        }

        try:
            if existing_feedback:
                for key, value in field_values.items():
                    setattr(existing_feedback, key, value)
                flash("Feedback updated successfully. Thank you!", "success")
            else:
                existing_feedback = Feedback(intern_id=intern.id, **field_values)
                db.session.add(existing_feedback)
                flash("Feedback submitted successfully. Thank you!", "success")

            db.session.commit()
            return redirect(url_for("intern_portal.feedback"))
        except IntegrityError:
            db.session.rollback()
            flash("Could not save your feedback due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to save feedback for intern #%s.", intern.id)
            flash("Could not save your feedback due to a system error. Please try again.", "danger")

    return render_template(
        "portal/feedback.html",
        feedback=existing_feedback,
        intern_profile=intern,
        feedback_competency_choices=Feedback.COMPETENCY_CHOICES,
    )


# ----------------------------------------------------------------------
# My Evaluations (read-only; Module 4)
# ----------------------------------------------------------------------
@intern_portal_bp.route("/evaluations")
@login_required
@roles_required("Intern")
def my_evaluations():
    """Let the intern view every evaluation submitted about them (both
    Project Manager and HR Final), read-only."""
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    evaluations = (
        Evaluation.query.filter_by(intern_id=intern.id)
        .order_by(Evaluation.created_at.desc())
        .all()
    )
    pm_evaluations = (
        PMEvaluation.query.filter_by(intern_id=intern.id, is_finalized=True)
        .order_by(PMEvaluation.evaluation_date.desc())
        .all()
    )
    return render_template(
        "portal/evaluations.html", evaluations=evaluations, pm_evaluations=pm_evaluations
    )
