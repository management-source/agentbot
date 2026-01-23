from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from pydantic import BaseModel

from app.db import get_db
from app.authz import get_current_user, require_role
from app.models import (
    ThreadTicket,
    TicketStatus,
    AppState,
    TicketCategory,
    User,
    UserRole,
    ThreadTicketNote,
    ThreadTicketAudit,
    AuditAction,
)
from app.schemas import (
    TicketListOut,
    TicketOut,
    DraftAckOut,
    SendAckIn,
    TicketNoteOut,
    TicketAuditOut,
)
from app.services.audit import add_audit
from app.services.ai_reply import draft_acknowledgement
from app.services.gmail_send import send_reply_in_thread

router = APIRouter()


SLA_HOURS = {
    TicketCategory.MAINTENANCE: {"high": 24, "medium": 48, "low": 72},
    TicketCategory.RENT_ARREARS: {"high": 12, "medium": 24, "low": 48},
    TicketCategory.LEASING: {"high": 24, "medium": 48, "low": 72},
    TicketCategory.COMPLIANCE: {"high": 24, "medium": 48, "low": 72},
    TicketCategory.SALES: {"high": 24, "medium": 48, "low": 72},
    TicketCategory.GENERAL: {"high": 48, "medium": 72, "low": 120},
}


def _compute_sla_due_at(category: TicketCategory, priority: str, base_time: datetime | None) -> datetime | None:
    if not base_time:
        return None
    pr = (priority or "medium").strip().lower()
    hours = SLA_HOURS.get(category, SLA_HOURS[TicketCategory.GENERAL]).get(pr, 72)
    return base_time + timedelta(hours=hours)

class StatusUpdate(BaseModel):
    status: TicketStatus
    
def _tab_filter(q, tab: str):
    tab = (tab or "all").lower().strip()

    if tab == "not_replied":
        return q.filter(ThreadTicket.is_not_replied == True)

    if tab == "pending":
        return q.filter(ThreadTicket.status == TicketStatus.PENDING)

    if tab == "in_progress":
        return q.filter(ThreadTicket.status == TicketStatus.IN_PROGRESS)

    if tab == "responded":
        return q.filter(ThreadTicket.status == TicketStatus.RESPONDED)

    if tab == "no_reply_needed":
        return q.filter(ThreadTicket.status == TicketStatus.NO_REPLY_NEEDED)

    if tab == "all":
        # KEEP ALL CLEAN: show only actionable tickets
        # Option B: Pending + In Progress
        return q.filter(ThreadTicket.status.in_([TicketStatus.PENDING, TicketStatus.IN_PROGRESS]))

        # If you want Option A (Pending only), use this instead:
        # return q.filter(ThreadTicket.status == TicketStatus.PENDING)

    return q  # fallback for unknown tab values



