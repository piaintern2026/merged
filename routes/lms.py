"""
routes/lms.py
--------------
Learning Management System (LMS) module.

This currently exposes only a "Coming Soon" placeholder page that is
reachable by all four roles (Admin, Station HR, Project Manager,
Intern) once logged in. The route/blueprint is intentionally kept
separate and minimal so the real LMS views can be added here later
without touching navigation or other routes -- the URL (/lms) and
endpoint name (lms.index) will stay the same.
"""

from flask import Blueprint, render_template
from flask_login import login_required

lms_bp = Blueprint("lms", __name__, url_prefix="/lms")


@lms_bp.route("/")
@login_required
def index():
    """Render the LMS landing page (placeholder until the module ships)."""
    return render_template("lms/index.html")
