from __future__ import annotations

from datetime import datetime
from sqlalchemy.orm import Session

from app.models import AppState


def get_state(db: Session, key: str) -> str | None:
    row = db.get(AppState, key)
    return row.value if row else None


def set_state(db: Session, key: str, value: str) -> None:
    row = db.get(AppState, key)
    if row is None:
        row = AppState(key=key, value=value)
        db.add(row)
    else:
        row.value = value
        row.updated_at = datetime.utcnow()
