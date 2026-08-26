from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


INK = colors.HexColor("#101828")
GOLD = colors.HexColor("#B58B2A")
SOFT = colors.HexColor("#F4F6F8")
BORDER = colors.HexColor("#DDE3EA")
MUTED = colors.HexColor("#667085")
WHITE = colors.white


def _text(value: object | None, fallback: str = "-") -> str:
    content = str(value or "").strip()
    return escape(content or fallback).replace("\n", "<br/>")


def _duration(minutes: object | None) -> str:
    total = max(0, int(minutes or 0))
    hours, remainder = divmod(total, 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


def _label(value: object | None) -> str:
    return str(value or "").replace("_", " ").strip().title() or "-"


def generate_timesheet_daily_pdf(reports: list[dict], work_date: date) -> bytes:
    stream = BytesIO()
    page_size = landscape(A4)
    static_dir = Path(__file__).resolve().parents[1] / "static"
    logo_path = static_dir / "dons_premier_transparent_v2.png"
    doc = SimpleDocTemplate(
        stream,
        pagesize=page_size,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=25 * mm,
        bottomMargin=20 * mm,
        title=f"Daily Timesheet Report - {work_date.isoformat()}",
        author="Dons Premier Estate Agents",
    )

    def branded_page(canvas, document):
        width, height = page_size
        if logo_path.exists():
            canvas.drawImage(
                str(logo_path),
                14 * mm,
                height - 21 * mm,
                width=15 * mm,
                height=15 * mm,
                preserveAspectRatio=True,
                mask="auto",
            )
        canvas.setFillColor(INK)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(32 * mm, height - 13 * mm, "DONS PREMIER ESTATE AGENTS")
        canvas.setFillColor(GOLD)
        canvas.rect(0, 14 * mm, width, 1.2 * mm, fill=1, stroke=0)
        canvas.setFillColor(INK)
        canvas.rect(0, 0, width, 14 * mm, fill=1, stroke=0)
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(14 * mm, 7 * mm, f"Daily Timesheet Report | {work_date.strftime('%d %B %Y')}")
        canvas.drawRightString(width - 14 * mm, 7 * mm, f"Page {document.page}")

    base = getSampleStyleSheet()
    title = ParagraphStyle(
        "TimesheetTitle",
        parent=base["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=INK,
        alignment=0,
        spaceAfter=4,
    )
    subtitle = ParagraphStyle(
        "TimesheetSubtitle",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=MUTED,
        spaceAfter=12,
    )
    heading = ParagraphStyle(
        "TimesheetHeading",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=INK,
        spaceBefore=10,
        spaceAfter=5,
    )
    body = ParagraphStyle(
        "TimesheetBody",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=7.7,
        leading=10.5,
        textColor=INK,
    )
    small = ParagraphStyle(
        "TimesheetSmall",
        parent=body,
        fontSize=7,
        leading=9,
        textColor=MUTED,
    )
    table_header = ParagraphStyle(
        "TimesheetTableHeader",
        parent=small,
        fontName="Helvetica-Bold",
        textColor=WHITE,
    )

    task_count = sum(len(report.get("entries") or []) for report in reports)
    total_minutes = sum(int(report.get("total_duration_minutes") or 0) for report in reports)
    story = [
        Paragraph("Daily Timesheet Report", title),
        Paragraph(work_date.strftime("%A, %d %B %Y"), subtitle),
    ]
    summary = Table(
        [
            [
                Paragraph("<b>Staff Reports</b>", small),
                Paragraph("<b>Tasks</b>", small),
                Paragraph("<b>Total Recorded Time</b>", small),
            ],
            [
                Paragraph(str(len(reports)), heading),
                Paragraph(str(task_count), heading),
                Paragraph(_duration(total_minutes), heading),
            ],
        ],
        colWidths=[55 * mm, 55 * mm, 65 * mm],
    )
    summary.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([summary, Spacer(1, 7 * mm)])

    if not reports:
        story.append(Paragraph("No submitted staff reports were found for this date.", heading))

    for report in reports:
        staff_name = _text(report.get("staff_name"), "Staff member")
        staff_email = _text(report.get("staff_email"), "")
        status = _label(report.get("status"))
        total = _duration(report.get("total_duration_minutes"))
        entries = report.get("entries") or []
        staff_header = Table(
            [
                [
                    Paragraph(f"<b>{staff_name}</b><br/><font color='#667085'>{staff_email}</font>", body),
                    Paragraph(f"<b>{status}</b><br/><font color='#667085'>{len(entries)} tasks | {total}</font>", body),
                ]
            ],
            colWidths=[180 * mm, 75 * mm],
        )
        staff_header.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF8E7")),
                    ("BOX", (0, 0), (-1, -1), 0.6, GOLD),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )

        rows = [
            [
                Paragraph("START", table_header),
                Paragraph("END", table_header),
                Paragraph("DURATION", table_header),
                Paragraph("TASK", table_header),
                Paragraph("TASK STATUS", table_header),
            ]
        ]
        for entry in entries:
            rows.append(
                [
                    Paragraph(_text(entry.get("start_time")), body),
                    Paragraph(_text(entry.get("end_time")), body),
                    Paragraph(_duration(entry.get("duration_minutes")), body),
                    Paragraph(_text(entry.get("task")), body),
                    Paragraph(_text(_label(entry.get("status"))), body),
                ]
            )
        tasks = Table(
            rows,
            colWidths=[22 * mm, 22 * mm, 28 * mm, 143 * mm, 40 * mm],
            repeatRows=1,
        )
        tasks.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), INK),
                    ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                    ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, SOFT]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        section = [staff_header, tasks]
        comment = str(report.get("director_comment") or "").strip()
        if comment:
            section.extend(
                [
                    Spacer(1, 2 * mm),
                    Paragraph(f"<b>Director comment:</b> {_text(comment)}", small),
                ]
            )
        story.extend([KeepTogether(section), Spacer(1, 6 * mm)])

    doc.build(story, onFirstPage=branded_page, onLaterPages=branded_page)
    return stream.getvalue()
