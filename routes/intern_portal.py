"""
routes/intern_portal.py
------------------------
Module 3: the Intern Portal. Every route here is scoped to the
logged-in intern's own data (an intern can never see or edit another
intern's records) using @roles_required("Intern") plus the shared
current_intern_profile() lookup.
"""

from datetime import datetime, date
from utils import today_pkt

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    Attendance,
    DailyWorkLog,
    Evaluation,
    Feedback,
    FinalReport,
    Notification,
    PMEvaluation,
    Project,
    ProjectSubmission,
)
from utils import (
    roles_required,
    current_intern_profile,
    save_profile_picture,
    delete_profile_picture,
    save_submission_file,
    delete_submission_file,
    is_valid_submission_link,
)
from services.email_service import (
    send_internship_completion_email,
    send_hr_pm_notification_email,
    get_hr_recipients,
)

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

    # An intern is linked to at most one active project at a time
    # (Project.assigned_intern_id is a single FK per Module 2's design).
    # Prefer the most recently created, not-yet-finished project.
    assigned_project = (
        Project.query.filter_by(assigned_intern_id=intern.id)
        .order_by(Project.created_at.desc())
        .first()
    )

    # Attendance percentage: Present / total marked days.
    attendance_records = Attendance.query.filter_by(intern_id=intern.id).all()
    total_attendance = len(attendance_records)
    present_count = sum(1 for r in attendance_records if r.status == "Present")
    attendance_percentage = (
        round((present_count / total_attendance) * 100, 1) if total_attendance else 0
    )

    # Current progress: most recent daily work log's progress_percent.
    latest_log = (
        DailyWorkLog.query.filter_by(intern_id=intern.id)
        .order_by(DailyWorkLog.log_date.desc(), DailyWorkLog.created_at.desc())
        .first()
    )
    current_progress = latest_log.progress_percent if latest_log else 0

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
        attendance_percentage=attendance_percentage,
        total_attendance=total_attendance,
        present_count=present_count,
        current_progress=current_progress,
        notifications=notifications,
        unread_count=unread_count,
    )


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

    assigned_project = (
        Project.query.filter_by(assigned_intern_id=intern.id)
        .order_by(Project.created_at.desc())
        .first()
    )

    if request.method == "POST":
        if assigned_project is None:
            flash("You do not have an assigned project to submit a link against.", "danger")
            return redirect(url_for("intern_portal.submissions"))

        notes = request.form.get("notes", "").strip()
        link = request.form.get("link", "").strip()

        try:
            if not link:
                flash("Please provide a Google Drive link or a deployed website URL.", "danger")
                return redirect(url_for("intern_portal.submissions"))

            if not is_valid_submission_link(link):
                flash("Please provide a valid URL (e.g. https://...).", "danger")
                return redirect(url_for("intern_portal.submissions"))

            submission = ProjectSubmission(
                intern_id=intern.id,
                project_id=assigned_project.id,
                link=link,
                notes=notes,
            )
            db.session.add(submission)
            db.session.commit()
            flash("Link submitted successfully.", "success")
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

    my_submissions = (
        ProjectSubmission.query.filter_by(intern_id=intern.id)
        .order_by(ProjectSubmission.submitted_at.desc())
        .all()
    )

    return render_template(
        "portal/submissions.html",
        assigned_project=assigned_project,
        submissions=my_submissions,
    )


@intern_portal_bp.route("/submissions/delete/<int:submission_id>", methods=["POST"])
@login_required
@roles_required("Intern")
def delete_submission(submission_id):
    """Remove a submission the intern previously uploaded."""
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    submission = ProjectSubmission.query.get_or_404(submission_id)
    if submission.intern_id != intern.id:
        flash("You can only delete your own submissions.", "danger")
        return redirect(url_for("intern_portal.submissions"))

    try:
        db.session.delete(submission)
        db.session.commit()
        flash("Submission deleted.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete submission #%s.", submission_id)
        flash("Could not delete the submission due to a system error. Please try again.", "danger")
    return redirect(url_for("intern_portal.submissions"))


# ----------------------------------------------------------------------
# Daily Work Log
# ----------------------------------------------------------------------
@intern_portal_bp.route("/work-log", methods=["GET", "POST"])
@login_required
@roles_required("Intern")
def work_log():
    """Submit and list daily work log entries."""
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    assigned_project = (
        Project.query.filter_by(assigned_intern_id=intern.id)
        .order_by(Project.created_at.desc())
        .first()
    )

    if request.method == "POST":
        log_date_raw = request.form.get("log_date", "")
        description = request.form.get("description", "").strip()
        hours_raw = request.form.get("hours_worked", "")
        progress_raw = request.form.get("progress_percent", "")

        errors = []
        log_date_value = None
        if not log_date_raw:
            errors.append("Date is required.")
        else:
            try:
                log_date_value = _parse_date(log_date_raw)
                if log_date_value > today_pkt():
                    errors.append("Log date cannot be in the future.")
            except ValueError:
                errors.append("Invalid date format.")

        if not description:
            errors.append("Description is required.")

        hours_value = None
        try:
            hours_value = float(hours_raw)
            if hours_value <= 0 or hours_value > 24:
                errors.append("Hours worked must be between 0 and 24.")
        except (TypeError, ValueError):
            errors.append("Hours worked must be a valid number.")

        progress_value = None
        try:
            progress_value = int(progress_raw)
            if progress_value < 0 or progress_value > 100:
                errors.append("Progress % must be between 0 and 100.")
        except (TypeError, ValueError):
            errors.append("Progress % must be a whole number.")

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            try:
                log_entry = DailyWorkLog(
                    intern_id=intern.id,
                    project_id=assigned_project.id if assigned_project else None,
                    log_date=log_date_value,
                    description=description,
                    hours_worked=hours_value,
                    progress_percent=progress_value,
                )
                db.session.add(log_entry)
                db.session.commit()
                flash("Work log entry saved.", "success")
                return redirect(url_for("intern_portal.work_log"))
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Failed to save work log for intern #%s.", intern.id)
                flash("Could not save the work log entry due to a system error. Please try again.", "danger")

    logs = (
        DailyWorkLog.query.filter_by(intern_id=intern.id)
        .order_by(DailyWorkLog.log_date.desc())
        .all()
    )

    return render_template(
        "portal/work_log.html",
        logs=logs,
        assigned_project=assigned_project,
        today=today_pkt().isoformat(),
    )


@intern_portal_bp.route("/work-log/delete/<int:log_id>", methods=["POST"])
@login_required
@roles_required("Intern")
def delete_work_log(log_id):
    """Delete one of the intern's own work log entries."""
    intern = _require_intern()
    if intern is None:
        return redirect(url_for("dashboard.index"))

    log_entry = DailyWorkLog.query.get_or_404(log_id)
    if log_entry.intern_id != intern.id:
        flash("You can only delete your own work log entries.", "danger")
        return redirect(url_for("intern_portal.work_log"))

    try:
        db.session.delete(log_entry)
        db.session.commit()
        flash("Work log entry deleted.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete work log #%s.", log_id)
        flash("Could not delete the work log entry due to a system error. Please try again.", "danger")
    return redirect(url_for("intern_portal.work_log"))


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
                flash("Final Internship Report updated successfully.", "success")
            else:
                stored_filename, original_filename = (saved[0], saved[1]) if saved else (None, None)
                existing_report = FinalReport(
                    intern_id=intern.id,
                    title=title,
                    summary=summary,
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
