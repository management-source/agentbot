from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

from app.services.landlord_reports import (
    AGENCY_EMAIL,
    AGENCY_NAME,
    AGENCY_PHONE,
    AGENCY_WEBSITE,
    LOGO_PATH,
    REPORT_DISCLAIMER,
)


BLACK = HexColor("#111827")
INK = HexColor("#101828")
GOLD = HexColor("#B58B2A")
PALE_GOLD = HexColor("#FFF8E7")
LIGHT_GOLD = HexColor("#F6F0DF")
SOFT = HexColor("#F4F6F8")
BORDER = HexColor("#DDE3EA")
MUTED = HexColor("#667085")
GREEN = HexColor("#18794E")
PALE_GREEN = HexColor("#EAF7F0")
RED = HexColor("#B4232D")
PALE_RED = HexColor("#FFF0F1")
PURPLE = HexColor("#6941C6")
PALE_PURPLE = HexColor("#F4F0FC")
WHITE = colors.white


def _safe(value: Any, fallback: str = "Not recorded") -> str:
    text = str(value or "").strip()
    return text or fallback


def _xml(value: Any, fallback: str = "Not recorded") -> str:
    return xml_escape(_safe(value, fallback)).replace("\n", "<br/>")


def _tone_colors(tone: str | None) -> tuple[Any, Any]:
    key = str(tone or "neutral").lower()
    if key == "success":
        return GREEN, PALE_GREEN
    if key == "danger":
        return RED, PALE_RED
    if key == "warning" or key == "gold":
        return GOLD, PALE_GOLD
    if key == "internal":
        return PURPLE, PALE_PURPLE
    return MUTED, SOFT


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle(
            "LRBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.6,
            leading=12.4,
            textColor=INK,
            spaceAfter=5,
            splitLongWords=True,
        ),
        "small": ParagraphStyle(
            "LRSmall",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.2,
            leading=10,
            textColor=MUTED,
            splitLongWords=True,
        ),
        "tiny": ParagraphStyle(
            "LRTiny",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=6.5,
            leading=8.5,
            textColor=MUTED,
            splitLongWords=True,
        ),
        "label": ParagraphStyle(
            "LRLabel",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=6.6,
            leading=8.5,
            textColor=MUTED,
            uppercase=True,
            spaceAfter=2,
        ),
        "section": ParagraphStyle(
            "LRSection",
            parent=base["Heading1"],
            fontName="Times-Bold",
            fontSize=21,
            leading=24,
            textColor=INK,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "subheading": ParagraphStyle(
            "LRSubheading",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=9.5,
            leading=12,
            textColor=INK,
            spaceBefore=6,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "cover_title": ParagraphStyle(
            "LRCoverTitle",
            parent=base["Title"],
            fontName="Times-Bold",
            fontSize=30,
            leading=33,
            textColor=WHITE,
            spaceAfter=8,
        ),
        "cover_address": ParagraphStyle(
            "LRCoverAddress",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=18,
            textColor=HexColor("#E7EAF0"),
        ),
        "cover_kicker": ParagraphStyle(
            "LRCoverKicker",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=10,
            textColor=HexColor("#E0BF68"),
            uppercase=True,
            spaceAfter=7,
        ),
        "cover_meta": ParagraphStyle(
            "LRCoverMeta",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.6,
            leading=12,
            textColor=INK,
        ),
        "toc": ParagraphStyle(
            "LRTOC",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=14,
            textColor=INK,
            leftIndent=4,
            firstLineIndent=0,
            spaceBefore=3,
        ),
        "status": ParagraphStyle(
            "LRStatus",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=6.5,
            leading=8,
            alignment=TA_CENTER,
            textColor=INK,
        ),
        "right_small": ParagraphStyle(
            "LRRightSmall",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.2,
            leading=9,
            alignment=TA_RIGHT,
            textColor=MUTED,
        ),
    }


class LandlordReportDocTemplate(BaseDocTemplate):
    def afterFlowable(self, flowable: Flowable) -> None:
        level = getattr(flowable, "_landlord_toc_level", None)
        if level is None:
            return
        text = getattr(flowable, "_landlord_toc_text", "Section")
        key = getattr(flowable, "_landlord_toc_key", None)
        if key:
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=level, closed=False)
        self.notify("TOCEntry", (level, text, self.page, key))


