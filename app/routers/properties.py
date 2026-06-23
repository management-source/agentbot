from __future__ import annotations

import re
import zipfile
from datetime import datetime
from io import BytesIO
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.authz import get_current_user
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import ManagedProperty, User

router = APIRouter()

NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class PropertyCreateIn(BaseModel):
    property_address: str
    address_line_2: str | None = None
    suburb: str | None = None
    state_code: str | None = None
    postcode: str | None = None


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _normalize_address_key(value: str | None) -> str:
    text = _normalize_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_state_code(value: str | None) -> str | None:
    text = _normalize_text(value).upper()
    return text or None


def _property_identity_key(
    property_address: str | None,
    suburb: str | None = None,
    state_code: str | None = None,
    postcode: str | None = None,
) -> str:
    return _normalize_address_key(" ".join([x for x in [property_address, suburb, state_code, postcode] if x]))


def _split_full_address(value: str | None) -> dict[str, str | None]:
    text = _normalize_text(value)
    parts = [_normalize_text(part) for part in text.split(",") if _normalize_text(part)]
    if len(parts) < 2:
        return {
            "property_address": text,
            "suburb": None,
            "state_code": None,
            "postcode": None,
        }

    postcode = None
    state_code = None
    suburb = None
    remaining = parts[:]

    last = remaining[-1]
    postcode_match = re.search(r"\b(\d{4})\b$", last)
    if postcode_match:
        postcode = postcode_match.group(1)
        prefix = _normalize_text(last[: postcode_match.start()])
        remaining.pop()
        if prefix:
            prefix_upper = prefix.upper()
            if re.fullmatch(r"[A-Z]{2,3}", prefix_upper):
                state_code = prefix_upper
            else:
                suburb = prefix

    if remaining:
        maybe_state = remaining[-1].upper()
        if not state_code and re.fullmatch(r"[A-Z]{2,3}", maybe_state):
            state_code = maybe_state
            remaining.pop()

    if remaining and not suburb and len(remaining) >= 2:
        suburb = remaining.pop()

    property_address = ", ".join(remaining) if remaining else text
    return {
        "property_address": property_address,
        "suburb": suburb,
        "state_code": state_code,
        "postcode": postcode,
    }


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
            vals[m.group(1)] = _cell_value(cell, sst).strip()
        if any(v for v in vals.values()):
            rows.append(vals)
    return rows


def _header_map(rows: list[dict[str, str]]) -> tuple[int, dict[str, str]]:
    aliases = {
        "property_address": {"property address", "address", "property", "property name", "address 1", "street address"},
        "address_line_2": {"address 2", "address2", "unit", "line 2"},
        "suburb": {"suburb", "city", "town"},
        "state_code": {"state", "province"},
        "postcode": {"postcode", "post code", "zip", "zip code"},
    }
    for idx, row in enumerate(rows[:10]):
        mapping: dict[str, str] = {}
        for col, value in row.items():
            key = _normalize_text(value).lower()
            for field, names in aliases.items():
                if key in names:
                    mapping[field] = col
        if "property_address" in mapping:
            return idx, mapping
    raise ValueError("Could not detect property address columns in the uploaded workbook.")


def _detect_address_only_column(rows: list[dict[str, str]]) -> str:
    scores: dict[str, int] = {}
    for row in rows[:200]:
        for col, value in row.items():
            text = _normalize_text(value)
            if len(text) < 5:
                continue
            score = 1
            if re.search(r"\d", text):
                score += 2
            if "," in text:
                score += 2
            if re.search(r"\b\d{4}\b", text):
                score += 2
            scores[col] = scores.get(col, 0) + score
    if not scores:
        raise ValueError("Could not detect property address columns in the uploaded workbook.")
    return max(scores, key=scores.get)


def _parse_property_workbook(content: bytes) -> list[dict[str, str | None]]:
    with zipfile.ZipFile(BytesIO(content)) as zf:
        shared_strings = _read_shared_strings(zf)
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
        first_sheet_path = None
        for s in wb.findall("a:sheets/a:sheet", NS):
            rel_id = s.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            if rel_id and rel_id in rel_map:
                first_sheet_path = "xl/" + rel_map[rel_id]
                break
        if not first_sheet_path:
            return []
        rows = _sheet_rows(zf, first_sheet_path, shared_strings)

    try:
        header_idx, mapping = _header_map(rows)
        data_rows = rows[header_idx + 1:]
    except ValueError:
        mapping = {"property_address": _detect_address_only_column(rows)}
        data_rows = rows

    items: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for row in data_rows:
        raw_address = _normalize_text(row.get(mapping["property_address"]))
        parsed = _split_full_address(raw_address)
        address = parsed["property_address"] or raw_address
        suburb = _normalize_text(row.get(mapping.get("suburb", ""))) or parsed["suburb"]
        state_code = _normalize_state_code(row.get(mapping.get("state_code", ""))) or parsed["state_code"]
        postcode = _normalize_text(row.get(mapping.get("postcode", ""))) or parsed["postcode"]
        if not address:
            continue
        key = _property_identity_key(address, suburb, state_code, postcode)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "property_address": address,
                "address_line_2": _normalize_text(row.get(mapping.get("address_line_2", ""))) or None,
                "suburb": suburb,
                "state_code": state_code,
                "postcode": postcode,
            }
        )
    return items


