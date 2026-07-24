"""
app.py
------
Application factory and entry point for the PIA Intern Management
System. Run this file directly to start the development server:

    python app.py

On first run it will automatically create pia.db, all tables, and the
default HR account (hr@piac.com / piacl@2026) if it does not exist.
"""

import os

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template
from flask_login import LoginManager

from config import Config
from extensions import db, login_manager, mail
from models import (
    User,
    Department,
    ProjectManager,
    Intern,
    Project,
    Attendance,
    Leave,
    ProjectSubmission,
    DailyWorkLog,
    FinalReport,
    Feedback,
    Notification,
    Evaluation,
    PMEvaluation,
    AuditLog,
    SystemSetting,
    InternRotation,
)

# Import blueprints
from routes.auth import auth_bp
from routes.dashboard import dashboard_bp
from routes.department import department_bp
from routes.project_manager import pm_bp
from routes.intern import intern_bp
from routes.project import project_bp
from routes.attendance import attendance_bp
from routes.leave import leave_bp
from routes.intern_portal import intern_portal_bp
from routes.evaluation import evaluation_bp
from routes.pm_evaluation import pm_evaluation_bp
from routes.reports import reports_bp
from routes.notification import notification_bp
from routes.admin import admin_bp
from routes.rotation import rotation_bp


