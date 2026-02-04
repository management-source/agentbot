from __future__ import annotations

import html
import os
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.authz import get_current_user
from app.config import settings
from app.db import get_db
from app.services.state import get_state, set_state
from app.services.gmail_client import (
    GMAIL_SCOPES,
    GMAIL_SIGNATURE_SCOPE,
    get_gmail_service,
    gmail_user_id,
)

router = APIRouter()


class SignatureOut(BaseModel):
    signature: str


class SignatureIn(BaseModel):
    signature: str


class GmailSignatureIn(BaseModel):
    # If omitted, we try to fetch the primary "send as" signature.
    send_as_email: str | None = None


def _strip_html(s: str) -> str:
    # Small, safe fallback for plain-text signature_text
    s = re.sub(r"<br\\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


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
    safe_html = html.escape(sig).replace("\n", "<br>")
    set_state(db, "signature_html", safe_html)
    set_state(db, "signature_source", "manual")
    set_state(db, "signature_send_as", "")
    db.commit()
    return SignatureOut(signature=sig)


@router.get("/signature/html")
def get_signature_html(db: Session = Depends(get_db), user=Depends(get_current_user)):
    sig_html = (get_state(db, "signature_html") or "").strip()
    source = (get_state(db, "signature_source") or "").strip() or "manual"
    send_as = (get_state(db, "signature_send_as") or "").strip()
    return {"html": sig_html, "source": source, "send_as": send_as}


@router.post("/signature/fetch-gmail")
def fetch_gmail_signature(payload: GmailSignatureIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    # Request the settings scope in addition to normal Gmail scopes
    scopes = list(dict.fromkeys(list(GMAIL_SCOPES) + [GMAIL_SIGNATURE_SCOPE]))

    try:
        service = get_gmail_service(db=db, scopes=scopes)
        user_id = gmail_user_id()

        sendas_list = (
            service.users()
            .settings()
            .sendAs()
            .list(userId=user_id)
            .execute()
        )
        sendas = sendas_list.get("sendAs", []) or []
        if not sendas:
            raise HTTPException(status_code=404, detail="No 'Send mail as' identities found in Gmail settings.")

        wanted = (payload.send_as_email or "").strip().lower()
        picked = None

        if wanted:
            for s in sendas:
                if (s.get("sendAsEmail") or "").strip().lower() == wanted:
                    picked = s
                    break
            if not picked:
                raise HTTPException(status_code=404, detail=f"send_as_email '{payload.send_as_email}' not found in Gmail settings.")
        else:
            for s in sendas:
                if s.get("isPrimary"):
                    picked = s
                    break
            picked = picked or sendas[0]

        send_as_email = (picked.get("sendAsEmail") or "").strip()
        if not send_as_email:
            raise HTTPException(status_code=500, detail="Gmail settings did not return a sendAsEmail value.")

        details = (
            service.users()
            .settings()
            .sendAs()
            .get(userId=user_id, sendAsEmail=send_as_email)
            .execute()
        )
        sig_html = (details.get("signature") or "").strip()
        if not sig_html:
            raise HTTPException(status_code=404, detail=f"No signature is set in Gmail for '{send_as_email}'.")

        sig_text = _strip_html(sig_html)

        set_state(db, "signature_html", sig_html)
        set_state(db, "signature_text", sig_text)
        set_state(db, "signature_source", "gmail")
        set_state(db, "signature_send_as", send_as_email)
        db.commit()

        return {"ok": True, "send_as_email": send_as_email}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch Gmail signature: {e}")


@router.post("/signature/assets/{asset_name}")
def upload_signature_asset(asset_name: str, file: UploadFile = File(...), user=Depends(get_current_user)):
    # Keep upload support for local assets (banner/profile) if you want to swap them into the fetched Gmail HTML.
    allowed = {"profile", "banner"}
    if asset_name not in allowed:
        raise HTTPException(status_code=400, detail="Invalid asset name. Use 'profile' or 'banner'.")

    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    static_base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "signature")
    os.makedirs(static_base, exist_ok=True)
    path = os.path.join(static_base, f"{asset_name}.png")
    with open(path, "wb") as f:
        f.write(content)

    return {"ok": True, "path": f"/static/signature/{asset_name}.png"}
