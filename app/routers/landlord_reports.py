from __future__ import annotations

import json
import logging
import zipfile
from datetime import date as Date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.authz import has_page_access, require_page_access
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import AppState, User
from app.models import ManagedProperty
from app.services.landlord_invoice_import import address_match_score, parse_invoice_csv, parse_invoice_workbook
from app.services.landlord_report_pdf import generate_landlord_report_pdf
from app.services.landlord_reports import (
    ALL_SECTION_IDS,
    LANDLORD_REPORT_DEFAULTS_KEY,
    MELBOURNE_TZ,
    LandlordReportError,
    assemble_report,
    build_report_context,
    default_section_ids,
    load_photo_bytes,
    normalize_section_ids,
    render_preview_html,
)


router = APIRouter(prefix="/landlord-reports", tags=["landlord-reports"])
logger = logging.getLogger(__name__)


@router.post("/invoice-workbook")
async def parse_invoice_workbook_for_reports(
    file: UploadFile = File(...),
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("landlord_reports")),
):
    filename = (file.filename or "").lower()
    if not filename.endswith((".xlsx", ".csv")):
        raise HTTPException(status_code=400, detail="Please upload the CRM invoice export as a .csv or .xlsx file.")
    raw = await file.read(15_000_001)
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded workbook is empty.")
    if len(raw) > 15_000_000:
        raise HTTPException(status_code=413, detail="The invoice workbook must be no larger than 15MB.")
    try:
        invoices = parse_invoice_csv(raw) if filename.endswith(".csv") else parse_invoice_workbook(raw)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid Excel workbook.") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invoice workbook could not be parsed: {exc}") from exc
    if not invoices:
        raise HTTPException(status_code=400, detail="No invoice rows were found. Use the CRM Outgoing invoices Report CSV or a workbook with property address and invoice fields.")
    properties = db.query(ManagedProperty).filter(ManagedProperty.mailbox == mailbox, ManagedProperty.is_active == True).all()
    property_labels = [(prop, " ".join(filter(None, [prop.property_address, prop.address_line_2, prop.suburb, prop.state_code, prop.postcode]))) for prop in properties]
    matched = 0
    for invoice in invoices:
        ranked = sorted(((address_match_score(invoice["property_address"], label), prop) for prop, label in property_labels), key=lambda value: value[0], reverse=True)
        score, prop = ranked[0] if ranked else (0.0, None)
        if prop is not None and score >= 0.58:
            invoice["property_id"] = prop.id
            invoice["matched_property_address"] = prop.property_address
            invoice["match_score"] = round(score, 3)
            matched += 1
        else:
            invoice["property_id"] = None
            invoice["matched_property_address"] = None
            invoice["match_score"] = round(score, 3)
    return {"filename": file.filename, "rows": invoices, "row_count": len(invoices), "matched_count": matched, "unmatched_count": len(invoices) - matched, "stored": False}


ReportActivityStatus = Literal["completed", "in_progress", "awaiting_landlord_approval", "scheduled"]


class ManualReportActivity(BaseModel):
    id: str | None = Field(default=None, max_length=120)
    section_id: str
    date: Date | None = None
    title: str = Field(min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=5000)
    status: ReportActivityStatus = "in_progress"
    category: str | None = Field(default=None, max_length=160)
    contractor: str | None = Field(default=None, max_length=200)
    amount: float | None = Field(default=None, allow_inf_nan=False)
    landlord_action: str | None = Field(default=None, max_length=2500)
    internal: bool = False
    photo_ids: list[int] = Field(default_factory=list, max_length=20)


class ReportOnlyPhoto(BaseModel):
    id: int = Field(lt=0)
    filename: str = Field(max_length=240)
    caption: str | None = Field(default=None, max_length=500)
    data_url: str = Field(max_length=10_100_000)


class ReportInvoiceRow(BaseModel):
    property_id: int = Field(gt=0)
    invoice_date: Date | None = None
    due_date: Date | None = None
    paid_date: Date | None = None
    invoice_number: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    supplier: str | None = Field(default=None, max_length=300)
    amount: float | None = Field(default=None, allow_inf_nan=False)
    status: str | None = Field(default=None, max_length=160)
    category: str | None = Field(default=None, max_length=160)
    gst: float | None = Field(default=None, allow_inf_nan=False)


class LandlordReportRequest(BaseModel):
    property_id: int = Field(gt=0)
    start_date: Date
    end_date: Date
    prepared_date: Date = Field(default_factory=lambda: datetime.now(MELBOURNE_TZ).date())
    landlord_name: str | None = Field(default=None, max_length=500)
    property_manager_id: int | None = Field(default=None, gt=0)
    intro_message: str | None = Field(default=None, max_length=6000)
    overall_summary: str | None = Field(default=None, max_length=6000)
    additional_notes: str | None = Field(default=None, max_length=10000)
    include_no_activity: bool = False
    include_photos: bool = True
    include_financial: bool = True
    include_internal_notes: bool = False
    selected_sections: list[str] = Field(max_length=21)
    section_notes: dict[str, str] = Field(default_factory=dict)
    manual_activities: list[ManualReportActivity] = Field(default_factory=list, max_length=500)
    photo_attachment_ids: list[int] = Field(default_factory=list, max_length=250)
    hero_photo_id: int | None = Field(default=None, gt=0)
    detail_overrides: dict[str, str] = Field(default_factory=dict)
    report_only_photos: list[ReportOnlyPhoto] = Field(default_factory=list, max_length=40)
    invoice_rows: list[ReportInvoiceRow] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def validate_report(self):
        try:
            self.selected_sections = normalize_section_ids(self.selected_sections)
        except LandlordReportError as exc:
            raise ValueError(str(exc)) from exc
        if self.end_date < self.start_date:
            raise ValueError("Reporting end date must be on or after the start date.")
        if (self.end_date - self.start_date).days > 366:
            raise ValueError("Reporting periods cannot exceed 12 months.")
        for section_id, note in self.section_notes.items():
            if section_id not in ALL_SECTION_IDS:
                raise ValueError(f"Unknown report section note: {section_id}.")
            if len(str(note or "")) > 6000:
                raise ValueError("Section notes cannot exceed 6,000 characters.")
        if len(self.detail_overrides) > 100:
            raise ValueError("No more than 100 PDF details can be customised.")
        for key, value in self.detail_overrides.items():
            if len(key) > 120 or len(str(value or "")) > 2000:
                raise ValueError("A customised PDF detail is too long.")
        return self


