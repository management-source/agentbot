from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timedelta
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, EmailStr
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.authz import get_current_user, require_role
from app.config import settings
from app.db import get_db
from app.models import AppState, PasswordResetToken, ThreadTicket, ThreadTicketAudit, ThreadTicketNote, User, UserRole
from app.schemas import TeamMemberOut, UserOut
from app.security import create_access_token, hash_password, verify_password
from app.services.activity_log import record_activity

router = APIRouter(prefix="/user-auth", tags=["user-auth"])
logger = logging.getLogger(__name__)
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
PASSWORD_RESET_EXPIRE_MINUTES = 30
PASSWORD_RESET_COOLDOWN_MINUTES = 2
ROLE_PAGE_ACCESS_KEY = "system:role_page_access"


PAGE_REGISTRY = [
    {
        "id": "portal",
        "label": "Portal Hub",
        "description": "Landing dashboard and shortcuts to assigned workspaces.",
        "section": "Core",
        "locked": True,
    },
    {
        "id": "notifications",
        "label": "Notification Center",
        "description": "Action center for assigned tickets, maintenance updates, compliance risk, rent alerts, and personal follow-ups.",
        "section": "Core",
        "locked": True,
    },
    {
        "id": "inbox",
        "label": "Email Manager",
        "description": "Shared mailbox triage, ticket queues, replies, and Gmail sync.",
        "section": "Operations",
    },
    {
        "id": "maintenance",
        "label": "Maintenance",
        "description": "Staff-managed maintenance orders, owner approvals, quotes, tradie arrangements, and completion tracking.",
        "section": "Operations",
    },
    {
        "id": "myspace",
        "label": "My Space",
        "description": "Private planner, follow-ups, quick links, snippets, notes, and staff guides.",
        "section": "Core",
    },
    {
        "id": "rent",
        "label": "Rent Tracker",
        "description": "Rent due tracking, arrears, payments, and yearly reporting.",
        "section": "Operations",
    },
    {
        "id": "lease_renewals",
        "label": "Lease Renewals",
        "description": "Track lease renewal due dates, signatures, rent review details, follow-ups, and portfolio reporting.",
        "section": "Operations",
    },
    {
        "id": "landlord_reports",
        "label": "Monthly Landlord Report",
        "description": "Build, preview, and download branded monthly property reports for landlords.",
        "section": "Operations",
    },
    {
        "id": "compliance",
        "label": "Compliance",
        "description": "Create and maintain compliance records for managed properties.",
        "section": "Compliance",
    },
    {
        "id": "coverage",
        "label": "Compliance Report",
        "description": "Review missing and incomplete MRS, Smoke, Gas, and Electrical checks.",
        "section": "Compliance",
    },
    {
        "id": "compliance_providers",
        "label": "Compliance Providers",
        "description": "Manage reusable provider contacts for compliance records.",
        "section": "Compliance",
    },
    {
        "id": "properties",
        "label": "Properties",
        "description": "Import, add, search, and manage the property register.",
        "section": "Setup",
    },
    {
        "id": "team",
        "label": "Our Team",
        "description": "View the registered Dons Premier team, roles, avatars, and contact details.",
        "section": "Setup",
    },
    {
        "id": "activity",
        "label": "Activity Log",
        "description": "Review staff actions, platform changes, imports, uploads, assignments, and security events.",
        "section": "Setup",
    },
    {
        "id": "system",
        "label": "System",
        "description": "Admin-only user accounts, avatars, roles, and page access controls.",
        "section": "Setup",
        "admin_only": True,
    },
]


