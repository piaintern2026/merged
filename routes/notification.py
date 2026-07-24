"""
routes/notification.py
------------------------
Module 5: Notification Center. Every role gets a full page to browse,
filter, and manage their own notifications (the Intern Dashboard's
inline panel from Module 3 remains as a quick-glance widget; this is
the complete, paginated view linked from the navbar bell for everyone).
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user

from extensions import db
from models import Notification
from utils import paginate_query

notification_bp = Blueprint("notification", __name__, url_prefix="/notifications")


def _relative_time(dt):
    """Return a short, human-friendly relative timestamp like the
    navbar dropdown needs (e.g. '5m ago', '3h ago', '2d ago')."""
    from utils import now_pkt

    now = now_pkt()
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    delta = now - dt
    seconds = delta.total_seconds()

    if seconds < 60:
        return "Just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours}h ago"
    days = int(hours // 24)
    if days < 7:
        return f"{days}d ago"
    return dt.strftime("%d %b %Y")


@notification_bp.route("/api/recent")
@login_required
def api_recent():
    """JSON feed of the current user's most recent notifications, used
    by the navbar bell dropdown. Read-only; never mutates state."""
    recent = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(8)
        .all()
    )
    unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()

    return jsonify(
        {
            "unread_count": unread_count,
            "notifications": [
                {
                    "id": n.id,
                    "message": n.message,
                    "icon": n.icon,
                    "notification_type": n.notification_type,
                    "is_read": n.is_read,
                    "time_ago": _relative_time(n.created_at),
                }
                for n in recent
            ],
        }
    )


@notification_bp.route("/api/mark-read/<int:notification_id>", methods=["POST"])
@login_required
def api_mark_read(notification_id):
    """AJAX variant of mark_read used by the navbar dropdown: returns
    JSON instead of redirecting, without changing the existing
    form-based route's behaviour."""
    notification = Notification.query.get_or_404(notification_id)
    if notification.user_id != current_user.id:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    try:
        notification.is_read = True
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to mark notification #%s as read.", notification_id
        )
        return jsonify({"success": False, "error": "A system error occurred."}), 500

    unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({"success": True, "unread_count": unread_count})


@notification_bp.route("/")
@login_required
def list_notifications():
    """Paginated list of the current user's notifications, filterable
    by type and read/unread status."""
    query = Notification.query.filter_by(user_id=current_user.id)

    notification_type = request.args.get("notification_type")
    status = request.args.get("status")  # 'unread' or 'read'

    if notification_type:
        query = query.filter_by(notification_type=notification_type)
    if status == "unread":
        query = query.filter_by(is_read=False)
    elif status == "read":
        query = query.filter_by(is_read=True)

    query = query.order_by(Notification.created_at.desc())

    page = request.args.get("page", 1, type=int)
    pagination = paginate_query(query, page)

    unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()

    return render_template(
        "notifications/list.html",
        pagination=pagination,
        notifications=pagination.items,
        notification_types=Notification.NOTIFICATION_TYPES,
        unread_count=unread_count,
        filters=request.args,
    )


@notification_bp.route("/mark-read/<int:notification_id>", methods=["POST"])
@login_required
def mark_read(notification_id):
    """Mark a single notification as read (owner only)."""
    notification = Notification.query.get_or_404(notification_id)
    if notification.user_id != current_user.id:
        flash("You can only manage your own notifications.", "danger")
        return redirect(url_for("notification.list_notifications"))

    try:
        notification.is_read = True
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to mark notification #%s as read.", notification_id
        )
        flash("Could not update the notification due to a system error.", "danger")
    return redirect(request.referrer or url_for("notification.list_notifications"))


@notification_bp.route("/mark-all-read", methods=["POST"])
@login_required
def mark_all_read():
    """Mark every notification belonging to the current user as read."""
    try:
        Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
        db.session.commit()
        flash("All notifications marked as read.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to mark all notifications as read for user #%s.", current_user.id
        )
        flash("Could not update notifications due to a system error.", "danger")
    return redirect(request.referrer or url_for("notification.list_notifications"))

