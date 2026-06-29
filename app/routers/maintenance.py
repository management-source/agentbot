from __future__ import annotations

from datetime import datetime
import json
import mimetypes
from pathlib import Path
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.authz import require_page_access
from app.config import settings
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import (
    MaintenanceAttachment,
    MaintenanceEvent,
    MaintenanceOrder,
    MaintenanceOrderStatus,
    MaintenanceTradie,
    ManagedProperty,
    User,
)


router = APIRouter()
MAX_MAINTENANCE_ATTACHMENT_BYTES = settings.TENANT_UPLOAD_MAX_BYTES
OPEN_STATUSES = {
    MaintenanceOrderStatus.NEW,
    MaintenanceOrderStatus.WAITING_OWNER_APPROVAL,
    MaintenanceOrderStatus.OWNER_APPROVED,
    MaintenanceOrderStatus.OWNER_DECLINED,
    MaintenanceOrderStatus.OWNER_ARRANGING,
    MaintenanceOrderStatus.QUOTE_REQUESTED,
    MaintenanceOrderStatus.QUOTE_RECEIVED,
    MaintenanceOrderStatus.TRADIE_ARRANGED,
    MaintenanceOrderStatus.TENANT_NOTIFIED,
}


class MaintenanceOrderCreateIn(BaseModel):
    property_id: int | None = None
    property_address: str | None = None
    suburb: str | None = None
    state_code: str | None = None
    postcode: str | None = None
    title: str
    category: str | None = None
    priority: str = "normal"
    description: str
    access_notes: str | None = None
    owner_name: str | None = None
    owner_email: str | None = None
    owner_phone: str | None = None
    tenant_name: str | None = None
    tenant_email: str | None = None
    tenant_phone: str | None = None
    due_by: datetime | None = None
    assignee_user_id: int | None = None
    tradie_name: str | None = None
    tradie_company: str | None = None
    tradie_email: str | None = None
    tradie_phone: str | None = None
    tradie_scheduled_for: datetime | None = None
    quoted_amount: float | None = None
    quote_notes: str | None = None


class MaintenanceOrderUpdateIn(BaseModel):
    property_id: int | None = None
    property_address: str | None = None
    suburb: str | None = None
    state_code: str | None = None
    postcode: str | None = None
    title: str | None = None
    category: str | None = None
    priority: str | None = None
    description: str | None = None
    access_notes: str | None = None
    owner_name: str | None = None
    owner_email: str | None = None
    owner_phone: str | None = None
    tenant_name: str | None = None
    tenant_email: str | None = None
    tenant_phone: str | None = None
    due_by: datetime | None = None
    assignee_user_id: int | None = None
    tradie_name: str | None = None
    tradie_company: str | None = None
    tradie_email: str | None = None
    tradie_phone: str | None = None
    tradie_scheduled_for: datetime | None = None
    quoted_amount: float | None = None
    quote_notes: str | None = None
    owner_decision_notes: str | None = None
    completion_notes: str | None = None


class MaintenanceStatusIn(BaseModel):
    status: MaintenanceOrderStatus
    note: str | None = None


class MaintenanceEmailIn(BaseModel):
    body_text: str | None = None
    cc: str | None = None
    bcc: str | None = None


class MaintenanceNoteIn(BaseModel):
    note: str


class MaintenanceTradieIn(BaseModel):
    company: str
    contact_name: str | None = None
    trade_type: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    is_active: bool = True


class MaintenanceTradieUpdateIn(BaseModel):
    company: str | None = None
    contact_name: str | None = None
    trade_type: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    is_active: bool | None = None


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _safe_filename(value: str | None) -> str:
    name = (value or "maintenance-attachment").replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[\r\n\t\x00-\x1f\x7f]+", "", name).replace('"', "")
    return name[:180] or "maintenance-attachment"


