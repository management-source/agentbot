from __future__ import annotations

import json
import os
from datetime import datetime

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
from app.deps import get_current_mailbox
from app.models import (
    AuditAction,
    Base,
    DismissedEmailThread,
    ThreadTicket,
    ThreadTicketAudit,
    ThreadTicketNote,
    TicketStatus,
    User,
    UserRole,
)
from app.routers.tickets import router as tickets_router
from app.services.gmail_sync import _upsert_ticket_from_thread


MAILBOX = "inbox@example.com"
OTHER_MAILBOX = "other@example.com"


@pytest.fixture()
def ticket_api():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    admin = User(
        email="admin@example.com",
        name="Admin",
        role=UserRole.ADMIN,
        is_active=True,
        password_hash="not-used",
    )
    staff = User(
        email="staff@example.com",
        name="Staff Member",
        role=UserRole.PM,
        is_active=True,
        password_hash="not-used",
    )
    readonly = User(
        email="readonly@example.com",
        name="Read Only",
        role=UserRole.READONLY,
        is_active=True,
        password_hash="not-used",
    )
    db.add_all([admin, staff, readonly])
    db.commit()

    api = FastAPI()
    api.include_router(tickets_router, prefix="/tickets")
    context = {"user": admin, "mailbox": MAILBOX}

    def override_db():
        yield db

    api.dependency_overrides[get_db] = override_db
    api.dependency_overrides[get_current_user] = lambda: context["user"]
    api.dependency_overrides[get_current_mailbox] = lambda: context["mailbox"]

    try:
        yield TestClient(api), db, context, {"admin": admin, "staff": staff, "readonly": readonly}
    finally:
        db.close()
        engine.dispose()


def _ticket(
    gmail_id: str,
    *,
    mailbox: str = MAILBOX,
    status: TicketStatus = TicketStatus.PENDING,
    last_message_id: str | None = None,
) -> ThreadTicket:
    return ThreadTicket(
        thread_id=f"{mailbox}:{gmail_id}",
        gmail_thread_id=gmail_id,
        mailbox=mailbox,
        last_message_id=last_message_id or f"message-{gmail_id}",
        subject=f"Subject {gmail_id}",
        from_email="sender@example.net",
        last_message_at=datetime(2026, 9, 2, 9, 0),
        status=status,
        is_not_replied=status in (TicketStatus.PENDING, TicketStatus.IN_PROGRESS),
    )


def test_assigning_pending_ticket_moves_it_to_in_progress(ticket_api):
    client, db, _context, users = ticket_api
    ticket = _ticket("assign-me")
    db.add(ticket)
    db.commit()

    response = client.patch(
        f"/tickets/{ticket.thread_id}/assignee",
        json={"assignee_user_id": users["staff"].id},
    )

    assert response.status_code == 200
    assert response.json()["status"] == TicketStatus.IN_PROGRESS.value
    assert response.json()["is_not_replied"] is True
    db.refresh(ticket)
    assert ticket.assignee_user_id == users["staff"].id
    assert ticket.status == TicketStatus.IN_PROGRESS

    audits = (
        db.query(ThreadTicketAudit)
        .filter(ThreadTicketAudit.thread_id == ticket.thread_id)
        .order_by(ThreadTicketAudit.id)
        .all()
    )
    assert [audit.action for audit in audits] == [AuditAction.ASSIGNED, AuditAction.STATUS_CHANGED]
    assert json.loads(audits[1].detail)["reason"] == "staff_assigned"


def test_assigning_closed_ticket_does_not_reopen_it(ticket_api):
    client, db, _context, users = ticket_api
    ticket = _ticket("already-done", status=TicketStatus.RESPONDED)
    db.add(ticket)
    db.commit()

    response = client.patch(
        f"/tickets/{ticket.thread_id}/assignee",
        json={"assignee_user_id": users["staff"].id},
    )

    assert response.status_code == 200
    assert response.json()["status"] == TicketStatus.RESPONDED.value
    db.refresh(ticket)
    assert ticket.status == TicketStatus.RESPONDED


