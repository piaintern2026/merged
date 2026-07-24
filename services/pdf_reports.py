"""
services/pdf_reports.py
------------------------
Generates the five PDF reports (Attendance, Evaluation, Intern
Progress, Department Summary, Project Summary) using ReportLab.
Every function returns an in-memory BytesIO buffer ready to be sent
with Flask's send_file() -- nothing is written to disk.

Every report shares the same professional letterhead (PIA logo,
navy header banner, confidentiality footer with page numbers) via
services/pdf_theme.py, so the output is presentation-ready for
management without any further formatting.
"""

from datetime import datetime
from utils import now_pkt
from io import BytesIO

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    HRFlowable,
)

from reportlab.lib import colors
from services.pdf_theme import (
    PIA_BLUE_DARK,
    PIA_BLUE,
    PIA_BLUE_LIGHT,
    PIA_GRAY,
    PIA_GRAY_BORDER,
    PIA_GOLD,
    TITLE_STYLE as _title_style,
    SUBTITLE_STYLE as _subtitle_style,
    SECTION_STYLE as _section_style,
    BODY_STYLE as _body_style,
    KPI_LABEL_STYLE as _kpi_label_style,
    KPI_VALUE_STYLE as _kpi_value_style,
    draw_header_footer,
)

# Space reserved at the top/bottom of every page for the branded
# header banner and footer drawn by draw_header_footer().
_TOP_MARGIN = 3.0 * cm
_BOTTOM_MARGIN = 2.0 * cm
_SIDE_MARGIN = 1.6 * cm
_LANDSCAPE_WIDTH = landscape(A4)[0] - 2 * _SIDE_MARGIN


def _header_flowables(title: str, subtitle: str) -> list:
    """Shared report header: title + subtitle + generation timestamp,
    plus a gold divider rule. The PIA logo/org name banner itself is
    drawn once per page by draw_header_footer(), not repeated here."""
    generated = now_pkt().strftime("%d %b %Y, %I:%M %p")
    return [
        Paragraph(title, _title_style),
        Paragraph(f"{subtitle} &nbsp;&bull;&nbsp; Generated {generated}", _subtitle_style),
        HRFlowable(width="100%", thickness=1.2, color=PIA_GOLD, spaceAfter=14),
    ]