def _tenant_upload_root() -> Path:
    root = Path(settings.TENANT_UPLOAD_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _maintenance_upload_path(order_id: int, filename: str) -> tuple[Path, str]:
    safe_name = _safe_filename(filename)
    relative = Path("staff_uploads") / f"order_{order_id}" / f"{uuid.uuid4().hex}_{safe_name}"
    root = _tenant_upload_root()
    target = (root / relative).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid upload path.")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target, relative.as_posix()


def _upload_content_type(file: UploadFile) -> str:
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if not content_type or content_type == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(file.filename or "")
        content_type = (guessed or content_type or "application/octet-stream").lower()
    return content_type


def _is_media_upload(content_type: str) -> bool:
    return content_type.startswith("image/") or content_type.startswith("video/")


def _attachment_disk_path(row: MaintenanceAttachment) -> Path | None:
    if not row.storage_path:
        return None
    root = _tenant_upload_root()
    candidate = Path(row.storage_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if root != resolved and root not in resolved.parents:
        raise HTTPException(status_code=400, detail="Attachment storage path is invalid.")
    return resolved


def _attachment_content(row: MaintenanceAttachment) -> bytes:
    disk_path = _attachment_disk_path(row)
    if not disk_path:
        return row.content_bytes or b""
    if not disk_path.exists() or not disk_path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file is missing from disk.")
    try:
        return disk_path.read_bytes()
    except OSError:
        raise HTTPException(status_code=503, detail="Attachment storage is unavailable.")


def _delete_attachment_file(row: MaintenanceAttachment) -> None:
    disk_path = _attachment_disk_path(row)
    if not disk_path:
        return
    try:
        if disk_path.exists() and disk_path.is_file():
            disk_path.unlink()
    except OSError:
        raise HTTPException(status_code=503, detail="Could not remove attachment from disk.")


def _fields_set(payload: BaseModel) -> set[str]:
    return set(getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", set())))


def _status_value(value: MaintenanceOrderStatus | str | None) -> str:
    if hasattr(value, "value"):
        return value.value
    return str(value or "")


def _status_label(value: MaintenanceOrderStatus | str | None) -> str:
    return _status_value(value).replace("_", " ").title()


def _property_label(row: MaintenanceOrder) -> str:
    tail = " ".join([x for x in [row.suburb, row.state_code, row.postcode] if x])
    return ", ".join([x for x in [row.property_address, tail] if x])


def _get_property(db: Session, mailbox: str, property_id: int | None) -> ManagedProperty | None:
    if not property_id:
        return None
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


def _property_primary_contact(prop: ManagedProperty | None, attr: str) -> dict[str, str]:
    if not prop:
        return {"name": "", "email": "", "phone": ""}
    raw = getattr(prop, attr, None)
    if not raw:
        return {"name": "", "email": "", "phone": ""}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {"name": "", "email": "", "phone": ""}
    contacts = parsed.get("contacts") if isinstance(parsed, dict) else []
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


def _apply_property(row: MaintenanceOrder, prop: ManagedProperty | None, payload) -> None:
    if prop:
        row.property_id = prop.id
        row.property_address = prop.property_address
        row.suburb = prop.suburb
        row.state_code = prop.state_code
        row.postcode = prop.postcode
        owner_contact = _property_primary_contact(prop, "owners_json")
        tenant_contact = _property_primary_contact(prop, "tenants_json")
        row.owner_name = row.owner_name or owner_contact["name"] or None
        row.owner_email = row.owner_email or owner_contact["email"] or None
        row.owner_phone = row.owner_phone or owner_contact["phone"] or None
        row.tenant_name = row.tenant_name or tenant_contact["name"] or None
        row.tenant_email = row.tenant_email or tenant_contact["email"] or None
        row.tenant_phone = row.tenant_phone or tenant_contact["phone"] or None
        return

    address = _clean(getattr(payload, "property_address", None))
    if address:
        row.property_address = address
    row.suburb = _clean(getattr(payload, "suburb", None))
    row.state_code = (_clean(getattr(payload, "state_code", None)) or "VIC").upper()
    row.postcode = _clean(getattr(payload, "postcode", None))


def _get_assignee(db: Session, assignee_user_id: int | None) -> User | None:
    if assignee_user_id is None:
        return None
    user = db.get(User, assignee_user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="Assignee must be an active staff account.")
    return user


def _get_order(db: Session, mailbox: str, order_id: int) -> MaintenanceOrder:
    row = (
        db.query(MaintenanceOrder)
        .options(
            selectinload(MaintenanceOrder.attachments),
            selectinload(MaintenanceOrder.events).selectinload(MaintenanceEvent.actor),
            selectinload(MaintenanceOrder.assignee),
        )
        .filter(MaintenanceOrder.mailbox == mailbox)
        .filter(MaintenanceOrder.id == order_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Maintenance order not found.")
    return row


def _add_event(
    db: Session,
    row: MaintenanceOrder,
    event_type: str,
    user: User | None = None,
    detail: str | None = None,
) -> None:
    db.add(
        MaintenanceEvent(
            mailbox=row.mailbox,
            order_id=row.id,
            actor_user_id=user.id if user else None,
            event_type=event_type,
            detail=_clean(detail),
            created_at=datetime.utcnow(),
        )
    )


def _attachment_to_dict(row: MaintenanceAttachment) -> dict:
    size = row.file_size if row.file_size is not None else len(row.content_bytes or b"")
    return {
        "id": row.id,
        "kind": row.kind,
        "filename": row.filename,
        "content_type": row.content_type,
        "notes": row.notes,
        "created_at": row.created_at,
        "uploaded_by_user_id": row.uploaded_by_user_id,
        "uploaded_by_tenant_id": row.uploaded_by_tenant_id,
        "storage": "disk" if row.storage_path else "database",
        "source": "tenant" if row.uploaded_by_tenant_id else "staff",
        "size": size,
    }


def _event_to_dict(row: MaintenanceEvent) -> dict:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "detail": row.detail,
        "actor_name": row.actor.name if row.actor else None,
        "created_at": row.created_at,
    }


def _tradie_to_dict(row: MaintenanceTradie) -> dict:
    return {
        "id": row.id,
        "company": row.company,
        "contact_name": row.contact_name,
        "trade_type": row.trade_type,
        "email": row.email,
        "phone": row.phone,
        "notes": row.notes,
        "is_active": row.is_active,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "label": " - ".join([x for x in [row.company, row.contact_name, row.trade_type] if x]),
    }


def _order_to_dict(row: MaintenanceOrder, include_detail: bool = False) -> dict:
    attachments = sorted(row.attachments or [], key=lambda x: x.created_at, reverse=True)
    events = sorted(row.events or [], key=lambda x: x.created_at, reverse=True)
    status = _status_value(row.status)
    return {
        "id": row.id,
        "mailbox": row.mailbox,
        "property_id": row.property_id,
        "property_address": row.property_address,
        "suburb": row.suburb,
        "state_code": row.state_code,
        "postcode": row.postcode,
        "property_label": _property_label(row),
        "title": row.title,
        "category": row.category,
        "priority": row.priority,
        "description": row.description,
        "access_notes": row.access_notes,
        "owner_name": row.owner_name,
        "owner_email": row.owner_email,
        "owner_phone": row.owner_phone,
        "tenant_name": row.tenant_name,
        "tenant_email": row.tenant_email,
        "tenant_phone": row.tenant_phone,
        "status": status,
        "status_label": _status_label(row.status),
        "assignee_user_id": row.assignee_user_id,
        "assignee_name": row.assignee.name if row.assignee else None,
        "due_by": row.due_by,
        "owner_sent_at": row.owner_sent_at,
        "owner_decided_at": row.owner_decided_at,
        "owner_decision_notes": row.owner_decision_notes,
        "tradie_name": row.tradie_name,
        "tradie_company": row.tradie_company,
        "tradie_email": row.tradie_email,
        "tradie_phone": row.tradie_phone,
        "tradie_scheduled_for": row.tradie_scheduled_for,
        "tradie_arranged_at": row.tradie_arranged_at,
        "quoted_amount": row.quoted_amount,
        "quote_notes": row.quote_notes,
        "quote_received_at": row.quote_received_at,
        "tenant_notified_at": row.tenant_notified_at,
        "completed_at": row.completed_at,
        "completion_notes": row.completion_notes,
        "source": row.source or "staff",
        "tenant_account_id": row.tenant_account_id,
        "tenant_submitted_at": row.tenant_submitted_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "attachment_count": len(attachments),
        "quote_count": len([a for a in attachments if str(a.kind or "").upper() == "QUOTE"]),
        "attachments": [_attachment_to_dict(a) for a in attachments] if include_detail else [],
        "events": [_event_to_dict(e) for e in events] if include_detail else [],
    }


def _owner_email_body(row: MaintenanceOrder) -> str:
    lines = [
        f"Hi {row.owner_name or 'there'},",
        "",
        "We are writing regarding a maintenance request for your property.",
        "",
        f"Property: {_property_label(row)}",
        f"Issue: {row.title}",
        f"Category: {row.category or '-'}",
        f"Priority: {row.priority or 'normal'}",
        "",
        "Description:",
        row.description or "-",
    ]
    if row.access_notes:
        lines.extend(["", "Access / tenant notes:", row.access_notes])
    if row.tenant_name or row.tenant_phone or row.tenant_email:
        lines.extend(
            [
                "",
                "Tenant contact:",
                f"{row.tenant_name or '-'}",
                f"{row.tenant_phone or '-'}",
                f"{row.tenant_email or '-'}",
            ]
        )
    lines.extend(
        [
            "",
            "Please confirm whether you approve us to proceed with quotes/repairs, or if you would prefer to arrange the repair yourself.",
            "",
            "Kind regards,",
            "Dons Premier Estate Agents",
        ]
    )
    return "\n".join(lines)


def _tenant_email_body(row: MaintenanceOrder) -> str:
    tradie = row.tradie_company or row.tradie_name or "our arranged tradesperson"
    schedule = row.tradie_scheduled_for.strftime("%d/%m/%Y %I:%M %p") if row.tradie_scheduled_for else "to be confirmed"
    lines = [
        f"Hi {row.tenant_name or 'there'},",
        "",
        "We are writing about your maintenance request.",
        "",
        f"Property: {_property_label(row)}",
        f"Issue: {row.title}",
        f"Tradesperson: {tradie}",
        f"Scheduled attendance: {schedule}",
    ]
    if row.tradie_phone or row.tradie_email:
        lines.extend(["", "Tradesperson contact:", row.tradie_phone or "-", row.tradie_email or "-"])
    if row.access_notes:
        lines.extend(["", "Access notes:", row.access_notes])
    lines.extend(
        [
            "",
            "Please let us know if the proposed arrangement is not suitable.",
            "",
            "Kind regards,",
            "Dons Premier Estate Agents",
        ]
    )
    return "\n".join(lines)


def _tradie_email_body(row: MaintenanceOrder) -> str:
    schedule = row.tradie_scheduled_for.strftime("%d/%m/%Y %I:%M %p") if row.tradie_scheduled_for else "to be arranged"
    lines = [
        f"Hi {row.tradie_name or row.tradie_company or 'there'},",
        "",
        "Please see the maintenance work order details below.",
        "",
        f"Property: {_property_label(row)}",
        f"Issue: {row.title}",
        f"Category: {row.category or '-'}",
        f"Priority: {row.priority or 'normal'}",
        f"Preferred / scheduled attendance: {schedule}",
        "",
        "Description:",
        row.description or "-",
    ]
    if row.access_notes:
        lines.extend(["", "Access / tenant notes:", row.access_notes])
    if row.tenant_name or row.tenant_phone or row.tenant_email:
        lines.extend(
            [
                "",
                "Tenant contact:",
                f"{row.tenant_name or '-'}",
                f"{row.tenant_phone or '-'}",
                f"{row.tenant_email or '-'}",
            ]
        )
    lines.extend(
        [
            "",
            "Please confirm availability and advise if a quote is required before proceeding.",
            "",
            "Kind regards,",
            "Dons Premier Estate Agents",
        ]
    )
    return "\n".join(lines)


def _email_draft(row: MaintenanceOrder, kind: str, body_text: str | None = None) -> dict[str, object]:
    draft_kind = (kind or "").strip().lower()
    if draft_kind in {"owner", "landlord", "owner_approval"}:
        recipient = row.owner_email
        subject = f"Maintenance request approval - {row.title} - {row.property_address}"
        body = _clean(body_text) or _owner_email_body(row)
        next_status = MaintenanceOrderStatus.WAITING_OWNER_APPROVAL
        label = "Owner Approval"
    elif draft_kind in {"tenant", "tenant_arrangement"}:
        recipient = row.tenant_email
        subject = f"Maintenance update - {row.title} - {row.property_address}"
        body = _clean(body_text) or _tenant_email_body(row)
        next_status = MaintenanceOrderStatus.TENANT_NOTIFIED
        label = "Tenant Arrangement"
    elif draft_kind in {"tradie", "tradesperson", "work_order"}:
        recipient = row.tradie_email
        subject = f"Maintenance work order - {row.title} - {row.property_address}"
        body = _clean(body_text) or _tradie_email_body(row)
        next_status = MaintenanceOrderStatus.TRADIE_ARRANGED
        label = "Tradie Work Order"
    else:
        raise HTTPException(status_code=400, detail="Invalid maintenance email draft type.")

    return {
        "kind": draft_kind,
        "label": label,
        "to_email": recipient or "",
        "subject": subject,
        "body_text": body,
        "copy_text": "\n".join(
            [
                f"To: {recipient or ''}",
                f"Subject: {subject}",
                "",
                body,
            ]
        ),
        "next_status": next_status.value,
        "next_status_label": _status_label(next_status),
        "sends_email": False,
    }


def _apply_update_fields(row: MaintenanceOrder, payload: MaintenanceOrderUpdateIn) -> None:
    fields = _fields_set(payload)
    simple_fields = [
        "title",
        "category",
        "priority",
        "description",
        "access_notes",
        "owner_name",
        "owner_email",
        "owner_phone",
        "tenant_name",
        "tenant_email",
        "tenant_phone",
        "due_by",
        "tradie_name",
        "tradie_company",
        "tradie_email",
        "tradie_phone",
        "tradie_scheduled_for",
        "quoted_amount",
        "quote_notes",
        "owner_decision_notes",
        "completion_notes",
    ]
    for field in simple_fields:
        if field not in fields:
            continue
        value = getattr(payload, field)
        if isinstance(value, str):
            value = _clean(value)
        setattr(row, field, value)


@router.get("/tradies")
def list_maintenance_tradies(
    query: str | None = None,
    active: str | None = "active",
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("maintenance")),
):
    q = db.query(MaintenanceTradie).filter(MaintenanceTradie.mailbox == mailbox)
    key = (active or "").strip().lower()
    if key in {"active", "true", "1", ""}:
        q = q.filter(MaintenanceTradie.is_active == True)
    elif key in {"inactive", "false", "0"}:
        q = q.filter(MaintenanceTradie.is_active == False)
    elif key not in {"all"}:
        raise HTTPException(status_code=400, detail="Invalid tradie active filter.")
    if query and query.strip():
        like = f"%{query.strip()}%"
        q = q.filter(
            or_(
                MaintenanceTradie.company.ilike(like),
                MaintenanceTradie.contact_name.ilike(like),
                MaintenanceTradie.trade_type.ilike(like),
                MaintenanceTradie.email.ilike(like),
                MaintenanceTradie.phone.ilike(like),
            )
        )
    rows = q.order_by(MaintenanceTradie.is_active.desc(), MaintenanceTradie.company.asc()).limit(500).all()
    return {"items": [_tradie_to_dict(row) for row in rows]}


@router.post("/tradies")
def create_maintenance_tradie(
    payload: MaintenanceTradieIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("maintenance")),
):
    company = _clean(payload.company)
    if not company:
        raise HTTPException(status_code=400, detail="Tradie company/name is required.")
    now = datetime.utcnow()
    row = MaintenanceTradie(
        mailbox=mailbox,
        company=company,
        contact_name=_clean(payload.contact_name),
        trade_type=_clean(payload.trade_type),
        email=_clean(payload.email),
        phone=_clean(payload.phone),
        notes=_clean(payload.notes),
        is_active=bool(payload.is_active),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _tradie_to_dict(row)


@router.patch("/tradies/{tradie_id}")
def update_maintenance_tradie(
    tradie_id: int,
    payload: MaintenanceTradieUpdateIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("maintenance")),
):
    row = (
        db.query(MaintenanceTradie)
        .filter(MaintenanceTradie.mailbox == mailbox)
        .filter(MaintenanceTradie.id == tradie_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Tradie not found.")
    fields = _fields_set(payload)
    for field in ["company", "contact_name", "trade_type", "email", "phone", "notes"]:
        if field in fields:
            value = _clean(getattr(payload, field))
            if field == "company" and not value:
                raise HTTPException(status_code=400, detail="Tradie company/name is required.")
            setattr(row, field, value)
    if "is_active" in fields and payload.is_active is not None:
        row.is_active = bool(payload.is_active)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _tradie_to_dict(row)


@router.get("/summary")
def maintenance_summary(
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("maintenance")),
):
    base = db.query(MaintenanceOrder).filter(MaintenanceOrder.mailbox == mailbox)
    counts = {
        status.value if hasattr(status, "value") else str(status): count
        for status, count in base.with_entities(MaintenanceOrder.status, func.count(MaintenanceOrder.id))
        .group_by(MaintenanceOrder.status)
        .all()
    }
    return {
        "total": base.count(),
        "open": base.filter(MaintenanceOrder.status.in_(list(OPEN_STATUSES))).count(),
        "waiting_owner": counts.get(MaintenanceOrderStatus.WAITING_OWNER_APPROVAL.value, 0),
        "quotes": counts.get(MaintenanceOrderStatus.QUOTE_REQUESTED.value, 0)
        + counts.get(MaintenanceOrderStatus.QUOTE_RECEIVED.value, 0),
        "scheduled": counts.get(MaintenanceOrderStatus.TRADIE_ARRANGED.value, 0)
        + counts.get(MaintenanceOrderStatus.TENANT_NOTIFIED.value, 0),
        "completed": counts.get(MaintenanceOrderStatus.COMPLETED.value, 0),
        "counts": counts,
    }


@router.get("/orders")
def list_maintenance_orders(
    status: str | None = None,
    query: str | None = None,
    page: int = 1,
    page_size: int = 25,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("maintenance")),
):
    q = (
        db.query(MaintenanceOrder)
        .options(selectinload(MaintenanceOrder.assignee), selectinload(MaintenanceOrder.attachments))
        .filter(MaintenanceOrder.mailbox == mailbox)
    )
    if status and status.upper() not in {"ALL", ""}:
        if status.upper() == "OPEN":
            q = q.filter(MaintenanceOrder.status.in_(list(OPEN_STATUSES)))
        else:
            try:
                wanted_status = MaintenanceOrderStatus(status.upper())
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid maintenance status filter.")
            q = q.filter(MaintenanceOrder.status == wanted_status)
    if query and query.strip():
        like = f"%{query.strip()}%"
        q = q.filter(
            or_(
                MaintenanceOrder.title.ilike(like),
                MaintenanceOrder.description.ilike(like),
                MaintenanceOrder.property_address.ilike(like),
                MaintenanceOrder.owner_name.ilike(like),
                MaintenanceOrder.tenant_name.ilike(like),
                MaintenanceOrder.tradie_company.ilike(like),
                MaintenanceOrder.source.ilike(like),
            )
        )
    page = max(int(page or 1), 1)
    page_size = max(10, min(int(page_size or 25), 100))
    total = q.with_entities(func.count(MaintenanceOrder.id)).scalar() or 0
    rows = (
        q.order_by(MaintenanceOrder.updated_at.desc(), MaintenanceOrder.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_order_to_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (page * page_size) < total,
    }


@router.post("/orders")
def create_maintenance_order(
    payload: MaintenanceOrderCreateIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("maintenance")),
):
    title = _clean(payload.title)
    description = _clean(payload.description)
    if not title:
        raise HTTPException(status_code=400, detail="Maintenance title is required.")
    if not description:
        raise HTTPException(status_code=400, detail="Description is required.")
    prop = _get_property(db, mailbox, payload.property_id)
    if not prop and not _clean(payload.property_address):
        raise HTTPException(status_code=400, detail="Property is required.")
    assignee = _get_assignee(db, payload.assignee_user_id)
    owner_contact = _property_primary_contact(prop, "owners_json")
    tenant_contact = _property_primary_contact(prop, "tenants_json")
    now = datetime.utcnow()
    row = MaintenanceOrder(
        mailbox=mailbox,
        property_id=prop.id if prop else None,
        property_address=prop.property_address if prop else _clean(payload.property_address) or "",
        suburb=prop.suburb if prop else _clean(payload.suburb),
        state_code=prop.state_code if prop else (_clean(payload.state_code) or "VIC"),
        postcode=prop.postcode if prop else _clean(payload.postcode),
        title=title,
        category=_clean(payload.category),
        priority=_clean(payload.priority) or "normal",
        description=description,
        access_notes=_clean(payload.access_notes),
        owner_name=_clean(payload.owner_name) or owner_contact["name"] or None,
        owner_email=_clean(payload.owner_email) or owner_contact["email"] or None,
        owner_phone=_clean(payload.owner_phone) or owner_contact["phone"] or None,
        tenant_name=_clean(payload.tenant_name) or tenant_contact["name"] or None,
        tenant_email=_clean(payload.tenant_email) or tenant_contact["email"] or None,
        tenant_phone=_clean(payload.tenant_phone) or tenant_contact["phone"] or None,
        due_by=payload.due_by,
        assignee_user_id=assignee.id if assignee else None,
        created_by_user_id=user.id,
        tradie_name=_clean(payload.tradie_name),
        tradie_company=_clean(payload.tradie_company),
        tradie_email=_clean(payload.tradie_email),
        tradie_phone=_clean(payload.tradie_phone),
        tradie_scheduled_for=payload.tradie_scheduled_for,
        quoted_amount=payload.quoted_amount,
        quote_notes=_clean(payload.quote_notes),
        source="staff",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    _add_event(db, row, "created", user, "Maintenance order created.")
    db.commit()
    db.refresh(row)
    return _order_to_dict(_get_order(db, mailbox, row.id), include_detail=True)


@router.get("/orders/{order_id}")
def get_maintenance_order(
    order_id: int,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("maintenance")),
):
    return _order_to_dict(_get_order(db, mailbox, order_id), include_detail=True)


@router.get("/orders/{order_id}/email-draft/{kind}")
def get_maintenance_email_draft(
    order_id: int,
    kind: str,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("maintenance")),
):
    row = _get_order(db, mailbox, order_id)
    return _email_draft(row, kind)


@router.patch("/orders/{order_id}")
def update_maintenance_order(
    order_id: int,
    payload: MaintenanceOrderUpdateIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("maintenance")),
):
    row = _get_order(db, mailbox, order_id)
    prop = _get_property(db, mailbox, payload.property_id) if "property_id" in _fields_set(payload) else None
    if "property_id" in _fields_set(payload) or "property_address" in _fields_set(payload):
        _apply_property(row, prop, payload)
    if "assignee_user_id" in _fields_set(payload):
        assignee = _get_assignee(db, payload.assignee_user_id)
        row.assignee_user_id = assignee.id if assignee else None
    _apply_update_fields(row, payload)
    row.updated_at = datetime.utcnow()
    _add_event(db, row, "updated", user, "Maintenance order details updated.")
    db.commit()
    return _order_to_dict(_get_order(db, mailbox, order_id), include_detail=True)


