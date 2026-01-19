from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import ThreadTicket, TicketStatus
from app.services.gmail_client import get_gmail_service, is_from_me, parse_email_address
from app.models import BlacklistedSender

logger = logging.getLogger(__name__)

def _get_header(headers: list[dict], name: str) -> str | None:
    for h in headers or []:
        if (h.get("name") or "").lower() == name.lower():
            return h.get("value")
    return None

def sync_inbox_threads(max_threads: int = 50, start: str | None = None, end: str | None = None) -> dict:
    db: Session = SessionLocal()
    try:
        try:
            service = get_gmail_service(db)
        except RuntimeError as e:
            # Expected for manual-sync deployments before OAuth is completed.
            logger.info("Gmail sync skipped: %s", e)
            return {"ok": False, "error": str(e)}

        q_parts = []
        if start:
            q_parts.append(f"after:{start.replace('-', '/')}")
        if end:
            q_parts.append(f"before:{end.replace('-', '/')}")
        q = " ".join(q_parts) if q_parts else None

        threads_resp = service.users().threads().list(
            userId="me",
            labelIds=["INBOX"],
            q=q,
            maxResults=max_threads,
        ).execute()

        threads = threads_resp.get("threads", [])

        upserted = 0
        for t in threads:
            thread_id = t["id"]

            # Get thread details with metadata only
            th = service.users().threads().get(
                userId="me",
                id=thread_id,
                format="metadata",
                metadataHeaders=["From", "Subject", "Date", "Message-ID", "In-Reply-To", "References"],
            ).execute()

            messages = th.get("messages", [])
            if not messages:
                continue

            last_msg = messages[-1]
            last_msg_id = last_msg.get("id")
            payload = last_msg.get("payload") or {}
            headers = payload.get("headers") or []

            from_h = _get_header(headers, "From")
            subject = _get_header(headers, "Subject") or "(no subject)"

            # snippet from thread (Gmail provides per message too)
            snippet = last_msg.get("snippet") or ""

            # unread detection: if any message has UNREAD label
            is_unread = any("UNREAD" in (m.get("labelIds") or []) for m in messages)

            # date: Gmail internalDate is ms epoch string
            internal_ms = last_msg.get("internalDate")
            last_dt = None
            if internal_ms:
                last_dt = datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc).replace(tzinfo=None)

            last_from_me = is_from_me(from_h)
            from_name, from_email = parse_email_address(from_h)

            ticket = db.get(ThreadTicket, thread_id)
            if ticket is None:
                ticket = ThreadTicket(thread_id=thread_id, status=TicketStatus.PENDING)
                db.add(ticket)

            # Update fields
            ticket.last_message_id = last_msg_id
            ticket.subject = subject
            ticket.snippet = snippet
            ticket.last_message_at = last_dt
            ticket.is_unread = bool(is_unread)
            ticket.last_from_me = bool(last_from_me)
            ticket.from_name = from_name
            ticket.from_email = from_email

            # SLA / due_at (simple policy: medium=2 days, high=0 days, low=3 days)
            # You can later map priority by AI; for MVP keep "medium"
            
            is_blacklisted = False
            if from_email:
                is_blacklisted = db.query(BlacklistedSender).filter(BlacklistedSender.email == from_email.lower()).first() is not None

            if is_blacklisted:
                continue  # skip ticket entirely
            
            if not ticket.priority:
                ticket.priority = "medium"
            if last_dt:
                days = {"high": 0, "medium": 2, "low": 3}.get(ticket.priority, 2)
                ticket.due_at = (last_dt + timedelta(days=days))

            # NOT REPLIED (Priority tab) rule:
            # Latest message is from external + not finalized status
            ticket.is_not_replied = (
                (not ticket.last_from_me)
                and ticket.status not in (TicketStatus.RESPONDED, TicketStatus.NO_REPLY_NEEDED)
            )

            ticket.updated_at = datetime.utcnow()
            upserted += 1

        db.commit()
        return {"ok": True, "threads_seen": len(threads), "upserted": upserted}

    finally:
        db.close()
