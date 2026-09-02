from __future__ import annotations

import re
import zipfile
import json
import uuid
from datetime import date, datetime
from io import BytesIO
from urllib.parse import urlencode
from urllib.request import urlopen
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.authz import get_current_user, require_role
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import ManagedProperty, User, UserRole

router = APIRouter()

NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
VICMAP_ADDRESS_QUERY_URL = "https://services-ap1.arcgis.com/P744lA0wf4LlBZ84/ArcGIS/rest/services/Vicmap_Address/FeatureServer/0/query"
LISTING_STATUSES = {"OPEN", "CLOSED"}


class PropertyContactIn(BaseModel):
    name: str | None = None
    email: str | None = None
    mobile: str | None = None
    phone: str | None = None
    phones: list[str] | None = None
    is_company: bool = False
    lease_start_date: str | None = None
    lease_end_date: str | None = None
    lease_amount: str | None = None
    lease_frequency: str | None = None


class PropertyKeyIn(BaseModel):
    key_number: str | None = None
    description: str | None = None
    location: str | None = None


class SocialMediaHistoryIn(BaseModel):
    date: str | None = None
    platform: str | None = None
    url: str | None = None
    notes: str | None = None


class ListingInspectionIn(BaseModel):
    id: str | None = None
    date: str | None = None
    start_time: str | None = None
    finish_time: str | None = None
    notes: str | None = None


class PropertyCreateIn(BaseModel):
    property_address: str
    address_line_2: str | None = None
    suburb: str | None = None
    state_code: str | None = None
    postcode: str | None = None
    crm_property_id: str | None = None
    property_type: str | None = None
    rental_type: str | None = None
    key_number: str | None = None
    listing_status: str = "OPEN"
    keys: list[PropertyKeyIn] | None = None
    social_media_history: list[SocialMediaHistoryIn] | None = None
    inspections: list[ListingInspectionIn] | None = None
    owner_is_company: bool = False
    tenancy_status: str | None = None
    owners: list[PropertyContactIn] | None = None
    tenants: list[PropertyContactIn] | None = None
    occupants: list[PropertyContactIn] | None = None


class PropertyUpdateIn(BaseModel):
    property_address: str | None = None
    address_line_2: str | None = None
    suburb: str | None = None
    state_code: str | None = None
    postcode: str | None = None
    crm_property_id: str | None = None
    property_type: str | None = None
    rental_type: str | None = None
    key_number: str | None = None
    listing_status: str | None = None
    keys: list[PropertyKeyIn] | None = None
    social_media_history: list[SocialMediaHistoryIn] | None = None
    inspections: list[ListingInspectionIn] | None = None
    owner_is_company: bool | None = None
    tenancy_status: str | None = None
    owners: list[PropertyContactIn] | None = None
    tenants: list[PropertyContactIn] | None = None
    occupants: list[PropertyContactIn] | None = None


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


