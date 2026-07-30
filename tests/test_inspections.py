from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DEBUG"] = "false"
os.environ.setdefault("DATABASE_URL", "sqlite:///./app.db")

from app.authz import ROLE_PAGE_ACCESS_KEY, get_current_user, has_page_access
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import (
    AppState,
    Base,
    InspectionGeocodeCache,
    InspectionPlan,
    InspectionPlanStatus,
    InspectionVisit,
    ManagedProperty,
    User,
    UserRole,
)
from app.routers.inspections import router as inspections_router
from app.routers.user_auth import _normalize_role_page_access
from app.services import inspection_planner as planner


MAILBOX = "inspections@example.com"
OTHER_MAILBOX = "other@example.com"
PLAN_DATE = date(2026, 8, 3)


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
def seeded(db):
    alice = User(
        email="alice@example.com",
        name="Alice Agent",
        role=UserRole.PM,
        is_active=True,
        password_hash="not-used",
    )
    bob = User(
        email="bob@example.com",
        name="Bob Agent",
        role=UserRole.LEASING,
        is_active=True,
        password_hash="not-used",
    )
    inactive_agent = User(
        email="inactive@example.com",
        name="Inactive Agent",
        role=UserRole.PM,
        is_active=False,
        password_hash="not-used",
    )
    restricted = User(
        email="restricted@example.com",
        name="Restricted User",
        role=UserRole.READONLY,
        is_active=True,
        password_hash="not-used",
    )
    properties = {
        "a": ManagedProperty(
            mailbox=MAILBOX,
            property_address="1 Alpha Street",
            suburb="Melbourne",
            state_code="VIC",
            postcode="3000",
            is_active=True,
        ),
        "b": ManagedProperty(
            mailbox=MAILBOX,
            property_address="2 Bravo Street",
            suburb="Melbourne",
            state_code="VIC",
            postcode="3000",
            is_active=True,
        ),
        "c": ManagedProperty(
            mailbox=MAILBOX,
            property_address="3 Charlie Street",
            suburb="Melbourne",
            state_code="VIC",
            postcode="3000",
            is_active=True,
        ),
        "other": ManagedProperty(
            mailbox=OTHER_MAILBOX,
            property_address="4 Other Street",
            suburb="Melbourne",
            state_code="VIC",
            postcode="3000",
            is_active=True,
        ),
        "inactive": ManagedProperty(
            mailbox=MAILBOX,
            property_address="5 Archived Street",
            suburb="Melbourne",
            state_code="VIC",
            postcode="3000",
            is_active=False,
        ),
    }
    db.add_all([alice, bob, inactive_agent, restricted, *properties.values()])
    db.commit()
    return {
        "alice": alice,
        "bob": bob,
        "inactive_agent": inactive_agent,
        "restricted": restricted,
        **properties,
    }


def _install_geocoder_transport(monkeypatch, handler):
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)

    def client_factory(*_args, **_kwargs):
        return real_client(transport=transport)

    monkeypatch.setattr(planner.httpx, "Client", client_factory)


def test_property_address_and_geocoder_input_remove_duplicate_suburb():
    prop = ManagedProperty(
        mailbox=MAILBOX,
        property_address="1 Delphinium Road Pakenham",
        suburb="Pakenham",
        state_code="VIC",
        postcode="3810",
    )

    assert planner.full_property_address(prop) == "1 Delphinium Road, Pakenham VIC 3810"
    assert (
        planner._canonical_geocode_address(
            "1 Delphinium Road Pakenham, Pakenham VIC 3810"
        )
        == "1 DELPHINIUM ROAD PAKENHAM 3810"
    )


def test_vicmap_locator_uses_canonical_input_and_caches_result(db, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url).startswith(planner.VICMAP_GEOCODER_URL)
        assert request.url.params["SingleLine"] == "1 DELPHINIUM ROAD PAKENHAM 3810"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "address": "1 DELPHINIUM ROAD PAKENHAM 3810",
                        "location": {"x": 145.4636926, "y": -38.0881278},
                        "score": 100,
                        "attributes": {"Score": 100, "Ref_ID": "212913211"},
                    }
                ]
            },
        )

    _install_geocoder_transport(monkeypatch, handler)
    first, first_warning = planner.geocode_address(
        db,
        mailbox=MAILBOX,
        address="1 Delphinium Road Pakenham, Pakenham VIC 3810",
    )
    second, second_warning = planner.geocode_address(
        db,
        mailbox=MAILBOX,
        address="1 DELPHINIUM ROAD PAKENHAM 3810",
    )

    assert first_warning is None
    assert second_warning is None
    assert first == second
    assert first.provider == "vicmap-locator"
    assert first.latitude == pytest.approx(-38.0881278)
    assert first.longitude == pytest.approx(145.4636926)
    assert len(requests) == 1
    assert db.query(InspectionGeocodeCache).count() == 1


