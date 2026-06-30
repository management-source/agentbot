from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.authz import get_current_user
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import (
    ComplianceProvider,
    ComplianceRecord,
    ComplianceRecordStatus,
    ComplianceType,
    ManagedProperty,
    User,
)

router = APIRouter()
DUE_SOON_DAYS = 30
MAIN_CHECK_TYPES = (
    ComplianceType.MRS,
    ComplianceType.SMOKE,
    ComplianceType.GAS,
    ComplianceType.ELECTRICAL,
)
CYCLE_YEARS = {
    ComplianceType.MRS: 2,
    ComplianceType.GAS: 2,
    ComplianceType.ELECTRICAL: 2,
    ComplianceType.SMOKE: 1,
}


class ComplianceRecordCreateIn(BaseModel):
    property_id: int
    compliance_type: ComplianceType
    status: ComplianceRecordStatus = ComplianceRecordStatus.OPEN
    due_date: datetime | None = None
    completed_at: datetime | None = None
    provider_name: str | None = None
    result_text: str | None = None
    notes: str | None = None


class ComplianceRecordUpdateIn(BaseModel):
    status: ComplianceRecordStatus | None = None
    due_date: datetime | None = None
    completed_at: datetime | None = None
    provider_name: str | None = None
    result_text: str | None = None
    notes: str | None = None


class ComplianceProviderIn(BaseModel):
    name: str
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    is_active: bool = True


def _record_state(row: ComplianceRecord) -> str:
    if row.status == ComplianceRecordStatus.WAIVED:
        return "WAIVED"
    if row.status == ComplianceRecordStatus.ACTION_REQUIRED:
        return "ACTION_REQUIRED"
    if row.due_date:
        today = datetime.utcnow().date()
        due = row.due_date.date()
        if due < today:
            return "OVERDUE"
        if due <= today + timedelta(days=DUE_SOON_DAYS):
            return "DUE_SOON"
    if row.status == ComplianceRecordStatus.COMPLETED:
        return "CURRENT"
    return "OPEN"


