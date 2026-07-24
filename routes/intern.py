"""
routes/intern.py
-----------------
Full CRUD for Intern registration, including creating their linked User
login account, profile picture upload and a detail "view" page.
"""

from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    Intern,
    Department,
    User,
    Attendance,
    Project,
    ProjectSubmission,
    DailyWorkLog,
    FinalReport,
    Feedback,
    PMEvaluation,
)
from utils import roles_required, save_profile_picture, delete_profile_picture, log_action, PIA_CITIES
from services.email_service import send_welcome_email, send_hr_pm_notification_email, get_hr_recipients

intern_bp = Blueprint("intern", __name__, url_prefix="/interns")


def _parse_date(value: str):
    """Parse an HTML date input (YYYY-MM-DD) into a date object."""
    return datetime.strptime(value, "%Y-%m-%d").date()


@intern_bp.route("/")
@login_required
@roles_required("HR")
def list_interns():
    """Show all registered interns."""
    interns = Intern.query.order_by(Intern.created_at.desc()).all()
    return render_template("interns/list.html", interns=interns, cities=PIA_CITIES)


@intern_bp.route("/view/<int:intern_id>")
@login_required
@roles_required("HR")
def view_intern(intern_id):
    """Show full details of a single intern, including their Module 3
    portal activity: assigned project, submissions, work logs, final
    report, feedback and attendance summary."""
    intern = Intern.query.get_or_404(intern_id)

    assigned_project = (
        Project.query.filter_by(assigned_intern_id=intern.id)
        .order_by(Project.created_at.desc())
        .first()
    )

    attendance_records = Attendance.query.filter_by(intern_id=intern.id).all()
    total_attendance = len(attendance_records)
    present_count = sum(1 for r in attendance_records if r.status == "Present")
    attendance_percentage = (
        round((present_count / total_attendance) * 100, 1) if total_attendance else 0
    )

    submissions = (
        ProjectSubmission.query.filter_by(intern_id=intern.id)
        .order_by(ProjectSubmission.submitted_at.desc())
        .all()
    )
    work_logs = (
        DailyWorkLog.query.filter_by(intern_id=intern.id)
        .order_by(DailyWorkLog.log_date.desc())
        .limit(10)
        .all()
    )
    final_report = FinalReport.query.filter_by(intern_id=intern.id).first()
    feedback = Feedback.query.filter_by(intern_id=intern.id).first()
    pm_evaluations = (
        PMEvaluation.query.filter_by(intern_id=intern.id)
        .order_by(PMEvaluation.evaluation_date.desc())
        .all()
    )

    return render_template(
        "interns/view.html",
        intern=intern,
        assigned_project=assigned_project,
        attendance_percentage=attendance_percentage,
        total_attendance=total_attendance,
        submissions=submissions,
        work_logs=work_logs,
        final_report=final_report,
        feedback=feedback,
        pm_evaluations=pm_evaluations,
    )