@router.delete("/orders/{order_id}")
def delete_maintenance_order(
    order_id: int,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("maintenance")),
):
    row = _get_order(db, mailbox, order_id)
    for attachment in list(row.attachments or []):
        _delete_attachment_file(attachment)
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted_id": order_id}


@router.post("/orders/{order_id}/status")
def update_maintenance_status(
    order_id: int,
    payload: MaintenanceStatusIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("maintenance")),
):
    row = _get_order(db, mailbox, order_id)
    now = datetime.utcnow()
    row.status = payload.status
    row.updated_at = now
    if payload.status == MaintenanceOrderStatus.WAITING_OWNER_APPROVAL:
        row.owner_sent_at = now
    if payload.status in {
        MaintenanceOrderStatus.OWNER_APPROVED,
        MaintenanceOrderStatus.OWNER_DECLINED,
        MaintenanceOrderStatus.OWNER_ARRANGING,
    }:
        row.owner_decided_at = now
        row.owner_decision_notes = _clean(payload.note) or row.owner_decision_notes
    if payload.status == MaintenanceOrderStatus.QUOTE_RECEIVED and not row.quote_received_at:
        row.quote_received_at = now
    if payload.status == MaintenanceOrderStatus.TRADIE_ARRANGED:
        row.tradie_arranged_at = now
    if payload.status == MaintenanceOrderStatus.TENANT_NOTIFIED:
        row.tenant_notified_at = now
    if payload.status == MaintenanceOrderStatus.COMPLETED:
        row.completed_at = now
        row.completion_notes = _clean(payload.note) or row.completion_notes
    elif payload.status != MaintenanceOrderStatus.COMPLETED:
        row.completed_at = None
    _add_event(db, row, f"status:{payload.status.value}", user, _clean(payload.note) or f"Status changed to {_status_label(payload.status)}.")
    db.commit()
    return _order_to_dict(_get_order(db, mailbox, order_id), include_detail=True)


