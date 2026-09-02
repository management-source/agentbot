from __future__ import annotations

import base64
import html
import json
import math
import mimetypes
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import (
    ComplianceProperty,
    ComplianceRecord,
    ComplianceRecordStatus,
    LeaseRenewalRecord,
    LandlordReportInvoice,
    MaintenanceAttachment,
    MaintenanceEvent,
    MaintenanceOrder,
    MaintenanceOrderStatus,
    ManagedProperty,
    RentDueTracker,
    RentTrackStatus,
    TenantAccount,
    User,
)


MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")
AGENCY_NAME = "Dons Premier Estate Agents"
AGENCY_EMAIL = "admin@donspremier.com.au"
AGENCY_PHONE = "0422 643 451"
AGENCY_WEBSITE = "www.donspremier.com.au"
REPORT_DISCLAIMER = (
    "This report has been prepared for the property owner and contains information "
    "recorded during the selected reporting period."
)
LOGO_PATH = Path(__file__).resolve().parents[1] / "static" / "dons_premier_transparent_v2.png"


SECTION_DEFINITIONS: list[dict[str, Any]] = [
    {"id": "executive_summary", "title": "Executive Summary", "default": True, "source": "Automatic"},
    {"id": "property_tenancy", "title": "Property and Tenancy Overview", "default": True, "source": "Automatic"},
    {"id": "rent_financial", "title": "Rent and Financial Summary", "default": True, "source": "Automatic + manual"},
    {"id": "rent_arrears", "title": "Rent Arrears Activity", "default": True, "source": "Automatic + manual"},
    {"id": "maintenance_repairs", "title": "Maintenance and Repairs", "default": True, "source": "Automatic + manual"},
    {"id": "quotes_approvals", "title": "Quotes and Landlord Approvals", "default": True, "source": "Automatic + manual"},
    {"id": "routine_inspections", "title": "Routine Inspections", "default": False, "source": "Manual"},
    {"id": "compliance_safety", "title": "Compliance and Safety", "default": True, "source": "Automatic + manual"},
    {"id": "lease_rent_review", "title": "Lease and Rent Review", "default": True, "source": "Automatic + manual"},
    {"id": "tenant_communications", "title": "Tenant Communications", "default": True, "source": "Automatic + manual"},
    {"id": "owners_corporation", "title": "Owners Corporation Matters", "default": False, "source": "Manual"},
    {"id": "formal_documents", "title": "Notices and Formal Documents", "default": False, "source": "Manual"},
    {"id": "insurance_incidents", "title": "Insurance and Property Incidents", "default": False, "source": "Manual"},
    {"id": "vacating_bond", "title": "Vacating and Bond Activity", "default": False, "source": "Automatic + manual"},
    {"id": "advertising_reletting", "title": "Advertising and Re-letting Activity", "default": False, "source": "Manual"},
    {"id": "applications_inspections", "title": "Rental Applications and Inspection Activity", "default": False, "source": "Manual"},
    {"id": "market_update", "title": "Market Update", "default": False, "source": "Manual only"},
    {"id": "pm_recommendations", "title": "Property Manager Recommendations", "default": True, "source": "Manual"},
    {"id": "upcoming_actions", "title": "Upcoming Dates and Required Actions", "default": True, "source": "Automatic + manual"},
    {"id": "supporting_photos", "title": "Supporting Photos", "default": True, "source": "Automatic"},
    {"id": "additional_notes", "title": "Additional Notes", "default": True, "source": "Manual"},
]
SECTION_BY_ID = {item["id"]: item for item in SECTION_DEFINITIONS}
ALL_SECTION_IDS = [item["id"] for item in SECTION_DEFINITIONS]
DEFAULT_SECTION_IDS = [item["id"] for item in SECTION_DEFINITIONS if item["default"]]
LANDLORD_REPORT_DEFAULTS_KEY = "landlord_reports:default_sections"
SUPPORTED_REPORT_IMAGE_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}

OPEN_MAINTENANCE_STATUSES = {
    MaintenanceOrderStatus.NEW,
    MaintenanceOrderStatus.WAITING_OWNER_APPROVAL,
    MaintenanceOrderStatus.OWNER_APPROVED,
    MaintenanceOrderStatus.OWNER_ARRANGING,
    MaintenanceOrderStatus.QUOTE_REQUESTED,
    MaintenanceOrderStatus.QUOTE_RECEIVED,
    MaintenanceOrderStatus.TRADIE_ARRANGED,
    MaintenanceOrderStatus.TENANT_NOTIFIED,
}
UNSETTLED_RENT_STATUSES = {
    RentTrackStatus.DUE,
    RentTrackStatus.PARTIAL,
    RentTrackStatus.AWAITING_CLEARANCE,
}


class LandlordReportError(Exception):
    pass


def section_definitions() -> list[dict[str, Any]]:
    return [dict(item) for item in SECTION_DEFINITIONS]


def normalize_section_ids(values: Iterable[str] | None, *, allow_empty: bool = False) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        section_id = str(value or "").strip()
        if section_id not in SECTION_BY_ID:
            raise LandlordReportError(f"Unknown report section: {section_id or 'blank'}.")
        if section_id not in seen:
            selected.append(section_id)
            seen.add(section_id)
    if not selected and not allow_empty:
        raise LandlordReportError("Select at least one report section.")
    return selected


def default_section_ids(raw_value: str | None = None) -> list[str]:
    if raw_value:
        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, list):
                normalized = normalize_section_ids(parsed, allow_empty=True)
                if normalized:
                    return normalized
        except (json.JSONDecodeError, LandlordReportError):
            pass
    return list(DEFAULT_SECTION_IDS)


def _value(obj: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _clean(value: Any, max_length: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:max_length] if max_length else text


def _multiline(value: Any, max_length: int = 12000) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max_length]


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def status_label(value: Any) -> str:
    text = _enum_value(value).replace("_", " ").strip()
    return text.title() if text else "Not recorded"


def format_date_au(value: date | datetime | str | None) -> str:
    if not value:
        return "Not recorded"
    parsed: date | datetime
    if isinstance(value, (date, datetime)):
        parsed = value
    else:
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = date.fromisoformat(raw[:10])
            except ValueError:
                return _clean(raw) or "Not recorded"
    if isinstance(parsed, datetime) and parsed.tzinfo:
        parsed = parsed.astimezone(MELBOURNE_TZ)
    return parsed.strftime("%d/%m/%Y")


def format_datetime_au(value: datetime | str | None) -> str:
    if not value:
        return "Not recorded"
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return _clean(value) or "Not recorded"
    if parsed.tzinfo:
        parsed = parsed.astimezone(MELBOURNE_TZ)
    else:
        parsed = parsed.replace(tzinfo=timezone.utc).astimezone(MELBOURNE_TZ)
    return parsed.strftime("%d/%m/%Y %I:%M %p")


def format_currency_aud(value: Any) -> str:
    if value is None or value == "":
        return "Not recorded"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "Not recorded"
    if not math.isfinite(amount):
        return "Not recorded"
    return f"${amount:,.2f}"


def period_label(start_date: date, end_date: date) -> str:
    if start_date.day == 1:
        next_month = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        if end_date == next_month - timedelta(days=1):
            return start_date.strftime("%B %Y")
    return f"{format_date_au(start_date)} to {format_date_au(end_date)}"


def validate_period(start_date: date, end_date: date) -> None:
    if end_date < start_date:
        raise LandlordReportError("Reporting end date must be on or after the start date.")
    if (end_date - start_date).days > 366:
        raise LandlordReportError("Reporting periods cannot exceed 12 months.")


def safe_report_filename(address: str, start_date: date, end_date: date) -> str:
    normalized = unicodedata.normalize("NFKD", str(address or "Property"))
    ascii_address = normalized.encode("ascii", "ignore").decode("ascii")
    safe_address = re.sub(r"[^A-Za-z0-9]+", "-", ascii_address).strip("-") or "Property"
    safe_address = safe_address[:90].rstrip("-")
    if start_date.day == 1:
        next_month = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        date_part = start_date.strftime("%B-%Y") if end_date == next_month - timedelta(days=1) else ""
    else:
        date_part = ""
    if not date_part:
        date_part = f"{start_date.strftime('%d-%b-%Y')}_to_{end_date.strftime('%d-%b-%Y')}"
    return f"Monthly-Property-Report_{safe_address}_{date_part}.pdf"


def _full_property_label(prop: ManagedProperty) -> str:
    street = " ".join(part for part in [prop.property_address, prop.address_line_2] if _clean(part))
    locality = " ".join(part for part in [prop.suburb, prop.state_code, prop.postcode] if _clean(part))
    return ", ".join(part for part in [street, locality] if part) or "Property address not recorded"


def _address_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _contact_book(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    contacts = parsed.get("contacts") if isinstance(parsed, dict) else []
    if not isinstance(contacts, list):
        return []
    result: list[dict[str, Any]] = []
    for item in contacts:
        if not isinstance(item, dict):
            continue
        phones = item.get("phones") if isinstance(item.get("phones"), list) else []
        phone = item.get("mobile") or item.get("phone") or (phones[0] if phones else "")
        result.append(
            {
                "name": _clean(item.get("name")) or "Not recorded",
                "email": _clean(item.get("email")),
                "phone": _clean(phone),
                "is_company": bool(item.get("is_company")),
            }
        )
    return result


def _names(contacts: list[dict[str, Any]]) -> str:
    values = [item["name"] for item in contacts if item.get("name") and item["name"] != "Not recorded"]
    return ", ".join(values) if values else "Not recorded"


def _period_utc_bounds(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(start_date, time.min, tzinfo=MELBOURNE_TZ)
    end_local = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=MELBOURNE_TZ)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _in_utc_period(value: datetime | None, start_utc: datetime, end_utc: datetime) -> bool:
    if not value:
        return False
    comparable = value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    return start_utc <= comparable < end_utc


def _date_in_period(value: date | datetime | None, start_date: date, end_date: date) -> bool:
    if not value:
        return False
    actual = value.date() if isinstance(value, datetime) else value
    return start_date <= actual <= end_date


def _tone_for_status(value: Any) -> str:
    key = _enum_value(value).upper()
    if any(token in key for token in ("OVERDUE", "DECLINED", "CANCELLED", "URGENT", "ACTION_REQUIRED")):
        return "danger"
    if any(token in key for token in ("COMPLETED", "PAID", "APPROVED", "CURRENT", "COMPLIANT", "WAIVED")):
        return "success"
    if any(token in key for token in ("WAITING", "DUE", "PARTIAL", "QUOTE", "ARRANGED", "SCHEDULED", "PROGRESS", "SOON")):
        return "warning"
    return "neutral"


def _get_property(db: Session, mailbox: str, property_id: int) -> ManagedProperty:
    prop = (
        db.query(ManagedProperty)
        .filter(ManagedProperty.id == property_id)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.is_active == True)
        .first()
    )
    if not prop:
        raise LandlordReportError("The selected property is not available in this mailbox.")
    return prop


