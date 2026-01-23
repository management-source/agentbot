from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.authz import get_current_user, require_role
from app.config import settings
from app.db import get_db
from app.models import User, UserRole
from app.schemas import UserOut
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/user-auth", tags=["user-auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str


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


class UpdateUserIn(BaseModel):
    name: str | None = None
    role: UserRole | None = None
    password: str | None = None
    is_active: bool | None = None


def _to_user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, name=u.name, role=u.role, is_active=u.is_active)


@router.post("/login", response_model=LoginOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(subject=user.email, secret=settings.JWT_SECRET)
    return LoginOut(access_token=token, user=_to_user_out(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _to_user_out(user)


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    users = db.query(User).order_by(User.name.asc()).all()
    return [_to_user_out(u) for u in users]


@router.post("/users", response_model=UserOut)
def create_user(
    payload: CreateUserIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    existing = db.query(User).filter(User.email == payload.email.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    u = User(
        email=payload.email.lower(),
        name=payload.name.strip(),
        role=payload.role,
        is_active=payload.is_active,
        password_hash=hash_password(payload.password),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
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

    if payload.name is not None:
        u.name = payload.name.strip()
    if payload.role is not None:
        u.role = payload.role
    if payload.is_active is not None:
        u.is_active = payload.is_active
    if payload.password:
        u.password_hash = hash_password(payload.password)

    u.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(u)
    return _to_user_out(u)
