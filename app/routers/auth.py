from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials

from app.config import settings
from app.db import get_db
from app.models import OAuthToken

router = APIRouter()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
]


@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    """Lightweight status check for UI."""
    token = db.query(OAuthToken).filter(OAuthToken.provider == "google").first()
    return {
        "connected": bool(token),
        "provider": "google",
        "has_refresh_token": bool(token and token.refresh_token),
        "expiry": (token.expiry.isoformat() if token and token.expiry else None),
        "scopes": (token.scopes.split(",") if token and token.scopes else []),
    }


@router.post("/google/disconnect")
def google_disconnect(db: Session = Depends(get_db)):
    """Disconnect Google by deleting stored OAuth token (MVP single-row store)."""
    token = db.query(OAuthToken).filter(OAuthToken.provider == "google").first()
    if token is None:
        return {"ok": True, "message": "No Google connection found."}

    db.delete(token)
    db.commit()
    return {"ok": True, "message": "Google account disconnected."}

def _flow() -> Flow:
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Google OAuth client not configured")
    if not settings.GOOGLE_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="GOOGLE_REDIRECT_URI not configured")

    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            # Google library may reference these; keep for completeness
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }

    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = settings.GOOGLE_REDIRECT_URI
    return flow

@router.get("/google/login")
def google_login():
    flow = _flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return RedirectResponse(auth_url)

@router.get("/google/callback")
def google_callback(request: Request, db: Session = Depends(get_db)):
    err = request.query_params.get("error")
    if err:
        raise HTTPException(status_code=400, detail=f"Google OAuth error: {err}")

    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    flow = _flow()

    try:
        flow.fetch_token(code=code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {repr(e)}")

    creds: Credentials = flow.credentials

    token = db.query(OAuthToken).filter(OAuthToken.provider == "google").first()
    scopes_csv = ",".join(creds.scopes or [])

    if token is None:
        token = OAuthToken(
            provider="google",
            access_token=creds.token,
            refresh_token=creds.refresh_token,
            token_uri=creds.token_uri,
            client_id="env",          # do not store real id/secret in DB
            client_secret="env",
            scopes=scopes_csv,
            expiry=creds.expiry,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(token)
    else:
        token.access_token = creds.token
        if creds.refresh_token:
            token.refresh_token = creds.refresh_token
        token.token_uri = creds.token_uri
        token.scopes = scopes_csv
        token.expiry = creds.expiry
        token.updated_at = datetime.utcnow()

    db.commit()

    # UX: return user to the UI after successful connection.
    return RedirectResponse(url="/?connected=1")