def test_vicmap_rejects_a_high_score_candidate_with_wrong_house_number(db, monkeypatch):
    def handler(request):
        if str(request.url).startswith(planner.VICMAP_GEOCODER_URL):
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "address": "10 DELPHINIUM ROAD PAKENHAM 3810",
                            "location": {"x": 145.46442, "y": -38.086865},
                            "score": 99,
                        }
                    ]
                },
            )
        assert str(request.url).startswith(planner.VICMAP_WFS_URL)
        return httpx.Response(200, json={"features": []})

    _install_geocoder_transport(monkeypatch, handler)
    point, warning = planner.geocode_address(
        db,
        mailbox=MAILBOX,
        address="1 Delphinium Road, Pakenham VIC 3810",
    )

    assert point is None
    assert warning == "Vicmap could not locate 1 Delphinium Road, Pakenham VIC 3810."
    assert db.query(InspectionGeocodeCache).count() == 0


def test_vicmap_timeout_uses_wfs_and_opens_the_locator_circuit(db, monkeypatch):
    locator_calls = 0
    wfs_calls = 0

    def handler(request):
        nonlocal locator_calls, wfs_calls
        if str(request.url).startswith(planner.VICMAP_GEOCODER_URL):
            locator_calls += 1
            raise httpx.ReadTimeout("locator timeout", request=request)
        wfs_calls += 1
        cql_filter = request.url.params["CQL_FILTER"]
        canonical = re.findall(r"'([^']+)'", cql_filter)[0]
        house_number = canonical.split()[0]
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {"ezi_address": canonical, "ufi": house_number},
                        "geometry": {
                            "type": "Point",
                            "coordinates": [145.46 + int(house_number) / 1000, -38.08],
                        },
                    }
                ]
            },
        )

    _install_geocoder_transport(monkeypatch, handler)
    provider_state = planner.GeocodeProviderState()
    first, first_warning = planner.geocode_address(
        db,
        mailbox=MAILBOX,
        address="1 Delphinium Road, Pakenham VIC 3810",
        provider_state=provider_state,
    )
    second, second_warning = planner.geocode_address(
        db,
        mailbox=MAILBOX,
        address="2 Delphinium Road, Pakenham VIC 3810",
        provider_state=provider_state,
    )

    assert first_warning is None
    assert second_warning is None
    assert first.provider == second.provider == "vicmap-wfs"
    assert locator_calls == 1
    assert wfs_calls == 2


def test_vicmap_expands_unit_address_road_type(db, monkeypatch):
    queries = []

    def handler(request):
        queries.append(request.url.params["SingleLine"])
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "address": "G15/2 BAILEY CRESCENT OAK PARK 3046",
                        "location": {"x": 144.9220213562, "y": -37.7206853615},
                        "score": 98.75,
                        "attributes": {"Ref_ID": "427919480"},
                    }
                ]
            },
        )

    _install_geocoder_transport(monkeypatch, handler)
    point, warning = planner.geocode_address(
        db,
        mailbox=MAILBOX,
        address="G15/2 Bailey Cres, Oak Park VIC 3046",
    )

    assert warning is None
    assert point.formatted_address == "G15/2 BAILEY CRESCENT OAK PARK 3046"
    assert point.latitude == pytest.approx(-37.7206853615)
    assert point.longitude == pytest.approx(144.9220213562)
    assert queries == ["G15/2 BAILEY CRESCENT OAK PARK 3046"]


def test_vicmap_unit_range_retries_lower_bound_without_changing_unit(db, monkeypatch):
    queries = []

    def handler(request):
        query = request.url.params["SingleLine"]
        queries.append(query)
        if query == "G05/16-18 DALGETY STREET OAKLEIGH 3166":
            return httpx.Response(200, json={"candidates": []})
        assert query == "G05/16 DALGETY STREET OAKLEIGH 3166"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "address": "G06/16 DALGETY STREET OAKLEIGH 3166",
                        "location": {"x": 145.090574, "y": -37.892113},
                        "score": 100,
                    },
                    {
                        "address": "G05/16 DALGETY STREET OAKLEIGH 3166",
                        "location": {"x": 145.0905677109, "y": -37.8921258802},
                        "score": 98.75,
                        "attributes": {"Ref_ID": "430346318"},
                    },
                ]
            },
        )

    _install_geocoder_transport(monkeypatch, handler)
    point, warning = planner.geocode_address(
        db,
        mailbox=MAILBOX,
        address="G05/16-18 Dalgety Street, Oakleigh VIC 3166",
    )

    assert warning is None
    assert point.formatted_address == "G05/16 DALGETY STREET OAKLEIGH 3166"
    assert point.latitude == pytest.approx(-37.8921258802)
    assert queries == [
        "G05/16-18 DALGETY STREET OAKLEIGH 3166",
        "G05/16 DALGETY STREET OAKLEIGH 3166",
    ]
    assert not planner._candidate_is_safe(
        "G15/2 BAILEY CRESCENT OAK PARK 3046",
        "G5/2 BAILEY CRESCENT OAK PARK 3046",
        100,
    )