def create_app(config_class: type = Config) -> Flask:
    """Application factory: builds and configures the Flask app."""

    app = Flask(__name__)
    app.config.from_object(config_class)

    # ------------------------------------------------------------------
    # Initialise extensions
    # ------------------------------------------------------------------
    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)

    # ------------------------------------------------------------------
    # Register blueprints
    # ------------------------------------------------------------------
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(department_bp)
    app.register_blueprint(pm_bp)
    app.register_blueprint(intern_bp)
    app.register_blueprint(project_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(leave_bp)
    app.register_blueprint(intern_portal_bp)
    app.register_blueprint(evaluation_bp)
    app.register_blueprint(pm_evaluation_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(notification_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(rotation_bp)

    # Redirect the root URL to the dashboard (which redirects to login
    # automatically if the user is not authenticated).
    @app.route("/")
    def root():
        from flask import redirect, url_for

        return redirect(url_for("dashboard.index"))

    # ------------------------------------------------------------------
    # Template filters
    # ------------------------------------------------------------------
    from utils import safe_link, to_pkt, format_pkt, display_role
    app.jinja_env.filters["safe_link"] = safe_link
    app.jinja_env.filters["display_role"] = display_role

    # PKT (Pakistan Standard Time) filters, available to every template:
    #   {{ some_datetime | pkt }}                 -> "17 Jul 2026, 03:45 PM"
    #   {{ some_datetime | pkt('%d %b %Y') }}      -> "17 Jul 2026"
    #   {{ some_datetime | to_pkt }}               -> aware datetime object
    app.jinja_env.filters["pkt"] = format_pkt
    app.jinja_env.filters["to_pkt"] = to_pkt

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------
    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return render_template("errors/500.html"), 500

    # ------------------------------------------------------------------
    # Database setup + default account seeding
    # ------------------------------------------------------------------
    with app.app_context():
        # db.create_all() removed — database schema is managed externally.
        run_schema_migrations()
        seed_default_hr_account(app)
        seed_default_super_admin_account(app)
        seed_default_settings()

    return app


def run_schema_migrations() -> None:
    """
    Lightweight, dependency-free "migration" for columns added to an
    existing table after it was first created.

    db.create_all() only creates tables that don't exist yet -- it never
    alters a table that's already there, so on a database that was
    created before a given column existed (e.g. Department.city), that
    column would silently be missing even though the ORM model expects
    it. This inspects the live schema and adds any such column with a
    plain ALTER TABLE, which works the same way on both SQLite and
    Postgres and never touches/loses existing data. Safe to call on
    every startup: it's a no-op once the column is present.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)

    # create_all() only CREATEs tables that don't exist yet -- it never
    # ALTERs a table that's already there -- so it's always safe to run
    # on every startup. This is what actually brings a brand-new table
    # (e.g. pm_evaluations, added for the Project Manager Evaluation
    # Form module) into existence on a database that predates it.
    db.create_all()

    if "departments" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("departments")}
        if "city" not in existing_columns:
            db.session.execute(text("ALTER TABLE departments ADD COLUMN city VARCHAR(80)"))
            db.session.commit()

    # The Intern Feedback Form was replaced with the Intern Exit Feedback
    # Form (new rating sections, competencies and overall-assessment
    # fields). Any table created under the old schema (experience /
    # suggestions / overall_rating columns) is incompatible with the new
    # model, so it's dropped and recreated by db.create_all() on the next
    # line of create_app(). Old feedback rows used the old form's
    # questions, so they can't be carried forward automatically.
    if "feedback" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("feedback")}
        if "a1_practical_learning" not in existing_columns:
            db.session.execute(text("DROP TABLE feedback"))
            db.session.commit()
            db.create_all()

    # Leave Management: approval authority moved from HR to the
    # assigned Project Manager, and approving a leave now auto-marks
    # Attendance as "Leave" for its date range. That requires:
    #   - attendance.time / attendance.time_out to allow NULL (Absent
    #     and Leave days have no clock-in), which the original schema
    #     didn't allow.
    #   - new attendance columns: source_leave_id + pre_leave_* to
    #     track/restore rows an approval created or overwrote.
    # SQLite can't ALTER a column to drop NOT NULL, so when an older
    # attendance table is detected we rebuild it in place, copying
    # every existing row across unchanged (only the new columns are
    # NULL for pre-existing data -- no data is lost).
    if "attendance" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("attendance")}
        needs_rebuild = "source_leave_id" not in existing_columns

        if needs_rebuild:
            old_columns = [
                "id", "intern_id", "marked_by_id", "date", "time", "time_out",
                "status", "remarks", "created_at",
            ]
            present_old_columns = [c for c in old_columns if c in existing_columns]

            db.session.execute(text("ALTER TABLE attendance RENAME TO attendance_old"))
            db.session.commit()

            # Recreates `attendance` per the current model (nullable
            # time/time_out, new leave-linkage columns included).
            db.create_all()

            copy_cols = ", ".join(present_old_columns)
            db.session.execute(
                text(
                    f"INSERT INTO attendance ({copy_cols}) "
                    f"SELECT {copy_cols} FROM attendance_old"
                )
            )
            db.session.execute(text("DROP TABLE attendance_old"))
            db.session.commit()

    # Leave Management: "Cancelled" is a new valid status value (a
    # previously Approved leave can now be cancelled by the assigned
    # PM). It's a plain VARCHAR column already, so no schema change is
    # needed -- new rows simply start using the extra value.


def seed_default_hr_account(app: Flask) -> None:
    """
    Ensure the default HR account exists. Runs once at startup and
    is safe to call repeatedly (idempotent).
    """
    existing = User.query.filter_by(email=app.config["DEFAULT_HR_EMAIL"]).first()
    if existing:
        return

    default_hr = User(
        email=app.config["DEFAULT_HR_EMAIL"],
        username="hr_admin",
        role=app.config["DEFAULT_HR_ROLE"],
        full_name="HR",
    )
    default_hr.set_password(app.config["DEFAULT_HR_PASSWORD"])
    db.session.add(default_hr)
    db.session.commit()
    app.logger.info("Default HR account created: %s", app.config["DEFAULT_HR_EMAIL"])


def seed_default_super_admin_account(app: Flask) -> None:
    """
    Ensure the default Super Admin account exists. Runs once at startup
    and is safe to call repeatedly (idempotent) -- checked by username
    (and email) so it is never duplicated on subsequent restarts.
    """
    existing = User.query.filter(
        (User.username == app.config["DEFAULT_SUPER_ADMIN_USERNAME"])
        | (User.email == app.config["DEFAULT_SUPER_ADMIN_EMAIL"])
    ).first()
    if existing:
        return

    default_super_admin = User(
        email=app.config["DEFAULT_SUPER_ADMIN_EMAIL"],
        username=app.config["DEFAULT_SUPER_ADMIN_USERNAME"],
        role=app.config["DEFAULT_SUPER_ADMIN_ROLE"],
        full_name="Super Admin",
    )
    default_super_admin.set_password(app.config["DEFAULT_SUPER_ADMIN_PASSWORD"])
    db.session.add(default_super_admin)
    db.session.commit()
    app.logger.info(
        "Default Super Admin account created: %s", app.config["DEFAULT_SUPER_ADMIN_USERNAME"]
    )


def seed_default_settings() -> None:
    """
    Ensure every default SystemSetting row exists (Module 5: Admin
    Features - Settings). Runs on every startup but only inserts rows
    that are genuinely missing, so it never overwrites a value HR has
    already changed via the Settings page.
    """
    for key, label, default_value, description in SystemSetting.DEFAULTS:
        if SystemSetting.query.filter_by(key=key).first() is None:
            db.session.add(
                SystemSetting(
                    key=key, label=label, value=default_value, description=description
                )
            )
    db.session.commit()


# Flask-Login user loader must be registered once, at import time.
@login_manager.user_loader
def load_user(user_id):
    """Reload a user object from the session-stored user id."""
    return User.query.get(int(user_id))



app = create_app()


@app.cli.command("send-deadline-reminders")
def send_deadline_reminders_command():
    """
    Flask CLI command: emails interns (and their Project Manager) whose
    project deadline is approaching or overdue. Safe to run repeatedly
    (e.g. from a daily cron job / scheduled task) since it only reads
    data - it makes no database changes of its own.

    Usage:
        flask send-deadline-reminders
    """
    from services.email_service import send_deadline_reminder_emails

    sent = send_deadline_reminder_emails()
    print(f"Deadline reminder emails sent: {sent}")

if __name__ == "__main__":
    # threaded=True so a single slow/stuck request (e.g. a stale DB
    # connection) can't block every other page in the app - this was
    # the main cause of the whole system appearing to "freeze".
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)