def _draw_cover_chrome(canvas, doc) -> None:
    width, height = A4
    canvas.saveState()
    canvas.setFillColor(WHITE)
    canvas.rect(0, 0, width, height, stroke=0, fill=1)
    canvas.setFillColor(LIGHT_GOLD)
    canvas.rect(width - 21 * mm, 0, 2.5 * mm, height, stroke=0, fill=1)
    canvas.setFillColor(BLACK)
    canvas.rect(width - 18.5 * mm, 0, 18.5 * mm, height, stroke=0, fill=1)
    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(1.1)
    canvas.line(18 * mm, 18 * mm, width - 25 * mm, 18 * mm)
    canvas.restoreState()


def _draw_content_chrome(canvas, doc) -> None:
    width, height = A4
    meta = doc.report_meta
    canvas.saveState()
    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(0.7)
    canvas.line(18 * mm, height - 17 * mm, width - 18 * mm, height - 17 * mm)
    if LOGO_PATH.exists():
        try:
            canvas.drawImage(str(LOGO_PATH), 18 * mm, height - 15.2 * mm, 8.2 * mm, 8.2 * mm, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica-Bold", 6.8)
    address = _safe(meta.get("property_address"))
    if len(address) > 92:
        address = address[:89] + "..."
    canvas.drawString(29 * mm, height - 12.2 * mm, address)
    canvas.setFont("Helvetica", 6.5)
    canvas.drawRightString(width - 18 * mm, height - 12.2 * mm, _safe(meta.get("period_label")))

    footer_y = 14.5 * mm
    canvas.setStrokeColor(GOLD)
    canvas.line(18 * mm, footer_y + 5.5 * mm, width - 18 * mm, footer_y + 5.5 * mm)
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 6.2)
    canvas.drawString(18 * mm, footer_y + 1.7 * mm, f"{AGENCY_NAME}  |  {AGENCY_EMAIL}  |  {AGENCY_PHONE}  |  {AGENCY_WEBSITE}")
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 5.7)
    disclaimer = REPORT_DISCLAIMER
    canvas.drawString(18 * mm, footer_y - 2.1 * mm, disclaimer[:145])
    canvas.setFont("Helvetica-Bold", 6.2)
    canvas.drawRightString(width - 18 * mm, footer_y - 2.1 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _scaled_image(source: Any, max_width: float, max_height: float) -> Image | None:
    try:
        reader = ImageReader(source)
        width, height = reader.getSize()
        if not width or not height:
            return None
        scale = min(max_width / float(width), max_height / float(height), 1.0)
        image = Image(source, width=float(width) * scale, height=float(height) * scale)
        image.hAlign = "CENTER"
        return image
    except Exception:
        return None


def _image_from_bytes(raw: bytes, max_width: float, max_height: float) -> Image | None:
    if not raw:
        return None
    stream = BytesIO(raw)
    image = _scaled_image(stream, max_width, max_height)
    if image is not None:
        image._landlord_source_stream = stream
    return image


def _paragraph(value: Any, style: ParagraphStyle, fallback: str = "Not recorded") -> Paragraph:
    return Paragraph(_xml(value, fallback), style)


def _gold_rule() -> Table:
    rule = Table([[""]], colWidths=[23 * mm], rowHeights=[1.2 * mm])
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GOLD)]))
    return rule