def test_legacy_default_access_gains_inspections_without_overriding_custom_roles(db):
    legacy_pm_pages = [
        "portal",
        "notifications",
        "myspace",
        "inbox",
        "maintenance",
        "rent",
        "lease_renewals",
        "landlord_reports",
        "compliance",
        "coverage",
        "compliance_providers",
        "properties",
        "team",
        "activity",
        "system",
    ]
    row = AppState(
        key=ROLE_PAGE_ACCESS_KEY,
        value=json.dumps({UserRole.PM.value: legacy_pm_pages}),
    )
    db.add(row)
    db.commit()

    assert has_page_access(UserRole.PM, "inspections", db)
    normalized = _normalize_role_page_access({UserRole.PM.value: legacy_pm_pages})
    assert set(normalized[UserRole.PM.value]) == {*legacy_pm_pages, "inspections"}

    customized_pages = [page for page in legacy_pm_pages if page != "activity"]
    row.value = json.dumps({UserRole.PM.value: customized_pages})
    db.commit()
    assert not has_page_access(UserRole.PM, "inspections", db)
    normalized = _normalize_role_page_access({UserRole.PM.value: customized_pages})
    assert set(normalized[UserRole.PM.value]) == set(customized_pages)


def test_property_option_state_is_cleared_across_mailboxes_and_failed_loads():
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    helper_start = script.index("function clearPropertyOptionsState()")
    helper_end = script.index("async function refreshPropertyOptions()", helper_start)
    helper = script[helper_start:helper_end]

    assert "propertyOptionsCache = [];" in helper
    assert "propertyOptionsByLabel = {};" in helper
    for datalist_id in (
        "compliancePropertyOptions",
        "maintenancePropertyOptions",
        "inspectionPropertyOptions",
        "rentPropertyOptions",
        "landlordReportPropertyOptions",
        "leaseRenewalPropertyOptions",
        "propertyAddressSuggestions",
    ):
        assert datalist_id in helper

    change_start = script.index('sel.addEventListener("change"')
    change_reset = script.index("// refresh UI data under new mailbox", change_start)
    assert "clearPropertyOptionsState();" in script[change_start:change_reset]

    refresh_end = script.index("function resolvePropertySearchValue", helper_end)
    refresh = script[helper_end:refresh_end]
    assert 'if (!r.ok) {\n            clearPropertyOptionsState();' in refresh
    assert 'catch {\n        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;\n        clearPropertyOptionsState();' in refresh


def _install_local_route_provider(monkeypatch, seeded, travel=None, *, default_minutes=0):
    points = {
        "office": (0.0, 0.0),
        "a": (1.0, 1.0),
        "b": (2.0, 2.0),
        "c": (3.0, 3.0),
        "other": (4.0, 4.0),
        "inactive": (5.0, 5.0),
    }
    addresses = {"Office": "office"}
    for key in ("a", "b", "c", "other", "inactive"):
        addresses[planner.full_property_address(seeded[key])] = key
    labels_by_point = {point: label for label, point in points.items()}
    travel = travel or {}

    def fake_geocode(_db, *, mailbox, address, **_kwargs):
        del _db, mailbox
        label = addresses.get(address)
        if not label:
            return None, f"No local coordinate for {address}."
        latitude, longitude = points[label]
        return planner.GeocodePoint(latitude, longitude, address, provider="test"), None

    def fake_matrix(coordinates, **_kwargs):
        durations = []
        distances = []
        for origin in coordinates:
            duration_row = []
            distance_row = []
            for destination in coordinates:
                if origin == destination:
                    minutes = 0
                else:
                    pair = (labels_by_point[origin], labels_by_point[destination])
                    minutes = int(travel.get(pair, default_minutes))
                duration_row.append(minutes)
                distance_row.append(round(minutes / 2, 2))
            durations.append(duration_row)
            distances.append(distance_row)
        return durations, distances, "test-matrix", []

    def fake_geometry(coordinates, **_kwargs):
        return [[latitude, longitude] for latitude, longitude in coordinates], "test-route", None

    monkeypatch.setattr(planner, "geocode_address", fake_geocode)
    monkeypatch.setattr(planner, "road_matrix", fake_matrix)
    monkeypatch.setattr(planner, "route_geometry", fake_geometry)
    return points


def _visit(client_id, prop, agent_ids, **overrides):
    value = {
        "client_id": client_id,
        "property_id": prop.id,
        "agent_ids": list(agent_ids),
        "duration_minutes": 30,
        "buffer_minutes": 0,
        "earliest_time": None,
        "latest_time": None,
        "notes": None,
    }
    value.update(overrides)
    return value


