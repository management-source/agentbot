from __future__ import annotations

import re
import zipfile
import json
from datetime import datetime
from io import BytesIO
from urllib.parse import urlencode
from urllib.request import urlopen
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
VICMAP_ADDRESS_QUERY_URL = "https://services-ap1.arcgis.com/P744lA0wf4LlBZ84/ArcGIS/rest/services/Vicmap_Address/FeatureServer/0/query"


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


def _split_multi_value(value: str | None) -> list[str]:
    return [_normalize_text(part) for part in str(value or "").split(",") if _normalize_text(part)]


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = re.sub(r"\D+", "", value) or value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _contact_book(
    *,
    names_raw: str | None,
    emails_raw: str | None,
    mobiles_raw: str | None,
    phones_raw: str | None,
    default_label: str,
    is_company: bool = False,
) -> dict[str, object]:
    names = _split_multi_value(names_raw)
    emails = _split_multi_value(emails_raw)
    mobiles = _split_multi_value(mobiles_raw)
    phones = _split_multi_value(phones_raw)

    if not names and not emails and not mobiles and not phones:
        return {"contacts": [], "extra_mobiles": [], "extra_phones": [], "raw": {}}

    contact_count = max(len(names), len(emails), 1)
    contacts: list[dict[str, object]] = []
    for idx in range(contact_count):
        if contact_count == 1:
            contact_mobiles = mobiles
            contact_phones = phones
        else:
            contact_mobiles = [mobiles[idx]] if idx < len(mobiles) else []
            contact_phones = [phones[idx]] if idx < len(phones) else []
        all_phones = _dedupe(contact_mobiles + contact_phones)
        contacts.append(
            {
                "name": names[idx] if idx < len(names) else (default_label if contact_count == 1 else f"{default_label} {idx + 1}"),
                "email": emails[idx] if idx < len(emails) else "",
                "mobile": contact_mobiles[0] if contact_mobiles else "",
                "phone": contact_phones[0] if contact_phones else "",
                "phones": all_phones,
                "is_company": is_company,
            }
        )

    extra_mobiles = mobiles[contact_count:] if contact_count > 1 else []
    extra_phones = phones[contact_count:] if contact_count > 1 else []
    return {
        "contacts": contacts,
        "extra_mobiles": _dedupe(extra_mobiles),
        "extra_phones": _dedupe(extra_phones),
        "raw": {
            "names": names,
            "emails": emails,
            "mobiles": mobiles,
            "phones": phones,
        },
    }


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str | None) -> dict[str, object]:
    if not value:
        return {"contacts": [], "extra_mobiles": [], "extra_phones": [], "raw": {}}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"contacts": [], "extra_mobiles": [], "extra_phones": [], "raw": {}}
    except Exception:
        return {"contacts": [], "extra_mobiles": [], "extra_phones": [], "raw": {}}


def _primary_contact(book: dict[str, object]) -> dict[str, str]:
    contacts = book.get("contacts")
    if not isinstance(contacts, list) or not contacts:
        return {"name": "", "email": "", "phone": ""}
    first = contacts[0] if isinstance(contacts[0], dict) else {}
    phones = first.get("phones") if isinstance(first.get("phones"), list) else []
    phone = first.get("mobile") or first.get("phone") or (phones[0] if phones else "")
    return {
        "name": str(first.get("name") or ""),
        "email": str(first.get("email") or ""),
        "phone": str(phone or ""),
    }


def _normalize_state_code(value: str | None) -> str | None:
    text = _normalize_text(value).upper()
    return text or None


