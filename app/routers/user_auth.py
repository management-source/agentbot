from __future__ import annotations

import base64
import imghdr
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, EmailStr
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.authz import get_current_user, require_role
from app.config import settings
from app.db import get_db
from app.models import ThreadTicket, ThreadTicketAudit, ThreadTicketNote, User, UserRole
from app.schemas import UserOut
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/user-auth", tags=["user-auth"])
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


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
    role: UserRole = UserRole.PM
    password: str
    is_active: bool = True
    must_change_password: bool = False


class UpdateUserIn(BaseModel):
    name: str | None = None
    role: UserRole | None = None
    password: str | None = None
    is_active: bool | None = None


class ChangeMyPasswordIn(BaseModel):
    current_password: str
    new_password: str


class AdminResetPasswordIn(BaseModel):
    new_password: str
    force_change_on_next_login: bool = True


def _to_user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        email=u.email,
        name=u.name,
        role=u.role,
        is_active=u.is_active,
        avatar_url=u.avatar_url,
        password_changed_at=u.password_changed_at,
        last_login_at=u.last_login_at,
        must_change_password=u.must_change_password,
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


def _active_admin_count(db: Session) -> int:
    return (
        db.query(func.count(User.id))
        .filter(and_(User.role == UserRole.ADMIN, User.is_active == True))
        .scalar()
        or 0
    )


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

    kind = imghdr.what(None, h=raw)
    ext_map = {"jpeg": "jpg", "png": "png", "gif": "gif", "webp": "webp"}
    ext = ext_map.get(kind or "")
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

    token = create_access_token(subject=user.email, secret=settings.JWT_SECRET)
    return LoginOut(access_token=token, user=_to_user_out(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _to_user_out(user)


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    users = db.query(User).order_by(User.name.asc()).all()
    return [_to_user_out(u) for u in users]


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
    if payload.role is not None:
        if u.role == UserRole.ADMIN and payload.role != UserRole.ADMIN and u.is_active and _active_admin_count(db) <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last active admin.")
        u.role = payload.role
    if payload.is_active is not None:
        if u.role == UserRole.ADMIN and payload.is_active is False and _active_admin_count(db) <= 1:
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
    if u.role == UserRole.ADMIN and u.is_active and _active_admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last active admin.")

    # Remove user-owned references so deletion succeeds safely.
    db.query(ThreadTicket).filter(ThreadTicket.owner_user_id == u.id).update({ThreadTicket.owner_user_id: None}, synchronize_session=False)
    db.query(ThreadTicket).filter(ThreadTicket.assignee_user_id == u.id).update({ThreadTicket.assignee_user_id: None}, synchronize_session=False)
    db.query(ThreadTicketAudit).filter(ThreadTicketAudit.actor_user_id == u.id).update({ThreadTicketAudit.actor_user_id: None}, synchronize_session=False)
    db.query(ThreadTicketNote).filter(ThreadTicketNote.author_user_id == u.id).delete(synchronize_session=False)

    _delete_local_avatar(u.avatar_url)
    db.delete(u)
    db.commit()
    return {"ok": True}