DEFAULT_ROLE_PAGE_ACCESS = {
    UserRole.ADMIN.value: ["portal", "notifications", "myspace", "inbox", "maintenance", "rent", "lease_renewals", "landlord_reports", "compliance", "coverage", "compliance_providers", "properties", "team", "activity", "system"],
    UserRole.PM.value: ["portal", "notifications", "myspace", "inbox", "maintenance", "rent", "lease_renewals", "landlord_reports", "compliance", "coverage", "compliance_providers", "properties", "team", "activity", "system"],
    UserRole.LEASING.value: ["portal", "notifications", "myspace", "inbox", "lease_renewals", "properties", "team"],
    UserRole.SALES.value: ["portal", "notifications", "myspace", "inbox", "maintenance", "rent", "lease_renewals", "landlord_reports", "compliance", "coverage", "compliance_providers", "properties", "team", "activity", "system"],
    UserRole.ACCOUNTS.value: ["portal", "notifications", "myspace", "inbox", "rent", "team"],
    UserRole.READONLY.value: ["portal", "notifications", "myspace", "team"],
}


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    recaptcha_token: str | None = None


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class CreateUserIn(BaseModel):
    email: EmailStr
    name: str
    phone: str | None = None
    role: UserRole = UserRole.PM
    password: str
    is_active: bool = True
    must_change_password: bool = False


class UpdateUserIn(BaseModel):
    name: str | None = None
    phone: str | None = None
    role: UserRole | None = None
    password: str | None = None
    is_active: bool | None = None


class ChangeMyPasswordIn(BaseModel):
    current_password: str
    new_password: str


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str


class AdminResetPasswordIn(BaseModel):
    new_password: str
    force_change_on_next_login: bool = True


class RolePageAccessIn(BaseModel):
    permissions: dict[str, list[str]]


def _to_user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        email=u.email,
        name=u.name,
        role=u.role,
        is_active=u.is_active,
        avatar_url=u.avatar_url,
        phone=u.phone,
        password_changed_at=u.password_changed_at,
        last_login_at=u.last_login_at,
        must_change_password=u.must_change_password,
    )


def _to_team_member_out(u: User, db: Session) -> TeamMemberOut:
    return TeamMemberOut(
        id=u.id,
        email=u.email,
        name=u.name,
        role=u.role,
        is_active=u.is_active,
        avatar_url=u.avatar_url,
        phone=u.phone,
        admin_access=_role_has_system_access(db, u.role),
        last_login_at=u.last_login_at,
    )


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