@router.post("/orders/{order_id}/send-owner-email")
def send_owner_email(
    order_id: int,
    payload: MaintenanceEmailIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("maintenance")),
):
    # Legacy route name kept for older clients. It intentionally returns a draft
    # only; Maintenance no longer sends emails directly.
    del user
    row = _get_order(db, mailbox, order_id)
    draft = _email_draft(row, "owner", payload.body_text)
    draft["cc"] = payload.cc or ""
    draft["bcc"] = payload.bcc or ""
    return draft


@router.post("/orders/{order_id}/send-tenant-email")
def send_tenant_email(
    order_id: int,
    payload: MaintenanceEmailIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("maintenance")),
):
    # Legacy route name kept for older clients. It intentionally returns a draft
    # only; Maintenance no longer sends emails directly.
    del user
    row = _get_order(db, mailbox, order_id)
    draft = _email_draft(row, "tenant", payload.body_text)
    draft["cc"] = payload.cc or ""
    draft["bcc"] = payload.bcc or ""
    return draft


@router.post("/orders/{order_id}/send-tradie-email")
def send_tradie_email(
    order_id: int,
    payload: MaintenanceEmailIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("maintenance")),
):
    # Legacy route name kept for older clients. It intentionally returns a draft
    # only; Maintenance no longer sends emails directly.
    del user
    row = _get_order(db, mailbox, order_id)
    draft = _email_draft(row, "tradie", payload.body_text)
    draft["cc"] = payload.cc or ""
    draft["bcc"] = payload.bcc or ""
    return draft


