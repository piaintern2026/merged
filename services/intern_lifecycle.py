"""
services/intern_lifecycle.py
-----------------------------
Module 2: automatically closes out an intern's account on their last
internship day.

`Intern.effective_status` (models/intern.py) already *displays* an
intern whose `internship_end_date` has passed as "Completed" even if
nobody has touched the record -- but that's a read-only computed
property, so nothing was actually persisted or enforced: the stored
`internship_status` stayed "Active" and the linked login account
stayed enabled.

`complete_expired_internships()` below is the single source of truth
that turns that computed state into a real, persisted one:

  - Sets Intern.internship_status = "Completed" (only for interns that
    are still "Active" and whose end date has passed -- an internship
    ended early via "End Internship", i.e. already "Ended", is left
    alone).
  - Deactivates the linked User account (is_active_account = False),
    which the existing login check in routes/auth.py already uses to
    block sign-in -- no changes needed there.
  - Never deletes or hides any historical record: attendance, leave,
    submissions, evaluations, reports, etc. all keep pointing at the
    same Intern/User rows, so reporting and analytics are unaffected.

Safe to call repeatedly (it only acts on interns still "Active" past
their end date, so re-running is a no-op for anyone already handled).
Two ways this gets triggered:

  1. Automatically, opportunistically, right before login is allowed
     (see routes/auth.py) -- so the very first login attempt on or
     after the last day is blocked, without needing any external
     scheduler.
  2. On a schedule, via `flask complete-expired-internships`
     (see app.py), for deployments that do have a cron/scheduled task
     runner and want this swept proactively rather than only at login.
"""

from extensions import db
from models import Intern, User
from utils import today_pkt, notify_user, log_action


def complete_expired_internships() -> int:
    """Find every still-"Active" intern whose internship_end_date has
    passed, mark them Completed, and disable their login account.
    Returns the number of interns processed."""
    today = today_pkt()

    expired = (
        Intern.query.join(User, Intern.user_id == User.id)
        .filter(
            Intern.internship_status == "Active",
            Intern.internship_end_date < today,
        )
        .all()
    )

    processed = 0
    for intern in expired:
        intern.internship_status = "Completed"
        user = intern.user
        if user and user.is_active_account:
            user.is_active_account = False
            notify_user(
                user.id,
                "Your internship has ended and your account has been marked Completed. "
                "Contact HR if you believe this is a mistake.",
                icon="bi-check-circle",
                notification_type="Internship Completed",
            )
        log_action(
            action="UPDATE",
            description=(
                f"Automatically marked internship for '{intern.full_name}' as Completed "
                "and disabled the account (internship end date reached)."
            ),
            target_type="Intern",
            target_id=intern.id,
        )
        processed += 1

    if processed:
        db.session.commit()

    return processed


def complete_expired_internship_for(intern) -> bool:
    """Single-intern variant of complete_expired_internships(), used
    opportunistically at login time (routes/auth.py) so an individual
    account gets closed out the moment its end date is reached, even
    between scheduled sweeps. Returns True if the intern was just
    completed by this call."""
    if intern is None:
        return False
    if intern.internship_status != "Active":
        return False
    if not intern.internship_end_date or intern.internship_end_date >= today_pkt():
        return False

    intern.internship_status = "Completed"
    user = intern.user
    if user and user.is_active_account:
        user.is_active_account = False
    log_action(
        action="UPDATE",
        description=(
            f"Automatically marked internship for '{intern.full_name}' as Completed "
            "and disabled the account (internship end date reached)."
        ),
        target_type="Intern",
        target_id=intern.id,
    )
    db.session.commit()
    return True
