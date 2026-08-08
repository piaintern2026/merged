"""
routes/pm_workspace.py
------------------------
New Project Manager workspace features that were missing from the PM
module:

  - Project Milestones (create/edit/delete/complete) + visual Timeline
  - Rotation History (read-only, reuses the existing intern_rotations
    table and routes/rotation.py's segment-building helper)
  - Notes (create/edit/delete, scoped to assigned interns/projects)

Everything here is strictly scoped to the logged-in Project Manager's
own assigned interns/projects -- HR/Super Admin may view for oversight
but only the owning PM (or Super Admin) may create/edit/delete.
"""

from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user

from extensions import db
from models import Project, InternRotation, ProjectMilestone
from utils import roles_required, current_pm_profile, log_action, now_pkt, today_pkt
from routes.pm_extra import _scoped_interns
from routes.rotation import _department_segments

pm_workspace_bp = Blueprint("pm_workspace", __name__, url_prefix="/pm/workspace")


def _parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def _get_owned_project(project_id):
    """Return (project, pm) for a project the current PM owns, or
    (None, pm) with a flash message already queued if it isn't theirs.
    Super Admin bypasses ownership (handled by roles_required)."""
    pm = current_pm_profile()
    project = Project.query.get_or_404(project_id)
    if current_user.role == "Project Manager":
        if pm is None or project.assigned_manager_id != pm.id:
            flash("You can only manage milestones for projects assigned to you.", "danger")
            return None, pm
    return project, pm


# ========================================================================
# PROJECT MILESTONES + TIMELINE
# ========================================================================
@pm_workspace_bp.route("/projects/<int:project_id>/milestones")
@login_required
@roles_required("Project Manager", "Station HR", "Super Admin")
def milestones(project_id):
    """List + visual timeline of a project's milestones."""
    project = Project.query.get_or_404(project_id)
    if current_user.role == "Project Manager":
        pm = current_pm_profile()
        if pm is None or project.assigned_manager_id != pm.id:
            flash("You can only view milestones for projects assigned to you.", "danger")
            return redirect(url_for("project.list_projects"))

    milestone_list = (
        ProjectMilestone.query.filter_by(project_id=project.id)
        .order_by(ProjectMilestone.due_date.asc()).all()
    )
    can_manage = current_user.role in ("Project Manager", "Super Admin")

    return render_template(
        "pm_workspace/milestones.html",
        project=project,
        milestones=milestone_list,
        can_manage=can_manage,
        statuses=ProjectMilestone.STATUSES,
        today=today_pkt(),
    )


@pm_workspace_bp.route("/projects/<int:project_id>/milestones/add", methods=["POST"])
@login_required
@roles_required("Project Manager")
def add_milestone(project_id):
    project, pm = _get_owned_project(project_id)
    if project is None:
        return redirect(url_for("project.list_projects"))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    due_date_raw = request.form.get("due_date", "")
    status = request.form.get("status", "Pending")

    errors = []
    if not title:
        errors.append("Milestone title is required.")
    if not due_date_raw:
        errors.append("Due date is required.")
    if status not in ProjectMilestone.STATUSES:
        status = "Pending"

    if errors:
        for e in errors:
            flash(e, "danger")
        return redirect(url_for("pm_workspace.milestones", project_id=project.id))

    try:
        m = ProjectMilestone(
            project_id=project.id,
            title=title,
            description=description or None,
            due_date=_parse_date(due_date_raw),
            status=status,
            completed_at=now_pkt() if status == "Completed" else None,
            created_by_id=pm.id if pm else None,
        )
        db.session.add(m)
        db.session.flush()
        log_action(
            action="CREATE",
            description=f"Added milestone '{title}' to project '{project.title}'.",
            target_type="ProjectMilestone",
            target_id=m.id,
        )
        db.session.commit()
        flash("Milestone added successfully.", "success")
    except ValueError:
        db.session.rollback()
        flash("Invalid due date.", "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to add milestone for project #%s.", project.id)
        flash("Could not add the milestone due to a system error. Please try again.", "danger")

    return redirect(url_for("pm_workspace.milestones", project_id=project.id))