def _contact_book_from_contacts(
    contacts_raw: list[PropertyContactIn] | None,
    *,
    default_label: str,
    is_company_default: bool = False,
    include_lease_fields: bool = False,
) -> dict[str, object]:
    contacts: list[dict[str, object]] = []
    raw_names: list[str] = []
    raw_emails: list[str] = []
    raw_mobiles: list[str] = []
    raw_phones: list[str] = []
    for idx, item in enumerate(contacts_raw or []):
        name = _normalize_text(item.name) or f"{default_label} {idx + 1}"
        email = _normalize_text(item.email)
        mobile = _normalize_text(item.mobile)
        phone = _normalize_text(item.phone)
        extra_phones = [_normalize_text(value) for value in (item.phones or []) if _normalize_text(value)]
        phones = _dedupe([mobile, phone, *extra_phones])
        lease_start_date = _normalize_text(item.lease_start_date)
        lease_end_date = _normalize_text(item.lease_end_date)
        lease_amount = _normalize_text(item.lease_amount)
        lease_frequency = _normalize_text(item.lease_frequency)
        if not name and not email and not phones and not any(
            (lease_start_date, lease_end_date, lease_amount, lease_frequency)
        ):
            continue
        raw_names.append(name)
        if email:
            raw_emails.append(email)
        if mobile:
            raw_mobiles.append(mobile)
        if phone:
            raw_phones.append(phone)
        contact: dict[str, object] = {
            "name": name,
            "email": email,
            "mobile": mobile,
            "phone": phone,
            "phones": phones,
            "is_company": bool(item.is_company or is_company_default),
        }
        if include_lease_fields:
            contact.update(
                {
                    "lease_start_date": lease_start_date,
                    "lease_end_date": lease_end_date,
                    "lease_amount": lease_amount,
                    "lease_frequency": lease_frequency,
                }
            )
        contacts.append(contact)
    return {
        "contacts": contacts,
        "extra_mobiles": [],
        "extra_phones": [],
        "raw": {
            "names": raw_names,
            "emails": raw_emails,
            "mobiles": raw_mobiles,
            "phones": raw_phones,
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


def _json_list_loads(value: str | None) -> list[dict[str, object]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _item_dict(item: BaseModel | dict[str, object]) -> dict[str, object]:
    if isinstance(item, BaseModel):
        return item.model_dump()
    return dict(item) if isinstance(item, dict) else {}


def _item_text(item: dict[str, object], field: str) -> str:
    value = item.get(field)
    return _normalize_text(str(value)) if value is not None else ""


def _normalize_listing_status(value: str | None, *, default: str | None = "OPEN") -> str:
    if value is None:
        if default is not None:
            return default
        raise HTTPException(status_code=400, detail="Listing status is required.")
    status = _normalize_text(value).upper()
    if status not in LISTING_STATUSES:
        raise HTTPException(status_code=400, detail="Listing status must be OPEN or CLOSED.")
    return status


def _normalize_property_keys(
    items: list[PropertyKeyIn] | list[dict[str, object]] | None,
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for raw in items or []:
        item = _item_dict(raw)
        entry = {
            "key_number": _item_text(item, "key_number"),
            "description": _item_text(item, "description"),
            "location": _item_text(item, "location"),
        }
        if any(entry.values()):
            normalized.append(entry)
    return normalized


def _normalize_social_media_history(
    items: list[SocialMediaHistoryIn] | list[dict[str, object]] | None,
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for raw in items or []:
        item = _item_dict(raw)
        entry = {
            "date": _item_text(item, "date"),
            "platform": _item_text(item, "platform"),
            "url": _item_text(item, "url"),
            "notes": _item_text(item, "notes"),
        }
        if any(entry.values()):
            normalized.append(entry)
    return normalized


def _normalize_listing_inspections(
    items: list[ListingInspectionIn] | list[dict[str, object]] | None,
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for raw in items or []:
        item = _item_dict(raw)
        inspection_date = _item_text(item, "date")
        start_time = _item_text(item, "start_time")
        finish_time = _item_text(item, "finish_time")
        if not inspection_date:
            raise HTTPException(status_code=400, detail="Inspection date is required.")
        try:
            date.fromisoformat(inspection_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Inspection date must use YYYY-MM-DD format.") from exc
        try:
            start = datetime.strptime(start_time, "%H:%M").time()
            finish = datetime.strptime(finish_time, "%H:%M").time()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Inspection times must use HH:MM format.") from exc
        if finish <= start:
            raise HTTPException(status_code=400, detail="Inspection finish time must be after start time.")
        inspection_id = _item_text(item, "id") or uuid.uuid4().hex
        if inspection_id in seen_ids:
            raise HTTPException(status_code=400, detail="Inspection IDs must be unique within a listing.")
        seen_ids.add(inspection_id)
        normalized.append(
            {
                "id": inspection_id,
                "date": inspection_date,
                "start_time": start_time,
                "finish_time": finish_time,
                "notes": _item_text(item, "notes"),
            }
        )
    return normalized


def _occupants_from_book(book: dict[str, object]) -> list[dict[str, object]]:
    contacts = book.get("contacts") if isinstance(book, dict) else []
    if not isinstance(contacts, list):
        return []
    occupants: list[dict[str, object]] = []
    for raw in contacts:
        if not isinstance(raw, dict):
            continue
        phones = raw.get("phones") if isinstance(raw.get("phones"), list) else []
        occupants.append(
            {
                "name": str(raw.get("name") or ""),
                "email": str(raw.get("email") or ""),
                "mobile": str(raw.get("mobile") or ""),
                "phone": str(raw.get("phone") or ""),
                "phones": [str(value) for value in phones if value],
                "is_company": bool(raw.get("is_company")),
                "lease_start_date": str(raw.get("lease_start_date") or ""),
                "lease_end_date": str(raw.get("lease_end_date") or ""),
                "lease_amount": str(raw.get("lease_amount") or ""),
                "lease_frequency": str(raw.get("lease_frequency") or ""),
            }
        )
    return occupants


def _property_keys(row: ManagedProperty) -> list[dict[str, str]]:
    stored = _normalize_property_keys(_json_list_loads(row.keys_json))
    if stored:
        return stored
    legacy_key_number = _normalize_text(row.key_number)
    if not legacy_key_number:
        return []
    return [{"key_number": legacy_key_number, "description": "", "location": ""}]


def _contacts_from_book(book: dict[str, object]) -> list[PropertyContactIn]:
    contacts = book.get("contacts") if isinstance(book, dict) else []
    if not isinstance(contacts, list):
        return []
    out: list[PropertyContactIn] = []
    for item in contacts:
        if not isinstance(item, dict):
            continue
        phones = item.get("phones") if isinstance(item.get("phones"), list) else []
        out.append(
            PropertyContactIn(
                name=str(item.get("name") or ""),
                email=str(item.get("email") or ""),
                mobile=str(item.get("mobile") or ""),
                phone=str(item.get("phone") or ""),
                phones=[str(value) for value in phones if value],
                is_company=bool(item.get("is_company")),
                lease_start_date=str(item.get("lease_start_date") or ""),
                lease_end_date=str(item.get("lease_end_date") or ""),
                lease_amount=str(item.get("lease_amount") or ""),
                lease_frequency=str(item.get("lease_frequency") or ""),
            )
        )
    return out


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


def _crm_identity_key(value: str | None) -> str:
    return _normalize_text(value).lower()


def _property_match_keys(
    property_address: str | None,
    suburb: str | None = None,
    state_code: str | None = None,
    postcode: str | None = None,
) -> list[str]:
    keys: list[str] = []

    def add_key(*parts: str | None) -> None:
        key = _normalize_address_key(" ".join([part for part in parts if part]))
        if key and key not in keys:
            keys.append(key)

    add_key(property_address, suburb, state_code, postcode)
    add_key(property_address, suburb, postcode)

    parsed = _split_full_address(property_address)
    parsed_address = parsed.get("property_address")
    parsed_suburb = suburb or parsed.get("suburb")
    parsed_state = state_code or parsed.get("state_code")
    parsed_postcode = postcode or parsed.get("postcode")
    add_key(parsed_address, parsed_suburb, parsed_state, parsed_postcode)
    add_key(parsed_address, parsed_suburb, parsed_postcode)

    return keys


def _index_property(
    row: ManagedProperty,
    by_crm: dict[str, list[ManagedProperty]],
    by_address: dict[str, list[ManagedProperty]],
) -> None:
    crm_key = _crm_identity_key(row.crm_property_id)
    if crm_key and row not in by_crm.setdefault(crm_key, []):
        by_crm[crm_key].append(row)
    for key in _property_match_keys(row.property_address, row.suburb, row.state_code, row.postcode):
        if row not in by_address.setdefault(key, []):
            by_address[key].append(row)


def _best_property_match(candidates: list[ManagedProperty]) -> ManagedProperty | None:
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: (not bool(row.is_active), row.id or 0))[0]


def _matching_existing_properties(
    item: dict[str, str | bool | None],
    by_crm: dict[str, list[ManagedProperty]],
    by_address: dict[str, list[ManagedProperty]],
) -> list[ManagedProperty]:
    candidates: list[ManagedProperty] = []

    def add_candidates(rows: list[ManagedProperty]) -> None:
        for row in rows:
            if row not in candidates:
                candidates.append(row)

    crm_key = _crm_identity_key(str(item.get("crm_property_id") or ""))
    if crm_key:
        add_candidates(by_crm.get(crm_key, []))
    for key in _property_match_keys(
        str(item.get("property_address") or ""),
        str(item.get("suburb") or ""),
        str(item.get("state_code") or ""),
        str(item.get("postcode") or ""),
    ):
        add_candidates(by_address.get(key, []))
    return candidates


def _apply_imported_property(
    row: ManagedProperty,
    item: dict[str, str | bool | None],
    now: datetime,
) -> None:
    row.crm_property_id = item.get("crm_property_id") or row.crm_property_id
    row.property_address = str(item["property_address"] or row.property_address or "")
    row.address_line_2 = item["address_line_2"]
    row.suburb = item["suburb"]
    row.state_code = _vic_state_code(str(item["state_code"] or "VIC"))
    row.postcode = item["postcode"]
    row.property_type = item.get("property_type")
    row.rental_type = item.get("rental_type")
    row.key_number = item.get("key_number")
    stored_keys = _normalize_property_keys(_json_list_loads(row.keys_json))
    if stored_keys:
        stored_keys[0]["key_number"] = _normalize_text(row.key_number)
        row.keys_json = _json_dumps(stored_keys)
    row.owner_is_company = bool(item.get("owner_is_company"))
    row.tenancy_status = item.get("tenancy_status")
    row.owners_json = item.get("owners_json")
    row.tenants_json = item.get("tenants_json")
    row.is_active = True
    row.source = "crm_import"
    row.updated_at = now


def _apply_manual_property_payload(
    row: ManagedProperty,
    payload: PropertyCreateIn | PropertyUpdateIn,
    *,
    address: str,
    suburb: str | None,
    state_code: str,
    postcode: str | None,
    now: datetime,
) -> None:
    listing_status = _normalize_listing_status(payload.listing_status)
    owners_book = _contact_book_from_contacts(
        payload.owners,
        default_label="Landlord",
        is_company_default=bool(payload.owner_is_company),
    )
    using_occupants_alias = payload.occupants is not None
    occupant_contacts = payload.occupants if using_occupants_alias else payload.tenants
    occupants_book = _contact_book_from_contacts(
        occupant_contacts,
        default_label="Occupant" if using_occupants_alias else "Tenant",
        include_lease_fields=True,
    )
    property_keys = _normalize_property_keys(payload.keys) if payload.keys is not None else None
    social_media_history = (
        _normalize_social_media_history(payload.social_media_history)
        if payload.social_media_history is not None
        else None
    )
    inspections = (
        _normalize_listing_inspections(payload.inspections)
        if payload.inspections is not None
        else None
    )

    row.property_address = address
    row.address_line_2 = _normalize_text(payload.address_line_2) or None
    row.suburb = _normalize_text(suburb) or None
    row.state_code = state_code
    row.postcode = _normalize_text(postcode) or None
    row.crm_property_id = _normalize_text(payload.crm_property_id) or None
    row.property_type = _normalize_text(payload.property_type) or None
    row.rental_type = _normalize_text(payload.rental_type) or None
    row.listing_status = listing_status
    if property_keys is None:
        row.key_number = _normalize_text(payload.key_number) or None
    else:
        row.keys_json = _json_dumps(property_keys)
        row.key_number = (property_keys[0]["key_number"] or None) if property_keys else None
    if social_media_history is not None:
        row.social_media_history_json = _json_dumps(social_media_history)
    if inspections is not None:
        row.listing_inspections_json = _json_dumps(inspections)
    row.owner_is_company = bool(payload.owner_is_company)
    row.tenancy_status = _normalize_text(payload.tenancy_status) or None
    row.owners_json = _json_dumps(owners_book)
    row.tenants_json = _json_dumps(occupants_book)
    row.is_active = True
    row.source = row.source or "manual"
    row.updated_at = now


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
    listing_status = _normalize_text(row.listing_status).upper()
    if listing_status not in LISTING_STATUSES:
        listing_status = "OPEN"
    property_keys = _property_keys(row)
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
        "listing_status": listing_status,
        "keys": property_keys,
        "social_media_history": _normalize_social_media_history(
            _json_list_loads(row.social_media_history_json)
        ),
        "inspections": _json_list_loads(row.listing_inspections_json),
        "owner_is_company": row.owner_is_company,
        "tenancy_status": row.tenancy_status,
        "owners": owners,
        "tenants": tenants,
        "occupants": _occupants_from_book(tenants),
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
    created = 0
    updated = 0
    reactivated = 0
    duplicates_archived = 0
    existing = db.query(ManagedProperty).filter(ManagedProperty.mailbox == mailbox).all()
    existing_by_crm: dict[str, list[ManagedProperty]] = {}
    existing_by_address: dict[str, list[ManagedProperty]] = {}
    for prop in existing:
        _index_property(prop, existing_by_crm, existing_by_address)

    for item in rows:
        candidates = _matching_existing_properties(item, existing_by_crm, existing_by_address)
        match = _best_property_match(candidates)
        if match:
            was_inactive = not bool(match.is_active)
            _apply_imported_property(match, item, now)
            for duplicate in candidates:
                if duplicate is match or not duplicate.is_active:
                    continue
                duplicate.is_active = False
                duplicate.updated_at = now
                duplicates_archived += 1
            if was_inactive:
                reactivated += 1
            else:
                updated += 1
            _index_property(match, existing_by_crm, existing_by_address)
        else:
            new_row = ManagedProperty(
                mailbox=mailbox,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            _apply_imported_property(new_row, item, now)
            db.add(new_row)
            _index_property(new_row, existing_by_crm, existing_by_address)
            created += 1
        imported += 1
    db.commit()
    return {
        "ok": True,
        "imported_rows": imported,
        "created": created,
        "updated": updated,
        "reactivated": reactivated,
        "duplicates_archived": duplicates_archived,
    }


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
    target_keys = set(_property_match_keys(address, suburb, state_code, postcode))
    existing = db.query(ManagedProperty).filter(ManagedProperty.mailbox == mailbox).all()
    for row in existing:
        row_keys = set(_property_match_keys(row.property_address, row.suburb, row.state_code, row.postcode))
        if target_keys.intersection(row_keys):
            if row.is_active:
                raise HTTPException(status_code=400, detail="This property already exists.")
            now = datetime.utcnow()
            _apply_manual_property_payload(
                row,
                payload,
                address=address,
                suburb=suburb,
                state_code=state_code,
                postcode=postcode,
                now=now,
            )
            row.source = "manual"
            db.commit()
            db.refresh(row)
            return _property_to_dict(row)

    now = datetime.utcnow()
    row = ManagedProperty(
        mailbox=mailbox,
        is_active=True,
        source="manual",
        created_at=now,
        updated_at=now,
    )
    _apply_manual_property_payload(
        row,
        payload,
        address=address,
        suburb=suburb,
        state_code=state_code,
        postcode=postcode,
        now=now,
    )
    row.source = "manual"
    db.add(row)
    db.commit()
    db.refresh(row)
    return _property_to_dict(row)


@router.put("/{property_id}")
def update_property(
    property_id: int,
    payload: PropertyUpdateIn,
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

    raw_address = _normalize_text(payload.property_address) or row.property_address
    parsed_address = _split_full_address(raw_address)
    address = parsed_address["property_address"] if parsed_address.get("state_code") else raw_address
    suburb = _normalize_text(payload.suburb) or parsed_address.get("suburb") or row.suburb
    state_code = _vic_state_code(parsed_address.get("state_code") or payload.state_code or row.state_code)
    postcode = _normalize_text(payload.postcode) or parsed_address.get("postcode") or row.postcode

    target_keys = set(_property_match_keys(address, suburb, state_code, postcode))
    if target_keys:
        existing = db.query(ManagedProperty).filter(ManagedProperty.mailbox == mailbox).filter(ManagedProperty.id != row.id).all()
        for other in existing:
            if not other.is_active:
                continue
            other_keys = set(_property_match_keys(other.property_address, other.suburb, other.state_code, other.postcode))
            if target_keys.intersection(other_keys):
                raise HTTPException(status_code=400, detail="Another active property already uses this address.")

    provided_fields = payload.model_fields_set
    if "occupants" in provided_fields:
        occupant_contacts = payload.occupants or []
    elif "tenants" in provided_fields:
        occupant_contacts = payload.tenants or []
    else:
        occupant_contacts = _contacts_from_book(_json_loads(row.tenants_json))

    if "keys" in provided_fields:
        property_keys: list[PropertyKeyIn] | list[dict[str, object]] = payload.keys or []
    else:
        property_keys = _property_keys(row)
        if "key_number" in provided_fields:
            legacy_key_number = _normalize_text(payload.key_number)
            if property_keys:
                property_keys[0]["key_number"] = legacy_key_number
            elif legacy_key_number:
                property_keys.append(
                    {"key_number": legacy_key_number, "description": "", "location": ""}
                )

    social_media_history = (
        payload.social_media_history or []
        if "social_media_history" in provided_fields
        else _json_list_loads(row.social_media_history_json)
    )
    inspections = (
        payload.inspections or []
        if "inspections" in provided_fields
        else _json_list_loads(row.listing_inspections_json)
    )
    listing_status = (
        _normalize_listing_status(payload.listing_status, default=None)
        if "listing_status" in provided_fields
        else _normalize_listing_status(row.listing_status)
    )

    merged = PropertyCreateIn(
        property_address=address,
        address_line_2=payload.address_line_2 if payload.address_line_2 is not None else row.address_line_2,
        suburb=suburb,
        state_code=state_code,
        postcode=postcode,
        crm_property_id=payload.crm_property_id if payload.crm_property_id is not None else row.crm_property_id,
        property_type=payload.property_type if payload.property_type is not None else row.property_type,
        rental_type=payload.rental_type if payload.rental_type is not None else row.rental_type,
        key_number=payload.key_number if payload.key_number is not None else row.key_number,
        listing_status=listing_status,
        keys=property_keys,
        social_media_history=social_media_history,
        inspections=inspections,
        owner_is_company=bool(row.owner_is_company if payload.owner_is_company is None else payload.owner_is_company),
        tenancy_status=payload.tenancy_status if payload.tenancy_status is not None else row.tenancy_status,
        owners=payload.owners if payload.owners is not None else _contacts_from_book(_json_loads(row.owners_json)),
        occupants=occupant_contacts,
    )
    _apply_manual_property_payload(
        row,
        merged,
        address=address,
        suburb=suburb,
        state_code=state_code,
        postcode=postcode,
        now=datetime.utcnow(),
    )
    row.source = row.source or "manual"
    db.commit()
    db.refresh(row)
    return _property_to_dict(row)


@router.delete("/flush")
def flush_properties(
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    now = datetime.utcnow()
    rows = (
        db.query(ManagedProperty)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.is_active == True)
        .all()
    )
    for row in rows:
        row.is_active = False
        row.updated_at = now
    db.commit()
    return {"ok": True, "deleted": len(rows), "archived": len(rows)}


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
                ManagedProperty.listing_status.ilike(like),
                ManagedProperty.keys_json.ilike(like),
                ManagedProperty.social_media_history_json.ilike(like),
                ManagedProperty.listing_inspections_json.ilike(like),
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
                "label": ", ".join([x for x in [r.property_address, r.suburb, r.postcode] if x]),
                **_property_to_dict(r),
            }
            for r in rows
        ]
    }


@router.get("/{property_id}")
def get_property(
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
    return _property_to_dict(row)
