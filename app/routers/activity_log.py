from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.authz import get_current_user
from app.db import get_db
from app.models import ActivityLog, User
from app.routers.user_auth import _get_role_page_access, _role_key


router = APIRouter(prefix="/activity-log", tags=["activity-log"])


def _require_activity_access(user: User, db: Session) -> None:
    pages = set(_get_role_page_access(db).get(_role_key(user.role), []))
    if "activity" not in pages:
        raise HTTPException(status_code=403, detail="Insufficient page access")


def _parse_date(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    if len(value.strip()) <= 10:
        if end_of_day:
            return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        return parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed


def _detail_payload(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"detail": raw}
    return parsed if isinstance(parsed, dict) else {"detail": parsed}


def _activity_payload(row: ActivityLog) -> dict:
    actor_name = row.actor_name or (row.actor.name if row.actor else None)
    actor_email = row.actor_email or (row.actor.email if row.actor else None)
    actor_role = row.actor_role or (row.actor.role.value if row.actor and hasattr(row.actor.role, "value") else None)
    return {
        "id": row.id,
        "mailbox": row.mailbox,
        "actor_user_id": row.actor_user_id,
        "actor_name": actor_name,
        "actor_email": actor_email,
        "actor_role": actor_role,
        "action": row.action,
        "area": row.area,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "entity_label": row.entity_label,
        "method": row.method,
        "path": row.path,
        "status_code": row.status_code,
        "request_id": row.request_id,
        "ip_address": row.ip_address,
        "user_agent": row.user_agent,
        "detail": _detail_payload(row.detail),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("")
def list_activity_log(
    page: int = 1,
    page_size: int = 30,
    q: str | None = None,
    actor_user_id: int | None = None,
    area: str | None = None,
    mailbox: str | None = None,
    action: str | None = None,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_activity_access(user, db)

    base = db.query(ActivityLog)
    filtered = base

    if actor_user_id:
        filtered = filtered.filter(ActivityLog.actor_user_id == actor_user_id)
    if area:
        filtered = filtered.filter(ActivityLog.area == area.strip())
    if mailbox:
        filtered = filtered.filter(ActivityLog.mailbox == mailbox.strip().lower())
    if action:
        filtered = filtered.filter(ActivityLog.action.ilike(f"%{action.strip()}%"))

    start_dt = _parse_date(start)
    end_dt = _parse_date(end, end_of_day=True)
    if start_dt:
        filtered = filtered.filter(ActivityLog.created_at >= start_dt)
    if end_dt:
        filtered = filtered.filter(ActivityLog.created_at <= end_dt)

    if q and q.strip():
        like = f"%{q.strip()}%"
        filtered = filtered.filter(
            or_(
                ActivityLog.actor_name.ilike(like),
                ActivityLog.actor_email.ilike(like),
                ActivityLog.action.ilike(like),
                ActivityLog.area.ilike(like),
                ActivityLog.entity_type.ilike(like),
                ActivityLog.entity_id.ilike(like),
                ActivityLog.entity_label.ilike(like),
                ActivityLog.path.ilike(like),
                ActivityLog.detail.ilike(like),
            )
        )

    page = max(int(page or 1), 1)
    page_size = max(10, min(int(page_size or 30), 100))
    total = filtered.with_entities(func.count(ActivityLog.id)).scalar() or 0
    items = (
        filtered.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = now - timedelta(hours=24)
    summary_base = base
    if mailbox:
        summary_base = summary_base.filter(ActivityLog.mailbox == mailbox.strip().lower())

    area_rows = (
        summary_base.with_entities(ActivityLog.area, func.count(ActivityLog.id))
        .filter(ActivityLog.created_at >= yesterday)
        .group_by(ActivityLog.area)
        .order_by(func.count(ActivityLog.id).desc())
        .limit(8)
        .all()
    )

    return {
        "items": [_activity_payload(row) for row in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (page * page_size) < total,
        "summary": {
            "today": summary_base.filter(ActivityLog.created_at >= today).count(),
            "last_24h": summary_base.filter(ActivityLog.created_at >= yesterday).count(),
            "failed_24h": summary_base.filter(ActivityLog.created_at >= yesterday)
            .filter(ActivityLog.status_code >= 400)
            .count(),
            "staff_24h": summary_base.filter(ActivityLog.created_at >= yesterday)
            .with_entities(func.count(func.distinct(ActivityLog.actor_user_id)))
            .scalar()
            or 0,
            "areas_24h": [{"area": area or "Portal", "count": count} for area, count in area_rows],
        },
    }


@router.get("/areas")
def list_activity_areas(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_activity_access(user, db)
    rows = (
        db.query(ActivityLog.area)
        .filter(ActivityLog.area.isnot(None))
        .group_by(ActivityLog.area)
        .order_by(ActivityLog.area.asc())
        .all()
    )
    return {"items": [row[0] for row in rows if row[0]]}
