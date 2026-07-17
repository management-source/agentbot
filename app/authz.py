from __future__ import annotations

import json

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import AppState, User, UserRole
from app.security import decode_access_token


ROLE_PAGE_ACCESS_KEY = "system:role_page_access"
DEFAULT_ADMIN_ACCESS_ROLES = {UserRole.ADMIN.value, UserRole.PM.value, UserRole.SALES.value}
DEFAULT_PAGE_ACCESS = {
    UserRole.ADMIN.value: {"portal", "notifications", "myspace", "inbox", "maintenance", "rent", "lease_renewals", "landlord_reports", "compliance", "coverage", "compliance_providers", "properties", "team", "activity", "system"},
    UserRole.PM.value: {"portal", "notifications", "myspace", "inbox", "maintenance", "rent", "lease_renewals", "landlord_reports", "compliance", "coverage", "compliance_providers", "properties", "team", "activity", "system"},
    UserRole.LEASING.value: {"portal", "notifications", "myspace", "inbox", "properties", "team"},
    UserRole.SALES.value: {"portal", "notifications", "myspace", "inbox", "maintenance", "rent", "lease_renewals", "landlord_reports", "compliance", "coverage", "compliance_providers", "properties", "team", "activity", "system"},
    UserRole.ACCOUNTS.value: {"portal", "notifications", "myspace", "inbox", "rent", "team"},
    UserRole.READONLY.value: {"portal", "notifications", "myspace", "team"},
}


def _role_key(role: UserRole | str | None) -> str:
    if isinstance(role, UserRole):
        return role.value
    return str(role or "").strip().upper()


def has_admin_access(role: UserRole | str | None, db: Session | None = None) -> bool:
    key = _role_key(role)
    if not key:
        return False
    if db is not None:
        row = db.get(AppState, ROLE_PAGE_ACCESS_KEY)
        if row and row.value:
            try:
                parsed = json.loads(row.value)
            except json.JSONDecodeError:
                parsed = None
            pages = parsed.get(key) if isinstance(parsed, dict) else None
            if isinstance(pages, list):
                return "system" in {str(page_id).strip() for page_id in pages}
    return key in DEFAULT_ADMIN_ACCESS_ROLES


def has_page_access(role: UserRole | str | None, page_id: str, db: Session | None = None) -> bool:
    key = _role_key(role)
    page = str(page_id or "").strip()
    if not key or not page:
        return False
    if page == "portal":
        return True
    if db is not None:
        row = db.get(AppState, ROLE_PAGE_ACCESS_KEY)
        if row and row.value:
            try:
                parsed = json.loads(row.value)
            except json.JSONDecodeError:
                parsed = None
            pages = parsed.get(key) if isinstance(parsed, dict) else None
            if isinstance(pages, list):
                selected = {str(page_id).strip() for page_id in pages}
                if "compliance" in selected:
                    selected.add("compliance_providers")
                return page in selected
    return page in DEFAULT_PAGE_ACCESS.get(key, {"portal"})


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("Authorization") or ""
    token = None
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    subject = decode_access_token(token, settings.JWT_SECRET)
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = db.query(User).filter(User.email == subject).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User disabled")
    return user


def require_role(*roles: UserRole):
    def _dep(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        if roles and UserRole.ADMIN in roles and has_admin_access(user.role, db):
            return user
        if roles and user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return _dep


def require_page_access(page_id: str):
    def _dep(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        if not has_page_access(user.role, page_id, db):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient page access")
        return user

    return _dep
