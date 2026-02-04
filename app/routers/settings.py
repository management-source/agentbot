from __future__ import annotations

import html
import os
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.authz import get_current_user
from app.config import settings
from app.db import get_db
from app.services.gmail_client import GMAIL_SCOPES, GMAIL_SIGNATURE_SCOPE, get_gmail_service, gmail_user_id
from app.services.gmail_parse import _strip_html
from app.services.signature_template import SignatureProfile, build_signature_html, build_signature_text
from app.services.state import get_state, set_state

router = APIRouter()


class SignatureOut(BaseModel):
    signature: str


class SignatureIn(BaseModel):
    signature: str


class OfficeIn(BaseModel):
    label: str
    address: str


class SignatureTemplateIn(BaseModel):
    # Defaults to match your current desired template.
    name: str = "Jessica Gale"
    title_line: str = (
        "Co-Founder | Principal Officer in Effective Control | Senior Property Manager | Licensed Estate Agent"
    )
    phone: str = "0422 643 451"
    email: str = "admin@donspremier.com.au"
    company: str = "DONS PREMIER ESTATE AGENTS"

    offices: List[OfficeIn] = Field(
        default_factory=lambda: [
            OfficeIn(label="CRANBOURNE", address="24 Coral-Pea Way, Cranbourne West Vic, 3977"),
            OfficeIn(label="CHADSTONE", address="Suite 797, Level 2 UL40, 1341 Dandenong Road, Chadstone, VIC 3148"),
            OfficeIn(label="BUNDOORA", address="Suite 279, Tenancy 202, Level 2, 1–3 Janefield Drive, Bundoora, VIC 3083"),
        ]
    )

    # Social links (optional)
    facebook: str = ""
    youtube: str = ""
    linkedin: str = ""
    instagram: str = ""
    whatsapp: str = ""
    discord: str = ""


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

    # Store a safe HTML variant for consistent sending.
    safe_html = html.escape(sig).replace("\n", "<br>")
    set_state(db, "signature_html", safe_html)

    db.commit()
    return SignatureOut(signature=sig)


@router.get("/signature/html")
def get_signature_html(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Return the stored HTML signature (for preview in settings UI)."""
    sig_html = (get_state(db, "signature_html") or "").strip()
    return {"html": sig_html}


@router.post("/signature/apply-template", response_model=SignatureOut)
def apply_app_signature_template(payload: SignatureTemplateIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Apply the app-managed signature template.

    Images reference local /static/signature/... paths and are embedded as CID when sending.
    """
    offices: list[tuple[str, str]] = []
    for o in payload.offices or []:
        label = (o.label or "").strip()
        addr = (o.address or "").strip()
        if label and addr:
            offices.append((label, addr))
    if not offices:
        offices = [("OFFICE", "")]

    prof = SignatureProfile(
        name=payload.name.strip(),
        title_line=payload.title_line.strip(),
        phone=payload.phone.strip(),
        email=payload.email.strip(),
        company=payload.company.strip(),
        offices=offices,
        facebook=(payload.facebook or "").strip(),
        youtube=(payload.youtube or "").strip(),
        linkedin=(payload.linkedin or "").strip(),
        instagram=(payload.instagram or "").strip(),
        whatsapp=(payload.whatsapp or "").strip(),
        discord=(payload.discord or "").strip(),
    )

    sig_html = build_signature_html(prof)
    sig_text = build_signature_text(prof)

    set_state(db, "signature_html", sig_html)
    set_state(db, "signature_text", sig_text)
    db.commit()
    return SignatureOut(signature=sig_text)


@router.post("/signature/upload/{asset_name}")
def upload_signature_asset(
    asset_name: str,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """Upload signature assets used by the app-managed signature template.

    Supported: profile | banner
    Saved with stable filenames under app/static/signature/.
    """
    allowed = {"profile": "profile.png", "banner": "banner.png"}
    if asset_name not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported asset. Use profile or banner")

    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "signature")
    os.makedirs(static_dir, exist_ok=True)
    out_path = os.path.join(static_dir, allowed[asset_name])

    with open(out_path, "wb") as f:
        f.write(content)

    return {"ok": True, "path": f"/static/signature/{allowed[asset_name]}"}


class GmailSignatureOut(BaseModel):
    send_as: str
    html: str
    text: str


@router.post("/signature/fetch-gmail", response_model=GmailSignatureOut)
def fetch_gmail_signature(
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Fetch the Gmail 'Send mail as' signature and store it as the app's signature_html.

    Notes:
    - Requires Gmail scope: https://www.googleapis.com/auth/gmail.settings.basic
    - For OAuth mode: user must (re)connect Google with that scope granted.
    - For service_account mode: DWD must include that scope in Admin Console.
    """
    service = get_gmail_service(db, scopes=[*GMAIL_SCOPES, GMAIL_SIGNATURE_SCOPE])
    uid = gmail_user_id()

    # Pick a sensible send-as identity
    sendas_list = service.users().settings().sendAs().list(userId=uid).execute().get("sendAs", []) or []
    target = None

    preferred = None
    if settings.GMAIL_AUTH_MODE == "service_account":
        preferred = (settings.IMPERSONATE_USER or "").strip().lower() or None
    else:
        preferred = (settings.DELEGATED_MAILBOX or "").strip().lower() or None
    if not preferred and settings.my_emails_list():
        preferred = settings.my_emails_list()[0].lower()

    for s in sendas_list:
        if preferred and (s.get("sendAsEmail") or "").lower() == preferred:
            target = s
            break
    if not target:
        for s in sendas_list:
            if s.get("isPrimary"):
                target = s
                break
    if not target and sendas_list:
        target = sendas_list[0]

    if not target:
        raise HTTPException(status_code=404, detail="No Gmail send-as identities found.")

    send_as = target.get("sendAsEmail") or "me"
    sendas = service.users().settings().sendAs().get(userId=uid, sendAsEmail=send_as).execute()
    sig_html = (sendas.get("signature") or "").strip()

    # Store
    set_state(db, "signature_html", sig_html)
    sig_text = _strip_html(sig_html) if sig_html else ""
    set_state(db, "signature_text", sig_text)
    db.commit()

    return GmailSignatureOut(send_as=send_as, html=sig_html, text=sig_text)
