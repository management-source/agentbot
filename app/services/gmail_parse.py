import base64
import re
from typing import Any, Optional

def _b64url_decode(data: str) -> str:
    # Gmail uses base64url without padding
    data = data.replace("-", "+").replace("_", "/")
    pad = "=" * (-len(data) % 4)
    return base64.b64decode(data + pad).decode("utf-8", errors="replace")

def _strip_html(html: str) -> str:
    # Minimal HTML to text cleanup (no external deps)
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</p\s*>", "\n\n", html)
    html = re.sub(r"(?is)<.*?>", "", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()

def _find_part(payload: dict, mime_type: str) -> Optional[dict]:
    # Depth-first search
    if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
        return payload

    for part in payload.get("parts", []) or []:
        found = _find_part(part, mime_type)
        if found:
            return found
    return None

def extract_message_body(payload: dict) -> dict:
    """
    Returns:
      {
        "body_text": "...",
        "body_html": "<html>...</html>" or None,
        "used_mime": "text/plain" or "text/html" or "none"
      }
    """
    # Gmail often provides BOTH text/plain and text/html (multipart/alternative).
    # We return both when available so the UI can render proper HTML email templates.

    plain_part = _find_part(payload, "text/plain")
    html_part = _find_part(payload, "text/html")

    body_text = ""
    body_html = None
    used_mime = "none"

    if plain_part and plain_part.get("body", {}).get("data"):
        body_text = _b64url_decode(plain_part["body"]["data"]).strip()
        used_mime = "text/plain"

    if html_part and html_part.get("body", {}).get("data"):
        body_html = _b64url_decode(html_part["body"]["data"])
        # If we don't have a plain part, produce a readable text fallback.
        if not body_text:
            body_text = _strip_html(body_html)
        used_mime = "text/html" if used_mime == "none" else used_mime

    if body_text or body_html:
        return {"body_text": body_text, "body_html": body_html, "used_mime": used_mime}

    # If payload has body.data at top-level
    if payload.get("body", {}).get("data"):
        text = _b64url_decode(payload["body"]["data"])
        # Top-level bodies are usually plain; keep HTML empty.
        return {"body_text": text.strip(), "body_html": None, "used_mime": payload.get("mimeType", "unknown")}

    return {"body_text": "", "body_html": None, "used_mime": "none"}
