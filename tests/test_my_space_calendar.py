from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DEBUG"] = "false"
os.environ.setdefault("DATABASE_URL", "sqlite:///./app.db")

from app.authz import get_current_user
from app.db import get_db
from app.models import Base, GoogleCalendarConnection, User, UserRole
from app.routers import my_space
from app.services import google_calendar
from app.services.google_calendar import calendar_state_user_id, make_calendar_state, normalize_event


@pytest.fixture()
def calendar_client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    users = [
        User(email="one@example.com", name="One", role=UserRole.PM, is_active=True, password_hash="x"),
        User(email="two@example.com", name="Two", role=UserRole.PM, is_active=True, password_hash="x"),
    ]
    db.add_all(users)
    db.commit()
    for user in users:
        db.refresh(user)
    app = FastAPI()
    app.include_router(my_space.router)
    context = {"user": users[0]}

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: context["user"]
    try:
        yield TestClient(app), db, users, context
    finally:
        db.close()
        engine.dispose()


def test_calendar_connection_is_private_per_user(calendar_client):
    client, db, users, context = calendar_client
    db.add(GoogleCalendarConnection(user_id=users[0].id, google_email="staff@gmail.com", access_token="token"))
    db.commit()

    response = client.get("/my-space/calendar/status")
    assert response.status_code == 200
    assert response.json()["email"] == "staff@gmail.com"

    context["user"] = users[1]
    response = client.get("/my-space/calendar/status")
    assert response.status_code == 200
    assert response.json() == {"connected": False, "email": None, "updated_at": None}


def test_calendar_events_are_normalized_and_cancelled_items_are_hidden(calendar_client, monkeypatch):
    client, db, users, _ = calendar_client
    db.add(GoogleCalendarConnection(user_id=users[0].id, google_email="staff@gmail.com", access_token="token"))
    db.commit()

    class Execute:
        def execute(self):
            return {"items": [
                {"id": "event-1", "summary": "Routine inspection", "start": {"dateTime": "2026-09-10T09:00:00+10:00"}, "end": {"dateTime": "2026-09-10T10:00:00+10:00"}, "htmlLink": "https://calendar.google.com/event"},
                {"id": "cancelled", "status": "cancelled", "start": {"date": "2026-09-11"}},
            ]}

    class Events:
        def list(self, **_kwargs):
            return Execute()

    class Service:
        def events(self):
            return Events()

    monkeypatch.setattr(my_space, "calendar_service", lambda _db, _connection: Service())
    response = client.get(
        "/my-space/calendar/events",
        params={"time_min": "2026-09-01T00:00:00Z", "time_max": "2026-10-01T00:00:00Z"},
    )
    assert response.status_code == 200
    assert response.json()["events"] == [{
        "id": "event-1", "title": "Routine inspection", "start": "2026-09-10T09:00:00+10:00",
        "end": "2026-09-10T10:00:00+10:00", "all_day": False, "location": None,
        "html_link": "https://calendar.google.com/event", "status": None, "source": "google",
    }]


def test_calendar_oauth_state_is_signed_for_the_local_user():
    state = make_calendar_state(42)
    assert calendar_state_user_id(state) == 42
    assert calendar_state_user_id(f"{state}broken") is None


def test_all_day_event_normalization():
    event = normalize_event({"id": "holiday", "summary": "Office closed", "start": {"date": "2026-12-25"}, "end": {"date": "2026-12-26"}})
    assert event["all_day"] is True
    assert event["start"] == "2026-12-25"


def test_connection_is_saved_when_calendar_metadata_lookup_fails(calendar_client, monkeypatch):
    _client, db, users, _context = calendar_client

    class Credentials:
        token = "access-token"
        refresh_token = "refresh-token"
        token_uri = "https://oauth2.googleapis.com/token"
        scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
        expiry = None

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("Calendar API is not enabled")

    monkeypatch.setattr(google_calendar, "build", unavailable)
    connection = google_calendar.save_calendar_connection(db, users[0].id, Credentials())

    assert connection.access_token == "access-token"
    assert connection.refresh_token == "refresh-token"
    assert db.get(GoogleCalendarConnection, users[0].id) is not None


def test_calendar_oauth_url_does_not_require_an_in_memory_pkce_verifier(monkeypatch):
    monkeypatch.setattr(google_calendar.settings, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(google_calendar.settings, "GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(
        google_calendar.settings,
        "GOOGLE_REDIRECT_URI",
        "https://agentbot-aw74.onrender.com/auth/google/callback",
    )

    flow = google_calendar.calendar_flow()
    authorization_url, _state = flow.authorization_url(state="signed-state")
    query = parse_qs(urlparse(authorization_url).query)

    assert "code_challenge" not in query
    assert flow.code_verifier is None


def test_calendar_api_disabled_error_identifies_oauth_client_project(calendar_client, monkeypatch):
    client, db, users, _context = calendar_client
    db.add(GoogleCalendarConnection(user_id=users[0].id, access_token="token"))
    db.commit()

    class Response:
        status = 403
        reason = "Forbidden"

    class Execute:
        def execute(self):
            raise HttpError(
                Response(),
                b'{"error":{"code":403,"message":"Calendar API has not been used in project 123 or it is disabled.","errors":[{"reason":"accessNotConfigured"}]}}',
            )

    class Events:
        def list(self, **_kwargs):
            return Execute()

    class Service:
        def events(self):
            return Events()

    monkeypatch.setattr(my_space, "calendar_service", lambda _db, _connection: Service())
    response = client.get(
        "/my-space/calendar/events",
        params={"time_min": "2026-09-01T00:00:00Z", "time_max": "2026-10-01T00:00:00Z"},
    )

    assert response.status_code == 502
    assert "project that owns this OAuth client ID" in response.json()["detail"]
