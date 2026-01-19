from __future__ import annotations

import base64
from email.message import EmailMessage
from sqlalchemy.orm import Session

from app.services.gmail_client import get_gmail_service

def send_reply_in_thread(
    db: Session,
    thread_id: str,
    to_email: str | None,
    subject: str,
    body: str,
):
    if not to_email:
        raise ValueError("Missing recipient email")

    service = get_gmail_service(db)

    msg = EmailMessage()
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service.users().messages().send(
        userId="me",
        body={"raw": raw, "threadId": thread_id},
    ).execute()
