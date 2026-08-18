from io import BytesIO
import base64
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image


def generate_checklist_pdf(report: dict) -> bytes:
    stream = BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=16*mm, leftMargin=16*mm, topMargin=16*mm, bottomMargin=16*mm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("ChecklistTitle", parent=styles["Title"], textColor=colors.HexColor("#111827"), fontSize=19, leading=23, spaceAfter=12)
    heading = ParagraphStyle("ChecklistHeading", parent=styles["Heading2"], textColor=colors.HexColor("#9A7820"), fontSize=12, spaceBefore=12, spaceAfter=7)
    body = ParagraphStyle("ChecklistBody", parent=styles["BodyText"], fontSize=9, leading=13)
    p = report.get("payload") or {}
    story = [Paragraph("Property Application Screening Checklist", title)]
    meta = [["Applicant", report.get("applicant_name") or "—"], ["Property", report.get("property_address") or "—"],
            ["Screened by", p.get("screened_by") or "Jessica Gale — Property Manager"], ["Overall status", p.get("overall_status") or "Pending"]]
    table = Table([[Paragraph(f"<b>{escape(str(a))}</b>", body), Paragraph(escape(str(b)), body)] for a,b in meta], colWidths=[38*mm, 130*mm])
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(0,-1),colors.HexColor("#F4F6F8")),("BOX",(0,0),(-1,-1),.5,colors.HexColor("#DDE3EA")),("INNERGRID",(0,0),(-1,-1),.5,colors.HexColor("#DDE3EA")),("VALIGN",(0,0),(-1,-1),"TOP"),("PADDING",(0,0),(-1,-1),7)]))
    story += [table, Paragraph("Screening checks", heading)]
    rows = [[Paragraph("<b>Check</b>", body), Paragraph("<b>Status</b>", body), Paragraph("<b>Result / finding</b>", body)]]
    for item in p.get("checks") or []:
        rows.append([Paragraph(escape(str(item.get("name") or "")), body), Paragraph(escape(str(item.get("status") or "Pending")), body), Paragraph(escape(str(item.get("result") or "—")), body)])
    checks = Table(rows, colWidths=[70*mm, 40*mm, 58*mm], repeatRows=1)
    checks.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#111827")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),.5,colors.HexColor("#DDE3EA")),("VALIGN",(0,0),(-1,-1),"TOP"),("PADDING",(0,0),(-1,-1),7)]))
    story.append(checks)
    for label, key in [("Evidence / reference","default_evidence"),("Key positive points","key_positive_points"),("Outstanding / follow-up items","outstanding_items"),("Property owner update / comment","owner_comment")]:
        story += [Paragraph(label, heading), Paragraph(escape(str(p.get(key) or "Not recorded")).replace("\n","<br/>"), body)]
    story += [Paragraph("Approval", heading), Paragraph(escape(str(p.get("approval_status") or "Not requested").replace("_", " ").title()), body)]
    signature = str(p.get("signature_data") or "")
    if signature.startswith("data:image/") and "," in signature:
        try:
            image = Image(BytesIO(base64.b64decode(signature.split(",", 1)[1])), width=50*mm, height=20*mm, kind="proportional")
            story += [Spacer(1, 4), image, Paragraph("Jessica Gale — Property Manager", body)]
        except Exception:
            pass
    doc.build(story)
    return stream.getvalue()
