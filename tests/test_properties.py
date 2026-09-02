from __future__ import annotations

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DEBUG"] = "false"
os.environ.setdefault("DATABASE_URL", "sqlite:///./app.db")

from app.authz import get_current_user
from app.db import get_db
from app.db_migrate import migrate
from app.deps import get_current_mailbox
from app.models import Base, ManagedProperty, User, UserRole
from app.routers.properties import router as properties_router


MAILBOX = "listings@example.com"
OTHER_MAILBOX = "other-listings@example.com"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def api_client(db):
    user = User(
        email="listings-agent@example.com",
        name="Listings Agent",
        role=UserRole.SALES,
        is_active=True,
        password_hash="not-used",
    )
    db.add(user)
    db.commit()

    api = FastAPI()
    api.include_router(properties_router, prefix="/properties")
    context = {"mailbox": MAILBOX}

    def override_db():
        yield db

    api.dependency_overrides[get_db] = override_db
    api.dependency_overrides[get_current_mailbox] = lambda: context["mailbox"]
    api.dependency_overrides[get_current_user] = lambda: user
    return TestClient(api), context


def _listing_payload(address: str = "10 Listing Lane") -> dict[str, object]:
    return {
        "property_address": address,
        "suburb": "Cranbourne West",
        "state_code": "VIC",
        "postcode": "3977",
        "property_type": "House",
        "listing_status": "open",
        "key_number": "legacy-value-is-replaced",
        "occupants": [
            {
                "name": "Alex Occupant",
                "email": "alex@example.com",
                "phone": "03 9000 0000",
                "lease_start_date": "2026-02-01",
                "lease_end_date": "2027-01-31",
                "lease_amount": "650",
                "lease_frequency": "Weekly",
            }
        ],
        "keys": [
            {
                "key_number": "KEY-101",
                "description": "Front door",
                "location": "Office cabinet",
            }
        ],
        "social_media_history": [
            {
                "date": "2026-09-01",
                "platform": "Instagram",
                "url": "https://example.com/listing-post",
                "notes": "Launch campaign",
            }
        ],
        "inspections": [
            {
                "id": "inspection-one",
                "date": "2026-09-05",
                "start_time": "10:00",
                "finish_time": "10:30",
                "notes": "Saturday open home",
            }
        ],
    }


