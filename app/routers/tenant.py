from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.models import (
    MaintenanceEvent,
    MaintenanceOrder,
    MaintenanceOrderStatus,
    ManagedProperty,
    TenantAccount,
)
from app.security import create_access_token, decode_access_token, hash_password, verify_password

router = APIRouter(prefix="/tenant/api", tags=["tenant"])

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


class TenantRegisterIn(BaseModel):
    name: str
    email: EmailStr
    phone: str | None = None
    password: str
    property_address: str
    suburb: str | None = None
    postcode: str | None = None
    recaptcha_token: str | None = None


class TenantLoginIn(BaseModel):
    email: EmailStr
    password: str
    recaptcha_token: str | None = None


class TenantMaintenanceIn(BaseModel):
    title: str
    category: str | None = None
    priority: str = "normal"
    description: str
    access_notes: str | None = None
    preferred_contact: str | None = None


def _clean(value: str | None, max_len: int | None = None) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if max_len:
        text = text[:max_len]
    return text or None


def _normalize_key(value: str | None) -> str:
    text = _clean(value) or ""
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _default_mailbox() -> str:
    mailboxes = settings.monitored_mailboxes_list()
    if mailboxes:
        return mailboxes[0].strip().lower()
    my_emails = settings.my_emails_list()
    if my_emails:
        return my_emails[0].strip().lower()
    return "admin@donspremier.com.au"


def _validate_password_strength(password: str) -> None:
    p = (password or "").strip()
    if len(p) < 12:
        raise HTTPException(status_code=400, detail="Password must be at least 12 characters.")
    if not re.search(r"[a-z]", p):
        raise HTTPException(status_code=400, detail="Password must include a lowercase letter.")
    if not re.search(r"[A-Z]", p):
        raise HTTPException(status_code=400, detail="Password must include an uppercase letter.")
    if not re.search(r"\d", p):
        raise HTTPException(status_code=400, detail="Password must include a number.")
    if not re.search(r"[^A-Za-z0-9]", p):
        raise HTTPException(status_code=400, detail="Password must include a symbol.")


def _verify_recaptcha(token: str | None, remote_ip: str | None) -> None:
    if not settings.recaptcha_enabled():
        return
    value = (token or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Please complete the reCAPTCHA check.")
    try:
        resp = httpx.post(
            settings.RECAPTCHA_VERIFY_URL,
            data={
                "secret": settings.RECAPTCHA_SECRET_KEY or "",
                "response": value,
                "remoteip": remote_ip or "",
            },
            timeout=settings.RECAPTCHA_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="reCAPTCHA verification is temporarily unavailable.")
    if not payload.get("success"):
        raise HTTPException(status_code=400, detail="reCAPTCHA verification failed. Please try again.")


def _contact_emails(raw_json: str | None) -> set[str]:
    if not raw_json:
        return set()
    try:
        parsed = json.loads(raw_json)
    except Exception:
        return set()
    contacts = parsed.get("contacts") if isinstance(parsed, dict) else []
    if not isinstance(contacts, list):
        return set()
    emails = set()
    for contact in contacts:
        if isinstance(contact, dict):
            email = str(contact.get("email") or "").strip().lower()
            if email:
                emails.add(email)
    return emails


def _primary_contact(raw_json: str | None) -> dict[str, str]:
    if not raw_json:
        return {"name": "", "email": "", "phone": ""}
    try:
        parsed = json.loads(raw_json)
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


def _property_label(prop: ManagedProperty | None, fallback: str | None = None) -> str:
    if not prop:
        return _clean(fallback) or ""
    tail = " ".join([x for x in [prop.suburb, prop.state_code, prop.postcode] if x])
    return ", ".join([x for x in [prop.property_address, tail] if x])


def _find_tenant_property(db: Session, mailbox: str, email: str, address: str | None) -> ManagedProperty | None:
    rows = (
        db.query(ManagedProperty)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.is_active == True)
        .all()
    )
    email_key = email.lower().strip()
    address_key = _normalize_key(address)

    email_matches = [row for row in rows if email_key in _contact_emails(row.tenants_json)]
    if len(email_matches) == 1:
        return email_matches[0]
    if email_matches and address_key:
        for row in email_matches:
            if address_key in _normalize_key(_property_label(row)) or _normalize_key(row.property_address) in address_key:
                return row

    if address_key:
        for row in rows:
            label_key = _normalize_key(_property_label(row))
            street_key = _normalize_key(row.property_address)
            if address_key == label_key or address_key == street_key or address_key in label_key or street_key in address_key:
                return row
    return None


