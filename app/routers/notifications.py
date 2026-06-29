from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql import exists

from app.authz import get_current_user
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import (
    BlacklistedSender,
    ComplianceRecord,
    ComplianceRecordStatus,
    MaintenanceOrder,
    MaintenanceOrderStatus,
    ManagedProperty,
    MySpaceTodo,
    RentDueTracker,
    RentTrackStatus,
    ThreadTicket,
    TicketStatus,
    User,
)
from app.routers.user_auth import _get_role_page_access, _role_key


router = APIRouter(prefix="/notifications", tags=["notifications"])


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _email_due_at(ticket: ThreadTicket) -> datetime | None:
    values = [value for value in (ticket.sla_due_at, ticket.due_at) if value]
    return min(values) if values else None


@router.get("")
def notification_summary(
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    now = datetime.utcnow()
    items: list[dict] = []
    allowed_pages = set(_get_role_page_access(db).get(_role_key(user.role), ["portal"]))

    email_count = 0
    if "inbox" in allowed_pages:
        email_base = (
            db.query(ThreadTicket)
            .options(joinedload(ThreadTicket.assignee))
            .filter(ThreadTicket.mailbox == mailbox)
            .filter(ThreadTicket.is_not_replied == True)
            .filter(ThreadTicket.status.notin_([TicketStatus.RESPONDED, TicketStatus.NO_REPLY_NEEDED]))
            .filter(or_(ThreadTicket.sla_due_at < now, ThreadTicket.due_at < now))
            .filter(
                ~exists()
                .where(BlacklistedSender.mailbox == mailbox)
                .where(BlacklistedSender.email == func.lower(ThreadTicket.from_email))
            )
        )
        email_count = email_base.count()
        for ticket in (
            email_base.order_by(ThreadTicket.sla_due_at.asc().nullslast(), ThreadTicket.due_at.asc().nullslast())
            .limit(6)
            .all()
        ):
            due_at = _email_due_at(ticket)
            assignee = ticket.assignee_name or "Unassigned"
            target_tab = "in_progress" if ticket.status == TicketStatus.IN_PROGRESS else "awaiting_reply"
            items.append(
                {
                    "kind": "email",
                    "severity": "overdue",
                    "page": "inbox",
                    "tab": target_tab,
                    "thread_id": ticket.thread_id,
                    "title": ticket.subject or "Unreplied email",
                    "detail": f"{ticket.from_name or ticket.from_email or 'Unknown sender'} - {assignee}",
                    "due_at": _iso(due_at),
                }
            )

    rent_count = 0
    if "rent" in allowed_pages:
        unsettled_rent = [RentTrackStatus.DUE, RentTrackStatus.PARTIAL, RentTrackStatus.AWAITING_CLEARANCE]
        rent_base = (
            db.query(RentDueTracker)
            .filter(RentDueTracker.mailbox == mailbox)
            .filter(RentDueTracker.due_date.isnot(None))
            .filter(RentDueTracker.due_date < now)
            .filter(RentDueTracker.status.in_(unsettled_rent))
        )
        rent_count = rent_base.count()
        for row in rent_base.order_by(RentDueTracker.due_date.asc()).limit(4).all():
            items.append(
                {
                    "kind": "rent",
                    "severity": "overdue",
                    "page": "rent",
                    "title": row.property_address or "Overdue rent item",
                    "detail": f"{row.status.value.replace('_', ' ').title()} - {row.period_label or row.frequency}",
                    "due_at": _iso(row.due_date),
                }
            )

    compliance_count = 0
    if "compliance" in allowed_pages or "coverage" in allowed_pages:
        compliance_target_page = "compliance" if "compliance" in allowed_pages else "coverage"
        compliance_base = (
            db.query(ComplianceRecord)
            .join(ManagedProperty, ComplianceRecord.property_id == ManagedProperty.id)
            .filter(ComplianceRecord.mailbox == mailbox)
            .filter(ManagedProperty.mailbox == mailbox)
            .filter(ManagedProperty.is_active == True)
            .filter(ComplianceRecord.due_date.isnot(None))
            .filter(ComplianceRecord.due_date < now)
            .filter(ComplianceRecord.status.notin_([ComplianceRecordStatus.COMPLETED, ComplianceRecordStatus.WAIVED]))
        )
        compliance_count = compliance_base.count()
        for row in compliance_base.order_by(ComplianceRecord.due_date.asc()).limit(4).all():
            address = row.property.property_address if row.property else "Compliance record"
            items.append(
                {
                    "kind": "compliance",
                    "severity": "overdue",
                    "page": compliance_target_page,
                    "title": address,
                    "detail": f"{row.compliance_type.value.title()} check overdue",
                    "due_at": _iso(row.due_date),
                }
            )

    maintenance_count = 0
    if "maintenance" in allowed_pages:
        open_maintenance = [
            MaintenanceOrderStatus.NEW,
            MaintenanceOrderStatus.WAITING_OWNER_APPROVAL,
            MaintenanceOrderStatus.OWNER_APPROVED,
            MaintenanceOrderStatus.OWNER_DECLINED,
            MaintenanceOrderStatus.OWNER_ARRANGING,
            MaintenanceOrderStatus.QUOTE_REQUESTED,
            MaintenanceOrderStatus.QUOTE_RECEIVED,
            MaintenanceOrderStatus.TRADIE_ARRANGED,
            MaintenanceOrderStatus.TENANT_NOTIFIED,
        ]
        maintenance_base = (
            db.query(MaintenanceOrder)
            .filter(MaintenanceOrder.mailbox == mailbox)
            .filter(MaintenanceOrder.due_by.isnot(None))
            .filter(MaintenanceOrder.due_by < now)
            .filter(MaintenanceOrder.status.in_(open_maintenance))
        )
        maintenance_count = maintenance_base.count()
        for row in maintenance_base.order_by(MaintenanceOrder.due_by.asc()).limit(4).all():
            items.append(
                {
                    "kind": "maintenance",
                    "severity": "overdue",
                    "page": "maintenance",
                    "title": row.title or "Maintenance order",
                    "detail": f"{row.property_address} - {row.status.value.replace('_', ' ').title()}",
                    "due_at": _iso(row.due_by),
                }
            )
        tenant_new_base = (
            db.query(MaintenanceOrder)
            .filter(MaintenanceOrder.mailbox == mailbox)
            .filter(MaintenanceOrder.source == "tenant_portal")
            .filter(MaintenanceOrder.status == MaintenanceOrderStatus.NEW)
        )
        tenant_new_count = tenant_new_base.count()
        maintenance_count += tenant_new_count
        for row in tenant_new_base.order_by(MaintenanceOrder.created_at.desc()).limit(4).all():
            items.append(
                {
                    "kind": "maintenance",
                    "severity": "new",
                    "page": "maintenance",
                    "title": row.title or "Tenant maintenance request",
                    "detail": f"{row.property_address} - Tenant Portal",
                    "due_at": _iso(row.created_at),
                }
            )

    my_space_count = 0
    if "myspace" in allowed_pages:
        my_space_base = (
            db.query(MySpaceTodo)
            .filter(MySpaceTodo.user_id == user.id)
            .filter(MySpaceTodo.is_done == False)
            .filter(MySpaceTodo.due_at.isnot(None))
            .filter(MySpaceTodo.due_at < now)
        )
        my_space_count = my_space_base.count()
        for row in my_space_base.order_by(MySpaceTodo.due_at.asc()).limit(4).all():
            item_type = "Follow-up" if row.item_type == "follow_up" else "Task"
            items.append(
                {
                    "kind": "myspace",
                    "severity": "overdue",
                    "page": "myspace",
                    "title": row.title,
                    "detail": f"{item_type}{f' with {row.follow_up_with}' if row.follow_up_with else ''}",
                    "due_at": _iso(row.due_at),
                }
            )

    total = email_count + rent_count + compliance_count + maintenance_count + my_space_count
    items.sort(key=lambda item: item.get("due_at") or "")
    return {
        "total": total,
        "categories": {
            "email": email_count,
            "rent": rent_count,
            "compliance": compliance_count,
            "maintenance": maintenance_count,
            "myspace": my_space_count,
        },
        "items": items[:18],
        "generated_at": _iso(now),
    }
