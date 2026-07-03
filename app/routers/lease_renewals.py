from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.authz import get_current_user
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import (
    LeaseRenewalEvent,
    LeaseRenewalRecord,
    LeaseRenewalStatus,
    ManagedProperty,
    User,
)


router = APIRouter()
DUE_SOON_DAYS = 30
DateInput = datetime | date | None
FINAL_STATUSES = {
    LeaseRenewalStatus.FULLY_SIGNED,
    LeaseRenewalStatus.PERIODIC_CONFIRMED,
    LeaseRenewalStatus.TENANT_VACATING,
    LeaseRenewalStatus.ADVERTISED,
    LeaseRenewalStatus.COMPLETED,
}


class LeaseRenewalCreateIn(BaseModel):
    property_id: int
    status: LeaseRenewalStatus = LeaseRenewalStatus.NOT_STARTED
    current_lease_start: DateInput = None
    current_lease_end: DateInput = None
    renewal_due_date: DateInput = None
    lease_sent_date: DateInput = None
    last_resent_date: DateInput = None
    proposed_lease_start: DateInput = None
    proposed_lease_end: DateInput = None
    proposed_term: str | None = None
    current_rent: float | None = None
    proposed_rent: float | None = None
    rent_increase_date: DateInput = None
    owner_signed_date: DateInput = None
    tenant_signed_date: DateInput = None
    follow_up_date: DateInput = None
    assigned_user_id: int | None = None
    notes: str | None = None


class LeaseRenewalUpdateIn(BaseModel):
    property_id: int | None = None
    status: LeaseRenewalStatus | None = None
    current_lease_start: DateInput = None
    current_lease_end: DateInput = None
    renewal_due_date: DateInput = None
    lease_sent_date: DateInput = None
    last_resent_date: DateInput = None
    proposed_lease_start: DateInput = None
    proposed_lease_end: DateInput = None
    proposed_term: str | None = None
    current_rent: float | None = None
    proposed_rent: float | None = None
    rent_increase_date: DateInput = None
    owner_signed_date: DateInput = None
    tenant_signed_date: DateInput = None
    follow_up_date: DateInput = None
    assigned_user_id: int | None = None
    notes: str | None = None


class LeaseRenewalStatusIn(BaseModel):
    status: LeaseRenewalStatus
    note: str | None = None


class LeaseRenewalNoteIn(BaseModel):
    note: str


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _as_datetime(value: DateInput) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)


def _fields_set(payload: BaseModel) -> set[str]:
    return set(getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", set())))


def _json_loads(value: str | None) -> dict[str, object]:
    if not value:
        return {"contacts": [], "extra_mobiles": [], "extra_phones": [], "raw": {}}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"contacts": [], "extra_mobiles": [], "extra_phones": [], "raw": {}}
    except Exception:
        return {"contacts": [], "extra_mobiles": [], "extra_phones": [], "raw": {}}


def _primary_contact(book: dict[str, object]) -> dict[str, str]:
    contacts = book.get("contacts") if isinstance(book, dict) else []
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


def _property_label(prop: ManagedProperty | None) -> str:
    if not prop:
        return ""
    tail = " ".join([x for x in [prop.suburb, prop.state_code, prop.postcode] if x])
    return ", ".join([x for x in [prop.property_address, tail] if x])


def _status_value(value: LeaseRenewalStatus | str | None) -> str:
    if hasattr(value, "value"):
        return value.value
    return str(value or "")


def _status_label(value: LeaseRenewalStatus | str | None) -> str:
    return _status_value(value).replace("_", " ").title()


def _is_final(row: LeaseRenewalRecord) -> bool:
    return row.status in FINAL_STATUSES