def _tenant_to_dict(row: TenantAccount) -> dict:
    return {
        "id": row.id,
        "email": row.email,
        "name": row.name,
        "phone": row.phone,
        "property_id": row.property_id,
        "property_label": _property_label(row.property, row.property_address),
        "property_address": row.property.property_address if row.property else row.property_address,
        "suburb": row.property.suburb if row.property else row.suburb,
        "state_code": row.property.state_code if row.property else row.state_code,
        "postcode": row.property.postcode if row.property else row.postcode,
        "is_verified": row.is_verified,
        "last_login_at": row.last_login_at,
    }


def _order_to_tenant_dict(row: MaintenanceOrder) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "category": row.category,
        "priority": row.priority,
        "description": row.description,
        "access_notes": row.access_notes,
        "status": row.status.value if hasattr(row.status, "value") else str(row.status),
        "property_label": _property_label(row.property, row.property_address),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "completed_at": row.completed_at,
    }


def get_current_tenant(request: Request, db: Session = Depends(get_db)) -> TenantAccount:
    auth = request.headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    token = auth.split(" ", 1)[1].strip()
    subject = decode_access_token(token, settings.JWT_SECRET)
    if not subject or not subject.startswith("tenant:"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid tenant token")
    email = subject.split("tenant:", 1)[1].strip().lower()
    tenant = (
        db.query(TenantAccount)
        .options(selectinload(TenantAccount.property))
        .filter(TenantAccount.email == email)
        .first()
    )
    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tenant account disabled")
    return tenant


@router.post("/register")
def register_tenant(payload: TenantRegisterIn, request: Request, db: Session = Depends(get_db)):
    _verify_recaptcha(payload.recaptcha_token, request.client.host if request.client else None)
    _validate_password_strength(payload.password)

    email = payload.email.lower().strip()
    existing = db.query(TenantAccount).filter(TenantAccount.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="This tenant email is already registered. Please log in instead.")

    name = _clean(payload.name, 160)
    address = _clean(payload.property_address, 240)
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    if not address:
        raise HTTPException(status_code=400, detail="Property address is required.")

    now = datetime.utcnow()
    mailbox = _default_mailbox()
    prop = _find_tenant_property(db, mailbox, email, address)
    tenant = TenantAccount(
        mailbox=mailbox,
        email=email,
        name=name,
        phone=_clean(payload.phone, 80),
        password_hash=hash_password(payload.password),
        property_id=prop.id if prop else None,
        property_address=prop.property_address if prop else address,
        suburb=prop.suburb if prop else _clean(payload.suburb, 120),
        state_code=prop.state_code if prop else "VIC",
        postcode=prop.postcode if prop else _clean(payload.postcode, 20),
        is_active=True,
        is_verified=bool(prop),
        created_at=now,
        updated_at=now,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    token = create_access_token(subject=f"tenant:{tenant.email}", secret=settings.JWT_SECRET, expires_minutes=1440)
    return {"ok": True, "access_token": token, "token_type": "bearer", "tenant": _tenant_to_dict(tenant)}


@router.post("/login")
def login_tenant(payload: TenantLoginIn, request: Request, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    _verify_recaptcha(payload.recaptcha_token, request.client.host if request.client else None)
    email = payload.email.lower().strip()
    tenant = db.query(TenantAccount).filter(TenantAccount.email == email).first()
    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if tenant.locked_until and tenant.locked_until > now:
        raise HTTPException(status_code=423, detail="Account temporarily locked. Try again later.")
    if not verify_password(payload.password, tenant.password_hash):
        tenant.failed_login_attempts = int(tenant.failed_login_attempts or 0) + 1
        if tenant.failed_login_attempts >= MAX_LOGIN_ATTEMPTS:
            tenant.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            tenant.failed_login_attempts = 0
        tenant.updated_at = now
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    tenant.failed_login_attempts = 0
    tenant.locked_until = None
    tenant.last_login_at = now
    tenant.updated_at = now
    db.commit()
    db.refresh(tenant)
    token = create_access_token(subject=f"tenant:{tenant.email}", secret=settings.JWT_SECRET, expires_minutes=1440)
    return {"ok": True, "access_token": token, "token_type": "bearer", "tenant": _tenant_to_dict(tenant)}


@router.get("/me")
def tenant_me(tenant: TenantAccount = Depends(get_current_tenant)):
    return {"tenant": _tenant_to_dict(tenant)}


@router.get("/maintenance-requests")
def list_tenant_maintenance(
    db: Session = Depends(get_db),
    tenant: TenantAccount = Depends(get_current_tenant),
):
    rows = (
        db.query(MaintenanceOrder)
        .options(selectinload(MaintenanceOrder.property))
        .filter(MaintenanceOrder.mailbox == tenant.mailbox)
        .filter(MaintenanceOrder.tenant_account_id == tenant.id)
        .order_by(MaintenanceOrder.created_at.desc())
        .limit(50)
        .all()
    )
    return {"items": [_order_to_tenant_dict(row) for row in rows]}


@router.post("/maintenance-requests")
def create_tenant_maintenance(
    payload: TenantMaintenanceIn,
    db: Session = Depends(get_db),
    tenant: TenantAccount = Depends(get_current_tenant),
):
    title = _clean(payload.title, 180)
    description = _clean(payload.description)
    if not title:
        raise HTTPException(status_code=400, detail="Issue title is required.")
    if not description:
        raise HTTPException(status_code=400, detail="Description is required.")

    now = datetime.utcnow()
    prop = db.get(ManagedProperty, tenant.property_id) if tenant.property_id else None
    owner_contact = _primary_contact(prop.owners_json) if prop else {"name": "", "email": "", "phone": ""}
    property_address = prop.property_address if prop else tenant.property_address
    if not property_address:
        raise HTTPException(status_code=400, detail="Your tenant profile is missing a property address.")

    access_notes = _clean(payload.access_notes)
    preferred_contact = _clean(payload.preferred_contact, 120)
    if preferred_contact:
        access_notes = "\n".join([x for x in [access_notes, f"Preferred tenant contact: {preferred_contact}"] if x])

    row = MaintenanceOrder(
        mailbox=tenant.mailbox,
        property_id=prop.id if prop else None,
        property_address=property_address,
        suburb=prop.suburb if prop else tenant.suburb,
        state_code=prop.state_code if prop else (tenant.state_code or "VIC"),
        postcode=prop.postcode if prop else tenant.postcode,
        title=title,
        category=_clean(payload.category, 80) or "Tenant Request",
        priority=_clean(payload.priority, 30) or "normal",
        description=description,
        access_notes=access_notes,
        owner_name=owner_contact["name"] or None,
        owner_email=owner_contact["email"] or None,
        owner_phone=owner_contact["phone"] or None,
        tenant_name=tenant.name,
        tenant_email=tenant.email,
        tenant_phone=tenant.phone,
        status=MaintenanceOrderStatus.NEW,
        source="tenant_portal",
        tenant_account_id=tenant.id,
        tenant_submitted_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    db.add(
        MaintenanceEvent(
            mailbox=row.mailbox,
            order_id=row.id,
            actor_user_id=None,
            event_type="tenant_submitted",
            detail=f"Tenant portal request submitted by {tenant.name} ({tenant.email}).",
            created_at=now,
        )
    )
    db.commit()
    db.refresh(row)
    return {"ok": True, "request": _order_to_tenant_dict(row)}
