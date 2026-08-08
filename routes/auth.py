"""
routes/auth.py
---------------
Handles authentication: login, logout, session management and
password change. Password hashing is handled inside the User model.
"""

import random

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db
from models import User
from utils import log_action
from services.email_service import send_password_reset_email, verify_password_reset_token

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _new_captcha():
    """Generate a fresh, simple arithmetic CAPTCHA and store the answer
    session-side only (never in the database, never in the HTML). Call
    this on every GET of the login page and after every failed login
    attempt (wrong CAPTCHA or wrong credentials) so a submitted/expired
    challenge can never be reused."""
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    session["captcha_answer"] = a + b
    session["captcha_question"] = f"{a} + {b}"
    return session["captcha_question"]


def _verify_captcha(submitted: str) -> bool:
    """Validate the submitted CAPTCHA answer against the session-stored
    value. Session-based, so nothing CAPTCHA-related ever touches the
    database."""
    expected = session.get("captcha_answer")
    if expected is None:
        return False
    try:
        return int(str(submitted).strip()) == int(expected)
    except (TypeError, ValueError):
        return False


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Display login form and authenticate the user. A CAPTCHA answer is
    required and validated server-side before any credential check runs
    -- a wrong/missing CAPTCHA fails the request the same way wrong
    credentials would, without revealing which was actually wrong."""

    # If already logged in, go straight to the dashboard.
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))
        captcha_answer = request.form.get("captcha_answer", "")

        # CAPTCHA is mandatory and checked first, server-side, against
        # the session-stored answer -- never the database. It is
        # regenerated below on every single failure path (CAPTCHA wrong
        # or credentials wrong) so a challenge can never be reused for a
        # second, automated attempt.
        if not _verify_captcha(captcha_answer):
            _new_captcha()
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html", captcha_question=session["captcha_question"])

        # Basic validation
        if not email or not password:
            _new_captcha()
            flash("Please enter both email and password.", "danger")
            return render_template("auth/login.html", captcha_question=session["captcha_question"])

        user = User.query.filter_by(email=email).first()

        if user is None or not user.check_password(password):
            if user is not None:
                # Track failed attempts for the Super Admin's account-security view.
                try:
                    user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception(
                        "Failed to record a failed login attempt for user #%s.", user.id
                    )
            _new_captcha()
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html", captcha_question=session["captcha_question"])

        if user.is_locked:
            _new_captcha()
            flash("This account has been locked. Contact your Super Admin.", "danger")
            return render_template("auth/login.html", captcha_question=session["captcha_question"])

        # Module 2 automation: an intern whose last internship day has
        # been reached gets auto-completed and their account disabled
        # right here, opportunistically, so this login attempt is the
        # one that enforces it even if no scheduled sweep has run yet
        # (see services/intern_lifecycle.py).
        if user.role == "Intern" and user.intern_profile is not None:
            from services.intern_lifecycle import complete_expired_internship_for

            complete_expired_internship_for(user.intern_profile)

        if not user.is_active_account:
            _new_captcha()
            flash("This account has been deactivated. Contact HR.", "danger")
            return render_template("auth/login.html", captcha_question=session["captcha_question"])

        # Successful login: the CAPTCHA has served its purpose, clear it
        # so it can't be replayed.
        session.pop("captcha_answer", None)
        session.pop("captcha_question", None)

        login_user(user, remember=remember)
        try:
            user.failed_login_attempts = 0
            user.last_login_at = None  # set below via now_pkt to avoid an extra import cycle
            from utils import now_pkt

            user.last_login_at = now_pkt()
            user.last_login_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
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

        if user.force_password_reset:
            flash("Your account requires a password reset before continuing.", "warning")
            return redirect(url_for("auth.change_password"))

        flash(f"Welcome back, {user.display_name()}!", "success")

        # Respect "next" redirect target if present and safe.
        next_page = request.args.get("next")
        return redirect(next_page or url_for("dashboard.index"))

    _new_captcha()
    return render_template("auth/login.html", captcha_question=session["captcha_question"])


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
                current_user.force_password_reset = False
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