def _optimize(db, seeded, visits, **overrides):
    values = {
        "mailbox": MAILBOX,
        "plan_name": "Monday inspections",
        "plan_date": PLAN_DATE,
        "day_start": "09:00",
        "day_end": "13:00",
        "start_address": "Office",
        "available_agent_ids": [seeded["alice"].id, seeded["bob"].id],
        "allow_agent_overlap": False,
        "visits": visits,
    }
    values.update(overrides)
    return planner.optimize_inspections(db, **values)


def _clock(value):
    return datetime.fromisoformat(value).strftime("%H:%M")


def _inspection_api_client(db, seeded):
    db.add(
        AppState(
            key=ROLE_PAGE_ACCESS_KEY,
            value=json.dumps({UserRole.PM.value: ["portal", "inspections"]}),
        )
    )
    db.commit()
    api = FastAPI()
    api.include_router(inspections_router)

    def override_db():
        yield db

    api.dependency_overrides[get_db] = override_db
    api.dependency_overrides[get_current_mailbox] = lambda: MAILBOX
    api.dependency_overrides[get_current_user] = lambda: seeded["alice"]
    return TestClient(api)


def _persist_plan_with_visit(
    db,
    seeded,
    *,
    name,
    status,
    scheduled_start,
    scheduled_end,
    buffer_minutes=0,
    allow_agent_overlap=False,
    mailbox=MAILBOX,
    plan_date=PLAN_DATE,
):
    plan = InspectionPlan(
        mailbox=mailbox,
        name=name,
        status=status,
        plan_date=plan_date,
        day_start="09:00",
        day_end="13:00",
        timezone=planner.DEFAULT_TIMEZONE,
        start_address="Office",
        allow_agent_overlap=allow_agent_overlap,
        provider="test",
        optimization_result_json="{}",
        created_by_user_id=seeded["alice"].id,
    )
    db.add(plan)
    db.flush()
    db.add(
        InspectionVisit(
            mailbox=mailbox,
            plan_id=plan.id,
            client_id=f"visit-{plan.id}",
            property_id=seeded["a"].id,
            property_address=planner.full_property_address(seeded["a"]),
            latitude=1.0,
            longitude=1.0,
            agent_ids_json=json.dumps([seeded["alice"].id]),
            agent_names_json=json.dumps([seeded["alice"].name]),
            duration_minutes=int((scheduled_end - scheduled_start).total_seconds() / 60),
            buffer_minutes=buffer_minutes,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            sequence=1,
            travel_minutes=0,
            distance_km=0,
        )
    )
    db.commit()
    db.refresh(plan)
    return plan


def test_route_order_uses_matrix_and_is_deterministic(db, seeded, monkeypatch):
    _install_local_route_provider(
        monkeypatch,
        seeded,
        {
            ("office", "a"): 20,
            ("office", "b"): 5,
            ("a", "b"): 5,
            ("b", "a"): 5,
        },
        default_minutes=40,
    )
    visits = [
        _visit("alpha", seeded["a"], [seeded["alice"].id]),
        _visit("bravo", seeded["b"], [seeded["alice"].id]),
    ]

    first = _optimize(db, seeded, visits, available_agent_ids=[seeded["alice"].id])
    second = _optimize(db, seeded, visits, available_agent_ids=[seeded["alice"].id])

    assert [row["client_id"] for row in first["visits"]] == ["bravo", "alpha"]
    assert [row["client_id"] for row in first["routes"][0]["stops"]] == ["bravo", "alpha"]
    assert first["available_agent_ids"] == [seeded["alice"].id]
    assert [
        (row["client_id"], row["scheduled_start"], row["scheduled_end"])
        for row in first["visits"]
    ] == [
        (row["client_id"], row["scheduled_start"], row["scheduled_end"])
        for row in second["visits"]
    ]
    assert first["provider"] == "vicmap+test-matrix"


@pytest.mark.parametrize("start_address", [None, "   "])
def test_optional_departure_starts_route_at_first_stop(db, seeded, monkeypatch, start_address):
    points = _install_local_route_provider(monkeypatch, seeded, default_minutes=25)
    result = _optimize(
        db,
        seeded,
        [_visit("first-stop", seeded["a"], [seeded["alice"].id])],
        start_address=start_address,
        available_agent_ids=[seeded["alice"].id],
    )

    assert len(result["visits"]) == 1
    assert result["visits"][0]["travel_minutes"] == 0
    assert result["routes"][0]["drive_minutes"] == 0
    assert result["routes"][0]["geometry"] == [[points["a"][0], points["a"][1]]]
    assert any("No departure address" in warning for warning in result["warnings"])


