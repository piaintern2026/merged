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
from utils import roles_required, current_pm_profile, notify_user, log_action, hr_city_scope
from services.email_service import send_evaluation_email

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
@roles_required("Station HR", "Project Manager", "Super Admin")
def list_evaluations():
    """HR sees every evaluation with filters; a PM sees only the ones
    they personally submitted."""

    query = Evaluation.query
    interns_query = Intern.query.order_by(db.func.lower(Intern.full_name))

    if current_user.role == "Project Manager":
        query = query.filter_by(evaluated_by_id=current_user.id)
    else:
        is_city_scoped, hr_city = hr_city_scope()
        if is_city_scoped:
            query = query.join(Intern, Evaluation.intern_id == Intern.id).filter(Intern.station == hr_city)
            interns_query = interns_query.filter(Intern.station == hr_city)

        # HR-only filters via query string
        intern_id = request.args.get("intern_id")
        evaluation_type = request.args.get("evaluation_type")

        if intern_id:
            query = query.filter(Evaluation.intern_id == intern_id)
        if evaluation_type:
            query = query.filter(Evaluation.evaluation_type == evaluation_type)

    evaluations = query.order_by(Evaluation.created_at.desc()).all()
    interns = interns_query.all()

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
@roles_required("Station HR", "Project Manager", "Super Admin")
def view_evaluation(evaluation_id):
    """Show the full criteria breakdown for one evaluation."""
    evaluation = Evaluation.query.get_or_404(evaluation_id)

    if current_user.role == "Project Manager" and evaluation.evaluated_by_id != current_user.id:
        flash("You can only view evaluations you submitted.", "danger")
        return redirect(url_for("evaluation.list_evaluations"))

    is_city_scoped, hr_city = hr_city_scope()
    if (
        current_user.role == "Station HR"
        and is_city_scoped
        and evaluation.intern
        and evaluation.intern.station != hr_city
    ):
        flash("You do not have permission to view evaluations outside your assigned city.", "danger")
        return redirect(url_for("evaluation.list_evaluations"))

    return render_template("evaluations/view.html", evaluation=evaluation)


# ----------------------------------------------------------------------
# Add a new evaluation (PM -> "Project Manager" type; HR -> "HR Final")
# ----------------------------------------------------------------------
@evaluation_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required("Station HR", "Project Manager", "Super Admin")
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
            {i for p in pm_projects for i in p.interns},
            key=lambda i: i.full_name,
        )
        evaluation_type = "Project Manager"
    else:
        eligible_interns = Intern.query.order_by(db.func.lower(Intern.full_name)).all()
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
                (p for p in pm_projects if p.has_intern(intern.id)), None
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
            send_evaluation_email(intern, existing or evaluation, evaluated_by_name=current_user.display_name())
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
# Disable / Enable (soft delete -- HR only, keeps evaluation records
# tamper-proof for PMs while preserving history for audit purposes)
# ----------------------------------------------------------------------
@evaluation_bp.route("/toggle-status/<int:evaluation_id>", methods=["POST"])
@login_required
@roles_required("Station HR", "Super Admin")
def toggle_evaluation_status(evaluation_id):
    """HR/Super Admin can disable an incorrect evaluation record instead
    of permanently deleting it, so the record stays in the database for
    audit history and can be re-enabled if needed."""
    evaluation = Evaluation.query.get_or_404(evaluation_id)
    is_city_scoped, hr_city = hr_city_scope()
    if is_city_scoped and evaluation.intern and evaluation.intern.station != hr_city:
        flash("You do not have permission to manage evaluations outside your assigned city.", "danger")
        return redirect(url_for("evaluation.list_evaluations"))
    intern_name = evaluation.intern.full_name if evaluation.intern else "Unknown"
    try:
        evaluation.is_active = not evaluation.is_active
        state = "enabled" if evaluation.is_active else "disabled"
        log_action(
            action="UPDATE",
            description=f"{evaluation.evaluation_type} evaluation for '{intern_name}' {state}.",
            target_type="Evaluation",
            target_id=evaluation.id,
        )
        db.session.commit()
        flash(f"Evaluation {state}.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to toggle status for evaluation #%s.", evaluation_id)
        flash("Could not update the evaluation's status due to a system error. Please try again.", "danger")
    return redirect(url_for("evaluation.list_evaluations"))
