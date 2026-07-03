from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.sql import exists

from app.authz import get_current_user
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import (
    BlacklistedSender,
    ComplianceProperty,
    ComplianceState,
    ComplianceRecord,
    ComplianceRecordStatus,
    LeaseRenewalRecord,
    LeaseRenewalStatus,
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
COMPLIANCE_DUE_SOON_DAYS = 30
MAINTENANCE_DUE_SOON_DAYS = 7
LEASE_RENEWAL_DUE_SOON_DAYS = 30
LEASE_RENEWAL_FINAL_STATUSES = {
    LeaseRenewalStatus.FULLY_SIGNED,
    LeaseRenewalStatus.PERIODIC_CONFIRMED,
    LeaseRenewalStatus.TENANT_VACATING,
    LeaseRenewalStatus.ADVERTISED,
    LeaseRenewalStatus.COMPLETED,
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _status_label(value) -> str:
    raw = value.value if hasattr(value, "value") else str(value or "")
    return raw.replace("_", " ").title()


def _ticket_tab(ticket: ThreadTicket) -> str:
    if ticket.status == TicketStatus.IN_PROGRESS:
        return "in_progress"
    if ticket.status == TicketStatus.RESPONDED:
        return "responded"
    if ticket.status == TicketStatus.NO_REPLY_NEEDED:
        return "no_reply_needed"
    return "awaiting_reply"


def _event_info_request(row: MaintenanceOrder) -> dict[str, object] | None:
    events = sorted(row.events or [], key=lambda item: item.created_at or datetime.min)
    latest_request = None
    for event in events:
        if event.event_type == "tenant_info_requested":
            latest_request = event
    if not latest_request:
        return None
    responses = [
        event
        for event in events
        if event.created_at
        and latest_request.created_at
        and event.created_at > latest_request.created_at
        and event.event_type in {"tenant_update", "tenant_media_uploaded"}
    ]
    latest_response = responses[-1] if responses else None
    return {
        "required": latest_response is None,
        "message": latest_request.detail,
        "requested_at": latest_request.created_at,
        "responded_at": latest_response.created_at if latest_response else None,
    }


def _add_item(
    items: list[dict],
    *,
    kind: str,
    severity: str,
    page: str,
    title: str,
    detail: str = "",
    action: str = "Review",
    due_at: datetime | None = None,
    created_at: datetime | None = None,
    **extra,
) -> None:
    items.append(
        {
            "kind": kind,
            "severity": severity,
            "page": page,
            "title": title,
            "detail": detail,
            "action": action,
            "due_at": _iso(due_at),
            "created_at": _iso(created_at),
            **extra,
        }
    )


def _sort_items(items: list[dict]) -> list[dict]:
    severity_rank = {
        "critical": 0,
        "overdue": 1,
        "action": 2,
        "assigned": 3,
        "new": 4,
        "soon": 5,
        "info": 6,
    }

    def key(item: dict):
        date_value = item.get("due_at") or item.get("created_at") or ""
        return (severity_rank.get(str(item.get("severity") or ""), 9), date_value)

    return sorted(items, key=key)


def _dedupe_items(items: list[dict]) -> list[dict]:
    severity_rank = {
        "critical": 0,
        "overdue": 1,
        "action": 2,
        "assigned": 3,
        "new": 4,
        "soon": 5,
        "info": 6,
    }
    best: dict[str, dict] = {}
    for item in items:
        identifier = item.get("thread_id") or item.get("order_id") or item.get("record_id")
        key_parts = [str(item.get("kind") or ""), str(item.get("page") or "")]
        if identifier:
            key_parts.append(str(identifier))
        else:
            key_parts.extend(
                [
                    str(item.get("title") or ""),
                    str(item.get("detail") or ""),
                    str(item.get("due_at") or ""),
                ]
            )
        key = ":".join(key_parts)
        existing = best.get(key)
        if not existing:
            best[key] = item
            continue
        current_rank = severity_rank.get(str(item.get("severity") or ""), 9)
        existing_rank = severity_rank.get(str(existing.get("severity") or ""), 9)
        if current_rank < existing_rank:
            best[key] = item
    return list(best.values())


@router.get("")
def notification_summary(
    limit: int = 80,
    mailbox: str = Depends(get_current_mailbox),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    now = datetime.utcnow()
    items: list[dict] = []
    allowed_pages = set(_get_role_page_access(db).get(_role_key(user.role), ["portal"]))

    email_count = 0
    if "inbox" in allowed_pages:
        assigned_base = (
            db.query(ThreadTicket)
            .options(joinedload(ThreadTicket.assignee))
            .filter(ThreadTicket.mailbox == mailbox)
            .filter(ThreadTicket.assignee_user_id == user.id)
            .filter(ThreadTicket.status.notin_([TicketStatus.RESPONDED, TicketStatus.NO_REPLY_NEEDED]))
            .filter(
                ~exists()
                .where(BlacklistedSender.mailbox == mailbox)
                .where(BlacklistedSender.email == func.lower(ThreadTicket.from_email))
            )
        )
        email_count = assigned_base.count()
        for ticket in assigned_base.order_by(ThreadTicket.updated_at.desc()).limit(20).all():
            _add_item(
                items,
                kind="email",
                severity="assigned",
                page="inbox",
                tab=_ticket_tab(ticket),
                thread_id=ticket.thread_id,
                title=ticket.subject or "Assigned email ticket",
                detail=f"{ticket.from_name or ticket.from_email or 'Unknown sender'} - {_status_label(ticket.status)}",
                action="Open ticket",
                created_at=ticket.updated_at or ticket.last_message_at,
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
        for row in rent_base.order_by(RentDueTracker.due_date.asc()).limit(8).all():
            _add_item(
                items,
                kind="rent",
                severity="overdue",
                page="rent",
                title=row.property_address or "Overdue rent item",
                detail=f"{_status_label(row.status)} - {row.period_label or row.frequency}",
                action="Review rent",
                due_at=row.due_date,
            )

    lease_count = 0
    if "lease_renewals" in allowed_pages:
        lease_ids: set[int] = set()
        active_lease_base = (
            db.query(LeaseRenewalRecord)
            .join(ManagedProperty, LeaseRenewalRecord.property_id == ManagedProperty.id)
            .filter(LeaseRenewalRecord.mailbox == mailbox)
            .filter(ManagedProperty.mailbox == mailbox)
            .filter(ManagedProperty.is_active == True)
            .filter(LeaseRenewalRecord.status.notin_(list(LEASE_RENEWAL_FINAL_STATUSES)))
        )
        overdue_base = active_lease_base.filter(
            or_(
                LeaseRenewalRecord.renewal_due_date < now,
                LeaseRenewalRecord.follow_up_date < now,
            )
        )
        soon_base = (
            active_lease_base.filter(LeaseRenewalRecord.renewal_due_date.isnot(None))
            .filter(LeaseRenewalRecord.renewal_due_date >= now)
            .filter(LeaseRenewalRecord.renewal_due_date <= now + timedelta(days=LEASE_RENEWAL_DUE_SOON_DAYS))
        )
        stale_base = (
            active_lease_base.filter(LeaseRenewalRecord.lease_sent_date.isnot(None))
            .filter(LeaseRenewalRecord.lease_sent_date <= now - timedelta(days=14))
            .filter(or_(LeaseRenewalRecord.owner_signed_date.is_(None), LeaseRenewalRecord.tenant_signed_date.is_(None)))
        )
        lease_ids.update(row_id for (row_id,) in overdue_base.with_entities(LeaseRenewalRecord.id).all())
        lease_ids.update(row_id for (row_id,) in soon_base.with_entities(LeaseRenewalRecord.id).all())
        lease_ids.update(row_id for (row_id,) in stale_base.with_entities(LeaseRenewalRecord.id).all())
        for row in overdue_base.order_by(LeaseRenewalRecord.renewal_due_date.asc().nullslast()).limit(8).all():
            address = row.property.property_address if row.property else "Lease renewal"
            due_at = row.follow_up_date if row.follow_up_date and row.follow_up_date < now else row.renewal_due_date
            _add_item(
                items,
                kind="lease",
                severity="overdue",
                page="lease_renewals",
                record_id=row.id,
                title=address,
                detail=f"Lease renewal needs follow-up - {_status_label(row.status)}",
                action="Open renewal",
                due_at=due_at,
            )
        for row in stale_base.order_by(LeaseRenewalRecord.lease_sent_date.asc()).limit(6).all():
            address = row.property.property_address if row.property else "Lease renewal"
            _add_item(
                items,
                kind="lease",
                severity="action",
                page="lease_renewals",
                record_id=row.id,
                title=address,
                detail="Lease sent more than 14 days ago and signatures are incomplete",
                action="Follow up",
                due_at=row.follow_up_date,
                created_at=row.lease_sent_date,
            )
        for row in soon_base.order_by(LeaseRenewalRecord.renewal_due_date.asc()).limit(6).all():
            address = row.property.property_address if row.property else "Lease renewal"
            _add_item(
                items,
                kind="lease",
                severity="soon",
                page="lease_renewals",
                record_id=row.id,
                title=address,
                detail=f"Renewal due within {LEASE_RENEWAL_DUE_SOON_DAYS} days",
                action="Plan renewal",
                due_at=row.renewal_due_date,
            )
        lease_count = len(lease_ids)

    compliance_count = 0
    if "compliance" in allowed_pages or "coverage" in allowed_pages:
        compliance_ids: set[tuple[str, int]] = set()
        compliance_target_page = "compliance" if "compliance" in allowed_pages else "coverage"
        compliance_issue_base = (
            db.query(ComplianceRecord)
            .join(ManagedProperty, ComplianceRecord.property_id == ManagedProperty.id)
            .filter(ComplianceRecord.mailbox == mailbox)
            .filter(ManagedProperty.mailbox == mailbox)
            .filter(ManagedProperty.is_active == True)
            .filter(ComplianceRecord.status.notin_([ComplianceRecordStatus.COMPLETED, ComplianceRecordStatus.WAIVED]))
        )
        overdue_base = compliance_issue_base.filter(ComplianceRecord.due_date.isnot(None)).filter(ComplianceRecord.due_date < now)
        due_soon_base = (
            compliance_issue_base.filter(ComplianceRecord.due_date.isnot(None))
            .filter(ComplianceRecord.due_date >= now)
            .filter(ComplianceRecord.due_date <= now + timedelta(days=COMPLIANCE_DUE_SOON_DAYS))
        )
        action_base = compliance_issue_base.filter(ComplianceRecord.status == ComplianceRecordStatus.ACTION_REQUIRED)
        compliance_ids.update(("record", row_id) for (row_id,) in overdue_base.with_entities(ComplianceRecord.id).all())
        compliance_ids.update(("record", row_id) for (row_id,) in due_soon_base.with_entities(ComplianceRecord.id).all())
        compliance_ids.update(("record", row_id) for (row_id,) in action_base.with_entities(ComplianceRecord.id).all())
        for row in overdue_base.order_by(ComplianceRecord.due_date.asc()).limit(8).all():
            address = row.property.property_address if row.property else "Compliance record"
            _add_item(
                items,
                kind="compliance",
                severity="overdue",
                page=compliance_target_page,
                title=address,
                detail=f"{_status_label(row.compliance_type)} check overdue",
                action="Open compliance",
                due_at=row.due_date,
            )
        for row in action_base.order_by(ComplianceRecord.updated_at.desc()).limit(6).all():
            address = row.property.property_address if row.property else "Compliance record"
            _add_item(
                items,
                kind="compliance",
                severity="action",
                page=compliance_target_page,
                title=address,
                detail=f"{_status_label(row.compliance_type)} requires action",
                action="Review action",
                created_at=row.updated_at,
            )
        for row in due_soon_base.order_by(ComplianceRecord.due_date.asc()).limit(6).all():
            address = row.property.property_address if row.property else "Compliance record"
            _add_item(
                items,
                kind="compliance",
                severity="soon",
                page=compliance_target_page,
                title=address,
                detail=f"{_status_label(row.compliance_type)} due soon",
                action="Plan check",
                due_at=row.due_date,
            )

        if "coverage" in allowed_pages:
            coverage_base = (
                db.query(ComplianceProperty)
                .filter(ComplianceProperty.mailbox == mailbox)
                .filter(ComplianceProperty.overall_state.in_([ComplianceState.OVERDUE, ComplianceState.ACTION_REQUIRED]))
            )
            compliance_ids.update(("coverage", row_id) for (row_id,) in coverage_base.with_entities(ComplianceProperty.id).all())
            for row in coverage_base.order_by(ComplianceProperty.updated_at.desc()).limit(6).all():
                _add_item(
                    items,
                    kind="compliance",
                    severity="action" if row.overall_state == ComplianceState.ACTION_REQUIRED else "overdue",
                    page="coverage",
                    title=row.property_address or "Compliance report item",
                    detail=row.overall_reason or f"Compliance report: {_status_label(row.overall_state)}",
                    action="Open report",
                    created_at=row.updated_at,
                )
        compliance_count = len(compliance_ids)

    maintenance_count = 0
    if "maintenance" in allowed_pages:
        maintenance_ids: set[int] = set()
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
        assigned_maintenance = (
            db.query(MaintenanceOrder)
            .options(selectinload(MaintenanceOrder.assignee), selectinload(MaintenanceOrder.events))
            .filter(MaintenanceOrder.mailbox == mailbox)
            .filter(MaintenanceOrder.assignee_user_id == user.id)
            .filter(MaintenanceOrder.status.in_(open_maintenance))
        )
        maintenance_ids.update(row_id for (row_id,) in assigned_maintenance.with_entities(MaintenanceOrder.id).all())
        for row in assigned_maintenance.order_by(MaintenanceOrder.updated_at.desc()).limit(10).all():
            info_request = _event_info_request(row)
            severity = "action" if info_request and info_request.get("required") else "assigned"
            _add_item(
                items,
                kind="maintenance",
                severity=severity,
                page="maintenance",
                view="active",
                order_id=row.id,
                title=row.title or "Assigned maintenance order",
                detail=f"{row.property_address} - {_status_label(row.status)}",
                action="Open job",
                due_at=row.due_by,
                created_at=row.updated_at,
            )

        tenant_new_base = (
            db.query(MaintenanceOrder)
            .filter(MaintenanceOrder.mailbox == mailbox)
            .filter(MaintenanceOrder.source == "tenant_portal")
            .filter(MaintenanceOrder.status == MaintenanceOrderStatus.NEW)
        )
        maintenance_ids.update(row_id for (row_id,) in tenant_new_base.with_entities(MaintenanceOrder.id).all())
        for row in tenant_new_base.order_by(MaintenanceOrder.created_at.desc()).limit(8).all():
            _add_item(
                items,
                kind="maintenance",
                severity="new",
                page="maintenance",
                view="active",
                order_id=row.id,
                title=row.title or "Tenant maintenance request",
                detail=f"{row.property_address} - Tenant Portal",
                action="Review request",
                created_at=row.created_at,
            )

        due_base = (
            db.query(MaintenanceOrder)
            .filter(MaintenanceOrder.mailbox == mailbox)
            .filter(MaintenanceOrder.due_by.isnot(None))
            .filter(MaintenanceOrder.status.in_(open_maintenance))
            .filter(or_(MaintenanceOrder.assignee_user_id == user.id, MaintenanceOrder.assignee_user_id.is_(None)))
        )
        overdue_base = due_base.filter(MaintenanceOrder.due_by < now)
        soon_base = due_base.filter(MaintenanceOrder.due_by >= now).filter(
            MaintenanceOrder.due_by <= now + timedelta(days=MAINTENANCE_DUE_SOON_DAYS)
        )
        maintenance_ids.update(row_id for (row_id,) in overdue_base.with_entities(MaintenanceOrder.id).all())
        maintenance_ids.update(row_id for (row_id,) in soon_base.with_entities(MaintenanceOrder.id).all())
        for row in overdue_base.order_by(MaintenanceOrder.due_by.asc()).limit(8).all():
            _add_item(
                items,
                kind="maintenance",
                severity="overdue",
                page="maintenance",
                view="active",
                order_id=row.id,
                title=row.title or "Maintenance follow-up overdue",
                detail=f"{row.property_address} - {_status_label(row.status)}",
                action="Follow up",
                due_at=row.due_by,
            )
        for row in soon_base.order_by(MaintenanceOrder.due_by.asc()).limit(6).all():
            _add_item(
                items,
                kind="maintenance",
                severity="soon",
                page="maintenance",
                view="active",
                order_id=row.id,
                title=row.title or "Maintenance follow-up due soon",
                detail=f"{row.property_address} - {_status_label(row.status)}",
                action="Plan follow-up",
                due_at=row.due_by,
            )

        info_rows = (
            db.query(MaintenanceOrder)
            .options(selectinload(MaintenanceOrder.events))
            .filter(MaintenanceOrder.mailbox == mailbox)
            .filter(MaintenanceOrder.status.in_(open_maintenance))
            .order_by(MaintenanceOrder.updated_at.desc())
            .limit(300)
            .all()
        )
        unresolved_info = [row for row in info_rows if (_event_info_request(row) or {}).get("required")]
        maintenance_ids.update(row.id for row in unresolved_info)
        for row in unresolved_info[:8]:
            info_request = _event_info_request(row) or {}
            _add_item(
                items,
                kind="maintenance",
                severity="action",
                page="maintenance",
                view="active",
                order_id=row.id,
                title=row.title or "Tenant information required",
                detail=info_request.get("message") or f"{row.property_address} - waiting for tenant update",
                action="Review request",
                created_at=info_request.get("requested_at") if isinstance(info_request.get("requested_at"), datetime) else row.updated_at,
            )
        maintenance_count = len(maintenance_ids)

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
        for row in my_space_base.order_by(MySpaceTodo.due_at.asc()).limit(8).all():
            item_type = "Follow-up" if row.item_type == "follow_up" else "Task"
            _add_item(
                items,
                kind="myspace",
                severity="overdue",
                page="myspace",
                title=row.title,
                detail=f"{item_type}{f' with {row.follow_up_with}' if row.follow_up_with else ''}",
                action="Open My Space",
                due_at=row.due_at,
            )

    total = email_count + rent_count + lease_count + compliance_count + maintenance_count + my_space_count
    sorted_items = _sort_items(_dedupe_items(items))
    item_limit = max(10, min(int(limit or 80), 200))
    return {
        "total": total,
        "categories": {
            "email": email_count,
            "rent": rent_count,
            "lease": lease_count,
            "compliance": compliance_count,
            "maintenance": maintenance_count,
            "myspace": my_space_count,
        },
        "items": sorted_items[:item_limit],
        "generated_at": _iso(now),
        "label": "Notification Center",
    }
