from __future__ import annotations

import json
import io
from fastapi import APIRouter, Depends, HTTPException, Body, File, Form, UploadFile
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, case, func, or_
from sqlalchemy.sql import exists
from datetime import datetime, timedelta
from pydantic import BaseModel

from app.db import get_db
from app.authz import get_current_user, require_role
from app.deps import get_current_mailbox
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
    DismissedEmailThread,
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
from app.services.gmail_send import send_reply_in_thread, OutgoingAttachment
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


class AssignmentUpdate(BaseModel):
    assignee_user_id: int | None = None


class PurgeNoReplyNeededIn(BaseModel):
    confirm: str


def _get_ticket(db: Session, thread_id: str, mailbox: str) -> ThreadTicket:
    # Primary path: namespaced internal ticket id.
    t = db.get(ThreadTicket, thread_id)
    if t and t.mailbox == mailbox:
        return t

    # Back-compat path: allow raw Gmail thread id in API path.
    t = (
        db.query(ThreadTicket)
        .filter(ThreadTicket.mailbox == mailbox)
        .filter(ThreadTicket.gmail_thread_id == thread_id)
        .first()
    )
    if t:
        return t

    raise HTTPException(status_code=404, detail="Ticket not found")


def _tab_filter(q, tab: str):
    tab = (tab or "all").lower().strip()

    if tab in ("awaiting_reply", "awaiting"):
        # Canonical action queue: new unreplied emails that have not been picked up yet.
        return q.filter(ThreadTicket.is_not_replied == True).filter(
            ThreadTicket.status == TicketStatus.PENDING
        )

    if tab == "in_progress":
        return q.filter(ThreadTicket.status == TicketStatus.IN_PROGRESS)

    if tab == "responded":
        return q.filter(ThreadTicket.status == TicketStatus.RESPONDED)

    if tab == "no_reply_needed":
        return q.filter(ThreadTicket.status == TicketStatus.NO_REPLY_NEEDED)

    if tab == "all":
        return q

    return q  # fallback for unknown tab values


def _user_payload(u: User) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "role": u.role.value if hasattr(u.role, "value") else str(u.role),
        "avatar_url": u.avatar_url,
        "is_active": bool(u.is_active),
    }