def _section_heading(section: dict[str, Any], number: int, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    anchor = f"section-{section['id']}"
    kicker = Paragraph(f"<font color='#B58B2A'><b>SECTION {number:02d}</b></font>", styles["small"])
    heading = Paragraph(f"<a name='{anchor}'/>{_xml(section['title'])}", styles["section"])
    heading._landlord_toc_level = 0
    heading._landlord_toc_text = section["title"]
    heading._landlord_toc_key = anchor
    return [CondPageBreak(34 * mm), kicker, heading, _gold_rule(), Spacer(1, 5 * mm)]


def _status_cards(block: dict[str, Any], styles: dict[str, ParagraphStyle], available_width: float) -> list[Flowable]:
    items = list(block.get("items", []))
    if not items:
        return []
    columns = min(3, len(items))
    rows: list[list[Any]] = []
    current: list[Any] = []
    for item in items:
        accent, background = _tone_colors(item.get("tone"))
        content = [
            _paragraph(item.get("label"), styles["label"]),
            _paragraph(item.get("value"), ParagraphStyle("CardValue", parent=styles["body"], fontName="Helvetica-Bold", fontSize=9.2, leading=12)),
        ]
        card = Table([[content]], colWidths=[available_width / columns - 4 * mm])
        card.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), background),
                    ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                    ("LINEABOVE", (0, 0), (-1, 0), 2.3, accent),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        current.append(card)
        if len(current) == columns:
            rows.append(current)
            current = []
    if current:
        current.extend([""] * (columns - len(current)))
        rows.append(current)
    table = Table(rows, colWidths=[available_width / columns] * columns, hAlign="LEFT")
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm)]))
    return [table, Spacer(1, 2 * mm)]


def _key_values(block: dict[str, Any], styles: dict[str, ParagraphStyle], available_width: float) -> list[Flowable]:
    items = list(block.get("items", []))
    rows: list[list[Any]] = []
    for index in range(0, len(items), 2):
        row: list[Any] = []
        for item in items[index:index + 2]:
            row.append(
                [
                    _paragraph(item.get("label"), styles["label"]),
                    _paragraph(item.get("value"), ParagraphStyle("KVValue", parent=styles["body"], fontName="Helvetica-Bold", fontSize=8.2, leading=11)),
                ]
            )
        if len(row) == 1:
            row.append([])
        rows.append(row)
    table = Table(rows, colWidths=[available_width / 2] * 2, splitByRow=True)
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.45, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]
    for row_index in range(len(rows)):
        if row_index % 2:
            commands.append(("BACKGROUND", (0, row_index), (-1, row_index), SOFT))
    table.setStyle(TableStyle(commands))
    return [table, Spacer(1, 4 * mm)]


def _data_table(block: dict[str, Any], styles: dict[str, ParagraphStyle], available_width: float) -> list[Flowable]:
    columns = list(block.get("columns", []))
    rows = list(block.get("rows", []))
    if not columns or not rows:
        return []
    flowables: list[Flowable] = []
    if block.get("title"):
        flowables.append(_paragraph(block["title"], styles["subheading"]))
    header = [_paragraph(column.get("label"), ParagraphStyle("TableHead", parent=styles["tiny"], fontName="Helvetica-Bold", textColor=WHITE, alignment=TA_LEFT)) for column in columns]
    data: list[list[Any]] = [header]
    for row in rows:
        data.append([_paragraph(row.get(column.get("key")), styles["tiny"]) for column in columns])
    width = available_width / len(columns)
    table = Table(data, colWidths=[width] * len(columns), repeatRows=1, splitByRow=True, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), BLACK),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_index in range(2, len(data), 2):
        commands.append(("BACKGROUND", (0, row_index), (-1, row_index), SOFT))
    table.setStyle(TableStyle(commands))
    flowables.extend([table, Spacer(1, 4 * mm)])
    return flowables


