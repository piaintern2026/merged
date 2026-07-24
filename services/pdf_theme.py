"""
services/pdf_theme.py
----------------------
Shared ReportLab styling constants and a branded page template (PIA
brand colors, logo, running header/footer with page numbers) used by
every PDF-producing service (pdf_reports.py, certificate_service.py).
Extracted here so the two services don't each redefine the same
color palette and page-decoration logic.
"""

import os

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import cm

# ---------------------------------------------------------------------
# PIA brand colors (matching static/css/style.css :root variables)
# ---------------------------------------------------------------------
PIA_BLUE_DARK = colors.HexColor("#012a54")
PIA_BLUE = colors.HexColor("#01477a")
PIA_BLUE_LIGHT = colors.HexColor("#1a6fb0")
PIA_GRAY = colors.HexColor("#eef1f5")
PIA_GRAY_BORDER = colors.HexColor("#dfe4ea")
PIA_GOLD = colors.HexColor("#c9a24b")
PIA_TEXT = colors.HexColor("#2b323d")
PIA_MUTED = colors.HexColor("#6c757d")

# ---------------------------------------------------------------------
# Logo asset (used in the header banner of every report)
# ---------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOGO_PATH = os.path.join(BASE_DIR, "static", "images", "pia-logo.png")

REPORT_ORG_NAME = "Pakistan International Airlines"
REPORT_SYSTEM_NAME = "Intern Management System"
REPORT_CONFIDENTIALITY_NOTE = "Confidential - For Internal Management Use Only"

# ---------------------------------------------------------------------
# Paragraph styles
# ---------------------------------------------------------------------
_styles = getSampleStyleSheet()

TITLE_STYLE = ParagraphStyle(
    "PIATitle",
    parent=_styles["Heading1"],
    fontName="Helvetica-Bold",
    fontSize=18,
    textColor=PIA_BLUE_DARK,
    spaceBefore=14,
    spaceAfter=2,
)
SUBTITLE_STYLE = ParagraphStyle(
    "PIASubtitle",
    parent=_styles["Normal"],
    fontName="Helvetica",
    fontSize=9.5,
    textColor=PIA_MUTED,
    spaceAfter=16,
)
SECTION_STYLE = ParagraphStyle(
    "PIASection",
    parent=_styles["Heading3"],
    fontName="Helvetica-Bold",
    fontSize=12,
    textColor=PIA_BLUE,
    spaceBefore=16,
    spaceAfter=8,
)
BODY_STYLE = ParagraphStyle(
    "PIABody",
    parent=_styles["Normal"],
    fontName="Helvetica",
    fontSize=9.5,
    textColor=PIA_TEXT,
    leading=13,
)
KPI_LABEL_STYLE = ParagraphStyle(
    "PIAKpiLabel",
    parent=_styles["Normal"],
    fontName="Helvetica",
    fontSize=8,
    textColor=colors.white,
    alignment=TA_CENTER,
)
KPI_VALUE_STYLE = ParagraphStyle(
    "PIAKpiValue",
    parent=_styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=15,
    textColor=colors.white,
    alignment=TA_CENTER,
)


def draw_header_footer(canvas, doc):
    """
    Page-decoration callback passed as onFirstPage/onLaterPages to
    SimpleDocTemplate. Draws a navy header banner with the PIA logo and
    organisation name on every page, plus a footer with the
    confidentiality notice, generation timestamp, and page number.
    Keeping this in one place means every report in the system shares
    an identical, professional letterhead.
    """
    canvas.saveState()
    page_width, page_height = doc.pagesize

    # ---- Header banner ----
    header_height = 2.1 * cm
    canvas.setFillColor(PIA_BLUE_DARK)
    canvas.rect(0, page_height - header_height, page_width, header_height, fill=1, stroke=0)

    # Gold accent underline beneath the header
    canvas.setFillColor(PIA_GOLD)
    canvas.rect(0, page_height - header_height - 0.09 * cm, page_width, 0.09 * cm, fill=1, stroke=0)

    # Logo (left)
    logo_h = 1.35 * cm
    logo_w = 1.35 * cm
    text_x = doc.leftMargin
    try:
        if os.path.exists(LOGO_PATH):
            canvas.drawImage(
                LOGO_PATH,
                doc.leftMargin,
                page_height - header_height / 2 - logo_h / 2,
                width=logo_w,
                height=logo_h,
                mask="auto",
                preserveAspectRatio=True,
            )
            text_x = doc.leftMargin + logo_w + 0.4 * cm
    except Exception:
        pass

    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(text_x, page_height - header_height / 2 + 0.05 * cm, REPORT_ORG_NAME)
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(colors.HexColor("#cfe0ee"))
    canvas.drawString(text_x, page_height - header_height / 2 - 0.45 * cm, REPORT_SYSTEM_NAME)

    # Right-aligned "Management Report" tag
    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(PIA_GOLD)
    canvas.drawRightString(
        page_width - doc.rightMargin, page_height - header_height / 2 - 0.1 * cm, "MANAGEMENT REPORT"
    )

    # ---- Footer ----
    footer_y = 1.1 * cm
    canvas.setStrokeColor(PIA_GRAY_BORDER)
    canvas.setLineWidth(0.6)
    canvas.line(doc.leftMargin, footer_y + 0.35 * cm, page_width - doc.rightMargin, footer_y + 0.35 * cm)

    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(PIA_MUTED)
    canvas.drawString(doc.leftMargin, footer_y, REPORT_CONFIDENTIALITY_NOTE)
    canvas.drawCentredString(page_width / 2, footer_y, REPORT_ORG_NAME)
    canvas.drawRightString(page_width - doc.rightMargin, footer_y, f"Page {doc.page}")

    canvas.restoreState()
