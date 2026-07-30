from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from hashlib import blake2b
from threading import Lock
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session, selectinload

from app.authz import require_page_access
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import InspectionPlan, InspectionPlanStatus, InspectionVisit, User
from app.services.inspection_planner import (
    DEFAULT_DEPARTURE_ADDRESS,
    DEFAULT_TIMEZONE,
    InspectionPlannerError,
    load_existing_windows,
    optimize_inspections,
    validate_optimization_result,
)


router = APIRouter(prefix="/inspections", tags=["inspections"])


_ACTIVE_PLAN_STATUS_VALUES = frozenset(
    {
        InspectionPlanStatus.PLANNED.value,
        InspectionPlanStatus.CONFIRMED.value,
        InspectionPlanStatus.IN_PROGRESS.value,
    }
)
# A fixed stripe set avoids an unbounded lock registry while keeping unrelated
# mailbox/date writes concurrent. Production PostgreSQL writes also take a
# transaction-scoped advisory lock below, which covers separate app workers.
_INSPECTION_WRITE_LOCKS = tuple(Lock() for _ in range(64))


class InspectionOptimizeVisitIn(BaseModel):
    client_id: str = Field(min_length=1, max_length=120)
    property_id: int | None = Field(default=None, gt=0)
    property_address: str | None = Field(default=None, max_length=500)
    agent_ids: list[int] = Field(default_factory=list, max_length=20)
    duration_minutes: int = Field(default=45, ge=5, le=480)
    buffer_minutes: int = Field(default=0, ge=0, le=240)
    earliest_time: str | None = Field(default=None, max_length=5)
    latest_time: str | None = Field(default=None, max_length=5)
    notes: str | None = Field(default=None, max_length=3000)

    @model_validator(mode="after")
    def require_property_location(self) -> "InspectionOptimizeVisitIn":
        if self.property_id is None and not (self.property_address or "").strip():
            raise ValueError("Select a managed property or enter a full address.")
        return self


class InspectionOptimizeIn(BaseModel):
    plan_name: str = Field(min_length=1, max_length=200)
    plan_date: date
    day_start: str = Field(min_length=4, max_length=5)
    day_end: str = Field(min_length=4, max_length=5)
    start_address: str | None = Field(default=DEFAULT_DEPARTURE_ADDRESS, max_length=500)
    available_agent_ids: list[int] = Field(min_length=1, max_length=50)
    allow_agent_overlap: bool = False
    visits: list[InspectionOptimizeVisitIn] = Field(min_length=1, max_length=100)


class InspectionPlanCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    status: InspectionPlanStatus = InspectionPlanStatus.PLANNED
    plan_date: date
    day_start: str = Field(min_length=4, max_length=5)
    day_end: str = Field(min_length=4, max_length=5)
    start_address: str | None = Field(default=DEFAULT_DEPARTURE_ADDRESS, max_length=500)
    allow_agent_overlap: bool = False
    optimization_result: dict[str, Any]


class InspectionPlanStatusIn(BaseModel):
    status: InspectionPlanStatus


def _status_value(value: InspectionPlanStatus | str) -> str:
    return value.value if isinstance(value, InspectionPlanStatus) else str(value)


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _visit_dict(row: InspectionVisit) -> dict[str, Any]:
    return {
        "id": row.id,
        "client_id": row.client_id,
        "property_id": row.property_id,
        "property_address": row.property_address,
        "latitude": row.latitude,
        "longitude": row.longitude,
        "agent_ids": _loads(row.agent_ids_json, []),
        "agent_names": _loads(row.agent_names_json, []),
        "duration_minutes": row.duration_minutes,
        "buffer_minutes": row.buffer_minutes,
        "scheduled_start": row.scheduled_start.isoformat(),
        "scheduled_end": row.scheduled_end.isoformat(),
        "sequence": row.sequence,
        "travel_minutes": row.travel_minutes,
        "distance_km": row.distance_km,
        "conflicts": _loads(row.conflicts_json, []),
        "notes": row.notes,
    }