@router.get("", response_model=TicketListOut)
def list_tickets(
    tab: str = "all",
    category: TicketCategory | None = None,
    assignee_user_id: int | None = None,
    mine: bool = False,
    overdue: bool = False,
    limit: int = 50,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List tickets with optional date filtering.

    - start/end are expected as YYYY-MM-DD (from <input type="date">)
    - filtering is applied against ThreadTicket.last_message_at
    """
    q = db.query(ThreadTicket)
    q = _tab_filter(q, tab)

    if category:
        q = q.filter(ThreadTicket.category == category)

    if mine:
        q = q.filter(ThreadTicket.assignee_user_id == user.id)
    elif assignee_user_id is not None:
        q = q.filter(ThreadTicket.assignee_user_id == assignee_user_id)

    if overdue:
        now = datetime.utcnow()
        q = q.filter(ThreadTicket.sla_due_at.isnot(None)).filter(ThreadTicket.sla_due_at < now)

    # Optional date filtering (inclusive)
    try:
        if start:
            start_dt = datetime.fromisoformat(start).replace(hour=0, minute=0, second=0, microsecond=0)
            q = q.filter(ThreadTicket.last_message_at >= start_dt)
        if end:
            end_dt = datetime.fromisoformat(end).replace(hour=23, minute=59, second=59, microsecond=999999)
            q = q.filter(ThreadTicket.last_message_at <= end_dt)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    q = q.order_by(ThreadTicket.last_message_at.desc().nullslast()).limit(limit)
    items = q.all()

    # Counters for top tiles / tabs
    counts = {
        "not_replied": db.query(func.count(ThreadTicket.thread_id)).filter(ThreadTicket.is_not_replied == True).scalar() or 0,
        "pending": db.query(func.count(ThreadTicket.thread_id)).filter(ThreadTicket.status == TicketStatus.PENDING).scalar() or 0,
        "in_progress": db.query(func.count(ThreadTicket.thread_id)).filter(ThreadTicket.status == TicketStatus.IN_PROGRESS).scalar() or 0,
        "responded": db.query(func.count(ThreadTicket.thread_id)).filter(ThreadTicket.status == TicketStatus.RESPONDED).scalar() or 0,
        "no_reply_needed": db.query(func.count(ThreadTicket.thread_id)).filter(ThreadTicket.status == TicketStatus.NO_REPLY_NEEDED).scalar() or 0,
        "all": db.query(func.count(ThreadTicket.thread_id)).scalar() or 0,
    }

    return TicketListOut(
        items=[TicketOut.model_validate(t.__dict__) for t in items],
        counts=counts,
    )

@router.patch("/{thread_id}/status")
def update_status(
    thread_id: str,
    payload: StatusUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.get(ThreadTicket, thread_id)
    if not t:
        raise HTTPException(404, "Ticket not found")

    old = t.status
    t.status = payload.status

    # Optional: keep ALL clean + not_replied logic
    if payload.status in [TicketStatus.RESPONDED, TicketStatus.NO_REPLY_NEEDED]:
        t.is_not_replied = False

    t.updated_at = datetime.utcnow()
    add_audit(db, thread_id=thread_id, action=AuditAction.STATUS_CHANGED, actor_user_id=user.id, detail={
        "from": old.value,
        "to": t.status.value,
    })
    db.commit()
    return {"ok": True, "thread_id": thread_id, "status": t.status.value}

@router.post("/{thread_id}/draft-ack", response_model=DraftAckOut)
def draft_ack(thread_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = db.get(ThreadTicket, thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")

    subject, body = draft_acknowledgement(
        from_name=t.from_name,
        subject=t.subject or "",
        snippet=t.snippet or "",
    )
    return DraftAckOut(subject=subject, body=body)

@router.post("/{thread_id}/send-ack")
def send_ack(thread_id: str, payload: SendAckIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = db.get(ThreadTicket, thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")

    send_reply_in_thread(
        db=db,
        thread_id=thread_id,
        to_email=t.from_email,
        subject=payload.subject,
        body=payload.body,
    )

    # Update ticket bookkeeping
    from datetime import datetime
    t.ack_sent_at = datetime.utcnow()
    t.last_from_me = True  # we just replied
    if payload.mark_as_responded:
        t.status = TicketStatus.RESPONDED

    # after send, this should not be not-replied
    t.is_not_replied = False

    add_audit(db, thread_id=thread_id, action=AuditAction.REPLIED, actor_user_id=user.id, detail={"subject": payload.subject})
    db.commit()
    return {"ok": True}


class AssignIn(BaseModel):
    assignee_user_id: int | None = None


@router.patch("/{thread_id}/assign")
def assign_ticket(
    thread_id: str,
    payload: AssignIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.get(ThreadTicket, thread_id)
    if not t:
        raise HTTPException(404, "Ticket not found")
    assignee = None
    if payload.assignee_user_id is not None:
        assignee = db.get(User, payload.assignee_user_id)
        if not assignee or not assignee.is_active:
            raise HTTPException(400, "Assignee not found")
    old = t.assignee_user_id
    t.assignee_user_id = payload.assignee_user_id
    t.updated_at = datetime.utcnow()
    add_audit(
        db,
        thread_id=thread_id,
        action=AuditAction.ASSIGNED,
        actor_user_id=user.id,
        detail={"from": old, "to": t.assignee_user_id},
    )
    db.commit()
    return {"ok": True}


class CategoryIn(BaseModel):
    category: TicketCategory


@router.patch("/{thread_id}/category")
def set_category(
    thread_id: str,
    payload: CategoryIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.get(ThreadTicket, thread_id)
    if not t:
        raise HTTPException(404, "Ticket not found")

    old = t.category
    t.category = payload.category

    # SLA due is based on last_message_at (fallback now)
    base_time = t.last_message_at or datetime.utcnow()
    t.sla_due_at = _compute_sla_due_at(t.category, t.priority, base_time)

    t.updated_at = datetime.utcnow()
    add_audit(
        db,
        thread_id=thread_id,
        action=AuditAction.CATEGORY_SET,
        actor_user_id=user.id,
        detail={"from": old.value if old else None, "to": t.category.value, "sla_due_at": t.sla_due_at.isoformat() if t.sla_due_at else None},
    )
    db.commit()
    return {"ok": True, "category": t.category.value, "sla_due_at": t.sla_due_at}


class NoteIn(BaseModel):
    body: str


@router.get("/{thread_id}/notes", response_model=list[TicketNoteOut])
def list_notes(thread_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    notes = (
        db.query(ThreadTicketNote)
        .filter(ThreadTicketNote.thread_id == thread_id)
        .order_by(ThreadTicketNote.created_at.asc())
        .all()
    )
    out = []
    for n in notes:
        out.append(
            TicketNoteOut(
                id=n.id,
                thread_id=n.thread_id,
                author_user_id=n.author_user_id,
                author_name=n.author.name if n.author else None,
                body=n.body,
                created_at=n.created_at,
            )
        )
    return out


@router.post("/{thread_id}/notes", response_model=TicketNoteOut)
def add_note(thread_id: str, payload: NoteIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(400, "Note body required")
    note = ThreadTicketNote(thread_id=thread_id, author_user_id=user.id, body=body, created_at=datetime.utcnow())
    db.add(note)
    db.flush()  # assigns note.id
    add_audit(db, thread_id=thread_id, action=AuditAction.NOTE_ADDED, actor_user_id=user.id, detail={"note_id": note.id})
    db.commit()
    db.refresh(note)
    return TicketNoteOut(
        id=note.id,
        thread_id=note.thread_id,
        author_user_id=note.author_user_id,
        author_name=user.name,
        body=note.body,
        created_at=note.created_at,
    )


@router.get("/{thread_id}/audit", response_model=list[TicketAuditOut])
def list_audit(thread_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(ThreadTicketAudit)
        .filter(ThreadTicketAudit.thread_id == thread_id)
        .order_by(ThreadTicketAudit.created_at.asc())
        .limit(200)
        .all()
    )
    out = []
    for r in rows:
        out.append(
            TicketAuditOut(
                id=r.id,
                thread_id=r.thread_id,
                action=r.action.value,
                actor_user_id=r.actor_user_id,
                actor_name=r.actor.name if r.actor else None,
                detail=r.detail,
                created_at=r.created_at,
            )
        )
    return out

class FlushIn(BaseModel):
    confirm: str

@router.post("/admin/flush")
def flush_database(
    payload: FlushIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Delete all tickets and sync watermarks (does not remove Google connection).

    Safety: requires confirm == 'FLUSH'.
    """
    if (payload.confirm or "").strip().upper() != "FLUSH":
        raise HTTPException(status_code=400, detail="Confirmation required. Send confirm='FLUSH'.")

    # Delete tickets
    db.query(ThreadTicket).delete(synchronize_session=False)

    # Clear sync-related state (keep other state keys if you add them later)
    db.query(AppState).delete(synchronize_session=False)

    db.commit()
    return {"ok": True, "message": "Database flushed (tickets and state cleared)."}
