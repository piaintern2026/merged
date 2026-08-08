"""
routes/messages.py
--------------------
Module: Enterprise in-app communication between an Intern and their
Project Manager. Internal-only (no email integration) -- two users
exchange direct Message rows, and each new message also creates a
Notification for the recipient so it surfaces through the existing
navbar bell/badge/notification-center, instead of building a second,
duplicate alerting mechanism.

Scoping:
  - A Project Manager may message any Intern currently assigned to
    them (same "scoped interns" definition used across the PM module:
    assigned via an owned project, or via the current rotation).
  - An Intern may message the Project Manager currently supervising
    them (derived the same way, from their assigned project or
    current rotation).
"""

from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required, current_user

from extensions import db
from models import Message, Project, Intern, ProjectManager
from utils import roles_required, current_pm_profile, current_intern_profile, notify_user

message_bp = Blueprint("message", __name__, url_prefix="/messages")


# ------------------------------------------------------------------------
# Contact scoping helpers
# ------------------------------------------------------------------------
def _pm_contacts(pm):
    """Interns this Project Manager is allowed to message: assigned via
    one of their own projects, or via the intern's current rotation."""
    projects = Project.query.filter_by(assigned_manager_id=pm.id).all()
    intern_ids = {i.id for p in projects for i in p.interns}
    rotation_ids = {
        i.id for i in Intern.query.all() if i.current_manager and i.current_manager.id == pm.id
    }
    all_ids = intern_ids | rotation_ids
    return (
        Intern.query.filter(Intern.id.in_(all_ids)).order_by(db.func.lower(Intern.full_name)).all()
        if all_ids
        else []
    )


def _intern_contact(intern):
    """The single Project Manager this Intern is allowed to message,
    derived from their assigned project (preferred) or their current
    rotation. None if no manager is linked yet."""
    project = (
        Project.query.filter(Project.interns.any(id=intern.id))
        .order_by(Project.created_at.desc())
        .first()
    )
    if project and project.assigned_manager_id:
        return ProjectManager.query.get(project.assigned_manager_id)
    return intern.current_manager


def _current_contacts():
    """Return (contacts list, role) for the logged-in user -- either the
    Project Manager's list of scoped Interns, or the Intern's single PM
    wrapped in a list. Empty list + None role if neither profile applies."""
    pm = current_pm_profile()
    if pm is not None:
        return _pm_contacts(pm), "pm"

    intern = current_intern_profile()
    if intern is not None:
        contact = _intern_contact(intern)
        return ([contact] if contact else []), "intern"

    return [], None


# ------------------------------------------------------------------------
# Inbox: list of conversations, one row per contact
# ------------------------------------------------------------------------
@message_bp.route("/")
@login_required
@roles_required("Project Manager", "Intern")
def inbox():
    contacts, role = _current_contacts()

    conversations = []
    for contact in contacts:
        other_user_id = contact.user_id
        last_message = (
            Message.query.filter(
                db.or_(
                    db.and_(Message.sender_id == current_user.id, Message.receiver_id == other_user_id),
                    db.and_(Message.sender_id == other_user_id, Message.receiver_id == current_user.id),
                )
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        unread_count = Message.query.filter_by(
            sender_id=other_user_id, receiver_id=current_user.id, is_read=False
        ).count()
        conversations.append(
            {
                "contact": contact,
                "last_message": last_message,
                "unread_count": unread_count,
            }
        )

    # Most recent activity first; contacts with no messages yet go last.
    conversations.sort(
        key=lambda c: c["last_message"].created_at if c["last_message"] else datetime.min,
        reverse=True,
    )

    return render_template("messages/inbox.html", conversations=conversations, role=role)


# ------------------------------------------------------------------------
# Conversation thread with one contact
# ------------------------------------------------------------------------
@message_bp.route("/<int:intern_id_or_pm_id>", methods=["GET", "POST"])
@login_required
@roles_required("Project Manager", "Intern")
def conversation(intern_id_or_pm_id):
    contacts, role = _current_contacts()
    contact = next((c for c in contacts if c and c.id == intern_id_or_pm_id), None)

    if contact is None:
        flash("You can only message people currently assigned to you.", "danger")
        return redirect(url_for("message.inbox"))

    other_user_id = contact.user_id

    if request.method == "POST":
        body = request.form.get("body", "").strip()
        if not body:
            flash("Message cannot be empty.", "danger")
            return redirect(url_for("message.conversation", intern_id_or_pm_id=intern_id_or_pm_id))

        try:
            msg = Message(sender_id=current_user.id, receiver_id=other_user_id, body=body)
            db.session.add(msg)

            sender_label = current_user.display_name()
            notify_user(
                other_user_id,
                f"New message from {sender_label}",
                icon="bi-chat-dots",
                notification_type="Message",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Failed to send message from user #%s to user #%s.", current_user.id, other_user_id
            )
            flash("Could not send the message due to a system error. Please try again.", "danger")

        return redirect(url_for("message.conversation", intern_id_or_pm_id=intern_id_or_pm_id))

    # Mark every message received from this contact as read on open.
    try:
        Message.query.filter_by(
            sender_id=other_user_id, receiver_id=current_user.id, is_read=False
        ).update({"is_read": True})
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to mark messages as read for user #%s.", current_user.id)

    thread = (
        Message.query.filter(
            db.or_(
                db.and_(Message.sender_id == current_user.id, Message.receiver_id == other_user_id),
                db.and_(Message.sender_id == other_user_id, Message.receiver_id == current_user.id),
            )
        )
        .order_by(Message.created_at.asc())
        .all()
    )

    return render_template(
        "messages/conversation.html", contact=contact, thread=thread, role=role
    )


# ------------------------------------------------------------------------
# Lightweight JSON endpoint for the inbox unread badge (mirrors the
# navbar bell's api_recent pattern in routes/notification.py).
# ------------------------------------------------------------------------
@message_bp.route("/api/unread-count")
@login_required
@roles_required("Project Manager", "Intern")
def api_unread_count():
    unread_count = Message.query.filter_by(receiver_id=current_user.id, is_read=False).count()
    return jsonify({"unread_count": unread_count})
