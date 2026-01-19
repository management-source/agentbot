from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from pydantic import BaseModel

from app.db import get_db
from app.models import ThreadTicket, TicketStatus
from app.schemas import TicketListOut, TicketOut, StatusUpdateIn, DraftAckOut, SendAckIn
from app.services.ai_reply import draft_acknowledgement
from app.services.gmail_send import send_reply_in_thread

router = APIRouter()

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
def list_tickets(tab: str = "all", limit: int = 50, db: Session = Depends(get_db)):
    q = db.query(ThreadTicket)
    q = _tab_filter(q, tab)
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
def update_status(thread_id: str, payload: StatusUpdate, db: Session = Depends(get_db)):
    t = db.get(ThreadTicket, thread_id)
    if not t:
        raise HTTPException(404, "Ticket not found")

    t.status = payload.status

    # Optional: keep ALL clean + not_replied logic
    if payload.status in [TicketStatus.RESPONDED, TicketStatus.NO_REPLY_NEEDED]:
        t.is_not_replied = False

    t.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "thread_id": thread_id, "status": t.status.value}

@router.post("/{thread_id}/draft-ack", response_model=DraftAckOut)
def draft_ack(thread_id: str, db: Session = Depends(get_db)):
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
def send_ack(thread_id: str, payload: SendAckIn, db: Session = Depends(get_db)):
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

    db.commit()
    return {"ok": True}
