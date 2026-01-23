from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import AuditAction, ThreadTicket
from app.services.audit import add_audit


def run_sla_escalations(db: Session) -> int:
    """Mark overdue tickets as escalated (MVP escalation path).

    If a ticket has sla_due_at in the past and escalation_level==0,
    we set escalation_level=1 and escalated_at=now.
    """
    now = datetime.utcnow()
    q = (
        db.query(ThreadTicket)
        .filter(ThreadTicket.sla_due_at.isnot(None))
        .filter(ThreadTicket.sla_due_at < now)
        .filter(ThreadTicket.escalation_level == 0)
    )
    items = q.all()
    for t in items:
        t.escalation_level = 1
        t.escalated_at = now
        t.updated_at = now
        add_audit(
            db,
            thread_id=t.thread_id,
            action=AuditAction.ESCALATED,
            actor_user_id=None,
            detail={"sla_due_at": t.sla_due_at.isoformat() if t.sla_due_at else None},
        )
    if items:
        db.commit()
    return len(items)
