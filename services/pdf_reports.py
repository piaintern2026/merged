"""
services/pdf_reports.py
------------------------
Generates the five PDF reports (Attendance, Evaluation, Intern
Progress, Department Summary, Project Summary) using ReportLab.
Every function returns an in-memory BytesIO buffer ready to be sent
with Flask's send_file() -- nothing is written to disk.

Every report shares the same professional letterhead (brand logo,
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
    plus a gold divider rule. The brand logo/org name banner itself is
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
    """Build a Table with consistent branded styling: navy header
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


def _build_pdf(flowables: list, landscape_mode: bool = False, report_title: str = "Report") -> BytesIO:
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
        title=f"Intern Onboarding Portal - {report_title}",
        author="Intern Onboarding Portal",
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
    late = sum(1 for r in records if r.status == "Late")
    absent = sum(1 for r in records if r.status == "Absent")
    on_leave = sum(1 for r in records if r.status == "Leave")
    # Attendance rate: Present + Late both count as the intern having
    # shown up that day; only Absent (and Leave) pull the rate down.
    rate = round(((present + late) / total) * 100, 1) if total else 0

    flowables = _header_flowables("Attendance Report", f"{total} record(s)")
    flowables.append(
        _kpi_strip(
            [
                ("Total Records", total),
                ("Present", present),
                ("Late", late),
                ("Absent", absent),
                ("Leave", on_leave),
                ("Attendance Rate", f"{rate}%"),
            ],
            usable_width=_LANDSCAPE_WIDTH,
        )
    )
    flowables.append(Spacer(1, 0.5 * cm))

    header = ["Intern", "Department", "Date", "Clock In", "Clock Out", "Status", "Remarks"]
    rows = [
        [
            r.intern.full_name,
            r.intern.department.name,
            r.date.strftime("%d %b %Y"),
            r.time.strftime("%I:%M %p") if r.time else "-",
            r.time_out.strftime("%I:%M %p") if r.time_out else "-",
            r.status,
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
    intern, attendance_percentage: float, evaluations: list, submissions: list
) -> BytesIO:
    """A detailed single-intern report: profile summary, attendance %,
    evaluations, and submitted files."""
    flowables = _header_flowables("Intern Progress Report", intern.full_name)
    flowables.append(
        _kpi_strip(
            [
                ("Attendance", f"{attendance_percentage}%"),
                ("Evaluations", len(evaluations)),
                ("Submissions", len(submissions)),
            ]
        )
    )
    flowables.append(Spacer(1, 0.5 * cm))

    # Profile summary block
    profile_rows = [
        ["Department", intern.department.name if intern.department else "-", "CNIC", intern.cnic],
        ["Division/Section", intern.sub_department.name if intern.sub_department else "-", "", ""],
        ["University", intern.university, "Qualification", intern.qualification],
        ["Major", intern.major or "-", "Station", intern.station],
        ["Documents Status", intern.documents_status, "Certificate Status", intern.certificate_status],
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
            [
                s.link or (s.original_filename or "-"),
                s.submitted_at.strftime("%d %b %Y"),
            ]
            for s in submissions
        ]
        flowables.append(_styled_table(["Link / File", "Submitted"], sub_rows))
    else:
        flowables.append(_empty_state("No submissions yet."))

    return _build_pdf(flowables, report_title=f"Intern Progress Report - {intern.full_name}")


# ------------------------------------------------------------------------
# 4. Department Summary Report
# ------------------------------------------------------------------------
def build_department_summary_pdf(department_rows: list) -> BytesIO:
    """One row per department (bold) followed by an indented row per
    Division/Section, showing the Department -> Division/Section
    breakdown of interns and projects."""
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

    header = ["Department", "Division/Section", "City", "Project Managers", "Interns", "Projects", "Avg. Evaluation %"]
    rows = []
    for d in department_rows:
        rows.append(
            [
                d["name"],
                "All Divisions/Sections",
                d.get("city") or "-",
                d["pm_count"],
                d["intern_count"],
                d["project_count"],
                f"{d['avg_score']}%" if d["avg_score"] is not None else "-",
            ]
        )
        for sub in d.get("sub_departments", []):
            rows.append(
                [
                    "",
                    sub["name"],
                    "",
                    "",
                    sub["intern_count"],
                    sub["project_count"],
                    "",
                ]
            )
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
    """One row per project: department, manager, intern(s),
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

    header = ["Title", "Department", "Sub Dept.", "Manager", "Intern(s)", "Status", "Deadline", "Project Brief"]
    rows = [
        [
            p.title,
            p.department.name if p.department else "-",
            p.sub_department.name if p.sub_department else "-",
            p.manager.full_name if p.manager else "-",
            ", ".join(i.full_name for i in p.interns) if p.interns else "-",
            p.status,
            p.deadline.strftime("%d %b %Y"),
            p.description or "-",
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
            intern.department.name if intern.department else "-",
        ],
        [
            "University",
            intern.university,
            "Qualification",
            intern.qualification,
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
        mgr_rows = [
            [m.full_name, m.designation or "-", m.department.name]
            for m in data["managers_worked_under"]
        ]
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
            data.get("late_count", 0),
            data["absent_count"],
            data["leave_count"],
            f"{data['attendance_percentage']}%",
        ]
    ]
    flowables.append(
        _styled_table(["Total", "Present", "Late", "Absent", "Leave", "Attendance %"], att_rows)
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


# ------------------------------------------------------------------------
# 7. Intern Profile Document (Documents module)
# ------------------------------------------------------------------------
def build_intern_profile_pdf(intern) -> BytesIO:
    """A stand-alone, printable profile document for a single intern:
    personal/academic details, current assignment, and internship
    status -- distinct from the Intern Progress Report (which focuses
    on performance data) and the Final Internship Report (which
    focuses on the full rotation history)."""
    status = intern.effective_status
    flowables = _header_flowables("Intern Profile", intern.full_name)
    flowables.append(
        _kpi_strip(
            [
                ("Status", status),
                ("Department", intern.department.name if intern.department else "-"),
                ("Division/Section", intern.sub_department.name if intern.sub_department else "-"),
                ("Station", intern.station),
                (
                    "Manager",
                    intern.current_manager.full_name if intern.current_manager else "-",
                ),
            ]
        )
    )
    flowables.append(Spacer(1, 0.5 * cm))

    flowables.append(Paragraph("Personal & Academic Details", _section_style))
    personal_rows = [
        ["Full Name", intern.full_name, "CNIC", intern.cnic],
        ["University", intern.university, "Qualification", intern.qualification],
        ["Major", intern.major or "-", "Cell No", intern.phone],
        ["Station", intern.station, "Address", intern.address or "-"],
        ["Documents Status", intern.documents_status, "Certificate Status", intern.certificate_status],
        ["Email", intern.user.email if intern.user else "-", "Username", intern.user.username if intern.user else "-"],
    ]
    flowables.append(_styled_table(["Field", "Value", "Field", "Value"], personal_rows))

    flowables.append(Paragraph("Internship Assignment", _section_style))
    assignment_rows = [
        ["Department", intern.department.name if intern.department else "-", "Division/Section", intern.sub_department.name if intern.sub_department else "-"],
        ["Status", status, "", ""],
        [
            "Internship Period",
            f"{intern.internship_start_date.strftime('%d %b %Y')} - "
            f"{intern.internship_end_date.strftime('%d %b %Y')}",
            "Current Manager",
            intern.current_manager.full_name if intern.current_manager else "-",
        ],
    ]
    if intern.end_reason:
        assignment_rows.append(["End Reason", intern.end_reason, "", ""])
    flowables.append(_styled_table(["Field", "Value", "Field", "Value"], assignment_rows))

    return _build_pdf(flowables, report_title=f"Intern Profile - {intern.full_name}")


# ------------------------------------------------------------------------
# 8. Rotation Report (org-wide, all rotations)
# ------------------------------------------------------------------------
def build_rotation_report_pdf(rotations: list) -> BytesIO:
    """One row per rotation record across every intern: from/to
    department, manager, project, dates, duration and reason."""
    total = len(rotations)
    active = sum(1 for r in rotations if r.is_current)

    flowables = _header_flowables("Rotation Report", f"{total} rotation(s)")
    flowables.append(
        _kpi_strip(
            [
                ("Total Rotations", total),
                ("Currently Active", active),
                ("Completed", total - active),
            ],
            usable_width=_LANDSCAPE_WIDTH,
        )
    )
    flowables.append(Spacer(1, 0.5 * cm))

    header = ["Intern", "From Dept.", "To Dept.", "To Manager", "Start", "End", "Duration", "Reason"]
    rows = [
        [
            r.intern.full_name,
            r.from_department.name if r.from_department else "-",
            r.to_department.name,
            r.to_manager.full_name,
            r.start_date.strftime("%d %b %Y"),
            r.end_date.strftime("%d %b %Y") if r.end_date else "Current",
            r.duration_display,
            (r.reason or "-")[:40],
        ]
        for r in rotations
    ]
    flowables.append(_styled_table(header, rows))
    return _build_pdf(flowables, landscape_mode=True, report_title="Rotation Report")


# ------------------------------------------------------------------------
# 9. Internship Report (org-wide summary, one row per intern)
# ------------------------------------------------------------------------
def build_internship_report_pdf(rows: list) -> BytesIO:
    """One row per intern: status, department, manager, completion %,
    attendance % and days remaining -- the organization-wide companion
    to the per-intern Final Internship Report."""
    total = len(rows)
    active = sum(1 for r in rows if r["status"] == "Active")

    flowables = _header_flowables("Internship Report", f"{total} intern(s)")
    flowables.append(
        _kpi_strip(
            [
                ("Total Interns", total),
                ("Active", active),
                ("Completed / Ended", total - active),
            ],
            usable_width=_LANDSCAPE_WIDTH,
        )
    )
    flowables.append(Spacer(1, 0.5 * cm))

    header = ["Intern", "Department", "Manager", "Status", "Completion %", "Attendance %", "Days Remaining"]
    table_rows = [
        [
            r["intern"].full_name,
            r["intern"].department.name if r["intern"].department else "-",
            r["manager"].full_name if r["manager"] else "-",
            r["status"],
            f"{r['completion_pct']}%",
            f"{r['attendance_pct']}%",
            r["days_remaining"],
        ]
        for r in rows
    ]
    flowables.append(_styled_table(header, table_rows))
    return _build_pdf(flowables, landscape_mode=True, report_title="Internship Report")
