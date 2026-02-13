from __future__ import annotations

import calendar
import re
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.authz import get_current_user
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import RentDueTracker, RentTrackStatus, User
from app.schemas import RentDueItemOut, RentDueListOut

router = APIRouter()

NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
NS_REL = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


class RentItemUpdateIn(BaseModel):
    status: RentTrackStatus | None = None
    paid_on: datetime | None = None
    notes: str | None = None


def _col_to_num(col: str) -> int:
    n = 0
    for ch in col:
        n = (n * 26) + (ord(ch) - 64)
    return n


def _num_to_col(num: int) -> str:
    s = ""
    n = num
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def _excel_serial_to_datetime(value: float) -> datetime:
    return datetime(1899, 12, 30) + timedelta(days=value)


def _parse_date_from_any(raw: str, fallback_year: int | None = None) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None

    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})(?:\s*/\s*(\d{2,4}))?", text)
    if m:
        dd = int(m.group(1))
        mm = int(m.group(2))
        yy_s = m.group(3)
        if yy_s:
            yy = int(yy_s)
            if yy < 100:
                yy = 2000 + yy
        else:
            yy = fallback_year or datetime.utcnow().year
        try:
            return datetime(yy, mm, dd)
        except ValueError:
            return None

    try:
        n = float(text)
        if 15000 <= n <= 90000:
            return _excel_serial_to_datetime(n)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _normalize_status(raw: str) -> RentTrackStatus:
    text = (raw or "").strip().lower()
    if not text:
        return RentTrackStatus.DUE
    if "vacant" in text:
        return RentTrackStatus.VACANT
    if "part paid" in text or "partial" in text:
        return RentTrackStatus.PARTIAL
    if "await" in text or "clearance" in text:
        return RentTrackStatus.AWAITING_CLEARANCE
    if "paid" in text:
        return RentTrackStatus.PAID
    return RentTrackStatus.DUE


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root.findall("a:si", NS):
        parts = [t.text or "" for t in si.findall(".//a:t", NS)]
        out.append("".join(parts))
    return out


def _cell_value(cell: ET.Element, sst: list[str]) -> str:
    t = cell.attrib.get("t")
    v = cell.find("a:v", NS)
    is_el = cell.find("a:is", NS)
    if t == "s" and v is not None and v.text is not None:
        idx = int(v.text)
        return sst[idx] if 0 <= idx < len(sst) else ""
    if t == "inlineStr" and is_el is not None:
        return "".join((x.text or "") for x in is_el.findall(".//a:t", NS))
    if v is not None and v.text is not None:
        return str(v.text)
    return ""


def _sheet_rows(zf: zipfile.ZipFile, path: str, sst: list[str]) -> list[tuple[int, dict[str, str]]]:
    root = ET.fromstring(zf.read(path))
    rows: list[tuple[int, dict[str, str]]] = []
    for row in root.findall("a:sheetData/a:row", NS):
        row_num = int(row.attrib.get("r", "0"))
        vals: dict[str, str] = {}
        for cell in row.findall("a:c", NS):
            ref = cell.attrib.get("r", "")
            m = re.match(r"([A-Z]+)\d+", ref)
            if not m:
                continue
            col = m.group(1)
            val = _cell_value(cell, sst).strip()
            if val:
                vals[col] = val
        if vals:
            rows.append((row_num, vals))
    return rows


