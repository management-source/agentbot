from __future__ import annotations

from datetime import datetime
from sqlalchemy.orm import Session

from app.models import AppState


def _k(mailbox_id: str | None, key: str) -> str:
    if mailbox_id:
        mid = mailbox_id.strip().lower()
        if ':' in key:
            return key
        return f"{mid}:{key}"
    return key


def get_state(db: Session, key: str, mailbox_id: str | None = None) -> str | None:
    row = db.get(AppState, _k(mailbox_id, key))
    return row.value if row else None


def set_state(db: Session, key: str, value: str, mailbox_id: str | None = None) -> None:
    row = db.get(AppState, _k(mailbox_id, key))
    if row is None:
        row = AppState(key=_k(mailbox_id, key), value=value)
        db.add(row)
    else:
        row.value = value
        row.updated_at = datetime.utcnow()