def _vic_state_code(value: str | None) -> str:
    state = _normalize_state_code(value) or "VIC"
    if state not in {"VIC", "VICTORIA"}:
        raise HTTPException(status_code=400, detail="Only Victorian properties can be added.")
    return "VIC"


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
            state_match = re.fullmatch(r"(.+?)\s+(VIC|VICTORIA|NSW|QLD|SA|WA|TAS|NT|ACT)", prefix_upper)
            if state_match:
                suburb = _normalize_text(prefix[: len(state_match.group(1))])
                state_code = state_match.group(2)
            elif prefix_upper in {"VIC", "VICTORIA", "NSW", "QLD", "SA", "WA", "TAS", "NT", "ACT"}:
                state_code = prefix_upper
            else:
                suburb = prefix

    if remaining:
        maybe_state = remaining[-1].upper()
        if not state_code and maybe_state in {"VIC", "VICTORIA", "NSW", "QLD", "SA", "WA", "TAS", "NT", "ACT"}:
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
        "crm_property_id": {"property id", "crm property id", "property code"},
        "property_address": {"property address", "address", "property", "property name", "address 1", "street address"},
        "address_line_2": {"address 2", "address2", "unit", "line 2"},
        "suburb": {"suburb", "city", "town"},
        "state_code": {"state", "province"},
        "postcode": {"postcode", "post code", "zip", "zip code"},
        "property_type": {"property type", "type"},
        "rental_type": {"rental type", "rental"},
        "key_number": {"key number", "key no", "keys", "key"},
        "owner_is_company": {"owner is a company", "owner company", "landlord company"},
        "owner_names": {"owner/landlord", "owner", "landlord", "owners", "landlords"},
        "owner_emails": {"owner/landlord email", "owner email", "landlord email", "owner emails", "landlord emails"},
        "owner_mobiles": {"landlord mobile", "owner mobile", "landlord mobiles", "owner mobiles"},
        "owner_phones": {"landlord phone", "owner phone", "landlord phones", "owner phones"},
        "tenant_names": {"tenant", "tenants", "tenant name", "tenant names"},
        "tenant_emails": {"tenant email", "tenant emails"},
        "tenant_mobiles": {"tenant mobile", "tenant mobiles"},
        "tenant_phones": {"tenant phone", "tenant phones"},
        "tenancy_status": {"status", "tenancy status", "lease status"},
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
        def mapped(field: str) -> str:
            col = mapping.get(field)
            return _normalize_text(row.get(col, "")) if col else ""

        raw_address = _normalize_text(row.get(mapping["property_address"]))
        parsed = _split_full_address(raw_address)
        address = parsed["property_address"] or raw_address
        suburb = mapped("suburb") or parsed["suburb"]
        state_code = _normalize_state_code(mapped("state_code")) or parsed["state_code"]
        postcode = mapped("postcode") or parsed["postcode"]
        if not address:
            continue
        key = _property_identity_key(address, suburb, state_code, postcode)
        if key in seen:
            continue
        seen.add(key)
        owner_is_company = mapped("owner_is_company").lower() in {"yes", "y", "true", "1", "company"}
        owners = _contact_book(
            names_raw=mapped("owner_names"),
            emails_raw=mapped("owner_emails"),
            mobiles_raw=mapped("owner_mobiles"),
            phones_raw=mapped("owner_phones"),
            default_label="Landlord",
            is_company=owner_is_company,
        )
        tenants = _contact_book(
            names_raw=mapped("tenant_names"),
            emails_raw=mapped("tenant_emails"),
            mobiles_raw=mapped("tenant_mobiles"),
            phones_raw=mapped("tenant_phones"),
            default_label="Tenant",
        )
        items.append(
            {
                "crm_property_id": mapped("crm_property_id") or None,
                "property_address": address,
                "address_line_2": mapped("address_line_2") or None,
                "suburb": suburb,
                "state_code": state_code,
                "postcode": postcode,
                "property_type": mapped("property_type") or None,
                "rental_type": mapped("rental_type") or None,
                "key_number": mapped("key_number") or None,
                "owner_is_company": owner_is_company,
                "tenancy_status": mapped("tenancy_status") or None,
                "owners_json": _json_dumps(owners),
                "tenants_json": _json_dumps(tenants),
            }
        )
    return items