def _parse_monthly_sheet(sheet_name: str, rows: list[tuple[int, dict[str, str]]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    year_match = re.search(r"(20\d{2})", sheet_name)
    year = int(year_match.group(1)) if year_match else datetime.utcnow().year

    month_cols: dict[str, int] = {}
    for row_num, vals in rows:
        if row_num > 10:
            break
        for col, val in vals.items():
            if _col_to_num(col) < 3:
                continue
            key = val.strip().lower()
            if key in MONTHS:
                month_cols[col] = MONTHS[key]

    if not month_cols:
        return items

    current_due_day = 1
    for row_num, vals in rows:
        if row_num < 4:
            continue
        address = (vals.get("B") or "").strip()
        if not address:
            continue
        day_text = (vals.get("A") or "").strip()
        if day_text:
            try:
                current_due_day = max(1, min(31, int(float(day_text))))
            except ValueError:
                pass

        for col, month_num in month_cols.items():
            raw_value = (vals.get(col) or "").strip()
            last_day = calendar.monthrange(year, month_num)[1]
            due_day = max(1, min(current_due_day, last_day))
            due_date = datetime(year, month_num, due_day)
            status = _normalize_status(raw_value)
            paid_on = _parse_date_from_any(raw_value, fallback_year=year)

            items.append(
                {
                    "property_address": address,
                    "frequency": "MONTHLY",
                    "due_date": due_date,
                    "due_day": due_day,
                    "period_label": f"{year}-{month_num:02d}",
                    "source_sheet": sheet_name,
                    "status": status,
                    "raw_value": raw_value or None,
                    "paid_on": paid_on,
                    "notes": None,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
            )
    return items


def _parse_fortnight_sheet(sheet_name: str, rows: list[tuple[int, dict[str, str]]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not rows:
        return items

    header_idx = -1
    header_cols: dict[str, str] = {}
    for idx, (_row_num, vals) in enumerate(rows):
        candidates = {
            c: v for c, v in vals.items()
            if re.search(r"[A-Za-z]", v) and len(v) >= 8 and _col_to_num(c) >= 2
        }
        if len(candidates) >= 2:
            header_idx = idx
            header_cols = candidates
            break

    if header_idx < 0:
        return items

    # Each fortnightly property block is laid out in 3-column groups;
    # the due date sits in the next column after the address cell.
    groups: list[tuple[str, str]] = []
    for col, addr in sorted(header_cols.items(), key=lambda x: _col_to_num(x[0])):
        due_col = _num_to_col(_col_to_num(col) + 1)
        groups.append((addr.strip(), due_col))

    for _row_num, vals in rows[header_idx + 1:]:
        for address, due_col in groups:
            raw_due = (vals.get(due_col) or "").strip()
            if not raw_due:
                continue
            due_date = _parse_date_from_any(raw_due)
            items.append(
                {
                    "property_address": address,
                    "frequency": "FORTNIGHTLY",
                    "due_date": due_date,
                    "due_day": due_date.day if due_date else None,
                    "period_label": due_date.strftime("%Y-%m-%d") if due_date else None,
                    "source_sheet": sheet_name,
                    "status": RentTrackStatus.DUE,
                    "raw_value": raw_due,
                    "paid_on": None,
                    "notes": None,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
            )
    return items


def _parse_rent_workbook(content: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(BytesIO(content)) as zf:
        shared_strings = _read_shared_strings(zf)
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.attrib["Id"]: r.attrib["Target"] for r in rels}

        sheets: list[tuple[str, str]] = []
        for s in wb.findall("a:sheets/a:sheet", NS):
            name = s.attrib.get("name", "")
            rel_id = s.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            if not rel_id or rel_id not in rel_map:
                continue
            sheets.append((name, "xl/" + rel_map[rel_id]))

        parsed_items: list[dict[str, Any]] = []
        for name, target in sheets:
            rows = _sheet_rows(zf, target, shared_strings)
            lname = name.lower()
            if "fortn" in lname:
                parsed_items.extend(_parse_fortnight_sheet(name, rows))
            elif "monthly" in lname:
                parsed_items.extend(_parse_monthly_sheet(name, rows))
    return parsed_items


@router.post("/import-xlsx")
async def import_xlsx(
    file: UploadFile = File(...),
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload an .xlsx file.")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        parsed = _parse_rent_workbook(raw)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid Excel file.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse workbook: {e}")

    if not parsed:
        raise HTTPException(status_code=400, detail="No rent tracking rows were detected in this workbook.")

    db.query(RentDueTracker).filter(RentDueTracker.mailbox == mailbox).delete(synchronize_session=False)

    status_counts: dict[str, int] = {}
    for row in parsed:
        status_key = row["status"].value if isinstance(row["status"], RentTrackStatus) else str(row["status"])
        status_counts[status_key] = status_counts.get(status_key, 0) + 1
        db.add(RentDueTracker(mailbox=mailbox, **row))
    db.commit()

    return {
        "ok": True,
        "imported_rows": len(parsed),
        "mailbox": mailbox,
        "imported_by": user.email,
        "status_counts": status_counts,
    }


@router.get("/items", response_model=RentDueListOut)
def list_items(
    status: RentTrackStatus | None = None,
    frequency: str | None = None,
    query: str | None = None,
    month: str | None = None,
    page: int = 1,
    page_size: int = 50,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(RentDueTracker).filter(RentDueTracker.mailbox == mailbox)

    if status:
        q = q.filter(RentDueTracker.status == status)
    if frequency:
        q = q.filter(func.upper(RentDueTracker.frequency) == frequency.strip().upper())
    if query and query.strip():
        like = f"%{query.strip()}%"
        q = q.filter(
            or_(
                RentDueTracker.property_address.ilike(like),
                RentDueTracker.notes.ilike(like),
                RentDueTracker.raw_value.ilike(like),
            )
        )

    if month:
        try:
            start = datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid month. Use YYYY-MM.")
        _, month_last_day = calendar.monthrange(start.year, start.month)
        end = datetime(start.year, start.month, month_last_day, 23, 59, 59, 999999)
        q = q.filter(RentDueTracker.due_date.isnot(None)).filter(RentDueTracker.due_date >= start).filter(RentDueTracker.due_date <= end)

    page = max(int(page or 1), 1)
    page_size = max(10, min(int(page_size or 50), 200))
    total = q.with_entities(func.count(RentDueTracker.id)).scalar() or 0

    rows = (
        q.order_by(RentDueTracker.due_date.asc().nullslast(), RentDueTracker.property_address.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return RentDueListOut(
        items=[RentDueItemOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,
    )


@router.get("/summary")
def summary(
    month: str | None = None,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(RentDueTracker).filter(RentDueTracker.mailbox == mailbox)
    if month:
        try:
            start = datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid month. Use YYYY-MM.")
        _, month_last_day = calendar.monthrange(start.year, start.month)
        end = datetime(start.year, start.month, month_last_day, 23, 59, 59, 999999)
        q = q.filter(RentDueTracker.due_date.isnot(None)).filter(RentDueTracker.due_date >= start).filter(RentDueTracker.due_date <= end)

    rows = q.all()
    today = datetime.utcnow().date()
    next_7 = today + timedelta(days=7)
    unsettled = {RentTrackStatus.DUE, RentTrackStatus.PARTIAL, RentTrackStatus.AWAITING_CLEARANCE}

    overdue = 0
    due_next_7_days = 0
    for r in rows:
        if not r.due_date:
            continue
        d = r.due_date.date()
        if r.status in unsettled and d < today:
            overdue += 1
        if r.status in unsettled and today <= d <= next_7:
            due_next_7_days += 1

    status_counts = {
        "DUE": 0,
        "PAID": 0,
        "PARTIAL": 0,
        "VACANT": 0,
        "AWAITING_CLEARANCE": 0,
    }
    for r in rows:
        status_counts[r.status.value] = status_counts.get(r.status.value, 0) + 1

    return {
        "total": len(rows),
        "overdue": overdue,
        "due_next_7_days": due_next_7_days,
        "status_counts": status_counts,
    }


@router.patch("/items/{item_id}", response_model=RentDueItemOut)
def update_item(
    item_id: int,
    payload: RentItemUpdateIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = (
        db.query(RentDueTracker)
        .filter(RentDueTracker.mailbox == mailbox)
        .filter(RentDueTracker.id == item_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Tracker row not found.")

    if payload.status is not None:
        row.status = payload.status
        if payload.status == RentTrackStatus.PAID and not payload.paid_on and not row.paid_on:
            row.paid_on = datetime.utcnow()
    if payload.paid_on is not None:
        row.paid_on = payload.paid_on
    if payload.notes is not None:
        row.notes = payload.notes.strip() or None

    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return RentDueItemOut.model_validate(row)
