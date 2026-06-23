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
    ComplianceRecord,
    ComplianceRecordStatus,
    ComplianceType,
    ManagedProperty,
    User,
)

router = APIRouter()
DUE_SOON_DAYS = 30


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


def _record_state(row: ComplianceRecord) -> str:
    if row.status == ComplianceRecordStatus.COMPLETED:
        return "CURRENT"
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
    return "OPEN"


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


@router.post("/records")
def create_record(
    payload: ComplianceRecordCreateIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    _get_property(db, mailbox, payload.property_id)
    now = datetime.utcnow()
    row = ComplianceRecord(
        mailbox=mailbox,
        property_id=payload.property_id,
        compliance_type=payload.compliance_type,
        status=payload.status,
        due_date=payload.due_date,
        completed_at=payload.completed_at,
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

    if payload.status is not None:
        row.status = payload.status
        if payload.status == ComplianceRecordStatus.COMPLETED and not row.completed_at:
            row.completed_at = datetime.utcnow()
        elif payload.status != ComplianceRecordStatus.COMPLETED:
            row.completed_at = None
    if payload.due_date is not None:
        row.due_date = payload.due_date
    if payload.completed_at is not None:
        row.completed_at = payload.completed_at
    if payload.provider_name is not None:
        row.provider_name = payload.provider_name.strip() or None
    if payload.result_text is not None:
        row.result_text = payload.result_text.strip() or None
    if payload.notes is not None:
        row.notes = payload.notes.strip() or None

    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _record_to_dict(row)


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