def _property_to_dict(row: ManagedProperty) -> dict[str, object]:
    owners = _json_loads(row.owners_json)
    tenants = _json_loads(row.tenants_json)
    return {
        "id": row.id,
        "crm_property_id": row.crm_property_id,
        "property_address": row.property_address,
        "address_line_2": row.address_line_2,
        "suburb": row.suburb,
        "state_code": row.state_code,
        "postcode": row.postcode,
        "property_type": row.property_type,
        "rental_type": row.rental_type,
        "key_number": row.key_number,
        "owner_is_company": row.owner_is_company,
        "tenancy_status": row.tenancy_status,
        "owners": owners,
        "tenants": tenants,
        "primary_owner": _primary_contact(owners),
        "primary_tenant": _primary_contact(tenants),
        "is_active": row.is_active,
        "source": row.source,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _title_address(value: str | None) -> str:
    return _normalize_text(value).title()


def _vicmap_address_where(query: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", query.upper())[:6]
    if not words:
        raise HTTPException(status_code=400, detail="Search query is required.")
    clauses = ["state = 'VIC'"]
    clauses.extend(f"UPPER(ezi_address) LIKE '%{word}%'" for word in words)
    return " AND ".join(clauses)


@router.get("/address-suggestions")
def address_suggestions(
    q: str | None = None,
    mailbox: str = Depends(get_current_mailbox),
    _user: User = Depends(get_current_user),
):
    del mailbox
    query = _normalize_text(q)
    if len(query) < 3:
        return {"items": []}

    params = {
        "where": _vicmap_address_where(query),
        "outFields": "ezi_address,num_road_address,locality_name,state,postcode",
        "returnGeometry": "false",
        "resultRecordCount": "12",
        "orderByFields": "ezi_address ASC",
        "f": "json",
    }
    url = f"{VICMAP_ADDRESS_QUERY_URL}?{urlencode(params)}"
    try:
        with urlopen(url, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return {"items": []}

    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for feature in payload.get("features", []):
        attrs = feature.get("attributes") or {}
        state = _normalize_state_code(attrs.get("state"))
        if state not in {"VIC", "VICTORIA"}:
            continue
        street = _title_address(attrs.get("num_road_address") or attrs.get("ezi_address"))
        suburb = _title_address(attrs.get("locality_name"))
        postcode = _normalize_text(attrs.get("postcode"))
        if not street or not suburb:
            continue
        label_tail = " ".join([x for x in [suburb, "VIC", postcode] if x])
        label = ", ".join([street, label_tail])
        key = _property_identity_key(street, suburb, "VIC", postcode)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "label": label,
                "property_address": street,
                "suburb": suburb,
                "state_code": "VIC",
                "postcode": postcode,
                "source": "vicmap",
            }
        )
    return {"items": items}


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
            match.crm_property_id = item.get("crm_property_id")
            match.address_line_2 = item["address_line_2"]
            match.suburb = item["suburb"]
            match.state_code = _vic_state_code(item["state_code"])
            match.postcode = item["postcode"]
            match.property_type = item.get("property_type")
            match.rental_type = item.get("rental_type")
            match.key_number = item.get("key_number")
            match.owner_is_company = bool(item.get("owner_is_company"))
            match.tenancy_status = item.get("tenancy_status")
            match.owners_json = item.get("owners_json")
            match.tenants_json = item.get("tenants_json")
            match.is_active = True
            match.source = "crm_import"
            match.updated_at = now
        else:
            new_row = ManagedProperty(
                mailbox=mailbox,
                crm_property_id=item.get("crm_property_id"),
                property_address=item["property_address"] or "",
                address_line_2=item["address_line_2"],
                suburb=item["suburb"],
                state_code=_vic_state_code(item["state_code"]),
                postcode=item["postcode"],
                property_type=item.get("property_type"),
                rental_type=item.get("rental_type"),
                key_number=item.get("key_number"),
                owner_is_company=bool(item.get("owner_is_company")),
                tenancy_status=item.get("tenancy_status"),
                owners_json=item.get("owners_json"),
                tenants_json=item.get("tenants_json"),
                is_active=True,
                source="crm_import",
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
    parsed_address = _split_full_address(address)
    if parsed_address.get("state_code"):
        address = parsed_address["property_address"] or address
    suburb = _normalize_text(payload.suburb) or parsed_address.get("suburb")
    state_code = _vic_state_code(parsed_address.get("state_code") or payload.state_code)
    postcode = _normalize_text(payload.postcode) or parsed_address.get("postcode")
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


@router.delete("/{property_id}")
def delete_property(
    property_id: int,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = (
        db.query(ManagedProperty)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.id == property_id)
        .filter(ManagedProperty.is_active == True)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Property not found.")
    row.is_active = False
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


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
                ManagedProperty.crm_property_id.ilike(like),
                ManagedProperty.property_type.ilike(like),
                ManagedProperty.rental_type.ilike(like),
                ManagedProperty.key_number.ilike(like),
                ManagedProperty.tenancy_status.ilike(like),
                ManagedProperty.owners_json.ilike(like),
                ManagedProperty.tenants_json.ilike(like),
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
                "crm_property_id": r.crm_property_id,
                "property_address": r.property_address,
                "address_line_2": r.address_line_2,
                "suburb": r.suburb,
                "state_code": r.state_code,
                "postcode": r.postcode,
                "property_type": r.property_type,
                "rental_type": r.rental_type,
                "key_number": r.key_number,
                "owner_is_company": r.owner_is_company,
                "tenancy_status": r.tenancy_status,
                "owners": _json_loads(r.owners_json),
                "tenants": _json_loads(r.tenants_json),
                "primary_owner": _primary_contact(_json_loads(r.owners_json)),
                "primary_tenant": _primary_contact(_json_loads(r.tenants_json)),
            }
            for r in rows
        ]
    }