def test_post_inspection_buffer_delays_the_next_stop(db, seeded, monkeypatch):
    _install_local_route_provider(monkeypatch, seeded)
    result = _optimize(
        db,
        seeded,
        [
            _visit(
                "buffered",
                seeded["a"],
                [seeded["alice"].id],
                duration_minutes=30,
                buffer_minutes=20,
                earliest_time="09:00",
                latest_time="09:00",
            ),
            _visit("next", seeded["b"], [seeded["alice"].id]),
        ],
        available_agent_ids=[seeded["alice"].id],
    )

    by_id = {row["client_id"]: row for row in result["visits"]}
    assert _clock(by_id["buffered"]["scheduled_start"]) == "09:00"
    assert _clock(by_id["buffered"]["scheduled_end"]) == "09:30"
    assert _clock(by_id["next"]["scheduled_start"]) == "09:50"
    assert result["metrics"]["total_buffer_minutes"] == 20


def test_multi_agent_stop_blocks_both_routes(db, seeded, monkeypatch):
    _install_local_route_provider(monkeypatch, seeded)
    alice_id = seeded["alice"].id
    bob_id = seeded["bob"].id
    result = _optimize(
        db,
        seeded,
        [
            _visit(
                "shared",
                seeded["a"],
                [alice_id, bob_id],
                earliest_time="09:00",
                latest_time="09:00",
            ),
            _visit("alice-next", seeded["b"], [alice_id]),
            _visit("bob-next", seeded["c"], [bob_id]),
        ],
    )

    by_id = {row["client_id"]: row for row in result["visits"]}
    assert by_id["shared"]["agent_ids"] == [alice_id, bob_id]
    assert _clock(by_id["shared"]["scheduled_end"]) == "09:30"
    assert _clock(by_id["alice-next"]["scheduled_start"]) == "09:30"
    assert _clock(by_id["bob-next"]["scheduled_start"]) == "09:30"
    assert len(result["routes"]) == 2


def test_different_agents_can_inspect_concurrently(db, seeded, monkeypatch):
    _install_local_route_provider(monkeypatch, seeded)
    result = _optimize(
        db,
        seeded,
        [
            _visit(
                "alice-only",
                seeded["a"],
                [seeded["alice"].id],
                earliest_time="09:00",
                latest_time="09:00",
            ),
            _visit(
                "bob-only",
                seeded["b"],
                [seeded["bob"].id],
                earliest_time="09:00",
                latest_time="09:00",
            ),
        ],
    )

    assert len(result["visits"]) == 2
    assert {_clock(row["scheduled_start"]) for row in result["visits"]} == {"09:00"}


def test_overlap_option_can_keep_intentional_simultaneous_assignments(db, seeded, monkeypatch):
    _install_local_route_provider(monkeypatch, seeded)
    agent_id = seeded["alice"].id
    visits = [
        _visit("first", seeded["a"], [agent_id], earliest_time="09:00", latest_time="09:00"),
        _visit("second", seeded["b"], [agent_id], earliest_time="09:00", latest_time="09:00"),
    ]

    protected = _optimize(
        db,
        seeded,
        visits,
        available_agent_ids=[agent_id],
        allow_agent_overlap=False,
    )
    overridden = _optimize(
        db,
        seeded,
        visits,
        available_agent_ids=[agent_id],
        allow_agent_overlap=True,
    )

    assert len(protected["visits"]) == 1
    assert len(overridden["visits"]) == 2
    assert {_clock(row["scheduled_start"]) for row in overridden["visits"]} == {"09:00"}
    assert sum(len(row["conflicts"]) for row in overridden["visits"]) == 1
    assert any("overlap is enabled" in warning for warning in overridden["warnings"])


def test_fixed_deadline_is_scheduled_before_a_flexible_visit(db, seeded, monkeypatch):
    _install_local_route_provider(monkeypatch, seeded)
    agent_id = seeded["alice"].id
    result = _optimize(
        db,
        seeded,
        [
            _visit(
                "flexible",
                seeded["a"],
                [agent_id],
                duration_minutes=60,
                earliest_time="09:00",
                latest_time="12:00",
            ),
            _visit(
                "fixed",
                seeded["b"],
                [agent_id],
                duration_minutes=30,
                earliest_time="09:30",
                latest_time="09:30",
            ),
        ],
        available_agent_ids=[agent_id],
    )

    by_id = {row["client_id"]: row for row in result["visits"]}
    assert len(by_id) == 2
    assert _clock(by_id["fixed"]["scheduled_start"]) == "09:30"
    assert _clock(by_id["flexible"]["scheduled_start"]) == "10:00"