def _record_cards(block: dict[str, Any], styles: dict[str, ParagraphStyle], available_width: float) -> list[Flowable]:
    flowables: list[Flowable] = []
    for item in block.get("items", []):
        accent, background = _tone_colors(item.get("tone"))
        title = _paragraph(item.get("title"), ParagraphStyle("RecordTitle", parent=styles["body"], fontName="Helvetica-Bold", fontSize=9.3, leading=12, textColor=INK))
        status = _paragraph(item.get("status"), ParagraphStyle("RecordStatus", parent=styles["status"], textColor=accent))
        header = Table([[title, status]], colWidths=[available_width - 38 * mm, 38 * mm])
        header.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), background),
                    ("BOX", (0, 0), (-1, -1), 0.6, accent),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        fields = list(item.get("fields", []))
        field_rows: list[list[Any]] = []
        for index in range(0, len(fields), 2):
            cells: list[Any] = []
            for field in fields[index:index + 2]:
                cells.append(
                    [
                        _paragraph(field.get("label"), styles["label"]),
                        _paragraph(field.get("value"), styles["small"]),
                    ]
                )
            if len(cells) == 1:
                cells.append([])
            field_rows.append(cells)
        field_table = Table(field_rows, colWidths=[available_width / 2] * 2, splitByRow=True)
        field_table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.35, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        flowables.append(KeepTogether([header, field_table]))
        if item.get("description"):
            description_box = Table([[_paragraph(item.get("description"), styles["body"])]], colWidths=[available_width])
            description_box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.35, BORDER), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
            flowables.append(description_box)
        flowables.append(Spacer(1, 4 * mm))
    return flowables


def _timeline(block: dict[str, Any], styles: dict[str, ParagraphStyle], available_width: float) -> list[Flowable]:
    flowables: list[Flowable] = []
    if block.get("title"):
        flowables.append(_paragraph(block["title"], styles["subheading"]))
    for item in block.get("items", []):
        accent, background = _tone_colors(item.get("tone"))
        title_text = _xml(item.get("title"))
        if item.get("internal"):
            title_text += " <font color='#6941C6' size='6'><b>INTERNAL - INTENTIONALLY INCLUDED</b></font>"
        title = Paragraph(title_text, ParagraphStyle("TimelineTitle", parent=styles["body"], fontName="Helvetica-Bold", fontSize=8.6, leading=11))
        status = _paragraph(item.get("status"), ParagraphStyle("TimelineStatus", parent=styles["status"], textColor=accent))
        date_cell = _paragraph(item.get("date"), ParagraphStyle("TimelineDate", parent=styles["small"], fontName="Helvetica-Bold", textColor=GOLD))
        header = Table([[date_cell, title, status]], colWidths=[26 * mm, available_width - 59 * mm, 33 * mm])
        header.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), background), ("LINEBELOW", (0, 0), (-1, -1), 0.45, accent), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        flowables.append(header)
        if item.get("description"):
            flowables.append(Paragraph(_xml(item.get("description")), ParagraphStyle("TimelineDescription", parent=styles["body"], leftIndent=26 * mm, borderPadding=(4, 0, 0, 0), spaceBefore=3, spaceAfter=3)))
        if item.get("action_required"):
            action = Table([[_paragraph("LANDLORD ACTION REQUIRED", styles["label"]), _paragraph(item.get("action_required"), styles["small"])]], colWidths=[38 * mm, available_width - 38 * mm])
            action.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PALE_GOLD), ("BOX", (0, 0), (-1, -1), 0.5, GOLD), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
            flowables.append(action)
        flowables.append(Spacer(1, 3 * mm))
    return flowables


def _note(block: dict[str, Any], styles: dict[str, ParagraphStyle], available_width: float) -> list[Flowable]:
    accent, background = _tone_colors(block.get("tone"))
    content = [
        _paragraph(block.get("title"), ParagraphStyle("NoteTitle", parent=styles["body"], fontName="Helvetica-Bold", textColor=accent)),
        _paragraph(block.get("text"), styles["body"]),
    ]
    table = Table([[content]], colWidths=[available_width])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), background), ("LINEBEFORE", (0, 0), (0, -1), 3, accent), ("BOX", (0, 0), (-1, -1), 0.4, accent), ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return [table, Spacer(1, 4 * mm)]


def _actions(block: dict[str, Any], styles: dict[str, ParagraphStyle], available_width: float) -> list[Flowable]:
    items = [_paragraph(f"- {item}", styles["body"]) for item in block.get("items", [])]
    content = [_paragraph(block.get("title") or "Landlord action required", ParagraphStyle("ActionTitle", parent=styles["body"], fontName="Helvetica-Bold", fontSize=9, textColor=HexColor("#795B12"))), *items]
    table = Table([[content]], colWidths=[available_width])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PALE_GOLD), ("BOX", (0, 0), (-1, -1), 0.8, GOLD), ("LINEBEFORE", (0, 0), (0, -1), 4, GOLD), ("LEFTPADDING", (0, 0), (-1, -1), 11), ("RIGHTPADDING", (0, 0), (-1, -1), 11), ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
    return [table, Spacer(1, 4 * mm)]