def test_ticket_list_returns_all_tab_counts(ticket_api):
    client, db, _context, _users = ticket_api
    db.add_all(
        [
            _ticket("pending"),
            _ticket("working", status=TicketStatus.IN_PROGRESS),
            _ticket("responded", status=TicketStatus.RESPONDED),
            _ticket("closed", status=TicketStatus.NO_REPLY_NEEDED),
            _ticket("different-mailbox", mailbox=OTHER_MAILBOX),
        ]
    )
    db.commit()

    response = client.get("/tickets", params={"tab": "awaiting_reply"})

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["counts"] == {
        "all": 4,
        "awaiting_reply": 1,
        "in_progress": 1,
        "responded": 1,
        "no_reply_needed": 1,
        "assigned_to_me": 0,
    }


def test_assigned_to_me_tab_is_isolated_to_logged_in_user_and_mailbox(ticket_api):
    client, db, context, users = ticket_api
    staff_pending = _ticket("staff-pending")
    staff_responded = _ticket("staff-responded", status=TicketStatus.RESPONDED)
    admin_ticket = _ticket("admin-ticket", status=TicketStatus.IN_PROGRESS)
    other_mailbox = _ticket("staff-other-mailbox", mailbox=OTHER_MAILBOX)
    staff_pending.assignee_user_id = users["staff"].id
    staff_responded.assignee_user_id = users["staff"].id
    admin_ticket.assignee_user_id = users["admin"].id
    other_mailbox.assignee_user_id = users["staff"].id
    db.add_all([staff_pending, staff_responded, admin_ticket, other_mailbox])
    db.commit()
    context["user"] = users["staff"]

    response = client.get("/tickets", params={"tab": "assigned_to_me"})

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["counts"]["assigned_to_me"] == 1
    assert {item["thread_id"] for item in data["items"]} == {staff_pending.thread_id}
    assert all(item["assignee_user_id"] == users["staff"].id for item in data["items"])


def test_assigned_to_me_clears_after_completion_or_reassignment(ticket_api):
    client, db, context, users = ticket_api
    completed = _ticket("complete-my-ticket", status=TicketStatus.IN_PROGRESS)
    reassigned = _ticket("reassign-my-ticket", status=TicketStatus.IN_PROGRESS)
    completed.assignee_user_id = users["staff"].id
    reassigned.assignee_user_id = users["staff"].id
    db.add_all([completed, reassigned])
    db.commit()
    context["user"] = users["staff"]

    status_response = client.patch(
        f"/tickets/{completed.thread_id}/status",
        json={"status": TicketStatus.RESPONDED.value},
    )
    assignment_response = client.patch(
        f"/tickets/{reassigned.thread_id}/assignee",
        json={"assignee_user_id": users["admin"].id},
    )

    assert status_response.status_code == 200
    assert assignment_response.status_code == 200

    staff_queue = client.get("/tickets", params={"tab": "assigned_to_me"}).json()
    assert staff_queue["total"] == 0
    assert staff_queue["counts"]["assigned_to_me"] == 0

    responded_queue = client.get("/tickets", params={"tab": "responded"}).json()
    assert {item["thread_id"] for item in responded_queue["items"]} == {completed.thread_id}

    context["user"] = users["admin"]
    admin_queue = client.get("/tickets", params={"tab": "assigned_to_me"}).json()
    assert {item["thread_id"] for item in admin_queue["items"]} == {reassigned.thread_id}