def _add_years(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # Handles 29 February on non-leap target years.
        return value.replace(month=2, day=28, year=value.year + years)


def _calculated_due_date(compliance_type: ComplianceType, completed_at: datetime | None) -> datetime | None:
    if not completed_at:
        return None
    years = CYCLE_YEARS.get(compliance_type)
    if not years:
        return None
    return _add_years(completed_at, years)


def _fields_set(payload: BaseModel) -> set[str]:
    return set(getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", set())))


def _check_label(compliance_type: ComplianceType) -> str:
    labels = {
        ComplianceType.MRS: "MRS",
        ComplianceType.SMOKE: "Smoke",
        ComplianceType.GAS: "Gas",
        ComplianceType.ELECTRICAL: "Electrical",
    }
    return labels.get(compliance_type, compliance_type.value.title())


def _record_to_dict(row: ComplianceRecord) -> dict[str, Any]:
    prop = row.property
    return {
        "id": row.id,
        "property_id": row.property_id,
        "property_address": prop.property_address if prop else "",
        "suburb": prop.suburb if prop else None,
        "state_code": prop.state_code if prop else None,
        "postcode": prop.postcode if prop else None,
        "compliance_type": row.compliance_type.value,
        "status": row.status.value,
        "state": _record_state(row),
        "due_date": row.due_date,
        "completed_at": row.completed_at,
        "provider_name": row.provider_name,
        "result_text": row.result_text,
        "notes": row.notes,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _provider_to_dict(row: ComplianceProvider) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "contact_name": row.contact_name,
        "email": row.email,
        "phone": row.phone,
        "notes": row.notes,
        "is_active": row.is_active,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _coverage_record_to_dict(row: ComplianceRecord | None, compliance_type: ComplianceType) -> dict[str, Any]:
    if not row:
        return {
            "type": compliance_type.value,
            "label": _check_label(compliance_type),
            "state": "MISSING",
            "status": None,
            "record_id": None,
            "completed_at": None,
            "due_date": None,
            "provider_name": None,
        }
    return {
        "type": compliance_type.value,
        "label": _check_label(compliance_type),
        "state": _record_state(row),
        "status": row.status.value,
        "record_id": row.id,
        "completed_at": row.completed_at,
        "due_date": row.due_date,
        "provider_name": row.provider_name,
    }


def _get_property(db: Session, mailbox: str, property_id: int) -> ManagedProperty:
    prop = (
        db.query(ManagedProperty)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.id == property_id)
        .filter(ManagedProperty.is_active == True)
        .first()
    )
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found.")
    return prop


def _clean_provider_payload(payload: ComplianceProviderIn) -> dict[str, Any]:
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Provider name is required.")
    return {
        "name": name,
        "contact_name": (payload.contact_name or "").strip() or None,
        "email": (payload.email or "").strip() or None,
        "phone": (payload.phone or "").strip() or None,
        "notes": (payload.notes or "").strip() or None,
        "is_active": bool(payload.is_active),
    }


@router.get("/providers")
def list_providers(
    query: str | None = None,
    include_inactive: bool = False,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(ComplianceProvider).filter(ComplianceProvider.mailbox == mailbox)
    if not include_inactive:
        q = q.filter(ComplianceProvider.is_active == True)
    if query and query.strip():
        like = f"%{query.strip()}%"
        q = q.filter(
            or_(
                ComplianceProvider.name.ilike(like),
                ComplianceProvider.contact_name.ilike(like),
                ComplianceProvider.email.ilike(like),
                ComplianceProvider.phone.ilike(like),
                ComplianceProvider.notes.ilike(like),
            )
        )
    rows = q.order_by(ComplianceProvider.is_active.desc(), ComplianceProvider.name.asc()).all()
    return {"items": [_provider_to_dict(row) for row in rows], "total": len(rows)}


@router.post("/providers")
def create_provider(
    payload: ComplianceProviderIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    data = _clean_provider_payload(payload)
    existing = (
        db.query(ComplianceProvider)
        .filter(ComplianceProvider.mailbox == mailbox)
        .filter(func.lower(ComplianceProvider.name) == data["name"].lower())
        .first()
    )
    now = datetime.utcnow()
    if existing:
        for field, value in data.items():
            setattr(existing, field, value)
        existing.updated_at = now
        db.commit()
        db.refresh(existing)
        return _provider_to_dict(existing)

    row = ComplianceProvider(mailbox=mailbox, created_at=now, updated_at=now, **data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _provider_to_dict(row)


@router.put("/providers/{provider_id}")
def update_provider(
    provider_id: int,
    payload: ComplianceProviderIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = (
        db.query(ComplianceProvider)
        .filter(ComplianceProvider.mailbox == mailbox)
        .filter(ComplianceProvider.id == provider_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Compliance provider not found.")
    data = _clean_provider_payload(payload)
    duplicate = (
        db.query(ComplianceProvider)
        .filter(ComplianceProvider.mailbox == mailbox)
        .filter(ComplianceProvider.id != provider_id)
        .filter(func.lower(ComplianceProvider.name) == data["name"].lower())
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="Another provider already uses this name.")
    for field, value in data.items():
        setattr(row, field, value)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _provider_to_dict(row)


@router.delete("/providers/{provider_id}")
def delete_provider(
    provider_id: int,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = (
        db.query(ComplianceProvider)
        .filter(ComplianceProvider.mailbox == mailbox)
        .filter(ComplianceProvider.id == provider_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Compliance provider not found.")
    row.is_active = False
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/records")
def create_record(
    payload: ComplianceRecordCreateIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    _get_property(db, mailbox, payload.property_id)
    now = datetime.utcnow()
    completed_at = payload.completed_at
    status = payload.status
    if completed_at and status == ComplianceRecordStatus.OPEN:
        status = ComplianceRecordStatus.COMPLETED
    row = ComplianceRecord(
        mailbox=mailbox,
        property_id=payload.property_id,
        compliance_type=payload.compliance_type,
        status=status,
        due_date=payload.due_date or _calculated_due_date(payload.compliance_type, completed_at),
        completed_at=completed_at,
        provider_name=(payload.provider_name or "").strip() or None,
        result_text=(payload.result_text or "").strip() or None,
        notes=(payload.notes or "").strip() or None,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _record_to_dict(row)


@router.patch("/records/{record_id}")
def update_record(
    record_id: int,
    payload: ComplianceRecordUpdateIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = (
        db.query(ComplianceRecord)
        .filter(ComplianceRecord.mailbox == mailbox)
        .filter(ComplianceRecord.id == record_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Compliance record not found.")

    fields_set = _fields_set(payload)
    if payload.status is not None:
        row.status = payload.status
        if payload.status == ComplianceRecordStatus.COMPLETED and not row.completed_at:
            row.completed_at = datetime.utcnow()
        elif payload.status != ComplianceRecordStatus.COMPLETED:
            row.completed_at = None

    if "completed_at" in fields_set:
        row.completed_at = payload.completed_at
        if payload.completed_at and row.status == ComplianceRecordStatus.OPEN:
            row.status = ComplianceRecordStatus.COMPLETED
    if row.status == ComplianceRecordStatus.COMPLETED and not row.completed_at:
        row.completed_at = datetime.utcnow()
    if "due_date" in fields_set:
        row.due_date = payload.due_date
    elif row.completed_at:
        row.due_date = _calculated_due_date(row.compliance_type, row.completed_at)
    elif payload.status is not None and payload.status != ComplianceRecordStatus.COMPLETED:
        row.due_date = None

    if "provider_name" in fields_set:
        row.provider_name = (payload.provider_name or "").strip() or None
    if "result_text" in fields_set:
        row.result_text = (payload.result_text or "").strip() or None
    if "notes" in fields_set:
        row.notes = (payload.notes or "").strip() or None

    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _record_to_dict(row)


@router.delete("/records/{record_id}")
def delete_record(
    record_id: int,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = (
        db.query(ComplianceRecord)
        .filter(ComplianceRecord.mailbox == mailbox)
        .filter(ComplianceRecord.id == record_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Compliance record not found.")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/records")
def list_records(
    state: str | None = None,
    compliance_type: ComplianceType | None = None,
    query: str | None = None,
    page: int = 1,
    page_size: int = 25,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = (
        db.query(ComplianceRecord)
        .join(ManagedProperty, ComplianceRecord.property_id == ManagedProperty.id)
        .filter(ComplianceRecord.mailbox == mailbox)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.is_active == True)
    )
    if compliance_type:
        q = q.filter(ComplianceRecord.compliance_type == compliance_type)
    if query and query.strip():
        like = f"%{query.strip()}%"
        q = q.filter(
            or_(
                ManagedProperty.property_address.ilike(like),
                ManagedProperty.suburb.ilike(like),
                ComplianceRecord.provider_name.ilike(like),
                ComplianceRecord.result_text.ilike(like),
                ComplianceRecord.notes.ilike(like),
            )
        )

    rows_all = q.order_by(ComplianceRecord.due_date.asc().nullslast(), ManagedProperty.property_address.asc()).all()
    if state and state.strip():
        wanted = state.strip().upper()
        rows_all = [r for r in rows_all if _record_state(r) == wanted]

    page = max(int(page or 1), 1)
    page_size = max(10, min(int(page_size or 25), 200))
    total = len(rows_all)
    start = (page - 1) * page_size
    rows = rows_all[start:start + page_size]
    return {
        "items": [_record_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (page * page_size) < total,
    }


@router.get("/summary")
def summary(
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    properties_total = (
        db.query(func.count(ManagedProperty.id))
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.is_active == True)
        .scalar()
        or 0
    )
    rows = (
        db.query(ComplianceRecord)
        .join(ManagedProperty, ComplianceRecord.property_id == ManagedProperty.id)
        .filter(ComplianceRecord.mailbox == mailbox)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.is_active == True)
        .all()
    )
    state_counts: dict[str, int] = {
        "OPEN": 0,
        "DUE_SOON": 0,
        "OVERDUE": 0,
        "ACTION_REQUIRED": 0,
        "CURRENT": 0,
        "WAIVED": 0,
    }
    for row in rows:
        key = _record_state(row)
        state_counts[key] = state_counts.get(key, 0) + 1

    return {
        "total_records": len(rows),
        "total_properties": properties_total,
        "due_soon_window_days": DUE_SOON_DAYS,
        "open_records": state_counts.get("OPEN", 0),
        "due_soon_records": state_counts.get("DUE_SOON", 0),
        "overdue_records": state_counts.get("OVERDUE", 0),
        "action_required_records": state_counts.get("ACTION_REQUIRED", 0),
        "current_records": state_counts.get("CURRENT", 0),
        "waived_records": state_counts.get("WAIVED", 0),
        "state_counts": state_counts,
    }


@router.get("/coverage")
def coverage(
    query: str | None = None,
    page: int = 1,
    page_size: int = 25,
    include_current: bool = False,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    props_q = (
        db.query(ManagedProperty)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.is_active == True)
    )
    if query and query.strip():
        like = f"%{query.strip()}%"
        props_q = props_q.filter(
            or_(
                ManagedProperty.property_address.ilike(like),
                ManagedProperty.suburb.ilike(like),
                ManagedProperty.postcode.ilike(like),
            )
        )
    props = props_q.order_by(ManagedProperty.property_address.asc()).all()
    prop_ids = [p.id for p in props]

    records = []
    if prop_ids:
        records = (
            db.query(ComplianceRecord)
            .filter(ComplianceRecord.mailbox == mailbox)
            .filter(ComplianceRecord.property_id.in_(prop_ids))
            .filter(ComplianceRecord.compliance_type.in_(MAIN_CHECK_TYPES))
            .all()
        )

    latest_by_key: dict[tuple[int, ComplianceType], ComplianceRecord] = {}

    def sort_key(row: ComplianceRecord) -> tuple[datetime, datetime, int]:
        return (
            row.updated_at or datetime.min,
            row.created_at or datetime.min,
            row.id or 0,
        )

    for row in records:
        key = (row.property_id, row.compliance_type)
        existing = latest_by_key.get(key)
        if not existing or sort_key(row) > sort_key(existing):
            latest_by_key[key] = row

    items: list[dict[str, Any]] = []
    summary_counts = {
        "total_properties": len(props),
        "fully_current": 0,
        "with_missing": 0,
        "with_incomplete": 0,
        "with_overdue": 0,
        "with_due_soon": 0,
        "needs_attention": 0,
    }

    for prop in props:
        missing: list[str] = []
        incomplete: list[str] = []
        overdue: list[str] = []
        due_soon: list[str] = []
        checks: list[dict[str, Any]] = []
        for check_type in MAIN_CHECK_TYPES:
            row = latest_by_key.get((prop.id, check_type))
            check = _coverage_record_to_dict(row, check_type)
            checks.append(check)
            if not row:
                missing.append(_check_label(check_type))
                continue
            state = _record_state(row)
            if row.status not in {ComplianceRecordStatus.COMPLETED, ComplianceRecordStatus.WAIVED}:
                incomplete.append(_check_label(check_type))
            if state == "OVERDUE":
                overdue.append(_check_label(check_type))
            elif state == "DUE_SOON":
                due_soon.append(_check_label(check_type))

        has_issue = bool(missing or incomplete or overdue or due_soon)
        if not has_issue:
            summary_counts["fully_current"] += 1
        else:
            summary_counts["needs_attention"] += 1
        if missing:
            summary_counts["with_missing"] += 1
        if incomplete:
            summary_counts["with_incomplete"] += 1
        if overdue:
            summary_counts["with_overdue"] += 1
        if due_soon:
            summary_counts["with_due_soon"] += 1

        if include_current or has_issue:
            items.append(
                {
                    "property_id": prop.id,
                    "property_address": prop.property_address,
                    "suburb": prop.suburb,
                    "state_code": prop.state_code,
                    "postcode": prop.postcode,
                    "missing": missing,
                    "incomplete": incomplete,
                    "overdue": overdue,
                    "due_soon": due_soon,
                    "checks": checks,
                }
            )

    page = max(int(page or 1), 1)
    page_size = max(10, min(int(page_size or 25), 200))
    total = len(items)
    start = (page - 1) * page_size
    rows = items[start:start + page_size]
    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (page * page_size) < total,
        "summary": summary_counts,
        "required_checks": [x.value for x in MAIN_CHECK_TYPES],
    }
