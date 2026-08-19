"""
services/reminders.py
----------------------
Generates in-app HR reminder notifications for three categories that
have no other trigger point in the app (unlike e.g. leave approvals
or rotations, which already notify immediately when they happen):

  - Overdue projects: still open past their deadline.
  - Pending evaluations: interns nearing the end of their internship
    (or already past it) with no HR Final evaluation on record.
  - Upcoming rotations: interns who have been in their current
    department/manager stint for a long time (default 45+ days) and
    have no scheduled rotation yet, since this app has no separate
    "scheduled future rotation" concept -- rotations happen the day
    HR performs them.

Safe to call repeatedly (e.g. from a daily cron job / CLI command, or
an HR-triggered "Send Reminders" button): each reminder is only
created once per target per day, by checking for an existing
unread/recent notification with the same message before inserting a
new one.
"""

from datetime import timedelta

from extensions import db
from models import Project, Intern, Evaluation, User, Notification
from utils import today_pkt, notify_user, log_action

# Interns whose current department stint has run this many days or
# more without a new rotation are flagged as "due for rotation review".
ROTATION_DUE_DAYS = 45

# Interns within this many days of their internship end date (or past
# it) with no HR Final evaluation are flagged as needing one.
EVALUATION_DUE_WINDOW_DAYS = 14


def _already_notified_today(user_id: int, message: str) -> bool:
    """True if this exact message was already sent to this user today,
    so re-running the generator doesn't spam duplicate reminders."""
    since = today_pkt()
    return (
        Notification.query.filter(
            Notification.user_id == user_id,
            Notification.message == message,
            db.func.date(Notification.created_at) == since,
        ).first()
        is not None
    )


def _hr_user_ids() -> list[int]:
    return [u.id for u in User.query.filter_by(role="Station HR", is_active_account=True).all()]


def generate_overdue_project_reminders() -> int:
    """Notify HR (and each project's assigned PM) about every project
    that is currently overdue. Returns the number of reminders sent."""
    sent = 0
    overdue_projects = [p for p in Project.query.all() if p.is_overdue()]
    hr_ids = _hr_user_ids()

    for project in overdue_projects:
        message = f"Project '{project.title}' is overdue (deadline {project.deadline.strftime('%d %b %Y')})."
        for hr_id in hr_ids:
            if not _already_notified_today(hr_id, message):
                notify_user(user_id=hr_id, message=message, icon="bi-exclamation-triangle", notification_type="Project Deadline")
                sent += 1
        if project.manager and project.manager.user_id:
            if not _already_notified_today(project.manager.user_id, message):
                notify_user(
                    user_id=project.manager.user_id,
                    message=message,
                    icon="bi-exclamation-triangle",
                    notification_type="Project Deadline",
                )
                sent += 1
    return sent


def generate_pending_evaluation_reminders() -> int:
    """Notify HR about interns nearing/past their internship end date
    who still have no HR Final evaluation on record."""
    sent = 0
    today = today_pkt()
    window_end = today + timedelta(days=EVALUATION_DUE_WINDOW_DAYS)
    hr_ids = _hr_user_ids()

    candidates = Intern.query.filter(Intern.internship_end_date <= window_end).all()
    for intern in candidates:
        if intern.effective_status == "Ended":
            continue
        has_final = Evaluation.query.filter_by(intern_id=intern.id, evaluation_type="HR Final").first()
        if has_final:
            continue
        message = f"'{intern.full_name}' has no HR Final evaluation yet (internship ends {intern.internship_end_date.strftime('%d %b %Y')})."
        for hr_id in hr_ids:
            if not _already_notified_today(hr_id, message):
                notify_user(user_id=hr_id, message=message, icon="bi-clipboard-check", notification_type="Evaluation Complete")
                sent += 1
    return sent


def generate_upcoming_rotation_reminders() -> int:
    """Notify HR about interns who have been in their current
    department stint for a long time without a rotation, flagging
    them as due for a rotation review."""
    sent = 0
    today = today_pkt()
    hr_ids = _hr_user_ids()

    for intern in Intern.query.all():
        if intern.effective_status != "Active":
            continue
        current = intern.current_rotation
        stint_start = current.start_date if current else intern.internship_start_date
        days_in_stint = (today - stint_start).days
        if days_in_stint < ROTATION_DUE_DAYS:
            continue
        message = (
            f"'{intern.full_name}' has been in {intern.department.name} for {days_in_stint} days "
            f"-- consider a rotation review."
        )
        for hr_id in hr_ids:
            if not _already_notified_today(hr_id, message):
                notify_user(user_id=hr_id, message=message, icon="bi-arrow-left-right", notification_type="General")
                sent += 1
    return sent


def generate_all_hr_reminders() -> dict:
    """Run every reminder generator and commit the results in one
    transaction. Returns a breakdown of how many of each were sent."""
    counts = {
        "overdue_projects": generate_overdue_project_reminders(),
        "pending_evaluations": generate_pending_evaluation_reminders(),
        "upcoming_rotations": generate_upcoming_rotation_reminders(),
    }
    try:
        log_action(
            action="REMINDERS",
            description=(
                f"Generated HR reminders: {counts['overdue_projects']} overdue project, "
                f"{counts['pending_evaluations']} pending evaluation, "
                f"{counts['upcoming_rotations']} upcoming rotation notification(s)."
            ),
        )
    except RuntimeError:
        # log_action reads current_user, which needs a request context.
        # When this runs from the CLI (no request in flight), skip the
        # audit entry rather than crash the whole reminder run.
        pass
    db.session.commit()
    return counts
