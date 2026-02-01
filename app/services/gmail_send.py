from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.services.gmail_client import get_gmail_service, gmail_user_id


@dataclass
class OutgoingAttachment:
    filename: str
    content: bytes
    content_type: str | None = None


def _normalize_addr_list(value: str | None) -> list[str]:
    """Parse a comma/semicolon separated address list into a clean list."""
    if not value:
        return []
    raw = value.replace(";", ",")
    out: list[str] = []
    for part in raw.split(","):
        p = (part or "").strip()
        if p:
            out.append(p)
    return out


def _guess_maintype_subtype(content_type: str | None, filename: str) -> tuple[str, str]:
    ct = (content_type or "").split(";")[0].strip().lower()
    if not ct:
        ct, _ = mimetypes.guess_type(filename)
        ct = ct or "application/octet-stream"
    if "/" not in ct:
        return "application", "octet-stream"
    mt, st = ct.split("/", 1)
    return mt, st


def build_reply_message(
    *,
    to_email: str,
    subject: str,
    body_text: str,
    cc: str | None = None,
    bcc: str | None = None,
    from_email: str | None = None,
    attachments: Optional[Iterable[OutgoingAttachment]] = None,
    body_html: str | None = None,
) -> EmailMessage:
    """Build a Gmail-compatible MIME message.

    - Supports CC/BCC.
    - Supports attachments (files/photos).
    - Supports optional HTML body (multipart/alternative).

    Note: threading is handled by Gmail API using threadId. We do not attempt
    to add In-Reply-To/References headers because we may not always have the
    original Message-ID available in DB.
    """

    msg = EmailMessage()
    msg["To"] = to_email
    if from_email:
        msg["From"] = from_email
    msg["Subject"] = subject

    cc_list = _normalize_addr_list(cc)
    bcc_list = _normalize_addr_list(bcc)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    if bcc_list:
        msg["Bcc"] = ", ".join(bcc_list)

    # Body
    msg.set_content(body_text or "")
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    # Attachments
    for a in attachments or []:
        if not a or not a.filename:
            continue
        mt, st = _guess_maintype_subtype(a.content_type, a.filename)
        msg.add_attachment(a.content, maintype=mt, subtype=st, filename=a.filename)

    return msg


def send_reply_in_thread(
    *,
    db: Session,
    thread_id: str,
    to_email: str | None,
    subject: str,
    body_text: str,
    cc: str | None = None,
    bcc: str | None = None,
    from_email: str | None = None,
    attachments: Optional[Iterable[OutgoingAttachment]] = None,
    body_html: str | None = None,
):
    """Send a reply in an existing Gmail thread."""

    if not to_email:
        raise ValueError("Missing recipient email")

    service = get_gmail_service(db)

    msg = build_reply_message(
        to_email=to_email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        cc=cc,
        bcc=bcc,
        from_email=from_email,
        attachments=attachments,
    )

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service.users().messages().send(
        userId=gmail_user_id(),
        body={"raw": raw, "threadId": thread_id},
    ).execute()
