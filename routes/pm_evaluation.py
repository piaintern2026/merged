"""
routes/pm_evaluation.py
-------------------------
Project Manager Evaluation Form module. Recreates the official 6-Week
Internship Learning & Performance Evaluation Form as a permanent,
database-backed record.

Access rules:
- A Project Manager may only create/edit an evaluation for an intern
  who is currently assigned to one of their own projects, and may only
  see evaluations they personally authored.
- HR/Admin can view, search, filter, print and export every record,
  and may edit/delete any record (e.g. to correct a mistake).
- Once a PM finalizes a record it is locked for further edits by that
  PM (HR can still correct it).
"""

import csv
import io
from datetime import datetime

from flask import (
    Blueprint, render_template, redirect, url_for, flash, request,
    current_app, Response,
)
from flask_login import login_required, current_user

from extensions import db
from models import PMEvaluation, Intern, Project, ProjectManager
from utils import roles_required, current_pm_profile, notify_user, log_action, today_pkt

pm_evaluation_bp = Blueprint("pm_evaluation", __name__, url_prefix="/pm-evaluations")


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------
def _pm_projects_with_interns(pm):
    """Every project led by this PM that currently has an intern
    assigned -- the only interns this PM is allowed to evaluate."""
    return (
        Project.query.filter_by(assigned_manager_id=pm.id)
        .filter(Project.assigned_intern_id.isnot(None))
        .order_by(Project.title)
        .all()
    )


def _parse_ratings(form) -> tuple[dict, list[str]]:
    """
    Parse the six week ratings and ten competency ratings from a
    submitted form. Every field is optional (a PM can save partial
    progress across the six weeks), but any value provided must be a
    whole number from 1-5. Returns (values_dict, errors_list).
    """
    values = {}
    errors = []

    fields = [(w["field"], f"Week {w['number']} rating") for w in PMEvaluation.WEEKS]
    fields += [(field, label) for field, label in PMEvaluation.COMPETENCIES]

    for field_name, label in fields:
        raw_value = (form.get(field_name) or "").strip()
        if raw_value == "":
            values[field_name] = None
            continue
        try:
            value = int(raw_value)
            if value < 1 or value > PMEvaluation.MAX_PER_RATING:
                errors.append(f"{label} must be between 1 and {PMEvaluation.MAX_PER_RATING}.")
            else:
                values[field_name] = value
        except (TypeError, ValueError):
            errors.append(f"{label} must be a whole number.")

    return values, errors


def _parse_evaluation_date(form) -> tuple[object, list[str]]:
    raw = (form.get("evaluation_date") or "").strip()
    if not raw:
        return today_pkt(), []
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date(), []
    except ValueError:
        return today_pkt(), ["Evaluation date must be a valid date."]


def _can_edit(evaluation: PMEvaluation) -> bool:
    """HR/Admin can always edit. A PM may edit only their own,
    unfinalized records."""
    if current_user.role == "HR":
        return True
    if current_user.role == "Project Manager":
        return (
            evaluation.evaluated_by_id == current_user.id
            and not evaluation.is_finalized
        )
    return False


def _form_context(**overrides):
    ctx = dict(
        weeks=PMEvaluation.WEEKS,
        competencies=PMEvaluation.COMPETENCIES,
        recommendations=PMEvaluation.RECOMMENDATIONS,
        max_total=PMEvaluation.MAX_GRAND_TOTAL,
        today=today_pkt().isoformat(),
    )
    ctx.update(overrides)
    return ctx


def _can_view(evaluation: PMEvaluation) -> bool:
    if current_user.role in ("HR",):
        return True
    if current_user.role == "Project Manager":
        return evaluation.evaluated_by_id == current_user.id
    if current_user.role == "Intern":
        intern = getattr(current_user, "intern_profile", None)
        return bool(intern) and evaluation.intern_id == intern.id and evaluation.is_finalized
    return False


# ----------------------------------------------------------------------
# Listing (role-aware)
# ----------------------------------------------------------------------
@pm_evaluation_bp.route("/")
@login_required
@roles_required("HR", "Project Manager")
def list_evaluations():
    query = PMEvaluation.query

    if current_user.role == "Project Manager":
        query = query.filter_by(evaluated_by_id=current_user.id)
        interns = []
        pm = current_pm_profile()
        if pm:
            interns = sorted(
                {p.intern for p in _pm_projects_with_interns(pm)},
                key=lambda i: i.full_name,
            )
    else:
        intern_id = request.args.get("intern_id")
        pm_id = request.args.get("project_manager_id")
        status = request.args.get("status")

        if intern_id:
            query = query.filter_by(intern_id=intern_id)
        if pm_id:
            query = query.filter_by(project_manager_id=pm_id)
        if status == "finalized":
            query = query.filter_by(is_finalized=True)
        elif status == "draft":
            query = query.filter_by(is_finalized=False)

        interns = Intern.query.order_by(Intern.full_name).all()

    evaluations = query.order_by(PMEvaluation.created_at.desc()).all()
    project_managers = (
        ProjectManager.query.order_by(ProjectManager.full_name).all()
        if current_user.role == "HR"
        else []
    )

    return render_template(
        "pm_evaluations/list.html",
        evaluations=evaluations,
        interns=interns,
        project_managers=project_managers,
        filters=request.args,
    )


