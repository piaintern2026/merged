# PIA Intern Management System

A full-stack Flask web application for Pakistan International Airlines to manage the
complete internship lifecycle: HR administration, project assignment, attendance,
performance evaluation, an Intern self-service portal, analytics, reporting, internship
completion certificates, and system administration — all backed by a single SQLite
database.

Built with **Python 3.12**, **Flask**, **SQLAlchemy**, **Flask-Login**, **Bootstrap 5**,
**Jinja2**, **Chart.js**, **ReportLab**, and **OpenPyXL**, following an MVC architecture.

---

## Quick Start

```bash
# 1. Create and activate a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
python app.py
```

The app starts at **http://localhost:5000**. On first run it automatically:

- Creates `pia.db` (SQLite) and every table.
- Seeds the default **HR** account.
- Seeds default system settings (organization name, HR contact email, etc.).

No further setup is required — the application is fully self-initializing.

### Default Login

| Field    | Value              |
|----------|--------------------|
| Email    | `hr@piac.com`      |
| Password | `piacl@2026`       |
| Role     | HR              |

> Change this password immediately after first login via **Change Password** in the
> account menu — it is a development default, not a production secret.

---

## Roles & What Each Can Do

| Role | Capabilities |
|---|---|
| **HR** | Full administration: departments, Project Managers, interns, projects, evaluations (Final), certificates, analytics, reports, audit log, settings, system statistics, global search. |
| **Project Manager** | View/update their own projects, mark & view attendance for their interns, evaluate interns assigned to their projects. |
| **Intern** | Personal dashboard, project submissions, daily work log, final internship report, view their own evaluations & certificate, edit their profile, submit feedback. |

Every route enforces role-based access server-side (`@roles_required(...)` in
`utils.py`), and the sidebar only renders links a role is actually permitted to use.

---

## Module Overview

1. **Core & Authentication** — login/logout, session management, password hashing
   (Werkzeug), change password, role-based access control, HR dashboard, Department CRUD,
   Project Manager management, Intern registration.
2. **Projects, Manager Dashboard & Attendance** — project CRUD/assignment with six
   statuses (Pending → Working → Submitted → Approved/Rejected → Completed), PM dashboard,
   attendance marking (Present/Absent/Leave/Late) with filters and an HR attendance report.
3. **Intern Portal** — intern dashboard (assigned project, manager, department,
   attendance %, progress, notifications), project file submission (ZIP/PDF/DOCX/images),
   daily work log, final internship report, editable profile, feedback.
4. **Evaluation, Analytics & Reports** — six-criteria PM/HR evaluations with an
   auto-calculated total score, a six-chart Chart.js analytics dashboard, and five
   downloadable PDF/Excel reports (Attendance, Evaluation, Intern Progress, Department
   Summary, Project Summary).
5. **Certificates, Notifications & Admin** — PDF internship completion certificates
   with an auto-generated certificate number, a full notification center (five
   notification types), and Admin Features: global search, filters & pagination,
   audit log, system settings, HR profile management, and system statistics.

All five modules share one database schema (`pia.db`) and one Flask application — there
is nothing further to merge or wire together.

---

## Folder Structure

```
pia_intern_system/
├── app.py                      # Application factory, blueprint registration, DB/setting seeding
├── config.py                   # Central configuration (paths, upload limits, defaults)
├── extensions.py                # Shared db / login_manager instances
├── utils.py                     # Shared helpers: RBAC decorator, uploads, notifications, audit log, pagination
├── requirements.txt
├── README.md
├── pia.db                       # SQLite database (created automatically on first run)
│
├── models/                      # SQLAlchemy ORM models (one file per entity)
│   ├── __init__.py               # Re-exports every model
│   ├── user.py                   # Central auth table (all roles)
│   ├── department.py
│   ├── project_manager.py
│   ├── intern.py
│   ├── project.py
│   ├── attendance.py
│   ├── submission.py             # ProjectSubmission
│   ├── work_log.py               # DailyWorkLog
│   ├── report.py                 # FinalReport
│   ├── feedback.py
│   ├── evaluation.py
│   ├── notification.py
│   ├── certificate.py
│   ├── audit_log.py
│   └── system_setting.py
│
├── routes/                      # Flask Blueprints (one file per feature area)
│   ├── auth.py                   # Login, logout, change password
│   ├── dashboard.py               # Role-aware landing page
│   ├── department.py              # Department CRUD
│   ├── project_manager.py         # PM CRUD + activate/deactivate
│   ├── intern.py                  # Intern CRUD + HR detail view
│   ├── project.py                 # Project CRUD, assignment, status
│   ├── attendance.py              # Marking, listing, HR report
│   ├── intern_portal.py           # All Intern-facing routes
│   ├── evaluation.py              # PM/HR evaluations
│   ├── analytics.py               # Chart.js data aggregation
│   ├── reports.py                 # PDF/Excel report downloads
│   ├── certificate.py             # Certificate generation/download
│   ├── notification.py            # Notification Center
│   └── admin.py                   # Search, audit log, settings, profile, statistics
│
├── services/                    # Business logic kept out of routes
│   ├── pdf_theme.py                # Shared ReportLab colors/styles
│   ├── pdf_reports.py              # 5 PDF report builders
│   ├── excel_reports.py            # 5 Excel report builders
│   └── certificate_service.py      # Certificate PDF renderer
│
├── templates/                   # Jinja2 templates
│   ├── base.html                   # Master layout
│   ├── dashboard.html              # HR/PM dashboard
│   ├── components/                 # Reusable partials: navbar, sidebar, flash, pagination
│   ├── auth/
│   ├── departments/
│   ├── project_managers/
│   ├── interns/
│   ├── projects/
│   ├── attendance/
│   ├── portal/                     # Intern Portal pages
│   ├── evaluations/
│   ├── analytics/
│   ├── reports/
│   ├── certificates/
│   ├── notifications/
│   ├── admin/
│   └── errors/                     # 403 / 404 / 500
│
└── static/
    ├── css/style.css              # PIA blue/white/gray theme
    ├── js/script.js                # Sidebar toggle, password visibility, alert auto-dismiss
    └── uploads/
        ├── profile_pics/
        ├── submissions/{projects,final_reports}/
        └── certificates/
```

