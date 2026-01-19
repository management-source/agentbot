from __future__ import annotations

import base64
import re
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.gmail_client import get_gmail_service
from app.services.gmail_parse import extract_message_body


router = APIRouter()


def _normalize_cid(cid: str) -> str:
    """Normalize content-id values (strip brackets, whitespace)."""
    cid = (cid or "").strip()
    cid = cid.strip("<>")
    return cid


def _walk_parts(payload: Dict[str, Any]):
    """Yield all MIME parts depth-first."""
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _walk_parts(part)


def _part_headers(part: Dict[str, Any]) -> Dict[str, str]:
    headers = {}
    for h in part.get("headers", []) or []:
        name = (h.get("name") or "").lower().strip()
        value = (h.get("value") or "").strip()
        if name:
            headers[name] = value
    return headers


def _find_inline_attachment(payload: Dict[str, Any], cid: str) -> Optional[Tuple[str, str]]:
    """Return (attachment_id, mime_type) for a part with matching Content-ID."""
    target = _normalize_cid(cid)
    for part in _walk_parts(payload):
        headers = _part_headers(part)
        part_cid = _normalize_cid(headers.get("content-id", ""))
        if not part_cid:
            continue
        if part_cid != target:
            continue

        body = part.get("body", {}) or {}
        attachment_id = body.get("attachmentId")
        mime_type = part.get("mimeType") or headers.get("content-type") or "application/octet-stream"
        if attachment_id:
            return attachment_id, mime_type
    return None


def _gmail_b64url_decode(data: str) -> bytes:
    data = (data or "").replace("-", "+").replace("_", "/")
    pad = "=" * (-len(data) % 4)
    return base64.b64decode(data + pad)


def _sanitize_html(html: str) -> str:
    """Very small sanitizer: remove scripts/iframes/object/embed.

    We intentionally keep basic inline CSS because many email templates rely on it.
    """
    if not html:
        return ""

    html = re.sub(r"(?is)<(script|iframe|object|embed).*?>.*?</\1>", "", html)
    # Also drop any stray opening tags without closing.
    html = re.sub(r"(?is)<(script|iframe|object|embed)[^>]*?/?>", "", html)
    return html


@router.get("/{thread_id}")
def get_thread(thread_id: str, db: Session = Depends(get_db)):
    """Return a Gmail thread with both text and (when available) HTML bodies.

    Frontend can safely render HTML inside a sandboxed iframe.
    """
    service = get_gmail_service(db)

    try:
        thread = (
            service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
    except HttpError as e:
        raise HTTPException(status_code=400, detail=str(e))

    messages_out = []
    for m in thread.get("messages", []) or []:
        payload = m.get("payload", {}) or {}
        headers = {
            h["name"].lower(): h["value"]
            for h in (payload.get("headers", []) or [])
            if "name" in h and "value" in h
        }

        body_info = extract_message_body(payload)
        body_html = body_info.get("body_html")
        if body_html:
            body_html = _sanitize_html(body_html)

        messages_out.append(
            {
                "id": m.get("id"),
                "thread_id": m.get("threadId"),
                "internal_date": m.get("internalDate"),
                "from": headers.get("from"),
                "to": headers.get("to"),
                "subject": headers.get("subject"),
                "date": headers.get("date"),
                "snippet": m.get("snippet"),
                "body_text": body_info.get("body_text") or "",
                "body_html": body_html,  # may be None
                "used_mime": body_info.get("used_mime"),
            }
        )

    return {
        "thread_id": thread_id,
        "messages": messages_out,
        "gmail_url": f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
    }


@router.get("/{thread_id}/messages/{message_id}/inline/{cid}")
def get_inline_attachment(
    thread_id: str,
    message_id: str,
    cid: str,
    db: Session = Depends(get_db),
):
    """Serve inline (cid:) images used inside HTML emails.

    The frontend rewrites <img src="cid:..."> into this endpoint.
    """
    service = get_gmail_service(db)
    try:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
    except HttpError as e:
        raise HTTPException(status_code=400, detail=str(e))

    payload = msg.get("payload", {}) or {}
    found = _find_inline_attachment(payload, cid)
    if not found:
        raise HTTPException(status_code=404, detail="Inline attachment not found")

    attachment_id, mime_type = found

    try:
        att = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
    except HttpError as e:
        raise HTTPException(status_code=400, detail=str(e))

    data = att.get("data")
    if not data:
        raise HTTPException(status_code=404, detail="Attachment data missing")

    raw = _gmail_b64url_decode(data)
    return Response(content=raw, media_type=mime_type)