def test_create_and_get_property_round_trip_listing_sections(db, api_client):
    client, _context = api_client
    response = client.post("/properties", json=_listing_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["listing_status"] == "OPEN"
    assert body["key_number"] == "KEY-101"
    assert body["keys"] == [
        {"key_number": "KEY-101", "description": "Front door", "location": "Office cabinet"}
    ]
    assert body["occupants"][0] == {
        "name": "Alex Occupant",
        "email": "alex@example.com",
        "mobile": "",
        "phone": "03 9000 0000",
        "phones": ["03 9000 0000"],
        "is_company": False,
        "lease_start_date": "2026-02-01",
        "lease_end_date": "2027-01-31",
        "lease_amount": "650",
        "lease_frequency": "Weekly",
    }
    assert body["tenants"]["contacts"] == body["occupants"]
    assert body["social_media_history"] == _listing_payload()["social_media_history"]
    assert body["inspections"] == _listing_payload()["inspections"]

    detail = client.get(f"/properties/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["occupants"] == body["occupants"]
    assert detail.json()["keys"] == body["keys"]

    stored = db.get(ManagedProperty, body["id"])
    assert json.loads(stored.tenants_json or "{}")["contacts"] == body["occupants"]
    assert json.loads(stored.keys_json or "[]") == body["keys"]
    assert json.loads(stored.social_media_history_json or "[]") == body["social_media_history"]
    assert json.loads(stored.listing_inspections_json or "[]") == body["inspections"]


def test_update_preserves_omitted_sections_and_syncs_explicit_aliases(api_client):
    client, _context = api_client
    created = client.post("/properties", json=_listing_payload()).json()
    property_id = created["id"]

    preserved = client.put(f"/properties/{property_id}", json={"property_type": "Townhouse"})
    assert preserved.status_code == 200, preserved.text
    preserved_body = preserved.json()
    for field in ("occupants", "keys", "social_media_history", "inspections"):
        assert preserved_body[field] == created[field]

    replaced = client.put(
        f"/properties/{property_id}",
        json={
            "listing_status": "closed",
            "occupants": [],
            "key_number": "ignored-when-keys-are-present",
            "keys": [
                {
                    "key_number": "KEY-202",
                    "description": "Side gate",
                    "location": "Lock box",
                }
            ],
        },
    )
    assert replaced.status_code == 200, replaced.text
    replaced_body = replaced.json()
    assert replaced_body["listing_status"] == "CLOSED"
    assert replaced_body["occupants"] == []
    assert replaced_body["tenants"]["contacts"] == []
    assert replaced_body["key_number"] == "KEY-202"
    assert replaced_body["keys"][0]["description"] == "Side gate"
    assert replaced_body["social_media_history"] == created["social_media_history"]
    assert replaced_body["inspections"] == created["inspections"]

    legacy_key_update = client.put(f"/properties/{property_id}", json={"key_number": "KEY-303"})
    assert legacy_key_update.status_code == 200
    assert legacy_key_update.json()["key_number"] == "KEY-303"
    assert legacy_key_update.json()["keys"][0] == {
        "key_number": "KEY-303",
        "description": "Side gate",
        "location": "Lock box",
    }

    cleared = client.put(
        f"/properties/{property_id}",
        json={"social_media_history": [], "inspections": []},
    )
    assert cleared.status_code == 200
    assert cleared.json()["social_media_history"] == []
    assert cleared.json()["inspections"] == []
    assert cleared.json()["keys"] == legacy_key_update.json()["keys"]


def test_listing_status_and_inspection_times_are_validated_without_partial_writes(db, api_client):
    client, _context = api_client
    invalid_status = _listing_payload()
    invalid_status["listing_status"] = "withdrawn"
    response = client.post("/properties", json=invalid_status)
    assert response.status_code == 400
    assert db.query(ManagedProperty).count() == 0

    invalid_time = _listing_payload()
    invalid_time["inspections"] = [
        {
            "date": "2026-09-05",
            "start_time": "10:30",
            "finish_time": "10:00",
            "notes": "Invalid window",
        }
    ]
    response = client.post("/properties", json=invalid_time)
    assert response.status_code == 400
    assert "after start time" in response.json()["detail"]
    assert db.query(ManagedProperty).count() == 0

    created = client.post("/properties", json=_listing_payload()).json()
    rejected_update = client.put(
        f"/properties/{created['id']}",
        json={
            "inspections": [
                {
                    "date": "2026-09-06",
                    "start_time": "11:00",
                    "finish_time": "11:00",
                }
            ]
        },
    )
    assert rejected_update.status_code == 400
    detail = client.get(f"/properties/{created['id']}").json()
    assert detail["inspections"] == created["inspections"]
    assert detail["listing_status"] == "OPEN"


def test_new_listing_fields_are_searchable_and_detail_is_mailbox_isolated(api_client):
    client, context = api_client
    payload = _listing_payload("22 Searchable Street")
    payload["listing_status"] = "closed"
    created = client.post("/properties", json=payload).json()

    for query in ("CLOSED", "Office cabinet", "Launch campaign", "Saturday open home", "Alex Occupant"):
        response = client.get("/properties", params={"query": query})
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == [created["id"]]

    context["mailbox"] = OTHER_MAILBOX
    assert client.get(f"/properties/{created['id']}").status_code == 404
    assert client.get("/properties", params={"query": "Searchable"}).json()["items"] == []


def test_existing_sqlite_managed_properties_gain_listing_fields_idempotently():
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE managed_properties (
                        id INTEGER PRIMARY KEY,
                        mailbox VARCHAR NOT NULL,
                        property_address VARCHAR NOT NULL,
                        key_number VARCHAR
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "INSERT INTO managed_properties "
                    "(id, mailbox, property_address, key_number) "
                    "VALUES (1, 'legacy@example.com', '1 Legacy Road', 'LEGACY-1')"
                )
            )

        migrate(engine)
        migrate(engine)

        columns = {column["name"] for column in inspect(engine).get_columns("managed_properties")}
        assert {
            "listing_status",
            "keys_json",
            "social_media_history_json",
            "listing_inspections_json",
        }.issubset(columns)
        indexes = {index["name"] for index in inspect(engine).get_indexes("managed_properties")}
        assert "ix_managed_properties_listing_status" in indexes
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT listing_status, key_number, keys_json, "
                    "social_media_history_json, listing_inspections_json "
                    "FROM managed_properties WHERE id = 1"
                )
            ).one()
        assert tuple(row) == ("OPEN", "LEGACY-1", None, None, None)
    finally:
        engine.dispose()