class DefaultSectionsIn(BaseModel):
    selected_sections: list[str]

    @model_validator(mode="after")
    def validate_sections(self):
        try:
            self.selected_sections = normalize_section_ids(self.selected_sections)
        except LandlordReportError as exc:
            raise ValueError(str(exc)) from exc
        return self


def _defaults(db: Session) -> list[str]:
    row = db.get(AppState, LANDLORD_REPORT_DEFAULTS_KEY)
    return default_section_ids(row.value if row else None)


def _report_photo_ids(report: dict) -> list[int]:
    result: list[int] = []
    hero_id = report.get("meta", {}).get("hero_photo_id")
    if hero_id:
        result.append(int(hero_id))
    for section in report.get("sections", []):
        for block in section.get("blocks", []):
            if block.get("type") != "photos":
                continue
            for item in block.get("items", []):
                attachment_id = int(item.get("attachment_id") or 0)
                if attachment_id and attachment_id not in result:
                    result.append(attachment_id)
    return result


def _report_only_photo_bytes(payload: LandlordReportRequest) -> dict[int, tuple[bytes, str]]:
    import base64
    import binascii

    result: dict[int, tuple[bytes, str]] = {}
    total = 0
    allowed = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    for photo in payload.report_only_photos:
        try:
            header, encoded = photo.data_url.split(",", 1)
            content_type = header[5:].split(";", 1)[0].lower() if header.startswith("data:") else ""
            if content_type not in allowed or ";base64" not in header:
                raise ValueError
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise LandlordReportError(f"{photo.filename} is not a supported report photo.") from exc
        if not raw or len(raw) > 7_500_000:
            raise LandlordReportError("Each report-only photo must be no larger than 7.5MB.")
        total += len(raw)
        if total > 25_000_000:
            raise LandlordReportError("Report-only photos cannot exceed 25MB in total.")
        result[photo.id] = (raw, content_type)
    return result


def _assemble(
    payload: LandlordReportRequest,
    *,
    mailbox: str,
    db: Session,
    user: User,
) -> dict:
    try:
        return assemble_report(db, mailbox=mailbox, current_user=user, options=payload)
    except LandlordReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/context")
def report_context(
    property_id: int,
    start_date: Date,
    end_date: Date,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("landlord_reports")),
):
    try:
        context = build_report_context(
            db,
            mailbox=mailbox,
            property_id=property_id,
            start_date=start_date,
            end_date=end_date,
            current_user=user,
            defaults=_defaults(db),
        )
    except LandlordReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    context["can_manage_defaults"] = has_page_access(user.role, "system", db)
    return context


@router.post("/preview")
def preview_report(
    payload: LandlordReportRequest,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("landlord_reports")),
):
    report = _assemble(payload, mailbox=mailbox, db=db, user=user)
    try:
        photos = load_photo_bytes(
            db,
            mailbox=mailbox,
            property_id=payload.property_id,
            attachment_ids=_report_photo_ids(report),
        )
        photos.update(_report_only_photo_bytes(payload))
        preview_html = render_preview_html(report, photos)
    except LandlordReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Monthly landlord report preview failed")
        raise HTTPException(status_code=500, detail="The report preview could not be created. Please try again.") from exc
    return {
        "html": preview_html,
        "filename": report["meta"]["filename"],
        "included_sections": report["included_section_ids"],
        "excluded_empty_sections": report["excluded_empty_section_ids"],
        "warnings": report["warnings"],
    }


@router.post("/pdf")
def download_report_pdf(
    payload: LandlordReportRequest,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("landlord_reports")),
):
    report = _assemble(payload, mailbox=mailbox, db=db, user=user)
    try:
        photos = load_photo_bytes(
            db,
            mailbox=mailbox,
            property_id=payload.property_id,
            attachment_ids=_report_photo_ids(report),
        )
        photos.update(_report_only_photo_bytes(payload))
        pdf_bytes = generate_landlord_report_pdf(report, photos)
    except LandlordReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Monthly landlord report PDF generation failed")
        raise HTTPException(status_code=500, detail="The PDF could not be generated. Please review the report and try again.") from exc
    filename = report["meta"]["filename"]
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put("/defaults")
def save_default_sections(
    payload: DefaultSectionsIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("landlord_reports")),
):
    if not has_page_access(user.role, "system", db):
        raise HTTPException(status_code=403, detail="System access is required to change report defaults.")
    row = db.get(AppState, LANDLORD_REPORT_DEFAULTS_KEY)
    value = json.dumps(payload.selected_sections, separators=(",", ":"))
    now = datetime.utcnow()
    if row:
        row.value = value
        row.updated_at = now
    else:
        db.add(AppState(key=LANDLORD_REPORT_DEFAULTS_KEY, value=value, updated_at=now))
    db.commit()
    return {"ok": True, "default_sections": payload.selected_sections}
