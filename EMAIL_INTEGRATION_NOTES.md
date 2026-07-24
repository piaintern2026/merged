# Email Notification System - Integration Notes

## What was added (no existing functionality/UI/DB schema changed)

### New files
- `services/email_service.py` - central reusable email service (Flask-Mail based)
- `templates/emails/*.html` - 8 responsive PIA-branded HTML email templates
- `templates/auth/forgot_password.html`, `templates/auth/reset_password.html`
- `EMAIL_INTEGRATION_NOTES.md` (this file)

### Modified files (additive only)
- `requirements.txt` - added Flask-Mail, itsdangerous
- `extensions.py` - added shared `mail = Mail()` instance
- `config.py` - added MAIL_* / APP_BASE_URL / PASSWORD_RESET_TOKEN_MAX_AGE settings (env-driven)
- `app.py` - `mail.init_app(app)` + new `flask send-deadline-reminders` CLI command
- `.env` / `.env.example` - added SMTP config placeholders (MAIL_SUPPRESS_SEND=True by default,
  so nothing is actually sent until real SMTP credentials are filled in)
- `routes/intern.py` - welcome email + HR notification after intern registration commit
- `routes/project.py` - project assignment email at all 4 existing assignment commit points
- `routes/rotation.py` - rotation email + HR notification after rotation commit
- `routes/project_manager.py` - account activation/deactivation email on existing toggle route
- `routes/intern_portal.py` - internship completion + HR notification on first Final Report submission
- `routes/auth.py` - added NEW `forgot_password` / `reset_password` routes (password reset didn't
  exist before; the login page already had a dead "Forgot Password?" link which is now wired up)

## How it works
1. Every email call happens strictly AFTER `db.session.commit()` succeeds.
2. `services/email_service.py` never raises - failures are caught and logged via
   `current_app.logger.exception(...)`, so a broken SMTP server never breaks a user-facing request.
3. Emails are sent on a background thread (with app context) so the request isn't blocked.
4. All 8 templates extend `templates/emails/base_email.html` for a consistent PIA-branded look
   (uses the same green/gold palette as `static/css/style.css`).

## Deadline reminders
No existing route polls for deadlines, so this is implemented as a new Flask CLI command:
```
flask send-deadline-reminders
```
Run it from a daily cron job / Windows Task Scheduler / cloud scheduler. It emails the assigned
intern and Project Manager for any open project that's overdue or due within 3 days.

## Setup
1. `pip install -r requirements.txt`
2. Fill in `MAIL_USERNAME` / `MAIL_PASSWORD` (and other MAIL_* vars) in `.env`
3. Set `MAIL_SUPPRESS_SEND=False` once real SMTP credentials are set
4. For Gmail: use a 16-character App Password, not your real password