def _clean_phone(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned[:80] or None


def _password_reset_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _password_reset_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/?reset_token={token}"


def _send_password_reset_email(db: Session, to_email: str, reset_url: str) -> bool:
    try:
        from app.services.gmail_client import get_gmail_service, gmail_user_id

        service = get_gmail_service(db)
        msg = EmailMessage()
        msg["To"] = to_email
        msg["Subject"] = "Reset your Dons Premier portal password"
        msg.set_content(
            "A password reset was requested for your Dons Premier Estate Agents Portal account.\n\n"
            f"Open this secure link within {PASSWORD_RESET_EXPIRE_MINUTES} minutes to set a new password:\n"
            f"{reset_url}\n\n"
            "If you did not request this, you can ignore this email. Your password will not change."
        )
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        service.users().messages().send(userId=gmail_user_id(), body={"raw": raw}).execute()
        return True
    except Exception:
        logger.exception("Password reset email could not be sent")
        return False


def _cleanup_password_reset_tokens(db: Session, user_id: int | None = None) -> None:
    now = datetime.utcnow()
    q = db.query(PasswordResetToken).filter(PasswordResetToken.expires_at < now)
    if user_id is not None:
        q = q.filter(PasswordResetToken.user_id == user_id)
    q.delete(synchronize_session=False)


def _active_admin_count(db: Session) -> int:
    permissions = _get_role_page_access(db)
    system_roles = [role for role, pages in permissions.items() if "system" in set(pages or [])]
    if not system_roles:
        return 0
    return (
        db.query(func.count(User.id))
        .filter(and_(User.role.in_(system_roles), User.is_active == True))
        .scalar()
        or 0
    )


def _page_ids() -> list[str]:
    return [str(page["id"]) for page in PAGE_REGISTRY]


def _role_key(role: UserRole | str) -> str:
    if isinstance(role, UserRole):
        return role.value
    return str(role or "").strip().upper()


def _ordered_page_list(values: list[str] | set[str]) -> list[str]:
    selected = {str(v).strip() for v in values if str(v or "").strip()}
    return [page_id for page_id in _page_ids() if page_id in selected]


def _normalize_role_page_access(raw: dict | None) -> dict[str, list[str]]:
    raw = raw if isinstance(raw, dict) else {}
    page_ids = set(_page_ids())
    normalized: dict[str, list[str]] = {}

    for role in UserRole:
        key = role.value
        requested = raw.get(key, DEFAULT_ROLE_PAGE_ACCESS.get(key, ["portal"]))
        if not isinstance(requested, list):
            requested = DEFAULT_ROLE_PAGE_ACCESS.get(key, ["portal"])
        selected = {str(page_id).strip() for page_id in requested if str(page_id or "").strip() in page_ids}
        default_selected = set(DEFAULT_ROLE_PAGE_ACCESS.get(key, ["portal"]))
        locked_pages = {str(page["id"]) for page in PAGE_REGISTRY if page.get("locked")}
        missing_default_pages = {
            page_id for page_id in ("maintenance", "lease_renewals", "landlord_reports", "team", "activity", "compliance_providers", *locked_pages)
            if page_id in default_selected and page_id not in selected
        }
        if missing_default_pages and selected == (default_selected - missing_default_pages):
            selected.update(missing_default_pages)

        # Portal prevents blank workspaces. System controls admin access and is
        # intentionally configurable from the Access Control matrix.
        selected.add("portal")
        selected.update(page_id for page_id in locked_pages if page_id in default_selected)

        normalized[key] = _ordered_page_list(selected)

    return normalized


def _get_role_page_access(db: Session) -> dict[str, list[str]]:
    row = db.get(AppState, ROLE_PAGE_ACCESS_KEY)
    raw: dict | None = None
    if row and row.value:
        try:
            parsed = json.loads(row.value)
            raw = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            raw = None
    return _normalize_role_page_access(raw)


def _role_has_system_access(db: Session, role: UserRole | str | None) -> bool:
    role_key = _role_key(role or "")
    return "system" in set(_get_role_page_access(db).get(role_key, []))


def _active_system_access_count_for_permissions(db: Session, permissions: dict[str, list[str]]) -> int:
    system_roles = [role for role, pages in permissions.items() if "system" in set(pages or [])]
    if not system_roles:
        return 0
    return (
        db.query(func.count(User.id))
        .filter(and_(User.role.in_(system_roles), User.is_active == True))
        .scalar()
        or 0
    )


def _save_role_page_access(db: Session, permissions: dict) -> dict[str, list[str]]:
    normalized = _normalize_role_page_access(permissions)
    if _active_system_access_count_for_permissions(db, normalized) <= 0:
        raise HTTPException(status_code=400, detail="At least one active staff title must keep System access.")
    row = db.get(AppState, ROLE_PAGE_ACCESS_KEY)
    value = json.dumps(normalized, sort_keys=True)
    if row:
        row.value = value
        row.updated_at = datetime.utcnow()
    else:
        row = AppState(key=ROLE_PAGE_ACCESS_KEY, value=value, updated_at=datetime.utcnow())
        db.add(row)
    db.commit()
    return normalized


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


def _delete_local_avatar(old_avatar_url: str | None) -> None:
    if not old_avatar_url:
        return
    if not old_avatar_url.startswith("/static/avatars/"):
        return
    try:
        local = Path("app") / old_avatar_url.lstrip("/")
        if local.exists() and local.is_file():
            local.unlink()
    except Exception:
        pass


def _save_avatar(user_id: int, file: UploadFile) -> str:
    raw = file.file.read(MAX_AVATAR_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(raw) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="Avatar exceeds 2MB limit.")

    try:
        with Image.open(BytesIO(raw)) as image:
            image.verify()
            kind = str(image.format or "").upper()
    except (OSError, UnidentifiedImageError):
        kind = ""
    ext_map = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}
    ext = ext_map.get(kind)
    if not ext:
        raise HTTPException(status_code=400, detail="Unsupported image type. Use JPG, PNG, GIF, or WEBP.")

    mime = "image/jpeg" if ext == "jpg" else f"image/{ext}"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


