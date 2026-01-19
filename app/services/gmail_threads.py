import base64
from sqlalchemy.orm import Session
from app.services.gmail_client import get_gmail_service

def _headers_map(headers: list[dict]) -> dict:
    m = {}
    for h in headers or []:
        m[(h.get("name") or "").lower()] = h.get("value") or ""
    return m

def _decode_body(payload: dict) -> str:
    if not payload:
        return ""
    body = (payload.get("body") or {})
    data = body.get("data")
    if data:
        try:
            return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
        except Exception:
            return ""
    for part in (payload.get("parts") or []):
        if part.get("mimeType") == "text/plain":
            txt = _decode_body(part)
            if txt:
                return txt
    for part in (payload.get("parts") or []):
        txt = _decode_body(part)
        if txt:
            return txt
    return ""

def gmail_thread_link(thread_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"

def get_thread_details(db: Session, thread_id: str) -> dict:
    service = get_gmail_service(db)
    th = service.users().threads().get(userId="me", id=thread_id, format="full").execute()

    messages_out = []
    for m in th.get("messages", []):
        payload = m.get("payload") or {}
        headers = _headers_map(payload.get("headers") or [])
        messages_out.append({
            "id": m.get("id"),
            "date": headers.get("date"),
            "from": headers.get("from"),
            "to": headers.get("to"),
            "subject": headers.get("subject"),
            "snippet": m.get("snippet"),
            "body": _decode_body(payload)[:8000],
        })

    return {"thread_id": thread_id, "gmail_url": gmail_thread_link(thread_id), "messages": messages_out}
