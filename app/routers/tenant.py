from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import re
import secrets
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import or_
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
    ManagedProperty,
    TenantAccount,
    TenantPasswordResetToken,
    User,
)
from app.security import create_access_token, decode_access_token, hash_password, verify_password

router = APIRouter(prefix="/tenant/api", tags=["tenant"])
logger = logging.getLogger(__name__)

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
MAX_TENANT_UPLOAD_FILES = 8
TENANT_UPLOAD_KIND = "TENANT_MEDIA"
PASSWORD_RESET_EXPIRE_MINUTES = 30
PASSWORD_RESET_COOLDOWN_MINUTES = 2
TENANT_PASSWORD_MIN_LENGTH = 8


class TenantRegisterIn(BaseModel):
    name: str
    email: EmailStr
    phone: str | None = None
    password: str
    property_id: int | None = None
    property_address: str
    suburb: str | None = None
    postcode: str | None = None
    recaptcha_token: str | None = None


class TenantLoginIn(BaseModel):
    email: EmailStr
    password: str
    recaptcha_token: str | None = None


class TenantForgotPasswordIn(BaseModel):
    email: EmailStr


class TenantResetPasswordIn(BaseModel):
    token: str
    new_password: str


class TenantProfileUpdateIn(BaseModel):
    phone: str | None = None
    preferred_contact_method: str | None = None


class TenantMaintenanceIn(BaseModel):
    title: str
    category: str | None = None
    priority: str = "normal"
    description: str
    access_notes: str | None = None
    preferred_contact: str | None = None


class TenantRegistrationUpdateIn(BaseModel):
    is_active: bool | None = None
    is_verified: bool | None = None


def _clean(value: str | None, max_len: int | None = None) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if max_len:
        text = text[:max_len]
    return text or None


def _safe_filename(value: str | None) -> str:
    name = Path(value or "tenant-upload").name
    name = re.sub(r"[\r\n\t\x00-\x1f\x7f]+", "", name).replace('"', "")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "-", name).strip(" .-_")
    return name[:180] or "tenant-upload"


def _tenant_upload_root() -> Path:
    root = Path(settings.TENANT_UPLOAD_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _tenant_upload_path(tenant_id: int, order_id: int, filename: str) -> tuple[Path, str]:
    safe_name = _safe_filename(filename)
    relative = Path(f"tenant_{tenant_id}") / f"order_{order_id}" / f"{uuid.uuid4().hex}_{safe_name}"
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


def _validate_tenant_media(file: UploadFile, raw: bytes) -> str:
    if not raw:
        raise HTTPException(status_code=400, detail=f"{file.filename or 'Upload'} is empty.")
    if len(raw) > settings.TENANT_UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Tenant uploads must be 25MB or smaller per file.")
    content_type = _upload_content_type(file)
    if not (content_type.startswith("image/") or content_type.startswith("video/")):
        raise HTTPException(status_code=400, detail="Only image and video uploads are allowed.")
    return content_type


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
    if len(p) < TENANT_PASSWORD_MIN_LENGTH:
        raise HTTPException(status_code=400, detail=f"Password must be at least {TENANT_PASSWORD_MIN_LENGTH} characters.")
    if not re.search(r"[a-z]", p):
        raise HTTPException(status_code=400, detail="Password must include a lowercase letter.")
    if not re.search(r"[A-Z]", p):
        raise HTTPException(status_code=400, detail="Password must include an uppercase letter.")
    if not re.search(r"\d", p):
        raise HTTPException(status_code=400, detail="Password must include a number.")
    if not re.search(r"[^A-Za-z0-9]", p):
        raise HTTPException(status_code=400, detail="Password must include a symbol.")


def _tenant_password_reset_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _tenant_password_reset_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/tenant/reset-password?token={token}"


def _send_tenant_password_reset_email(db: Session, to_email: str, reset_url: str) -> bool:
    try:
        from app.services.gmail_client import get_gmail_service, gmail_user_id

        service = get_gmail_service(db)
        msg = EmailMessage()
        msg["To"] = to_email
        msg["Subject"] = "Reset your Dons Premier tenant portal password"
        msg.set_content(
            "A password reset was requested for your Dons Premier Tenant Portal account.\n\n"
            f"Open this secure link within {PASSWORD_RESET_EXPIRE_MINUTES} minutes to set a new password:\n"
            f"{reset_url}\n\n"
            "If you did not request this, you can ignore this email. Your password will not change."
        )
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        service.users().messages().send(userId=gmail_user_id(), body={"raw": raw}).execute()
        return True
    except Exception:
        logger.exception("Tenant password reset email could not be sent")
        return False


def _send_tenant_request_confirmation_email(db: Session, tenant: TenantAccount, row: MaintenanceOrder) -> bool:
    try:
        from app.services.gmail_client import get_gmail_service, gmail_user_id

        service = get_gmail_service(db)
        reference = _maintenance_reference(row.id)
        msg = EmailMessage()
        msg["To"] = tenant.email
        msg["Subject"] = f"We received your maintenance request {reference}"
        msg.set_content(
            f"Hi {tenant.name or 'there'},\n\n"
            "Thank you. We have received your maintenance request and it has been added to the Dons Premier maintenance queue.\n\n"
            f"Reference: {reference}\n"
            f"Property: {_property_label(row.property, row.property_address)}\n"
            f"Issue: {row.title}\n"
            f"Status: {row.status.value if hasattr(row.status, 'value') else row.status}\n\n"
            "Our team will review the details and contact you if we need more information.\n\n"
            "If this is urgent or unsafe, please call Dons Premier Estate Agents directly instead of waiting for a portal update.\n\n"
            "Kind regards,\n"
            "Dons Premier Estate Agents"
        )
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        service.users().messages().send(userId=gmail_user_id(), body={"raw": raw}).execute()
        return True
    except Exception:
        logger.exception("Tenant maintenance confirmation email could not be sent")
        return False


def _cleanup_tenant_password_reset_tokens(db: Session, tenant_id: int | None = None) -> None:
    now = datetime.utcnow()
    q = db.query(TenantPasswordResetToken).filter(TenantPasswordResetToken.expires_at < now)
    if tenant_id is not None:
        q = q.filter(TenantPasswordResetToken.tenant_account_id == tenant_id)
    q.delete(synchronize_session=False)


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


def _property_suggestion(row: ManagedProperty) -> dict[str, object]:
    return {
        "id": row.id,
        "label": _property_label(row),
        "property_address": row.property_address,
        "suburb": row.suburb,
        "state_code": row.state_code,
        "postcode": row.postcode,
    }


def _status_value(value: MaintenanceOrderStatus | str | None) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value or MaintenanceOrderStatus.NEW.value)


