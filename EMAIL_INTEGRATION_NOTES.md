# Email Notification System - Integration Notes

## What was added (no existing functionality/UI/DB schema changed)

### New files
- `services/email_service.py` - central reusable email service (Flask-Mail based)
- `templates/emails/*.html` - 8 responsive on-brand HTML email templates
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
4. All 8 templates extend `templates/emails/base_email.html` for a consistent branded look
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

---

## Update (this pass): full event coverage, dynamic SMTP settings, logging, announcements

### New files
- `models/email_log.py` - `EmailLog` model: persists every send attempt (recipient, subject,
  event/template, status `Sent|Failed|Suppressed`, error message) for the Email Logs table.
- `models/announcement.py` - `Announcement` model for the broadcast Announcements feature.
- `templates/emails/staff_account.html` - HR/PM/Admin account creation & password reset email.
- `templates/emails/leave_status.html` - leave submitted / approved / rejected / cancelled.
- `templates/emails/attendance_alert.html` - Late/Absent attendance alerts.
- `templates/emails/report_submission.html` - project link / report submitted to PM/HR.
- `templates/emails/evaluation_complete.html` - HR/PM/6-week evaluation recorded.
- `templates/emails/announcement.html` - broadcast announcement email.
- `templates/emails/test_email.html` - Email Settings "Send Test Email" confirmation.
- `templates/admin/email_settings.html` - Admin SMTP config + test email + log viewer.
- `templates/admin/announcements.html` - post/view announcements.

### Modified files
- `services/email_service.py` - delivery now goes through `smtplib` directly instead of
  Flask-Mail, resolving SMTP host/port/TLS/SSL/credentials/sender/suppress-send from
  `SystemSetting` (keys `mail_*`) at send time, falling back to the `MAIL_*` env vars in
  `config.py` if no override is saved. Every attempt writes an `EmailLog` row
  (`Sent`/`Failed`/`Suppressed`). Added `send_staff_account_email`, `send_leave_status_email`,
  `send_attendance_alert_email`, `send_report_submission_email`, `send_evaluation_email`,
  `send_announcement_email`, and `send_test_email` (synchronous, used by the settings page).
- `models/system_setting.py` - added `EMAIL_DEFAULTS` (the `mail_*` keys) seeded on startup.
- `app.py` - seeds `mail_*` SystemSetting rows from `.env`/`config.py` on first run.
- `routes/admin.py` - `add_user` and `reset_user_password` now email the username + temporary
  password; new `email_settings` / `email_settings_test` / `announcements` routes.
- `routes/project_manager.py` - `add_pm` now emails the new PM's credentials.
- `routes/leave.py` - submit/approve/reject/cancel each send the matching status email.
- `routes/evaluation.py`, `routes/pm_evaluation.py` - evaluation submission emails the intern.
- `routes/attendance.py`, `routes/intern_portal.py` - Late (clock-in) and Absent/Late (manual
  edit) attendance emails; project-link submission emails the assigned PM.
- `templates/components/sidebar.html` - "Email Settings" (Admin) and "Announcements"
  (Admin + Station HR) nav links.

### How SMTP configuration works now
1. `config.py` still reads `MAIL_SERVER` / `MAIL_PORT` / `MAIL_USERNAME` / `MAIL_PASSWORD` /
   etc. from the environment - nothing is hard-coded.
2. On first run those values seed a `SystemSetting` row per field (`mail_server`, `mail_port`,
   `mail_use_tls`, `mail_use_ssl`, `mail_username`, `mail_password`,
   `mail_default_sender_name`, `mail_default_sender_email`, `mail_suppress_send`).
3. A Admin can then edit these live from **Email Settings** without redeploying; the
   saved value always wins over the environment variable at send time.
4. "Suppress Sending (Test Mode)" renders and logs every email without actually contacting an
   SMTP server - the safe default until real credentials are entered.

### Logging & reliability
- Every send happens strictly AFTER `db.session.commit()` succeeds in the calling route.
- `_deliver()` never raises out of its background thread; failures are caught, logged via
  `app.logger.exception`, and recorded in `EmailLog` so a broken SMTP server never breaks a
  user-facing request - visible on the Email Settings page instead of only in server logs.
- Emails render synchronously (so a broken template is caught pre-send) but dispatch to SMTP
  on a daemon thread, so a slow/unreachable mail server never blocks a page load.

### Reused notification system
Every important in-app `notify_user(...)` call for the events above is paired with the
matching `send_*_email(...)` call right after `db.session.commit()`, so the same event
produces both the bell-icon notification and the outbound email from one place in the route -
no separate/duplicate alerting mechanism was introduced.
