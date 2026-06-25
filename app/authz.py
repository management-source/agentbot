from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User, UserRole
from app.security import decode_access_token


ADMIN_ACCESS_ROLES = {UserRole.ADMIN, UserRole.PM, UserRole.SALES}


def has_admin_access(role: UserRole | str | None) -> bool:
    if isinstance(role, UserRole):
        return role in ADMIN_ACCESS_ROLES
    value = str(role or "").strip().upper()
    return value in {r.value for r in ADMIN_ACCESS_ROLES}


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
    def _dep(user: User = Depends(get_current_user)) -> User:
        if roles and UserRole.ADMIN in roles and has_admin_access(user.role):
            return user
        if roles and user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return _dep