def _maintenance_reference(order_id: int | None) -> str:
    return f"DP-MNT-{int(order_id or 0):05d}"


def _tenant_timeline(row: MaintenanceOrder) -> list[dict[str, object]]:
    status = _status_value(row.status).upper()
    stages = [
        ("submitted", "Submitted", row.tenant_submitted_at or row.created_at),
        ("review", "Staff review", row.updated_at),
        ("approval", "Owner approval / quote", row.owner_sent_at or row.owner_decided_at or row.quote_received_at),
        ("arranged", "Tradie arranged", row.tradie_arranged_at or row.tenant_notified_at),
        ("completed", "Completed", row.completed_at),
    ]
    progress_map = {
        MaintenanceOrderStatus.NEW.value: 1,
        MaintenanceOrderStatus.WAITING_OWNER_APPROVAL.value: 2,
        MaintenanceOrderStatus.OWNER_APPROVED.value: 2,
        MaintenanceOrderStatus.OWNER_DECLINED.value: 2,
        MaintenanceOrderStatus.OWNER_ARRANGING.value: 2,
        MaintenanceOrderStatus.QUOTE_REQUESTED.value: 2,
        MaintenanceOrderStatus.QUOTE_RECEIVED.value: 2,
        MaintenanceOrderStatus.TRADIE_ARRANGED.value: 3,
        MaintenanceOrderStatus.TENANT_NOTIFIED.value: 3,
        MaintenanceOrderStatus.COMPLETED.value: 4,
        MaintenanceOrderStatus.CANCELLED.value: 2,
    }
    progress = progress_map.get(status, 1)
    result: list[dict[str, object]] = []
    for index, (key, label, at) in enumerate(stages):
        if status == MaintenanceOrderStatus.CANCELLED.value and key == "completed":
            state = "stopped"
            label = "Cancelled"
        elif status == MaintenanceOrderStatus.COMPLETED.value:
            state = "complete"
        elif index == 0:
            state = "complete"
        elif progress > index:
            state = "complete"
        elif progress == index:
            state = "current"
        else:
            state = "pending"
        result.append({"key": key, "label": label, "state": state, "at": at})
    return result