def _record_state(row: LeaseRenewalRecord) -> str:
    if _is_final(row):
        return "COMPLETED"
    if not row.current_lease_end or not row.renewal_due_date:
        return "MISSING_DETAILS"
    today = datetime.utcnow().date()
    due = row.renewal_due_date.date()
    follow = row.follow_up_date.date() if row.follow_up_date else None
    if due < today or (follow and follow < today):
        return "OVERDUE"
    if due <= today + timedelta(days=DUE_SOON_DAYS):
        return "DUE_SOON"
    return "ACTIVE"


def _state_label(state: str) -> str:
    return state.replace("_", " ").title()


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


def _get_assignee(db: Session, assignee_user_id: int | None) -> User | None:
    if assignee_user_id is None:
        return None
    user = db.get(User, assignee_user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="Assigned staff must be an active account.")
    return user


def _get_record(db: Session, mailbox: str, record_id: int) -> LeaseRenewalRecord:
    row = (
        db.query(LeaseRenewalRecord)
        .options(
            selectinload(LeaseRenewalRecord.property),
            selectinload(LeaseRenewalRecord.assigned_user),
            selectinload(LeaseRenewalRecord.events).selectinload(LeaseRenewalEvent.actor),
        )
        .filter(LeaseRenewalRecord.mailbox == mailbox)
        .filter(LeaseRenewalRecord.id == record_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Lease renewal record not found.")
    return row


def _add_event(
    db: Session,
    row: LeaseRenewalRecord,
    event_type: str,
    user: User | None = None,
    detail: str | None = None,
) -> None:
    db.add(
        LeaseRenewalEvent(
            mailbox=row.mailbox,
            record_id=row.id,
            actor_user_id=user.id if user else None,
            event_type=event_type,
            detail=_clean(detail),
            created_at=datetime.utcnow(),
        )
    )


def _event_to_dict(row: LeaseRenewalEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "detail": row.detail,
        "actor_name": row.actor.name if row.actor else None,
        "created_at": row.created_at,
    }


def _record_to_dict(row: LeaseRenewalRecord, include_detail: bool = False) -> dict[str, Any]:
    prop = row.property
    owners = _json_loads(prop.owners_json if prop else None)
    tenants = _json_loads(prop.tenants_json if prop else None)
    state = _record_state(row)
    events = sorted(row.events or [], key=lambda item: item.created_at or datetime.min, reverse=True)
    return {
        "id": row.id,
        "mailbox": row.mailbox,
        "property_id": row.property_id,
        "property_address": prop.property_address if prop else "",
        "suburb": prop.suburb if prop else None,
        "state_code": prop.state_code if prop else None,
        "postcode": prop.postcode if prop else None,
        "property_label": _property_label(prop),
        "tenancy_status": prop.tenancy_status if prop else None,
        "primary_owner": _primary_contact(owners),
        "primary_tenant": _primary_contact(tenants),
        "status": row.status.value,
        "status_label": _status_label(row.status),
        "state": state,
        "state_label": _state_label(state),
        "current_lease_start": row.current_lease_start,
        "current_lease_end": row.current_lease_end,
        "renewal_due_date": row.renewal_due_date,
        "lease_sent_date": row.lease_sent_date,
        "last_resent_date": row.last_resent_date,
        "proposed_lease_start": row.proposed_lease_start,
        "proposed_lease_end": row.proposed_lease_end,
        "proposed_term": row.proposed_term,
        "current_rent": row.current_rent,
        "proposed_rent": row.proposed_rent,
        "rent_increase_date": row.rent_increase_date,
        "owner_signed_date": row.owner_signed_date,
        "tenant_signed_date": row.tenant_signed_date,
        "follow_up_date": row.follow_up_date,
        "assigned_user_id": row.assigned_user_id,
        "assigned_user_name": row.assigned_user.name if row.assigned_user else None,
        "notes": row.notes,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "events": [_event_to_dict(event) for event in events] if include_detail else [],
    }


def _apply_payload(row: LeaseRenewalRecord, payload: LeaseRenewalCreateIn | LeaseRenewalUpdateIn) -> None:
    fields = _fields_set(payload)
    simple_fields = [
        "current_lease_start",
        "current_lease_end",
        "renewal_due_date",
        "lease_sent_date",
        "last_resent_date",
        "proposed_lease_start",
        "proposed_lease_end",
        "current_rent",
        "proposed_rent",
        "rent_increase_date",
        "owner_signed_date",
        "tenant_signed_date",
        "follow_up_date",
    ]
    for field in simple_fields:
        if isinstance(payload, LeaseRenewalCreateIn) or field in fields:
            setattr(row, field, _as_datetime(getattr(payload, field)))
    if isinstance(payload, LeaseRenewalCreateIn) or "proposed_term" in fields:
        row.proposed_term = _clean(payload.proposed_term)
    if isinstance(payload, LeaseRenewalCreateIn) or "notes" in fields:
        row.notes = _clean(payload.notes)
    if payload.status is not None:
        row.status = payload.status

    if row.status in FINAL_STATUSES and not row.completed_at:
        row.completed_at = datetime.utcnow()
    if row.status not in FINAL_STATUSES:
        row.completed_at = None


def _open_record_for_property(db: Session, mailbox: str, property_id: int, exclude_id: int | None = None) -> LeaseRenewalRecord | None:
    q = (
        db.query(LeaseRenewalRecord)
        .filter(LeaseRenewalRecord.mailbox == mailbox)
        .filter(LeaseRenewalRecord.property_id == property_id)
        .filter(LeaseRenewalRecord.status.notin_(list(FINAL_STATUSES)))
    )
    if exclude_id:
        q = q.filter(LeaseRenewalRecord.id != exclude_id)
    return q.order_by(LeaseRenewalRecord.updated_at.desc()).first()


@router.get("/statuses")
def statuses(_user: User = Depends(get_current_user)):
    return {
        "items": [
            {"value": status.value, "label": _status_label(status), "is_final": status in FINAL_STATUSES}
            for status in LeaseRenewalStatus
        ]
    }


@router.get("/summary")
def summary(
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = (
        db.query(LeaseRenewalRecord)
        .join(ManagedProperty, LeaseRenewalRecord.property_id == ManagedProperty.id)
        .options(selectinload(LeaseRenewalRecord.property), selectinload(LeaseRenewalRecord.assigned_user))
        .filter(LeaseRenewalRecord.mailbox == mailbox)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.is_active == True)
        .all()
    )
    today = datetime.utcnow().date()
    status_counts: dict[str, int] = {}
    state_counts: dict[str, int] = {}
    due_30 = due_60 = due_90 = 0
    awaiting_owner = awaiting_tenant = stale_followups = missing_details = 0
    fully_signed = periodic = vacating = 0
    needs_attention: list[LeaseRenewalRecord] = []
    for row in rows:
        status_counts[row.status.value] = status_counts.get(row.status.value, 0) + 1
        state = _record_state(row)
        state_counts[state] = state_counts.get(state, 0) + 1
        if state in {"OVERDUE", "DUE_SOON", "MISSING_DETAILS"}:
            needs_attention.append(row)
        if row.status == LeaseRenewalStatus.SENT_TO_OWNER:
            awaiting_owner += 1
        if row.status in {LeaseRenewalStatus.OWNER_SIGNED, LeaseRenewalStatus.SENT_TO_TENANT, LeaseRenewalStatus.PARTIALLY_SIGNED}:
            awaiting_tenant += 1
        if row.status == LeaseRenewalStatus.FULLY_SIGNED:
            fully_signed += 1
        if row.status == LeaseRenewalStatus.PERIODIC_CONFIRMED:
            periodic += 1
        if row.status in {LeaseRenewalStatus.TENANT_VACATING, LeaseRenewalStatus.ADVERTISED}:
            vacating += 1
        if state == "MISSING_DETAILS":
            missing_details += 1
        if not _is_final(row) and row.lease_sent_date and row.lease_sent_date.date() <= today - timedelta(days=14):
            if not row.owner_signed_date or not row.tenant_signed_date:
                stale_followups += 1
                if row not in needs_attention:
                    needs_attention.append(row)
        if row.renewal_due_date and not _is_final(row):
            due = row.renewal_due_date.date()
            if today <= due <= today + timedelta(days=30):
                due_30 += 1
            if today <= due <= today + timedelta(days=60):
                due_60 += 1
            if today <= due <= today + timedelta(days=90):
                due_90 += 1

    needs_attention = sorted(
        needs_attention,
        key=lambda row: (
            row.renewal_due_date or row.follow_up_date or datetime.max,
            row.updated_at or datetime.min,
        ),
    )[:8]
    return {
        "total_records": len(rows),
        "active_records": len([row for row in rows if not _is_final(row)]),
        "overdue": state_counts.get("OVERDUE", 0),
        "due_next_30": due_30,
        "due_next_60": due_60,
        "due_next_90": due_90,
        "awaiting_owner": awaiting_owner,
        "awaiting_tenant": awaiting_tenant,
        "fully_signed": fully_signed,
        "periodic_confirmed": periodic,
        "vacating_or_advertised": vacating,
        "missing_details": missing_details,
        "stale_followups": stale_followups,
        "status_counts": status_counts,
        "state_counts": state_counts,
        "needs_attention": [_record_to_dict(row) for row in needs_attention],
    }


@router.post("/records")
def create_record(
    payload: LeaseRenewalCreateIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_property(db, mailbox, payload.property_id)
    existing = _open_record_for_property(db, mailbox, payload.property_id)
    if existing:
        raise HTTPException(status_code=409, detail="This property already has an active lease renewal record.")
    assignee = _get_assignee(db, payload.assigned_user_id)
    now = datetime.utcnow()
    row = LeaseRenewalRecord(
        mailbox=mailbox,
        property_id=payload.property_id,
        assigned_user_id=assignee.id if assignee else None,
        created_by_user_id=user.id,
        created_at=now,
        updated_at=now,
    )
    _apply_payload(row, payload)
    db.add(row)
    db.flush()
    _add_event(db, row, "created", user, "Lease renewal tracking record created.")
    db.commit()
    return _record_to_dict(_get_record(db, mailbox, row.id), include_detail=True)


@router.get("/records")
def list_records(
    status: LeaseRenewalStatus | None = None,
    state: str | None = None,
    window: str | None = None,
    assigned_user_id: int | None = None,
    query: str | None = None,
    page: int = 1,
    page_size: int = 25,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = (
        db.query(LeaseRenewalRecord)
        .join(ManagedProperty, LeaseRenewalRecord.property_id == ManagedProperty.id)
        .options(selectinload(LeaseRenewalRecord.property), selectinload(LeaseRenewalRecord.assigned_user))
        .filter(LeaseRenewalRecord.mailbox == mailbox)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.is_active == True)
    )
    if status:
        q = q.filter(LeaseRenewalRecord.status == status)
    if assigned_user_id:
        q = q.filter(LeaseRenewalRecord.assigned_user_id == assigned_user_id)
    if query and query.strip():
        like = f"%{query.strip()}%"
        q = q.filter(
            or_(
                ManagedProperty.property_address.ilike(like),
                ManagedProperty.suburb.ilike(like),
                ManagedProperty.postcode.ilike(like),
                ManagedProperty.tenancy_status.ilike(like),
                ManagedProperty.owners_json.ilike(like),
                ManagedProperty.tenants_json.ilike(like),
                LeaseRenewalRecord.proposed_term.ilike(like),
                LeaseRenewalRecord.notes.ilike(like),
            )
        )
    rows_all = q.order_by(LeaseRenewalRecord.renewal_due_date.asc().nullslast(), ManagedProperty.property_address.asc()).all()
    today = datetime.utcnow().date()
    wanted_window = (window or "").strip().upper()
    wanted_state = (state or "").strip().upper()
    if wanted_state:
        rows_all = [row for row in rows_all if _record_state(row) == wanted_state]
    if wanted_window:
        if wanted_window == "ACTIVE":
            rows_all = [row for row in rows_all if not _is_final(row)]
        elif wanted_window == "COMPLETED":
            rows_all = [row for row in rows_all if _is_final(row)]
        elif wanted_window == "MISSING":
            rows_all = [row for row in rows_all if _record_state(row) == "MISSING_DETAILS"]
        elif wanted_window == "OVERDUE":
            rows_all = [row for row in rows_all if _record_state(row) == "OVERDUE"]
        elif wanted_window in {"30", "60", "90"}:
            days = int(wanted_window)
            rows_all = [
                row
                for row in rows_all
                if row.renewal_due_date
                and not _is_final(row)
                and today <= row.renewal_due_date.date() <= today + timedelta(days=days)
            ]

    page = max(int(page or 1), 1)
    page_size = max(10, min(int(page_size or 25), 200))
    total = len(rows_all)
    start = (page - 1) * page_size
    rows = rows_all[start:start + page_size]
    return {
        "items": [_record_to_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (page * page_size) < total,
    }


@router.get("/records/{record_id}")
def get_record(
    record_id: int,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return _record_to_dict(_get_record(db, mailbox, record_id), include_detail=True)


@router.patch("/records/{record_id}")
def update_record(
    record_id: int,
    payload: LeaseRenewalUpdateIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _get_record(db, mailbox, record_id)
    fields = _fields_set(payload)
    if "property_id" in fields and payload.property_id is not None and payload.property_id != row.property_id:
        _get_property(db, mailbox, payload.property_id)
        existing = _open_record_for_property(db, mailbox, payload.property_id, exclude_id=row.id)
        if existing:
            raise HTTPException(status_code=409, detail="That property already has an active lease renewal record.")
        row.property_id = payload.property_id
    if "assigned_user_id" in fields:
        assignee = _get_assignee(db, payload.assigned_user_id)
        row.assigned_user_id = assignee.id if assignee else None
    old_status = row.status
    _apply_payload(row, payload)
    row.updated_at = datetime.utcnow()
    if payload.status is not None and payload.status != old_status:
        _add_event(db, row, f"status:{payload.status.value}", user, f"Status changed to {_status_label(payload.status)}.")
    else:
        _add_event(db, row, "updated", user, "Lease renewal details updated.")
    db.commit()
    return _record_to_dict(_get_record(db, mailbox, record_id), include_detail=True)


@router.post("/records/{record_id}/status")
def update_status(
    record_id: int,
    payload: LeaseRenewalStatusIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _get_record(db, mailbox, record_id)
    row.status = payload.status
    if payload.status in FINAL_STATUSES and not row.completed_at:
        row.completed_at = datetime.utcnow()
    if payload.status not in FINAL_STATUSES:
        row.completed_at = None
    row.updated_at = datetime.utcnow()
    _add_event(db, row, f"status:{payload.status.value}", user, _clean(payload.note) or f"Status changed to {_status_label(payload.status)}.")
    db.commit()
    return _record_to_dict(_get_record(db, mailbox, record_id), include_detail=True)


@router.post("/records/{record_id}/notes")
def add_note(
    record_id: int,
    payload: LeaseRenewalNoteIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    note = _clean(payload.note)
    if not note:
        raise HTTPException(status_code=400, detail="Note cannot be empty.")
    row = _get_record(db, mailbox, record_id)
    row.updated_at = datetime.utcnow()
    _add_event(db, row, "note", user, note)
    db.commit()
    return _record_to_dict(_get_record(db, mailbox, record_id), include_detail=True)


@router.delete("/records/{record_id}")
def delete_record(
    record_id: int,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = _get_record(db, mailbox, record_id)
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted_id": record_id}