def test_saved_overlap_is_avoided_by_default_and_warned_on_override(db, seeded, monkeypatch):
    _install_local_route_provider(monkeypatch, seeded)
    existing = InspectionPlan(
        mailbox=MAILBOX,
        name="Existing plan",
        status=InspectionPlanStatus.PLANNED,
        plan_date=PLAN_DATE,
        day_start="09:00",
        day_end="13:00",
        timezone=planner.DEFAULT_TIMEZONE,
        start_address="Office",
        allow_agent_overlap=False,
        provider="test",
        optimization_result_json="{}",
        created_by_user_id=seeded["alice"].id,
    )
    db.add(existing)
    db.flush()
    db.add(
        InspectionVisit(
            mailbox=MAILBOX,
            plan_id=existing.id,
            client_id="existing",
            property_id=seeded["a"].id,
            property_address=planner.full_property_address(seeded["a"]),
            latitude=1.0,
            longitude=1.0,
            agent_ids_json=json.dumps([seeded["alice"].id]),
            agent_names_json=json.dumps([seeded["alice"].name]),
            duration_minutes=60,
            buffer_minutes=0,
            scheduled_start=datetime(2026, 8, 3, 9, 0),
            scheduled_end=datetime(2026, 8, 3, 10, 0),
            sequence=1,
            travel_minutes=0,
            distance_km=0,
        )
    )
    db.commit()
    visit = _visit(
        "new",
        seeded["b"],
        [seeded["alice"].id],
        earliest_time="09:00",
        latest_time="09:00",
    )

    safe = _optimize(
        db,
        seeded,
        [visit],
        available_agent_ids=[seeded["alice"].id],
        allow_agent_overlap=False,
    )
    overridden = _optimize(
        db,
        seeded,
        [visit],
        available_agent_ids=[seeded["alice"].id],
        allow_agent_overlap=True,
    )

    assert safe["visits"] == []
    assert safe["unscheduled"][0]["client_id"] == "new"
    assert len(overridden["visits"]) == 1
    assert overridden["visits"][0]["conflicts"][0]["plan_id"] == existing.id
    assert any("overlap is enabled" in warning for warning in overridden["warnings"])


def test_saved_visit_reserves_travel_to_its_fixed_location(db, seeded, monkeypatch):
    _install_local_route_provider(
        monkeypatch,
        seeded,
        {("b", "a"): 20},
        default_minutes=0,
    )
    _persist_plan_with_visit(
        db,
        seeded,
        name="Fixed later visit",
        status=InspectionPlanStatus.PLANNED,
        scheduled_start=datetime(2026, 8, 3, 10, 0),
        scheduled_end=datetime(2026, 8, 3, 10, 30),
    )

    result = _optimize(
        db,
        seeded,
        [
            _visit(
                "before-anchor",
                seeded["b"],
                [seeded["alice"].id],
                duration_minutes=30,
                earliest_time="09:20",
                latest_time="09:20",
            )
        ],
        available_agent_ids=[seeded["alice"].id],
    )
    overridden = _optimize(
        db,
        seeded,
        [
            _visit(
                "before-anchor",
                seeded["b"],
                [seeded["alice"].id],
                duration_minutes=30,
                earliest_time="09:20",
                latest_time="09:20",
            )
        ],
        available_agent_ids=[seeded["alice"].id],
        allow_agent_overlap=True,
    )

    assert result["visits"] == []
    assert result["unscheduled"][0]["client_id"] == "before-anchor"
    assert overridden["visits"][0]["conflicts"][0]["type"] == "agent_travel_conflict"
    assert overridden["visits"][0]["conflicts"][0]["required_travel_minutes"] == 20


def test_saved_visit_location_drives_travel_after_the_anchor(db, seeded, monkeypatch):
    _install_local_route_provider(
        monkeypatch,
        seeded,
        {("a", "b"): 20},
        default_minutes=0,
    )
    _persist_plan_with_visit(
        db,
        seeded,
        name="Fixed earlier visit",
        status=InspectionPlanStatus.CONFIRMED,
        scheduled_start=datetime(2026, 8, 3, 9, 0),
        scheduled_end=datetime(2026, 8, 3, 10, 0),
    )
    visit = _visit(
        "after-anchor",
        seeded["b"],
        [seeded["alice"].id],
        earliest_time="10:00",
        latest_time="11:00",
    )

    protected = _optimize(
        db,
        seeded,
        [visit],
        available_agent_ids=[seeded["alice"].id],
        allow_agent_overlap=False,
    )
    overridden = _optimize(
        db,
        seeded,
        [visit],
        available_agent_ids=[seeded["alice"].id],
        allow_agent_overlap=True,
    )

    assert _clock(protected["visits"][0]["scheduled_start"]) == "10:20"
    assert protected["visits"][0]["travel_minutes"] == 20
    assert _clock(overridden["visits"][0]["scheduled_start"]) == "10:20"


def test_infeasible_travel_window_has_an_explicit_reason(db, seeded, monkeypatch):
    _install_local_route_provider(
        monkeypatch,
        seeded,
        {("office", "c"): 45},
        default_minutes=0,
    )
    result = _optimize(
        db,
        seeded,
        [
            _visit(
                "too-tight",
                seeded["c"],
                [seeded["alice"].id],
                earliest_time="09:00",
                latest_time="09:00",
            )
        ],
        available_agent_ids=[seeded["alice"].id],
    )

    assert result["visits"] == []
    assert result["unscheduled"] == [
        {
            "client_id": "too-tight",
            "property_id": seeded["c"].id,
            "property_address": planner.full_property_address(seeded["c"]),
            "reason": "No selected agent can reach this inspection within its time window and the working day.",
        }
    ]


