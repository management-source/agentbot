from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import AuditAction, ThreadTicketAudit


def add_audit(
    db: Session,
    thread_id: str,
    action: AuditAction,
    actor_user_id: Optional[int] = None,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    row = ThreadTicketAudit(
        thread_id=thread_id,
        action=action,
        actor_user_id=actor_user_id,
        detail=json.dumps(detail, ensure_ascii=False) if detail else None,
        created_at=datetime.utcnow(),
    )
    db.add(row)
