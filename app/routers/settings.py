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


## Gmail signature fetch removed: we use app-managed signature by default.


@router.post("/signature/apply-template", response_model=SignatureOut)
def apply_app_signature_template(payload: SignatureTemplateIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Use an app-managed signature template (recommended).

    This produces a robust, email-client-safe HTML signature that closely matches the user's target design.
    Images are referenced via /static/signature/... and are embedded as CID images at send time.
    """
    offices = []
    for o in payload.offices or []:
        label = (o.get("label") or "").strip()
        addr = (o.get("address") or "").strip()
        if label and addr:
            offices.append((label, addr))
    if not offices:
        offices = [("OFFICE", "")]

    profile = SignatureProfile(
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

    html_sig = build_signature_html(profile)
    text_sig = build_signature_text(profile)

    set_state(db, "signature_html", html_sig)
    set_state(db, "signature_text", text_sig)
    db.commit()
    return SignatureOut(signature=text_sig)


@router.post("/signature/upload/{asset_name}")
def upload_signature_asset(
    asset_name: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Upload signature assets used by the app-managed signature template.

    asset_name: profile | banner | icon-facebook | etc.
    For now we support: profile, banner.
    """
    allowed = {"profile": "profile.png", "banner": "banner.png"}
    if asset_name not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported asset. Use profile or banner")

    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
    out_path = os.path.join(static_dir, "signature", allowed[asset_name])
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "wb") as f:
        f.write(content)

    return {"ok": True, "path": f"/static/signature/{allowed[asset_name]}"}