def _plan_dict(row: InspectionPlan, *, include_result: bool) -> dict[str, Any]:
    result = _loads(row.optimization_result_json, {})
    value = {
        "id": row.id,
        "name": row.name,
        "status": _status_value(row.status),
        "plan_date": row.plan_date.isoformat(),
        "day_start": row.day_start,
        "day_end": row.day_end,
        "timezone": row.timezone,
        "start_address": row.start_address,
        "allow_agent_overlap": row.allow_agent_overlap,
        "provider": row.provider,
        "visit_count": len(row.visits),
        "metrics": result.get("metrics") if isinstance(result, dict) else None,
        "warnings": result.get("warnings", []) if isinstance(result, dict) else [],
        "created_by_user_id": row.created_by_user_id,
        "created_by_name": row.created_by.name if row.created_by else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
    if include_result:
        value["visits"] = [_visit_dict(visit) for visit in sorted(row.visits, key=lambda item: (item.scheduled_start, item.sequence, item.id))]
        value["optimization_result"] = result
    return value


def _planner_http_error(exc: InspectionPlannerError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _inspection_write_lock(mailbox: str, plan_date: date) -> tuple[Lock, int]:
    scope = f"{mailbox.strip().casefold()}\0{plan_date.isoformat()}".encode("utf-8")
    digest = blake2b(scope, digest_size=8, person=b"inspection-plan").digest()
    stripe = int.from_bytes(digest, byteorder="big", signed=False) % len(_INSPECTION_WRITE_LOCKS)
    advisory_lock_id = int.from_bytes(digest, byteorder="big", signed=True)
    return _INSPECTION_WRITE_LOCKS[stripe], advisory_lock_id


def _acquire_database_write_lock(db: Session, advisory_lock_id: int) -> None:
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": advisory_lock_id},
        )


def _plan_activation_conflicts(db: Session, row: InspectionPlan) -> list[dict[str, Any]]:
    existing_by_agent = load_existing_windows(
        db,
        mailbox=row.mailbox,
        plan_date=row.plan_date,
        exclude_plan_id=row.id,
    )
    conflicts: list[dict[str, Any]] = []
    for visit in sorted(row.visits, key=lambda item: (item.scheduled_start, item.sequence, item.id)):
        blocked_until = visit.scheduled_end + timedelta(minutes=max(0, int(visit.buffer_minutes or 0)))
        raw_agent_ids = _loads(visit.agent_ids_json, [])
        raw_agent_names = _loads(visit.agent_names_json, [])
        agent_names: dict[int, str | None] = {}
        for index, raw_agent_id in enumerate(raw_agent_ids):
            try:
                agent_id = int(raw_agent_id)
            except (TypeError, ValueError):
                continue
            raw_name = raw_agent_names[index] if index < len(raw_agent_names) else None
            agent_names.setdefault(agent_id, str(raw_name).strip() if raw_name else None)

        for agent_id, agent_name in agent_names.items():
            for window in existing_by_agent.get(agent_id, []):
                if visit.scheduled_start >= window.blocked_until or blocked_until <= window.scheduled_start:
                    continue
                conflicts.append(
                    {
                        "type": "agent_overlap",
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "current_visit": {
                            "visit_id": visit.id,
                            "client_id": visit.client_id,
                            "property_id": visit.property_id,
                            "property_address": visit.property_address,
                            "scheduled_start": visit.scheduled_start.isoformat(),
                            "scheduled_end": visit.scheduled_end.isoformat(),
                            "blocked_until": blocked_until.isoformat(),
                        },
                        "conflicting_visit": {
                            "plan_id": window.plan_id,
                            "plan_name": window.plan_name,
                            "property_id": window.property_id,
                            "property_address": window.property_address,
                            "scheduled_start": window.scheduled_start.isoformat(),
                            "scheduled_end": window.scheduled_end.isoformat(),
                            "blocked_until": window.blocked_until.isoformat(),
                        },
                    }
                )
    return conflicts


@router.post("/optimize")
def optimize_inspection_plan(
    payload: InspectionOptimizeIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("inspections")),
):
    try:
        result = optimize_inspections(
            db,
            mailbox=mailbox,
            plan_name=payload.plan_name,
            plan_date=payload.plan_date,
            day_start=payload.day_start,
            day_end=payload.day_end,
            start_address=payload.start_address,
            available_agent_ids=payload.available_agent_ids,
            allow_agent_overlap=payload.allow_agent_overlap,
            visits=[visit.model_dump() for visit in payload.visits],
        )
        # The optimization is a dry run, but successful Vicmap results are cached.
        db.commit()
        return result
    except InspectionPlannerError as exc:
        db.rollback()
        raise _planner_http_error(exc) from exc
    except Exception:
        db.rollback()
        raise


