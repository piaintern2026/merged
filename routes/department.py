"""
routes/department.py
---------------------
Full CRUD for the Department entity. Only HR may manage departments.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Department, SubDepartment
from utils import (
    roles_required,
    log_action,
    PIA_CITIES,
    DEPARTMENT_HIERARCHY,
    hr_city_scope,
)

department_bp = Blueprint("department", __name__, url_prefix="/departments")


SORT_OPTIONS = {
    "name_asc": (Department.name.asc(),),
    "name_desc": (Department.name.desc(),),
    "city_asc": (Department.city.asc(), Department.name.asc()),
    "city_desc": (Department.city.desc(), Department.name.asc()),
    "newest": (Department.created_at.desc(),),
}


@department_bp.route("/")
@login_required
@roles_required("Station HR", "Admin")
def list_departments():
    """Show all departments, with an optional city filter and sort order
    (the organization has departments/interns spread across multiple cities)."""
    city = request.args.get("city", "").strip()
    status = request.args.get("status", "").strip()
    search = request.args.get("q", "").strip()
    sort = request.args.get("sort", "newest")
    if sort not in SORT_OPTIONS:
        sort = "newest"
    year = request.args.get("year", type=int)

    is_city_scoped, hr_city = hr_city_scope()

    query = Department.query
    if is_city_scoped:
        # A Station HR is always confined to their own assigned city,
        # regardless of any ?city= query string they might pass.
        query = query.filter(Department.city == hr_city)
    elif city:
        query = query.filter(Department.city == city)
    if status == "active":
        query = query.filter(Department.is_active.is_(True))
    elif status == "disabled":
        query = query.filter(Department.is_active.is_(False))
    if search:
        like = f"%{search}%"
        query = query.filter(Department.name.ilike(like))
    if year:
        from sqlalchemy import extract
        query = query.filter(extract("year", Department.created_at) == year)

    departments = query.order_by(*SORT_OPTIONS[sort]).all()

    from sqlalchemy import extract
    available_years = sorted(
        {
            y[0]
            for y in db.session.query(extract("year", Department.created_at)).distinct().all()
            if y[0] is not None
        },
        reverse=True,
    )

    return render_template(
        "departments/list.html",
        departments=departments,
        cities=[hr_city] if is_city_scoped and hr_city else PIA_CITIES,
        selected_city=hr_city if is_city_scoped else city,
        selected_status=status,
        selected_search=search,
        selected_sort=sort,
        available_years=available_years,
        selected_year=year,
    )


@department_bp.route("/api/sub-departments")
@department_bp.route("/api/divisions-sections")
@login_required
def api_sub_departments():
    """Cascading dropdown endpoint: given ?department_id=<id>, return the
    active Divisions/Sections belonging to it as JSON. Used by every
    searchable Department -> Division/Section picker in the system
    (Intern Registration, Project Creation, Project Assignment,
    Filters, Reports) so the division/section options always update
    dynamically to match the selected department.

    Registered under both /api/sub-departments (legacy, kept so no
    existing bookmarked/cached URL breaks) and /api/divisions-sections
    (current name) -- both return the identical payload, which also
    includes the "divisions_sections" key as an alias of
    "sub_departments" for forward-compatibility.
    """
    department_id = request.args.get("department_id", type=int)
    if not department_id:
        return jsonify({"sub_departments": [], "divisions_sections": []})

    sub_departments = (
        SubDepartment.query.filter_by(department_id=department_id, is_active=True)
        .order_by(db.func.lower(SubDepartment.name))
        .all()
    )
    return jsonify(
        {
            "sub_departments": [
                {"id": sd.id, "name": sd.name} for sd in sub_departments
            ],
            "divisions_sections": [
                {"id": sd.id, "name": sd.name} for sd in sub_departments
            ],
        }
    )


@department_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required("Admin")
def add_department():
    """Create a new department. Admin only."""

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        city = request.form.get("city", "").strip()
        # Division/Section is entirely optional here -- Admin can
        # create a department on its own and add sub-departments later
        # (via Edit, or they get auto-seeded from DEPARTMENT_HIERARCHY
        # below). No validation is applied to this field on purpose.
        sub_department_name = request.form.get("sub_department", "").strip()

        # Validation
        if not name:
            flash("Department is required.", "danger")
            return render_template(
                "departments/form.html", department=None, cities=PIA_CITIES,
            )

        if not city:
            flash("City is required.", "danger")
            return render_template(
                "departments/form.html", department=None, cities=PIA_CITIES,
            )

        if city not in PIA_CITIES:
            flash("Please select a valid city.", "danger")
            return render_template(
                "departments/form.html", department=None, cities=PIA_CITIES,
            )

        # Prevent duplicate department names within the same city
        # (case-insensitive, so "IT" and "it" in Karachi collide too).
        existing = Department.query.filter(
            db.func.lower(Department.name) == name.lower(),
            Department.city == city,
        ).first()

        if existing:
            flash(
                "A department with this name already exists in this city.",
                "danger"
            )
            return render_template(
                "departments/form.html",
                department=None,
                cities=PIA_CITIES,
            )

        try:
            department = Department(
                name=name,
                city=city,
                is_active=True
            )

            db.session.add(department)
            db.session.flush()

            # Seed this department's Divisions/Sections from the fixed
            # hierarchy so the cascading dropdown has options for it
            # immediately, without waiting for the next app restart.
            for sub_name in DEPARTMENT_HIERARCHY.get(name, []):
                db.session.add(SubDepartment(name=sub_name, department_id=department.id))

            # If the user optionally typed/selected a Division/Section on
            # the Add Department form, create it too -- unless a
            # sub-department with that name was already seeded above
            # from DEPARTMENT_HIERARCHY (case-insensitive match), in
            # which case there's nothing to add.
            if sub_department_name:
                already_seeded = any(
                    sub_department_name.lower() == sub_name.lower()
                    for sub_name in DEPARTMENT_HIERARCHY.get(name, [])
                )
                if not already_seeded:
                    db.session.add(
                        SubDepartment(name=sub_department_name, department_id=department.id)
                    )

            # Temporarily disabled because of audit_logs issue
            # log_action(
            #     action="CREATE",
            #     description=f"Created department '{name}'.",
            #     target_type="Department",
            #     target_id=department.id,
            # )

            db.session.commit()

            flash(
                f"Department '{name}' created successfully.",
                "success"
            )
            return redirect(url_for("department.list_departments"))

        except IntegrityError as e:
            db.session.rollback()
            current_app.logger.exception(
                "Database error while creating department '%s': %s",
                name,
                e
            )
            flash(
                f"Database error: {str(e)}",
                "danger"
            )

        except Exception as e:
            db.session.rollback()
            current_app.logger.exception(
                "Failed to create department '%s': %s",
                name,
                e
            )
            flash(
                f"System error: {str(e)}",
                "danger"
            )

    return render_template(
        "departments/form.html",
        department=None,
        cities=PIA_CITIES,
    )


@department_bp.route("/edit/<int:department_id>", methods=["GET", "POST"])
@login_required
@roles_required("Admin")
def edit_department(department_id):
    """Edit an existing department: name, city, and status. Admin
    only. The department text field pre-fills the currently saved
    value; validation mirrors add_department()."""
    department = Department.query.get_or_404(department_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        city = request.form.get("city", "").strip()
        is_active = request.form.get("is_active") == "on"

        if not name:
            flash("Department is required.", "danger")
            return render_template(
                "departments/form.html", department=department, cities=PIA_CITIES,
            )

        if not city:
            flash("City is required.", "danger")
            return render_template(
                "departments/form.html", department=department, cities=PIA_CITIES,
            )

        if city not in PIA_CITIES:
            flash("Please select a valid city.", "danger")
            return render_template(
                "departments/form.html", department=department, cities=PIA_CITIES,
            )

        # Prevent duplicate department names within the same city
        # (case-insensitive), excluding this department itself.
        existing = Department.query.filter(
            db.func.lower(Department.name) == name.lower(),
            Department.city == city,
            Department.id != department.id,
        ).first()

        if existing:
            flash(
                "A department with this name already exists in this city.",
                "danger"
            )
            return render_template(
                "departments/form.html", department=department, cities=PIA_CITIES,
            )

        try:
            department.name = name
            department.city = city
            department.is_active = is_active
            db.session.flush()

            # If the department was renamed to a different hierarchy
            # entry, make sure that entry's Divisions/Sections now exist
            # for it too (existing sub-department rows/links are never
            # removed, so nothing already assigned is lost).
            existing_sub_names = {sd.name for sd in department.sub_departments}
            for sub_name in DEPARTMENT_HIERARCHY.get(name, []):
                if sub_name not in existing_sub_names:
                    db.session.add(SubDepartment(name=sub_name, department_id=department.id))

            log_action(
                action="UPDATE",
                description=f"Updated department '{name}'.",
                target_type="Department",
                target_id=department.id,
            )

            db.session.commit()

            flash(
                f"Department '{name}' updated successfully.",
                "success"
            )
            return redirect(url_for("department.list_departments"))

        except IntegrityError as e:
            db.session.rollback()
            current_app.logger.exception(
                "Database error while updating department '%s': %s",
                name,
                e
            )
            flash(
                f"Database error: {str(e)}",
                "danger"
            )

        except Exception as e:
            db.session.rollback()
            current_app.logger.exception(
                "Failed to update department '%s': %s",
                name,
                e
            )
            flash(
                f"System error: {str(e)}",
                "danger"
            )

    return render_template(
        "departments/form.html",
        department=department,
        cities=PIA_CITIES,
    )


@department_bp.route("/toggle-status/<int:department_id>", methods=["POST"])
@login_required
@roles_required("Admin")



def toggle_department_status(department_id):
    """Disable or re-enable a department (soft delete). Existing PMs,
    interns and projects linked to it are preserved; a disabled
    department is simply hidden from "Add" pickers for new records."""
    department = Department.query.get_or_404(department_id)

    try:
        department.is_active = not department.is_active
        state = "enabled" if department.is_active else "disabled"
        log_action(
            action="UPDATE",
            description=f"Department '{department.name}' {state}.",
            target_type="Department",
            target_id=department.id,
        )
        db.session.commit()
        flash(f"Department '{department.name}' has been {state}.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to toggle status for department #%s.", department_id)
        flash("Could not update the department's status due to a system error. Please try again.", "danger")

    return redirect(url_for("department.list_departments"))

def _department_has_linked_records(department):
    """True if anything still references this department, in which case
    it must not be permanently deleted (only disabled)."""
    if department.project_managers or department.interns:
        return True
    if department.sub_departments:
        return True
    from models import Project
    if Project.query.filter(Project.department_id == department.id).first():
        return True
    from models.rotation import InternRotation
    if InternRotation.query.filter(
        db.or_(
            InternRotation.from_department_id == department.id,
            InternRotation.to_department_id == department.id,
        )
    ).first():
        return True
    return False


@department_bp.route("/delete/<int:department_id>", methods=["POST"])
@login_required
@roles_required("Admin")
def delete_department(department_id):
    """Permanently delete a department. Admin only -- unlike
    toggle_department_status (soft delete/disable), this removes the
    row (and its Divisions/Sections, via cascade) from the database for
    good. Blocked if any Project Manager, Intern, Project or Rotation
    still references this department, since that would either orphan
    those records or violate a foreign key constraint."""
    department = Department.query.get_or_404(department_id)

    if _department_has_linked_records(department):
        flash(
            f"Cannot permanently delete '{department.name}': it still has "
            "Project Managers, Interns, Projects, Divisions/Sections, or "
            "Rotations linked to it. Disable it instead, or "
            "reassign/remove those records first.",
            "danger",
        )
        return redirect(url_for("department.list_departments"))

    try:
        name = department.name
        log_action(
            action="DELETE",
            description=f"Permanently deleted department '{name}'.",
            target_type="Department",
            target_id=department.id,
        )
        db.session.delete(department)
        db.session.commit()
        flash(f"Department '{name}' has been permanently deleted.", "success")
    except IntegrityError as e:
        db.session.rollback()
        current_app.logger.exception(
            "Database error while permanently deleting department #%s: %s",
            department_id, e,
        )
        flash(
            "Could not permanently delete this department because other "
            "records still reference it.", "danger",
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to permanently delete department #%s.", department_id
        )
        flash(
            "Could not permanently delete the department due to a system "
            "error. Please try again.", "danger",
        )

    return redirect(url_for("department.list_departments"))
