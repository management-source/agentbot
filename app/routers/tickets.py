from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
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
    DraftAiReplyOut,
    SendAckIn,
    TicketNoteOut,
    TicketAuditOut,
    AiAnalyzeOut,
)
from app.services.audit import add_audit
from app.services.ai_reply import draft_acknowledgement
from app.services.ai_assistant import triage_email, content_hash, draft_context_reply
from app.services.gmail_send import send_reply_in_thread
from app.services.gmail_client import get_gmail_service, gmail_user_id
from app.services.gmail_parse import extract_message_body

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
    ai_category: str | None = None,
    assignee_user_id: int | None = None,
    query: str | None = None,
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

    # AI category filter (preferred)
    if ai_category:
        q = q.filter(ThreadTicket.ai_category == ai_category)

    # Full-text-ish search across key fields
    if query:
        like = f"%{query.strip()}%"
        q = q.filter(
            or_(
                ThreadTicket.subject.ilike(like),
                ThreadTicket.snippet.ilike(like),
                ThreadTicket.from_email.ilike(like),
                ThreadTicket.from_name.ilike(like),
            )
        )

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

    # --- AI enrichment (on-demand) ---
    # We keep this best-effort and bounded to avoid slow pages/cost spikes.
    ai_updates = 0
    for t in items:
        if ai_updates >= 10:
            break
        subj = t.subject or ""
        snip = t.snippet or ""
        src_hash = content_hash(subj, snip)
        if not t.ai_category or t.ai_source_hash != src_hash:
            res = triage_email(subj, snip, "")
            t.ai_category = res.ai_category
            t.ai_urgency = res.urgency
            t.ai_confidence = res.confidence_percent
            t.ai_reasons = json.dumps(res.reasons)
            t.ai_summary = res.summary
            t.ai_source_hash = src_hash
            t.ai_last_scored_at = datetime.utcnow()
            ai_updates += 1
    if ai_updates:
        db.commit()

    # Counters for top tiles / tabs
    counts = {
        "not_replied": db.query(func.count(ThreadTicket.thread_id)).filter(ThreadTicket.is_not_replied == True).scalar() or 0,
        "pending": db.query(func.count(ThreadTicket.thread_id)).filter(ThreadTicket.status == TicketStatus.PENDING).scalar() or 0,
        "in_progress": db.query(func.count(ThreadTicket.thread_id)).filter(ThreadTicket.status == TicketStatus.IN_PROGRESS).scalar() or 0,
        "responded": db.query(func.count(ThreadTicket.thread_id)).filter(ThreadTicket.status == TicketStatus.RESPONDED).scalar() or 0,
        "no_reply_needed": db.query(func.count(ThreadTicket.thread_id)).filter(ThreadTicket.status == TicketStatus.NO_REPLY_NEEDED).scalar() or 0,
        "all": db.query(func.count(ThreadTicket.thread_id)).scalar() or 0,
    }

    # AI KPI: urgent
    counts["urgent_ai"] = db.query(func.count(ThreadTicket.thread_id)).filter(ThreadTicket.ai_urgency >= 4).scalar() or 0

    return TicketListOut(
        items=[TicketOut.model_validate(t) for t in items],
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
        ai_category=t.ai_category,
        ai_urgency=t.ai_urgency,
    )
    return DraftAckOut(subject=subject, body=body)


@router.post("/{thread_id}/draft-reply", response_model=DraftAiReplyOut)
def draft_reply(thread_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Generate a context-aware draft reply for a ticket.

    This uses AI when configured, and a deterministic fallback template otherwise.
    """
    t = db.get(ThreadTicket, thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")

    subj = t.subject or ""
    snip = t.snippet or ""
    # Prefer AI category if present; otherwise fall back to legacy category.
    cat = t.ai_category or (t.category.value.lower() if hasattr(t.category, "value") else str(t.category).lower())

    out = draft_context_reply(
        from_name=t.from_name,
        subject=subj,
        snippet=snip,
        category=cat,
        tone="professional",
        constraints=[
            "Be factual; do not invent details.",
            "If dates, amounts, or addresses are missing, ask for them.",
            "Avoid legal advice; keep to process and next steps.",
        ],
    )

    return DraftAiReplyOut(subject=out.subject, body=out.body, meta={
        "ai_category": t.ai_category,
        "ai_urgency": t.ai_urgency,
        "ai_confidence": t.ai_confidence,
    })


@router.post("/{thread_id}/ai-analyze", response_model=AiAnalyzeOut)
def ai_analyze(thread_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Run AI triage on a single ticket and persist the result."""
    t = db.get(ThreadTicket, thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")

    subj = t.subject or ""
    snip = t.snippet or ""
    src_hash = content_hash(subj, snip)
    res = triage_email(subj, snip, "")

    t.ai_category = res.ai_category
    t.ai_urgency = res.urgency
    t.ai_confidence = res.confidence_percent
    t.ai_reasons = json.dumps(res.reasons)
    t.ai_summary = res.summary
    t.ai_source_hash = src_hash
    t.ai_last_scored_at = datetime.utcnow()
    db.commit()

    return AiAnalyzeOut(
        ai_category=res.ai_category,
        ticket_category=res.ticket_category,
        ai_urgency=res.urgency,
        ai_confidence=res.confidence_percent,
        ai_reasons=res.reasons,
        ai_summary=res.summary or "",
    )


@router.post("/{thread_id}/draft-ai-reply", response_model=DraftAiReplyOut)
def draft_ai_reply(thread_id: str, tone: str = "neutral", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Draft a context-aware reply using the latest message body.

    This is designed as a human-in-the-loop tool; it does not send email.
    """
    t = db.get(ThreadTicket, thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # Fetch last message body (plain text) from Gmail.
    service = get_gmail_service(db)
    th = (
        service.users()
        .threads()
        .get(userId=gmail_user_id(), id=thread_id, format="full")
        .execute()
    )
    messages = th.get("messages", []) or []
    last_body_text = ""
    if messages:
        last = messages[-1]
        payload = last.get("payload") or {}
        body_info = extract_message_body(payload)
        last_body_text = (body_info.get("body_text") or "").strip()
        if not last_body_text:
            last_body_text = (last.get("snippet") or "").strip()

    # Ensure we have triage info.
    subj = t.subject or ""
    snip = t.snippet or ""
    if not t.ai_category:
        res = triage_email(subj, snip, last_body_text)
        t.ai_category = res.ai_category
        t.ai_urgency = res.urgency
        t.ai_confidence = res.confidence_percent
        t.ai_reasons = json.dumps(res.reasons)
        t.ai_summary = res.summary
        t.ai_source_hash = content_hash(subj, snip)
        t.ai_last_scored_at = datetime.utcnow()

    reply_subject, reply_body, meta = draft_context_reply(
        from_name=t.from_name,
        from_email=t.from_email,
        subject=subj,
        last_message_text=last_body_text or snip,
        ai_category=t.ai_category or "general",
        urgency=int(t.ai_urgency or 1),
        tone=tone,
    )

    # Cache draft in DB (optional convenience)
    t.ai_draft_subject = reply_subject
    t.ai_draft_body = reply_body
    t.ai_draft_updated_at = datetime.utcnow()
    db.commit()

    return DraftAiReplyOut(subject=reply_subject, body=reply_body, meta=meta)


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
