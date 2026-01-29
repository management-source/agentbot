from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from sqlalchemy.sql import exists
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
    BlacklistedSender,
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
from app.services.ai_assistant import draft_context_reply
from app.services.state import get_state
from app.config import settings
from app.services.gmail_send import send_reply_in_thread
from app.services.gmail_client import get_gmail_service, gmail_user_id
from app.services.gmail_parse import extract_message_body
from app.schemas import DraftAiIn

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

    if tab in ("awaiting_reply", "awaiting"):
        # Canonical KPI tab
        return q.filter(ThreadTicket.is_not_replied == True)

    if tab == "in_progress":
        return q.filter(ThreadTicket.status == TicketStatus.IN_PROGRESS)

    if tab == "responded":
        return q.filter(ThreadTicket.status == TicketStatus.RESPONDED)

    if tab == "no_reply_needed":
        return q.filter(ThreadTicket.status == TicketStatus.NO_REPLY_NEEDED)

    if tab == "all":
        return q

    return q  # fallback for unknown tab values



@router.get("", response_model=TicketListOut)
def list_tickets(
    tab: str = "awaiting_reply",
    category: TicketCategory | None = None,
    ai_category: str | None = None,
    query: str | None = None,
    overdue: bool = False,
    page: int = 1,
    page_size: int = 25,
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

    # Always hide blacklisted senders.
    q = q.filter(
        ~exists().where(BlacklistedSender.email == func.lower(ThreadTicket.from_email))
    )

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

    # Assignment is removed; no assignee filters.

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

    # Pagination
    page = max(int(page or 1), 1)
    page_size = int(page_size or 25)
    page_size = 10 if page_size < 10 else page_size
    page_size = 100 if page_size > 100 else page_size

    total = q.with_entities(func.count(ThreadTicket.thread_id)).scalar() or 0

    q = q.order_by(ThreadTicket.last_message_at.desc().nullslast())
    q = q.offset((page - 1) * page_size).limit(page_size)
    items = q.all()

    # IMPORTANT: Do not call AI during list/fetch operations.
    # AI is invoked only when the user explicitly requests it (e.g., AI Draft).

    # Counters for top tiles / tabs
    # KPI counts (exclude blacklisted senders to match list behavior)
    base = db.query(ThreadTicket).filter(
        ~exists().where(BlacklistedSender.email == func.lower(ThreadTicket.from_email))
    )
    counts = {
        "awaiting_reply": base.filter(ThreadTicket.is_not_replied == True).count(),
        "in_progress": base.filter(ThreadTicket.status == TicketStatus.IN_PROGRESS).count(),
        "responded": base.filter(ThreadTicket.status == TicketStatus.RESPONDED).count(),
        "no_reply_needed": base.filter(ThreadTicket.status == TicketStatus.NO_REPLY_NEEDED).count(),
    }

    return TicketListOut(
        items=[TicketOut.model_validate(t) for t in items],
        counts=counts,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,
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
    """Generate a deterministic (non-AI) quick-reply draft.

    IMPORTANT: To avoid OpenAI rate-limit failures and to keep "Fetch now" reliable,
    this endpoint NEVER calls OpenAI. Use /draft-ai-reply for AI drafting.
    """
    t = db.get(ThreadTicket, thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")

    subj = (t.subject or "").strip()
    safe_subject = subj or "(no subject)"
    reply_subject = f"Re: {safe_subject}"

    name = (t.from_name or "").strip()
    greeting = f"Hello {name}," if name else "Hello,"

    # Prefer AI category if present; otherwise fall back to legacy category.
    cat = (t.ai_category or "").strip() or (
        (t.category.value.lower() if hasattr(t.category, "value") else str(t.category).lower())
    )

    if cat == "maintenance":
        body = (
            f"{greeting}\n\n"
            "Thank you for your email. We have noted the maintenance request and will review the details. "
            "We will be in touch shortly with the next steps (including arranging access if required).\n\n"
            "Kind regards,"
        )
    elif cat == "rent_arrears":
        body = (
            f"{greeting}\n\n"
            "Thank you for your email. We have noted your message regarding rent and will review the tenant ledger. "
            "We will follow up shortly with an update.\n\n"
            "Kind regards,"
        )
    elif cat == "compliance":
        body = (
            f"{greeting}\n\n"
            "Thank you for your email. We have noted the compliance matter and will review what is required. "
            "We will follow up shortly with confirmation of next steps.\n\n"
            "Kind regards,"
        )
    elif cat == "lease_renewal":
        body = (
            f"{greeting}\n\n"
            "Thank you for your email. We have received your message regarding the lease/tenancy and will review the details. "
            "We will be in touch shortly with an update.\n\n"
            "Kind regards,"
        )
    else:
        body = (
            f"{greeting}\n\n"
            "Thank you for your email. We have received your message and will respond shortly.\n\n"
            "Kind regards,"
        )

    signature = (get_state(db, "signature_text") or settings.DEFAULT_SIGNATURE or "").strip()
    if signature:
        body = body.rstrip() + "\n\n" + signature + "\n"

    return DraftAiReplyOut(subject=reply_subject, body=body, meta={
        "ai_category": t.ai_category,
        "ai_urgency": t.ai_urgency,
        "ai_confidence": t.ai_confidence,
        "used_ai": False,
    })


@router.post("/{thread_id}/ai-analyze", response_model=AiAnalyzeOut)
def ai_analyze(thread_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """AI triage is disabled.

    Rationale: avoid background AI calls and AI-based categorization.
    AI is only used when the user explicitly requests an AI draft.
    """
    raise HTTPException(status_code=410, detail="AI analysis is currently disabled.")

class DraftAiIn(BaseModel):
    tone: str = "neutral"
    # Frontend historically sent `extra_context`. Some older UI versions may send
    # `additional_info`. Support both for backwards compatibility.
    extra_context: str | None = None
    additional_info: str | None = None


@router.post("/{thread_id}/draft-ai-reply", response_model=DraftAiReplyOut)
def draft_ai_reply(
    thread_id: str,
    tone: str = "neutral",
    payload: DraftAiIn | None = Body(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Draft a context-aware reply using the latest message body (human-in-the-loop)."""

    # Read request body safely
    req_tone = ((payload.tone if payload and payload.tone else None) or tone or "neutral").strip()
    additional_info = None
    if payload:
        additional_info = (payload.additional_info or payload.extra_context)

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
        gmail_payload = last.get("payload") or {}  # IMPORTANT: do NOT overwrite request payload
        body_info = extract_message_body(gmail_payload)
        last_body_text = (body_info.get("body_text") or "").strip()
        if not last_body_text:
            last_body_text = (last.get("snippet") or "").strip()

    subj = t.subject or ""
    snip = t.snippet or ""

    # No AI triage: keep AI requests limited to explicit draft generation.
    # We pass a neutral/default category and urgency into the drafting prompt.
    if not settings.OPENAI_API_KEY:
        raise HTTPException(status_code=400, detail="AI drafting is not configured. Set OPENAI_API_KEY.")

    signature = (get_state(db, "signature_text") or settings.DEFAULT_SIGNATURE or "").strip()

    try:
        reply_subject, reply_body, meta = draft_context_reply(
            from_name=t.from_name or "",
            from_email=t.from_email or "",
            subject=subj,
            last_message_text=(last_body_text or snip),
            ai_category="general",
            urgency=3,
            tone=req_tone,
            extra_context=additional_info,   # <-- map additional info into extra_context
            signature=signature,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

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