@intern_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def add_intern():
    """Register a new intern (creates User + Intern rows)."""
    departments = Department.query.filter_by(status="Active").order_by(Department.name).all()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        cnic = request.form.get("cnic", "").strip()
        university = request.form.get("university", "").strip()
        degree = request.form.get("degree", "").strip()
        semester = request.form.get("semester", "").strip()
        department_id = request.form.get("department_id")
        city = request.form.get("city", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        start_date_raw = request.form.get("internship_start_date", "")
        end_date_raw = request.form.get("internship_end_date", "")
        photo = request.files.get("profile_picture")

        # ---- Validation ----
        errors = []
        required_fields = {
            "Full name": full_name,
            "CNIC": cnic,
            "University": university,
            "Degree": degree,
            "Semester": semester,
            "Department": department_id,
            "City": city,
            "Email": email,
            "Phone": phone,
            "Username": username,
            "Internship start date": start_date_raw,
            "Internship end date": end_date_raw,
        }
        for label, value in required_fields.items():
            if not value:
                errors.append(f"{label} is required.")

        if city and city not in PIA_CITIES:
            errors.append("Please select a valid city from the list.")

        if not password or len(password) < 8:
            errors.append("Password must be at least 8 characters long.")

        if User.query.filter_by(email=email).first():
            errors.append("A user with this email already exists.")
        if User.query.filter_by(username=username).first():
            errors.append("This username is already taken.")
        if Intern.query.filter_by(cnic=cnic).first():
            errors.append("This CNIC is already registered.")

        start_date = end_date = None
        if start_date_raw and end_date_raw and not errors:
            try:
                start_date = _parse_date(start_date_raw)
                end_date = _parse_date(end_date_raw)
                if end_date <= start_date:
                    errors.append("Internship end date must be after the start date.")
            except ValueError:
                errors.append("Invalid date format provided.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "interns/form.html", intern=None, departments=departments, cities=PIA_CITIES, form=request.form
            )

        try:
            picture_filename = save_profile_picture(photo)

            user = User(
                email=email,
                username=username,
                role="Intern",
                profile_picture=picture_filename,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()

            intern = Intern(
                user_id=user.id,
                full_name=full_name,
                cnic=cnic,
                university=university,
                degree=degree,
                semester=semester,
                department_id=int(department_id),
                city=city,
                phone=phone,
                internship_start_date=start_date,
                internship_end_date=end_date,
            )
            db.session.add(intern)
            db.session.flush()
            log_action(
                action="CREATE",
                description=f"Registered intern '{full_name}' (CNIC {cnic}).",
                target_type="Intern",
                target_id=intern.id,
            )
            db.session.commit()

            # Email notifications - fired only after the commit above
            # succeeded. Failures are logged internally and never
            # interrupt this request (see services/email_service.py).
            send_welcome_email(user=user, intern=intern, raw_password=password)
            send_hr_pm_notification_email(
                recipients=get_hr_recipients(),
                recipient_name="HR Team",
                event_title="New Intern Registered",
                event_message=f"A new intern, {full_name}, has been registered in the system.",
                details=[
                    ("Name", full_name),
                    ("Department", intern.department.name if intern.department else "N/A"),
                    ("CNIC", cnic),
                    ("Internship Period", f"{start_date} to {end_date}"),
                ],
            )

            flash(f"Intern '{full_name}' registered successfully.", "success")
            return redirect(url_for("intern.list_interns"))

        except ValueError as ve:
            db.session.rollback()
            flash(str(ve), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("Could not register intern due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to register new intern.")
            flash("Could not register intern due to a system error. Please try again.", "danger")

    return render_template(
        "interns/form.html", intern=None, departments=departments, cities=PIA_CITIES, form=None
    )


@intern_bp.route("/edit/<int:intern_id>", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def edit_intern(intern_id):
    """Edit an existing intern's profile and account details."""
    intern = Intern.query.get_or_404(intern_id)
    user = intern.user
    departments = Department.query.filter_by(status="Active").order_by(Department.name).all()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        cnic = request.form.get("cnic", "").strip()
        university = request.form.get("university", "").strip()
        degree = request.form.get("degree", "").strip()
        semester = request.form.get("semester", "").strip()
        department_id = request.form.get("department_id")
        city = request.form.get("city", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        start_date_raw = request.form.get("internship_start_date", "")
        end_date_raw = request.form.get("internship_end_date", "")
        photo = request.files.get("profile_picture")

        errors = []
        required_fields = {
            "Full name": full_name,
            "CNIC": cnic,
            "University": university,
            "Degree": degree,
            "Semester": semester,
            "Department": department_id,
            "City": city,
            "Email": email,
            "Phone": phone,
            "Username": username,
            "Internship start date": start_date_raw,
            "Internship end date": end_date_raw,
        }
        for label, value in required_fields.items():
            if not value:
                errors.append(f"{label} is required.")

        if city and city not in PIA_CITIES:
            errors.append("Please select a valid city from the list.")

        duplicate_email = User.query.filter(User.email == email, User.id != user.id).first()
        if duplicate_email:
            errors.append("Another user already uses this email.")
        duplicate_username = User.query.filter(
            User.username == username, User.id != user.id
        ).first()
        if duplicate_username:
            errors.append("Another user already uses this username.")
        duplicate_cnic = Intern.query.filter(
            Intern.cnic == cnic, Intern.id != intern.id
        ).first()
        if duplicate_cnic:
            errors.append("This CNIC is already registered to another intern.")

        if password and len(password) < 8:
            errors.append("New password must be at least 8 characters long.")

        start_date = end_date = None
        if start_date_raw and end_date_raw and not errors:
            try:
                start_date = _parse_date(start_date_raw)
                end_date = _parse_date(end_date_raw)
                if end_date <= start_date:
                    errors.append("Internship end date must be after the start date.")
            except ValueError:
                errors.append("Invalid date format provided.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "interns/form.html", intern=intern, departments=departments, cities=PIA_CITIES, form=request.form
            )

        try:
            new_picture = save_profile_picture(photo)
            if new_picture:
                delete_profile_picture(user.profile_picture)
                user.profile_picture = new_picture

            user.email = email
            user.username = username
            if password:
                user.set_password(password)

            intern.full_name = full_name
            intern.cnic = cnic
            intern.university = university
            intern.degree = degree
            intern.semester = semester
            intern.department_id = int(department_id)
            intern.city = city
            intern.phone = phone
            intern.internship_start_date = start_date
            intern.internship_end_date = end_date

            log_action(
                action="UPDATE",
                description=f"Updated intern '{full_name}' (CNIC {cnic}).",
                target_type="Intern",
                target_id=intern.id,
            )
            db.session.commit()
            flash(f"Intern '{full_name}' updated successfully.", "success")
            return redirect(url_for("intern.list_interns"))

        except ValueError as ve:
            db.session.rollback()
            flash(str(ve), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("Could not update intern due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update intern #%s.", intern.id)
            flash("Could not update intern due to a system error. Please try again.", "danger")

    return render_template(
        "interns/form.html", intern=intern, departments=departments, cities=PIA_CITIES, form=None
    )


@intern_bp.route("/delete/<int:intern_id>", methods=["POST"])
@login_required
@roles_required("Super Admin")
def delete_intern(intern_id):
    """Delete an intern and their login account."""
    intern = Intern.query.get_or_404(intern_id)
    user = intern.user
    full_name = intern.full_name

    try:
        intern_id_val = intern.id
        delete_profile_picture(user.profile_picture)
        db.session.delete(user)  # cascades to delete intern profile too
        log_action(
            action="DELETE",
            description=f"Deleted intern '{full_name}'.",
            target_type="Intern",
            target_id=intern_id_val,
        )
        db.session.commit()
        flash(f"Intern '{full_name}' deleted successfully.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Could not delete intern due to a database error.", "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete intern #%s.", intern_id)
        flash("Could not delete intern due to a system error. Please try again.", "danger")

    return redirect(url_for("intern.list_interns"))