def _photos(
    block: dict[str, Any],
    styles: dict[str, ParagraphStyle],
    available_width: float,
    photo_bytes: dict[int, tuple[bytes, str]],
) -> list[Flowable]:
    cells: list[Any] = []
    cell_width = available_width / 2 - 2 * mm
    for item in block.get("items", []):
        loaded = photo_bytes.get(int(item.get("attachment_id") or 0))
        if not loaded:
            continue
        image = _image_from_bytes(loaded[0], cell_width - 5 * mm, 55 * mm)
        if not image:
            continue
        caption = _paragraph(f"<b>{_xml(item.get('caption'))}</b><br/><font color='#667085'>{_xml(item.get('date'))}</font>", styles["tiny"])
        cell = Table([[image], [caption]], colWidths=[cell_width])
        cell.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("BOX", (0, 0), (-1, -1), 0.45, BORDER), ("ALIGN", (0, 0), (-1, 0), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        cells.append(cell)
    rows: list[list[Any]] = []
    for index in range(0, len(cells), 2):
        row = cells[index:index + 2]
        if len(row) == 1:
            row.append("")
        rows.append(row)
    if not rows:
        return []
    table = Table(rows, colWidths=[available_width / 2] * 2, splitByRow=True)
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 4 * mm)]))
    return [table]


def _empty(block: dict[str, Any], styles: dict[str, ParagraphStyle], available_width: float) -> list[Flowable]:
    table = Table([[_paragraph(block.get("text"), styles["body"])]], colWidths=[available_width])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("BOX", (0, 0), (-1, -1), 0.45, BORDER), ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
    return [table, Spacer(1, 4 * mm)]


def _block_flowables(
    block: dict[str, Any],
    styles: dict[str, ParagraphStyle],
    available_width: float,
    photo_bytes: dict[int, tuple[bytes, str]],
) -> list[Flowable]:
    kind = block.get("type")
    if kind == "status_cards":
        return _status_cards(block, styles, available_width)
    if kind == "key_values":
        return _key_values(block, styles, available_width)
    if kind == "table":
        return _data_table(block, styles, available_width)
    if kind == "record_cards":
        return _record_cards(block, styles, available_width)
    if kind == "timeline":
        return _timeline(block, styles, available_width)
    if kind == "note":
        return _note(block, styles, available_width)
    if kind == "actions":
        return _actions(block, styles, available_width)
    if kind == "photos":
        return _photos(block, styles, available_width, photo_bytes)
    if kind == "empty":
        return _empty(block, styles, available_width)
    return []


