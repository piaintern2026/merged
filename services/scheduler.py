"""
services/scheduler.py
----------------------
Production-ready daily background job that automatically disables
intern accounts once their internship end date is reached.

This is intentionally decoupled from user activity: it does NOT rely
on a user logging in, an HR/Admin visiting a dashboard, or anyone
running a CLI command by hand. It runs on its own on a schedule.

Two complementary delivery mechanisms are provided so the same logic
works whether the app is deployed as a normal long-running process
(a dev server, Gunicorn/uWSGI behind Nginx, a VM, a Docker
container, Render/Railway/Heroku-style host, etc.) or as a
serverless deployment (Vercel) where no process stays alive between
requests:

1. In-process scheduler (APScheduler), started once when the Flask
   app boots. Used for every deployment that keeps a long-running
   Python process alive. Runs daily at DISABLE_JOB_HOUR:MINUTE
   (Asia/Karachi time, matching the rest of the app's `now_pkt()`)
   and also once shortly after startup so a host that only stays up
   for part of the day (or restarts daily) still gets a sweep.

2. HTTP cron endpoint (`routes/cron.py`), protected by a shared
   secret (`CRON_SECRET`), that an external scheduler can hit once a
   day. This is what makes it work on serverless platforms like
   Vercel, where `vercel.json` below is configured with a native
   Vercel Cron entry that calls this endpoint daily -- Vercel Cron
   invokes the URL itself, so there is no dependency on any user
   visiting the site. It also gives ops a way to run the sweep from
   any external uptime/cron service (cron-job.org, GitHub Actions
   scheduled workflow, system crontab + curl, etc.) regardless of
   platform.

Both paths call the exact same idempotent
`complete_expired_internships()` function, so running them together
(e.g. APScheduler AND an external cron hitting the HTTP endpoint) is
always safe -- an intern already marked "Completed" is simply
skipped on subsequent runs.
"""

import logging
import os
import threading

logger = logging.getLogger("pia_lms.scheduler")

# Hour/minute (24h, Asia/Karachi) the daily sweep runs at when using
# the in-process APScheduler. Configurable via env var so ops can
# move it to a quiet hour without a code change.
DISABLE_JOB_HOUR = int(os.environ.get("INTERN_DISABLE_JOB_HOUR", 1))
DISABLE_JOB_MINUTE = int(os.environ.get("INTERN_DISABLE_JOB_MINUTE", 0))

_scheduler_lock = threading.Lock()
_scheduler_started = False


def _run_job(app):
    """Wrapper that pushes an app context and runs the sweep, with
    logging and error isolation so a single failed run can never
    crash the scheduler thread or take down the web process."""
    from services.intern_lifecycle import complete_expired_internships

    with app.app_context():
        try:
            count = complete_expired_internships()
            logger.info(
                "[scheduler] Daily intern-expiry sweep complete: %d account(s) disabled.",
                count,
            )
        except Exception:
            logger.exception("[scheduler] Daily intern-expiry sweep failed.")


def init_scheduler(app):
    """Start the in-process daily scheduler exactly once per running
    process. Safe to call from create_app() on every worker/process --
    guarded so:

      - It never starts twice inside the same process (idempotent).
      - It is skipped entirely when explicitly disabled via env var
        (useful on Vercel, where a serverless function has no
        business trying to keep a background thread alive between
        invocations -- the HTTP cron endpoint is used there instead).
      - Under the Flask dev reloader, it only starts in the actual
        running child process, not the reloader's parent watcher
        process, so the job doesn't fire twice in dev.
    """
    global _scheduler_started

    if os.environ.get("DISABLE_IN_PROCESS_SCHEDULER", "").lower() in ("1", "true", "yes"):
        logger.info("[scheduler] In-process scheduler disabled via DISABLE_IN_PROCESS_SCHEDULER.")
        return None

    if os.environ.get("VERCEL"):
        # Serverless: no process stays alive between requests, so a
        # background thread here would never actually fire. Vercel
        # Cron -> routes/cron.py is the mechanism used instead (see
        # vercel.json "crons").
        logger.info("[scheduler] Running on Vercel -- using HTTP cron endpoint instead of in-process scheduler.")
        return None

    # Flask's debug reloader spawns a parent "watcher" process and a
    # real child process; WERKZEUG_RUN_MAIN is only set in the child.
    # If debug/reloader isn't in play at all (e.g. Gunicorn in prod),
    # this env var simply won't be set, and we proceed normally.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return None

    with _scheduler_lock:
        if _scheduler_started:
            return None

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            logger.warning(
                "[scheduler] APScheduler is not installed -- the in-process daily "
                "scheduler will NOT run. Install it (see requirements.txt) or rely "
                "on the /api/cron/complete-expired-internships HTTP endpoint with an "
                "external scheduler instead."
            )
            return None

        try:
            import pytz
            tz = pytz.timezone("Asia/Karachi")
        except ImportError:
            tz = None  # fall back to the scheduler's local/naive time

        scheduler = BackgroundScheduler(daemon=True, timezone=tz)
        scheduler.add_job(
            func=_run_job,
            args=[app],
            trigger=CronTrigger(hour=DISABLE_JOB_HOUR, minute=DISABLE_JOB_MINUTE),
            id="complete_expired_internships_daily",
            name="Disable intern accounts past their internship end date",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
        )
        scheduler.start()
        _scheduler_started = True

        logger.info(
            "[scheduler] In-process daily scheduler started (runs %02d:%02d Asia/Karachi).",
            DISABLE_JOB_HOUR, DISABLE_JOB_MINUTE,
        )

        # Also run once immediately at boot (in a background thread so
        # it never delays app startup / the first request) so a host
        # that isn't kept alive across the scheduled hour (e.g.
        # restarted daily by the platform, or only up during business
        # hours) still gets at least one sweep per boot.
        threading.Thread(target=_run_job, args=(app,), daemon=True).start()

        return scheduler