def _property_to_dict(row: ManagedProperty) -> dict[str, object]:
    return {
        "id": row.id,
        "property_address": row.property_address,
        "address_line_2": row.address_line_2,
        "suburb": row.suburb,
        "state_code": row.state_code,
        "postcode": row.postcode,
        "is_active": row.is_active,
        "source": row.source,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.post("/import-xlsx")
async def import_xlsx(
    file: UploadFile = File(...),
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload an .xlsx property workbook.")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        rows = _parse_property_workbook(raw)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid Excel file.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse workbook: {e}")

    if not rows:
        raise HTTPException(status_code=400, detail="No property rows were detected in this workbook.")

    now = datetime.utcnow()
    imported = 0
    existing = db.query(ManagedProperty).filter(ManagedProperty.mailbox == mailbox).all()
    existing_by_key = {
        _property_identity_key(prop.property_address, prop.suburb, prop.state_code, prop.postcode): prop
        for prop in existing
    }
    for item in rows:
        address_key = _property_identity_key(
            item["property_address"],
            item["suburb"],
            item["state_code"],
            item["postcode"],
        )
        match = existing_by_key.get(address_key)
        if match:
            match.address_line_2 = item["address_line_2"]
            match.suburb = item["suburb"]
            match.state_code = item["state_code"]
            match.postcode = item["postcode"]
            match.is_active = True
            match.source = "xlsx_import"
            match.updated_at = now
        else:
            new_row = ManagedProperty(
                mailbox=mailbox,
                property_address=item["property_address"] or "",
                address_line_2=item["address_line_2"],
                suburb=item["suburb"],
                state_code=item["state_code"],
                postcode=item["postcode"],
                is_active=True,
                source="xlsx_import",
                created_at=now,
                updated_at=now,
            )
            db.add(new_row)
            existing_by_key[address_key] = new_row
        imported += 1
    db.commit()
    return {"ok": True, "imported_rows": imported}


@router.post("")
def create_property(
    payload: PropertyCreateIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    address = _normalize_text(payload.property_address)
    if not address:
        raise HTTPException(status_code=400, detail="Property address is required.")
    suburb = _normalize_text(payload.suburb) or None
    state_code = _normalize_state_code(payload.state_code)
    postcode = _normalize_text(payload.postcode) or None
    target_key = _property_identity_key(address, suburb, state_code, postcode)
    existing = db.query(ManagedProperty).filter(ManagedProperty.mailbox == mailbox).all()
    for row in existing:
        if _property_identity_key(row.property_address, row.suburb, row.state_code, row.postcode) == target_key:
            raise HTTPException(status_code=400, detail="This property already exists.")

    now = datetime.utcnow()
    row = ManagedProperty(
        mailbox=mailbox,
        property_address=address,
        address_line_2=_normalize_text(payload.address_line_2) or None,
        suburb=suburb,
        state_code=state_code,
        postcode=postcode,
        is_active=True,
        source="manual",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _property_to_dict(row)


@router.get("")
def list_properties(
    query: str | None = None,
    page: int = 1,
    page_size: int = 25,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(ManagedProperty).filter(ManagedProperty.mailbox == mailbox).filter(ManagedProperty.is_active == True)
    if query and query.strip():
        like = f"%{query.strip()}%"
        q = q.filter(
            or_(
                ManagedProperty.property_address.ilike(like),
                ManagedProperty.suburb.ilike(like),
                ManagedProperty.postcode.ilike(like),
            )
        )
    page = max(int(page or 1), 1)
    page_size = max(10, min(int(page_size or 25), 200))
    total = q.with_entities(func.count(ManagedProperty.id)).scalar() or 0
    rows = (
        q.order_by(ManagedProperty.property_address.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_property_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (page * page_size) < total,
    }


@router.get("/options")
def property_options(
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = (
        db.query(ManagedProperty)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.is_active == True)
        .order_by(ManagedProperty.property_address.asc())
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "label": ", ".join([x for x in [r.property_address, r.suburb, r.postcode] if x]),
            }
            for r in rows
        ]
    }
