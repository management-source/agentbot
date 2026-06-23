from __future__ import annotations

import re
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.authz import get_current_user
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import ComplianceProperty, ComplianceState, User

router = APIRouter()

NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

SMOKE_INTERVAL_DAYS = 365
GAS_INTERVAL_DAYS = 365 * 2
ELECTRICAL_INTERVAL_DAYS = 365 * 2
DUE_SOON_DAYS = 30


def _excel_serial_to_datetime(value: float) -> datetime:
    return datetime(1899, 12, 30) + timedelta(days=value)


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _normalize_address_key(value: str | None) -> str:
    text = _normalize_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root.findall("a:si", NS):
        out.append("".join((t.text or "") for t in si.findall(".//a:t", NS)))
    return out


def _cell_value(cell: ET.Element, sst: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    value = cell.find("a:v", NS)
    inline = cell.find("a:is", NS)
    if cell_type == "s" and value is not None and value.text is not None:
        idx = int(value.text)
        return sst[idx] if 0 <= idx < len(sst) else ""
    if cell_type == "inlineStr" and inline is not None:
        return "".join((x.text or "") for x in inline.findall(".//a:t", NS))
    if value is not None and value.text is not None:
        return str(value.text)
    return ""


def _sheet_rows(zf: zipfile.ZipFile, path: str, sst: list[str]) -> list[dict[str, str]]:
    root = ET.fromstring(zf.read(path))
    rows: list[dict[str, str]] = []
    for row in root.findall("a:sheetData/a:row", NS):
        vals: dict[str, str] = {}
        for cell in row.findall("a:c", NS):
            ref = cell.attrib.get("r", "")
            m = re.match(r"([A-Z]+)\d+", ref)
            if not m:
                continue
            col = m.group(1)
            vals[col] = _cell_value(cell, sst).strip()
        if any(v for v in vals.values()):
            rows.append(vals)
    return rows


def _parse_date_loose(raw: str | None) -> datetime | None:
    text = _normalize_text(raw)
    if not text:
        return None

    serial_match = re.fullmatch(r"\d+(?:\.\d+)?", text)
    if serial_match:
        n = float(text)
        if 15000 <= n <= 90000:
            return _excel_serial_to_datetime(n)

    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", text)
    if m:
        a = int(m.group(1))
        b = int(m.group(2))
        year = int(m.group(3))
        if year < 100:
            year += 2000
        # Prefer month/day/year when the second value is > 12.
        candidates = []
        if 1 <= a <= 12:
            candidates.append((year, a, b))
        if 1 <= b <= 12:
            candidates.append((year, b, a))
        for y, month, day in candidates:
            try:
                return datetime(y, month, day)
            except ValueError:
                continue

    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _extract_latest_date(raw: str | None) -> datetime | None:
    text = _normalize_text(raw)
    if not text:
        return None
    dates = re.findall(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text)
    parsed = [_parse_date_loose(x) for x in dates]
    parsed = [x for x in parsed if x is not None]
    if parsed:
        return max(parsed)
    return _parse_date_loose(text)


def _truthy_done(raw: str | None) -> bool:
    text = _normalize_text(raw).lower()
    return bool(text) and any(x in text for x in ("done", "yes", "paid", "passed", "received", "complete"))


def _fault_flag(raw: str | None) -> bool:
    text = _normalize_text(raw).lower()
    if not text or text in {"-", "done", "no", "none"}:
        return False
    return "yes" in text or "fault" in text or "not passed" in text


def _next_due(last_checked: datetime | None, interval_days: int) -> datetime | None:
    if not last_checked:
        return None
    return last_checked + timedelta(days=interval_days)


def _find_match_key(index: dict[str, dict[str, Any]], address: str) -> str:
    key = _normalize_address_key(address)
    if key in index:
        return key
    for existing in index:
        if existing and (existing in key or key in existing):
            return existing
    index[key] = {"property_address": _normalize_text(address)}
    return key


def _parse_workbook(content: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(BytesIO(content)) as zf:
        shared_strings = _read_shared_strings(zf)
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.attrib["Id"]: r.attrib["Target"] for r in rels}

        sheets: dict[str, list[dict[str, str]]] = {}
        for s in wb.findall("a:sheets/a:sheet", NS):
            name = s.attrib.get("name", "")
            rel_id = s.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            if not rel_id or rel_id not in rel_map:
                continue
            sheets[name] = _sheet_rows(zf, "xl/" + rel_map[rel_id], shared_strings)

    index: dict[str, dict[str, Any]] = {}

    # Sheet4: property register + last completed compliance entries
    for row in sheets.get("Sheet4", []):
        address = _normalize_text(row.get("A"))
        if not address or address.lower() == "address":
            continue
        key = _find_match_key(index, address)
        item = index[key]
        item.update(
            {
                "property_address": address,
                "address_line_2": _normalize_text(row.get("B")) or None,
                "suburb": _normalize_text(row.get("C")) or None,
                "state_code": _normalize_text(row.get("D")) or None,
                "postcode": _normalize_text(row.get("E")) or None,
                "mrs_raw": _normalize_text(row.get("F")) or None,
                "gas_raw": _normalize_text(row.get("G")) or None,
                "smoke_raw": _normalize_text(row.get("H")) or None,
                "electrical_raw": _normalize_text(row.get("I")) or None,
                "pool_raw": _normalize_text(row.get("J")) or None,
                "powerband_raw": _normalize_text(row.get("K")) or None,
                "disclosure_raw": _normalize_text(row.get("L")) or None,
                "source_sheet": "Sheet4",
            }
        )

    # Sheet1: workflow/status staging for properties
    for row in sheets.get("Sheet1", []):
        address = _normalize_text(row.get("B"))
        if not address or address.lower() == "property address":
            continue
        key = _find_match_key(index, address)
        item = index[key]
        item["property_address"] = address
        item["source_sheet"] = item.get("source_sheet") or "Sheet1"
        for field, col in (
            ("mrs_raw", "C"),
            ("gas_raw", "F"),
            ("smoke_raw", "H"),
            ("electrical_raw", "J"),
            ("pool_raw", "K"),
            ("powerband_raw", "L"),
        ):
            value = _normalize_text(row.get(col))
            if value:
                item[field] = value
        item["work_order_requested_at"] = _parse_date_loose(row.get("N")) or item.get("work_order_requested_at")
        item["completed_raw"] = _normalize_text(row.get("O")) or item.get("completed_raw")
        item["report_received_at"] = _parse_date_loose(row.get("P")) or item.get("report_received_at")
        item["report_result"] = _normalize_text(row.get("Q")) or item.get("report_result")
        item["invoice_date"] = _parse_date_loose(row.get("R")) or item.get("invoice_date")
        item["invoice_payment_status"] = _normalize_text(row.get("S")) or item.get("invoice_payment_status")

    # Sheet5: fault/payment follow-up
    for row in sheets.get("Sheet5", []):
        address = _normalize_text(row.get("A"))
        if not address or address.lower() == "property nme":
            continue
        key = _find_match_key(index, address)
        item = index[key]
        item["property_address"] = item.get("property_address") or address
        item["electrical_faults_raw"] = _normalize_text(row.get("B")) or item.get("electrical_faults_raw")
        item["gas_faults_raw"] = _normalize_text(row.get("C")) or item.get("gas_faults_raw")
        item["smoke_faults_raw"] = _normalize_text(row.get("D")) or item.get("smoke_faults_raw")
        item["mrs_faults_raw"] = _normalize_text(row.get("E")) or item.get("mrs_faults_raw")
        item["invoice_payment_status"] = _normalize_text(row.get("F")) or item.get("invoice_payment_status")
        item["quoted_electrical_payment_raw"] = _normalize_text(row.get("G")) or item.get("quoted_electrical_payment_raw")
        item["quoted_gas_payment_raw"] = _normalize_text(row.get("H")) or item.get("quoted_gas_payment_raw")
        item["quoted_smoke_payment_raw"] = _normalize_text(row.get("I")) or item.get("quoted_smoke_payment_raw")

    # Sheet6: free-form notes
    for row in sheets.get("Sheet6", []):
        address = _normalize_text(row.get("A"))
        note = _normalize_text(row.get("B"))
        if not address or not note:
            continue
        key = _find_match_key(index, address)
        item = index[key]
        existing = _normalize_text(item.get("compliance_notes"))
        item["compliance_notes"] = f"{existing}\n{note}".strip() if existing else note

    now = datetime.utcnow()
    today = now.date()
    out: list[dict[str, Any]] = []
    for item in index.values():
        property_address = _normalize_text(item.get("property_address"))
        if not property_address:
            continue

        gas_last = _extract_latest_date(item.get("gas_raw"))
        smoke_last = _extract_latest_date(item.get("smoke_raw"))
        electrical_last = _extract_latest_date(item.get("electrical_raw"))

        gas_due = _next_due(gas_last, GAS_INTERVAL_DAYS)
        smoke_due = _next_due(smoke_last, SMOKE_INTERVAL_DAYS)
        electrical_due = _next_due(electrical_last, ELECTRICAL_INTERVAL_DAYS)

        reasons: list[str] = []
        state = ComplianceState.UNKNOWN

        if any(_fault_flag(item.get(x)) for x in ("electrical_faults_raw", "gas_faults_raw", "smoke_faults_raw", "mrs_faults_raw")):
            state = ComplianceState.ACTION_REQUIRED
            reasons.append("Faults are recorded and need follow-up.")

        report_result = _normalize_text(item.get("report_result")).lower()
        if report_result and "not passed" in report_result:
            state = ComplianceState.ACTION_REQUIRED
            reasons.append("A report is marked as not passed.")

        due_candidates = [
            ("Gas", gas_due),
            ("Smoke", smoke_due),
            ("Electrical", electrical_due),
        ]
        for label, due_at in due_candidates:
            if due_at and due_at.date() < today:
                state = ComplianceState.OVERDUE if state != ComplianceState.ACTION_REQUIRED else state
                reasons.append(f"{label} compliance is overdue.")
            elif due_at and due_at.date() <= (today + timedelta(days=DUE_SOON_DAYS)):
                if state in {ComplianceState.UNKNOWN, ComplianceState.CURRENT}:
                    state = ComplianceState.DUE_SOON
                reasons.append(f"{label} compliance is due soon.")

        if state == ComplianceState.UNKNOWN:
            if any([gas_last, smoke_last, electrical_last]) or any(_truthy_done(item.get(x)) for x in ("mrs_raw", "pool_raw", "powerband_raw", "completed_raw")):
                state = ComplianceState.CURRENT
            else:
                reasons.append("No recent compliance date was detected.")

        if state == ComplianceState.CURRENT and not reasons:
            reasons.append("Compliance dates are currently within the expected cycle.")

        out.append(
            {
                "property_address": property_address,
                "address_line_2": item.get("address_line_2"),
                "suburb": item.get("suburb"),
                "state_code": item.get("state_code"),
                "postcode": item.get("postcode"),
                "source_sheet": item.get("source_sheet"),
                "mrs_raw": item.get("mrs_raw"),
                "gas_raw": item.get("gas_raw"),
                "smoke_raw": item.get("smoke_raw"),
                "electrical_raw": item.get("electrical_raw"),
                "pool_raw": item.get("pool_raw"),
                "powerband_raw": item.get("powerband_raw"),
                "disclosure_raw": item.get("disclosure_raw"),
                "gas_last_checked_at": gas_last,
                "gas_next_due_at": gas_due,
                "smoke_last_checked_at": smoke_last,
                "smoke_next_due_at": smoke_due,
                "electrical_last_checked_at": electrical_last,
                "electrical_next_due_at": electrical_due,
                "work_order_requested_at": item.get("work_order_requested_at"),
                "completed_raw": item.get("completed_raw"),
                "report_received_at": item.get("report_received_at"),
                "report_result": item.get("report_result"),
                "invoice_date": item.get("invoice_date"),
                "invoice_payment_status": item.get("invoice_payment_status"),
                "electrical_faults_raw": item.get("electrical_faults_raw"),
                "gas_faults_raw": item.get("gas_faults_raw"),
                "smoke_faults_raw": item.get("smoke_faults_raw"),
                "mrs_faults_raw": item.get("mrs_faults_raw"),
                "quoted_electrical_payment_raw": item.get("quoted_electrical_payment_raw"),
                "quoted_gas_payment_raw": item.get("quoted_gas_payment_raw"),
                "quoted_smoke_payment_raw": item.get("quoted_smoke_payment_raw"),
                "compliance_notes": item.get("compliance_notes"),
                "overall_state": state,
                "overall_reason": " ".join(dict.fromkeys(reasons)),
                "created_at": now,
                "updated_at": now,
            }
        )
    return out


@router.post("/import-xlsx")
async def import_xlsx(
    file: UploadFile = File(...),
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload an .xlsx compliance workbook.")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        rows = _parse_workbook(raw)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid Excel file.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse workbook: {e}")

    if not rows:
        raise HTTPException(status_code=400, detail="No compliance rows were detected in this workbook.")

    db.query(ComplianceProperty).filter(ComplianceProperty.mailbox == mailbox).delete(synchronize_session=False)
    state_counts: dict[str, int] = {}
    for row in rows:
        state_key = row["overall_state"].value
        state_counts[state_key] = state_counts.get(state_key, 0) + 1
        db.add(ComplianceProperty(mailbox=mailbox, **row))
    db.commit()

    return {
        "ok": True,
        "imported_rows": len(rows),
        "mailbox": mailbox,
        "imported_by": user.email,
        "state_counts": state_counts,
    }


@router.get("/summary")
def summary(
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = db.query(ComplianceProperty).filter(ComplianceProperty.mailbox == mailbox).all()
    state_counts = {state.value: 0 for state in ComplianceState}
    due_this_month = 0
    today = datetime.utcnow().date()
    window_end = today + timedelta(days=DUE_SOON_DAYS)
    for row in rows:
        state_counts[row.overall_state.value] = state_counts.get(row.overall_state.value, 0) + 1
        for dt in (row.gas_next_due_at, row.smoke_next_due_at, row.electrical_next_due_at):
            if dt and today <= dt.date() <= window_end:
                due_this_month += 1
                break
    return {
        "total": len(rows),
        "due_soon_window_days": DUE_SOON_DAYS,
        "due_soon_properties": state_counts.get(ComplianceState.DUE_SOON.value, 0),
        "overdue_properties": state_counts.get(ComplianceState.OVERDUE.value, 0),
        "action_required_properties": state_counts.get(ComplianceState.ACTION_REQUIRED.value, 0),
        "current_properties": state_counts.get(ComplianceState.CURRENT.value, 0),
        "unknown_properties": state_counts.get(ComplianceState.UNKNOWN.value, 0),
        "upcoming_due_dates": due_this_month,
        "state_counts": state_counts,
    }


@router.get("/properties")
def list_properties(
    state: ComplianceState | None = None,
    query: str | None = None,
    suburb: str | None = None,
    page: int = 1,
    page_size: int = 25,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(ComplianceProperty).filter(ComplianceProperty.mailbox == mailbox)
    if state:
        q = q.filter(ComplianceProperty.overall_state == state)
    if suburb and suburb.strip():
        q = q.filter(ComplianceProperty.suburb.ilike(suburb.strip()))
    if query and query.strip():
        like = f"%{query.strip()}%"
        q = q.filter(
            or_(
                ComplianceProperty.property_address.ilike(like),
                ComplianceProperty.suburb.ilike(like),
                ComplianceProperty.compliance_notes.ilike(like),
                ComplianceProperty.overall_reason.ilike(like),
            )
        )

    page = max(int(page or 1), 1)
    page_size = max(10, min(int(page_size or 25), 200))
    total = q.with_entities(func.count(ComplianceProperty.id)).scalar() or 0
    rows = (
        q.order_by(
            ComplianceProperty.overall_state.asc(),
            ComplianceProperty.suburb.asc().nullslast(),
            ComplianceProperty.property_address.asc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    def _serialize(row: ComplianceProperty) -> dict[str, Any]:
        return {
            "id": row.id,
            "property_address": row.property_address,
            "suburb": row.suburb,
            "state_code": row.state_code,
            "postcode": row.postcode,
            "overall_state": row.overall_state.value,
            "overall_reason": row.overall_reason,
            "gas_raw": row.gas_raw,
            "smoke_raw": row.smoke_raw,
            "electrical_raw": row.electrical_raw,
            "mrs_raw": row.mrs_raw,
            "pool_raw": row.pool_raw,
            "powerband_raw": row.powerband_raw,
            "disclosure_raw": row.disclosure_raw,
            "gas_last_checked_at": row.gas_last_checked_at,
            "gas_next_due_at": row.gas_next_due_at,
            "smoke_last_checked_at": row.smoke_last_checked_at,
            "smoke_next_due_at": row.smoke_next_due_at,
            "electrical_last_checked_at": row.electrical_last_checked_at,
            "electrical_next_due_at": row.electrical_next_due_at,
            "work_order_requested_at": row.work_order_requested_at,
            "report_result": row.report_result,
            "invoice_payment_status": row.invoice_payment_status,
            "electrical_faults_raw": row.electrical_faults_raw,
            "gas_faults_raw": row.gas_faults_raw,
            "smoke_faults_raw": row.smoke_faults_raw,
            "mrs_faults_raw": row.mrs_faults_raw,
            "compliance_notes": row.compliance_notes,
            "source_sheet": row.source_sheet,
        }

    return {
        "items": [_serialize(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (page * page_size) < total,
    }
