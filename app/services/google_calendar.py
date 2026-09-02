from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.models import GoogleCalendarConnection


CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
STATE_PURPOSE = "my_space_google_calendar"


def calendar_flow() -> Flow:
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Google OAuth client is not configured.")
    if not settings.GOOGLE_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="GOOGLE_REDIRECT_URI is not configured.")
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=CALENDAR_SCOPES)
    flow.redirect_uri = settings.GOOGLE_REDIRECT_URI
    return flow


def make_calendar_state(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "purpose": STATE_PURPOSE,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )


def calendar_state_user_id(state: str | None) -> int | None:
    if not state:
        return None
    try:
        payload = jwt.decode(state, settings.JWT_SECRET, algorithms=["HS256"])
        if payload.get("purpose") != STATE_PURPOSE:
            return None
        return int(payload["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        return None


def credentials_for(connection: GoogleCalendarConnection) -> Credentials:
    return Credentials(
        token=connection.access_token,
        refresh_token=connection.refresh_token,
        token_uri=connection.token_uri or "https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=[scope for scope in (connection.scopes or "").split(",") if scope] or CALENDAR_SCOPES,
        expiry=connection.expiry,
    )


def persist_credentials(db: Session, connection: GoogleCalendarConnection, creds: Credentials) -> None:
    connection.access_token = creds.token
    if creds.refresh_token:
        connection.refresh_token = creds.refresh_token
    connection.token_uri = creds.token_uri or connection.token_uri
    connection.scopes = ",".join(creds.scopes or CALENDAR_SCOPES)
    connection.expiry = creds.expiry
    connection.updated_at = datetime.utcnow()
    db.commit()


def calendar_service(db: Session, connection: GoogleCalendarConnection):
    creds = credentials_for(connection)
    if creds.expired:
        if not creds.refresh_token:
            raise HTTPException(status_code=401, detail="Google Calendar access expired. Please reconnect.")
        try:
            creds.refresh(GoogleRequest())
            persist_credentials(db, connection, creds)
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Google Calendar access expired. Please reconnect.") from exc
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def save_calendar_connection(db: Session, user_id: int, creds: Credentials) -> GoogleCalendarConnection:
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    primary = service.calendarList().get(calendarId="primary").execute()
    email = str(primary.get("id") or primary.get("summary") or "").strip() or None
    connection = db.get(GoogleCalendarConnection, user_id)
    if connection is None:
        connection = GoogleCalendarConnection(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token,
            token_uri=creds.token_uri,
            scopes=",".join(creds.scopes or CALENDAR_SCOPES),
            expiry=creds.expiry,
            google_email=email,
        )
        db.add(connection)
    else:
        connection.google_email = email
        connection.access_token = creds.token
        if creds.refresh_token:
            connection.refresh_token = creds.refresh_token
        connection.token_uri = creds.token_uri
        connection.scopes = ",".join(creds.scopes or CALENDAR_SCOPES)
        connection.expiry = creds.expiry
        connection.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(connection)
    return connection


def normalize_event(event: dict) -> dict:
    start_data = event.get("start") or {}
    end_data = event.get("end") or {}
    start = start_data.get("dateTime") or start_data.get("date")
    end = end_data.get("dateTime") or end_data.get("date")
    return {
        "id": str(event.get("id") or ""),
        "title": event.get("summary") or "Busy",
        "start": start,
        "end": end,
        "all_day": bool(start_data.get("date") and not start_data.get("dateTime")),
        "location": event.get("location"),
        "html_link": event.get("htmlLink"),
        "status": event.get("status"),
        "source": "google",
    }