# ----------------------------------------------------------------------
# View a single evaluation
# ----------------------------------------------------------------------
@pm_evaluation_bp.route("/view/<int:evaluation_id>")
@login_required
def view_evaluation(evaluation_id):
    evaluation = PMEvaluation.query.get_or_404(evaluation_id)

    if not _can_view(evaluation):
        flash("You are not authorised to view that evaluation.", "danger")
        return redirect(url_for("dashboard.index"))

    return render_template(
        "pm_evaluations/view.html",
        evaluation=evaluation,
        can_edit=_can_edit(evaluation),
    )


# ----------------------------------------------------------------------
# Add a new evaluation (Project Manager only)
# ----------------------------------------------------------------------
@pm_evaluation_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required("Project Manager")
def add_evaluation():
    pm = current_pm_profile()
    if pm is None:
        flash("Your Project Manager profile could not be found.", "danger")
        return redirect(url_for("dashboard.index"))

    pm_projects = _pm_projects_with_interns(pm)

    if request.method == "POST":
        project_id = request.form.get("project_id")
        remarks = request.form.get("remarks", "").strip()
        recommendation = request.form.get("recommendation") or None

        errors = []
        project = next((p for p in pm_projects if str(p.id) == project_id), None)
        if project is None:
            errors.append("Please select one of your assigned interns/projects.")

        evaluation_date, date_errors = _parse_evaluation_date(request.form)
        errors.extend(date_errors)

        values, rating_errors = _parse_ratings(request.form)
        errors.extend(rating_errors)

        if recommendation and recommendation not in PMEvaluation.RECOMMENDATIONS:
            errors.append("Invalid recommendation selected.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "pm_evaluations/form.html",
                **_form_context(evaluation=None, projects=pm_projects, form=request.form),
            )

        try:
            evaluation = PMEvaluation(
                intern_id=project.assigned_intern_id,
                project_id=project.id,
                project_manager_id=pm.id,
                evaluated_by_id=current_user.id,
                evaluation_date=evaluation_date,
                remarks=remarks,
                recommendation=recommendation,
                **values,
            )
            db.session.add(evaluation)
            db.session.flush()
            log_action(
                action="CREATE",
                description=(
                    f"Submitted Project Manager Evaluation Form for "
                    f"'{project.intern.full_name}' ({project.title})."
                ),
                target_type="PMEvaluation",
                target_id=evaluation.id,
            )
            notify_user(
                project.intern.user_id,
                "A new Project Manager Evaluation Form has been started for you.",
                icon="bi-clipboard-check",
                notification_type="Evaluation Complete",
            )
            db.session.commit()
            flash("Evaluation form saved.", "success")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to save PM evaluation.")
            flash("Could not save the evaluation due to a system error. Please try again.", "danger")
            return render_template(
                "pm_evaluations/form.html",
                **_form_context(evaluation=None, projects=pm_projects, form=request.form),
            )

        return redirect(url_for("pm_evaluation.view_evaluation", evaluation_id=evaluation.id))

    return render_template(
        "pm_evaluations/form.html",
        **_form_context(evaluation=None, projects=pm_projects, form=None),
    )


# ----------------------------------------------------------------------
# Edit an existing evaluation
# ----------------------------------------------------------------------
@pm_evaluation_bp.route("/edit/<int:evaluation_id>", methods=["GET", "POST"])
@login_required
@roles_required("HR", "Project Manager")
def edit_evaluation(evaluation_id):
    evaluation = PMEvaluation.query.get_or_404(evaluation_id)

    if not _can_edit(evaluation):
        flash(
            "You are not authorised to edit that evaluation "
            "(it may already be finalized).",
            "danger",
        )
        return redirect(url_for("pm_evaluation.view_evaluation", evaluation_id=evaluation.id))

    # A PM only ever has one eligible project (the one already on the
    # record); HR can, in principle, re-point the record if needed, but
    # we keep intern/project fixed after creation to preserve the audit
    # trail and simply let them adjust ratings/remarks/recommendation.
    if request.method == "POST":
        remarks = request.form.get("remarks", "").strip()
        recommendation = request.form.get("recommendation") or None

        errors = []
        evaluation_date, date_errors = _parse_evaluation_date(request.form)
        errors.extend(date_errors)

        values, rating_errors = _parse_ratings(request.form)
        errors.extend(rating_errors)

        if recommendation and recommendation not in PMEvaluation.RECOMMENDATIONS:
            errors.append("Invalid recommendation selected.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "pm_evaluations/form.html",
                **_form_context(evaluation=evaluation, projects=[evaluation.project], form=request.form),
            )

        try:
            evaluation.evaluation_date = evaluation_date
            evaluation.remarks = remarks
            evaluation.recommendation = recommendation
            for field_name, value in values.items():
                setattr(evaluation, field_name, value)

            log_action(
                action="UPDATE",
                description=(
                    f"Updated Project Manager Evaluation Form for "
                    f"'{evaluation.intern.full_name}'."
                ),
                target_type="PMEvaluation",
                target_id=evaluation.id,
            )
            db.session.commit()
            flash("Evaluation updated.", "success")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update PM evaluation #%s.", evaluation_id)
            flash("Could not save changes due to a system error. Please try again.", "danger")
            return render_template(
                "pm_evaluations/form.html",
                **_form_context(evaluation=evaluation, projects=[evaluation.project], form=request.form),
            )

        return redirect(url_for("pm_evaluation.view_evaluation", evaluation_id=evaluation.id))

    return render_template(
        "pm_evaluations/form.html",
        **_form_context(evaluation=evaluation, projects=[evaluation.project], form=None),
    )


