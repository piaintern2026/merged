"""
routes/cron.py
----------------
HTTP entry point for the daily "disable expired interns" job on
serverless deployments (e.g. Vercel), where no process stays alive
in the background so services/scheduler.py's in-process APScheduler
never gets a chance to fire.

An external scheduler (Vercel Cron -- see the "crons" section of
vercel.json, or any other daily cron/uptime service) calls this
endpoint once a day. It is intentionally NOT tied to any user
visiting a dashboard or logging in -- it is invoked by the platform
itself on a timer, independent of traffic.

Authentication: a shared secret (CRON_SECRET env var) must be
supplied either as `?token=...` or an `Authorization: Bearer ...`
header, so this endpoint can't be used by a random visitor to spam
the audit log or (harmlessly, since the job is idempotent) trigger
extra runs. If CRON_SECRET isn't configured, the endpoint refuses to
run rather than silently operating unauthenticated.
"""

import os

from flask import Blueprint, jsonify, request

cron_bp = Blueprint("cron", __name__, url_prefix="/api/cron")


def _authorized() -> bool:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        return False

    token = request.args.get("token")
    if token and token == secret:
        return True

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:] == secret:
        return True

    # Vercel Cron sends this header on requests it triggers itself.
    if request.headers.get("x-vercel-cron") and os.environ.get("VERCEL"):
        return True

    return False


@cron_bp.route("/complete-expired-internships", methods=["GET", "POST"])
def complete_expired_internships_endpoint():
    """Runs the same idempotent sweep as `flask
    complete-expired-internships` / the in-process scheduler. Safe to
    call more than once a day -- interns already marked "Completed"
    are simply skipped."""
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401

    from services.intern_lifecycle import complete_expired_internships

    count = complete_expired_internships()
    return jsonify({"status": "ok", "accounts_disabled": count}), 200