@router.get("/assignees")
def list_assignees(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    users = (
        db.query(User)
        .filter(User.is_active == True)
        .order_by(User.name.asc(), User.email.asc())
        .all()
    )
    return {"items": [_user_payload(u) for u in users]}



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
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List tickets with optional date filtering.

    - start/end are expected as YYYY-MM-DD (from <input type="date">)
    - filtering is applied against ThreadTicket.last_message_at
    """
    q = db.query(ThreadTicket).options(joinedload(ThreadTicket.assignee)).filter(ThreadTicket.mailbox == mailbox)
    q = _tab_filter(q, tab)

    # Always hide blacklisted senders.
    q = q.filter(
        ~exists()
        .where(BlacklistedSender.mailbox == mailbox)
        .where(BlacklistedSender.email == func.lower(ThreadTicket.from_email))
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

    # Assignment is displayed on tickets; the list remains filtered by queue/status/search.

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
        ThreadTicket.mailbox == mailbox
    ).filter(
        ~exists()
        .where(BlacklistedSender.mailbox == mailbox)
        .where(BlacklistedSender.email == func.lower(ThreadTicket.from_email))
    )
    count_row = base.with_entities(
        func.count(ThreadTicket.thread_id),
        func.sum(case((and_(ThreadTicket.is_not_replied == True, ThreadTicket.status == TicketStatus.PENDING), 1), else_=0)),
        func.sum(case((ThreadTicket.status == TicketStatus.IN_PROGRESS, 1), else_=0)),
        func.sum(case((ThreadTicket.status == TicketStatus.RESPONDED, 1), else_=0)),
        func.sum(case((ThreadTicket.status == TicketStatus.NO_REPLY_NEEDED, 1), else_=0)),
    ).one()
    counts = {
        "all": int(count_row[0] or 0),
        "awaiting_reply": int(count_row[1] or 0),
        "in_progress": int(count_row[2] or 0),
        "responded": int(count_row[3] or 0),
        "no_reply_needed": int(count_row[4] or 0),
    }

    return TicketListOut(
        items=[TicketOut.model_validate(t) for t in items],
        counts=counts,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,
    )


@router.post("/no-reply-needed/purge")
def purge_no_reply_needed(
    payload: PurgeNoReplyNeededIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Delete locally closed tickets without deleting any Gmail messages."""
    if (payload.confirm or "").strip().upper() != "PURGE":
        raise HTTPException(status_code=400, detail="Confirmation required. Send confirm='PURGE'.")

    tickets = (
        db.query(ThreadTicket)
        .filter(ThreadTicket.mailbox == mailbox)
        .filter(ThreadTicket.status == TicketStatus.NO_REPLY_NEEDED)
        .all()
    )
    if not tickets:
        return {"ok": True, "deleted": 0, "message": "There are no Reply Not Needed tickets to clear."}

    thread_ids = [ticket.thread_id for ticket in tickets]
    gmail_ids = [ticket.gmail_thread_id for ticket in tickets]
    existing = {
        row.gmail_thread_id: row
        for row in db.query(DismissedEmailThread)
        .filter(DismissedEmailThread.mailbox == mailbox)
        .filter(DismissedEmailThread.gmail_thread_id.in_(gmail_ids))
        .all()
    }
    now = datetime.utcnow()
    for ticket in tickets:
        dismissed = existing.get(ticket.gmail_thread_id)
        if dismissed is None:
            dismissed = DismissedEmailThread(
                mailbox=mailbox,
                gmail_thread_id=ticket.gmail_thread_id,
                last_message_id=ticket.last_message_id,
                dismissed_by_user_id=user.id,
                dismissed_at=now,
            )
            db.add(dismissed)
        else:
            dismissed.last_message_id = ticket.last_message_id
            dismissed.dismissed_by_user_id = user.id
            dismissed.dismissed_at = now

    db.query(ThreadTicketNote).filter(
        ThreadTicketNote.mailbox == mailbox,
        ThreadTicketNote.thread_id.in_(thread_ids),
    ).delete(synchronize_session=False)
    db.query(ThreadTicketAudit).filter(
        ThreadTicketAudit.mailbox == mailbox,
        ThreadTicketAudit.thread_id.in_(thread_ids),
    ).delete(synchronize_session=False)
    deleted = db.query(ThreadTicket).filter(
        ThreadTicket.mailbox == mailbox,
        ThreadTicket.status == TicketStatus.NO_REPLY_NEEDED,
    ).delete(synchronize_session=False)
    db.commit()
    return {
        "ok": True,
        "deleted": int(deleted or 0),
        "gmail_deleted": False,
        "message": f"Cleared {int(deleted or 0)} Reply Not Needed ticket(s). Gmail messages were not deleted.",
    }

@router.patch("/{thread_id}/status")
def update_status(
    thread_id: str,
    payload: StatusUpdate,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _get_ticket(db, thread_id, mailbox)

    old = t.status
    t.status = payload.status

    # Keep tab membership deterministic after manual status changes.
    if payload.status in [TicketStatus.RESPONDED, TicketStatus.NO_REPLY_NEEDED]:
        t.is_not_replied = False
    elif payload.status in [TicketStatus.PENDING, TicketStatus.IN_PROGRESS]:
        t.is_not_replied = True

    t.updated_at = datetime.utcnow()
    add_audit(
        db,
        mailbox=t.mailbox,
        thread_id=t.thread_id,
        action=AuditAction.STATUS_CHANGED,
        actor_user_id=user.id,
        detail={"from": old.value, "to": t.status.value},
    )
    db.commit()
    return {
        "ok": True,
        "thread_id": t.thread_id,
        "status": t.status.value,
        "is_not_replied": bool(t.is_not_replied),
    }


@router.patch("/{thread_id}/assignee")
def update_assignee(
    thread_id: str,
    payload: AssignmentUpdate,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _get_ticket(db, thread_id, mailbox)
    old_assignee_id = t.assignee_user_id
    assignee = None

    if payload.assignee_user_id is not None:
        assignee = db.get(User, payload.assignee_user_id)
        if not assignee or not assignee.is_active:
            raise HTTPException(status_code=400, detail="Assignee must be an active staff account.")
        t.assignee_user_id = assignee.id
        old_status = t.status
        if t.status == TicketStatus.PENDING:
            t.status = TicketStatus.IN_PROGRESS
            t.is_not_replied = True
    else:
        t.assignee_user_id = None
        old_status = t.status

    t.updated_at = datetime.utcnow()
    add_audit(
        db,
        mailbox=t.mailbox,
        thread_id=t.thread_id,
        action=AuditAction.ASSIGNED,
        actor_user_id=user.id,
        detail={
            "from_user_id": old_assignee_id,
            "to_user_id": t.assignee_user_id,
            "to_name": assignee.name if assignee else None,
        },
    )
    if old_status != t.status:
        add_audit(
            db,
            mailbox=t.mailbox,
            thread_id=t.thread_id,
            action=AuditAction.STATUS_CHANGED,
            actor_user_id=user.id,
            detail={"from": old_status.value, "to": t.status.value, "reason": "staff_assigned"},
        )
    db.commit()
    db.refresh(t)
    return {
        "ok": True,
        "thread_id": t.thread_id,
        "assignee_user_id": t.assignee_user_id,
        "assignee_name": t.assignee_name,
        "assignee_email": t.assignee_email,
        "assignee_avatar_url": t.assignee_avatar_url,
        "status": t.status.value,
        "is_not_replied": bool(t.is_not_replied),
    }


@router.post("/{thread_id}/draft-ack", response_model=DraftAckOut)
def draft_ack(
    thread_id: str,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _get_ticket(db, thread_id, mailbox)

    subject, body = draft_acknowledgement(
        from_name=t.from_name,
        subject=t.subject or "",
        snippet=t.snippet or "",
        ai_category=t.ai_category,
        ai_urgency=t.ai_urgency,
    )
    return DraftAckOut(subject=subject, body=body)


@router.post("/{thread_id}/draft-reply", response_model=DraftAiReplyOut)
def draft_reply(
    thread_id: str,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate a deterministic (non-AI) quick-reply draft.

    IMPORTANT: To avoid OpenAI rate-limit failures and to keep "Fetch now" reliable,
    this endpoint NEVER calls OpenAI. Use /draft-ai-reply for AI drafting.
    """
    t = _get_ticket(db, thread_id, mailbox)

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
            "Thank you for your email. We have noted your message regarding rent and will review the rent account records. "
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

    # IMPORTANT: Do NOT append the legacy plain-text signature here.
    # The application uses an app-managed HTML signature (with embedded images)
    # at send-time to avoid double signatures.

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
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Draft a context-aware reply using the latest message body (human-in-the-loop)."""

    # Read request body safely
    req_tone = ((payload.tone if payload and payload.tone else None) or tone or "neutral").strip()
    additional_info = None
    if payload:
        additional_info = (payload.additional_info or payload.extra_context)

    t = _get_ticket(db, thread_id, mailbox)

    # Fetch last message body (plain text) from Gmail.
    service = get_gmail_service(db, impersonate_user=t.mailbox)
    th = (
        service.users()
        .threads()
        .get(userId=gmail_user_id(), id=t.gmail_thread_id, format="full")
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

    signature = (get_state(db, "signature_text", t.mailbox) or settings.DEFAULT_SIGNATURE or "").strip()

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


@router.post("/transcribe-audio")
async def transcribe_audio(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
):
    """Transcribe short voice notes for AI Draft additional context."""
    if not settings.OPENAI_API_KEY:
        raise HTTPException(status_code=400, detail="Speech-to-text is not configured. Set OPENAI_API_KEY.")
    if not file:
        raise HTTPException(status_code=400, detail="No audio file uploaded.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Audio file too large (max 25MB).")

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        audio = io.BytesIO(data)
        audio.name = file.filename or "voice.webm"
        resp = client.audio.transcriptions.create(
            model=settings.OPENAI_TRANSCRIBE_MODEL,
            file=audio,
            language="en",
            temperature=0,
            response_format="text",
        )
        text = ""
        if isinstance(resp, str):
            text = resp.strip()
        else:
            text = (getattr(resp, "text", "") or "").strip()
        return {"text": text}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Transcription failed: {e}")

@router.post("/{thread_id}/send-ack")
def send_ack(
    thread_id: str,
    payload: SendAckIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _get_ticket(db, thread_id, mailbox)

    # Signature support:
    # - Text signature is appended for the plain-text part
    # - HTML signature (if present) is appended for the HTML part so images render
    signature_text = (get_state(db, "signature_text", t.mailbox) or settings.DEFAULT_SIGNATURE or "").strip()
    signature_html = (get_state(db, "signature_html", t.mailbox) or "").strip()

    body_text = (payload.body or "").rstrip()
    if signature_text and signature_text not in body_text:
        body_text = (body_text + "\n\n" + signature_text).strip()

    # Build a safe HTML variant from the user's text body, then append signature_html.
    import html as _html
    body_html = _html.escape((payload.body or "")).replace("\n", "<br>")
    if signature_html:
        body_html = (body_html + "<br><br>" + signature_html).strip()
    else:
        # Fallback: use the text signature converted to HTML.
        if signature_text and signature_text not in (payload.body or ""):
            body_html = (body_html + "<br><br>" + _html.escape(signature_text).replace("\n", "<br>")).strip()

    send_reply_in_thread(
        db=db,
        mailbox=t.mailbox,
        thread_id=t.gmail_thread_id,
        to_email=t.from_email,
        subject=payload.subject,
        body_text=body_text,
        body_html=body_html,
    )

    # Update ticket bookkeeping
    from datetime import datetime
    t.ack_sent_at = datetime.utcnow()
    t.last_from_me = True  # we just replied
    if payload.mark_as_responded:
        t.status = TicketStatus.RESPONDED

    # after send, this should not be not-replied
    t.is_not_replied = False

    add_audit(
        db,
        mailbox=t.mailbox,
        thread_id=t.thread_id,
        action=AuditAction.REPLIED,
        actor_user_id=user.id,
        detail={"subject": payload.subject},
    )
    db.commit()
    return {"ok": True}


@router.post("/{thread_id}/send-reply")
async def send_reply_form(
    thread_id: str,
    subject: str = Form(...),
    body: str = Form(""),
    cc: str = Form(""),
    bcc: str = Form(""),
    mark_as_responded: bool = Form(True),
    attachments: list[UploadFile] | None = File(default=None),
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send a reply with Gmail-like compose features (CC/BCC + attachments).

    This endpoint is used by the Quick Reply modal. It avoids breaking the legacy
    JSON-based /send-ack endpoint.
    """
    t = _get_ticket(db, thread_id, mailbox)

    # Signature support:
    # We ONLY use the app-managed HTML signature. The legacy plain-text signature
    # field is kept for backwards compatibility, but must NOT be appended automatically
    # (otherwise users get double signatures).
    signature_html = (get_state(db, "signature_html", t.mailbox) or "").strip()

    # Build text + HTML bodies.
    import html as _html
    final_body = (body or "").rstrip()
    body_html = _html.escape((body or "")).replace("\n", "<br>")

    if signature_html:
        # Derive a plain-text signature from the HTML so text-only clients still look ok.
        try:
            from app.services.gmail_parse import _strip_html as _strip
            sig_text = _strip(signature_html)
        except Exception:
            sig_text = ""

        if sig_text:
            final_body = (final_body + "\n\n" + sig_text).strip() if final_body else sig_text
        body_html = (body_html + "<br><br>" + signature_html).strip() if body_html else signature_html

    out_attachments: list[OutgoingAttachment] = []
    for f in attachments or []:
        if not f:
            continue
        data = await f.read()
        if not data:
            continue
        out_attachments.append(
            OutgoingAttachment(
                filename=f.filename or "attachment",
                content=data,
                content_type=f.content_type,
            )
        )

    try:
        send_reply_in_thread(
            db=db,
            mailbox=t.mailbox,
            thread_id=t.gmail_thread_id,
            to_email=t.from_email,
            subject=subject,
            body_text=final_body,
            body_html=body_html,
            cc=cc or None,
            bcc=bcc or None,
            from_email=t.mailbox,
            attachments=out_attachments,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to send email: {e}")

    # Update ticket bookkeeping
    t.ack_sent_at = datetime.utcnow()
    t.last_from_me = True
    if mark_as_responded:
        t.status = TicketStatus.RESPONDED
    t.is_not_replied = False

    add_audit(
        db,
        mailbox=t.mailbox,
        thread_id=t.thread_id,
        action=AuditAction.REPLIED,
        actor_user_id=user.id,
        detail={"subject": subject, "cc": cc, "bcc": bcc, "attachments": [a.filename for a in out_attachments]},
    )
    db.commit()
    return {"ok": True}



class CategoryIn(BaseModel):
    category: TicketCategory


@router.patch("/{thread_id}/category")
def set_category(
    thread_id: str,
    payload: CategoryIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _get_ticket(db, thread_id, mailbox)

    old = t.category
    t.category = payload.category

    # SLA due is based on last_message_at (fallback now)
    base_time = t.last_message_at or datetime.utcnow()
    t.sla_due_at = _compute_sla_due_at(t.category, t.priority, base_time)

    t.updated_at = datetime.utcnow()
    add_audit(
        db,
        mailbox=t.mailbox,
        thread_id=t.thread_id,
        action=AuditAction.CATEGORY_SET,
        actor_user_id=user.id,
        detail={"from": old.value if old else None, "to": t.category.value, "sla_due_at": t.sla_due_at.isoformat() if t.sla_due_at else None},
    )
    db.commit()
    return {"ok": True, "category": t.category.value, "sla_due_at": t.sla_due_at}


class NoteIn(BaseModel):
    body: str


@router.get("/{thread_id}/notes", response_model=list[TicketNoteOut])
def list_notes(
    thread_id: str,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _get_ticket(db, thread_id, mailbox)
    notes = (
        db.query(ThreadTicketNote)
        .filter(ThreadTicketNote.mailbox == t.mailbox)
        .filter(ThreadTicketNote.thread_id == t.thread_id)
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
def add_note(
    thread_id: str,
    payload: NoteIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _get_ticket(db, thread_id, mailbox)
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(400, "Note body required")
    note = ThreadTicketNote(
        mailbox=t.mailbox,
        thread_id=t.thread_id,
        author_user_id=user.id,
        body=body,
        created_at=datetime.utcnow(),
    )
    db.add(note)
    db.flush()  # assigns note.id
    add_audit(
        db,
        mailbox=t.mailbox,
        thread_id=t.thread_id,
        action=AuditAction.NOTE_ADDED,
        actor_user_id=user.id,
        detail={"note_id": note.id},
    )
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
def list_audit(
    thread_id: str,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _get_ticket(db, thread_id, mailbox)
    rows = (
        db.query(ThreadTicketAudit)
        .filter(ThreadTicketAudit.mailbox == t.mailbox)
        .filter(ThreadTicketAudit.thread_id == t.thread_id)
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
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Delete all tickets and sync watermarks (does not remove Google connection).

    Safety: requires confirm == 'FLUSH'.
    """
    if (payload.confirm or "").strip().upper() != "FLUSH":
        raise HTTPException(status_code=400, detail="Confirmation required. Send confirm='FLUSH'.")

    # Delete mailbox-scoped records only.
    db.query(ThreadTicket).filter(ThreadTicket.mailbox == mailbox).delete(synchronize_session=False)
    db.query(ThreadTicketNote).filter(ThreadTicketNote.mailbox == mailbox).delete(synchronize_session=False)
    db.query(ThreadTicketAudit).filter(ThreadTicketAudit.mailbox == mailbox).delete(synchronize_session=False)
    db.query(DismissedEmailThread).filter(DismissedEmailThread.mailbox == mailbox).delete(synchronize_session=False)

    # Clear mailbox-scoped sync/settings state keys.
    db.query(AppState).filter(AppState.key.like(f"{mailbox}:%")).delete(synchronize_session=False)

    db.commit()
    return {"ok": True, "message": f"Mailbox '{mailbox}' flushed (tickets, notes, audit, dismissed threads, and state cleared)."}