# ----------------------------------------------------------------------
# Finalize (Project Manager locks the form once complete; HR may also
# finalize on a PM's behalf if needed)
# ----------------------------------------------------------------------
@pm_evaluation_bp.route("/finalize/<int:evaluation_id>", methods=["POST"])
@login_required
@roles_required("HR", "Project Manager")
def finalize_evaluation(evaluation_id):
    evaluation = PMEvaluation.query.get_or_404(evaluation_id)

    if not _can_edit(evaluation):
        flash("You are not authorised to finalize that evaluation.", "danger")
        return redirect(url_for("pm_evaluation.view_evaluation", evaluation_id=evaluation.id))

    if not evaluation.is_complete:
        flash(
            "Every week and competency rating must be scored before this "
            "form can be finalized.",
            "warning",
        )
        return redirect(url_for("pm_evaluation.view_evaluation", evaluation_id=evaluation.id))

    try:
        evaluation.is_finalized = True
        evaluation.finalized_at = datetime.utcnow()
        log_action(
            action="UPDATE",
            description=f"Finalized Project Manager Evaluation Form for '{evaluation.intern.full_name}'.",
            target_type="PMEvaluation",
            target_id=evaluation.id,
        )
        notify_user(
            evaluation.intern.user_id,
            "Your Project Manager Evaluation Form has been finalized.",
            icon="bi-clipboard-check",
            notification_type="Evaluation Complete",
        )
        db.session.commit()
        flash("Evaluation finalized and locked.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to finalize PM evaluation #%s.", evaluation_id)
        flash("Could not finalize the evaluation due to a system error. Please try again.", "danger")

    return redirect(url_for("pm_evaluation.view_evaluation", evaluation_id=evaluation.id))


# ----------------------------------------------------------------------
# Delete (HR only)
# ----------------------------------------------------------------------
@pm_evaluation_bp.route("/delete/<int:evaluation_id>", methods=["POST"])
@login_required
@roles_required("HR")
def delete_evaluation(evaluation_id):
    evaluation = PMEvaluation.query.get_or_404(evaluation_id)
    intern_name = evaluation.intern.full_name if evaluation.intern else "Unknown"
    try:
        log_action(
            action="DELETE",
            description=f"Deleted Project Manager Evaluation Form for '{intern_name}'.",
            target_type="PMEvaluation",
            target_id=evaluation.id,
        )
        db.session.delete(evaluation)
        db.session.commit()
        flash("Evaluation deleted.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete PM evaluation #%s.", evaluation_id)
        flash("Could not delete the evaluation due to a system error. Please try again.", "danger")
    return redirect(url_for("pm_evaluation.list_evaluations"))


# ----------------------------------------------------------------------
# HR Export (CSV) -- respects the same filters as the list view
# ----------------------------------------------------------------------
@pm_evaluation_bp.route("/export/csv")
@login_required
@roles_required("HR")
def export_csv():
    query = PMEvaluation.query

    intern_id = request.args.get("intern_id")
    pm_id = request.args.get("project_manager_id")
    status = request.args.get("status")

    if intern_id:
        query = query.filter_by(intern_id=intern_id)
    if pm_id:
        query = query.filter_by(project_manager_id=pm_id)
    if status == "finalized":
        query = query.filter_by(is_finalized=True)
    elif status == "draft":
        query = query.filter_by(is_finalized=False)

    evaluations = query.order_by(PMEvaluation.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    header = (
        ["Intern", "Project", "Project Manager", "Evaluation Date"]
        + [f"Week {w['number']}" for w in PMEvaluation.WEEKS]
        + [label for _, label in PMEvaluation.COMPETENCIES]
        + ["Total Score", "Percentage", "Recommendation", "Status", "Remarks"]
    )
    writer.writerow(header)

    for e in evaluations:
        writer.writerow(
            [
                e.intern.full_name if e.intern else "",
                e.project.title if e.project else "",
                e.project_manager.full_name if e.project_manager else "",
                e.evaluation_date.strftime("%Y-%m-%d") if e.evaluation_date else "",
            ]
            + [s if s is not None else "" for s in e.week_scores]
            + [s if s is not None else "" for s in e.competency_scores]
            + [
                f"{e.total_score}/{e.MAX_GRAND_TOTAL}",
                f"{e.percentage}%",
                e.recommendation or "",
                "Finalized" if e.is_finalized else "Draft",
                (e.remarks or "").replace("\n", " "),
            ]
        )

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=pm_evaluations.csv"
    return response
