from __future__ import annotations

import base64
import re
import ipaddress
from urllib.parse import urlparse

import bleach
import httpx
from typing import Any, Dict, Optional, Tuple

import html2text
from premailer import transform

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.gmail_client import get_gmail_service, gmail_user_id
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


def _extract_attachments(payload: Dict[str, Any]):
    """Extract non-inline attachments metadata from a Gmail message payload."""
    out = []
    for part in _walk_parts(payload):
        filename = (part.get("filename") or "").strip()
        body = part.get("body", {}) or {}
        attachment_id = body.get("attachmentId")
        if not filename or not attachment_id:
            continue

        headers = _part_headers(part)
        disp = (headers.get("content-disposition") or "").lower()
        is_inline = "inline" in disp or bool(headers.get("content-id"))
        mime_type = part.get("mimeType") or headers.get("content-type") or "application/octet-stream"
        size = body.get("size")
        out.append({
            "filename": filename,
            "mime_type": mime_type,
            "size": size,
            "attachment_id": attachment_id,
            "is_inline": is_inline,
        })
    return out


def _gmail_b64url_decode(data: str) -> bytes:
    data = (data or "").replace("-", "+").replace("_", "/")
    pad = "=" * (-len(data) % 4)
    return base64.b64decode(data + pad)


def _sanitize_html(html: str) -> str:
    """Sanitize HTML for safe inline rendering.

    - strips scripts/iframes/objects
    - removes event handler attributes (on*)
    - restricts protocols

    This is not intended to be a perfect email renderer; it is a pragmatic safety layer.
    """
    if not html:
        return ""

    allowed_tags = set(bleach.sanitizer.ALLOWED_TAGS) | {
        "div","span","p","br","hr","table","thead","tbody","tfoot","tr","td","th",
        "img","a","ul","ol","li","strong","em","b","i","u","blockquote","pre","code",
        "h1","h2","h3","h4","h5","h6","style"
    }
    allowed_attrs = dict(bleach.sanitizer.ALLOWED_ATTRIBUTES)
    allowed_attrs.update({
        "*": ["class","style","title","dir","lang"],
        "a": ["href","title","target","rel","name"],
        "img": ["src","alt","title","width","height"],
        "td": ["colspan","rowspan","align","valign"],
        "th": ["colspan","rowspan","align","valign"],
        "table": ["cellpadding","cellspacing","border","width"],
    })

    cleaned = bleach.clean(
        html,
        tags=list(allowed_tags),
        attributes=allowed_attrs,
        protocols=["http","https","mailto","cid","data"],
        strip=True,
    )

    # Remove any lingering on* attributes that can slip through style blocks, etc.
    cleaned = re.sub(r'\son\w+\s*=\s*([\"\']).*?\1', '', cleaned, flags=re.I)

    return cleaned


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
            .get(userId=gmail_user_id(), id=thread_id, format="full")
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
            # Inline CSS <style> into elements to improve rendering consistency.
            try:
                body_html = transform(body_html, disable_leftover_css=True)
            except Exception:
                pass

            body_html = _sanitize_html(body_html)

        # Always provide a text variant for consistent preview/search.
        body_text = (body_info.get("body_text") or "").strip()
        if not body_text and body_html:
            try:
                body_text = html2text.html2text(body_html).strip()
            except Exception:
                body_text = ""

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
                "body_text": body_text,
                "body_text_preview": (body_text[:800] + "…") if len(body_text) > 800 else body_text,
                "body_html": body_html,  # may be None
                "used_mime": body_info.get("used_mime"),
                "attachments": _extract_attachments(payload),
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
            .get(userId=gmail_user_id(), id=message_id, format="full")
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
            .get(userId=gmail_user_id(), messageId=message_id, id=attachment_id)
            .execute()
        )
    except HttpError as e:
        raise HTTPException(status_code=400, detail=str(e))

    data = att.get("data")
    if not data:
        raise HTTPException(status_code=404, detail="Attachment data missing")

    raw = _gmail_b64url_decode(data)
    return Response(content=raw, media_type=mime_type)


def _is_private_host(host: str) -> bool:
    host = (host or "").strip().lower()
    if not host:
        return True
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    # IP literal
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        pass
    # Basic hostname blocks
    if host.endswith('.local') or host.endswith('.internal'):
        return True
    return False


@router.get('/proxy-image')
def proxy_image(url: str, db: Session = Depends(get_db)):
    """Privacy-preserving remote image proxy.

    This allows email logos/icons to display without loading them directly in the browser.
    Security notes:
    - http/https only
    - blocks localhost/private IP literals
    - enforces size cap
    - images only
    """
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'}:
        raise HTTPException(status_code=400, detail='Invalid URL scheme')
    if _is_private_host(parsed.hostname or ''):
        raise HTTPException(status_code=400, detail='Blocked host')

    timeout = httpx.Timeout(10.0, connect=5.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers={'User-Agent': 'AgentBotImageProxy/1.0'})
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f'Upstream error {r.status_code}')
        ctype = (r.headers.get('content-type') or '').split(';')[0].strip().lower()
        if not ctype.startswith('image/'):
            raise HTTPException(status_code=400, detail='Not an image')
        content = r.content
        if len(content) > 5_000_000:
            raise HTTPException(status_code=413, detail='Image too large')

    return Response(content=content, media_type=ctype)


@router.get('/{thread_id}/messages/{message_id}/attachments/{attachment_id}')
def download_attachment(thread_id: str, message_id: str, attachment_id: str, filename: str | None = None, db: Session = Depends(get_db)):
    """Download an attachment by attachmentId."""
    service = get_gmail_service(db)
    try:
        att = (
            service.users()
            .messages()
            .attachments()
            .get(userId=gmail_user_id(), messageId=message_id, id=attachment_id)
            .execute()
        )
    except HttpError as e:
        raise HTTPException(status_code=400, detail=str(e))

    data = att.get('data')
    if not data:
        raise HTTPException(status_code=404, detail='Attachment data missing')

    raw = _gmail_b64url_decode(data)
    headers = {}
    if filename:
        safe = filename.replace('\n', ' ').replace('\r', ' ')
        headers['Content-Disposition'] = f'attachment; filename="{safe}"'
    # Gmail doesn't always provide content-type here; let browser sniff. Security headers prevent abuse.
    return Response(content=raw, media_type='application/octet-stream', headers=headers)
