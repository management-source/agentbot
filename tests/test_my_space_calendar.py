from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DEBUG"] = "false"
os.environ.setdefault("DATABASE_URL", "sqlite:///./app.db")

from app.authz import get_current_user
from app.db import get_db
from app.models import Base, GoogleCalendarConnection, User, UserRole
from app.routers import my_space
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
