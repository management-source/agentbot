from __future__ import annotations

import csv
import io
import os
from datetime import date

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
from app.models import Base, TimesheetEntry, TimesheetReport, User, UserRole
from app.routers.timesheets import router as timesheets_router


WORK_DATE = date(2026, 8, 26)
OTHER_DATE = date(2026, 8, 25)


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
def users(db):
    rows = {
        "staff": User(
            email="staff@example.com",
            name="Staff Member",
            role=UserRole.PM,
            is_active=True,
            password_hash="not-used",
        ),
        "other_staff": User(
            email="other@example.com",
            name="Other Staff",
            role=UserRole.LEASING,
            is_active=True,
            password_hash="not-used",
        ),
        "director": User(
            email="lushan@example.com",
            name="Lushan Dons",
            role=UserRole.SALES,
            is_active=True,
            password_hash="not-used",
        ),
        "admin": User(
            email="admin@example.com",
            name="Administrator",
            role=UserRole.ADMIN,
            is_active=True,
            password_hash="not-used",
        ),
        "manager": User(
            email="manager@example.com",
            name="Another Manager",
            role=UserRole.PM,
            is_active=True,
            password_hash="not-used",
        ),
        "accounts": User(
            email="accounts@example.com",
            name="Accounts Staff",
            role=UserRole.ACCOUNTS,
            is_active=True,
            password_hash="not-used",
        ),
        "readonly": User(
            email="readonly@example.com",
            name="Read Only Staff",
            role=UserRole.READONLY,
            is_active=True,
            password_hash="not-used",
        ),
    }
    db.add_all(rows.values())
    db.commit()
    for row in rows.values():
        db.refresh(row)
    return rows


@pytest.fixture()
def api_client(db, users):
    app = FastAPI()
    app.include_router(timesheets_router)
    context = {"user": users["staff"]}

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: context["user"]
    return TestClient(app), context


def _entry_payload(
    *,
    work_date: date = WORK_DATE,
    start_time: str = "09:00",
    end_time: str = "10:00",
    task: str = "Prepare the daily property report",
    status: str = "COMPLETED",
) -> dict:
    return {
        "work_date": work_date.isoformat(),
        "start_time": start_time,
        "end_time": end_time,
        "task": task,
        "status": status,
    }


