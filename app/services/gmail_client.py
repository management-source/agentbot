from __future__ import annotations

from email.utils import parseaddr
from sqlalchemy.orm import Session

from googleapiclient.discovery import build

from app.config import settings

# OAuth (user flow)
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from app.models import OAuthToken

# Service Account (Domain-Wide Delegation)
from google.oauth2 import service_account


GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.labels",
]


def gmail_user_id() -> str:
    """Return the Gmail userId to operate on for API calls.

    - service_account mode: always "me" (credentials already impersonate IMPERSONATE_USER)
    - oauth mode: "me" unless DELEGATED_MAILBOX is set (Gmail UI delegation)
    """
    if settings.GMAIL_AUTH_MODE == "service_account":
        return "me"
    mb = (settings.DELEGATED_MAILBOX or "").strip()
    return mb if mb else "me"


def get_gmail_service(db: Session | None = None):
    """Build a Gmail API service using either OAuth or Service Account DWD."""
    if settings.GMAIL_AUTH_MODE == "service_account":
        info = settings.service_account_info()
        if not info:
            raise RuntimeError("Service account JSON is not configured.")
        subject = (settings.IMPERSONATE_USER or "").strip()
        if not subject:
            raise RuntimeError("IMPERSONATE_USER is not configured.")
        creds = service_account.Credentials.from_service_account_info(info, scopes=GMAIL_SCOPES).with_subject(subject)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    # OAuth mode
    if db is None:
        raise RuntimeError("Database session is required for OAuth mode.")

    token = db.query(OAuthToken).filter(OAuthToken.provider == "google").first()
    if not token:
        raise RuntimeError("Google is not connected. Visit /auth/google/login first.")

    scopes = [s for s in (token.scopes or "").split(",") if s] or GMAIL_SCOPES

    creds = Credentials(
        token=token.access_token,
        refresh_token=token.refresh_token,
        token_uri=token.token_uri or "https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=scopes,
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        token.access_token = creds.token
        token.expiry = creds.expiry
        db.commit()

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def parse_email_address(from_header: str | None) -> tuple[str | None, str | None]:
    """Parse an RFC5322-ish From header into (display_name, email)."""
    if not from_header:
        return None, None
    name, email = parseaddr(from_header)
    name = (name or "").strip() or None
    email = (email or "").strip() or None
    return name, (email.lower() if email else None)


def is_from_me(from_header: str | None) -> bool:
    """True if the message From header matches any configured MY_EMAILS."""
    _name, email = parse_email_address(from_header)
    if not email:
        return False
    my = set(settings.my_emails_list())
    return bool(my) and email.lower() in my