def test_purge_only_removes_no_reply_needed_from_selected_mailbox(ticket_api):
    client, db, _context, users = ticket_api
    removable = _ticket("remove", status=TicketStatus.NO_REPLY_NEEDED, last_message_id="last-remove")
    keep_pending = _ticket("keep-pending")
    keep_other_mailbox = _ticket(
        "keep-other",
        mailbox=OTHER_MAILBOX,
        status=TicketStatus.NO_REPLY_NEEDED,
    )
    db.add_all([removable, keep_pending, keep_other_mailbox])
    db.flush()
    db.add(
        ThreadTicketNote(
            mailbox=MAILBOX,
            thread_id=removable.thread_id,
            author_user_id=users["admin"].id,
            body="old note",
        )
    )
    db.add(
        ThreadTicketAudit(
            mailbox=MAILBOX,
            thread_id=removable.thread_id,
            action=AuditAction.STATUS_CHANGED,
            actor_user_id=users["admin"].id,
        )
    )
    db.commit()
    removable_id = removable.thread_id
    keep_pending_id = keep_pending.thread_id
    keep_other_mailbox_id = keep_other_mailbox.thread_id

    response = client.post("/tickets/no-reply-needed/purge", json={"confirm": "PURGE"})

    assert response.status_code == 200
    assert response.json()["deleted"] == 1
    assert response.json()["gmail_deleted"] is False
    assert db.get(ThreadTicket, removable_id) is None
    assert db.get(ThreadTicket, keep_pending_id) is not None
    assert db.get(ThreadTicket, keep_other_mailbox_id) is not None
    assert db.query(ThreadTicketNote).filter_by(thread_id=removable_id).count() == 0
    assert db.query(ThreadTicketAudit).filter_by(thread_id=removable_id).count() == 0

    dismissed = db.query(DismissedEmailThread).filter_by(mailbox=MAILBOX, gmail_thread_id="remove").one()
    assert dismissed.last_message_id == "last-remove"
    assert dismissed.dismissed_by_user_id == users["admin"].id


def test_purge_requires_admin_access(ticket_api):
    client, db, context, users = ticket_api
    db.add(_ticket("protected", status=TicketStatus.NO_REPLY_NEEDED))
    db.commit()
    context["user"] = users["readonly"]

    response = client.post("/tickets/no-reply-needed/purge", json={"confirm": "PURGE"})

    assert response.status_code == 403
    assert db.get(ThreadTicket, f"{MAILBOX}:protected") is not None


def _gmail_thread(message_id: str) -> dict:
    return {
        "id": "gmail-thread",
        "messages": [
            {
                "id": message_id,
                "internalDate": "1788339600000",
                "labelIds": ["INBOX", "UNREAD"],
                "snippet": "A new inbound message",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Tenant <tenant@example.net>"},
                        {"name": "Subject", "value": "Routine inspection"},
                    ]
                },
            }
        ],
    }


def test_dismissed_thread_stays_hidden_until_a_new_message_arrives(ticket_api):
    _client, db, _context, users = ticket_api
    dismissed = DismissedEmailThread(
        mailbox=MAILBOX,
        gmail_thread_id="gmail-thread",
        last_message_id="old-message",
        dismissed_by_user_id=users["admin"].id,
    )
    db.add(dismissed)
    db.commit()

    unchanged = _upsert_ticket_from_thread(
        db,
        service=None,
        mailbox=MAILBOX,
        thread_id="gmail-thread",
        thread=_gmail_thread("old-message"),
        auto_triage=False,
    )
    assert unchanged is False
    assert db.get(ThreadTicket, f"{MAILBOX}:gmail-thread") is None

    reopened = _upsert_ticket_from_thread(
        db,
        service=None,
        mailbox=MAILBOX,
        thread_id="gmail-thread",
        thread=_gmail_thread("new-message"),
        auto_triage=False,
    )
    db.commit()

    assert reopened is True
    ticket = db.get(ThreadTicket, f"{MAILBOX}:gmail-thread")
    assert ticket is not None
    assert ticket.status == TicketStatus.PENDING
    assert ticket.last_message_id == "new-message"
    assert db.query(DismissedEmailThread).filter_by(mailbox=MAILBOX, gmail_thread_id="gmail-thread").count() == 0