def test_inactive_agents_and_wrong_mailbox_properties_are_not_scheduled(db, seeded, monkeypatch):
    _install_local_route_provider(monkeypatch, seeded)
    with pytest.raises(planner.InspectionPlannerError, match="selected agents are unavailable"):
        _optimize(
            db,
            seeded,
            [_visit("valid", seeded["a"], [seeded["inactive_agent"].id])],
            available_agent_ids=[seeded["inactive_agent"].id],
        )

    result = _optimize(
        db,
        seeded,
        [_visit("wrong-mailbox", seeded["other"], [seeded["alice"].id])],
        available_agent_ids=[seeded["alice"].id],
    )
    assert result["visits"] == []
    assert result["unscheduled"][0]["reason"] == "The property is not active in this mailbox."


def test_plan_api_save_detail_status_mailbox_and_permission_roundtrip(db, seeded, monkeypatch):
    _install_local_route_provider(monkeypatch, seeded)
    db.add(
        AppState(
            key=ROLE_PAGE_ACCESS_KEY,
            value=json.dumps(
                {
                    UserRole.PM.value: ["portal", "inspections"],
                    UserRole.LEASING.value: ["portal", "inspections"],
                    UserRole.READONLY.value: ["portal"],
                }
            ),
        )
    )
    db.commit()

    api = FastAPI()
    api.include_router(inspections_router)
    context = {"mailbox": MAILBOX, "user": seeded["alice"]}

    def override_db():
        yield db

    api.dependency_overrides[get_db] = override_db
    api.dependency_overrides[get_current_mailbox] = lambda: context["mailbox"]
    api.dependency_overrides[get_current_user] = lambda: context["user"]
    client = TestClient(api)

    optimize_payload = {
        "plan_name": "Saved plan",
        "plan_date": PLAN_DATE.isoformat(),
        "day_start": "09:00",
        "day_end": "13:00",
        "start_address": "Office",
        "available_agent_ids": [seeded["alice"].id],
        "allow_agent_overlap": False,
        "visits": [
            _visit(
                "saved-stop",
                seeded["a"],
                [seeded["alice"].id],
                earliest_time="09:00",
                latest_time="09:00",
            )
        ],
    }
    optimized = client.post("/inspections/optimize", json=optimize_payload)
    assert optimized.status_code == 200
    assert optimized.json()["metrics"]["inspection_count"] == 1
    assert optimized.json()["integrity_token"]
    assert optimized.json()["available_agent_ids"] == [seeded["alice"].id]

    save_payload = {
        "name": "Saved plan",
        "status": "PLANNED",
        "plan_date": PLAN_DATE.isoformat(),
        "day_start": "09:00",
        "day_end": "13:00",
        "start_address": "Office",
        "allow_agent_overlap": False,
        "optimization_result": optimized.json(),
    }
    tampered_payload = json.loads(json.dumps(save_payload))
    tampered_payload["optimization_result"]["metrics"]["total_drive_minutes"] = 9999
    tampered = client.post("/inspections/plans", json=tampered_payload)
    assert tampered.status_code == 400
    assert "changed or has expired" in tampered.json()["detail"]

    saved = client.post("/inspections/plans", json=save_payload)
    assert saved.status_code == 201
    plan = saved.json()["plan"]
    plan_id = plan["id"]
    assert plan["visit_count"] == 1
    assert plan["visits"][0]["client_id"] == "saved-stop"

    listed = client.get("/inspections/plans")
    detail = client.get(f"/inspections/plans/{plan_id}")
    updated = client.patch(f"/inspections/plans/{plan_id}/status", json={"status": "CONFIRMED"})
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["plans"]] == [plan_id]
    assert detail.status_code == 200
    assert detail.json()["plan"]["optimization_result"]["provider"] == "vicmap+test-matrix"
    assert detail.json()["plan"]["optimization_result"]["available_agent_ids"] == [seeded["alice"].id]
    assert detail.json()["plan"]["optimization_result"]["visits"][0]["earliest_time"] == "09:00"
    assert detail.json()["plan"]["optimization_result"]["visits"][0]["latest_time"] == "09:00"
    assert updated.status_code == 200
    assert updated.json()["plan"]["status"] == "CONFIRMED"

    context["mailbox"] = OTHER_MAILBOX
    assert client.get(f"/inspections/plans/{plan_id}").status_code == 404
    assert client.get("/inspections/plans").json()["plans"] == []

    context["mailbox"] = MAILBOX
    context["user"] = seeded["restricted"]
    denied = client.get("/inspections/plans")
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Insufficient page access"