def _get_registered_property(db: Session, mailbox: str, property_id: int | None) -> ManagedProperty:
    if not property_id:
        raise HTTPException(status_code=400, detail="Please select your property from the portal property list.")
    prop = (
        db.query(ManagedProperty)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.id == property_id)
        .filter(ManagedProperty.is_active == True)
        .first()
    )
    if not prop:
        raise HTTPException(status_code=400, detail="Please select a valid property from the portal property list.")
    return prop


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
        "preferred_contact_method": row.preferred_contact_method,
        "property_id": row.property_id,
        "property_label": _property_label(row.property, row.property_address),
        "property_address": row.property.property_address if row.property else row.property_address,
        "suburb": row.property.suburb if row.property else row.suburb,
        "state_code": row.property.state_code if row.property else row.state_code,
        "postcode": row.property.postcode if row.property else row.postcode,
        "is_verified": row.is_verified,
        "is_active": row.is_active,
        "last_login_at": row.last_login_at,
    }


def _tenant_admin_to_dict(row: TenantAccount) -> dict:
    data = _tenant_to_dict(row)
    data.update(
        {
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )
    return data


def _order_to_tenant_dict(row: MaintenanceOrder) -> dict:
    return {
        "id": row.id,
        "reference": _maintenance_reference(row.id),
        "title": row.title,
        "category": row.category,
        "priority": row.priority,
        "description": row.description,
        "access_notes": row.access_notes,
        "status": _status_value(row.status),
        "property_label": _property_label(row.property, row.property_address),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "completed_at": row.completed_at,
        "attachment_count": len(row.attachments or []),
        "timeline": _tenant_timeline(row),
    }


def _tenant_registration_query(db: Session, mailbox: str):
    return (
        db.query(TenantAccount)
        .options(selectinload(TenantAccount.property))
        .filter(TenantAccount.mailbox == mailbox)
    )


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
    prop = _get_registered_property(db, mailbox, payload.property_id)
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
        is_verified=email in _contact_emails(prop.tenants_json),
        created_at=now,
        updated_at=now,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    token = create_access_token(subject=f"tenant:{tenant.email}", secret=settings.JWT_SECRET, expires_minutes=1440)
    return {"ok": True, "access_token": token, "token_type": "bearer", "tenant": _tenant_to_dict(tenant)}


@router.get("/property-suggestions")
def tenant_property_suggestions(q: str | None = None, db: Session = Depends(get_db)):
    query = _clean(q, 120) or ""
    if len(query) < 3:
        return {"items": []}
    mailbox = _default_mailbox()
    query_key = _normalize_key(query)
    tokens = [token for token in query_key.split() if len(token) > 1 or token.isdigit()]
    if not tokens:
        return {"items": []}
    token_filters = []
    for token in tokens[:8]:
        like = f"%{token}%"
        token_filters.extend(
            [
                ManagedProperty.property_address.ilike(like),
                ManagedProperty.suburb.ilike(like),
                ManagedProperty.state_code.ilike(like),
                ManagedProperty.postcode.ilike(like),
                ManagedProperty.tenants_json.ilike(like),
            ]
        )
    rows = (
        db.query(ManagedProperty)
        .filter(ManagedProperty.mailbox == mailbox)
        .filter(ManagedProperty.is_active == True)
        .filter(or_(*token_filters))
        .order_by(ManagedProperty.property_address.asc())
        .limit(80)
        .all()
    )
    scored: list[tuple[int, ManagedProperty]] = []
    for row in rows:
        haystack = _normalize_key(
            " ".join(
                [
                    row.property_address or "",
                    row.suburb or "",
                    row.state_code or "",
                    row.postcode or "",
                ]
            )
        )
        if not all(token in haystack for token in tokens if token not in {"vic"}):
            continue
        street_key = _normalize_key(row.property_address)
        label_key = _normalize_key(_property_label(row))
        if query_key == street_key:
            score = 0
        elif query_key == label_key:
            score = 1
        elif street_key.startswith(query_key) or label_key.startswith(query_key):
            score = 2
        else:
            score = 3
        scored.append((score, row))
    scored.sort(key=lambda item: (item[0], item[1].property_address or ""))
    return {"items": [_property_suggestion(row) for _, row in scored[:10]]}


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


@router.post("/forgot-password")
def forgot_tenant_password(payload: TenantForgotPasswordIn, request: Request, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    email = payload.email.lower().strip()
    response = {
        "ok": True,
        "message": "If that tenant email is registered, a secure password reset link has been sent.",
    }
    tenant = db.query(TenantAccount).filter(TenantAccount.email == email).first()
    if not tenant or not tenant.is_active:
        return response

    _cleanup_tenant_password_reset_tokens(db, tenant.id)
    recent = (
        db.query(TenantPasswordResetToken)
        .filter(TenantPasswordResetToken.tenant_account_id == tenant.id)
        .filter(TenantPasswordResetToken.created_at > now - timedelta(minutes=PASSWORD_RESET_COOLDOWN_MINUTES))
        .order_by(TenantPasswordResetToken.created_at.desc())
        .first()
    )
    if recent:
        db.commit()
        return response

    db.query(TenantPasswordResetToken).filter(TenantPasswordResetToken.tenant_account_id == tenant.id).filter(
        TenantPasswordResetToken.used_at.is_(None)
    ).update({TenantPasswordResetToken.used_at: now}, synchronize_session=False)

    token = secrets.token_urlsafe(32)
    reset = TenantPasswordResetToken(
        tenant_account_id=tenant.id,
        token_hash=_tenant_password_reset_hash(token),
        expires_at=now + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES),
        requested_ip=request.client.host if request.client else None,
        created_at=now,
    )
    db.add(reset)
    db.commit()

    sent = _send_tenant_password_reset_email(db, tenant.email, _tenant_password_reset_url(request, token))
    if not sent:
        reset.used_at = datetime.utcnow()
        db.commit()
    return response


@router.post("/reset-password")
def reset_tenant_password(payload: TenantResetPasswordIn, db: Session = Depends(get_db)):
    token = (payload.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Reset token is required.")
    _validate_password_strength(payload.new_password)

    now = datetime.utcnow()
    reset = (
        db.query(TenantPasswordResetToken)
        .filter(TenantPasswordResetToken.token_hash == _tenant_password_reset_hash(token))
        .filter(TenantPasswordResetToken.used_at.is_(None))
        .filter(TenantPasswordResetToken.expires_at >= now)
        .first()
    )
    if not reset:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")

    tenant = db.get(TenantAccount, reset.tenant_account_id)
    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")

    tenant.password_hash = hash_password(payload.new_password)
    tenant.failed_login_attempts = 0
    tenant.locked_until = None
    tenant.updated_at = now
    reset.used_at = now
    db.commit()
    return {"ok": True, "message": "Password updated. You can now log in."}


@router.get("/me")
def tenant_me(tenant: TenantAccount = Depends(get_current_tenant)):
    return {"tenant": _tenant_to_dict(tenant)}


@router.patch("/me")
def update_tenant_profile(
    payload: TenantProfileUpdateIn,
    db: Session = Depends(get_db),
    tenant: TenantAccount = Depends(get_current_tenant),
):
    fields = getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", set()))
    if "phone" in fields:
        tenant.phone = _clean(payload.phone, 80)
    if "preferred_contact_method" in fields:
        tenant.preferred_contact_method = _clean(payload.preferred_contact_method, 120)
    tenant.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(tenant)
    return {"tenant": _tenant_to_dict(tenant)}


@router.get("/maintenance-requests")
def list_tenant_maintenance(
    db: Session = Depends(get_db),
    tenant: TenantAccount = Depends(get_current_tenant),
):
    rows = (
        db.query(MaintenanceOrder)
        .options(selectinload(MaintenanceOrder.property), selectinload(MaintenanceOrder.attachments))
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
    preferred_contact = _clean(payload.preferred_contact, 120) or _clean(tenant.preferred_contact_method, 120)
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
    confirmation_sent = _send_tenant_request_confirmation_email(db, tenant, row)
    db.add(
        MaintenanceEvent(
            mailbox=row.mailbox,
            order_id=row.id,
            actor_user_id=None,
            event_type="tenant_confirmation_email",
            detail="Tenant confirmation email sent." if confirmation_sent else "Tenant confirmation email could not be sent automatically.",
            created_at=datetime.utcnow(),
        )
    )
    db.commit()
    db.refresh(row)
    return {"ok": True, "request": _order_to_tenant_dict(row), "confirmation_email_sent": confirmation_sent}


@router.post("/maintenance-requests/{order_id}/attachments")
def upload_tenant_maintenance_attachments(
    order_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    tenant: TenantAccount = Depends(get_current_tenant),
):
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one image or video.")
    if len(files) > MAX_TENANT_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"Upload up to {MAX_TENANT_UPLOAD_FILES} files at a time.")

    order = (
        db.query(MaintenanceOrder)
        .filter(MaintenanceOrder.mailbox == tenant.mailbox)
        .filter(MaintenanceOrder.id == order_id)
        .filter(MaintenanceOrder.tenant_account_id == tenant.id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Maintenance request not found.")

    pending: list[tuple[UploadFile, bytes, str]] = []
    for file in files:
        raw = file.file.read(settings.TENANT_UPLOAD_MAX_BYTES + 1)
        content_type = _validate_tenant_media(file, raw)
        pending.append((file, raw, content_type))

    now = datetime.utcnow()
    saved: list[MaintenanceAttachment] = []
    written_paths: list[Path] = []
    try:
        for file, raw, content_type in pending:
            target, relative_path = _tenant_upload_path(tenant.id, order.id, file.filename or "tenant-upload")
            target.write_bytes(raw)
            written_paths.append(target)
            attachment = MaintenanceAttachment(
                mailbox=tenant.mailbox,
                order_id=order.id,
                kind=TENANT_UPLOAD_KIND,
                filename=_safe_filename(file.filename),
                content_type=content_type,
                content_bytes=b"",
                storage_path=relative_path,
                file_size=len(raw),
                notes="Uploaded by tenant portal.",
                uploaded_by_user_id=None,
                uploaded_by_tenant_id=tenant.id,
                created_at=now,
            )
            db.add(attachment)
            saved.append(attachment)
    except OSError:
        for path in written_paths:
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except OSError:
                pass
        raise HTTPException(status_code=503, detail="Upload storage is unavailable. Please try again later.")

    order.updated_at = now
    db.add(
        MaintenanceEvent(
            mailbox=order.mailbox,
            order_id=order.id,
            actor_user_id=None,
            event_type="tenant_media_uploaded",
            detail=f"Tenant uploaded {len(saved)} media file(s).",
            created_at=now,
        )
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        for path in written_paths:
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except OSError:
                pass
        raise
    return {
        "ok": True,
        "items": [
            {
                "id": item.id,
                "filename": item.filename,
                "content_type": item.content_type,
                "size": item.file_size,
                "created_at": item.created_at,
            }
            for item in saved
        ],
    }


@router.get("/admin/registrations")
def list_tenant_registrations(
    query: str | None = None,
    active: str | None = None,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("maintenance")),
):
    q = _tenant_registration_query(db, mailbox)
    if query and query.strip():
        like = f"%{query.strip()}%"
        q = q.filter(
            or_(
                TenantAccount.name.ilike(like),
                TenantAccount.email.ilike(like),
                TenantAccount.phone.ilike(like),
                TenantAccount.property_address.ilike(like),
            )
        )
    key = (active or "").strip().lower()
    if key in {"active", "true", "1"}:
        q = q.filter(TenantAccount.is_active == True)
    elif key in {"inactive", "false", "0"}:
        q = q.filter(TenantAccount.is_active == False)

    rows = q.order_by(TenantAccount.created_at.desc()).limit(250).all()
    return {"items": [_tenant_admin_to_dict(row) for row in rows]}


@router.patch("/admin/registrations/{tenant_id}")
def update_tenant_registration(
    tenant_id: int,
    payload: TenantRegistrationUpdateIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("maintenance")),
):
    row = _tenant_registration_query(db, mailbox).filter(TenantAccount.id == tenant_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant registration not found.")
    fields = set(getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", set())))
    if "is_active" in fields and payload.is_active is not None:
        row.is_active = payload.is_active
    if "is_verified" in fields and payload.is_verified is not None:
        row.is_verified = payload.is_verified
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _tenant_admin_to_dict(row)


@router.delete("/admin/registrations/{tenant_id}")
def delete_tenant_registration(
    tenant_id: int,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("maintenance")),
):
    row = _tenant_registration_query(db, mailbox).filter(TenantAccount.id == tenant_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant registration not found.")

    now = datetime.utcnow()
    db.query(MaintenanceOrder).filter(MaintenanceOrder.tenant_account_id == row.id).update(
        {MaintenanceOrder.tenant_account_id: None, MaintenanceOrder.updated_at: now},
        synchronize_session=False,
    )
    db.query(MaintenanceAttachment).filter(MaintenanceAttachment.uploaded_by_tenant_id == row.id).update(
        {MaintenanceAttachment.uploaded_by_tenant_id: None},
        synchronize_session=False,
    )
    db.query(TenantPasswordResetToken).filter(TenantPasswordResetToken.tenant_account_id == row.id).delete(
        synchronize_session=False
    )
    deleted_email = row.email
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted_id": tenant_id, "deleted_email": deleted_email}
