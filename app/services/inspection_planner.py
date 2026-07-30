from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    InspectionGeocodeCache,
    InspectionPlan,
    InspectionPlanStatus,
    InspectionVisit,
    ManagedProperty,
    User,
)


VICMAP_GEOCODER_URL = settings.INSPECTIONS_VICMAP_URL.rstrip("/")
VICMAP_WFS_URL = settings.INSPECTIONS_VICMAP_WFS_URL.rstrip("/")
OSRM_BASE_URL = settings.INSPECTIONS_OSRM_BASE_URL.rstrip("/")
HTTP_TIMEOUT_SECONDS = max(1.0, float(settings.INSPECTIONS_HTTP_TIMEOUT_SECONDS))
PROVIDER_BUDGET_SECONDS = max(5.0, float(settings.INSPECTIONS_PROVIDER_BUDGET_SECONDS))
VICMAP_MINIMUM_SCORE = 90.0
DEFAULT_TIMEZONE = "Australia/Melbourne"
ROUTE_COLORS = (
    "#2563eb",
    "#dc2626",
    "#059669",
    "#7c3aed",
    "#d97706",
    "#0891b2",
    "#db2777",
    "#4f46e5",
)


class InspectionPlannerError(Exception):
    def __init__(self, detail: str | dict[str, Any], status_code: int = 400):
        super().__init__(str(detail))
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class GeocodePoint:
    latitude: float
    longitude: float
    formatted_address: str
    provider: str = "vicmap"


@dataclass
class GeocodeProviderState:
    """Per-optimization circuit breakers for the external address providers."""

    unavailable: dict[str, str] = field(default_factory=dict)


@dataclass
class AgentRouteState:
    user: User
    current_time: datetime
    current_coordinate_index: int | None = None
    stops: list[dict[str, Any]] = field(default_factory=list)
    total_drive_minutes: int = 0
    total_distance_km: float = 0.0


@dataclass
class ExistingInspectionWindow:
    agent_id: int
    scheduled_start: datetime
    scheduled_end: datetime
    blocked_until: datetime
    plan_id: int
    plan_name: str
    property_id: int
    property_address: str
    latitude: float
    longitude: float
    coordinate_index: int | None = None


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def full_property_address(prop: ManagedProperty) -> str:
    street = ", ".join(part for part in (prop.property_address, prop.address_line_2) if _clean_text(part))
    # Some CRM imports put the suburb in both the street field and the suburb
    # column. Avoid sending values such as "... Pakenham, Pakenham VIC 3810"
    # to address locators, while preserving the human-friendly display format.
    for repeated_part in (prop.postcode, prop.state_code, prop.suburb):
        repeated = _clean_text(repeated_part)
        if not repeated or not street:
            continue
        suffix = r"\s+".join(re.escape(word) for word in repeated.split())
        street = re.sub(rf"(?:,\s*|\s+){suffix}\s*$", "", street, flags=re.IGNORECASE).strip(" ,")
    locality = " ".join(part for part in (prop.suburb, prop.state_code, prop.postcode) if _clean_text(part))
    return ", ".join(part for part in (street, locality) if part)