def _kpi_strip(items: list, usable_width: float = A4[0] - 2 * _SIDE_MARGIN) -> Table:
    """A row of small navy KPI tiles (label + big value) summarising
    the report at a glance -- e.g. total records, average %, etc.
    `items` is a list of (label, value) tuples. `usable_width` should
    match the document's actual content width (differs for landscape
    vs. portrait reports) so the strip always spans the full page."""
    labels = [Paragraph(str(label).upper(), _kpi_label_style) for label, _ in items]
    values = [Paragraph(str(value), _kpi_value_style) for _, value in items]
    data = [values, labels]
    col_width = usable_width / len(items)
    table = Table(data, colWidths=[col_width] * len(items))
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PIA_BLUE),
                ("TOPPADDING", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                ("TOPPADDING", (0, 1), (-1, 1), 0),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
                ("LINEAFTER", (0, 0), (-2, -1), 0.75, PIA_BLUE_LIGHT),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


def _styled_table(header: list, rows: list, col_widths=None) -> Table:
    """Build a Table with consistent PIA-themed styling: navy header
    row, white text, alternating light-gray body rows, gold rule
    under the header for a polished corporate look."""
    data = [header] + rows
    table = Table(data, colWidths=col_widths, repeatRows=1)

    style = [
        ("BACKGROUND", (0, 0), (-1, 0), PIA_BLUE_DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, PIA_GOLD),
        ("BOX", (0, 0), (-1, -1), 0.75, PIA_GRAY_BORDER),
        ("INNERGRID", (0, 1), (-1, -1), 0.5, PIA_GRAY_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]
    for row_index in range(1, len(data)):
        if row_index % 2 == 0:
            style.append(("BACKGROUND", (0, row_index), (-1, row_index), PIA_GRAY))

    table.setStyle(TableStyle(style))
    return table


def _build_pdf(flowables: list, landscape_mode: bool = False, report_title: str = "PIA Report") -> BytesIO:
    """Render a list of flowables into a PDF and return the buffer,
    rewound to position 0 so it's ready for send_file(). Every page
    gets the shared branded letterhead via draw_header_footer()."""
    buffer = BytesIO()
    page_size = landscape(A4) if landscape_mode else A4
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        topMargin=_TOP_MARGIN,
        bottomMargin=_BOTTOM_MARGIN,
        leftMargin=_SIDE_MARGIN,
        rightMargin=_SIDE_MARGIN,
        title=f"PIA Intern Management System - {report_title}",
        author="PIA Intern Management System",
    )
    doc.build(flowables, onFirstPage=draw_header_footer, onLaterPages=draw_header_footer)
    buffer.seek(0)
    return buffer


def _empty_state(message: str) -> Paragraph:
    return Paragraph(f"<i>{message}</i>", _body_style)


# ------------------------------------------------------------------------
# 1. Attendance Report
# ------------------------------------------------------------------------
def build_attendance_pdf(records: list) -> BytesIO:
    """One row per attendance record: intern, department, date, time,
    status, remarks, marked by."""
    total = len(records)
    present = sum(1 for r in records if r.status == "Present")
    absent = sum(1 for r in records if r.status == "Absent")
    rate = round((present / total) * 100, 1) if total else 0

    flowables = _header_flowables("Attendance Report", f"{total} record(s)")
    flowables.append(
        _kpi_strip(
            [
                ("Total Records", total),
                ("Present", present),
                ("Absent", absent),
                ("Attendance Rate", f"{rate}%"),
            ],
            usable_width=_LANDSCAPE_WIDTH,
        )
    )
    flowables.append(Spacer(1, 0.5 * cm))

    header = ["Intern", "Department", "Date", "Time", "Status", "Marked By", "Remarks"]
    rows = [
        [
            r.intern.full_name,
            r.intern.department.name,
            r.date.strftime("%d %b %Y"),
            r.time.strftime("%I:%M %p") if r.time else "-",
            r.status,
            r.marked_by.full_name,
            r.remarks or "-",
        ]
        for r in records
    ]
    flowables.append(_styled_table(header, rows))
    return _build_pdf(flowables, landscape_mode=True, report_title="Attendance Report")


# ------------------------------------------------------------------------
# 2. Evaluation Report
# ------------------------------------------------------------------------
def build_evaluation_pdf(evaluations: list) -> BytesIO:
    """One row per evaluation, with per-criterion scores and total."""
    total = len(evaluations)
    avg_pct = round(sum(e.percentage for e in evaluations) / total, 1) if total else 0

    flowables = _header_flowables("Evaluation Report", f"{total} evaluation(s)")
    flowables.append(
        _kpi_strip(
            [
                ("Total Evaluations", total),
                ("Average Score", f"{avg_pct}%"),
            ],
            usable_width=_LANDSCAPE_WIDTH,
        )
    )
    flowables.append(Spacer(1, 0.5 * cm))

    header = [
        "Intern", "Type", "Technical", "Comm.", "Discipline",
        "Learning", "Teamwork", "Attendance", "Total /60", "Evaluated By",
    ]
    rows = [
        [
            e.intern.full_name,
            e.evaluation_type,
            e.technical_skills,
            e.communication,
            e.discipline,
            e.learning,
            e.teamwork,
            e.attendance_score,
            f"{e.total_score} ({e.percentage}%)",
            e.evaluated_by.display_name(),
        ]
        for e in evaluations
    ]
    flowables.append(_styled_table(header, rows))
    return _build_pdf(flowables, landscape_mode=True, report_title="Evaluation Report")


# ------------------------------------------------------------------------
# 3. Intern Progress Report (single intern, detailed)
# ------------------------------------------------------------------------
def build_intern_progress_pdf(
    intern, attendance_percentage: float, work_logs: list, evaluations: list, submissions: list
) -> BytesIO:
    """A detailed single-intern report: profile summary, attendance %,
    recent work logs, evaluations, and submitted files."""
    flowables = _header_flowables("Intern Progress Report", intern.full_name)
    flowables.append(
        _kpi_strip(
            [
                ("Attendance", f"{attendance_percentage}%"),
                ("Evaluations", len(evaluations)),
                ("Work Logs", len(work_logs)),
                ("Submissions", len(submissions)),
            ]
        )
    )
    flowables.append(Spacer(1, 0.5 * cm))

    # Profile summary block
    profile_rows = [
        ["Department", intern.department.name, "CNIC", intern.cnic],
        ["University", intern.university, "Degree", intern.degree],
        [
            "Internship Period",
            f"{intern.internship_start_date.strftime('%d %b %Y')} - "
            f"{intern.internship_end_date.strftime('%d %b %Y')}",
            "Attendance %",
            f"{attendance_percentage}%",
        ],
    ]
    flowables.append(Paragraph("Intern Profile", _section_style))
    flowables.append(_styled_table(["Field", "Value", "Field", "Value"], profile_rows))

    flowables.append(Paragraph("Recent Work Log Entries", _section_style))
    if work_logs:
        log_rows = [
            [wl.log_date.strftime("%d %b %Y"), wl.description[:80], f"{wl.hours_worked}h", f"{wl.progress_percent}%"]
            for wl in work_logs
        ]
        flowables.append(_styled_table(["Date", "Description", "Hours", "Progress"], log_rows))
    else:
        flowables.append(_empty_state("No work log entries recorded."))

    flowables.append(Paragraph("Evaluations", _section_style))
    if evaluations:
        eval_rows = [
            [e.evaluation_type, e.created_at.strftime("%d %b %Y"), f"{e.total_score}/60 ({e.percentage}%)", (e.remarks or "-")[:60]]
            for e in evaluations
        ]
        flowables.append(_styled_table(["Type", "Date", "Score", "Remarks"], eval_rows))
    else:
        flowables.append(_empty_state("No evaluations recorded yet."))

    flowables.append(Paragraph("Project Submissions", _section_style))
    if submissions:
        sub_rows = [
            [s.link, s.submitted_at.strftime("%d %b %Y")]
            for s in submissions
        ]
        flowables.append(_styled_table(["Link", "Submitted"], sub_rows))
    else:
        flowables.append(_empty_state("No submissions yet."))

    return _build_pdf(flowables, report_title=f"Intern Progress Report - {intern.full_name}")


# ------------------------------------------------------------------------
# 4. Department Summary Report
# ------------------------------------------------------------------------
def build_department_summary_pdf(department_rows: list) -> BytesIO:
    """One row per department: counts of PMs, interns, projects, and
    average evaluation score."""
    total_depts = len(department_rows)
    total_interns = sum(d["intern_count"] for d in department_rows)
    total_projects = sum(d["project_count"] for d in department_rows)

    flowables = _header_flowables("Department Summary Report", f"{total_depts} department(s)")
    flowables.append(
        _kpi_strip(
            [
                ("Departments", total_depts),
                ("Total Interns", total_interns),
                ("Total Projects", total_projects),
            ]
        )
    )
    flowables.append(Spacer(1, 0.5 * cm))

    header = ["Department", "Status", "Project Managers", "Interns", "Projects", "Avg. Evaluation %"]
    rows = [
        [
            d["name"],
            d["status"],
            d["pm_count"],
            d["intern_count"],
            d["project_count"],
            f"{d['avg_score']}%" if d["avg_score"] is not None else "-",
        ]
        for d in department_rows
    ]
    flowables.append(_styled_table(header, rows))
    return _build_pdf(flowables, report_title="Department Summary Report")


# ------------------------------------------------------------------------
# 6. Station x Department Report (City x Department Matrix)
# ------------------------------------------------------------------------
def build_station_department_pdf(matrix_data: dict) -> BytesIO:
    """One row per department, one column per city/station, matching
    the on-screen City x Department Matrix exactly (same cell values,
    row totals, column totals and grand total)."""
    cities = matrix_data["cities"]
    matrix = matrix_data["matrix"]

    flowables = _header_flowables(
        "Station \u00d7 Department Report", f"{matrix_data['grand_total']} intern(s) total"
    )
    flowables.append(
        _kpi_strip(
            [
                ("Departments", len(matrix)),
                ("Stations", len(cities)),
                ("Total Interns", matrix_data["grand_total"]),
            ],
            usable_width=_LANDSCAPE_WIDTH,
        )
    )
    flowables.append(Spacer(1, 0.5 * cm))

    header = ["Department"] + cities + ["Total"]
    rows = [[row["department"]] + row["cells"] + [row["row_total"]] for row in matrix]
    rows.append(["Total"] + matrix_data["city_totals"] + [matrix_data["grand_total"]])

    table = _styled_table(header, rows)
    # Bold the final "Total" row to match the on-screen tfoot styling.
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, -1), (-1, -1), PIA_GRAY),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    flowables.append(table)
    return _build_pdf(flowables, landscape_mode=True, report_title="Station x Department Report")


