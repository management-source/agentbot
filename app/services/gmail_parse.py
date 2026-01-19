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
    # Prefer text/plain
    plain_part = _find_part(payload, "text/plain")
    if plain_part:
        text = _b64url_decode(plain_part["body"]["data"])
        return {"body_text": text.strip(), "body_html": None, "used_mime": "text/plain"}

    # Fallback to text/html
    html_part = _find_part(payload, "text/html")
    if html_part:
        html = _b64url_decode(html_part["body"]["data"])
        text = _strip_html(html)
        return {"body_text": text, "body_html": html, "used_mime": "text/html"}

    # If payload has body.data at top-level
    if payload.get("body", {}).get("data"):
        text = _b64url_decode(payload["body"]["data"])
        return {"body_text": text.strip(), "body_html": None, "used_mime": payload.get("mimeType", "unknown")}

    return {"body_text": "", "body_html": None, "used_mime": "none"}