def _create_entry(client: TestClient, **overrides) -> dict:
    response = client.post(
        "/my-space/timesheets/entries",
        json=_entry_payload(**overrides),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _submit_day(client: TestClient, work_date: date = WORK_DATE) -> dict:
    response = client.post(
        f"/my-space/timesheets/day/{work_date.isoformat()}/submit"
    )
    assert response.status_code == 200, response.text
    return response.json()["report"]


def test_entry_crud_calculates_duration_on_the_server(db, api_client):
    client, _context = api_client
    payload = _entry_payload(
        start_time="09:15",
        end_time="10:45",
        task="  Prepare inspection notes  ",
        status="in_progress",
    )
    payload["duration_minutes"] = 1

    created = client.post("/my-space/timesheets/entries", json=payload)

    assert created.status_code == 200
    body = created.json()
    entry_id = body["entry"]["id"]
    assert body["entry"]["duration_minutes"] == 90
    assert body["entry"]["task"] == "Prepare inspection notes"
    assert body["entry"]["status"] == "IN_PROGRESS"
    assert body["report"]["status"] == "DRAFT"
    assert body["report"]["total_duration_minutes"] == 90
    assert db.query(TimesheetReport).count() == 1
    assert db.query(TimesheetEntry).one().duration_minutes == 90

    updated = client.patch(
        f"/my-space/timesheets/entries/{entry_id}",
        json={
            "end_time": "11:00",
            "task": "  Finish inspection notes  ",
            "status": "follow_up_required",
        },
    )

    assert updated.status_code == 200
    assert updated.json()["entry"]["duration_minutes"] == 105
    assert updated.json()["entry"]["task"] == "Finish inspection notes"
    assert updated.json()["entry"]["status"] == "FOLLOW_UP_REQUIRED"

    deleted = client.delete(f"/my-space/timesheets/entries/{entry_id}")

    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert deleted.json()["report"]["entries"] == []
    assert deleted.json()["report"]["total_duration_minutes"] == 0
    assert db.query(TimesheetReport).count() == 1
    assert db.query(TimesheetEntry).count() == 0

    empty_submit = client.post(
        f"/my-space/timesheets/day/{WORK_DATE.isoformat()}/submit"
    )
    assert empty_submit.status_code == 400
    assert "at least one task" in empty_submit.json()["detail"]


def test_invalid_times_tasks_and_statuses_do_not_create_partial_rows(db, api_client):
    client, _context = api_client

    equal_times = client.post(
        "/my-space/timesheets/entries",
        json=_entry_payload(start_time="10:00", end_time="10:00"),
    )
    reversed_times = client.post(
        "/my-space/timesheets/entries",
        json=_entry_payload(start_time="11:00", end_time="10:00"),
    )
    second_precision = client.post(
        "/my-space/timesheets/entries",
        json=_entry_payload(start_time="09:00:30", end_time="10:00"),
    )
    blank_task = client.post(
        "/my-space/timesheets/entries",
        json=_entry_payload(task="   "),
    )
    invalid_status = client.post(
        "/my-space/timesheets/entries",
        json=_entry_payload(status="WAITING"),
    )

    for response in (equal_times, reversed_times):
        assert response.status_code == 400
        assert "later than start time" in response.json()["detail"]
    assert second_precision.status_code == 400
    assert "whole-minute precision" in second_precision.json()["detail"]
    assert blank_task.status_code == 400
    assert blank_task.json()["detail"] == "Task is required."
    assert invalid_status.status_code == 422
    assert db.query(TimesheetReport).count() == 0
    assert db.query(TimesheetEntry).count() == 0


def test_overlaps_are_rejected_while_adjacent_entries_are_allowed(db, api_client):
    client, _context = api_client
    first = _create_entry(client, start_time="09:00", end_time="10:00")
    adjacent = _create_entry(
        client,
        start_time="10:00",
        end_time="10:30",
        task="Call the landlord",
    )

    assert adjacent["report"]["total_duration_minutes"] == 90
    assert len(adjacent["report"]["entries"]) == 2
    assert db.query(TimesheetReport).count() == 1

    overlapping_create = client.post(
        "/my-space/timesheets/entries",
        json=_entry_payload(
            start_time="09:30",
            end_time="10:15",
            task="Overlapping work",
        ),
    )
    overlapping_patch = client.patch(
        f"/my-space/timesheets/entries/{adjacent['entry']['id']}",
        json={"start_time": "09:59"},
    )

    for response in (overlapping_create, overlapping_patch):
        assert response.status_code == 409
        assert "overlaps another entry" in response.json()["detail"]

    day = client.get(
        "/my-space/timesheets/day",
        params={"work_date": WORK_DATE.isoformat()},
    ).json()["report"]
    assert [entry["id"] for entry in day["entries"]] == [
        first["entry"]["id"],
        adjacent["entry"]["id"],
    ]
    assert day["entries"][1]["start_time"] == "10:00"
    assert day["total_duration_minutes"] == 90


def test_staff_can_only_access_and_mutate_their_own_timesheet(db, users, api_client):
    client, context = api_client
    created = _create_entry(client)
    entry_id = created["entry"]["id"]
    report_id = created["report"]["id"]

    context["user"] = users["other_staff"]
    own_day = client.get(
        "/my-space/timesheets/day",
        params={"work_date": WORK_DATE.isoformat()},
    )
    foreign_patch = client.patch(
        f"/my-space/timesheets/entries/{entry_id}",
        json={"task": "Try to change another user's task"},
    )
    foreign_delete = client.delete(f"/my-space/timesheets/entries/{entry_id}")
    foreign_submit = client.post(
        f"/my-space/timesheets/day/{WORK_DATE.isoformat()}/submit"
    )

    assert own_day.status_code == 200
    assert own_day.json()["report"] is None
    assert foreign_patch.status_code == 404
    assert foreign_delete.status_code == 404
    assert foreign_submit.status_code == 404

    db.expire_all()
    protected = db.get(TimesheetReport, report_id)
    assert protected.staff_user_id == users["staff"].id
    assert protected.status == "DRAFT"
    assert db.get(TimesheetEntry, entry_id).task == created["entry"]["task"]


def test_submission_send_back_resubmission_and_approval_lifecycle(
    db,
    users,
    api_client,
):
    client, context = api_client
    created = _create_entry(client)
    entry_id = created["entry"]["id"]
    report_id = created["report"]["id"]

    submitted = _submit_day(client)
    assert submitted["status"] == "SUBMITTED"
    assert submitted["submitted_at"] is not None

    repeated_submit = client.post(
        f"/my-space/timesheets/day/{WORK_DATE.isoformat()}/submit"
    )
    locked_patch = client.patch(
        f"/my-space/timesheets/entries/{entry_id}",
        json={"task": "Submitted task edit"},
    )
    locked_delete = client.delete(f"/my-space/timesheets/entries/{entry_id}")
    assert repeated_submit.status_code == 409
    assert locked_patch.status_code == 409
    assert locked_delete.status_code == 409

    context["user"] = users["director"]
    blank_send_back = client.post(
        f"/my-space/timesheets/reports/{report_id}/review",
        json={"action": "send_back", "comment": "   "},
    )
    assert blank_send_back.status_code == 400
    db.expire_all()
    assert db.get(TimesheetReport, report_id).status == "SUBMITTED"

    sent_back_response = client.post(
        f"/my-space/timesheets/reports/{report_id}/review",
        json={
            "action": "send_back",
            "comment": "  Please add the follow-up outcome.  ",
        },
    )
    assert sent_back_response.status_code == 200
    sent_back = sent_back_response.json()["report"]
    assert sent_back["status"] == "CHANGES_REQUESTED"
    assert sent_back["director_comment"] == "Please add the follow-up outcome."
    assert sent_back["reviewed_by_user_id"] == users["director"].id
    assert sent_back["reviewed_by_name"] == users["director"].name
    assert sent_back["reviewed_at"] is not None

    context["user"] = users["staff"]
    returned_day = client.get(
        "/my-space/timesheets/day",
        params={"work_date": WORK_DATE.isoformat()},
    ).json()["report"]
    assert returned_day["director_comment"] == "Please add the follow-up outcome."

    editable_again = client.patch(
        f"/my-space/timesheets/entries/{entry_id}",
        json={
            "task": "Prepare the daily property report and record the outcome",
            "status": "COMPLETED",
        },
    )
    assert editable_again.status_code == 200
    assert editable_again.json()["report"]["status"] == "CHANGES_REQUESTED"
    assert _submit_day(client)["status"] == "SUBMITTED"

    context["user"] = users["director"]
    approved_response = client.post(
        f"/my-space/timesheets/reports/{report_id}/review",
        json={"action": "approve", "comment": "Approved for the day."},
    )
    assert approved_response.status_code == 200
    approved = approved_response.json()["report"]
    assert approved["status"] == "APPROVED"
    assert approved["director_comment"] == "Approved for the day."

    review_again = client.post(
        f"/my-space/timesheets/reports/{report_id}/review",
        json={"action": "approve"},
    )
    assert review_again.status_code == 409

    context["user"] = users["staff"]
    approved_patch = client.patch(
        f"/my-space/timesheets/entries/{entry_id}",
        json={"task": "Try to change approved work"},
    )
    approved_delete = client.delete(f"/my-space/timesheets/entries/{entry_id}")
    assert approved_patch.status_code == 409
    assert approved_delete.status_code == 409


@pytest.mark.parametrize(
    ("reviewer_key", "allowed"),
    [
        ("director", True),
        ("admin", True),
        ("manager", False),
        ("other_staff", False),
        ("accounts", False),
        ("readonly", False),
    ],
)
def test_only_directors_and_administrators_can_review_timesheets(
    db,
    users,
    api_client,
    reviewer_key,
    allowed,
):
    client, context = api_client
    created = _create_entry(client)
    report_id = created["report"]["id"]
    _submit_day(client)
    context["user"] = users[reviewer_key]

    listed = client.get(
        "/my-space/timesheets/reports",
        params={
            "date_from": WORK_DATE.isoformat(),
            "date_to": WORK_DATE.isoformat(),
            "status": "SUBMITTED",
        },
    )
    reviewed = client.post(
        f"/my-space/timesheets/reports/{report_id}/review",
        json={"action": "approve"},
    )

    if allowed:
        assert listed.status_code == 200
        assert [row["id"] for row in listed.json()["reports"]] == [report_id]
        assert reviewed.status_code == 200
        assert reviewed.json()["report"]["status"] == "APPROVED"
        assert reviewed.json()["report"]["reviewed_by_user_id"] == users[
            reviewer_key
        ].id
    else:
        assert listed.status_code == 403
        assert reviewed.status_code == 403
        assert listed.json()["detail"] == (
            "Only a Director or Administrator can review timesheets."
        )
        db.expire_all()
        assert db.get(TimesheetReport, report_id).status == "SUBMITTED"


def test_director_report_filters_exclude_drafts_and_return_staff_totals(
    users,
    api_client,
):
    client, context = api_client

    first = _create_entry(
        client,
        work_date=OTHER_DATE,
        start_time="09:00",
        end_time="10:00",
        task="Inspect application documents",
    )
    _create_entry(
        client,
        work_date=OTHER_DATE,
        start_time="10:00",
        end_time="10:30",
        task="Call the applicant",
    )
    staff_report = _submit_day(client, OTHER_DATE)
    assert staff_report["total_duration_minutes"] == 90

    context["user"] = users["other_staff"]
    second = _create_entry(
        client,
        work_date=WORK_DATE,
        start_time="08:30",
        end_time="09:15",
        task="Prepare advertising copy",
    )
    other_report = _submit_day(client)
    draft = _create_entry(
        client,
        work_date=OTHER_DATE,
        start_time="13:00",
        end_time="14:00",
        task="Unsubmitted draft task",
    )

    context["user"] = users["director"]
    approved = client.post(
        f"/my-space/timesheets/reports/{other_report['id']}/review",
        json={"action": "approve"},
    )
    assert approved.status_code == 200

    all_reports = client.get(
        "/my-space/timesheets/reports",
        params={
            "date_from": OTHER_DATE.isoformat(),
            "date_to": WORK_DATE.isoformat(),
        },
    )
    assert all_reports.status_code == 200
    reports = all_reports.json()["reports"]
    assert [row["id"] for row in reports] == [other_report["id"], staff_report["id"]]
    assert draft["report"]["id"] not in {row["id"] for row in reports}
    assert reports[0]["status"] == "APPROVED"
    assert reports[0]["total_duration_minutes"] == 45
    assert reports[0]["staff_name"] == users["other_staff"].name
    assert reports[0]["staff_email"] == users["other_staff"].email
    assert reports[1]["total_duration_minutes"] == 90

    filtered = client.get(
        "/my-space/timesheets/reports",
        params={
            "date_from": OTHER_DATE.isoformat(),
            "date_to": WORK_DATE.isoformat(),
            "staff_id": users["staff"].id,
            "status": "submitted",
        },
    )
    assert filtered.status_code == 200
    assert [row["id"] for row in filtered.json()["reports"]] == [
        first["report"]["id"]
    ]
    assert [entry["start_time"] for entry in filtered.json()["reports"][0]["entries"]] == [
        "09:00",
        "10:00",
    ]

    invalid_range = client.get(
        "/my-space/timesheets/reports",
        params={
            "date_from": WORK_DATE.isoformat(),
            "date_to": OTHER_DATE.isoformat(),
        },
    )
    assert invalid_range.status_code == 400
    assert invalid_range.json()["detail"] == "date_to must be on or after date_from."


def test_only_administrator_can_delete_a_full_report(db, users, api_client):
    client, context = api_client
    created = _create_entry(client)
    report_id = created["report"]["id"]
    entry_id = created["entry"]["id"]
    _submit_day(client)

    context["user"] = users["director"]
    denied_director = client.delete(
        f"/my-space/timesheets/reports/{report_id}"
    )
    context["user"] = users["manager"]
    denied_manager = client.delete(
        f"/my-space/timesheets/reports/{report_id}"
    )

    assert denied_director.status_code == 403
    assert denied_manager.status_code == 403
    assert db.get(TimesheetReport, report_id) is not None
    assert db.get(TimesheetEntry, entry_id) is not None

    context["user"] = users["admin"]
    permissions = client.get(
        "/my-space/timesheets/day",
        params={"work_date": WORK_DATE.isoformat()},
    )
    deleted = client.delete(f"/my-space/timesheets/reports/{report_id}")

    assert permissions.status_code == 200
    assert permissions.json()["can_delete_reports"] is True
    assert deleted.status_code == 200
    assert deleted.json() == {
        "ok": True,
        "report_id": report_id,
        "work_date": WORK_DATE.isoformat(),
        "staff_user_id": users["staff"].id,
    }
    assert db.get(TimesheetReport, report_id) is None
    assert db.get(TimesheetEntry, entry_id) is None


def test_director_can_export_all_submitted_staff_for_one_day(
    users,
    api_client,
):
    client, context = api_client
    _create_entry(
        client,
        start_time="09:00",
        end_time="10:00",
        task="=SUM(1,1)",
    )
    _submit_day(client)

    context["user"] = users["other_staff"]
    _create_entry(
        client,
        start_time="10:10",
        end_time="11:20",
        task="Prepare advertising copy",
        status="IN_PROGRESS",
    )
    _submit_day(client)

    context["user"] = users["accounts"]
    draft = _create_entry(
        client,
        start_time="12:00",
        end_time="12:30",
        task="Unsubmitted draft must not be exported",
    )

    context["user"] = users["director"]
    exported = client.get(
        "/my-space/timesheets/reports/export",
        params={"work_date": WORK_DATE.isoformat()},
    )

    assert exported.status_code == 200
    assert exported.headers["content-disposition"] == (
        f'attachment; filename="timesheet-report-{WORK_DATE.isoformat()}.csv"'
    )
    rows = list(
        csv.DictReader(
            io.StringIO(exported.content.decode("utf-8-sig"))
        )
    )
    assert len(rows) == 2
    assert {row["Staff Member"] for row in rows} == {
        users["staff"].name,
        users["other_staff"].name,
    }
    assert {row["Approval Status"] for row in rows} == {"SUBMITTED"}
    assert {row["Duration (Minutes)"] for row in rows} == {"60", "70"}
    assert "'=SUM(1,1)" in {row["Task"] for row in rows}
    assert draft["entry"]["task"] not in {row["Task"] for row in rows}

    context["user"] = users["manager"]
    denied = client.get(
        "/my-space/timesheets/reports/export",
        params={"work_date": WORK_DATE.isoformat()},
    )
    assert denied.status_code == 403
