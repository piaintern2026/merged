"""
app.py
------
Application factory and entry point for the Intern Onboarding Portal.
Run this file directly to start the development server:

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
from routes.pm_extra import pm_extra_bp
from routes.pm_workspace import pm_workspace_bp
from routes.messages import message_bp
from routes.lms import lms_bp
from routes.cron import cron_bp


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
    app.register_blueprint(pm_extra_bp)
    app.register_blueprint(pm_workspace_bp)
    app.register_blueprint(message_bp)
    app.register_blueprint(lms_bp)
    app.register_blueprint(cron_bp)

    # Redirect the root URL to the dashboard (which redirects to login
    # automatically if the user is not authenticated).
    @app.route("/")
    def root():
        from flask import redirect, url_for

        return redirect(url_for("dashboard.index"))

    # ------------------------------------------------------------------
    # Template filters
    # ------------------------------------------------------------------
    from utils import (
        safe_link,
        to_pkt,
        format_pkt,
        display_role,
        department_display,
        pm_display,
        resolve_file_url,
    )
    app.jinja_env.filters["safe_link"] = safe_link
    app.jinja_env.filters["display_role"] = display_role

    # Resolves a stored submission/report file reference (a Vercel Blob
    # URL in production, or a legacy on-disk filename) into a URL a
    # browser can open. Used everywhere a Project Submission or Final
    # Internship Report attachment is shown -- Intern Portal, PM
    # project view, HR/Admin intern view, etc.
    app.jinja_env.globals["resolve_file_url"] = resolve_file_url

    # PKT (Pakistan Standard Time) filters, available to every template:
    #   {{ some_datetime | pkt }}                 -> "17 Jul 2026, 03:45 PM"
    #   {{ some_datetime | pkt('%d %b %Y') }}      -> "17 Jul 2026"
    #   {{ some_datetime | to_pkt }}               -> aware datetime object
    app.jinja_env.filters["pkt"] = format_pkt
    app.jinja_env.filters["to_pkt"] = to_pkt

    # Cosmetic display-name helpers -- 'Department-City' and
    # 'Name-City-Department' -- used across dropdowns/forms/filters/
    # reports. Registered as both filters and globals so they can be
    # used as {{ dept | department_display }} or department_display(dept).
    app.jinja_env.filters["department_display"] = department_display
    app.jinja_env.filters["pm_display"] = pm_display
    app.jinja_env.globals["department_display"] = department_display
    app.jinja_env.globals["pm_display"] = pm_display

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

    # ------------------------------------------------------------------
    # Module 2 automation: daily background job that auto-disables
    # intern accounts once their internship end date is reached. Does
    # NOT depend on anyone logging in or visiting a dashboard -- see
    # services/scheduler.py for how this works in both dev and
    # production (including serverless, via routes/cron.py).
    # ------------------------------------------------------------------
    from services.scheduler import init_scheduler
    init_scheduler(app)

    return app


def _sync_department_hierarchy() -> None:
    """Keep the normalized SubDepartment table in sync with
    utils.DEPARTMENT_HIERARCHY (the single source of truth for the
    Department -> Sub Department cascade). For every existing
    Department row whose name matches a key in the hierarchy, make
    sure a SubDepartment row exists for each of its children -- never
    deletes rows (so any sub-department already linked to an
    Intern/Project/ProjectManager is never dropped out from under
    them), only adds ones that are missing. Safe/idempotent on every
    startup.
    """
    from models import Department, SubDepartment
    from utils import DEPARTMENT_HIERARCHY

    try:
        departments = Department.query.all()
    except Exception:
        # Table doesn't exist yet on a brand-new database before the
        # very first request -- create_all() above handles that case.
        return

    changed = False
    for department in departments:
        sub_names = DEPARTMENT_HIERARCHY.get(department.name)
        if not sub_names:
            continue
        existing = {sd.name for sd in department.sub_departments}
        for sub_name in sub_names:
            if sub_name not in existing:
                db.session.add(
                    SubDepartment(name=sub_name, department_id=department.id)
                )
                changed = True
    if changed:
        db.session.commit()


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

    # Department -> Sub Department cascading hierarchy: sub_department_id
    # FK columns added to tables (interns, projects, project_managers)
    # that already existed before this feature. The sub_departments
    # table itself is brand new so db.create_all() above already
    # created it -- nothing to ALTER there.
    inspector = inspect(db.engine)  # re-inspect: create_all() may have added sub_departments
    for table_name in ("interns", "projects", "project_managers"):
        if table_name in inspector.get_table_names():
            existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
            if "sub_department_id" not in existing_columns:
                db.session.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN sub_department_id INTEGER")
                )
                db.session.commit()

    # Intern Rotation Management: from/to Sub Department columns, added
    # after the initial Department -> Sub Department rollout above so
    # a rotation record captures the sub-department on both ends of
    # the move, not just the department -- keeping rotation history
    # consistent with Intern.sub_department_id.
    if "intern_rotations" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("intern_rotations")}
        for col_name in ("from_sub_department_id", "to_sub_department_id"):
            if col_name not in existing_columns:
                db.session.execute(
                    text(f"ALTER TABLE intern_rotations ADD COLUMN {col_name} INTEGER")
                )
                db.session.commit()

    _sync_department_hierarchy()

    # Super Admin User Management: account-security columns added after
    # the users table already existed in deployed databases (lock/unlock,
    # force password reset, failed-login tracking, last-login audit trail).
    if "users" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("users")}
        user_column_ddl = {
            "is_locked": "ALTER TABLE users ADD COLUMN is_locked BOOLEAN NOT NULL DEFAULT 0",
            "force_password_reset": "ALTER TABLE users ADD COLUMN force_password_reset BOOLEAN NOT NULL DEFAULT 0",
            "failed_login_attempts": "ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0",
            "last_login_at": "ALTER TABLE users ADD COLUMN last_login_at DATETIME",
            "last_login_ip": "ALTER TABLE users ADD COLUMN last_login_ip VARCHAR(45)",
            # Station HR City-Based Management: city each Station HR
            # account is scoped to. Nullable/absent for every other role.
            "city": "ALTER TABLE users ADD COLUMN city VARCHAR(80)",
        }
        for column_name, ddl in user_column_ddl.items():
            if column_name not in existing_columns:
                db.session.execute(text(ddl))
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

        # Attendance redesign: attendance is now an intern self-service
        # time clock (Clock In only) instead of PM-marked, so
        # attendance.marked_by_id must allow NULL (self clock-ins have
        # no PM). SQLite can't ALTER a column to drop NOT NULL, so an
        # older table where marked_by_id is still required is rebuilt
        # in place the same way as above, preserving every existing row.
        else:
            marked_by_col = next(
                (c for c in inspector.get_columns("attendance") if c["name"] == "marked_by_id"),
                None,
            )
            if marked_by_col is not None and not marked_by_col.get("nullable", True):
                all_columns = [c["name"] for c in inspector.get_columns("attendance")]

                db.session.execute(text("ALTER TABLE attendance RENAME TO attendance_old"))
                db.session.commit()

                db.create_all()

                copy_cols = ", ".join(all_columns)
                db.session.execute(
                    text(
                        f"INSERT INTO attendance ({copy_cols}) "
                        f"SELECT {copy_cols} FROM attendance_old"
                    )
                )
                db.session.execute(text("DROP TABLE attendance_old"))
                db.session.commit()

    # Project Managers: p_number, phone and designation are meant to be
    # OPTIONAL at creation time (see models/project_manager.py) and are
    # deliberately stored as NULL -- never "" -- so the unique constraint
    # on p_number never collides between two PMs who both skipped it
    # (NULL != NULL, unlike "" == ""). On any database created before
    # this was the case, these columns may still be missing, or
    # p_number/phone/designation may still be NOT NULL, so leaving
    # either field blank throws an IntegrityError ("null value in
    # column ... violates not-null constraint") that surfaces to the
    # user as "Could not save Project Manager due to a database error."
    # This brings any such table in line with the current model, the
    # same way every other table above is patched.
    if "project_managers" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("project_managers")}
        pm_column_ddl = {
            "p_number": "ALTER TABLE project_managers ADD COLUMN p_number VARCHAR(30)",
            "phone": "ALTER TABLE project_managers ADD COLUMN phone VARCHAR(20)",
            "designation": "ALTER TABLE project_managers ADD COLUMN designation VARCHAR(120)",
        }
        for column_name, ddl in pm_column_ddl.items():
            if column_name not in existing_columns:
                db.session.execute(text(ddl))
                db.session.commit()

        dialect_name = db.engine.dialect.name

        # p_number should have a unique index -- without it, a duplicate
        # P.No typed by mistake would silently save instead of being
        # caught cleanly. A fresh db already gets this for free from
        # db.create_all() above; this only matters for a database that
        # predates the p_number column entirely.
        existing_indexes = {ix["name"] for ix in inspector.get_indexes("project_managers")}
        if "ix_project_managers_p_number" not in existing_indexes and dialect_name != "sqlite":
            db.session.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_project_managers_p_number "
                    "ON project_managers (p_number)"
                )
            )
            db.session.commit()

        # Now that the columns definitely exist, make sure they're
        # actually nullable. A table created back when these fields were
        # required would still have NOT NULL constraints even after the
        # ADD COLUMN step above (which only runs for columns that were
        # missing entirely), so this is checked unconditionally.
        refreshed_columns = {col["name"]: col for col in inspector.get_columns("project_managers")}
        optional_pm_columns = ["p_number", "phone", "designation"]
        if dialect_name == "postgresql":
            for column_name in optional_pm_columns:
                col = refreshed_columns.get(column_name)
                if col is not None and not col.get("nullable", True):
                    db.session.execute(
                        text(
                            f"ALTER TABLE project_managers ALTER COLUMN {column_name} DROP NOT NULL"
                        )
                    )
                    db.session.commit()
        elif dialect_name == "sqlite":
            # SQLite can't ALTER a column to drop NOT NULL directly, so
            # (only if actually needed) rebuild the table in place,
            # copying every existing row across unchanged -- same
            # pattern used for `attendance` above.
            needs_rebuild = any(
                (refreshed_columns.get(c) is not None and not refreshed_columns[c].get("nullable", True))
                for c in optional_pm_columns
            )
            if needs_rebuild:
                all_columns = list(refreshed_columns.keys())
                db.session.execute(text("ALTER TABLE project_managers RENAME TO project_managers_old"))
                db.session.commit()
                db.create_all()
                copy_cols = ", ".join(all_columns)
                db.session.execute(
                    text(
                        f"INSERT INTO project_managers ({copy_cols}) "
                        f"SELECT {copy_cols} FROM project_managers_old"
                    )
                )
                db.session.execute(text("DROP TABLE project_managers_old"))
                db.session.commit()

    # Leave Management: "Cancelled" is a new valid status value (a
    # previously Approved leave can now be cancelled by the assigned
    # PM). It's a plain VARCHAR column already, so no schema change is
    # needed -- new rows simply start using the extra value.

    # Intern Management: Extend/End Internship actions need a lifecycle
    # status + optional end reason on the Intern row.
    if "interns" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("interns")}
        intern_column_ddl = {
            "internship_status": "ALTER TABLE interns ADD COLUMN internship_status VARCHAR(20) NOT NULL DEFAULT 'Active'",
            "end_reason": "ALTER TABLE interns ADD COLUMN end_reason VARCHAR(255)",
        }
        for column_name, ddl in intern_column_ddl.items():
            if column_name not in existing_columns:
                db.session.execute(text(ddl))
                db.session.commit()

    # Intern Management: Excel Import alignment. `degree` -> `qualification`
    # and `city` -> `station` were renamed to match the Excel import
    # template exactly; `major`, `placement`, `documents_status` and
    # `certificate_status` are new columns. Every existing intern row
    # (and its data) is preserved -- only the column names/additions
    # change.
    if "interns" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("interns")}
        dialect_name = db.engine.dialect.name

        rename_map = {"degree": "qualification", "city": "station"}
        for old_name, new_name in rename_map.items():
            if new_name not in existing_columns and old_name in existing_columns:
                db.session.execute(
                    text(f"ALTER TABLE interns RENAME COLUMN {old_name} TO {new_name}")
                )
                db.session.commit()
                existing_columns.discard(old_name)
                existing_columns.add(new_name)

        new_column_ddl = {
            "major": "ALTER TABLE interns ADD COLUMN major VARCHAR(120)",
            "placement": "ALTER TABLE interns ADD COLUMN placement VARCHAR(150)",
            "documents_status": "ALTER TABLE interns ADD COLUMN documents_status VARCHAR(20) NOT NULL DEFAULT 'Pending'",
            "certificate_status": "ALTER TABLE interns ADD COLUMN certificate_status VARCHAR(20) NOT NULL DEFAULT 'Pending'",
        }
        for column_name, ddl in new_column_ddl.items():
            if column_name not in existing_columns:
                db.session.execute(text(ddl))
                db.session.commit()

        # `semester` is no longer collected at registration (dropped
        # from the Excel template and the manual form), so any table
        # still requiring it must be loosened to NULLable.
        # Re-inspect: the `inspector` object created at the top of this
        # function can return stale column metadata after the ALTER
        # TABLE statements just run above, which would make the
        # SQLite rebuild below try to copy old column names (e.g.
        # "degree") that no longer exist. A fresh Inspector guarantees
        # this reflects the columns as they actually are right now.
        inspector = inspect(db.engine)
        refreshed_columns = {col["name"]: col for col in inspector.get_columns("interns")}
        semester_col = refreshed_columns.get("semester")
        needs_semester_nullable = semester_col is not None and not semester_col.get("nullable", True)
        if needs_semester_nullable and dialect_name == "postgresql":
            db.session.execute(text("ALTER TABLE interns ALTER COLUMN semester DROP NOT NULL"))
            db.session.commit()

        # `department_id` is optional at the database level (Bulk Import
        # now requires a real Department match rather than deriving one
        # from free text, but the column stays nullable for backward
        # compatibility with any legacy rows), so any table still
        # requiring it must be loosened to NULLable too.
        department_col = refreshed_columns.get("department_id")
        needs_department_nullable = department_col is not None and not department_col.get("nullable", True)
        if needs_department_nullable and dialect_name == "postgresql":
            db.session.execute(text("ALTER TABLE interns ALTER COLUMN department_id DROP NOT NULL"))
            db.session.commit()

        # SQLite can't ALTER a column's NOT NULL constraint directly --
        # rebuild the table in place (same pattern used elsewhere in
        # this function), preserving every existing row, if either
        # column still needs loosening.
        if dialect_name == "sqlite" and (needs_semester_nullable or needs_department_nullable):
            all_columns = list(refreshed_columns.keys())
            db.session.execute(text("ALTER TABLE interns RENAME TO interns_old"))
            db.session.commit()
            db.create_all()
            copy_cols = ", ".join(all_columns)
            db.session.execute(
                text(f"INSERT INTO interns ({copy_cols}) SELECT {copy_cols} FROM interns_old")
            )
            db.session.execute(text("DROP TABLE interns_old"))
            db.session.commit()

    # Project Module: projects <-> interns used to be a single required
    # `projects.assigned_intern_id` foreign key (one intern per project)
    # before it was replaced with the `project_interns` many-to-many
    # table (see routes/project.py's _parse_intern_ids, which still
    # accepts that old single field name for backward compatibility
    # with any old link/bookmark that posts it). On any database
    # created under that older schema, `assigned_intern_id` is still a
    # NOT NULL column that the current code never populates -- so every
    # INSERT into `projects` (i.e. every attempt to create/assign a
    # project) fails with "NOT NULL constraint failed:
    # projects.assigned_intern_id" ("Could not create/update project
    # due to a database error."). This copies any existing
    # single-intern assignments into `project_interns` (so no
    # assignment history is lost) and then loosens the column to
    # nullable -- the column itself is intentionally kept (not
    # dropped), matching the current model, which still maps it as a
    # deprecated nullable column.
    if "projects" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("projects")}
        project_columns_by_name = {
            col["name"]: col for col in inspector.get_columns("projects")
        }
        legacy_intern_col = project_columns_by_name.get("assigned_intern_id")
        needs_intern_nullable = (
            legacy_intern_col is not None and not legacy_intern_col.get("nullable", True)
        )
        if needs_intern_nullable:
            # Carry forward any existing single-intern assignments into
            # the many-to-many table before the old NOT NULL constraint
            # is loosened, so no assignment history is lost.
            if "project_interns" in inspector.get_table_names():
                db.session.execute(
                    text(
                        "INSERT INTO project_interns (project_id, intern_id, assigned_at) "
                        "SELECT id, assigned_intern_id, CURRENT_TIMESTAMP FROM projects "
                        "WHERE assigned_intern_id IS NOT NULL"
                    )
                )
                db.session.commit()

            if dialect_name == "postgresql":
                db.session.execute(
                    text("ALTER TABLE projects ALTER COLUMN assigned_intern_id DROP NOT NULL")
                )
                db.session.commit()
            elif dialect_name == "sqlite":
                # SQLite can't ALTER a column's NOT NULL constraint
                # directly -- rebuild the table in place, preserving
                # every existing column (including the deprecated
                # `assigned_intern_id`, which the current model still
                # maps as nullable) and every existing row.
                all_columns = list(existing_columns)
                db.session.execute(text("ALTER TABLE projects RENAME TO projects_old"))
                db.session.commit()

                db.create_all()

                copy_cols = ", ".join(all_columns)
                db.session.execute(
                    text(
                        f"INSERT INTO projects ({copy_cols}) "
                        f"SELECT {copy_cols} FROM projects_old"
                    )
                )
                db.session.execute(text("DROP TABLE projects_old"))
                db.session.commit()

    # Soft-delete rollout: permanent "Delete" actions were replaced with
    # Disable/Enable across Projects, Evaluations, PM Evaluations, and
    # Project Milestones so records/relationships are always preserved.
    soft_delete_columns = {
        "projects": ("is_active", "ALTER TABLE projects ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"),
        "evaluations": ("is_active", "ALTER TABLE evaluations ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"),
        "pm_evaluations": ("is_active", "ALTER TABLE pm_evaluations ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"),
        "project_milestones": ("is_active", "ALTER TABLE project_milestones ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"),
        "departments": ("is_active", "ALTER TABLE departments ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"),
    }
    for table_name, (column_name, ddl) in soft_delete_columns.items():
        if table_name in inspector.get_table_names():
            existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
            if column_name not in existing_columns:
                db.session.execute(text(ddl))
                db.session.commit()

    # PM Evaluation Form: the Recommendation field was removed entirely
    # (Module 2). SQLite can't drop a column directly, so an older
    # table that still has it is rebuilt in place, copying every other
    # column across unchanged -- no evaluation data is lost, only the
    # retired field is dropped.
    if "pm_evaluations" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("pm_evaluations")}
        if "recommendation" in existing_columns:
            keep_columns = [c for c in existing_columns if c != "recommendation"]

            db.session.execute(text("ALTER TABLE pm_evaluations RENAME TO pm_evaluations_old"))
            db.session.commit()

            db.create_all()

            copy_cols = ", ".join(keep_columns)
            db.session.execute(
                text(
                    f"INSERT INTO pm_evaluations ({copy_cols}) "
                    f"SELECT {copy_cols} FROM pm_evaluations_old"
                )
            )
            db.session.execute(text("DROP TABLE pm_evaluations_old"))
            db.session.commit()

    # Attendance: "Marked By" was removed entirely (Module 2) -- the
    # module became a self-service intern time clock. marked_by_id is
    # dropped the same way the earlier attendance rebuilds above
    # handled removing/loosening columns.
    #
    # NOTE: an earlier revision of this migration also folded every
    # "Late" row into "Present" here, on the theory that "Late" had
    # been removed as a status. That is no longer true -- Automatic
    # Late Attendance (check-in after 09:30 AM server time) is a
    # required, supported status again (see Attendance.STATUSES /
    # Attendance.status_for_checkin), so that destructive UPDATE has
    # been removed: it was silently erasing every intern's Late
    # history back to "Present" on every app restart.
    if "attendance" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("attendance")}

        if "marked_by_id" in existing_columns:
            keep_columns = [c for c in existing_columns if c != "marked_by_id"]

            db.session.execute(text("ALTER TABLE attendance RENAME TO attendance_old"))
            db.session.commit()

            db.create_all()

            copy_cols = ", ".join(keep_columns)
            db.session.execute(
                text(
                    f"INSERT INTO attendance ({copy_cols}) "
                    f"SELECT {copy_cols} FROM attendance_old"
                )
            )
            db.session.execute(text("DROP TABLE attendance_old"))
            db.session.commit()


    # Project Submissions: the "one link per submission" model gained
    # optional single-file attachments (PDF/Word/Excel/PowerPoint) and
    # the link itself became optional (a submission is now "link, file,
    # or both") -- see models/submission.py. Add the new columns for
    # anyone upgrading from before this change, and drop the old NOT
    # NULL on `link` the same way the other optional-field migrations
    # above do.
    if "project_submissions" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("project_submissions")}
        submission_new_columns = {
            "stored_reference": "ALTER TABLE project_submissions ADD COLUMN stored_reference VARCHAR(1000)",
            "original_filename": "ALTER TABLE project_submissions ADD COLUMN original_filename VARCHAR(255)",
        }
        for column_name, ddl in submission_new_columns.items():
            if column_name not in existing_columns:
                db.session.execute(text(ddl))
                db.session.commit()

        dialect_name = db.engine.dialect.name
        refreshed_columns = {
            col["name"]: col for col in inspector.get_columns("project_submissions")
        }
        link_col = refreshed_columns.get("link")
        if link_col is not None and not link_col.get("nullable", True):
            if dialect_name == "postgresql":
                db.session.execute(
                    text("ALTER TABLE project_submissions ALTER COLUMN link DROP NOT NULL")
                )
                db.session.commit()
            elif dialect_name == "sqlite":
                # SQLite can't ALTER a column's NOT NULL constraint
                # directly -- rebuild the table in place per the model,
                # copying every existing row across unchanged (same
                # pattern used for `attendance`/`project_managers` above).
                all_columns = list(refreshed_columns.keys())
                db.session.execute(
                    text("ALTER TABLE project_submissions RENAME TO project_submissions_old")
                )
                db.session.commit()

                db.create_all()

                copy_cols = ", ".join(all_columns)
                db.session.execute(
                    text(
                        f"INSERT INTO project_submissions ({copy_cols}) "
                        f"SELECT {copy_cols} FROM project_submissions_old"
                    )
                )
                db.session.execute(text("DROP TABLE project_submissions_old"))
                db.session.commit()

    # Final Internship Report: gained an optional supporting `link`
    # field alongside its existing optional file attachment, and now
    # also accepts Word/Excel/PowerPoint files, not just PDF (that part
    # is enforced in config.ALLOWED_DOCUMENT_EXTENSIONS, not the schema).
    if "final_reports" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("final_reports")}
        if "link" not in existing_columns:
            db.session.execute(text("ALTER TABLE final_reports ADD COLUMN link VARCHAR(2048)"))
            db.session.commit()


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
        full_name="Station HR",
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

    # Email Notification System: seed SMTP settings from the current
    # environment/config.py defaults so the Email Settings page starts
    # in sync with .env, then can be overridden from the UI afterwards
    # without touching environment variables again.
    from flask import current_app

    env_defaults = {
        "mail_server": current_app.config.get("MAIL_SERVER", "smtp.gmail.com"),
        "mail_port": str(current_app.config.get("MAIL_PORT", 587)),
        "mail_use_tls": "true" if current_app.config.get("MAIL_USE_TLS") else "false",
        "mail_use_ssl": "true" if current_app.config.get("MAIL_USE_SSL") else "false",
        "mail_username": current_app.config.get("MAIL_USERNAME") or "",
        "mail_password": current_app.config.get("MAIL_PASSWORD") or "",
        "mail_default_sender_name": current_app.config.get("MAIL_DEFAULT_SENDER", ("Intern Onboarding Portal", ""))[0],
        "mail_default_sender_email": current_app.config.get("MAIL_DEFAULT_SENDER", ("", ""))[1],
        "mail_suppress_send": "true" if current_app.config.get("MAIL_SUPPRESS_SEND") else "false",
    }
    for key, label, default_value, description in SystemSetting.EMAIL_DEFAULTS:
        if SystemSetting.query.filter_by(key=key).first() is None:
            db.session.add(
                SystemSetting(
                    key=key,
                    label=label,
                    value=env_defaults.get(key, default_value),
                    description=description,
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

@app.cli.command("send-hr-reminders")
def send_hr_reminders_command():
    """
    Flask CLI command: generates in-app HR reminder notifications for
    overdue projects, pending evaluations, and interns due for a
    rotation review. Safe to run repeatedly (e.g. daily cron) --
    duplicate reminders for the same day are skipped automatically.

    Usage:
        flask send-hr-reminders
    """
    from services.reminders import generate_all_hr_reminders

    with app.app_context():
        counts = generate_all_hr_reminders()
    print(f"HR reminders generated: {counts}")


@app.cli.command("complete-expired-internships")
def complete_expired_internships_command():
    """
    Flask CLI command (Module 2 automation): checks every intern's
    internship end date and, for anyone whose last internship day has
    been reached, marks their internship Completed and disables their
    login account so they can no longer sign in -- while every
    existing record (attendance, leave, submissions, evaluations,
    reports, etc.) stays fully intact for reporting/analytics.

    Safe to run repeatedly (e.g. daily cron/scheduled task); this is
    also enforced opportunistically on login (see routes/auth.py), so
    this command is a proactive sweep on top of that, not the only
    thing enforcing it.

    Usage:
        flask complete-expired-internships
    """
    from services.intern_lifecycle import complete_expired_internships

    with app.app_context():
        count = complete_expired_internships()
    print(f"Internships auto-completed: {count}")


if __name__ == "__main__":
    # threaded=True so a single slow/stuck request (e.g. a stale DB
    # connection) can't block every other page in the app - this was
    # the main cause of the whole system appearing to "freeze".
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)