@router.post("/orders/{order_id}/attachments")
def upload_maintenance_attachment(
    order_id: int,
    kind: str = Form(default="GENERAL"),
    notes: str | None = Form(default=None),
    quoted_amount: float | None = Form(default=None),
    quote_notes: str | None = Form(default=None),
    file: UploadFile = File(...),
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("maintenance")),
):
    row = _get_order(db, mailbox, order_id)
    raw = file.file.read(MAX_MAINTENANCE_ATTACHMENT_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(raw) > MAX_MAINTENANCE_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="Maintenance attachment exceeds 25MB.")
    clean_kind = (_clean(kind) or "GENERAL").upper()
    content_type = _upload_content_type(file)
    store_on_disk = clean_kind == "MEDIA"
    if store_on_disk and not _is_media_upload(content_type):
        raise HTTPException(status_code=400, detail="Only image and video files are allowed in the media upload section.")
    storage_path = None
    if store_on_disk:
        target, storage_path = _maintenance_upload_path(row.id, file.filename or "maintenance-media")
        try:
            target.write_bytes(raw)
        except OSError:
            raise HTTPException(status_code=503, detail="Upload storage is unavailable. Please try again later.")
    attachment = MaintenanceAttachment(
        mailbox=mailbox,
        order_id=row.id,
        kind=clean_kind,
        filename=_safe_filename(file.filename),
        content_type=content_type,
        content_bytes=b"" if store_on_disk else raw,
        storage_path=storage_path,
        file_size=len(raw),
        notes=_clean(notes),
        uploaded_by_user_id=user.id,
        created_at=datetime.utcnow(),
    )
    db.add(attachment)
    now = datetime.utcnow()
    if clean_kind == "QUOTE":
        row.status = MaintenanceOrderStatus.QUOTE_RECEIVED
        row.quote_received_at = row.quote_received_at or now
        if quoted_amount is not None:
            row.quoted_amount = quoted_amount
        if quote_notes is not None:
            row.quote_notes = _clean(quote_notes)
    row.updated_at = now
    _add_event(db, row, "attachment_uploaded", user, f"{clean_kind.title()} uploaded: {attachment.filename}")
    try:
        db.commit()
    except Exception:
        db.rollback()
        if storage_path:
            try:
                _delete_attachment_file(attachment)
            except HTTPException:
                pass
        raise
    return _order_to_dict(_get_order(db, mailbox, order_id), include_detail=True)