def _get_property_manager(db: Session, manager_id: int | None, fallback: User) -> User:
    if not manager_id:
        return fallback
    user = db.query(User).filter(User.id == manager_id).filter(User.is_active == True).first()
    if not user:
        raise LandlordReportError("The selected property manager is not an active staff member.")
    return user


def _orders_for_property(db: Session, mailbox: str, prop: ManagedProperty) -> list[MaintenanceOrder]:
    return (
        db.query(MaintenanceOrder)
        .options(
            selectinload(MaintenanceOrder.attachments),
            selectinload(MaintenanceOrder.events).selectinload(MaintenanceEvent.actor),
            selectinload(MaintenanceOrder.assignee),
        )
        .filter(MaintenanceOrder.mailbox == mailbox)
        .filter(
            or_(
                MaintenanceOrder.property_id == prop.id,
                MaintenanceOrder.property_address.ilike(prop.property_address),
            )
        )
        .order_by(MaintenanceOrder.created_at.desc())
        .all()
    )


def _maintenance_relevant(
    order: MaintenanceOrder,
    start_utc: datetime,
    end_utc: datetime,
) -> bool:
    timestamps = [
        order.created_at,
        order.updated_at,
        order.tenant_submitted_at,
        order.owner_sent_at,
        order.owner_decided_at,
        order.quote_received_at,
        order.tradie_arranged_at,
        order.tradie_scheduled_for,
        order.tenant_notified_at,
        order.completed_at,
    ]
    if any(_in_utc_period(value, start_utc, end_utc) for value in timestamps):
        return True
    created_at = order.created_at or datetime.min
    if created_at.tzinfo:
        created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
    if order.status in OPEN_MAINTENANCE_STATUSES and created_at < end_utc:
        return True
    return any(_in_utc_period(event.created_at, start_utc, end_utc) for event in order.events or [])


def _rent_rows_for_property(
    db: Session,
    mailbox: str,
    prop: ManagedProperty,
    start_date: date,
    end_date: date,
) -> list[RentDueTracker]:
    prop_keys = {
        _address_key(prop.property_address),
        _address_key(_full_property_label(prop)),
    }
    prop_keys.discard("")
    rows = db.query(RentDueTracker).filter(RentDueTracker.mailbox == mailbox).all()
    matched: list[RentDueTracker] = []
    for row in rows:
        row_key = _address_key(row.property_address)
        if not row_key or not any(row_key == key or row_key.startswith(f"{key} ") or key.startswith(f"{row_key} ") for key in prop_keys):
            continue
        relevant = _date_in_period(row.due_date, start_date, end_date) or _date_in_period(row.paid_on, start_date, end_date)
        if row.status in UNSETTLED_RENT_STATUSES and row.due_date and row.due_date.date() <= end_date:
            relevant = True
        if relevant:
            matched.append(row)
    return sorted(matched, key=lambda item: item.due_date or datetime.min)


def _latest_lease(db: Session, mailbox: str, property_id: int) -> LeaseRenewalRecord | None:
    return (
        db.query(LeaseRenewalRecord)
        .options(selectinload(LeaseRenewalRecord.assigned_user), selectinload(LeaseRenewalRecord.events))
        .filter(LeaseRenewalRecord.mailbox == mailbox)
        .filter(LeaseRenewalRecord.property_id == property_id)
        .order_by(LeaseRenewalRecord.updated_at.desc(), LeaseRenewalRecord.id.desc())
        .first()
    )


def _latest_compliance(db: Session, mailbox: str, property_id: int) -> list[ComplianceRecord]:
    rows = (
        db.query(ComplianceRecord)
        .filter(ComplianceRecord.mailbox == mailbox)
        .filter(ComplianceRecord.property_id == property_id)
        .order_by(ComplianceRecord.updated_at.desc(), ComplianceRecord.id.desc())
        .all()
    )
    latest: dict[str, ComplianceRecord] = {}
    for row in rows:
        key = _enum_value(row.compliance_type)
        if key not in latest:
            latest[key] = row
    return list(latest.values())


def _legacy_compliance(db: Session, mailbox: str, prop: ManagedProperty) -> ComplianceProperty | None:
    return (
        db.query(ComplianceProperty)
        .filter(ComplianceProperty.mailbox == mailbox)
        .filter(ComplianceProperty.property_address.ilike(prop.property_address))
        .order_by(ComplianceProperty.updated_at.desc(), ComplianceProperty.id.desc())
        .first()
    )


def _compliance_state(row: ComplianceRecord, prepared_date: date) -> tuple[str, str]:
    status = row.status
    due = row.due_date.date() if row.due_date else None
    if status == ComplianceRecordStatus.WAIVED:
        return "Waived", "success"
    if due and due < prepared_date:
        return "Overdue", "danger"
    if status == ComplianceRecordStatus.ACTION_REQUIRED:
        return "Action required", "danger"
    if status == ComplianceRecordStatus.COMPLETED:
        if due and due <= prepared_date + timedelta(days=30):
            return "Due soon", "warning"
        return "Current", "success"
    if due and due <= prepared_date + timedelta(days=30):
        return "Due soon", "warning"
    return status_label(status), "warning"


def _owner_approval(order: MaintenanceOrder) -> str:
    status = order.status
    if status == MaintenanceOrderStatus.OWNER_APPROVED:
        return "Approved"
    if status == MaintenanceOrderStatus.OWNER_DECLINED:
        return "Declined"
    if status == MaintenanceOrderStatus.OWNER_ARRANGING:
        return "Owner arranging"
    if order.owner_decided_at:
        return status_label(status)
    if order.owner_sent_at or status == MaintenanceOrderStatus.WAITING_OWNER_APPROVAL:
        return "Awaiting landlord response"
    return "Not recorded"