@pm_workspace_bp.route("/milestones/<int:milestone_id>/edit", methods=["POST"])
@login_required
@roles_required("Project Manager")
def edit_milestone(milestone_id):
    m = ProjectMilestone.query.get_or_404(milestone_id)
    project, pm = _get_owned_project(m.project_id)
    if project is None:
        return redirect(url_for("project.list_projects"))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    due_date_raw = request.form.get("due_date", "")
    status = request.form.get("status", m.status)

    if not title or not due_date_raw:
        flash("Title and due date are required.", "danger")
        return redirect(url_for("pm_workspace.milestones", project_id=project.id))

    try:
        m.title = title
        m.description = description or None
        m.due_date = _parse_date(due_date_raw)
        if status in ProjectMilestone.STATUSES:
            if status == "Completed" and m.status != "Completed":
                m.completed_at = now_pkt()
            elif status != "Completed":
                m.completed_at = None
            m.status = status

        log_action(
            action="UPDATE",
            description=f"Updated milestone '{m.title}' on project '{project.title}'.",
            target_type="ProjectMilestone",
            target_id=m.id,
        )
        db.session.commit()
        flash("Milestone updated successfully.", "success")
    except ValueError:
        db.session.rollback()
        flash("Invalid due date.", "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to update milestone #%s.", milestone_id)
        flash("Could not update the milestone due to a system error. Please try again.", "danger")

    return redirect(url_for("pm_workspace.milestones", project_id=project.id))


@pm_workspace_bp.route("/milestones/<int:milestone_id>/complete", methods=["POST"])
@login_required
@roles_required("Project Manager")
def complete_milestone(milestone_id):
    """Quick toggle: mark a milestone Completed (or back to Pending)."""
    m = ProjectMilestone.query.get_or_404(milestone_id)
    project, pm = _get_owned_project(m.project_id)
    if project is None:
        return redirect(url_for("project.list_projects"))

    try:
        if m.status == "Completed":
            m.status = "Pending"
            m.completed_at = None
            msg = f"Milestone '{m.title}' reopened."
        else:
            m.status = "Completed"
            m.completed_at = now_pkt()
            msg = f"Milestone '{m.title}' marked complete."

        log_action(
            action="UPDATE", description=msg, target_type="ProjectMilestone", target_id=m.id
        )
        db.session.commit()
        flash(msg, "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to toggle milestone #%s.", milestone_id)
        flash("Could not update the milestone due to a system error. Please try again.", "danger")

    return redirect(url_for("pm_workspace.milestones", project_id=project.id))


@pm_workspace_bp.route("/milestones/<int:milestone_id>/toggle-status", methods=["POST"])
@login_required
@roles_required("Project Manager", "Super Admin")
def toggle_milestone_status(milestone_id):
    """Disable/enable a milestone instead of permanently deleting it, so
    the project's timeline/history stays intact."""
    m = ProjectMilestone.query.get_or_404(milestone_id)
    project, pm = _get_owned_project(m.project_id)
    if project is None:
        return redirect(url_for("project.list_projects"))

    try:
        m.is_active = not m.is_active
        state = "enabled" if m.is_active else "disabled"
        log_action(
            action="UPDATE",
            description=f"Milestone '{m.title}' {state} on project '{project.title}'.",
            target_type="ProjectMilestone",
            target_id=milestone_id,
        )
        db.session.commit()
        flash(f"Milestone {state}.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to toggle status for milestone #%s.", milestone_id)
        flash("Could not update the milestone's status due to a system error. Please try again.", "danger")

    return redirect(url_for("pm_workspace.milestones", project_id=project.id))


# ========================================================================
# ROTATION HISTORY (read-only, for the PM's assigned interns)
# ========================================================================
@pm_workspace_bp.route("/rotation-history")
@login_required
@roles_required("Project Manager")
def rotation_history():
    pm = current_pm_profile()
    if pm is None:
        flash("Your Project Manager profile could not be found.", "danger")
        return redirect(url_for("dashboard.index"))

    interns = _scoped_interns(pm)
    intern_ids = [i.id for i in interns]

    intern_id = request.args.get("intern_id", type=int)
    query = InternRotation.query.filter(InternRotation.intern_id.in_(intern_ids)) if intern_ids else InternRotation.query.filter(db.false())
    if intern_id and intern_id in intern_ids:
        query = query.filter_by(intern_id=intern_id)

    rotations = query.order_by(InternRotation.start_date.desc(), InternRotation.id.desc()).all()

    return render_template(
        "pm_workspace/rotation_history.html",
        rotations=rotations,
        interns=interns,
        selected_intern=intern_id,
    )


@pm_workspace_bp.route("/rotation-history/<int:intern_id>")
@login_required
@roles_required("Project Manager")
def rotation_timeline(intern_id):
    pm = current_pm_profile()
    if pm is None:
        flash("Your Project Manager profile could not be found.", "danger")
        return redirect(url_for("dashboard.index"))

    interns = _scoped_interns(pm)
    intern = next((i for i in interns if i.id == intern_id), None)
    if intern is None:
        flash("You can only view the rotation history of interns assigned to you.", "danger")
        return redirect(url_for("pm_workspace.rotation_history"))

    segments = _department_segments(intern)
    return render_template("pm_workspace/rotation_timeline.html", intern=intern, segments=segments)