@router.post("/plans", status_code=201)
def save_inspection_plan(
    payload: InspectionPlanCreateIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(require_page_access("inspections")),
):
    write_lock, advisory_lock_id = _inspection_write_lock(mailbox, payload.plan_date)
    write_lock.acquire()
    try:
        _acquire_database_write_lock(db, advisory_lock_id)
        cleaned_visits = validate_optimization_result(
            db,
            mailbox=mailbox,
            plan_date=payload.plan_date,
            day_start=payload.day_start,
            day_end=payload.day_end,
            start_address=payload.start_address,
            allow_agent_overlap=payload.allow_agent_overlap,
            optimization_result=payload.optimization_result,
        )
        if not cleaned_visits:
            raise InspectionPlannerError("The optimization result has no scheduled visits to save.")

        stored_result = json.loads(json.dumps(payload.optimization_result, ensure_ascii=False, default=str))
        # The integrity token protects the optimize-to-save handoff. The
        # persisted snapshot is rebuilt from validated server-side values and
        # is intentionally not reusable as a save token.
        stored_result.pop("integrity_token", None)
        stored_result["visits"] = [
            {
                "client_id": visit["client_id"],
                "property_id": visit["property_id"],
                "property_address": visit["property_address"],
                "latitude": visit["latitude"],
                "longitude": visit["longitude"],
                "agent_ids": visit["agent_ids"],
                "agent_names": visit["agent_names"],
                "duration_minutes": visit["duration_minutes"],
                "buffer_minutes": visit["buffer_minutes"],
                "earliest_time": visit["earliest_time"],
                "latest_time": visit["latest_time"],
                "scheduled_start": visit["scheduled_start"].isoformat(),
                "scheduled_end": visit["scheduled_end"].isoformat(),
                "sequence": visit["sequence"],
                "travel_minutes": visit["travel_minutes"],
                "distance_km": visit["distance_km"],
                "conflicts": visit["conflicts"],
                "notes": visit["notes"],
            }
            for visit in cleaned_visits
        ]

        now = datetime.utcnow()
        plan = InspectionPlan(
            mailbox=mailbox,
            name=payload.plan_date.isoformat(),
            status=payload.status,
            plan_date=payload.plan_date,
            day_start=payload.day_start,
            day_end=payload.day_end,
            timezone=DEFAULT_TIMEZONE,
            start_address=str(stored_result.get("start_address") or ""),
            allow_agent_overlap=payload.allow_agent_overlap,
            provider=str(stored_result.get("provider") or "")[:120] or None,
            optimization_result_json=json.dumps(stored_result, ensure_ascii=False, default=str),
            created_by_user_id=user.id,
            created_at=now,
            updated_at=now,
        )
        db.add(plan)
        db.flush()
        for visit in cleaned_visits:
            db.add(
                InspectionVisit(
                    mailbox=mailbox,
                    plan_id=plan.id,
                    client_id=visit["client_id"],
                    property_id=visit["property_id"],
                    property_address=visit["property_address"],
                    latitude=visit["latitude"],
                    longitude=visit["longitude"],
                    agent_ids_json=json.dumps(visit["agent_ids"]),
                    agent_names_json=json.dumps(visit["agent_names"], ensure_ascii=False),
                    duration_minutes=visit["duration_minutes"],
                    buffer_minutes=visit["buffer_minutes"],
                    scheduled_start=visit["scheduled_start"],
                    scheduled_end=visit["scheduled_end"],
                    sequence=visit["sequence"],
                    travel_minutes=visit["travel_minutes"],
                    distance_km=visit["distance_km"],
                    conflicts_json=json.dumps(visit["conflicts"], ensure_ascii=False, default=str),
                    notes=visit["notes"],
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()
        saved = (
            db.query(InspectionPlan)
            .options(selectinload(InspectionPlan.visits), selectinload(InspectionPlan.created_by))
            .filter(InspectionPlan.id == plan.id, InspectionPlan.mailbox == mailbox)
            .one()
        )
        return {"plan": _plan_dict(saved, include_result=True)}
    except InspectionPlannerError as exc:
        db.rollback()
        raise _planner_http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        write_lock.release()


@router.get("/plans")
def list_inspection_plans(
    limit: int = Query(default=12, ge=1, le=100),
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("inspections")),
):
    rows = (
        db.query(InspectionPlan)
        .options(selectinload(InspectionPlan.visits), selectinload(InspectionPlan.created_by))
        .filter(InspectionPlan.mailbox == mailbox)
        .order_by(InspectionPlan.plan_date.desc(), InspectionPlan.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"plans": [_plan_dict(row, include_result=False) for row in rows]}


@router.get("/plans/{plan_id}")
def get_inspection_plan(
    plan_id: int,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("inspections")),
):
    row = (
        db.query(InspectionPlan)
        .options(selectinload(InspectionPlan.visits), selectinload(InspectionPlan.created_by))
        .filter(InspectionPlan.id == plan_id, InspectionPlan.mailbox == mailbox)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Inspection plan not found.")
    return {"plan": _plan_dict(row, include_result=True)}


@router.patch("/plans/{plan_id}/status")
def update_inspection_plan_status(
    plan_id: int,
    payload: InspectionPlanStatusIn,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    _user: User = Depends(require_page_access("inspections")),
):
    row = (
        db.query(InspectionPlan)
        .options(selectinload(InspectionPlan.visits), selectinload(InspectionPlan.created_by))
        .filter(InspectionPlan.id == plan_id, InspectionPlan.mailbox == mailbox)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Inspection plan not found.")

    write_lock, advisory_lock_id = _inspection_write_lock(mailbox, row.plan_date)
    write_lock.acquire()
    try:
        _acquire_database_write_lock(db, advisory_lock_id)
        # Refresh after waiting for the scope lock so the conflict check sees
        # any plan committed by the writer that held it immediately before us.
        row = (
            db.query(InspectionPlan)
            .options(selectinload(InspectionPlan.visits), selectinload(InspectionPlan.created_by))
            .filter(InspectionPlan.id == plan_id, InspectionPlan.mailbox == mailbox)
            .populate_existing()
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Inspection plan not found.")

        target_status = _status_value(payload.status)
        if target_status in _ACTIVE_PLAN_STATUS_VALUES and not row.allow_agent_overlap:
            conflicts = _plan_activation_conflicts(db, row)
            if conflicts:
                raise InspectionPlannerError(
                    {
                        "message": (
                            f"Plan '{row.name}' cannot be changed to {target_status} because "
                            "one or more assigned agents have conflicting inspections."
                        ),
                        "plan_id": row.id,
                        "plan_name": row.name,
                        "target_status": target_status,
                        "conflict_count": len(conflicts),
                        "conflicts": conflicts,
                        "resolution": (
                            "Move or deactivate the conflicting inspection, or recalculate and save "
                            "a replacement plan with agent overlap explicitly enabled."
                        ),
                    },
                    status_code=409,
                )

        row.status = payload.status
        row.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        return {"plan": _plan_dict(row, include_result=True)}
    except InspectionPlannerError as exc:
        db.rollback()
        raise _planner_http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        write_lock.release()