def parse_clock(value: str, *, field_name: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", _clean_text(value))
    if not match:
        raise InspectionPlannerError(f"{field_name} must use HH:MM format.")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise InspectionPlannerError(f"{field_name} must be a valid time.")
    return hour, minute


def clock_datetime(plan_date: date, value: str, *, field_name: str) -> datetime:
    hour, minute = parse_clock(value, field_name=field_name)
    return datetime(plan_date.year, plan_date.month, plan_date.day, hour, minute)


def _address_cache_key(mailbox: str, address: str) -> str:
    normalized = _clean_text(address).casefold()
    return hashlib.sha256(f"{mailbox.strip().lower()}|{normalized}".encode("utf-8")).hexdigest()


def _optimization_integrity_token(mailbox: str, result: dict[str, Any]) -> str:
    signed_result = {key: value for key, value in result.items() if key != "integrity_token"}
    canonical = json.dumps(
        signed_result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    message = f"{mailbox.strip().lower()}|{canonical}".encode("utf-8")
    return hmac.new(settings.JWT_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _collapse_adjacent_repeated_phrases(tokens: list[str]) -> list[str]:
    collapsed = list(tokens)
    # CRM duplication occurs in the locality at the end of an address. Limit
    # de-duplication to that position so legitimate unit/house pairs such as
    # "1 1 Main Street" are never collapsed.
    locality_end = len(collapsed) - (1 if collapsed and re.fullmatch(r"\d{4}", collapsed[-1]) else 0)
    for width in range(min(4, locality_end // 2), 0, -1):
        first_start = locality_end - (2 * width)
        second_start = locality_end - width
        if collapsed[first_start:second_start] != collapsed[second_start:locality_end]:
            continue
        del collapsed[second_start:locality_end]
        break
    return collapsed


def _canonical_geocode_address(address: str) -> str:
    tokens = re.findall(r"[A-Z0-9]+(?:[-/][A-Z0-9]+)*", _clean_text(address).upper())
    tokens = [token for token in tokens if token not in {"VIC", "VICTORIA", "AUSTRALIA"}]
    tokens = _collapse_adjacent_repeated_phrases(tokens)
    if not tokens:
        raise InspectionPlannerError("An address is required for geocoding.")
    return " ".join(tokens)


def _comparable_address_tokens(address: str) -> set[str]:
    aliases = {
        "AVE": "AVENUE",
        "BLVD": "BOULEVARD",
        "CRES": "CRESCENT",
        "CT": "COURT",
        "DR": "DRIVE",
        "HWY": "HIGHWAY",
        "PDE": "PARADE",
        "PL": "PLACE",
        "RD": "ROAD",
        "ST": "STREET",
        "TCE": "TERRACE",
    }
    return {aliases.get(token, token) for token in _canonical_geocode_address(address).split()}


def _leading_property_number(address: str) -> str | None:
    match = re.match(
        r"^\s*(?:(?:UNIT|APT|APARTMENT|SHOP|LOT)\s+)?(\d+[A-Z]?(?:[-/]\d+[A-Z]?)?)\b",
        _clean_text(address).upper(),
    )
    return match.group(1) if match else None


def _address_postcode(address: str) -> str | None:
    matches = re.findall(r"\b\d{4}\b", address)
    return matches[-1] if matches else None


def _candidate_is_safe(query_address: str, candidate_address: str, score: float) -> bool:
    if score < VICMAP_MINIMUM_SCORE:
        return False
    query_number = _leading_property_number(query_address)
    candidate_number = _leading_property_number(candidate_address)
    if query_number and query_number != candidate_number:
        return False
    query_postcode = _address_postcode(query_address)
    candidate_postcode = _address_postcode(candidate_address)
    if query_postcode and query_postcode != candidate_postcode:
        return False
    query_tokens = _comparable_address_tokens(query_address)
    candidate_tokens = _comparable_address_tokens(candidate_address)
    overlap = len(query_tokens & candidate_tokens) / max(len(query_tokens | candidate_tokens), 1)
    return overlap >= 0.7


def _point_from_locator_payload(
    payload: dict[str, Any],
    *,
    query_address: str,
) -> tuple[GeocodePoint | None, dict[str, Any] | None]:
    ranked: list[tuple[float, float, GeocodePoint, dict[str, Any]]] = []
    query_tokens = _comparable_address_tokens(query_address)
    for candidate in payload.get("candidates") or []:
        candidate_address = _clean_text(
            candidate.get("address") or (candidate.get("attributes") or {}).get("Match_addr")
        )
        try:
            score = float(candidate.get("score", (candidate.get("attributes") or {}).get("Score", 0)))
            longitude = float((candidate.get("location") or {})["x"])
            latitude = float((candidate.get("location") or {})["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if not candidate_address or not _candidate_is_safe(query_address, candidate_address, score):
            continue
        candidate_tokens = _comparable_address_tokens(candidate_address)
        overlap = len(query_tokens & candidate_tokens) / max(len(query_tokens | candidate_tokens), 1)
        ranked.append(
            (
                score,
                overlap,
                GeocodePoint(latitude, longitude, candidate_address, provider="vicmap-locator"),
                candidate,
            )
        )
    if not ranked:
        return None, None
    _, _, point, candidate = max(ranked, key=lambda item: (item[0], item[1]))
    return point, candidate


def _point_from_wfs_payload(
    payload: dict[str, Any],
    *,
    query_address: str,
) -> tuple[GeocodePoint | None, dict[str, Any] | None]:
    for feature in payload.get("features") or []:
        properties = feature.get("properties") or {}
        candidate_address = _clean_text(properties.get("ezi_address"))
        coordinates = (feature.get("geometry") or {}).get("coordinates") or []
        try:
            longitude, latitude = float(coordinates[0]), float(coordinates[1])
        except (IndexError, TypeError, ValueError):
            continue
        if _canonical_geocode_address(candidate_address) != query_address:
            continue
        return GeocodePoint(latitude, longitude, candidate_address, provider="vicmap-wfs"), properties
    return None, None


def _provider_error(payload: dict[str, Any]) -> None:
    error = payload.get("error")
    if error:
        if isinstance(error, dict):
            message = _clean_text(error.get("message")) or "Vicmap returned an error."
        else:
            message = _clean_text(error) or "Vicmap returned an error."
        raise ValueError(message)


def geocode_address(
    db: Session,
    *,
    mailbox: str,
    address: str,
    allow_network: bool = True,
    provider_state: GeocodeProviderState | None = None,
) -> tuple[GeocodePoint | None, str | None]:
    query_address = _clean_text(address)
    if not query_address:
        return None, "Address is empty."

    canonical_address = _canonical_geocode_address(query_address)
    cache_key = _address_cache_key(mailbox, canonical_address)
    legacy_cache_key = _address_cache_key(mailbox, query_address)
    cache_keys = list(dict.fromkeys([cache_key, legacy_cache_key]))
    cached = (
        db.query(InspectionGeocodeCache)
        .filter(InspectionGeocodeCache.cache_key.in_(cache_keys))
        .order_by(InspectionGeocodeCache.id.desc())
        .first()
    )
    if cached:
        return (
            GeocodePoint(
                latitude=float(cached.latitude),
                longitude=float(cached.longitude),
                formatted_address=cached.formatted_address or cached.query_address,
                provider=cached.provider or "vicmap-cache",
            ),
            None,
        )

    if not allow_network:
        return None, "The route-provider time budget was reached before this address could be geocoded."

    state = provider_state or GeocodeProviderState()
    point: GeocodePoint | None = None
    provider_payload: dict[str, Any] | None = None
    provider_errors: list[str] = []

    if "locator" not in state.unavailable:
        params = {
            "SingleLine": canonical_address,
            "outFields": "Score,Match_addr,Ref_ID",
            "outSR": "4326",
            "maxLocations": "5",
            "f": "json",
        }
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
                response = client.get(VICMAP_GEOCODER_URL, params=params)
                response.raise_for_status()
                payload = response.json()
            _provider_error(payload)
            point, provider_payload = _point_from_locator_payload(payload, query_address=canonical_address)
        except Exception as exc:
            reason = exc.__class__.__name__
            state.unavailable["locator"] = reason
            provider_errors.append(reason)
    else:
        provider_errors.append(state.unavailable["locator"])

    if point is None and "wfs" not in state.unavailable:
        escaped_address = canonical_address.replace("'", "''")
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": "open-data-platform:address",
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "count": "5",
            "CQL_FILTER": f"ezi_address='{escaped_address}'",
        }
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
                response = client.get(VICMAP_WFS_URL, params=params)
                response.raise_for_status()
                payload = response.json()
            _provider_error(payload)
            point, provider_payload = _point_from_wfs_payload(payload, query_address=canonical_address)
        except Exception as exc:
            reason = exc.__class__.__name__
            state.unavailable["wfs"] = reason
            provider_errors.append(reason)
    elif point is None:
        provider_errors.append(state.unavailable["wfs"])

    if point is None:
        if provider_errors:
            reasons = ", ".join(dict.fromkeys(provider_errors))
            return None, f"Vicmap geocoding was unavailable ({reasons})."
        return None, f"Vicmap could not locate {query_address}."

    cache = InspectionGeocodeCache(
        mailbox=mailbox.strip().lower(),
        cache_key=cache_key,
        query_address=canonical_address,
        formatted_address=point.formatted_address,
        latitude=point.latitude,
        longitude=point.longitude,
        provider=point.provider,
        provider_payload_json=json.dumps(provider_payload or {}, ensure_ascii=False, default=str),
    )
    db.add(cache)
    db.flush()
    return (
        GeocodePoint(
            latitude=point.latitude,
            longitude=point.longitude,
            formatted_address=point.formatted_address,
            provider=point.provider,
        ),
        None,
    )


def _haversine_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    lat1, lon1 = left
    lat2, lon2 = right
    radius_km = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius_km * (2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a))))


def _haversine_matrices(coordinates: list[tuple[float, float]]) -> tuple[list[list[int]], list[list[float]]]:
    durations: list[list[int]] = []
    distances: list[list[float]] = []
    for origin in coordinates:
        duration_row: list[int] = []
        distance_row: list[float] = []
        for destination in coordinates:
            straight_km = _haversine_km(origin, destination)
            road_km = straight_km * 1.25
            drive_minutes = 0 if road_km < 0.01 else max(1, math.ceil((road_km / 35.0) * 60.0))
            duration_row.append(drive_minutes)
            distance_row.append(round(road_km, 3))
        durations.append(duration_row)
        distances.append(distance_row)
    return durations, distances


def road_matrix(
    coordinates: list[tuple[float, float]],
    *,
    allow_network: bool = True,
) -> tuple[list[list[int]], list[list[float]], str, list[str]]:
    fallback_durations, fallback_distances = _haversine_matrices(coordinates)
    if len(coordinates) <= 1:
        return fallback_durations, fallback_distances, "haversine", []
    if not allow_network:
        return (
            fallback_durations,
            fallback_distances,
            "haversine",
            ["The route-provider time budget was reached, so estimated road times were used."],
        )
    if len(coordinates) > 100:
        return (
            fallback_durations,
            fallback_distances,
            "haversine",
            ["More than 100 locations were supplied, so estimated road times were used."],
        )

    coordinate_text = ";".join(f"{longitude:.7f},{latitude:.7f}" for latitude, longitude in coordinates)
    url = f"{OSRM_BASE_URL}/table/v1/driving/{coordinate_text}"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = client.get(url, params={"annotations": "duration,distance"})
            response.raise_for_status()
            payload = response.json()
        raw_durations = payload.get("durations")
        raw_distances = payload.get("distances")
        count = len(coordinates)
        if payload.get("code") != "Ok" or len(raw_durations or []) != count or len(raw_distances or []) != count:
            raise ValueError("OSRM returned an incomplete matrix")
        if any(value is None for row in raw_durations for value in row):
            raise ValueError("OSRM could not connect all inspection locations")
        if any(value is None for row in raw_distances for value in row):
            raise ValueError("OSRM did not return road distances")
        durations = [[int(math.ceil(float(seconds) / 60.0)) for seconds in row] for row in raw_durations]
        distances = [[round(float(metres) / 1000.0, 3) for metres in row] for row in raw_distances]
        return durations, distances, "osrm", []
    except Exception as exc:
        return (
            fallback_durations,
            fallback_distances,
            "haversine",
            [f"Live road times were unavailable; Haversine estimates were used ({exc.__class__.__name__})."],
        )


def route_geometry(
    coordinates: list[tuple[float, float]],
    *,
    allow_network: bool = True,
) -> tuple[list[list[float]], str, str | None]:
    straight_line = [[round(latitude, 7), round(longitude, 7)] for latitude, longitude in coordinates]
    if len(coordinates) <= 1:
        return straight_line, "haversine", None
    if not allow_network:
        return straight_line, "haversine", "The provider time budget was reached; map route geometry is an estimate."
    coordinate_text = ";".join(f"{longitude:.7f},{latitude:.7f}" for latitude, longitude in coordinates)
    url = f"{OSRM_BASE_URL}/route/v1/driving/{coordinate_text}"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = client.get(
                url,
                params={"overview": "full", "geometries": "geojson", "steps": "false"},
            )
            response.raise_for_status()
            payload = response.json()
        route = (payload.get("routes") or [])[0]
        raw_coordinates = (route.get("geometry") or {}).get("coordinates") or []
        geometry = [[round(float(lat), 7), round(float(lon), 7)] for lon, lat in raw_coordinates]
        if len(geometry) < 2:
            raise ValueError("OSRM returned no route geometry")
        return geometry, "osrm", None
    except Exception as exc:
        return straight_line, "haversine", f"Map route geometry is an estimate ({exc.__class__.__name__})."


def load_existing_windows(
    db: Session,
    *,
    mailbox: str,
    plan_date: date,
    exclude_plan_id: int | None = None,
) -> dict[int, list[ExistingInspectionWindow]]:
    query = (
        db.query(InspectionVisit, InspectionPlan)
        .join(InspectionPlan, InspectionPlan.id == InspectionVisit.plan_id)
        .filter(InspectionVisit.mailbox == mailbox)
        .filter(InspectionPlan.mailbox == mailbox)
        .filter(InspectionPlan.plan_date == plan_date)
        .filter(
            InspectionPlan.status.in_(
                [
                    InspectionPlanStatus.PLANNED,
                    InspectionPlanStatus.CONFIRMED,
                    InspectionPlanStatus.IN_PROGRESS,
                ]
            )
        )
    )
    if exclude_plan_id is not None:
        query = query.filter(InspectionPlan.id != exclude_plan_id)

    by_agent: dict[int, list[ExistingInspectionWindow]] = {}
    for visit, plan in query.all():
        for raw_agent_id in _json_list(visit.agent_ids_json):
            try:
                agent_id = int(raw_agent_id)
            except (TypeError, ValueError):
                continue
            by_agent.setdefault(agent_id, []).append(
                ExistingInspectionWindow(
                    agent_id=agent_id,
                    scheduled_start=visit.scheduled_start,
                    scheduled_end=visit.scheduled_end,
                    blocked_until=visit.scheduled_end + timedelta(minutes=max(0, int(visit.buffer_minutes or 0))),
                    plan_id=plan.id,
                    plan_name=plan.name,
                    property_id=visit.property_id,
                    property_address=visit.property_address,
                    latitude=float(visit.latitude),
                    longitude=float(visit.longitude),
                )
            )
    for windows in by_agent.values():
        windows.sort(key=lambda row: (row.scheduled_start, row.scheduled_end, row.plan_id))
    return by_agent


def _window_conflict_dict(window: ExistingInspectionWindow) -> dict[str, Any]:
    return {
        "type": "agent_overlap",
        "agent_id": window.agent_id,
        "plan_id": window.plan_id,
        "plan_name": window.plan_name,
        "property_id": window.property_id,
        "property_address": window.property_address,
        "scheduled_start": window.scheduled_start.isoformat(),
        "scheduled_end": window.scheduled_end.isoformat(),
        "blocked_until": window.blocked_until.isoformat(),
    }


def _overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    return start < other_end and end > other_start


def _anchor_travel_conflicts(
    *,
    visit: dict[str, Any],
    agent_ids: tuple[int, ...],
    agent_states: dict[int, AgentRouteState],
    scheduled_start: datetime,
    blocked_until: datetime,
    durations: list[list[int]],
    existing_windows: dict[int, list[ExistingInspectionWindow]],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for agent_id in agent_ids:
        state = agent_states[agent_id]
        windows = existing_windows.get(agent_id, [])
        previous = [
            window
            for window in windows
            if window.coordinate_index is not None
            and state.current_time <= window.blocked_until <= scheduled_start
        ]
        if previous:
            window = max(previous, key=lambda item: item.blocked_until)
            travel_minutes = durations[window.coordinate_index][visit["coordinate_index"]]
            available_minutes = max(0, int((scheduled_start - window.blocked_until).total_seconds() // 60))
            if travel_minutes > available_minutes:
                conflicts.append(
                    {
                        **_window_conflict_dict(window),
                        "type": "agent_travel_conflict",
                        "direction": "from_saved_inspection",
                        "required_travel_minutes": travel_minutes,
                        "available_travel_minutes": available_minutes,
                    }
                )

        following = [
            window
            for window in windows
            if window.coordinate_index is not None and window.scheduled_start >= blocked_until
        ]
        if following:
            window = min(following, key=lambda item: item.scheduled_start)
            travel_minutes = durations[visit["coordinate_index"]][window.coordinate_index]
            available_minutes = max(0, int((window.scheduled_start - blocked_until).total_seconds() // 60))
            if travel_minutes > available_minutes:
                conflicts.append(
                    {
                        **_window_conflict_dict(window),
                        "type": "agent_travel_conflict",
                        "direction": "to_saved_inspection",
                        "required_travel_minutes": travel_minutes,
                        "available_travel_minutes": available_minutes,
                    }
                )
    return conflicts


def _earliest_anchor_feasible_start(
    *,
    visit: dict[str, Any],
    state: AgentRouteState,
    not_before: datetime,
    durations: list[list[int]],
    distances: list[list[float]],
    windows: list[ExistingInspectionWindow],
    day_end_at: datetime,
) -> tuple[datetime, dict[str, Any]] | None:
    """Find the first reachable gap between an agent's fixed saved visits."""

    duration = timedelta(minutes=visit["duration_minutes"])
    buffer = timedelta(minutes=visit["buffer_minutes"])
    predecessor_time = state.current_time
    predecessor_coordinate_index = state.current_coordinate_index

    def incoming_leg() -> dict[str, Any]:
        if predecessor_coordinate_index is None:
            return {"travel_minutes": 0, "distance_km": 0.0}
        return {
            "travel_minutes": durations[predecessor_coordinate_index][visit["coordinate_index"]],
            "distance_km": distances[predecessor_coordinate_index][visit["coordinate_index"]],
        }

    for window in windows:
        anchor_coordinate_index = window.coordinate_index
        if anchor_coordinate_index is None:
            continue

        # A fixed visit ending exactly at the current cursor determines the
        # agent's location even though it no longer blocks any time.
        if window.blocked_until == predecessor_time:
            predecessor_coordinate_index = anchor_coordinate_index
            continue
        if window.blocked_until < predecessor_time:
            continue

        if window.scheduled_start >= predecessor_time:
            leg = incoming_leg()
            scheduled_start = max(
                not_before,
                visit["earliest_at"],
                predecessor_time + timedelta(minutes=leg["travel_minutes"]),
            )
            blocked_until = scheduled_start + duration + buffer
            outbound_minutes = durations[visit["coordinate_index"]][anchor_coordinate_index]
            if (
                scheduled_start <= visit["latest_at"]
                and blocked_until <= day_end_at
                and blocked_until + timedelta(minutes=outbound_minutes) <= window.scheduled_start
            ):
                return scheduled_start, leg

        # The candidate does not fit before this fixed visit. Resume from the
        # saved property's location after the inspection and its custom buffer.
        if window.blocked_until >= predecessor_time:
            predecessor_time = window.blocked_until
            predecessor_coordinate_index = anchor_coordinate_index

    leg = incoming_leg()
    scheduled_start = max(
        not_before,
        visit["earliest_at"],
        predecessor_time + timedelta(minutes=leg["travel_minutes"]),
    )
    blocked_until = scheduled_start + duration + buffer
    if scheduled_start > visit["latest_at"] or blocked_until > day_end_at:
        return None
    return scheduled_start, leg


def _candidate_for_agents(
    *,
    visit: dict[str, Any],
    agent_ids: tuple[int, ...],
    agent_states: dict[int, AgentRouteState],
    durations: list[list[int]],
    distances: list[list[float]],
    existing_windows: dict[int, list[ExistingInspectionWindow]],
    allow_agent_overlap: bool,
    day_end_at: datetime,
    allow_current_plan_overlap: bool = False,
) -> dict[str, Any] | None:
    legs: dict[int, dict[str, Any]] = {}
    ready_by_agent: dict[int, datetime] = {}
    for agent_id in agent_ids:
        state = agent_states[agent_id]
        if state.current_coordinate_index is None:
            drive_minutes = 0
            distance_km = 0.0
        else:
            drive_minutes = durations[state.current_coordinate_index][visit["coordinate_index"]]
            distance_km = distances[state.current_coordinate_index][visit["coordinate_index"]]
        # current_time already includes the previous visit's post-inspection buffer.
        ready_at = state.current_time + timedelta(minutes=drive_minutes)
        ready_by_agent[agent_id] = ready_at
        legs[agent_id] = {"travel_minutes": drive_minutes, "distance_km": distance_km}

    duration = timedelta(minutes=visit["duration_minutes"])
    buffer = timedelta(minutes=visit["buffer_minutes"])
    conflicts: list[dict[str, Any]] = []
    uses_current_plan_overlap = False

    if allow_current_plan_overlap:
        if not allow_agent_overlap:
            return None
        possible_starts = {visit["earliest_at"]}
        for agent_id in agent_ids:
            for stop in agent_states[agent_id].stops:
                stop_start = datetime.fromisoformat(stop["scheduled_start"])
                if visit["earliest_at"] <= stop_start <= visit["latest_at"]:
                    possible_starts.add(stop_start)

        scheduled_start: datetime | None = None
        for possible_start in sorted(possible_starts):
            possible_end = possible_start + duration
            possible_blocked_until = possible_end + buffer
            if possible_start > visit["latest_at"] or possible_blocked_until > day_end_at:
                continue

            proposed_conflicts: list[dict[str, Any]] = []
            infeasible_agent = False
            for agent_id in agent_ids:
                if ready_by_agent[agent_id] <= possible_start:
                    continue
                agent_conflicts: list[dict[str, Any]] = []
                for stop in agent_states[agent_id].stops:
                    stop_start = datetime.fromisoformat(stop["scheduled_start"])
                    stop_end = datetime.fromisoformat(stop["scheduled_end"])
                    stop_blocked_until = stop_end + timedelta(minutes=max(0, int(stop.get("buffer_minutes") or 0)))
                    if not _overlaps(possible_start, possible_blocked_until, stop_start, stop_blocked_until):
                        continue
                    agent_conflicts.append(
                        {
                            "type": "agent_overlap",
                            "source": "current_plan",
                            "agent_id": agent_id,
                            "client_id": stop["client_id"],
                            "property_id": stop["property_id"],
                            "property_address": stop["property_address"],
                            "scheduled_start": stop["scheduled_start"],
                            "scheduled_end": stop["scheduled_end"],
                            "blocked_until": stop_blocked_until.isoformat(),
                        }
                    )
                if not agent_conflicts:
                    infeasible_agent = True
                    break
                proposed_conflicts.extend(agent_conflicts)

            if not infeasible_agent and proposed_conflicts:
                scheduled_start = possible_start
                conflicts.extend(proposed_conflicts)
                uses_current_plan_overlap = True
                break

        if scheduled_start is None:
            return None
    else:
        scheduled_start = max([visit["earliest_at"], *ready_by_agent.values()])

    if allow_agent_overlap:
        scheduled_end = scheduled_start + duration
        blocked_until = scheduled_end + buffer
        for agent_id in agent_ids:
            for window in existing_windows.get(agent_id, []):
                if _overlaps(scheduled_start, blocked_until, window.scheduled_start, window.blocked_until):
                    conflicts.append(_window_conflict_dict(window))
        conflicts.extend(
            _anchor_travel_conflicts(
                visit=visit,
                agent_ids=agent_ids,
                agent_states=agent_states,
                scheduled_start=scheduled_start,
                blocked_until=blocked_until,
                durations=durations,
                existing_windows=existing_windows,
            )
        )
    else:
        # Synchronize the earliest fixed-anchor-safe start across every agent
        # assigned to a joint inspection. The proposal only moves forward and
        # each move crosses at least one saved anchor, so this converges after a
        # bounded number of gaps.
        scheduled_start = visit["earliest_at"]
        max_attempts = 2 + sum(len(existing_windows.get(agent_id, [])) for agent_id in agent_ids)
        for _attempt in range(max_attempts):
            slots: dict[int, tuple[datetime, dict[str, Any]]] = {}
            for agent_id in agent_ids:
                slot = _earliest_anchor_feasible_start(
                    visit=visit,
                    state=agent_states[agent_id],
                    not_before=scheduled_start,
                    durations=durations,
                    distances=distances,
                    windows=existing_windows.get(agent_id, []),
                    day_end_at=day_end_at,
                )
                if slot is None:
                    return None
                slots[agent_id] = slot

            synchronized_start = max(slot[0] for slot in slots.values())
            if synchronized_start == scheduled_start:
                legs = {agent_id: slot[1] for agent_id, slot in slots.items()}
                break
            scheduled_start = synchronized_start
        else:
            return None

    scheduled_end = scheduled_start + duration
    blocked_until = scheduled_end + buffer
    if scheduled_start > visit["latest_at"] or blocked_until > day_end_at:
        return None
    return {
        "agent_ids": agent_ids,
        "scheduled_start": scheduled_start,
        "scheduled_end": scheduled_end,
        "legs": legs,
        "conflicts": conflicts,
        "uses_current_plan_overlap": uses_current_plan_overlap,
        "incremental_drive": sum(leg["travel_minutes"] for leg in legs.values()),
        "incremental_distance": sum(leg["distance_km"] for leg in legs.values()),
    }


def optimize_inspections(
    db: Session,
    *,
    mailbox: str,
    plan_name: str,
    plan_date: date,
    day_start: str,
    day_end: str,
    start_address: str | None,
    available_agent_ids: list[int],
    allow_agent_overlap: bool,
    visits: list[dict[str, Any]],
) -> dict[str, Any]:
    del plan_name  # Reserved for future AI summaries; scheduling remains deterministic.
    provider_deadline = time.monotonic() + PROVIDER_BUDGET_SECONDS
    day_start_at = clock_datetime(plan_date, day_start, field_name="day_start")
    day_end_at = clock_datetime(plan_date, day_end, field_name="day_end")
    if day_end_at <= day_start_at:
        raise InspectionPlannerError("day_end must be later than day_start.")

    normalized_agent_ids = sorted({int(agent_id) for agent_id in available_agent_ids})
    if not normalized_agent_ids:
        raise InspectionPlannerError("Select at least one available agent.")
    users = {
        user.id: user
        for user in db.query(User)
        .filter(User.id.in_(normalized_agent_ids), User.is_active == True)
        .all()
    }
    if set(users) != set(normalized_agent_ids):
        raise InspectionPlannerError("One or more selected agents are unavailable.")

    warnings: list[str] = []
    unscheduled: list[dict[str, Any]] = []
    geocode_provider_state = GeocodeProviderState()
    cleaned_start_address = _clean_text(start_address)
    start_point: GeocodePoint | None = None
    if cleaned_start_address:
        start_point, geocode_warning = geocode_address(
            db,
            mailbox=mailbox,
            address=cleaned_start_address,
            allow_network=time.monotonic() < provider_deadline,
            provider_state=geocode_provider_state,
        )
        if geocode_warning:
            warnings.append(geocode_warning)
    else:
        warnings.append("No departure address was supplied, so travel to each agent's first inspection is excluded.")

    property_ids = sorted(
        {
            int(visit.get("property_id"))
            for visit in visits
            if visit.get("property_id") is not None and str(visit.get("property_id")).isdigit()
        }
    )
    properties = {
        row.id: row
        for row in db.query(ManagedProperty)
        .filter(ManagedProperty.mailbox == mailbox, ManagedProperty.is_active == True)
        .filter(ManagedProperty.id.in_(property_ids or [-1]))
        .all()
    }

    prepared: list[dict[str, Any]] = []
    points_by_address: dict[str, GeocodePoint] = {}
    for position, raw_visit in enumerate(visits):
        client_id = _clean_text(raw_visit.get("client_id")) or f"visit-{position + 1}"
        try:
            property_id = int(raw_visit.get("property_id"))
        except (TypeError, ValueError):
            unscheduled.append({"client_id": client_id, "property_id": None, "reason": "Select a property."})
            continue
        prop = properties.get(property_id)
        if not prop:
            unscheduled.append(
                {
                    "client_id": client_id,
                    "property_id": property_id,
                    "reason": "The property is not active in this mailbox.",
                }
            )
            continue

        try:
            duration_minutes = int(raw_visit.get("duration_minutes") or 0)
            buffer_minutes = int(raw_visit.get("buffer_minutes") or 0)
        except (TypeError, ValueError):
            unscheduled.append(
                {
                    "client_id": client_id,
                    "property_id": property_id,
                    "property_address": full_property_address(prop),
                    "reason": "Duration and custom buffer must be whole minutes.",
                }
            )
            continue
        if not 5 <= duration_minutes <= 480 or not 0 <= buffer_minutes <= 240:
            unscheduled.append(
                {
                    "client_id": client_id,
                    "property_id": property_id,
                    "property_address": full_property_address(prop),
                    "reason": "Duration or custom buffer is outside the supported range.",
                }
            )
            continue

        requested_agent_ids = sorted({int(agent_id) for agent_id in (raw_visit.get("agent_ids") or [])})
        if any(agent_id not in users for agent_id in requested_agent_ids):
            unscheduled.append(
                {
                    "client_id": client_id,
                    "property_id": property_id,
                    "property_address": full_property_address(prop),
                    "reason": "A requested agent is not available for this plan.",
                }
            )
            continue

        earliest_value = _clean_text(raw_visit.get("earliest_time")) or day_start
        latest_value = _clean_text(raw_visit.get("latest_time")) or day_end
        try:
            earliest_at = clock_datetime(plan_date, earliest_value, field_name="earliest_time")
            latest_at = clock_datetime(plan_date, latest_value, field_name="latest_time")
        except InspectionPlannerError as exc:
            unscheduled.append(
                {
                    "client_id": client_id,
                    "property_id": property_id,
                    "property_address": full_property_address(prop),
                    "reason": str(exc.detail),
                }
            )
            continue
        earliest_at = max(earliest_at, day_start_at)
        latest_at = min(latest_at, day_end_at)
        if latest_at < earliest_at:
            unscheduled.append(
                {
                    "client_id": client_id,
                    "property_id": property_id,
                    "property_address": full_property_address(prop),
                    "reason": "The requested inspection window is outside the working day.",
                }
            )
            continue

        property_address = full_property_address(prop)
        point = points_by_address.get(property_address.casefold())
        if not point:
            point, warning = geocode_address(
                db,
                mailbox=mailbox,
                address=property_address,
                allow_network=time.monotonic() < provider_deadline,
                provider_state=geocode_provider_state,
            )
            if warning:
                warnings.append(warning)
            if point:
                points_by_address[property_address.casefold()] = point
        if not point:
            unscheduled.append(
                {
                    "client_id": client_id,
                    "property_id": property_id,
                    "property_address": property_address,
                    "reason": "The property address could not be geocoded.",
                }
            )
            continue

        prepared.append(
            {
                "input_position": position,
                "client_id": client_id,
                "property_id": property_id,
                "property_address": property_address,
                "latitude": point.latitude,
                "longitude": point.longitude,
                "requested_agent_ids": tuple(requested_agent_ids),
                "duration_minutes": duration_minutes,
                "buffer_minutes": buffer_minutes,
                "earliest_at": earliest_at,
                "latest_at": latest_at,
                "notes": _clean_text(raw_visit.get("notes")) or None,
            }
        )

    if cleaned_start_address and start_point is None:
        warnings.append("The departure address could not be located, so first-leg travel is excluded.")

    existing_windows = load_existing_windows(db, mailbox=mailbox, plan_date=plan_date)
    coordinates: list[tuple[float, float]] = []
    start_coordinate_index: int | None = None
    if start_point is not None:
        coordinates.append((start_point.latitude, start_point.longitude))
        start_coordinate_index = 0
    for visit in sorted(prepared, key=lambda row: (row["property_id"], row["input_position"])):
        coordinate = (visit["latitude"], visit["longitude"])
        try:
            coordinate_index = coordinates.index(coordinate)
        except ValueError:
            coordinates.append(coordinate)
            coordinate_index = len(coordinates) - 1
        visit["coordinate_index"] = coordinate_index

    for agent_id in normalized_agent_ids:
        for window in existing_windows.get(agent_id, []):
            coordinate = (window.latitude, window.longitude)
            try:
                coordinate_index = coordinates.index(coordinate)
            except ValueError:
                coordinates.append(coordinate)
                coordinate_index = len(coordinates) - 1
            window.coordinate_index = coordinate_index

    durations, distances, matrix_provider, matrix_warnings = road_matrix(
        coordinates,
        allow_network=time.monotonic() < provider_deadline,
    )
    warnings.extend(matrix_warnings)
    agent_states = {
        agent_id: AgentRouteState(
            user=users[agent_id],
            current_time=day_start_at,
            current_coordinate_index=start_coordinate_index,
        )
        for agent_id in normalized_agent_ids
    }

    remaining = list(prepared)
    scheduled: list[dict[str, Any]] = []
    while remaining:
        choices: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, Any]]] = []
        for visit in remaining:
            candidate_agent_sets = (
                [visit["requested_agent_ids"]]
                if visit["requested_agent_ids"]
                else [(agent_id,) for agent_id in normalized_agent_ids]
            )
            for agent_ids in candidate_agent_sets:
                candidate = _candidate_for_agents(
                    visit=visit,
                    agent_ids=agent_ids,
                    agent_states=agent_states,
                    durations=durations,
                    distances=distances,
                    existing_windows=existing_windows,
                    allow_agent_overlap=False,
                    day_end_at=day_end_at,
                )
                if not candidate and allow_agent_overlap:
                    candidate = _candidate_for_agents(
                        visit=visit,
                        agent_ids=agent_ids,
                        agent_states=agent_states,
                        durations=durations,
                        distances=distances,
                        existing_windows=existing_windows,
                        allow_agent_overlap=True,
                        day_end_at=day_end_at,
                    )
                if not candidate and allow_agent_overlap:
                    candidate = _candidate_for_agents(
                        visit=visit,
                        agent_ids=agent_ids,
                        agent_states=agent_states,
                        durations=durations,
                        distances=distances,
                        existing_windows=existing_windows,
                        allow_agent_overlap=True,
                        day_end_at=day_end_at,
                        allow_current_plan_overlap=True,
                    )
                if not candidate:
                    continue
                score = (
                    visit["latest_at"],
                    bool(candidate["uses_current_plan_overlap"]),
                    candidate["scheduled_start"],
                    candidate["incremental_drive"],
                    candidate["incremental_distance"],
                    visit["input_position"],
                    agent_ids,
                )
                choices.append((score, visit, candidate))

        if not choices:
            for visit in remaining:
                unscheduled.append(
                    {
                        "client_id": visit["client_id"],
                        "property_id": visit["property_id"],
                        "property_address": visit["property_address"],
                        "reason": "No selected agent can reach this inspection within its time window and the working day.",
                    }
                )
            break

        _, visit, candidate = min(choices, key=lambda item: item[0])
        agent_ids = candidate["agent_ids"]
        agent_names = [users[agent_id].name for agent_id in agent_ids]
        sequences: dict[int, int] = {}
        for agent_id in agent_ids:
            state = agent_states[agent_id]
            leg = candidate["legs"][agent_id]
            sequence = len(state.stops) + 1
            sequences[agent_id] = sequence
            stop = {
                "client_id": visit["client_id"],
                "property_id": visit["property_id"],
                "property_address": visit["property_address"],
                "latitude": visit["latitude"],
                "longitude": visit["longitude"],
                "scheduled_start": candidate["scheduled_start"].isoformat(),
                "scheduled_end": candidate["scheduled_end"].isoformat(),
                "buffer_minutes": visit["buffer_minutes"],
                "sequence": sequence,
                "travel_minutes": int(leg["travel_minutes"]),
                "distance_km": round(float(leg["distance_km"]), 2),
            }
            state.stops.append(stop)
            # Custom time is a post-inspection allowance (parking, keys, notes,
            # hand-off, etc.) and therefore delays the agent's next departure.
            next_available_at = candidate["scheduled_end"] + timedelta(minutes=visit["buffer_minutes"])
            if next_available_at >= state.current_time:
                state.current_time = next_available_at
                state.current_coordinate_index = visit["coordinate_index"]
            state.total_drive_minutes += int(leg["travel_minutes"])
            state.total_distance_km += float(leg["distance_km"])

        scheduled.append(
            {
                "client_id": visit["client_id"],
                "property_id": visit["property_id"],
                "property_address": visit["property_address"],
                "latitude": round(float(visit["latitude"]), 7),
                "longitude": round(float(visit["longitude"]), 7),
                "agent_ids": list(agent_ids),
                "agent_names": agent_names,
                "duration_minutes": visit["duration_minutes"],
                "buffer_minutes": visit["buffer_minutes"],
                "earliest_time": visit["earliest_at"].strftime("%H:%M"),
                "latest_time": visit["latest_at"].strftime("%H:%M"),
                "scheduled_start": candidate["scheduled_start"].isoformat(),
                "scheduled_end": candidate["scheduled_end"].isoformat(),
                "sequence": sequences[agent_ids[0]],
                "travel_minutes": max(int(leg["travel_minutes"]) for leg in candidate["legs"].values()),
                "distance_km": round(max(float(leg["distance_km"]) for leg in candidate["legs"].values()), 2),
                "conflicts": candidate["conflicts"],
                "notes": visit["notes"],
            }
        )
        remaining.remove(visit)

    routes: list[dict[str, Any]] = []
    for color_index, agent_id in enumerate(normalized_agent_ids):
        state = agent_states[agent_id]
        if not state.stops:
            continue
        route_coordinates = (
            ([(start_point.latitude, start_point.longitude)] if start_point is not None else [])
            + [(float(stop["latitude"]), float(stop["longitude"])) for stop in state.stops]
        )
        geometry, geometry_provider, geometry_warning = route_geometry(
            route_coordinates,
            allow_network=time.monotonic() < provider_deadline,
        )
        if geometry_warning:
            warnings.append(geometry_warning)
        routes.append(
            {
                "agent_id": agent_id,
                "agent_name": state.user.name,
                "color": ROUTE_COLORS[color_index % len(ROUTE_COLORS)],
                "stops": state.stops,
                "distance_km": round(state.total_distance_km, 2),
                "drive_minutes": state.total_drive_minutes,
                "geometry": geometry,
                "geometry_provider": geometry_provider,
            }
        )

    scheduled.sort(key=lambda row: (row["scheduled_start"], row["sequence"], row["client_id"]))
    total_distance = round(sum(route["distance_km"] for route in routes), 2)
    total_drive = sum(int(route["drive_minutes"]) for route in routes)
    total_inspection = sum(int(visit["duration_minutes"]) for visit in scheduled)
    total_buffer = sum(int(visit["buffer_minutes"]) for visit in scheduled)
    conflict_count = sum(len(visit["conflicts"]) for visit in scheduled)
    if conflict_count:
        warnings.append(
            f"{conflict_count} inspection assignment conflict{'s were' if conflict_count != 1 else ' was'} retained because overlap is enabled."
        )
    if unscheduled:
        warnings.append(f"{len(unscheduled)} inspection{'s were' if len(unscheduled) != 1 else ' was'} left unscheduled.")

    insights = [
        f"Scheduled {len(scheduled)} inspection{'s' if len(scheduled) != 1 else ''} across {len(routes)} agent route{'s' if len(routes) != 1 else ''}.",
        f"Estimated driving is {total_drive} minutes over {total_distance:.1f} km.",
    ]
    if total_buffer:
        insights.append(f"The plan reserves {total_buffer} custom minutes for parking and other access delays.")
    if len(routes) > 1:
        insights.append("Flexible inspections were allocated to the earliest feasible agent route.")
    if start_point is None:
        insights.append("Departure travel is excluded; each route begins at its agent's first inspection.")

    result = {
        "ok": True,
        "provider": f"vicmap+{matrix_provider}",
        "plan_date": plan_date.isoformat(),
        "day_start": day_start,
        "day_end": day_end,
        "start_address": cleaned_start_address or None,
        "allow_agent_overlap": allow_agent_overlap,
        "available_agent_ids": normalized_agent_ids,
        "visits": scheduled,
        "routes": routes,
        "metrics": {
            "inspection_count": len(scheduled),
            "agent_count": len(routes),
            "total_distance_km": total_distance,
            "total_drive_minutes": total_drive,
            "total_inspection_minutes": total_inspection,
            "total_buffer_minutes": total_buffer,
            "day_start": day_start,
            "day_end": day_end,
            "score": round(total_drive + (total_distance * 2) + (len(unscheduled) * 1000), 2),
        },
        "warnings": list(dict.fromkeys(warnings)),
        "insights": insights,
        "unscheduled": unscheduled,
    }
    result["integrity_token"] = _optimization_integrity_token(mailbox, result)
    return result


def _parse_result_datetime(value: Any, *, field_name: str) -> datetime:
    text = _clean_text(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InspectionPlannerError(f"{field_name} is not a valid ISO date/time.") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def validate_optimization_result(
    db: Session,
    *,
    mailbox: str,
    plan_date: date,
    day_start: str,
    day_end: str,
    start_address: str | None,
    allow_agent_overlap: bool,
    optimization_result: dict[str, Any],
) -> list[dict[str, Any]]:
    supplied_token = _clean_text(optimization_result.get("integrity_token"))
    expected_token = _optimization_integrity_token(mailbox, optimization_result)
    if not supplied_token or not hmac.compare_digest(supplied_token, expected_token):
        raise InspectionPlannerError("The optimization result was changed or has expired. Recalculate the plan before saving.")
    expected_snapshot = {
        "plan_date": plan_date.isoformat(),
        "day_start": day_start,
        "day_end": day_end,
        "start_address": _clean_text(start_address) or None,
        "allow_agent_overlap": allow_agent_overlap,
    }
    actual_snapshot = {key: optimization_result.get(key) for key in expected_snapshot}
    if actual_snapshot != expected_snapshot:
        raise InspectionPlannerError("The plan settings changed after optimization. Recalculate the plan before saving.")

    result_visits = optimization_result.get("visits")
    if not isinstance(result_visits, list):
        raise InspectionPlannerError("optimization_result.visits must be a list.")
    if len(result_visits) > 100:
        raise InspectionPlannerError("An inspection plan can contain at most 100 visits.")
    day_start_at = clock_datetime(plan_date, day_start, field_name="day_start")
    day_end_at = clock_datetime(plan_date, day_end, field_name="day_end")
    if day_end_at <= day_start_at:
        raise InspectionPlannerError("day_end must be later than day_start.")

    property_ids: set[int] = set()
    agent_ids: set[int] = set()
    for raw in result_visits:
        if not isinstance(raw, dict) or not raw.get("scheduled_start") or not raw.get("scheduled_end"):
            continue
        try:
            property_ids.add(int(raw.get("property_id")))
            agent_ids.update(int(agent_id) for agent_id in (raw.get("agent_ids") or []))
        except (TypeError, ValueError) as exc:
            raise InspectionPlannerError("A scheduled visit contains an invalid property or agent id.") from exc

    properties = {
        row.id: row
        for row in db.query(ManagedProperty)
        .filter(ManagedProperty.mailbox == mailbox, ManagedProperty.is_active == True)
        .filter(ManagedProperty.id.in_(property_ids or [-1]))
        .all()
    }
    users = {
        row.id: row
        for row in db.query(User)
        .filter(User.is_active == True)
        .filter(User.id.in_(agent_ids or [-1]))
        .all()
    }
    if set(properties) != property_ids:
        raise InspectionPlannerError("A scheduled property is no longer active in this mailbox.")
    if set(users) != agent_ids:
        raise InspectionPlannerError("A scheduled agent is no longer active.")

    existing_windows = load_existing_windows(db, mailbox=mailbox, plan_date=plan_date)
    proposed_by_agent: dict[int, list[dict[str, Any]]] = {}
    cleaned: list[dict[str, Any]] = []
    seen_client_ids: set[str] = set()
    for position, raw in enumerate(result_visits):
        if not isinstance(raw, dict) or not raw.get("scheduled_start") or not raw.get("scheduled_end"):
            continue
        client_id = _clean_text(raw.get("client_id")) or f"visit-{position + 1}"
        if len(client_id) > 120:
            raise InspectionPlannerError("Scheduled visit client ids cannot exceed 120 characters.")
        if client_id in seen_client_ids:
            raise InspectionPlannerError("Scheduled visit client ids must be unique.")
        seen_client_ids.add(client_id)
        property_id = int(raw["property_id"])
        prop = properties[property_id]
        selected_agent_ids = sorted({int(agent_id) for agent_id in (raw.get("agent_ids") or [])})
        if not selected_agent_ids:
            raise InspectionPlannerError(f"{client_id} has no assigned agent.")
        start = _parse_result_datetime(raw.get("scheduled_start"), field_name="scheduled_start")
        end = _parse_result_datetime(raw.get("scheduled_end"), field_name="scheduled_end")
        if start >= end or start < day_start_at or end > day_end_at or start.date() != plan_date or end.date() != plan_date:
            raise InspectionPlannerError(f"{client_id} is outside the saved plan's working day.")
        try:
            duration_minutes = int(raw.get("duration_minutes") or round((end - start).total_seconds() / 60))
            buffer_minutes = int(raw.get("buffer_minutes") or 0)
            latitude = float(raw.get("latitude"))
            longitude = float(raw.get("longitude"))
            sequence = max(0, int(raw.get("sequence") or position + 1))
            travel_minutes = max(0, int(raw.get("travel_minutes") or 0))
            distance_km = max(0.0, float(raw.get("distance_km") or 0.0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise InspectionPlannerError(f"{client_id} contains invalid schedule or map values.") from exc
        if not 5 <= duration_minutes <= 480 or not 0 <= buffer_minutes <= 240:
            raise InspectionPlannerError(f"{client_id} has an invalid duration or custom buffer.")
        actual_duration = int(round((end - start).total_seconds() / 60.0))
        if duration_minutes != actual_duration:
            raise InspectionPlannerError(f"{client_id}'s scheduled interval does not match its duration.")
        earliest_time = _clean_text(raw.get("earliest_time"))
        latest_time = _clean_text(raw.get("latest_time"))
        try:
            earliest_at = clock_datetime(plan_date, earliest_time, field_name="earliest_time")
            latest_at = clock_datetime(plan_date, latest_time, field_name="latest_time")
        except InspectionPlannerError as exc:
            raise InspectionPlannerError(f"{client_id} has an invalid inspection window.") from exc
        if latest_at < earliest_at or start < earliest_at or start > latest_at:
            raise InspectionPlannerError(f"{client_id} is outside its optimized inspection window.")
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise InspectionPlannerError(f"{client_id} contains invalid map coordinates.")
        blocked_until = end + timedelta(minutes=buffer_minutes)
        if blocked_until > day_end_at:
            raise InspectionPlannerError(f"{client_id}'s custom buffer extends beyond the working day.")

        # Rebuild conflicts from persisted and proposed schedules instead of
        # trusting stale or client-supplied conflict metadata.
        conflicts: list[dict[str, Any]] = []
        for agent_id in selected_agent_ids:
            for window in existing_windows.get(agent_id, []):
                if _overlaps(start, blocked_until, window.scheduled_start, window.blocked_until):
                    conflict = _window_conflict_dict(window)
                    if not allow_agent_overlap:
                        raise InspectionPlannerError(
                            {
                                "message": "The plan now conflicts with a saved inspection. Recalculate before saving.",
                                "conflicts": [conflict],
                            },
                            status_code=409,
                        )
                    conflicts.append(conflict)
            for other in proposed_by_agent.get(agent_id, []):
                if _overlaps(start, blocked_until, other["scheduled_start"], other["blocked_until"]):
                    conflict = {
                        "type": "agent_overlap",
                        "agent_id": agent_id,
                        "client_id": other["client_id"],
                        "property_id": other["property_id"],
                        "property_address": other["property_address"],
                        "scheduled_start": other["scheduled_start"].isoformat(),
                        "scheduled_end": other["scheduled_end"].isoformat(),
                        "blocked_until": other["blocked_until"].isoformat(),
                    }
                    if not allow_agent_overlap:
                        raise InspectionPlannerError(
                            {
                                "message": "The plan assigns an agent to overlapping inspections.",
                                "conflicts": [conflict],
                            },
                            status_code=409,
                        )
                    conflicts.append(conflict)

        notes = _clean_text(raw.get("notes")) or None
        if notes and len(notes) > 3000:
            raise InspectionPlannerError(f"{client_id} has inspection notes longer than 3000 characters.")

        cleaned_visit = {
            "client_id": client_id,
            "property_id": property_id,
            "property_address": full_property_address(prop),
            "latitude": latitude,
            "longitude": longitude,
            "agent_ids": selected_agent_ids,
            "agent_names": [users[agent_id].name for agent_id in selected_agent_ids],
            "duration_minutes": duration_minutes,
            "buffer_minutes": buffer_minutes,
            "earliest_time": earliest_time,
            "latest_time": latest_time,
            "scheduled_start": start,
            "scheduled_end": end,
            "blocked_until": blocked_until,
            "sequence": sequence,
            "travel_minutes": travel_minutes,
            "distance_km": distance_km,
            "conflicts": conflicts,
            "notes": notes,
        }
        cleaned.append(cleaned_visit)
        for agent_id in selected_agent_ids:
            proposed_by_agent.setdefault(agent_id, []).append(cleaned_visit)

    return cleaned