@router.get("/attachments/{attachment_id}/view")
def view_maintenance_attachment(
    attachment_id: int,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("maintenance")),
):
    row = (
        db.query(MaintenanceAttachment)
        .filter(MaintenanceAttachment.mailbox == mailbox)
        .filter(MaintenanceAttachment.id == attachment_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    filename = _safe_filename(row.filename)
    return Response(
        content=_attachment_content(row),
        media_type=row.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.delete("/attachments/{attachment_id}")
def delete_maintenance_attachment(
    attachment_id: int,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("maintenance")),
):
    row = (
        db.query(MaintenanceAttachment)
        .filter(MaintenanceAttachment.mailbox == mailbox)
        .filter(MaintenanceAttachment.id == attachment_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    order = _get_order(db, mailbox, row.order_id)
    filename = row.filename
    _delete_attachment_file(row)
    db.delete(row)
    order.updated_at = datetime.utcnow()
    _add_event(db, order, "attachment_deleted", user, f"Attachment deleted: {filename}")
    db.commit()
    return _order_to_dict(_get_order(db, mailbox, order.id), include_detail=True)


@router.post("/orders/{order_id}/notes")
def add_maintenance_note(
    order_id: int,
    payload: MaintenanceNoteIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("maintenance")),
):
    note = _clean(payload.note)
    if not note:
        raise HTTPException(status_code=400, detail="Note cannot be empty.")
    row = _get_order(db, mailbox, order_id)
    row.updated_at = datetime.utcnow()
    _add_event(db, row, "note", user, note)
    db.commit()
    return _order_to_dict(_get_order(db, mailbox, order_id), include_detail=True)