# ------------------------------------------------------------------------
# 5. Project Summary Report
# ------------------------------------------------------------------------
def build_project_summary_pdf(projects: list) -> BytesIO:
    """One row per project: department, manager, intern, priority,
    status, and deadline."""
    total = len(projects)
    completed = sum(1 for p in projects if p.status == "Completed")
    in_progress = sum(1 for p in projects if p.status == "Working")

    flowables = _header_flowables("Project Summary Report", f"{total} project(s)")
    flowables.append(
        _kpi_strip(
            [
                ("Total Projects", total),
                ("Completed", completed),
                ("In Progress", in_progress),
            ],
            usable_width=_LANDSCAPE_WIDTH,
        )
    )
    flowables.append(Spacer(1, 0.5 * cm))

    header = ["Title", "Department", "Manager", "Intern", "Priority", "Status", "Deadline"]
    rows = [
        [
            p.title,
            p.department.name,
            p.manager.full_name if p.manager else "-",
            p.intern.full_name if p.intern else "-",
            p.priority,
            p.status,
            p.deadline.strftime("%d %b %Y"),
        ]
        for p in projects
    ]
    flowables.append(_styled_table(header, rows))
    return _build_pdf(flowables, landscape_mode=True, report_title="Project Summary Report")


# ------------------------------------------------------------------------
# 6. Final Internship Report (Intern Rotation Management)
# ------------------------------------------------------------------------
def build_intern_final_report_pdf(data: dict) -> BytesIO:
    """The consolidated end-of-internship report: duration, departments
    served, managers worked under, projects completed, time spent per
    department, performance ratings, attendance summary, and full
    rotation history. `data` is the dict built by
    routes/rotation.py::_final_report_data()."""
    intern = data["intern"]

    flowables = _header_flowables("Final Internship Report", intern.full_name)
    flowables.append(
        _kpi_strip(
            [
                ("Duration (days)", data["total_days"]),
                ("Departments Served", len(data["departments_served"])),
                ("Rotations", len(data["rotations"])),
                ("Attendance", f"{data['attendance_percentage']}%"),
            ]
        )
    )
    flowables.append(Spacer(1, 0.5 * cm))

    profile_rows = [
        [
            "Internship Period",
            f"{intern.internship_start_date.strftime('%d %b %Y')} - "
            f"{intern.internship_end_date.strftime('%d %b %Y')}",
            "Current Department",
            intern.department.name,
        ],
        [
            "University",
            intern.university,
            "Degree",
            intern.degree,
        ],
    ]
    flowables.append(Paragraph("Intern Profile", _section_style))
    flowables.append(_styled_table(["Field", "Value", "Field", "Value"], profile_rows))

    flowables.append(Paragraph("Departments Served", _section_style))
    if data["departments_served"]:
        dept_rows = [
            [d.name, f"{data['dept_time'].get(d.name, 0)} day(s)"]
            for d in data["departments_served"]
        ]
        flowables.append(_styled_table(["Department", "Time Spent"], dept_rows))
    else:
        flowables.append(_empty_state("No department history recorded."))

    flowables.append(Paragraph("Managers Worked Under", _section_style))
    if data["managers_worked_under"]:
        mgr_rows = [[m.full_name, m.designation, m.department.name] for m in data["managers_worked_under"]]
        flowables.append(_styled_table(["Manager", "Designation", "Department"], mgr_rows))
    else:
        flowables.append(_empty_state("No manager history recorded."))

    flowables.append(Paragraph("Projects Completed", _section_style))
    if data["projects_completed"]:
        proj_rows = [
            [p.title, p.department.name, p.manager.full_name if p.manager else "-", p.status]
            for p in data["projects_completed"]
        ]
        flowables.append(_styled_table(["Project", "Department", "Manager", "Status"], proj_rows))
    else:
        flowables.append(_empty_state("No completed projects recorded."))

    flowables.append(Paragraph("Performance Ratings", _section_style))
    if data["evaluations"]:
        eval_rows = [
            [
                e.evaluation_type,
                e.created_at.strftime("%d %b %Y"),
                f"{e.total_score}/60 ({e.percentage}%)",
                e.evaluated_by.display_name(),
            ]
            for e in data["evaluations"]
        ]
        flowables.append(_styled_table(["Type", "Date", "Score", "Evaluated By"], eval_rows))
        flowables.append(
            Paragraph(f"<b>Average Score:</b> {data['avg_score_pct']}%", _body_style)
        )
    else:
        flowables.append(_empty_state("No evaluations recorded yet."))

    flowables.append(Paragraph("Attendance Summary", _section_style))
    att_rows = [
        [
            data["total_attendance"],
            data["present_count"],
            data["absent_count"],
            data["leave_count"],
            data["late_count"],
            f"{data['attendance_percentage']}%",
        ]
    ]
    flowables.append(
        _styled_table(["Total", "Present", "Absent", "Leave", "Late", "Attendance %"], att_rows)
    )

    flowables.append(Paragraph("Rotation History", _section_style))
    if data["rotations"]:
        rot_rows = [
            [
                (r.from_department.name if r.from_department else "-"),
                r.to_department.name,
                r.to_manager.full_name,
                r.project.title if r.project else "-",
                r.start_date.strftime("%d %b %Y"),
                r.end_date.strftime("%d %b %Y") if r.end_date else "Current",
                r.duration_display,
            ]
            for r in data["rotations"]
        ]
        flowables.append(
            _styled_table(
                ["From Dept.", "To Dept.", "To Manager", "Project", "Start", "End", "Duration"],
                rot_rows,
            )
        )
    else:
        flowables.append(_empty_state("This intern has not been rotated yet."))

    return _build_pdf(
        flowables, landscape_mode=True, report_title=f"Final Internship Report - {intern.full_name}"
    )