@pytest.mark.parametrize(
    (
        "draft_start",
        "draft_end",
        "draft_buffer",
        "active_start",
        "active_end",
        "active_buffer",
    ),
    [
        (
            datetime(2026, 8, 3, 10, 0),
            datetime(2026, 8, 3, 10, 30),
            20,
            datetime(2026, 8, 3, 10, 45),
            datetime(2026, 8, 3, 11, 15),
            0,
        ),
        (
            datetime(2026, 8, 3, 10, 40),
            datetime(2026, 8, 3, 11, 10),
            0,
            datetime(2026, 8, 3, 10, 0),
            datetime(2026, 8, 3, 10, 30),
            15,
        ),
    ],
    ids=["reactivated-plan-buffer", "existing-plan-buffer"],
)
def test_status_reactivation_rejects_agent_overlap_including_buffers(
    db,
    seeded,
    draft_start,
    draft_end,
    draft_buffer,
    active_start,
    active_end,
    active_buffer,
):
    active = _persist_plan_with_visit(
        db,
        seeded,
        name="Already active",
        status=InspectionPlanStatus.CONFIRMED,
        scheduled_start=active_start,
        scheduled_end=active_end,
        buffer_minutes=active_buffer,
    )
    draft = _persist_plan_with_visit(
        db,
        seeded,
        name="Reactivate me",
        status=InspectionPlanStatus.DRAFT,
        scheduled_start=draft_start,
        scheduled_end=draft_end,
        buffer_minutes=draft_buffer,
    )
    client = _inspection_api_client(db, seeded)

    response = client.patch(f"/inspections/plans/{draft.id}/status", json={"status": "PLANNED"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["plan_id"] == draft.id
    assert detail["plan_name"] == draft.name
    assert detail["target_status"] == "PLANNED"
    assert detail["conflict_count"] == 1
    assert "recalculate" in detail["resolution"].lower()
    conflict = detail["conflicts"][0]
    assert conflict["agent_id"] == seeded["alice"].id
    assert conflict["agent_name"] == seeded["alice"].name
    assert conflict["current_visit"]["blocked_until"] == (
        draft_end.replace(microsecond=0) + timedelta(minutes=draft_buffer)
    ).isoformat()
    assert conflict["conflicting_visit"]["plan_id"] == active.id
    assert conflict["conflicting_visit"]["blocked_until"] == (
        active_end.replace(microsecond=0) + timedelta(minutes=active_buffer)
    ).isoformat()
    db.expire_all()
    assert db.get(InspectionPlan, draft.id).status == InspectionPlanStatus.DRAFT


def test_status_reactivation_allows_overlap_only_when_plan_opted_in(db, seeded):
    _persist_plan_with_visit(
        db,
        seeded,
        name="Active",
        status=InspectionPlanStatus.PLANNED,
        scheduled_start=datetime(2026, 8, 3, 9, 0),
        scheduled_end=datetime(2026, 8, 3, 9, 30),
    )
    opted_in = _persist_plan_with_visit(
        db,
        seeded,
        name="Overlap allowed",
        status=InspectionPlanStatus.DRAFT,
        scheduled_start=datetime(2026, 8, 3, 9, 0),
        scheduled_end=datetime(2026, 8, 3, 9, 30),
        allow_agent_overlap=True,
    )
    client = _inspection_api_client(db, seeded)

    response = client.patch(f"/inspections/plans/{opted_in.id}/status", json={"status": "IN_PROGRESS"})

    assert response.status_code == 200
    assert response.json()["plan"]["status"] == "IN_PROGRESS"


def test_status_conflict_check_excludes_self_inactive_and_other_scopes(db, seeded):
    target = _persist_plan_with_visit(
        db,
        seeded,
        name="Target",
        status=InspectionPlanStatus.PLANNED,
        scheduled_start=datetime(2026, 8, 3, 9, 0),
        scheduled_end=datetime(2026, 8, 3, 9, 30),
    )
    _persist_plan_with_visit(
        db,
        seeded,
        name="Cancelled",
        status=InspectionPlanStatus.CANCELLED,
        scheduled_start=datetime(2026, 8, 3, 9, 0),
        scheduled_end=datetime(2026, 8, 3, 9, 30),
    )
    _persist_plan_with_visit(
        db,
        seeded,
        name="Other mailbox",
        status=InspectionPlanStatus.CONFIRMED,
        scheduled_start=datetime(2026, 8, 3, 9, 0),
        scheduled_end=datetime(2026, 8, 3, 9, 30),
        mailbox=OTHER_MAILBOX,
    )
    _persist_plan_with_visit(
        db,
        seeded,
        name="Other date",
        status=InspectionPlanStatus.IN_PROGRESS,
        scheduled_start=datetime(2026, 8, 4, 9, 0),
        scheduled_end=datetime(2026, 8, 4, 9, 30),
        plan_date=date(2026, 8, 4),
    )
    client = _inspection_api_client(db, seeded)

    response = client.patch(f"/inspections/plans/{target.id}/status", json={"status": "CONFIRMED"})

    assert response.status_code == 200
    assert response.json()["plan"]["status"] == "CONFIRMED"
