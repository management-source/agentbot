from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session, selectinload

from app.authz import get_current_user
from app.db import get_db
from app.models import TimesheetEntry, TimesheetReport, User, UserRole
from app.services.timesheet_pdf import generate_timesheet_daily_pdf


router = APIRouter(prefix="/my-space/timesheets", tags=["timesheets"])

REPORT_STATUSES = frozenset(
    {"DRAFT", "SUBMITTED", "CHANGES_REQUESTED", "APPROVED"}
)
EDITABLE_REPORT_STATUSES = frozenset({"DRAFT", "CHANGES_REQUESTED"})
TASK_STATUSES = frozenset(
    {"COMPLETED", "IN_PROGRESS", "FOLLOW_UP_REQUIRED"}
)
REVIEWER_ROLES = frozenset({UserRole.SALES, UserRole.ADMIN})


def _validate_task_status(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in TASK_STATUSES:
        raise ValueError(
            "Status must be COMPLETED, IN_PROGRESS, or FOLLOW_UP_REQUIRED."
        )
    return normalized


class TimesheetEntryCreateIn(BaseModel):
    work_date: date
    start_time: time
    end_time: time
    task: str = Field(min_length=1, max_length=4000)
    status: str = "COMPLETED"

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return _validate_task_status(value)


class TimesheetEntryPatchIn(BaseModel):
    start_time: time | None = None
    end_time: time | None = None
    task: str | None = Field(default=None, min_length=1, max_length=4000)
    status: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_task_status(value)


class TimesheetReviewIn(BaseModel):
    action: Literal["approve", "send_back"]
    comment: str | None = Field(default=None, max_length=5000)


def _is_reviewer(user: User) -> bool:
    return user.role in REVIEWER_ROLES


def _require_reviewer(user: User) -> None:
    if not _is_reviewer(user):
        raise HTTPException(
            status_code=403,
            detail="Only a Director or Administrator can review timesheets.",
        )


def _is_report_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


def _require_report_admin(user: User) -> None:
    if not _is_report_admin(user):
        raise HTTPException(
            status_code=403,
            detail="Only an Administrator can delete timesheet reports.",
        )


def _entry_out(entry: TimesheetEntry, work_date: date | None = None) -> dict:
    report_date = work_date or (entry.report.work_date if entry.report else None)
    return {
        "id": entry.id,
        "report_id": entry.report_id,
        "work_date": report_date.isoformat() if report_date else None,
        "start_time": entry.start_time.strftime("%H:%M"),
        "end_time": entry.end_time.strftime("%H:%M"),
        "duration_minutes": entry.duration_minutes,
        "task": entry.task,
        "status": entry.status,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


def _report_out(report: TimesheetReport) -> dict:
    entries = sorted(report.entries, key=lambda row: (row.start_time, row.id))
    reviewer = report.reviewed_by
    staff = report.staff
    return {
        "id": report.id,
        "staff_user_id": report.staff_user_id,
        "staff_name": staff.name if staff else None,
        "staff_email": staff.email if staff else None,
        "staff_avatar_url": staff.avatar_url if staff else None,
        "work_date": report.work_date.isoformat(),
        "status": report.status,
        "total_duration_minutes": sum(row.duration_minutes for row in entries),
        "director_comment": report.director_comment,
        "submitted_at": report.submitted_at.isoformat() if report.submitted_at else None,
        "reviewed_at": report.reviewed_at.isoformat() if report.reviewed_at else None,
        "reviewed_by_user_id": report.reviewed_by_user_id,
        "reviewed_by_name": reviewer.name if reviewer else None,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
        "entries": [_entry_out(row, report.work_date) for row in entries],
    }


def _report_query(db: Session):
    return db.query(TimesheetReport).options(
        selectinload(TimesheetReport.entries),
        selectinload(TimesheetReport.staff),
        selectinload(TimesheetReport.reviewed_by),
    )


def _own_report_for_date(
    db: Session,
    user_id: int,
    work_date: date,
    *,
    for_update: bool = False,
) -> TimesheetReport | None:
    query = _report_query(db).filter(
        TimesheetReport.staff_user_id == user_id,
        TimesheetReport.work_date == work_date,
    )
    if for_update:
        query = query.with_for_update()
    return query.first()


def _get_or_create_report(
    db: Session,
    user: User,
    work_date: date,
) -> TimesheetReport:
    report = _own_report_for_date(db, user.id, work_date, for_update=True)
    if report:
        return report

    now = datetime.utcnow()
    report = TimesheetReport(
        staff_user_id=user.id,
        work_date=work_date,
        status="DRAFT",
        created_at=now,
        updated_at=now,
    )
    db.add(report)
    db.flush()
    report.staff = user
    return report


def _get_own_entry(
    db: Session,
    user_id: int,
    entry_id: int,
) -> TimesheetEntry:
    entry = (
        db.query(TimesheetEntry)
        .join(TimesheetReport, TimesheetReport.id == TimesheetEntry.report_id)
        .options(
            selectinload(TimesheetEntry.report).selectinload(TimesheetReport.entries),
            selectinload(TimesheetEntry.report).selectinload(TimesheetReport.staff),
            selectinload(TimesheetEntry.report).selectinload(TimesheetReport.reviewed_by),
        )
        .filter(
            TimesheetEntry.id == entry_id,
            TimesheetReport.staff_user_id == user_id,
        )
        .with_for_update()
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Timesheet entry not found.")
    return entry


def _ensure_editable(report: TimesheetReport) -> None:
    if report.status not in EDITABLE_REPORT_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Submitted or approved timesheets cannot be edited.",
        )


def _duration_minutes(start_time: time, end_time: time) -> int:
    if start_time.tzinfo is not None or end_time.tzinfo is not None:
        raise HTTPException(
            status_code=400,
            detail="Use local start and end times without a timezone offset.",
        )
    if any(
        (
            start_time.second,
            start_time.microsecond,
            end_time.second,
            end_time.microsecond,
        )
    ):
        raise HTTPException(
            status_code=400,
            detail="Start and end times must use whole-minute precision.",
        )

    start = datetime.combine(date.min, start_time)
    end = datetime.combine(date.min, end_time)
    seconds = int((end - start).total_seconds())
    if seconds <= 0:
        raise HTTPException(
            status_code=400,
            detail="End time must be later than start time on the same date.",
        )
    return seconds // 60


def _ensure_no_overlap(
    report: TimesheetReport,
    start_time: time,
    end_time: time,
    *,
    exclude_entry_id: int | None = None,
) -> None:
    for existing in report.entries:
        if exclude_entry_id is not None and existing.id == exclude_entry_id:
            continue
        # Treat each interval as [start, end), so adjacent tasks are valid.
        if start_time < existing.end_time and end_time > existing.start_time:
            raise HTTPException(
                status_code=409,
                detail="This time overlaps another entry for the selected date.",
            )


def _normalize_report_status(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in REPORT_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Status must be DRAFT, SUBMITTED, CHANGES_REQUESTED, or APPROVED."
            ),
        )
    return normalized


@router.get("/day")
def get_timesheet_day(
    work_date: date = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = _own_report_for_date(db, user.id, work_date)
    return {
        "report": _report_out(report) if report else None,
        "can_review": _is_reviewer(user),
        "can_delete_reports": _is_report_admin(user),
    }


@router.post("/entries")
def create_timesheet_entry(
    payload: TimesheetEntryCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = payload.task.strip()
    if not task:
        raise HTTPException(status_code=400, detail="Task is required.")
    duration = _duration_minutes(payload.start_time, payload.end_time)
    report = _get_or_create_report(db, user, payload.work_date)
    _ensure_editable(report)
    _ensure_no_overlap(report, payload.start_time, payload.end_time)

    now = datetime.utcnow()
    entry = TimesheetEntry(
        report_id=report.id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        duration_minutes=duration,
        task=task,
        status=payload.status,
        created_at=now,
        updated_at=now,
    )
    report.updated_at = now
    db.add(entry)
    db.commit()
    db.refresh(entry)
    report = _own_report_for_date(db, user.id, payload.work_date)
    return {
        "entry": _entry_out(entry, payload.work_date),
        "report": _report_out(report),
    }


@router.patch("/entries/{entry_id}")
def update_timesheet_entry(
    entry_id: int,
    payload: TimesheetEntryPatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entry = _get_own_entry(db, user.id, entry_id)
    report = entry.report
    _ensure_editable(report)

    start_time = payload.start_time or entry.start_time
    end_time = payload.end_time or entry.end_time
    duration = _duration_minutes(start_time, end_time)
    _ensure_no_overlap(
        report,
        start_time,
        end_time,
        exclude_entry_id=entry.id,
    )

    if payload.task is not None:
        task = payload.task.strip()
        if not task:
            raise HTTPException(status_code=400, detail="Task is required.")
        entry.task = task
    if payload.status is not None:
        entry.status = payload.status
    entry.start_time = start_time
    entry.end_time = end_time
    entry.duration_minutes = duration
    entry.updated_at = datetime.utcnow()
    report.updated_at = entry.updated_at
    db.commit()
    db.refresh(entry)
    report = _own_report_for_date(db, user.id, report.work_date)
    return {
        "entry": _entry_out(entry, report.work_date),
        "report": _report_out(report),
    }


@router.delete("/entries/{entry_id}")
def delete_timesheet_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entry = _get_own_entry(db, user.id, entry_id)
    report = entry.report
    _ensure_editable(report)
    work_date = report.work_date
    report.updated_at = datetime.utcnow()
    db.delete(entry)
    db.commit()
    report = _own_report_for_date(db, user.id, work_date)
    return {
        "ok": True,
        "report": _report_out(report),
    }


@router.post("/day/{work_date}/submit")
def submit_timesheet_day(
    work_date: date,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = _own_report_for_date(db, user.id, work_date, for_update=True)
    if not report:
        raise HTTPException(status_code=404, detail="Timesheet not found.")
    if report.status not in EDITABLE_REPORT_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Only a draft or returned timesheet can be submitted.",
        )
    if not report.entries:
        raise HTTPException(
            status_code=400,
            detail="Add at least one task before sending the timesheet for approval.",
        )

    now = datetime.utcnow()
    report.status = "SUBMITTED"
    report.submitted_at = now
    report.updated_at = now
    db.commit()
    report = _own_report_for_date(db, user.id, work_date)
    return {"report": _report_out(report)}


@router.get("/reports")
def list_timesheet_reports(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    staff_id: int | None = Query(default=None, gt=0),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_reviewer(user)

    if date_from is None and date_to is None:
        date_from = date_to = date.today()
    elif date_from is None:
        date_from = date_to
    elif date_to is None:
        date_to = date_from
    if date_from is None or date_to is None:  # Helps static type checkers.
        raise HTTPException(status_code=400, detail="A report date is required.")
    if date_to < date_from:
        raise HTTPException(
            status_code=400,
            detail="date_to must be on or after date_from.",
        )

    query = _report_query(db).filter(
        TimesheetReport.work_date >= date_from,
        TimesheetReport.work_date <= date_to,
        TimesheetReport.status != "DRAFT",
    )
    if staff_id is not None:
        query = query.filter(TimesheetReport.staff_user_id == staff_id)
    if status is not None:
        query = query.filter(
            TimesheetReport.status == _normalize_report_status(status)
        )

    reports = query.order_by(
        TimesheetReport.work_date.desc(),
        TimesheetReport.staff_user_id.asc(),
    ).all()
    return {"reports": [_report_out(report) for report in reports]}


@router.get("/reports/export")
def export_daily_timesheet_reports(
    work_date: date = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_reviewer(user)
    reports = (
        _report_query(db)
        .filter(
            TimesheetReport.work_date == work_date,
            TimesheetReport.status != "DRAFT",
        )
        .all()
    )
    reports.sort(
        key=lambda report: (
            (report.staff.name if report.staff else "").casefold(),
            report.staff_user_id,
        )
    )

    content = generate_timesheet_daily_pdf(
        [_report_out(report) for report in reports],
        work_date,
    )
    filename = f"timesheet-report-{work_date.isoformat()}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/reports/{report_id}")
def delete_timesheet_report(
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_report_admin(user)
    report = (
        _report_query(db)
        .filter(TimesheetReport.id == report_id)
        .with_for_update()
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Timesheet report not found.")

    deleted = {
        "ok": True,
        "report_id": report.id,
        "work_date": report.work_date.isoformat(),
        "staff_user_id": report.staff_user_id,
    }
    db.delete(report)
    db.commit()
    return deleted


@router.post("/reports/{report_id}/review")
def review_timesheet_report(
    report_id: int,
    payload: TimesheetReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_reviewer(user)
    report = (
        _report_query(db)
        .filter(TimesheetReport.id == report_id)
        .with_for_update()
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Timesheet report not found.")
    if report.status != "SUBMITTED":
        raise HTTPException(
            status_code=409,
            detail="Only a submitted timesheet can be reviewed.",
        )

    comment = (payload.comment or "").strip()
    if payload.action == "send_back" and not comment:
        raise HTTPException(
            status_code=400,
            detail="Add a comment explaining what the staff member needs to change.",
        )

    now = datetime.utcnow()
    report.status = (
        "APPROVED" if payload.action == "approve" else "CHANGES_REQUESTED"
    )
    report.director_comment = comment or None
    report.reviewed_by_user_id = user.id
    report.reviewed_at = now
    report.updated_at = now
    db.commit()
    report = _report_query(db).filter(TimesheetReport.id == report_id).first()
    return {"report": _report_out(report)}
