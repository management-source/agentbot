from __future__ import annotations

from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import ThreadTicket, TicketStatus
from app.config import settings
from app.services.gmail_send import send_reply_in_thread

def run_reminders():
    """
    MVP reminder strategy: send yourself a digest using Gmail send.
    To keep it simple, we send it as a new email (not a thread reply).
    """
    db: Session = SessionLocal()
    try:
        now = datetime.utcnow()
        cooldown_cutoff = now - timedelta(seconds=settings.REMINDER_COOLDOWN_SECONDS)

        # Tickets that require reminder
        tickets = (
            db.query(ThreadTicket)
            .filter(
                ThreadTicket.status.in_([TicketStatus.PENDING, TicketStatus.IN_PROGRESS]),
            )
            .filter(
                (ThreadTicket.is_not_replied == True)
                | (ThreadTicket.is_unread == True)
            )
            .filter(
                (ThreadTicket.last_reminded_at == None) | (ThreadTicket.last_reminded_at < cooldown_cutoff)
            )
            .order_by(ThreadTicket.last_message_at.asc().nullslast())
            .limit(20)
            .all()
        )

        if not tickets:
            return {"ok": True, "reminded": 0}

        lines = []
        for t in tickets:
            when = t.last_message_at.isoformat() if t.last_message_at else "unknown time"
            lines.append(f"- {t.from_email or ''} | {t.subject or ''} | last: {when} | status: {t.status}")

        body = (
            "You have pending emails that require attention:\n\n"
            + "\n".join(lines)
            + "\n\nPlease review in Email Autopilot Manager."
        )

        # Send digest to yourself: easiest is using thread send method with a "fake" thread
        # Instead, we can send as a normal message using Gmail API directly.
        # We'll reuse gmail_send but it requires a thread_id, so we'll implement minimal direct send here.
        _send_new_email(db, settings.REMINDER_TO_EMAIL, "Email Autopilot Reminder Digest", body)

        # Update reminder timestamps
        for t in tickets:
            t.last_reminded_at = now
            t.reminder_count = (t.reminder_count or 0) + 1
        db.commit()

        return {"ok": True, "reminded": len(tickets)}

    finally:
        db.close()


def _send_new_email(db: Session, to_email: str, subject: str, body: str):
    import base64
    from email.message import EmailMessage
    from app.services.gmail_client import get_gmail_service

    service = get_gmail_service(db)
    msg = EmailMessage()
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