---

## Database Schema

Sixteen tables, all created automatically by `db.create_all()` from the SQLAlchemy
models: `users`, `departments`, `project_managers`, `interns`, `projects`, `attendance`,
`project_submissions`, `daily_work_logs`, `final_reports`, `feedback`,
`evaluations`, `certificates`, `notifications`, `audit_logs`, `system_settings`.

`User` is the single authentication table for every role; `ProjectManager` and `Intern`
hold role-specific profile data linked back to it 1-to-1. This keeps the schema
normalized with no unused columns per role, while `Flask-Login` only ever needs to know
about the one `User` model.

---

## Configuration

All configuration lives in `config.py` and can be overridden with environment variables:

| Variable | Purpose | Default |
|---|---|---|
| `PIA_SECRET_KEY` | Flask session signing key | dev key (change in production) |

Upload limits, allowed file types, items-per-page, and the default HR credentials are
also defined in `config.py` — see the inline comments there for details.

---

## Uploads

User-supplied files are stored under `static/uploads/`, organized by purpose:

- `profile_pics/` — profile photos (PM, Intern, HR)
- `submissions/projects/` — intern project deliverables (ZIP, PDF, DOCX, images)
- `submissions/final_reports/` — Final Internship Report PDF attachments
- `certificates/` — generated internship completion certificate PDFs

Every uploaded/generated file is stored under a UUID-based filename to prevent
collisions and path traversal; the original filename is preserved separately in the
database for display.

---

## Notes on the Certificate Module

Certificates are generated on demand as a single-page landscape PDF (via ReportLab)
containing: a PIA logo placeholder, an auto-incrementing certificate number
(`PIA-CERT-<year>-<sequence>`, prefix configurable in Settings), intern name,
department, internship duration, Project Manager name, HR name, issue date, and a
QR-code placeholder graphic. Regenerating a certificate updates the same database row
and replaces the stored PDF, so there is always exactly one authoritative certificate
per intern.

---

## Verifying the Install

After `python app.py` is running, a quick manual smoke test:

1. Visit `http://localhost:5000` → redirected to `/auth/login`.
2. Log in with the default HR credentials above.
3. Create a Department, a Project Manager, and an Intern.
4. Log out, log back in as the Project Manager (username/password set during creation)
   → mark attendance, view "My Projects".
5. Log back in as HR → Analytics Dashboard should render six charts; Reports → download
   a PDF and an Excel report; Certificates → generate one for the intern.
6. Log in as the Intern → dashboard should show the assigned project, attendance %,
   and a certificate download card once HR has issued one.

---

## Tech Stack Summary

| Layer | Technology |
|---|---|
| Backend framework | Flask 3.x |
| ORM | SQLAlchemy (via Flask-SQLAlchemy) |
| Database | SQLite (`pia.db`) |
| Auth | Flask-Login (hashed passwords via Werkzeug) |
| Templates | Jinja2 |
| Frontend | Bootstrap 5, vanilla JavaScript |
| Charts | Chart.js (CDN) |
| PDF generation | ReportLab |
| Excel generation | OpenPyXL |

---

## License / Attribution

Built as an internal internship-management tool for Pakistan International Airlines.
Not affiliated with or endorsed by PIA outside of this internal engineering exercise.
