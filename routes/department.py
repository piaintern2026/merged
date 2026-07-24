"""
routes/department.py
---------------------
Full CRUD for the Department entity. Only HR may manage departments.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Department
from utils import roles_required, log_action

department_bp = Blueprint("department", __name__, url_prefix="/departments")


SORT_OPTIONS = {
    "name_asc": (Department.name.asc(),),
    "name_desc": (Department.name.desc(),),
    "city_asc": (Department.city.asc(), Department.name.asc()),
    "city_desc": (Department.city.desc(), Department.name.asc()),
    "status_asc": (Department.status.asc(), Department.name.asc()),
    "status_desc": (Department.status.desc(), Department.name.asc()),
}


@department_bp.route("/")
@login_required
@roles_required("HR")
def list_departments():
    """Show all departments, with an optional city filter and sort order
    (PIA has departments/interns spread across multiple cities)."""
    city = request.args.get("city", "").strip()
    sort = request.args.get("sort", "name_asc")
    if sort not in SORT_OPTIONS:
        sort = "name_asc"

    query = Department.query
    if city:
        query = query.filter(Department.city == city)

    departments = query.order_by(*SORT_OPTIONS[sort]).all()

    # Distinct, non-empty city list for the filter dropdown.
    cities = [
        c[0]
        for c in db.session.query(Department.city)
        .filter(Department.city.isnot(None), Department.city != "")
        .distinct()
        .order_by(Department.city.asc())
        .all()
    ]

    return render_template(
        "departments/list.html",
        departments=departments,
        cities=cities,
        selected_city=city,
        selected_sort=sort,
    )


@department_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def add_department():
    """Create a new department."""

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        city = request.form.get("city", "").strip()
        status = request.form.get("status", "Active")

        # Validation
        if not name:
            flash("Department name is required.", "danger")
            return render_template("departments/form.html", department=None)

        if not city:
            flash("City is required.", "danger")
            return render_template("departments/form.html", department=None)

        if Department.query.filter_by(name=name).first():
            flash("A department with this name already exists.", "danger")
            return render_template("departments/form.html", department=None)

        try:
            department = Department(name=name, description=description, city=city, status=status)
            db.session.add(department)
            db.session.flush()
            log_action(
                action="CREATE",
                description=f"Created department '{name}'.",
                target_type="Department",
                target_id=department.id,
            )
            db.session.commit()
            flash(f"Department '{name}' created successfully.", "success")
            return redirect(url_for("department.list_departments"))
        except IntegrityError:
            db.session.rollback()
            flash("Could not create department due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to create department '%s'.", name)
            flash("Could not create department due to a system error. Please try again.", "danger")

    return render_template("departments/form.html", department=None)


@department_bp.route("/edit/<int:department_id>", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def edit_department(department_id):
    """Edit an existing department."""
    department = Department.query.get_or_404(department_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        city = request.form.get("city", "").strip()
        status = request.form.get("status", "Active")

        if not name:
            flash("Department name is required.", "danger")
            return render_template("departments/form.html", department=department)

        if not city:
            flash("City is required.", "danger")
            return render_template("departments/form.html", department=department)

        # Ensure uniqueness excluding the current record.
        existing = Department.query.filter(
            Department.name == name, Department.id != department_id
        ).first()
        if existing:
            flash("A department with this name already exists.", "danger")
            return render_template("departments/form.html", department=department)

        try:
            department.name = name
            department.description = description
            department.city = city
            department.status = status
            log_action(
                action="UPDATE",
                description=f"Updated department '{name}'.",
                target_type="Department",
                target_id=department.id,
            )
            db.session.commit()
            flash(f"Department '{name}' updated successfully.", "success")
            return redirect(url_for("department.list_departments"))
        except IntegrityError:
            db.session.rollback()
            flash("Could not update department due to a database error.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update department #%s.", department_id)
            flash("Could not update department due to a system error. Please try again.", "danger")

    return render_template("departments/form.html", department=department)


@department_bp.route("/delete/<int:department_id>", methods=["POST"])
@login_required
@roles_required("Super Admin")
def delete_department(department_id):
    """Delete a department (blocked if it still has PMs/interns assigned)."""
    department = Department.query.get_or_404(department_id)

    if department.project_managers or department.interns:
        flash(
            "Cannot delete this department while Project Managers or Interns "
            "are still assigned to it.",
            "danger",
        )
        return redirect(url_for("department.list_departments"))

    try:
        dept_name = department.name
        dept_id = department.id
        db.session.delete(department)
        log_action(
            action="DELETE",
            description=f"Deleted department '{dept_name}'.",
            target_type="Department",
            target_id=dept_id,
        )
        db.session.commit()
        flash(f"Department '{dept_name}' deleted successfully.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Could not delete department due to a database error.", "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete department #%s.", department_id)
        flash("Could not delete department due to a system error. Please try again.", "danger")

    return redirect(url_for("department.list_departments"))