def _cover_story(
    report: dict[str, Any],
    styles: dict[str, ParagraphStyle],
    available_width: float,
    photo_bytes: dict[int, tuple[bytes, str]],
) -> list[Flowable]:
    meta = report["meta"]
    logo = _scaled_image(str(LOGO_PATH), 38 * mm, 38 * mm) if LOGO_PATH.exists() else None
    agency_copy = [
        _paragraph(AGENCY_NAME, ParagraphStyle("AgencyName", parent=styles["body"], fontName="Times-Bold", fontSize=13, leading=16, textColor=INK)),
        _paragraph("The Knights of Real Estate", ParagraphStyle("AgencyTag", parent=styles["small"], textColor=GOLD)),
    ]
    brand = Table([[logo or "", agency_copy]], colWidths=[44 * mm, available_width - 44 * mm])
    brand.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))

    title_panel = Table(
        [[[
            _paragraph("DONS PREMIER ESTATE AGENTS", styles["cover_kicker"]),
            _paragraph("Monthly Property Report", styles["cover_title"]),
            _paragraph(meta.get("property_address"), styles["cover_address"]),
        ]]],
        colWidths=[available_width],
    )
    title_panel.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), BLACK), ("LINEBEFORE", (0, 0), (0, -1), 6, GOLD), ("LEFTPADDING", (0, 0), (-1, -1), 18), ("RIGHTPADDING", (0, 0), (-1, -1), 18), ("TOPPADDING", (0, 0), (-1, -1), 18), ("BOTTOMPADDING", (0, 0), (-1, -1), 18)]))

    meta_items = [
        ("Reporting period", meta.get("period_label")),
        ("Landlord", meta.get("landlord_name")),
        ("Property manager", meta.get("property_manager_name")),
        ("Prepared date", meta.get("prepared_date_label")),
    ]
    meta_cells = []
    for label, value in meta_items:
        meta_cells.append([_paragraph(label, styles["label"]), _paragraph(value, styles["cover_meta"])])
    meta_table = Table([meta_cells[:2], meta_cells[2:]], colWidths=[available_width / 2] * 2)
    meta_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.45, BORDER), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))

    story: list[Flowable] = [brand, Spacer(1, 9 * mm), title_panel, Spacer(1, 7 * mm), meta_table]
    hero_id = meta.get("hero_photo_id")
    if hero_id and int(hero_id) in photo_bytes:
        hero = _image_from_bytes(photo_bytes[int(hero_id)][0], available_width, 67 * mm)
        if hero:
            frame = Table([[hero]], colWidths=[available_width])
            frame.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("BOX", (0, 0), (-1, -1), 0.5, BORDER), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
            story.extend([Spacer(1, 6 * mm), frame])
    cover_intro = meta.get("cover_intro_message") or meta.get("intro_message")
    if cover_intro:
        story.extend([Spacer(1, 5 * mm), *_note({"title": "Message to the landlord", "text": cover_intro, "tone": "gold"}, styles, available_width)])
    story.extend(
        [
            Spacer(1, 5 * mm),
            _paragraph(f"{AGENCY_EMAIL}  |  {AGENCY_PHONE}  |  {AGENCY_WEBSITE}", styles["small"]),
            _paragraph(REPORT_DISCLAIMER, styles["tiny"]),
        ]
    )
    return story


def generate_landlord_report_pdf(
    report: dict[str, Any],
    photo_bytes: dict[int, tuple[bytes, str]] | None = None,
) -> bytes:
    photos = photo_bytes or {}
    buffer = BytesIO()
    width, height = A4
    left = 18 * mm
    right = 18 * mm
    available_width = width - left - right
    cover_frame = Frame(left, 22 * mm, available_width, height - 38 * mm, id="cover-frame", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    content_frame = Frame(left, 23 * mm, available_width, height - 45 * mm, id="content-frame", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc = LandlordReportDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=left,
        rightMargin=right,
        topMargin=22 * mm,
        bottomMargin=23 * mm,
        title=f"Monthly Property Report - {_safe(report['meta'].get('property_address'))}",
        author=AGENCY_NAME,
        subject=f"Landlord report for {_safe(report['meta'].get('period_label'))}",
    )
    doc.report_meta = report["meta"]
    doc.addPageTemplates(
        [
            PageTemplate(id="Cover", frames=[cover_frame], onPage=_draw_cover_chrome),
            PageTemplate(id="Content", frames=[content_frame], onPage=_draw_content_chrome),
        ]
    )
    styles = _styles()
    story: list[Flowable] = []
    story.extend(_cover_story(report, styles, available_width, photos))
    story.extend([NextPageTemplate("Content"), PageBreak()])

    story.extend(
        [
            _paragraph("REPORT NAVIGATION", styles["cover_kicker"]),
            _paragraph("Contents", styles["section"]),
            _gold_rule(),
            Spacer(1, 3 * mm),
        ]
    )
    toc = TableOfContents()
    toc.levelStyles = [styles["toc"]]
    toc.dotsMinLevel = 0
    story.extend([toc, PageBreak()])

    for number, section in enumerate(report.get("sections", []), 1):
        story.extend(_section_heading(section, number, styles))
        for block in section.get("blocks", []):
            story.extend(_block_flowables(block, styles, available_width, photos))
        story.append(Spacer(1, 3 * mm))

    doc.multiBuild(story)
    return buffer.getvalue()
