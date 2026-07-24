"""
routes/evaluation.py
---------------------
Module 4: Evaluation Module. A Project Manager scores interns assigned
to their own projects on six criteria; HR can submit one authoritative
"HR Final" evaluation per intern. The total score is always calculated
automatically from the criteria (see Evaluation.total_score).
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user

from extensions import db
from models import Evaluation, Intern, Project
from utils import roles_required, current_pm_profile, notify_user, log_action

evaluation_bp = Blueprint("evaluation", __name__, url_prefix="/evaluations")


def _validate_scores(form) -> tuple[dict, list[str]]:
    """
    Parse and validate the six 1-10 criterion scores from a submitted
    form. Returns (scores_dict, errors_list) -- shared by both the PM
    and HR evaluation forms since they use identical fields.
    """
    scores = {}
    errors = []

    for field_name, label in Evaluation.CRITERIA:
        raw_value = form.get(field_name, "")
        try:
            value = int(raw_value)
            if value < 1 or value > Evaluation.MAX_PER_CRITERION:
                errors.append(f"{label} must be between 1 and {Evaluation.MAX_PER_CRITERION}.")
            else:
                scores[field_name] = value
        except (TypeError, ValueError):
            errors.append(f"{label} must be a whole number.")

    return scores, errors


# ----------------------------------------------------------------------
# Listing (role-aware)
# ----------------------------------------------------------------------
@evaluation_bp.route("/")
@login_required
@roles_required("HR", "Project Manager")
def list_evaluations():
    """HR sees every evaluation with filters; a PM sees only the ones
    they personally submitted."""

    query = Evaluation.query

    if current_user.role == "Project Manager":
        query = query.filter_by(evaluated_by_id=current_user.id)
    else:
        # HR-only filters via query string
        intern_id = request.args.get("intern_id")
        evaluation_type = request.args.get("evaluation_type")

        if intern_id:
            query = query.filter_by(intern_id=intern_id)
        if evaluation_type:
            query = query.filter_by(evaluation_type=evaluation_type)

    evaluations = query.order_by(Evaluation.created_at.desc()).all()
    interns = Intern.query.order_by(Intern.full_name).all()

    return render_template(
        "evaluations/list.html",
        evaluations=evaluations,
        interns=interns,
        evaluation_types=Evaluation.EVALUATION_TYPES,
        filters=request.args,
    )


# ----------------------------------------------------------------------
# View a single evaluation's full breakdown
# ----------------------------------------------------------------------
@evaluation_bp.route("/view/<int:evaluation_id>")
@login_required
@roles_required("HR", "Project Manager")
def view_evaluation(evaluation_id):
    """Show the full criteria breakdown for one evaluation."""
    evaluation = Evaluation.query.get_or_404(evaluation_id)

    if current_user.role == "Project Manager" and evaluation.evaluated_by_id != current_user.id:
        flash("You can only view evaluations you submitted.", "danger")
        return redirect(url_for("evaluation.list_evaluations"))

    return render_template("evaluations/view.html", evaluation=evaluation)


# ----------------------------------------------------------------------
# Add a new evaluation (PM -> "Project Manager" type; HR -> "HR Final")
# ----------------------------------------------------------------------
@evaluation_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required("HR", "Project Manager")
def add_evaluation():
    """Submit a new evaluation. The intern list and resulting
    evaluation_type differ by role."""

    if current_user.role == "Project Manager":
        pm = current_pm_profile()
        if pm is None:
            flash("Your Project Manager profile could not be found.", "danger")
            return redirect(url_for("dashboard.index"))

        # A PM may only evaluate interns currently assigned to one of
        # their own projects.
        pm_projects = Project.query.filter_by(assigned_manager_id=pm.id).all()
        eligible_interns = sorted(
            {p.intern for p in pm_projects if p.intern is not None},
            key=lambda i: i.full_name,
        )
        evaluation_type = "Project Manager"
    else:
        eligible_interns = Intern.query.order_by(Intern.full_name).all()
        evaluation_type = "HR Final"

    if request.method == "POST":
        intern_id = request.form.get("intern_id")
        remarks = request.form.get("remarks", "").strip()

        errors = []
        intern = None
        if not intern_id:
            errors.append("Please select an intern to evaluate.")
        else:
            intern = next((i for i in eligible_interns if str(i.id) == intern_id), None)
            if intern is None:
                errors.append("You are not authorised to evaluate that intern.")

        scores, score_errors = _validate_scores(request.form)
        errors.extend(score_errors)

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "evaluations/form.html",
                interns=eligible_interns,
                evaluation_type=evaluation_type,
                criteria=Evaluation.CRITERIA,
                form=request.form,
            )

        # Determine which project this evaluation relates to (for a PM,
        # the project linking them to this intern).
        project_id = None
        if current_user.role == "Project Manager":
            related_project = next(
                (p for p in pm_projects if p.assigned_intern_id == intern.id), None
            )
            project_id = related_project.id if related_project else None

        # HR Final evaluations are singular per intern: update in place
        # if one already exists, matching the Feedback/FinalReport pattern.
        existing = None
        if evaluation_type == "HR Final":
            existing = Evaluation.query.filter_by(
                intern_id=intern.id, evaluation_type="HR Final"
            ).first()

        try:
            if existing:
                for field_name, _ in Evaluation.CRITERIA:
                    setattr(existing, field_name, scores[field_name])
                existing.remarks = remarks
                log_action(
                    action="UPDATE",
                    description=f"Updated {evaluation_type} evaluation for '{intern.full_name}'.",
                    target_type="Evaluation",
                    target_id=existing.id,
                )
                flash(f"HR Final evaluation for {intern.full_name} updated.", "success")
            else:
                evaluation = Evaluation(
                    intern_id=intern.id,
                    evaluated_by_id=current_user.id,
                    project_id=project_id,
                    evaluation_type=evaluation_type,
                    remarks=remarks,
                    **scores,
                )
                db.session.add(evaluation)
                db.session.flush()
                log_action(
                    action="CREATE",
                    description=f"Submitted {evaluation_type} evaluation for '{intern.full_name}'.",
                    target_type="Evaluation",
                    target_id=evaluation.id,
                )
                flash(f"Evaluation for {intern.full_name} submitted successfully.", "success")

            # Let the intern know a new evaluation was recorded.
            notify_user(
                intern.user_id,
                f"A new {evaluation_type} evaluation has been recorded for you.",
                icon="bi-clipboard-check",
                notification_type="Evaluation Complete",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Failed to save evaluation for intern #%s.", intern.id if intern else None
            )
            flash("Could not save the evaluation due to a system error. Please try again.", "danger")
            return render_template(
                "evaluations/form.html",
                interns=eligible_interns,
                evaluation_type=evaluation_type,
                criteria=Evaluation.CRITERIA,
                form=request.form,
            )

        return redirect(url_for("evaluation.list_evaluations"))

    return render_template(
        "evaluations/form.html",
        interns=eligible_interns,
        evaluation_type=evaluation_type,
        criteria=Evaluation.CRITERIA,
        form=None,
    )


# ----------------------------------------------------------------------
# Delete (HR only, keeps evaluation records tamper-proof for PMs)
# ----------------------------------------------------------------------
@evaluation_bp.route("/delete/<int:evaluation_id>", methods=["POST"])
@login_required
@roles_required("HR")
def delete_evaluation(evaluation_id):
    """HR can remove an incorrect evaluation record."""
    evaluation = Evaluation.query.get_or_404(evaluation_id)
    intern_name = evaluation.intern.full_name if evaluation.intern else "Unknown"
    try:
        log_action(
            action="DELETE",
            description=f"Deleted {evaluation.evaluation_type} evaluation for '{intern_name}'.",
            target_type="Evaluation",
            target_id=evaluation.id,
        )
        db.session.delete(evaluation)
        db.session.commit()
        flash("Evaluation deleted.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete evaluation #%s.", evaluation_id)
        flash("Could not delete the evaluation due to a system error. Please try again.", "danger")
    return redirect(url_for("evaluation.list_evaluations"))