def _manual_entries(options: Mapping[str, Any], include_internal: bool) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[Any, ...]] = set()
    for raw in _value(options, "manual_activities", []) or []:
        section_id = _clean(_value(raw, "section_id"))
        if section_id not in SECTION_BY_ID:
            continue
        internal = bool(_value(raw, "internal", False))
        if internal and not include_internal:
            continue
        entry_date = _value(raw, "date")
        key = (
            section_id,
            str(entry_date or ""),
            _clean(_value(raw, "title")).lower(),
            _clean(_value(raw, "description")).lower(),
            _clean(_value(raw, "status")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        grouped[section_id].append(
            {
                "date": format_date_au(entry_date),
                "sort_date": str(entry_date or ""),
                "title": _clean(_value(raw, "title"), 240) or "Report activity",
                "description": _multiline(_value(raw, "description"), 5000),
                "status": status_label(_value(raw, "status") or "IN_PROGRESS"),
                "tone": _tone_for_status(_value(raw, "status") or "IN_PROGRESS"),
                "category": _clean(_value(raw, "category"), 160),
                "contractor": _clean(_value(raw, "contractor"), 200),
                "amount": _value(raw, "amount"),
                "action_required": _multiline(_value(raw, "landlord_action"), 2500),
                "internal": internal,
                "photo_ids": [int(value) for value in (_value(raw, "photo_ids", []) or []) if int(value) < 0],
                "pdf_ids": [int(value) for value in (_value(raw, "pdf_ids", []) or []) if int(value) < 0],
            }
        )
    for entries in grouped.values():
        entries.sort(key=lambda item: item["sort_date"] or "9999-12-31")
    return grouped


def _manual_block(entries: list[dict[str, Any]], include_financial: bool) -> dict[str, Any] | None:
    if not entries:
        return None
    items: list[dict[str, Any]] = []
    for entry in entries:
        meta = [entry["status"]]
        if entry.get("category"):
            meta.append(entry["category"])
        if entry.get("contractor"):
            meta.append(entry["contractor"])
        if include_financial and entry.get("amount") is not None:
            meta.append(format_currency_aud(entry["amount"]))
        items.append(
            {
                "date": entry["date"],
                "title": entry["title"],
                "description": entry["description"],
                "status": " - ".join(meta),
                "tone": entry["tone"],
                "action_required": entry["action_required"],
                "internal": entry["internal"],
            }
        )
    return {"type": "timeline", "title": "Report activities", "items": items}


def _section(section_id: str, blocks: list[dict[str, Any]], has_activity: bool, empty_message: str) -> dict[str, Any]:
    definition = SECTION_BY_ID[section_id]
    return {
        "id": section_id,
        "title": definition["title"],
        "blocks": [block for block in blocks if block],
        "has_activity": bool(has_activity),
        "empty_message": empty_message,
    }


def _photo_metadata(orders: list[MaintenanceOrder], start_utc: datetime, end_utc: datetime) -> list[dict[str, Any]]:
    photos: list[dict[str, Any]] = []
    seen: set[int] = set()
    for order in orders:
        for attachment in sorted(order.attachments or [], key=lambda item: item.created_at or datetime.min):
            content_type = str(attachment.content_type or "").lower()
            if not content_type.startswith("image/"):
                guessed = mimetypes.guess_type(attachment.filename or "")[0] or ""
                content_type = guessed.lower()
            if content_type not in SUPPORTED_REPORT_IMAGE_TYPES or attachment.id in seen:
                continue
            if not _in_utc_period(attachment.created_at, start_utc, end_utc) and order.status not in OPEN_MAINTENANCE_STATUSES:
                continue
            seen.add(attachment.id)
            photos.append(
                {
                    "attachment_id": attachment.id,
                    "filename": _clean(attachment.filename, 180) or "Property photo",
                    "content_type": content_type,
                    "caption": _clean(attachment.notes, 500) or _clean(order.title, 240) or "Property photo",
                    "date": format_date_au(attachment.created_at),
                    "order_title": _clean(order.title, 240),
                }
            )
    return photos


def build_report_context(
    db: Session,
    *,
    mailbox: str,
    property_id: int,
    start_date: date,
    end_date: date,
    current_user: User,
    defaults: list[str],
) -> dict[str, Any]:
    validate_period(start_date, end_date)
    prop = _get_property(db, mailbox, property_id)
    owners = _contact_book(prop.owners_json)
    tenants = _contact_book(prop.tenants_json)
    tenant_accounts = (
        db.query(TenantAccount)
        .filter(TenantAccount.mailbox == mailbox)
        .filter(TenantAccount.property_id == prop.id)
        .filter(TenantAccount.is_active == True)
        .all()
    )
    known_tenant_names = {item["name"].lower() for item in tenants if item.get("name")}
    for account in tenant_accounts:
        if account.name and account.name.lower() not in known_tenant_names:
            tenants.append({"name": account.name, "email": account.email, "phone": account.phone or "", "is_company": False})
    start_utc, end_utc = _period_utc_bounds(start_date, end_date)
    orders = _orders_for_property(db, mailbox, prop)
    relevant_orders = [item for item in orders if _maintenance_relevant(item, start_utc, end_utc)]
    photos = _photo_metadata(relevant_orders, start_utc, end_utc)
    rent_rows = _rent_rows_for_property(db, mailbox, prop, start_date, end_date)
    compliance = _latest_compliance(db, mailbox, prop.id)
    lease = _latest_lease(db, mailbox, prop.id)
    staff = db.query(User).filter(User.is_active == True).order_by(User.name.asc()).all()
    return {
        "property": {
            "id": prop.id,
            "label": _full_property_label(prop),
            "property_address": prop.property_address,
            "suburb": prop.suburb,
            "state_code": prop.state_code,
            "postcode": prop.postcode,
            "property_type": prop.property_type,
            "rental_type": prop.rental_type,
            "tenancy_status": prop.tenancy_status,
        },
        "landlords": owners,
        "tenants": tenants,
        "suggested_landlord_name": _names(owners),
        "suggested_property_manager_id": lease.assigned_user_id if lease and lease.assigned_user_id else current_user.id,
        "staff": [{"id": user.id, "name": user.name, "email": user.email, "role": _enum_value(user.role)} for user in staff],
        "period": {"start_date": start_date, "end_date": end_date, "label": period_label(start_date, end_date)},
        "sections": section_definitions(),
        "default_sections": normalize_section_ids(defaults),
        "available_photos": photos,
        "source_summary": {
            "maintenance_orders": len(relevant_orders),
            "rent_records": len(rent_rows),
            "compliance_records": len(compliance),
            "lease_record_available": bool(lease),
            "supporting_photos": len(photos),
        },
        "data_limitations": [
            "The current portal does not contain a disbursement, invoice, bond, inspection, insurance, advertising, application, or market-data table.",
            "Unsupported figures are shown as Not recorded and may be added as report-only manual activities.",
            "Internal notes remain excluded unless Include internal notes is deliberately enabled.",
        ],
    }


def assemble_report(
    db: Session,
    *,
    mailbox: str,
    current_user: User,
    options: Mapping[str, Any] | Any,
) -> dict[str, Any]:
    selected = normalize_section_ids(_value(options, "selected_sections"))
    start_date = _value(options, "start_date")
    end_date = _value(options, "end_date")
    prepared_date = _value(options, "prepared_date") or date.today()
    if not isinstance(start_date, date) or not isinstance(end_date, date) or not isinstance(prepared_date, date):
        raise LandlordReportError("A valid property, reporting period and prepared date are required.")
    validate_period(start_date, end_date)
    # The operational snapshot closes at the selected period end. The prepared
    # date remains a document-generation label and never extends report data.
    report_as_of = end_date
    prop = _get_property(db, mailbox, int(_value(options, "property_id") or 0))
    manager = _get_property_manager(db, _value(options, "property_manager_id"), current_user)
    owners = _contact_book(prop.owners_json)
    tenants = _contact_book(prop.tenants_json)
    landlord_name = _clean(_value(options, "landlord_name"), 500) or _names(owners)
    include_empty = bool(_value(options, "include_no_activity", False))
    include_photos = bool(_value(options, "include_photos", True))
    include_financial = bool(_value(options, "include_financial", True))
    include_internal = bool(_value(options, "include_internal_notes", False))
    manual = _manual_entries(options, include_internal)
    detail_overrides = {
        _clean(key, 120).lower(): _multiline(value, 2000)
        for key, value in (_value(options, "detail_overrides", {}) or {}).items()
        if _clean(key) and _multiline(value, 2000)
    }
    uploaded_photo_by_id: dict[int, dict[str, Any]] = {}
    for raw in _value(options, "report_only_photos", []) or []:
        photo_id = int(_value(raw, "id") or 0)
        if photo_id < 0:
            uploaded_photo_by_id[photo_id] = {
                "attachment_id": photo_id,
                "filename": _clean(_value(raw, "filename"), 240) or "Report photo",
                "caption": _clean(_value(raw, "caption"), 500) or "Report photo",
                "date": "Report only",
            }
    start_utc, end_utc = _period_utc_bounds(start_date, end_date)

    tenant_accounts = (
        db.query(TenantAccount)
        .filter(TenantAccount.mailbox == mailbox)
        .filter(TenantAccount.property_id == prop.id)
        .filter(TenantAccount.is_active == True)
        .all()
    )
    known_tenant_names = {item["name"].lower() for item in tenants if item.get("name")}
    for account in tenant_accounts:
        if account.name and account.name.lower() not in known_tenant_names:
            tenants.append({"name": account.name, "email": account.email, "phone": account.phone or "", "is_company": False})

    all_orders = _orders_for_property(db, mailbox, prop)
    orders = [item for item in all_orders if _maintenance_relevant(item, start_utc, end_utc)]
    open_orders = [item for item in all_orders if item.status in OPEN_MAINTENANCE_STATUSES]
    rent_rows = _rent_rows_for_property(db, mailbox, prop, start_date, end_date)
    lease = _latest_lease(db, mailbox, prop.id)
    compliance_rows = _latest_compliance(db, mailbox, prop.id)
    legacy_compliance = _legacy_compliance(db, mailbox, prop)

    latest_rent = max(rent_rows, key=lambda item: item.due_date or datetime.min, default=None)
    paid_rows = [item for item in rent_rows if item.status == RentTrackStatus.PAID and item.due_date]
    paid_to = max((item.due_date for item in paid_rows), default=None)
    overdue_rent = [
        item
        for item in rent_rows
        if item.status in UNSETTLED_RENT_STATUSES and item.due_date and item.due_date.date() < report_as_of
    ]

    compliance_cards: list[dict[str, Any]] = []
    compliance_actions: list[str] = []
    compliance_due_dates: list[tuple[date, str]] = []
    for row in compliance_rows:
        state, tone = _compliance_state(row, report_as_of)
        label = status_label(row.compliance_type)
        compliance_cards.append(
            {
                "title": label,
                "status": state,
                "tone": tone,
                "fields": [
                    {"label": "Last completed", "value": format_date_au(row.completed_at)},
                    {"label": "Next due", "value": format_date_au(row.due_date)},
                    {"label": "Provider", "value": _clean(row.provider_name) or "Not recorded"},
                    {"label": "Result", "value": _clean(row.result_text) or "Not recorded"},
                ],
                "description": _multiline(row.notes) if include_internal else "",
            }
        )
        if tone == "danger":
            compliance_actions.append(f"{label}: {state.lower()}.")
        if row.due_date and row.due_date.date() >= report_as_of:
            compliance_due_dates.append((row.due_date.date(), f"{label} compliance due"))

    if not compliance_cards and legacy_compliance:
        legacy_specs = [
            ("Smoke alarms", legacy_compliance.smoke_last_checked_at, legacy_compliance.smoke_next_due_at),
            ("Gas safety", legacy_compliance.gas_last_checked_at, legacy_compliance.gas_next_due_at),
            ("Electrical safety", legacy_compliance.electrical_last_checked_at, legacy_compliance.electrical_next_due_at),
            ("Minimum rental standards", None, None),
        ]
        for label, completed, due in legacy_specs:
            state = "Not recorded"
            tone = "neutral"
            if due:
                state = "Overdue" if due.date() < report_as_of else "Current"
                tone = "danger" if state == "Overdue" else "success"
                if due.date() >= report_as_of:
                    compliance_due_dates.append((due.date(), f"{label} due"))
            compliance_cards.append(
                {
                    "title": label,
                    "status": state,
                    "tone": tone,
                    "fields": [
                        {"label": "Last completed", "value": format_date_au(completed)},
                        {"label": "Next due", "value": format_date_au(due)},
                    ],
                    "description": _multiline(legacy_compliance.compliance_notes) if include_internal else "",
                }
            )

    lease_status = status_label(lease.status) if lease else "Not recorded"
    lease_tone = _tone_for_status(lease.status) if lease else "neutral"
    rent_status = status_label(latest_rent.status) if latest_rent else "Not recorded"
    rent_tone = _tone_for_status(latest_rent.status) if latest_rent else "neutral"
    maintenance_status = f"{len(open_orders)} open request{'s' if len(open_orders) != 1 else ''}" if open_orders else "No open requests"
    maintenance_tone = "warning" if open_orders else "success"
    if any(str(item.priority or "").lower() == "urgent" for item in open_orders):
        maintenance_tone = "danger"
    compliance_status = "Not recorded"
    compliance_tone = "neutral"
    if compliance_cards:
        if any(card["tone"] == "danger" for card in compliance_cards):
            compliance_status, compliance_tone = "Action required", "danger"
        elif any(card["tone"] == "warning" for card in compliance_cards):
            compliance_status, compliance_tone = "Due or in progress", "warning"
        else:
            compliance_status, compliance_tone = "Current", "success"

    critical_actions: list[str] = []
    if overdue_rent:
        critical_actions.append(f"Rent tracking shows {len(overdue_rent)} overdue or unsettled period{'s' if len(overdue_rent) != 1 else ''}.")
    critical_actions.extend(compliance_actions)
    for order in open_orders:
        if order.status in {MaintenanceOrderStatus.WAITING_OWNER_APPROVAL, MaintenanceOrderStatus.QUOTE_RECEIVED}:
            critical_actions.append(f"Landlord decision required for maintenance: {order.title}.")
    if lease and lease.current_lease_end and lease.current_lease_end.date() <= report_as_of + timedelta(days=60):
        critical_actions.append(f"Lease expiry is approaching on {format_date_au(lease.current_lease_end)}.")

    sections: dict[str, dict[str, Any]] = {}

    executive_blocks: list[dict[str, Any]] = [
        {
            "type": "status_cards",
            "items": [
                {"label": "Tenancy", "value": _clean(prop.tenancy_status) or "Not recorded", "tone": "neutral"},
                {"label": "Rent", "value": rent_status, "tone": rent_tone},
                {"label": "Maintenance", "value": maintenance_status, "tone": maintenance_tone},
                {"label": "Compliance", "value": compliance_status, "tone": compliance_tone},
                {"label": "Lease", "value": lease_status, "tone": lease_tone},
            ],
        }
    ]
    intro_message = _multiline(_value(options, "intro_message"), 6000)
    if intro_message:
        executive_blocks.append({"type": "note", "title": "Message to the landlord", "text": intro_message, "tone": "gold"})
    overall_summary = _multiline(_value(options, "overall_summary"), 6000)
    if overall_summary:
        executive_blocks.append({"type": "note", "title": "Property manager summary", "text": overall_summary, "tone": "gold"})
    if critical_actions:
        executive_blocks.append({"type": "actions", "title": "Landlord action required", "items": critical_actions})
    manual_block = _manual_block(manual["executive_summary"], include_financial)
    if manual_block:
        executive_blocks.append(manual_block)
    sections["executive_summary"] = _section(
        "executive_summary", executive_blocks, True, "No executive summary information was available."
    )

    overview_items = [
        {"label": "Property address", "value": _full_property_label(prop)},
        {"label": "Landlord", "value": landlord_name},
        {"label": "Tenant names", "value": _names(tenants)},
        {"label": "Lease type", "value": _clean(prop.rental_type) or _clean(lease.proposed_term if lease else None) or "Not recorded"},
        {"label": "Lease commencement", "value": format_date_au(lease.current_lease_start if lease else None)},
        {"label": "Lease expiry", "value": format_date_au(lease.current_lease_end if lease else None)},
        {"label": "Current weekly rent", "value": format_currency_aud(lease.current_rent) if include_financial and lease else ("Excluded" if not include_financial else "Not recorded")},
        {"label": "Bond amount", "value": "Not recorded" if include_financial else "Excluded"},
        {"label": "Rent paid-to date", "value": format_date_au(paid_to)},
        {"label": "Occupancy status", "value": _clean(prop.tenancy_status) or "Not recorded"},
        {"label": "Property type", "value": _clean(prop.property_type) or "Not recorded"},
        {"label": "Property manager", "value": manager.name},
    ]
    overview_blocks = [{"type": "key_values", "items": overview_items}]
    manual_block = _manual_block(manual["property_tenancy"], include_financial)
    if manual_block:
        overview_blocks.append(manual_block)
    sections["property_tenancy"] = _section(
        "property_tenancy", overview_blocks, True, "No property or tenancy details were available."
    )

    partial_total = sum(float(item.partial_amount or 0) for item in rent_rows if item.partial_amount is not None)
    persisted_invoices = (
        db.query(LandlordReportInvoice)
        .filter(
            LandlordReportInvoice.mailbox == mailbox,
            LandlordReportInvoice.property_id == prop.id,
            LandlordReportInvoice.invoice_date >= start_date,
            LandlordReportInvoice.invoice_date <= end_date,
        )
        .order_by(LandlordReportInvoice.invoice_date, LandlordReportInvoice.id)
        .all()
    )
    supplied_invoices = list(_value(options, "invoice_rows", []) or [])
    source_priority = {"bond": 0, "mortgage": 1, "incoming": 2, "outgoing": 3}
    persisted_invoices = sorted(
        persisted_invoices,
        key=lambda row: (source_priority.get(row.report_type, 99), row.invoice_date or date.max, row.id),
    )
    supplied_invoices.extend({
        "property_id": row.property_id,
        "invoice_date": row.invoice_date,
        "invoice_number": row.invoice_number,
        "supplier": row.supplier,
        "category": row.category,
        "description": row.description,
        "amount": row.amount,
        "gst": row.gst,
        "status": row.status,
        "source_type": row.report_type,
    } for row in persisted_invoices)
    invoice_rows: list[dict[str, Any]] = []
    seen_invoice_keys: set[tuple[Any, ...]] = set()
    for raw in supplied_invoices:
        invoice_date = _value(raw, "invoice_date")
        if int(_value(raw, "property_id") or 0) != prop.id or not isinstance(invoice_date, date) or not start_date <= invoice_date <= end_date:
            continue
        amount = _value(raw, "amount")
        invoice_number = _clean(_value(raw, "invoice_number"), 160)
        description = _clean(_value(raw, "description"), 1000)
        dedupe_key = (
            "number",
            invoice_number.lower(),
        ) if invoice_number else (
            "detail",
            str(invoice_date),
            round(float(amount or 0), 2),
            description.lower(),
        )
        if dedupe_key in seen_invoice_keys:
            continue
        seen_invoice_keys.add(dedupe_key)
        invoice_rows.append({
            "invoice_date": format_date_au(invoice_date),
            "invoice_number": invoice_number or "—",
            "supplier": _clean(_value(raw, "supplier"), 300) or "—",
            "category": _clean(_value(raw, "category"), 160) or "—",
            "description": description or "—",
            "amount": format_currency_aud(amount),
            "amount_raw": float(amount) if amount is not None else 0.0,
            "gst": format_currency_aud(_value(raw, "gst")),
            "status": _clean(_value(raw, "status"), 160) or "—",
            "source_type": status_label(_value(raw, "source_type") or "invoice"),
        })
    invoice_total = sum(item["amount_raw"] for item in invoice_rows)
    invoice_totals_by_type: dict[str, float] = defaultdict(float)
    for item in invoice_rows:
        invoice_totals_by_type[item["source_type"]] += item["amount_raw"]
    outstanding_total = sum(
        item["amount_raw"] for item in invoice_rows
        if not (
            ("paid" in item["status"].lower() and "unpaid" not in item["status"].lower() and "not paid" not in item["status"].lower())
            or "processed" in item["status"].lower()
            or "complete" in item["status"].lower()
        )
    )
    financial_items = [
        {"label": "Rent received during period", "value": "Not recorded in current system"},
        {"label": "Recorded partial payments", "value": format_currency_aud(partial_total) if partial_total else "Not recorded"},
        {"label": "Owner disbursements", "value": "Not recorded in current system"},
        {"label": "Current rent balance", "value": "Not recorded in current system"},
        {"label": "Invoices during period", "value": f"{len(invoice_rows)} invoice{'s' if len(invoice_rows) != 1 else ''} (deduplicated)" if invoice_rows else "Not recorded in current system"},
        {"label": "Total invoice amount", "value": format_currency_aud(invoice_total) if invoice_rows else "Not recorded in current system"},
        *[
            {"label": f"{source_type} total", "value": format_currency_aud(total)}
            for source_type, total in sorted(invoice_totals_by_type.items())
        ],
        {"label": "Outstanding invoices", "value": format_currency_aud(outstanding_total) if invoice_rows else "Not recorded in current system"},
    ]
    financial_blocks: list[dict[str, Any]] = []
    if include_financial:
        financial_blocks.append({"type": "key_values", "items": financial_items})
    else:
        financial_blocks.append({"type": "note", "title": "Financial figures excluded", "text": "Financial figures were intentionally excluded from this report.", "tone": "neutral"})
    if rent_rows:
        financial_blocks.append(
            {
                "type": "table",
                "title": "Rent tracker activity",
                "columns": [
                    {"key": "due_date", "label": "Due date"},
                    {"key": "frequency", "label": "Frequency"},
                    {"key": "status", "label": "Status"},
                    {"key": "paid_on", "label": "Paid / updated"},
                    *([{"key": "partial", "label": "Partial amount"}] if include_financial else []),
                ],
                "rows": [
                    {
                        "due_date": format_date_au(item.due_date),
                        "frequency": status_label(item.frequency),
                        "status": status_label(item.status),
                        "paid_on": format_date_au(item.paid_on),
                        "partial": format_currency_aud(item.partial_amount),
                    }
                    for item in rent_rows
                ],
            }
        )
    if include_financial and invoice_rows:
        financial_blocks.append({
            "type": "table",
            "title": "CRM invoices for this reporting period",
            "columns": [
                {"key": "invoice_date", "label": "Invoice date"},
                {"key": "source_type", "label": "Type"},
                {"key": "invoice_number", "label": "Invoice no."},
                {"key": "supplier", "label": "Supplier"},
                {"key": "category", "label": "Category"},
                {"key": "description", "label": "Description"},
                {"key": "amount", "label": "Amount"},
                {"key": "gst", "label": "GST"},
                {"key": "status", "label": "Status"},
            ],
            "rows": invoice_rows,
        })
    manual_block = _manual_block(manual["rent_financial"], include_financial)
    if manual_block:
        financial_blocks.append(manual_block)
    sections["rent_financial"] = _section(
        "rent_financial",
        financial_blocks,
        bool(rent_rows or invoice_rows or manual["rent_financial"]),
        "No financial or rent activity was recorded for this period.",
    )

    arrears_rows: list[dict[str, Any]] = []
    for item in overdue_rent:
        due = item.due_date.date() if item.due_date else report_as_of
        arrears_rows.append(
            {
                "due_date": format_date_au(due),
                "days": str(max((report_as_of - due).days, 0)),
                "outstanding": "Not recorded" if include_financial else "Excluded",
                "follow_up": _multiline(item.notes, 800) if include_internal and item.notes else "Not recorded",
                "status": status_label(item.status),
            }
        )
    arrears_blocks: list[dict[str, Any]] = []
    if arrears_rows:
        arrears_blocks.append(
            {
                "type": "table",
                "columns": [
                    {"key": "due_date", "label": "Due date"},
                    {"key": "days", "label": "Arrears days"},
                    {"key": "outstanding", "label": "Amount outstanding"},
                    {"key": "follow_up", "label": "Follow-up / communication"},
                    {"key": "status", "label": "Current status"},
                ],
                "rows": arrears_rows,
            }
        )
    manual_block = _manual_block(manual["rent_arrears"], include_financial)
    if manual_block:
        arrears_blocks.append(manual_block)
    sections["rent_arrears"] = _section(
        "rent_arrears", arrears_blocks, bool(arrears_rows or manual["rent_arrears"]), "No rent arrears activity was recorded for this period."
    )

    maintenance_cards: list[dict[str, Any]] = []
    maintenance_actions: list[str] = []
    for order in orders:
        approval = _owner_approval(order)
        fields = [
            {"label": "Date reported", "value": format_date_au(order.tenant_submitted_at or order.created_at)},
            {"label": "Priority", "value": status_label(order.priority)},
            {"label": "Action taken", "value": status_label(order.status)},
            {"label": "Contractor", "value": _clean(order.tradie_company or order.tradie_name) or "Not recorded"},
            {"label": "Work-order status", "value": status_label(order.status)},
            {"label": "Cost / quote", "value": format_currency_aud(order.quoted_amount) if include_financial else "Excluded"},
            {"label": "Completion date", "value": format_date_au(order.completed_at)},
            {"label": "Landlord approval", "value": approval},
        ]
        maintenance_cards.append(
            {
                "title": _clean(order.title, 300) or "Maintenance request",
                "status": status_label(order.status),
                "tone": _tone_for_status(order.status),
                "fields": fields,
                "description": _multiline(order.description, 4000),
            }
        )
        if approval == "Awaiting landlord response":
            maintenance_actions.append(f"Approval or direction is required for {order.title}.")
    maintenance_blocks: list[dict[str, Any]] = []
    if maintenance_cards:
        maintenance_blocks.append({"type": "record_cards", "items": maintenance_cards})
    if maintenance_actions:
        maintenance_blocks.append({"type": "actions", "title": "Landlord action required", "items": maintenance_actions})
    manual_block = _manual_block(manual["maintenance_repairs"], include_financial)
    if manual_block:
        maintenance_blocks.append(manual_block)
    sections["maintenance_repairs"] = _section(
        "maintenance_repairs", maintenance_blocks, bool(maintenance_cards or manual["maintenance_repairs"]), "No maintenance or repair activity was recorded for this period."
    )

    quote_rows: list[dict[str, Any]] = []
    quote_actions: list[str] = []
    for order in orders:
        if not (
            order.quoted_amount is not None
            or order.quote_received_at
            or order.status in {
                MaintenanceOrderStatus.WAITING_OWNER_APPROVAL,
                MaintenanceOrderStatus.QUOTE_REQUESTED,
                MaintenanceOrderStatus.QUOTE_RECEIVED,
            }
        ):
            continue
        approval = _owner_approval(order)
        action = "Approval or instructions required" if approval == "Awaiting landlord response" else "No action currently required"
        quote_rows.append(
            {
                "description": _clean(order.title, 300),
                "contractor": _clean(order.tradie_company or order.tradie_name) or "Not recorded",
                "amount": format_currency_aud(order.quoted_amount) if include_financial else "Excluded",
                "received": format_date_au(order.quote_received_at),
                "approval": approval,
                "decision_date": format_date_au(order.owner_decided_at),
                "action": action,
            }
        )
        if approval == "Awaiting landlord response":
            quote_actions.append(f"Review the quote or maintenance proposal for {order.title}.")
    quote_blocks: list[dict[str, Any]] = []
    if quote_rows:
        quote_blocks.append(
            {
                "type": "table",
                "columns": [
                    {"key": "description", "label": "Quote"},
                    {"key": "contractor", "label": "Contractor"},
                    {"key": "amount", "label": "Amount"},
                    {"key": "received", "label": "Received"},
                    {"key": "approval", "label": "Approval"},
                    {"key": "decision_date", "label": "Decision date"},
                    {"key": "action", "label": "Landlord action"},
                ],
                "rows": quote_rows,
            }
        )
    if quote_actions:
        quote_blocks.append({"type": "actions", "title": "Landlord action required", "items": quote_actions})
    manual_block = _manual_block(manual["quotes_approvals"], include_financial)
    if manual_block:
        quote_blocks.append(manual_block)
    sections["quotes_approvals"] = _section(
        "quotes_approvals", quote_blocks, bool(quote_rows or manual["quotes_approvals"]), "No quotes or landlord approval activity was recorded for this period."
    )

    for section_id, empty_message in [
        ("routine_inspections", "No routine inspection activity was recorded for this period."),
        ("owners_corporation", "No owners corporation matters were recorded for this period."),
        ("formal_documents", "No notices or formal documents were recorded for this period."),
        ("insurance_incidents", "No insurance or property incidents were recorded for this period."),
        ("advertising_reletting", "No advertising or re-letting activity was recorded for this period."),
        ("applications_inspections", "No rental application or open-for-inspection activity was recorded for this period."),
        ("market_update", "No verified market update was recorded for this period."),
        ("pm_recommendations", "No property manager recommendations were added for this period."),
    ]:
        block = _manual_block(manual[section_id], include_financial)
        sections[section_id] = _section(section_id, [block] if block else [], bool(block), empty_message)

    compliance_blocks: list[dict[str, Any]] = []
    if compliance_cards:
        compliance_blocks.append({"type": "record_cards", "items": compliance_cards})
    if compliance_actions:
        compliance_blocks.append({"type": "actions", "title": "Outstanding compliance actions", "items": compliance_actions})
    manual_block = _manual_block(manual["compliance_safety"], include_financial)
    if manual_block:
        compliance_blocks.append(manual_block)
    sections["compliance_safety"] = _section(
        "compliance_safety", compliance_blocks, bool(compliance_cards or manual["compliance_safety"]), "No compliance or safety records were available for this property."
    )

    lease_blocks: list[dict[str, Any]] = []
    if lease:
        lease_blocks.append(
            {
                "type": "key_values",
                "items": [
                    {"label": "Current lease expiry", "value": format_date_au(lease.current_lease_end)},
                    {"label": "Renewal status", "value": status_label(lease.status)},
                    {"label": "Current weekly rent", "value": format_currency_aud(lease.current_rent) if include_financial else "Excluded"},
                    {"label": "Market rent estimate", "value": "Not recorded" if include_financial else "Excluded"},
                    {"label": "Recommended / proposed rent", "value": format_currency_aud(lease.proposed_rent) if include_financial else "Excluded"},
                    {"label": "Rent increase status", "value": "Scheduled" if lease.rent_increase_date else "Not recorded"},
                    {"label": "Notice issue date", "value": format_date_au(lease.lease_sent_date)},
                    {"label": "New rent commencement", "value": format_date_au(lease.rent_increase_date)},
                    {"label": "Proposed lease term", "value": _clean(lease.proposed_term) or "Not recorded"},
                    {"label": "Follow-up date", "value": format_date_au(lease.follow_up_date)},
                ],
            }
        )
        if include_internal and lease.notes:
            lease_blocks.append({"type": "note", "title": "Internal lease note", "text": _multiline(lease.notes), "tone": "internal"})
    manual_block = _manual_block(manual["lease_rent_review"], include_financial)
    if manual_block:
        lease_blocks.append(manual_block)
    sections["lease_rent_review"] = _section(
        "lease_rent_review", lease_blocks, bool(lease or manual["lease_rent_review"]), "No lease renewal or rent review information was recorded."
    )

    communication_items: list[dict[str, Any]] = []
    communication_seen: set[tuple[str, str]] = set()
    for order in orders:
        candidates = [
            (order.tenant_submitted_at, "Maintenance request received", f"Tenant reported: {order.title}.", "Received"),
            (order.tenant_notified_at, "Maintenance arrangement communicated", f"Tenant was notified regarding {order.title}.", "Completed"),
        ]
        for event in order.events or []:
            if not _in_utc_period(event.created_at, start_utc, end_utc):
                continue
            event_type = str(event.event_type or "").lower()
            if event_type == "tenant_update":
                candidates.append((event.created_at, "Tenant update received", f"Tenant supplied an update regarding {order.title}.", "Received"))
            elif event_type == "tenant_media_uploaded":
                candidates.append((event.created_at, "Supporting media received", f"Tenant supplied photos or video regarding {order.title}.", "Received"))
            elif event_type == "tenant_info_requested":
                candidates.append((event.created_at, "Further information requested", f"The tenant was asked for more information regarding {order.title}.", "In progress"))
        for when, title, description, outcome in candidates:
            if not when or not _in_utc_period(when, start_utc, end_utc):
                continue
            key = (format_date_au(when), title + description)
            if key in communication_seen:
                continue
            communication_seen.add(key)
            communication_items.append(
                {
                    "date": format_date_au(when),
                    "title": title,
                    "description": description,
                    "status": outcome,
                    "tone": "success" if outcome == "Completed" else "warning",
                    "action_required": "",
                }
            )
    communication_blocks: list[dict[str, Any]] = []
    if communication_items:
        communication_blocks.append({"type": "timeline", "items": communication_items})
    manual_block = _manual_block(manual["tenant_communications"], include_financial)
    if manual_block:
        communication_blocks.append(manual_block)
    sections["tenant_communications"] = _section(
        "tenant_communications", communication_blocks, bool(communication_items or manual["tenant_communications"]), "No landlord-appropriate tenant communications were recorded for this period."
    )

    vacating_detected = any(token in str(prop.tenancy_status or "").lower() for token in ("vacat", "notice", "ending"))
    vacating_blocks: list[dict[str, Any]] = []
    if vacating_detected:
        vacating_blocks.append(
            {
                "type": "key_values",
                "items": [
                    {"label": "Current tenancy status", "value": _clean(prop.tenancy_status) or "Not recorded"},
                    {"label": "Vacate date", "value": "Not recorded"},
                    {"label": "Final inspection", "value": "Not recorded"},
                    {"label": "Bond status", "value": "Not recorded" if include_financial else "Excluded"},
                    {"label": "Outstanding actions", "value": "Add a report activity if action is required"},
                ],
            }
        )
    manual_block = _manual_block(manual["vacating_bond"], include_financial)
    if manual_block:
        vacating_blocks.append(manual_block)
    sections["vacating_bond"] = _section(
        "vacating_bond", vacating_blocks, bool(vacating_detected or manual["vacating_bond"]), "No vacating or bond activity was recorded for this period."
    )

    upcoming: list[dict[str, Any]] = []
    if lease:
        for when, title in [
            (lease.current_lease_end, "Current lease expiry"),
            (lease.renewal_due_date, "Lease renewal due"),
            (lease.follow_up_date, "Lease renewal follow-up"),
            (lease.rent_increase_date, "New rent commencement"),
        ]:
            if when and when.date() >= report_as_of:
                upcoming.append({"date": when.date(), "title": title, "status": "Upcoming", "tone": "warning"})
    for due, title in compliance_due_dates:
        upcoming.append({"date": due, "title": title, "status": "Upcoming", "tone": "warning"})
    for order in open_orders:
        for when, title in [
            (order.due_by, f"Maintenance follow-up: {order.title}"),
            (order.tradie_scheduled_for, f"Maintenance appointment: {order.title}"),
        ]:
            if when and when.date() >= report_as_of:
                upcoming.append({"date": when.date(), "title": title, "status": status_label(order.status), "tone": _tone_for_status(order.status)})
        if order.status in {MaintenanceOrderStatus.WAITING_OWNER_APPROVAL, MaintenanceOrderStatus.QUOTE_RECEIVED}:
            upcoming.append({"date": report_as_of, "title": f"Landlord approval required: {order.title}", "status": "Action required", "tone": "danger"})
    unique_upcoming: list[dict[str, Any]] = []
    upcoming_seen: set[tuple[date, str]] = set()
    for item in sorted(upcoming, key=lambda value: (value["date"], value["title"])):
        key = (item["date"], item["title"])
        if key in upcoming_seen:
            continue
        upcoming_seen.add(key)
        unique_upcoming.append(
            {
                "date": format_date_au(item["date"]),
                "title": item["title"],
                "description": "",
                "status": item["status"],
                "tone": item["tone"],
                "action_required": item["title"] if item["tone"] == "danger" else "",
            }
        )
    upcoming_blocks: list[dict[str, Any]] = []
    if unique_upcoming:
        upcoming_blocks.append({"type": "timeline", "items": unique_upcoming})
    manual_block = _manual_block(manual["upcoming_actions"], include_financial)
    if manual_block:
        upcoming_blocks.append(manual_block)
    sections["upcoming_actions"] = _section(
        "upcoming_actions", upcoming_blocks, bool(unique_upcoming or manual["upcoming_actions"]), "No upcoming dates or required actions were recorded."
    )

    available_photos = _photo_metadata(orders, start_utc, end_utc)
    available_photo_ids = {item["attachment_id"] for item in available_photos}
    requested_photo_ids = []
    for raw_id in _value(options, "photo_attachment_ids", []) or []:
        try:
            photo_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if photo_id in available_photo_ids and photo_id not in requested_photo_ids:
            requested_photo_ids.append(photo_id)
    if not requested_photo_ids:
        requested_photo_ids = [item["attachment_id"] for item in available_photos]
    selected_photos = [item for item in available_photos if item["attachment_id"] in requested_photo_ids]
    photo_blocks = [{"type": "photos", "items": selected_photos}] if include_photos and selected_photos else []
    sections["supporting_photos"] = _section(
        "supporting_photos", photo_blocks, bool(photo_blocks), "No supporting photos were recorded for this period."
    )

    additional_blocks: list[dict[str, Any]] = []
    additional_notes = _multiline(_value(options, "additional_notes"), 10000)
    if additional_notes:
        additional_blocks.append({"type": "note", "title": "Additional landlord notes", "text": additional_notes, "tone": "gold"})
    manual_block = _manual_block(manual["additional_notes"], include_financial)
    if manual_block:
        additional_blocks.append(manual_block)
    if include_internal:
        internal_items: list[dict[str, Any]] = []
        for order in orders:
            if order.access_notes:
                internal_items.append({"date": format_date_au(order.updated_at), "title": f"Access note - {order.title}", "description": _multiline(order.access_notes), "status": "Internal", "tone": "neutral", "action_required": "", "internal": True})
            for event in order.events or []:
                if str(event.event_type or "").lower() in {"note", "internal_note"} and _in_utc_period(event.created_at, start_utc, end_utc):
                    internal_items.append({"date": format_date_au(event.created_at), "title": f"Maintenance note - {order.title}", "description": _multiline(event.detail), "status": "Internal", "tone": "neutral", "action_required": "", "internal": True})
        if internal_items:
            additional_blocks.append({"type": "timeline", "title": "Internal notes - intentionally included", "items": internal_items})
    sections["additional_notes"] = _section(
        "additional_notes", additional_blocks, bool(additional_blocks), "No additional notes were added for this report."
    )

    # Report-only images stay in this request and appear beside the activity
    # they document; they are never persisted as maintenance attachments.
    for section_id, entries in manual.items():
        photo_items: list[dict[str, Any]] = []
        seen_photo_ids: set[int] = set()
        for entry in entries:
            for photo_id in entry.get("photo_ids", []):
                if photo_id in uploaded_photo_by_id and photo_id not in seen_photo_ids:
                    photo_items.append(uploaded_photo_by_id[photo_id])
                    seen_photo_ids.add(photo_id)
        if include_photos and photo_items:
            sections[section_id]["blocks"].append({"type": "photos", "title": "Activity photos", "items": photo_items})
            sections[section_id]["has_activity"] = True

    # Overrides are matched by the visible field label, allowing source-backed
    # and unavailable values alike to be tailored for this PDF only.
    if detail_overrides:
        for section in sections.values():
            for block in section["blocks"]:
                for item in block.get("items", []):
                    if isinstance(item, dict):
                        label = _clean(item.get("label"), 120).lower()
                        if label in detail_overrides:
                            item["value"] = detail_overrides[label]

    # Do not clutter owner reports with unavailable placeholders. A field is
    # restored automatically when staff choose it and provide an override.
    unavailable_prefixes = ("not recorded", "not available")
    for section in sections.values():
        for block in section["blocks"]:
            if block.get("type") not in {"key_values", "status_cards"}:
                continue
            block["items"] = [
                item for item in block.get("items", [])
                if not _clean(item.get("value")).lower().startswith(unavailable_prefixes)
            ]

    section_notes = _value(options, "section_notes", {}) or {}
    if isinstance(section_notes, Mapping):
        for section_id, note in section_notes.items():
            if section_id in sections and _multiline(note):
                sections[section_id]["blocks"].append({"type": "note", "title": "Section note", "text": _multiline(note), "tone": "gold"})
                sections[section_id]["has_activity"] = True

    included_sections: list[dict[str, Any]] = []
    excluded_empty: list[str] = []
    for section_id in selected:
        section = sections[section_id]
        if section_id == "supporting_photos" and not include_photos:
            excluded_empty.append(section_id)
            continue
        if not section["has_activity"] and not include_empty:
            excluded_empty.append(section_id)
            continue
        if not section["has_activity"]:
            section["blocks"] = [{"type": "empty", "text": section["empty_message"]}]
        included_sections.append(section)
    if not included_sections:
        raise LandlordReportError("The selected sections have no reportable activity. Enable sections with no activity or add a report activity.")

    included_section_ids = {section["id"] for section in included_sections}
    appendix_pdf_ids: list[int] = []
    for section_id in selected:
        if section_id not in included_section_ids:
            continue
        for entry in manual.get(section_id, []):
            for pdf_id in entry.get("pdf_ids", []):
                if pdf_id not in appendix_pdf_ids:
                    appendix_pdf_ids.append(pdf_id)

    hero_photo_id = _value(options, "hero_photo_id")
    try:
        hero_photo_id = int(hero_photo_id) if hero_photo_id else None
    except (TypeError, ValueError):
        hero_photo_id = None
    if hero_photo_id not in available_photo_ids or not include_photos:
        hero_photo_id = None

    report = {
        "meta": {
            "agency_name": AGENCY_NAME,
            "agency_email": AGENCY_EMAIL,
            "agency_phone": AGENCY_PHONE,
            "agency_website": AGENCY_WEBSITE,
            "disclaimer": REPORT_DISCLAIMER,
            "property_id": prop.id,
            "property_address": detail_overrides.get("property address") or _full_property_label(prop),
            "landlord_name": landlord_name,
            "property_manager_name": manager.name,
            "property_manager_email": manager.email,
            "period_start": start_date,
            "period_end": end_date,
            "period_label": period_label(start_date, end_date),
            "prepared_date": prepared_date,
            "prepared_date_label": format_date_au(prepared_date),
            "intro_message": intro_message,
            "cover_intro_message": intro_message[:240] + ("..." if len(intro_message) > 240 else ""),
            "include_internal_notes": include_internal,
            "include_financial": include_financial,
            "include_photos": include_photos,
            "hero_photo_id": hero_photo_id,
            "filename": safe_report_filename(_full_property_label(prop), start_date, end_date),
        },
        "sections": included_sections,
        "included_section_ids": [section["id"] for section in included_sections],
        "excluded_empty_section_ids": excluded_empty,
        "available_photo_ids": [item["attachment_id"] for item in available_photos] + list(uploaded_photo_by_id),
        "appendix_pdf_ids": appendix_pdf_ids,
        "warnings": [
            "Inspection, bond, insurance, advertising, applications and market figures are included only when staff add verified report activities."
        ],
    }
    return report


def _attachment_disk_path(row: MaintenanceAttachment) -> Path | None:
    if not row.storage_path:
        return None
    root = Path(settings.TENANT_UPLOAD_DIR).resolve()
    candidate = Path(row.storage_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if root != resolved and root not in resolved.parents:
        raise LandlordReportError("A supporting photo has an invalid storage path.")
    return resolved


def _attachment_bytes(row: MaintenanceAttachment) -> bytes:
    disk_path = _attachment_disk_path(row)
    if not disk_path:
        return row.content_bytes or b""
    if not disk_path.exists() or not disk_path.is_file():
        raise LandlordReportError("A selected supporting photo is no longer available.")
    try:
        return disk_path.read_bytes()
    except OSError as exc:
        raise LandlordReportError("A selected supporting photo could not be read.") from exc


def load_photo_bytes(
    db: Session,
    *,
    mailbox: str,
    property_id: int,
    attachment_ids: Iterable[int],
) -> dict[int, tuple[bytes, str]]:
    wanted = {int(value) for value in attachment_ids if value}
    if not wanted:
        return {}
    prop = _get_property(db, mailbox, property_id)
    rows = (
        db.query(MaintenanceAttachment)
        .join(MaintenanceOrder, MaintenanceAttachment.order_id == MaintenanceOrder.id)
        .filter(MaintenanceAttachment.mailbox == mailbox)
        .filter(MaintenanceOrder.mailbox == mailbox)
        .filter(
            or_(
                MaintenanceOrder.property_id == property_id,
                MaintenanceOrder.property_address.ilike(prop.property_address),
            )
        )
        .filter(MaintenanceAttachment.id.in_(wanted))
        .all()
    )
    loaded: dict[int, tuple[bytes, str]] = {}
    for row in rows:
        content_type = str(row.content_type or "").lower()
        if content_type not in SUPPORTED_REPORT_IMAGE_TYPES:
            guessed = mimetypes.guess_type(row.filename or "")[0] or ""
            content_type = guessed.lower()
        if content_type not in SUPPORTED_REPORT_IMAGE_TYPES:
            continue
        content = _attachment_bytes(row)
        if content:
            loaded[row.id] = (content, content_type)
    return loaded


def _h(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "Not recorded"), quote=True)


def _html_block(block: dict[str, Any], photo_bytes: dict[int, tuple[bytes, str]]) -> str:
    block_type = block.get("type")
    if block_type == "status_cards":
        return '<div class="lrp-status-grid">' + "".join(
            f'<div class="lrp-status-card {_h(item.get("tone", "neutral"))}"><span>{_h(item.get("label"))}</span><strong>{_h(item.get("value"))}</strong></div>'
            for item in block.get("items", [])
        ) + "</div>"
    if block_type == "key_values":
        return '<div class="lrp-kv-grid">' + "".join(
            f'<div><span>{_h(item.get("label"))}</span><strong>{_h(item.get("value"))}</strong></div>'
            for item in block.get("items", [])
        ) + "</div>"
    if block_type == "table":
        columns = block.get("columns", [])
        head = "".join(f"<th>{_h(column.get('label'))}</th>" for column in columns)
        rows = "".join(
            "<tr>" + "".join(f"<td>{_h(row.get(column.get('key')))}</td>" for column in columns) + "</tr>"
            for row in block.get("rows", [])
        )
        title = f"<h4>{_h(block.get('title'))}</h4>" if block.get("title") else ""
        return f'{title}<div class="lrp-table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>'
    if block_type == "record_cards":
        cards = []
        for item in block.get("items", []):
            fields = "".join(
                f'<div><span>{_h(field.get("label"))}</span><strong>{_h(field.get("value"))}</strong></div>'
                for field in item.get("fields", [])
            )
            description = f'<p>{_h(item.get("description"))}</p>' if item.get("description") else ""
            cards.append(
                f'<article class="lrp-record-card"><header><h4>{_h(item.get("title"))}</h4><b class="lrp-badge {_h(item.get("tone", "neutral"))}">{_h(item.get("status"))}</b></header><div class="lrp-record-fields">{fields}</div>{description}</article>'
            )
        return "".join(cards)
    if block_type == "timeline":
        title = f"<h4>{_h(block.get('title'))}</h4>" if block.get("title") else ""
        items = []
        for item in block.get("items", []):
            action = f'<div class="lrp-action-mini"><strong>Landlord action:</strong> {_h(item.get("action_required"))}</div>' if item.get("action_required") else ""
            internal = '<b class="lrp-badge internal">Internal - intentionally included</b>' if item.get("internal") else ""
            items.append(
                f'<article class="lrp-timeline-item"><time>{_h(item.get("date"))}</time><div><div class="lrp-timeline-title"><strong>{_h(item.get("title"))}</strong><b class="lrp-badge {_h(item.get("tone", "neutral"))}">{_h(item.get("status"))}</b>{internal}</div><p>{_h(item.get("description"))}</p>{action}</div></article>'
            )
        return title + '<div class="lrp-timeline">' + "".join(items) + "</div>"
    if block_type == "note":
        return f'<div class="lrp-note {_h(block.get("tone", "neutral"))}"><strong>{_h(block.get("title"))}</strong><p>{_h(block.get("text"))}</p></div>'
    if block_type == "actions":
        items = "".join(f"<li>{_h(item)}</li>" for item in block.get("items", []))
        return f'<div class="lrp-action-box"><strong>{_h(block.get("title") or "Landlord action required")}</strong><ul>{items}</ul></div>'
    if block_type == "photos":
        photos = []
        for item in block.get("items", []):
            attachment_id = int(item.get("attachment_id") or 0)
            loaded = photo_bytes.get(attachment_id)
            if not loaded:
                continue
            raw, content_type = loaded
            src = f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"
            photos.append(
                f'<figure><img src="{src}" alt="{_h(item.get("caption"))}"/><figcaption>{_h(item.get("caption"))}<span>{_h(item.get("date"))}</span></figcaption></figure>'
            )
        return '<div class="lrp-photo-grid">' + "".join(photos) + "</div>"
    if block_type == "empty":
        return f'<div class="lrp-empty">{_h(block.get("text"))}</div>'
    return ""


def render_preview_html(report: dict[str, Any], photo_bytes: dict[int, tuple[bytes, str]]) -> str:
    meta = report["meta"]
    hero = ""
    hero_id = meta.get("hero_photo_id")
    if hero_id and hero_id in photo_bytes:
        raw, content_type = photo_bytes[hero_id]
        src = f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"
        hero = f'<img class="lrp-hero" src="{src}" alt="Property supporting photo"/>'
    cover_intro = meta.get("cover_intro_message") or meta.get("intro_message")
    intro = f'<div class="lrp-cover-message">{_h(cover_intro)}</div>' if cover_intro else ""
    contents = "".join(f'<li><span>{idx}</span>{_h(section["title"])}</li>' for idx, section in enumerate(report["sections"], 1))
    section_pages = []
    for idx, section in enumerate(report["sections"], 1):
        blocks = "".join(_html_block(block, photo_bytes) for block in section["blocks"])
        section_pages.append(
            f'<section class="lrp-page"><div class="lrp-page-head"><img src="/static/dons_premier_transparent_v2.png" alt="Dons Premier Estate Agents"/><span>{_h(meta["property_address"])}</span></div><div class="lrp-section-kicker">Section {idx:02d}</div><h2>{_h(section["title"])}</h2><div class="lrp-gold-rule"></div>{blocks}<div class="lrp-footer"><span>{_h(AGENCY_NAME)} | {_h(AGENCY_EMAIL)} | {_h(AGENCY_PHONE)} | {_h(AGENCY_WEBSITE)}</span><small>{_h(REPORT_DISCLAIMER)}</small></div></section>'
        )
    return f"""
    <div class="lr-pdf-preview">
      <style>
        .lr-pdf-preview{{--ink:#101828;--gold:#b58b2a;--paper:#fff;--soft:#f3f5f8;display:grid;gap:22px;color:var(--ink);font-family:Georgia,'Times New Roman',serif}}
        .lrp-page{{position:relative;width:min(100%,794px);min-height:1123px;margin:0 auto;padding:74px 62px 86px;background:var(--paper);border:1px solid #d8dee8;box-shadow:0 18px 48px rgba(15,23,42,.12)}}
        .lrp-cover{{padding-top:54px;overflow:hidden;background:linear-gradient(90deg,#fff 0 92%,#f4ead0 92% 94%,#111827 94%)}}
        .lrp-cover-logo{{width:118px;height:118px;object-fit:contain}}
        .lrp-cover-band{{margin:42px -62px 32px;padding:38px 62px;background:#111827;color:#fff;border-left:9px solid var(--gold)}}
        .lrp-cover-band small,.lrp-section-kicker{{display:block;color:#d7b764;font:800 12px/1.2 'Segoe UI',sans-serif;letter-spacing:.14em;text-transform:uppercase}}
        .lrp-cover-band h1{{margin:10px 0 8px;font-size:42px;line-height:1.05}}
        .lrp-cover-band p{{margin:0;color:#e5e7eb;font:600 17px/1.4 'Segoe UI',sans-serif}}
        .lrp-cover-meta{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#d9dee7;border:1px solid #d9dee7}}
        .lrp-cover-meta div{{padding:14px;background:#fff}}.lrp-cover-meta span,.lrp-kv-grid span,.lrp-record-fields span,.lrp-status-card span{{display:block;color:#667085;font:800 10px/1.2 'Segoe UI',sans-serif;letter-spacing:.07em;text-transform:uppercase}}
        .lrp-cover-meta strong{{display:block;margin-top:5px;font:700 14px/1.35 'Segoe UI',sans-serif}}
        .lrp-hero{{display:block;width:100%;max-height:280px;margin-top:24px;object-fit:contain;background:#f5f6f8}}
        .lrp-cover-message{{margin-top:22px;padding:16px 18px;border-left:4px solid var(--gold);background:#faf7ef;font:15px/1.55 'Segoe UI',sans-serif;white-space:pre-wrap}}
        .lrp-page-head{{position:absolute;left:62px;right:62px;top:24px;display:flex;align-items:center;gap:12px;padding-bottom:9px;border-bottom:1px solid #d8c48e;color:#475467;font:700 10px/1.2 'Segoe UI',sans-serif}}
        .lrp-page-head img{{width:28px;height:28px;object-fit:contain}}.lrp-page h2{{margin:8px 0 6px;font-size:28px;line-height:1.15}}
        .lrp-gold-rule{{width:62px;height:4px;margin-bottom:22px;background:var(--gold)}}
        .lrp-contents{{list-style:none;padding:0;margin:26px 0}}.lrp-contents li{{display:grid;grid-template-columns:34px 1fr;gap:12px;padding:12px 0;border-bottom:1px solid #e4e7ec;font:700 14px/1.3 'Segoe UI',sans-serif}}.lrp-contents li span{{color:var(--gold)}}
        .lrp-status-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:18px}}.lrp-status-card{{padding:13px;border:1px solid #e2e7ee;border-top:4px solid #98a2b3;background:#fff}}.lrp-status-card strong{{display:block;margin-top:7px;font:800 14px/1.3 'Segoe UI',sans-serif}}.lrp-status-card.success{{border-top-color:#25855a}}.lrp-status-card.warning{{border-top-color:#b58b2a}}.lrp-status-card.danger{{border-top-color:#c73b3b}}
        .lrp-kv-grid{{display:grid;grid-template-columns:1fr 1fr;border:1px solid #e2e7ee;margin-bottom:18px}}.lrp-kv-grid div{{padding:12px;border-bottom:1px solid #e2e7ee}}.lrp-kv-grid div:nth-child(odd){{border-right:1px solid #e2e7ee}}.lrp-kv-grid strong{{display:block;margin-top:5px;font:650 13px/1.4 'Segoe UI',sans-serif;overflow-wrap:anywhere}}
        .lrp-page h4{{margin:18px 0 8px;font:800 14px/1.3 'Segoe UI',sans-serif}}.lrp-table-wrap{{overflow:hidden;border:1px solid #dfe4eb;margin-bottom:18px}}.lr-pdf-preview table{{width:100%;border-collapse:collapse;table-layout:fixed}}.lr-pdf-preview th{{padding:9px 8px;background:#111827;color:#fff;text-align:left;font:800 9px/1.25 'Segoe UI',sans-serif;text-transform:uppercase}}.lr-pdf-preview td{{padding:9px 8px;border-bottom:1px solid #e6e9ef;vertical-align:top;font:11px/1.4 'Segoe UI',sans-serif;overflow-wrap:anywhere}}.lr-pdf-preview tr:nth-child(even) td{{background:#f7f8fa}}
        .lrp-record-card{{margin-bottom:14px;border:1px solid #dfe4eb;break-inside:avoid}}.lrp-record-card header{{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:11px 13px;background:#f5f6f8;border-bottom:1px solid #e0e4ea}}.lrp-record-card header h4{{margin:0}}.lrp-record-fields{{display:grid;grid-template-columns:repeat(2,1fr)}}.lrp-record-fields div{{padding:10px 13px;border-bottom:1px solid #edf0f3}}.lrp-record-fields strong{{display:block;margin-top:4px;font:650 11px/1.4 'Segoe UI',sans-serif}}.lrp-record-card>p{{margin:0;padding:12px 13px;font:12px/1.5 'Segoe UI',sans-serif;white-space:pre-wrap}}
        .lrp-badge{{display:inline-flex;padding:4px 7px;border:1px solid #d0d5dd;border-radius:999px;background:#f2f4f7;color:#475467;font:800 8px/1.2 'Segoe UI',sans-serif;text-transform:uppercase;white-space:nowrap}}.lrp-badge.success{{border-color:#a6d7c1;background:#eaf8f1;color:#166b48}}.lrp-badge.warning{{border-color:#ead49a;background:#fff8e5;color:#8a6512}}.lrp-badge.danger{{border-color:#edb4b4;background:#fff0f0;color:#9e2929}}.lrp-badge.internal{{border-color:#c9b5e8;background:#f5f0fb;color:#6f42a0}}
        .lrp-timeline{{display:grid;gap:10px;margin-bottom:18px}}.lrp-timeline-item{{display:grid;grid-template-columns:84px 1fr;gap:12px;padding:11px 0;border-bottom:1px solid #e4e7ec}}.lrp-timeline-item time{{color:#8a6a1e;font:800 10px/1.35 'Segoe UI',sans-serif}}.lrp-timeline-title{{display:flex;align-items:center;gap:7px;flex-wrap:wrap;font:700 12px/1.35 'Segoe UI',sans-serif}}.lrp-timeline-item p{{margin:5px 0 0;font:11px/1.5 'Segoe UI',sans-serif;white-space:pre-wrap}}
        .lrp-note,.lrp-action-box,.lrp-empty{{margin:14px 0;padding:14px 16px;border-left:4px solid #98a2b3;background:#f5f6f8;font:12px/1.5 'Segoe UI',sans-serif;white-space:pre-wrap}}.lrp-note.gold{{border-color:var(--gold);background:#faf7ef}}.lrp-note.internal{{border-color:#7653a6;background:#f6f1fb}}.lrp-note p{{margin:6px 0 0}}.lrp-action-box{{border:1px solid #dfc475;border-left:6px solid var(--gold);background:#fff8e7}}.lrp-action-box strong{{color:#6e5010}}.lrp-action-box ul{{margin:7px 0 0;padding-left:18px}}.lrp-action-mini{{margin-top:7px;padding:8px;background:#fff8e7;border-left:3px solid var(--gold);font:10px/1.4 'Segoe UI',sans-serif}}
        .lrp-photo-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.lrp-photo-grid figure{{margin:0;border:1px solid #dfe4eb;background:#f8f9fb;break-inside:avoid}}.lrp-photo-grid img{{display:block;width:100%;height:230px;object-fit:contain;background:#eef1f5}}.lrp-photo-grid figcaption{{display:flex;justify-content:space-between;gap:10px;padding:9px;font:10px/1.35 'Segoe UI',sans-serif}}.lrp-photo-grid figcaption span{{color:#667085;white-space:nowrap}}
        .lrp-footer{{position:absolute;left:62px;right:62px;bottom:24px;padding-top:8px;border-top:1px solid #d8c48e;color:#475467;font:8px/1.35 'Segoe UI',sans-serif}}.lrp-footer small{{display:block;margin-top:4px}}
        @media(max-width:900px){{.lrp-page{{min-height:auto;padding:58px 28px 78px}}.lrp-cover-band{{margin-left:-28px;margin-right:-28px;padding-left:28px;padding-right:28px}}.lrp-page-head,.lrp-footer{{left:28px;right:28px}}.lrp-status-grid{{grid-template-columns:1fr 1fr}}}}
        @media(max-width:620px){{.lrp-cover{{background:#fff}}.lrp-cover-meta,.lrp-kv-grid,.lrp-record-fields,.lrp-photo-grid{{grid-template-columns:1fr}}.lrp-kv-grid div:nth-child(odd){{border-right:none}}.lrp-status-grid{{grid-template-columns:1fr}}.lrp-timeline-item{{grid-template-columns:1fr}}}}
      </style>
      <section class="lrp-page lrp-cover">
        <img class="lrp-cover-logo" src="/static/dons_premier_transparent_v2.png" alt="Dons Premier Estate Agents"/>
        <div class="lrp-cover-band"><small>Dons Premier Estate Agents</small><h1>Monthly Property Report</h1><p>{_h(meta["property_address"])}</p></div>
        <div class="lrp-cover-meta">
          <div><span>Reporting period</span><strong>{_h(meta["period_label"])}</strong></div>
          <div><span>Landlord</span><strong>{_h(meta["landlord_name"])}</strong></div>
          <div><span>Property manager</span><strong>{_h(meta["property_manager_name"])}</strong></div>
          <div><span>Prepared date</span><strong>{_h(meta["prepared_date_label"])}</strong></div>
        </div>{hero}{intro}
        <div class="lrp-footer"><span>{_h(AGENCY_NAME)} | {_h(AGENCY_EMAIL)} | {_h(AGENCY_PHONE)} | {_h(AGENCY_WEBSITE)}</span><small>{_h(REPORT_DISCLAIMER)}</small></div>
      </section>
      <section class="lrp-page"><div class="lrp-page-head"><img src="/static/dons_premier_transparent_v2.png" alt="Dons Premier Estate Agents"/><span>{_h(meta["property_address"])}</span></div><div class="lrp-section-kicker">Report navigation</div><h2>Contents</h2><div class="lrp-gold-rule"></div><ol class="lrp-contents">{contents}</ol><div class="lrp-footer"><span>{_h(AGENCY_NAME)} | {_h(AGENCY_EMAIL)} | {_h(AGENCY_PHONE)} | {_h(AGENCY_WEBSITE)}</span><small>{_h(REPORT_DISCLAIMER)}</small></div></section>
      {''.join(section_pages)}
    </div>
    """
