from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.authz import get_current_user
from app.config import settings
from app.db import get_db
from app.services.state import get_state, set_state
from app.services.gmail_client import get_gmail_service, gmail_user_id

import html
import re


router = APIRouter()


class SignatureOut(BaseModel):
    signature: str


class SignatureIn(BaseModel):
    signature: str


@router.get("/signature", response_model=SignatureOut)
def get_signature(db: Session = Depends(get_db), user=Depends(get_current_user)):
    sig = (get_state(db, "signature_text") or "").strip()
    if not sig:
        sig = (settings.DEFAULT_SIGNATURE or "").strip()
    return SignatureOut(signature=sig)


@router.put("/signature", response_model=SignatureOut)
def set_signature(payload: SignatureIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    sig = (payload.signature or "").strip()
    set_state(db, "signature_text", sig)
    db.commit()
    return SignatureOut(signature=sig)


def _html_to_text(s: str) -> str:
    """Best-effort HTML signature -> plain text for use in replies.

    Gmail signatures are stored as HTML. We keep this conservative to avoid
    surprising formatting: strip tags, preserve line breaks, unescape.
    """
    if not s:
        return ""
    # Normalize common line breaks
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p\s*>", "\n", s)
    s = re.sub(r"(?i)</div\s*>", "\n", s)
    # Strip remaining tags
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    # Collapse excessive blank lines
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


@router.post("/signature/fetch-gmail", response_model=SignatureOut)
def fetch_signature_from_gmail(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Fetch the Gmail signature from the connected mailbox and store it.

    Requires the Gmail settings scope (gmail.settings.basic).
    """
    try:
        service = get_gmail_service(db)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        # Find a sensible SendAs identity.
        sendas = service.users().settings().sendAs().list(userId=gmail_user_id()).execute()
        items = sendas.get("sendAs", []) or []
        chosen = None
        for it in items:
            if it.get("isPrimary"):
                chosen = it
                break
        if not chosen and items:
            chosen = items[0]
        if not chosen:
            raise HTTPException(status_code=404, detail="No send-as identities found.")

        send_as_email = chosen.get("sendAsEmail")
        if not send_as_email:
            raise HTTPException(status_code=404, detail="No sendAsEmail found.")

        full = service.users().settings().sendAs().get(userId=gmail_user_id(), sendAsEmail=send_as_email).execute()
        sig_html = (full.get("signature") or "").strip()
        sig_text = _html_to_text(sig_html)

        set_state(db, "signature_text", sig_text)
        db.commit()
        return SignatureOut(signature=sig_text)
    except HTTPException:
        raise
    except Exception as e:
        # Google API can raise HttpError; surface as 502
        raise HTTPException(status_code=502, detail=f"Failed to fetch signature from Gmail: {e}")