@router.post("/login", response_model=LoginOut)
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    _verify_recaptcha(payload.recaptcha_token, request.client.host if request.client else None)
    user = db.query(User).filter(User.email == payload.email.lower().strip()).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if user.locked_until and user.locked_until > now:
        raise HTTPException(status_code=423, detail="Account temporarily locked. Try again later.")

    if not verify_password(payload.password, user.password_hash):
        user.failed_login_attempts = int(user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= MAX_LOGIN_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_login_attempts = 0
        user.updated_at = now
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    user.updated_at = now
    db.commit()

    record_activity(
        db,
        actor=user,
        action="Logged in",
        area="System Access",
        entity_type="User Account",
        entity_id=str(user.id),
        method="POST",
        path="/user-auth/login",
        status_code=200,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
        detail={"result": "success"},
        commit=True,
    )

    token = create_access_token(subject=user.email, secret=settings.JWT_SECRET)
    return LoginOut(access_token=token, user=_to_user_out(user))


@router.post("/logout")
def logout(user: User = Depends(get_current_user)):
    return {"ok": True, "user_id": user.id}


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordIn, request: Request, db: Session = Depends(get_db)):
    """Create and email a one-time password reset link.

    The response is intentionally generic so callers cannot enumerate valid
    staff accounts.
    """
    now = datetime.utcnow()
    email = payload.email.lower().strip()
    user = db.query(User).filter(User.email == email).first()
    response = {
        "ok": True,
        "message": "If that email is registered, a secure password reset link has been sent.",
    }
    if not user or not user.is_active:
        return response

    _cleanup_password_reset_tokens(db, user.id)
    recent = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.user_id == user.id)
        .filter(PasswordResetToken.created_at > now - timedelta(minutes=PASSWORD_RESET_COOLDOWN_MINUTES))
        .order_by(PasswordResetToken.created_at.desc())
        .first()
    )
    if recent:
        db.commit()
        return response

    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).filter(
        PasswordResetToken.used_at.is_(None)
    ).update({PasswordResetToken.used_at: now}, synchronize_session=False)

    token = secrets.token_urlsafe(32)
    reset = PasswordResetToken(
        user_id=user.id,
        token_hash=_password_reset_hash(token),
        expires_at=now + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES),
        requested_ip=request.client.host if request.client else None,
        created_at=now,
    )
    db.add(reset)
    db.commit()

    sent = _send_password_reset_email(db, user.email, _password_reset_url(request, token))
    if not sent:
        reset.used_at = datetime.utcnow()
        db.commit()
    return response


