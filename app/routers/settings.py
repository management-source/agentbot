from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.authz import get_current_user
from app.config import settings
from app.db import get_db
from app.services.state import get_state, set_state
import html

from app.services.signature_template import SignatureProfile, build_signature_html, build_signature_text
import os



router = APIRouter()


class SignatureOut(BaseModel):
    signature: str


class SignatureIn(BaseModel):
    signature: str


class SignatureTemplateIn(BaseModel):
    # Defaults are set to match the user's screenshot as closely as possible.
    name: str = "Jessica Gale"
    title_line: str = "Co-Founder | Principal Officer in Effective Control | Senior Property Manager | Licensed Estate Agent"
    phone: str = "0422 643 451"
    email: str = "admin@donspremier.com.au"
    company: str = "DONS PREMIER ESTATE AGENTS"

    office1_label: str = "CRANBOURNE"
    office1_addr: str = "24 Coral-Pea Way, Cranbourne West Vic, 3977"
    office2_label: str = "CHADSTONE"
    office2_addr: str = "Suite 797, Level 2 UL40, 1341 Dandenong Road, Chadstone, VIC 3148"
    office3_label: str = "BUNDOORA"
    office3_addr: str = "Suite 279, Tenancy 202, Level 2, 1–3 Janefield Drive, Bundoora, VIC 3083"

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
    # If user sets a plain-text signature, also store a safe HTML variant.
    # This ensures consistent behavior for outgoing HTML emails.
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

    This stores both signature_html and signature_text. Images reference local /static/signature/... paths and are later embedded as CID when sending.
    """

    offices = [
        (payload.office1_label, payload.office1_addr),
        (payload.office2_label, payload.office2_addr),
        (payload.office3_label, payload.office3_addr),
    ]

    prof = SignatureProfile(
        name=payload.name,
        title_line=payload.title_line,
        phone=payload.phone,
        email=payload.email,
        company=payload.company,
        offices=offices,
        facebook=payload.facebook,
        youtube=payload.youtube,
        linkedin=payload.linkedin,
        instagram=payload.instagram,
        whatsapp=payload.whatsapp,
        discord=payload.discord,
    )

    sig_html = build_signature_html(prof)
    sig_text = build_signature_text(prof)

    set_state(db, "signature_html", sig_html)
    set_state(db, "signature_text", sig_text)
    db.commit()
    return SignatureOut(signature=sig_text)


@router.post("/signature/assets/{asset_name}")
def upload_signature_asset(asset_name: str, file: UploadFile = File(...), user=Depends(get_current_user)):
    """Upload an app-managed signature asset.

    Supported assets:
      - profile (profile.png)
      - banner (banner.png)
      - icon files under icons/ (facebook.png, ...)

    Note: we keep filenames stable so the signature template always works.
    """

    allowed = {"profile", "banner"}
    if asset_name not in allowed:
        raise HTTPException(status_code=400, detail="Invalid asset name. Use 'profile' or 'banner'.")

    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # Always store as .png to keep URLs stable; user can upload PNG/JPG; we do not convert here.
    # If user uploads JPG, it will still be served with application/octet-stream but embedded as CID during send.
    static_base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "signature")
    os.makedirs(static_base, exist_ok=True)
    path = os.path.join(static_base, f"{asset_name}.png")
    with open(path, "wb") as f:
        f.write(content)

    return {"ok": True, "path": f"/static/signature/{asset_name}.png"}



# --- Gmail signature fetch ---
# Fetches the exact signature HTML stored in Gmail settings for a chosen "Send mail as" identity.
# Stores into state keys: signature_html, signature_text

from app.services.gmail_client import get_gmail_service, GMAIL_SIGNATURE_SCOPE, GMAIL_SCOPES  # type: ignore
import html2text


class GmailSignatureIn(BaseModel):
    # If provided, fetch signature for this specific "Send mail as" address (e.g. admin@donspremier.com.au)
    send_as_email: str | None = None


@router.post("/signature/fetch-gmail", response_model=SignatureOut)
def fetch_gmail_signature(payload: GmailSignatureIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Fetch the Gmail signature HTML from Google and store it for use in outgoing emails.

    Notes:
    - Requires OAuth scope gmail.settings.basic (or DWD authorized scope for service-account mode).
    - The stored HTML is later processed by embed_images_as_cid() during sending.
    """
    # Request signature scope in addition to normal Gmail scopes
    svc = get_gmail_service(db=db, scopes=list(dict.fromkeys(GMAIL_SCOPES + [GMAIL_SIGNATURE_SCOPE])))

    user_id = "me"
    send_as_list = svc.users().settings().sendAs().list(userId=user_id).execute()
    send_as = send_as_list.get("sendAs", []) or []

    if not send_as:
        raise HTTPException(status_code=404, detail="No 'Send mail as' identities found in Gmail settings.")

    requested = (payload.send_as_email or "").strip().lower()
    chosen = None

    if requested:
        for item in send_as:
            if (item.get("sendAsEmail") or "").strip().lower() == requested:
                chosen = item
                break
        if not chosen:
            raise HTTPException(status_code=404, detail=f"Send-as identity not found: {payload.send_as_email}")

    if not chosen:
        # Prefer the primary identity; otherwise the first one returned
        for item in send_as:
            if item.get("isPrimary"):
                chosen = item
                break
        chosen = chosen or send_as[0]

    send_as_email = chosen.get("sendAsEmail")
    if not send_as_email:
        raise HTTPException(status_code=500, detail="Chosen send-as identity is missing sendAsEmail.")

    details = svc.users().settings().sendAs().get(userId=user_id, sendAsEmail=send_as_email).execute()
    sig_html = (details.get("signature") or "").strip()

    if not sig_html:
        raise HTTPException(status_code=404, detail=f"No Gmail signature found for {send_as_email}.")

    sig_text = html2text.html2text(sig_html).strip()

    set_state(db, "signature_html", sig_html)
    set_state(db, "signature_text", sig_text)
    db.commit()
    return SignatureOut(signature=sig_text)

