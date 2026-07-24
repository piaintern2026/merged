"""
routes/auth.py
---------------
Handles authentication: login, logout, session management and
password change. Password hashing is handled inside the User model.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db
from models import User
from utils import log_action
from services.email_service import send_password_reset_email, verify_password_reset_token

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Display login form and authenticate the user."""

    # If already logged in, go straight to the dashboard.
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        # Basic validation
        if not email or not password:
            flash("Please enter both email and password.", "danger")
            return render_template("auth/login.html")

        user = User.query.filter_by(email=email).first()

        if user is None or not user.check_password(password):
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html")

        if not user.is_active_account:
            flash("This account has been deactivated. Contact HR.", "danger")
            return render_template("auth/login.html")

        login_user(user, remember=remember)
        try:
            log_action(
                action="LOGIN",
                description=f"{user.display_name()} logged in.",
                target_type="User",
                target_id=user.id,
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to record login for user #%s.", user.id)
        flash(f"Welcome back, {user.display_name()}!", "success")

        # Respect "next" redirect target if present and safe.
        next_page = request.args.get("next")
        return redirect(next_page or url_for("dashboard.index"))

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    """Log the current user out and end their session."""
    try:
        log_action(
            action="LOGOUT",
            description=f"{current_user.display_name()} logged out.",
            target_type="User",
            target_id=current_user.id,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to record logout for user #%s.", current_user.id)
    logout_user()
    flash("You have been logged out successfully.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """Allow the logged-in user to change their own password."""

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        # Validation
        if not current_user.check_password(current_password):
            flash("Current password is incorrect.", "danger")
        elif len(new_password) < 8:
            flash("New password must be at least 8 characters long.", "danger")
        elif new_password != confirm_password:
            flash("New password and confirmation do not match.", "danger")
        else:
            try:
                current_user.set_password(new_password)
                log_action(
                    action="UPDATE",
                    description=f"{current_user.display_name()} changed their password.",
                    target_type="User",
                    target_id=current_user.id,
                )
                db.session.commit()
                flash("Password changed successfully.", "success")
                return redirect(url_for("dashboard.index"))
            except Exception:
                db.session.rollback()
                current_app.logger.exception(
                    "Failed to change password for user #%s.", current_user.id
                )
                flash("Could not change the password due to a system error. Please try again.", "danger")

    return render_template("auth/change_password.html")


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """
    Request a password reset link by email. Always shows the same
    success message regardless of whether the email exists, so the
    form can't be used to enumerate registered accounts.
    """
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if not email:
            flash("Please enter your email address.", "danger")
            return render_template("auth/forgot_password.html")

        user = User.query.filter_by(email=email).first()
        if user and user.is_active_account:
            # Email sending never raises and never blocks this request
            # (see services/email_service.py); the DB has nothing to
            # commit here since no rows change.
            send_password_reset_email(user)

        flash(
            "If an account exists for that email, a password reset link has been sent.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Complete a password reset using the signed, time-limited token
    emailed by forgot_password()."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    payload = verify_password_reset_token(token)
    if not payload:
        flash("This password reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.forgot_password"))

    user = User.query.get(payload.get("user_id"))
    if not user or user.email != payload.get("email"):
        flash("This password reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(new_password) < 8:
            flash("New password must be at least 8 characters long.", "danger")
        elif new_password != confirm_password:
            flash("New password and confirmation do not match.", "danger")
        else:
            try:
                user.set_password(new_password)
                log_action(
                    action="UPDATE",
                    description=f"{user.display_name()} reset their password via email link.",
                    target_type="User",
                    target_id=user.id,
                )
                db.session.commit()
                flash("Your password has been reset successfully. Please log in.", "success")
                return redirect(url_for("auth.login"))
            except Exception:
                db.session.rollback()
                current_app.logger.exception(
                    "Failed to reset password for user #%s.", user.id
                )
                flash("Could not reset the password due to a system error. Please try again.", "danger")

    return render_template("auth/reset_password.html", token=token)
