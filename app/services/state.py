from __future__ import annotations

from datetime import datetime
from sqlalchemy.orm import Session

from app.models import AppState


def _ns(mailbox: str, key: str) -> str:
    mailbox = (mailbox or "").strip().lower()
    key = (key or "").strip()
    return f"{mailbox}:{key}"


def get_state(db: Session, key: str, mailbox: str) -> str | None:
    row = db.get(AppState, _ns(mailbox, key))
    return row.value if row else None


def set_state(db: Session, key: str, value: str, mailbox: str) -> None:
    ns_key = _ns(mailbox, key)
    row = db.get(AppState, ns_key)
    if row is None:
        row = AppState(key=ns_key, value=value)
        db.add(row)
    else:
        row.value = value
        row.updated_at = datetime.utcnow()
