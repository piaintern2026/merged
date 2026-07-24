"""
config.py
---------
Central configuration for the PIA Intern Management System.
Keeping configuration in one place makes it easy to merge this module
with future modules of the same application without conflicts.
"""

import os

# Base directory of the project (absolute path)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Base configuration shared by the whole application."""

    # Secret key used to sign session cookies / CSRF tokens.
    # In production this MUST be set via an environment variable.
    SECRET_KEY = os.environ.get("PIA_SECRET_KEY", "pia-intern-system-dev-secret-key-2026")

    # Database connection.
    # In production, set DATABASE_URL to your Neon connection string, e.g.:
    #   postgresql://<user>:<password>@<endpoint>.neon.tech/<dbname>?sslmode=require
    # If DATABASE_URL is not set, falls back to a local SQLite file for dev.
    _db_url = os.environ.get("DATABASE_URL")
    if _db_url:
        # SQLAlchemy 2.x / newer psycopg2 expect "postgresql://", but Neon/Heroku
        # style URLs sometimes start with "postgres://" - normalize it.
        if _db_url.startswith("postgres://"):
            _db_url = _db_url.replace("postgres://", "postgresql://", 1)
        SQLALCHEMY_DATABASE_URI = _db_url
    else:
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(BASE_DIR, "pia.db")

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ------------------------------------------------------------------
    # Engine options.
    #
    # WITHOUT these, a serverless/managed Postgres host such as Neon
    # silently closes idle connections after a few minutes. SQLAlchemy
    # then hands a *dead* connection to the next request, and psycopg2
    # blocks on that dead socket with no timeout - this is what was
    # making the whole app (including completely unrelated pages such
    # as "Check Submission Link") appear to freeze after any period of
    # inactivity: the worker got stuck waiting on a broken connection
    # and could no longer serve any other request.
    #
    #   - pool_pre_ping: cheaply validates a pooled connection (a real
    #     round-trip) before handing it to a request, transparently
    #     reconnecting if it's gone stale.
    #   - pool_recycle: proactively discards/reconnects connections
    #     older than this, comfortably inside Neon's idle-close window.
    #   - connect_args: fails fast instead of hanging forever if the
    #     server is genuinely unreachable, and enables TCP keepalives so
    #     a silently-dropped connection is detected instead of hanging.
    if _db_url:
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "pool_recycle": 280,
            "connect_args": {
                "connect_timeout": 10,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
            },
        }
    else:
        # SQLite (local dev fallback) doesn't understand the psycopg2
        # connect_args above, but still benefits from pre-ping/recycle.
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "pool_recycle": 280,
        }

    # Folder where uploaded profile pictures are stored.
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "profile_pics")
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

    # Folder where intern portal submissions (Final Internship Report)
    # are stored. Organised into subfolders per type so files never
    # collide across the different upload categories.
    SUBMISSIONS_UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "submissions")
    ALLOWED_DOCUMENT_EXTENSIONS = {"pdf"}

    MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # 20 MB max upload size (project ZIPs can be large)

    # Default page size for the paginated admin list views (Module 5).
    ITEMS_PER_PAGE = 15

    # Default HR account, auto-created on first run.
    DEFAULT_HR_EMAIL = "hr@piac.com"
    DEFAULT_HR_PASSWORD = "piacl@2026"
    DEFAULT_HR_ROLE = "HR"

    # Default Super Admin account, auto-created on first run.
    DEFAULT_SUPER_ADMIN_USERNAME = "piaadmin"
    DEFAULT_SUPER_ADMIN_EMAIL = "piaadmin@piac.com"
    DEFAULT_SUPER_ADMIN_PASSWORD = "piacl@2026"
    DEFAULT_SUPER_ADMIN_ROLE = "Super Admin"

    # ------------------------------------------------------------------
    # Email Notification System (Flask-Mail)
    # ------------------------------------------------------------------
    # All SMTP credentials live in environment variables (.env) and are
    # never hard-coded. Sensible defaults are provided so the app still
    # boots in dev even if .env doesn't define every mail var yet -
    # in that case MAIL_SUPPRESS_SEND stays True and emails are simply
    # logged instead of sent (see services/email_service.py).
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "True").lower() in ("true", "1", "yes")
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "False").lower() in ("true", "1", "yes")
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = (
        os.environ.get("MAIL_DEFAULT_SENDER_NAME", "PIA Intern Management System"),
        os.environ.get("MAIL_DEFAULT_SENDER_EMAIL", os.environ.get("MAIL_USERNAME", "no-reply@piac.com")),
    )
    # Automatically suppress real SMTP sending unless a username/password
    # was actually configured (or the developer explicitly overrides
    # this). Emails are still fully rendered and logged in this mode -
    # useful for local development without real SMTP credentials.
    MAIL_SUPPRESS_SEND = os.environ.get(
        "MAIL_SUPPRESS_SEND",
        "False" if os.environ.get("MAIL_USERNAME") else "True",
    ).lower() in ("true", "1", "yes")

    # Public base URL used to build absolute links inside emails
    # (password reset links, "open in app" buttons, etc.).
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000")

    # How long a password-reset link stays valid, in seconds.
    PASSWORD_RESET_TOKEN_MAX_AGE = int(os.environ.get("PASSWORD_RESET_TOKEN_MAX_AGE", 3600))
