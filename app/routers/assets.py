from __future__ import annotations

import base64
import ipaddress
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.gmail_client import get_gmail_service, gmail_user_id

router = APIRouter()


def _gmail_b64url_decode(data: str) -> bytes:
    data = (data or "").replace("-", "+").replace("_", "/")
    pad = "=" * (-len(data) % 4)
    return base64.b64decode(data + pad)


def _normalize_cid(cid: str) -> str:
    cid = (cid or "").strip()
    return cid.strip("<>")


def _part_headers(part: Dict[str, Any]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for h in part.get("headers", []) or []:
        name = (h.get("name") or "").lower().strip()
        value = (h.get("value") or "").strip()
        if name:
            headers[name] = value
    return headers


def _walk_parts(payload: Dict[str, Any]):
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _walk_parts(part)


def _find_inline_attachment(payload: Dict[str, Any], cid: str) -> Optional[Tuple[str, str]]:
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


def _is_private_host(host: str) -> bool:
    host = (host or "").strip().lower()
    if not host:
        return True
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        pass
    if host.endswith(".local") or host.endswith(".internal"):
        return True
    return False


@router.get("/proxy-image")
def proxy_image(url: str):
    """Privacy-preserving remote image proxy.

    This allows email logos/icons to display without loading them directly in the browser.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Invalid URL scheme")
    if _is_private_host(parsed.hostname or ""):
        raise HTTPException(status_code=400, detail="Blocked host")

    timeout = httpx.Timeout(10.0, connect=5.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers={"User-Agent": "AgentBotImageProxy/1.0"})
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Upstream error {r.status_code}")
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if not ctype.startswith("image/"):
            raise HTTPException(status_code=400, detail="Not an image")
        content = r.content
        if len(content) > 5_000_000:
            raise HTTPException(status_code=413, detail="Image too large")

    return Response(content=content, media_type=ctype)


@router.get("/inline/{message_id}/{cid}")
def get_inline_attachment(message_id: str, cid: str, db: Session = Depends(get_db)):
    """Serve inline (cid:) images used inside HTML emails."""
    service = get_gmail_service(db)
    try:
        msg = service.users().messages().get(userId=gmail_user_id(), id=message_id, format="full").execute()
    except HttpError as e:
        raise HTTPException(status_code=400, detail=str(e))

    payload = msg.get("payload", {}) or {}
    found = _find_inline_attachment(payload, cid)
    if not found:
        raise HTTPException(status_code=404, detail="Inline attachment not found")

    attachment_id, mime_type = found

    try:
        att = service.users().messages().attachments().get(
            userId=gmail_user_id(), messageId=message_id, id=attachment_id
        ).execute()
    except HttpError as e:
        raise HTTPException(status_code=400, detail=str(e))

    data = att.get("data")
    if not data:
        raise HTTPException(status_code=404, detail="Attachment data missing")

    raw = _gmail_b64url_decode(data)
    return Response(content=raw, media_type=mime_type)


@router.get("/attachment/{message_id}/{attachment_id}")
def download_attachment(
    message_id: str,
    attachment_id: str,
    filename: str | None = None,
    mime: str | None = None,
    db: Session = Depends(get_db),
):
    """Download an attachment by attachmentId."""
    service = get_gmail_service(db)
    try:
        att = service.users().messages().attachments().get(
            userId=gmail_user_id(), messageId=message_id, id=attachment_id
        ).execute()
    except HttpError as e:
        raise HTTPException(status_code=400, detail=str(e))

    data = att.get("data")
    if not data:
        raise HTTPException(status_code=404, detail="Attachment data missing")

    raw = _gmail_b64url_decode(data)

    safe_headers: Dict[str, str] = {}
    safe_name = None
    if filename:
        safe_name = filename.replace("\n", " ").replace("\r", " ").strip() or None

    # For PDFs/images, inline is nicer; for others, force download.
    ctype = (mime or "").split(";")[0].strip().lower() or "application/octet-stream"
    dispo = "inline" if (ctype.startswith("image/") or ctype == "application/pdf") else "attachment"

    dispo_name = safe_name or "attachment"
    safe_headers["Content-Disposition"] = f'{dispo}; filename="{dispo_name}"'

    return Response(content=raw, media_type=ctype, headers=safe_headers)