@router.post("/reset-password")
def reset_password(payload: ResetPasswordIn, db: Session = Depends(get_db)):
    token = (payload.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Reset token is required.")
    _validate_password_strength(payload.new_password)

    now = datetime.utcnow()
    reset = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == _password_reset_hash(token))
        .filter(PasswordResetToken.used_at.is_(None))
        .filter(PasswordResetToken.expires_at >= now)
        .first()
    )
    if not reset:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")

    user = db.get(User, reset.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")

    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = now
    user.must_change_password = False
    user.failed_login_attempts = 0
    user.locked_until = None
    user.updated_at = now
    reset.used_at = now
    db.commit()
    return {"ok": True, "message": "Password updated. You can now log in."}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _to_user_out(user)


@router.get("/page-access")
def page_access(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    permissions = _get_role_page_access(db)
    role = _role_key(user.role)
    return {
        "pages": PAGE_REGISTRY,
        "role": role,
        "allowed_pages": permissions.get(role, ["portal"]),
    }


@router.get("/role-page-access")
def get_role_page_access(
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    return {
        "pages": PAGE_REGISTRY,
        "permissions": _get_role_page_access(db),
    }


@router.put("/role-page-access")
def update_role_page_access(
    payload: RolePageAccessIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    return {
        "pages": PAGE_REGISTRY,
        "permissions": _save_role_page_access(db, payload.permissions),
    }


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    users = db.query(User).order_by(User.name.asc()).all()
    return [_to_user_out(u) for u in users]


@router.get("/team", response_model=list[TeamMemberOut])
def list_team(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    users = (
        db.query(User)
        .filter(User.is_active == True)
        .order_by(User.name.asc())
        .all()
    )
    return [_to_team_member_out(u, db) for u in users]


@router.post("/users", response_model=UserOut)
def create_user(
    payload: CreateUserIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    _validate_password_strength(payload.password)
    existing = db.query(User).filter(User.email == payload.email.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    now = datetime.utcnow()
    u = User(
        email=payload.email.lower(),
        name=payload.name.strip(),
        phone=_clean_phone(payload.phone),
        role=payload.role,
        is_active=payload.is_active,
        password_hash=hash_password(payload.password),
        must_change_password=payload.must_change_password,
        password_changed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return _to_user_out(u)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UpdateUserIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.utcnow()
    if payload.name is not None:
        u.name = payload.name.strip()
    if payload.phone is not None:
        u.phone = _clean_phone(payload.phone)
    if payload.role is not None:
        if _role_has_system_access(db, u.role) and not _role_has_system_access(db, payload.role) and u.is_active and _active_admin_count(db) <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last active admin.")
        u.role = payload.role
    if payload.is_active is not None:
        if _role_has_system_access(db, u.role) and payload.is_active is False and _active_admin_count(db) <= 1:
            raise HTTPException(status_code=400, detail="Cannot disable the last active admin.")
        u.is_active = payload.is_active
    if payload.password:
        _validate_password_strength(payload.password)
        u.password_hash = hash_password(payload.password)
        u.password_changed_at = now
        u.must_change_password = False

    u.updated_at = now
    db.commit()
    db.refresh(u)
    return _to_user_out(u)


@router.post("/me/password")
def change_my_password(
    payload: ChangeMyPasswordIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="New password must be different from current password.")
    _validate_password_strength(payload.new_password)

    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = datetime.utcnow()
    user.must_change_password = False
    user.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/me/avatar", response_model=UserOut)
def upload_my_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    new_url = _save_avatar(user.id, file)
    _delete_local_avatar(user.avatar_url)
    user.avatar_url = new_url
    user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return _to_user_out(user)


@router.post("/users/{user_id}/avatar", response_model=UserOut)
def admin_upload_user_avatar(
    user_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    new_url = _save_avatar(u.id, file)
    _delete_local_avatar(u.avatar_url)
    u.avatar_url = new_url
    u.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(u)
    return _to_user_out(u)


@router.patch("/users/{user_id}/password", response_model=UserOut)
def admin_reset_user_password(
    user_id: int,
    payload: AdminResetPasswordIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    _validate_password_strength(payload.new_password)
    u.password_hash = hash_password(payload.new_password)
    u.password_changed_at = datetime.utcnow()
    u.must_change_password = bool(payload.force_change_on_next_login)
    u.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(u)
    return _to_user_out(u)


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_role(UserRole.ADMIN)),
):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    if u.id == admin_user.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    if _role_has_system_access(db, u.role) and u.is_active and _active_admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last active admin.")

    # Remove user-owned references so deletion succeeds safely.
    db.query(ThreadTicket).filter(ThreadTicket.owner_user_id == u.id).update({ThreadTicket.owner_user_id: None}, synchronize_session=False)
    db.query(ThreadTicket).filter(ThreadTicket.assignee_user_id == u.id).update({ThreadTicket.assignee_user_id: None}, synchronize_session=False)
    db.query(ThreadTicketAudit).filter(ThreadTicketAudit.actor_user_id == u.id).update({ThreadTicketAudit.actor_user_id: None}, synchronize_session=False)
    db.query(ThreadTicketNote).filter(ThreadTicketNote.author_user_id == u.id).delete(synchronize_session=False)
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == u.id).delete(synchronize_session=False)

    _delete_local_avatar(u.avatar_url)
    db.delete(u)
    db.commit()
    return {"ok": True}
