from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import unquote

from sqlalchemy.orm import Session

from app.models import ActivityLog, User


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SKIP_PATH_PREFIXES = (
    "/static/",
    "/metrics",
    "/health",
    "/activity-log",
)
SKIP_EXACT_PATHS = {
    "/user-auth/login",
    "/user-auth/forgot-password",
    "/user-auth/reset-password",
}

AREA_LABELS = {
    "tickets": "Email Manager",
    "threads": "Email Manager",
    "sync": "Email Sync",
    "blacklist": "Blacklist",
    "maintenance": "Maintenance",
    "tenant": "Tenant Portal",
    "rent-tracker": "Rent Tracker",
    "lease-renewals": "Lease Renewals",
    "properties": "Properties",
    "compliance": "Compliance",
    "my-space": "My Space",
    "settings": "Settings",
    "user-auth": "System Access",
    "assets": "Assets",
    "tasks": "Tasks",
}

ENTITY_LABELS = {
    "tickets": "Email Ticket",
    "threads": "Email Thread",
    "maintenance": "Maintenance",
    "rent-tracker": "Rent Item",
    "lease-renewals": "Lease Renewal",
    "properties": "Property",
    "compliance": "Compliance",
    "my-space": "My Space",
    "settings": "Setting",
    "blacklist": "Blacklist Entry",
    "user-auth": "User Account",
    "tenant": "Tenant Portal",
}

ACTION_BY_METHOD = {
    "POST": "Created",
    "PUT": "Updated",
    "PATCH": "Updated",
    "DELETE": "Deleted",
}


def should_record_request(method: str, path: str) -> bool:
    normalized_path = path or ""
    if method.upper() not in MUTATING_METHODS:
        return False
    if normalized_path in SKIP_EXACT_PATHS:
        return False
    return not any(normalized_path.startswith(prefix) for prefix in SKIP_PATH_PREFIXES)


def _role_value(user: User | None) -> str | None:
    if not user:
        return None
    return user.role.value if hasattr(user.role, "value") else str(user.role)


def _safe_detail(detail: dict[str, Any] | None) -> str | None:
    if not detail:
        return None
    return json.dumps(detail, ensure_ascii=False, default=str)


def _segments(path: str) -> list[str]:
    return [unquote(part) for part in str(path or "").strip("/").split("/") if part]


def _first_identifier(parts: list[str]) -> str | None:
    for part in parts[1:]:
        if part.lower() in {
            "admin",
            "api",
            "records",
            "orders",
            "items",
            "todos",
            "quick-links",
            "snippets",
            "staff-guides",
            "users",
            "tradies",
            "registrations",
        }:
            continue
        if part.lower() in {"status", "assignee", "attachments", "notes", "avatar", "password", "view"}:
            continue
        return part[:180]
    return None


def infer_activity(method: str, path: str) -> dict[str, str | None]:
    parts = _segments(path)
    root = parts[0] if parts else "portal"
    tail = parts[-1].lower() if parts else ""
    area = AREA_LABELS.get(root, root.replace("-", " ").title())
    entity_type = ENTITY_LABELS.get(root, area)
    entity_id = _first_identifier(parts)
    action = ACTION_BY_METHOD.get(method.upper(), "Changed")

    if root == "tickets":
        entity_type = "Email Ticket"
        if tail == "status":
            action = "Updated ticket status"
        elif tail == "assignee":
            action = "Assigned ticket"
        elif tail == "send-reply":
            action = "Sent email reply"
        elif tail == "draft-ai-reply":
            action = "Generated AI draft"
        elif tail == "draft-reply":
            action = "Generated reply draft"
        elif "admin" in parts and "flush" in parts:
            action = "Flushed email tickets"
            entity_type = "Email Manager"
    elif root == "maintenance":
        entity_type = "Maintenance Order" if "orders" in parts else "Tradie"
        if tail == "status":
            action = "Updated maintenance status"
        elif tail == "request-info":
            action = "Requested tenant information"
        elif tail == "attachments":
            action = "Uploaded maintenance attachment"
        elif tail == "notes":
            action = "Added maintenance note"
        elif "tradies" in parts:
            entity_type = "Tradie"
    elif root == "tenant" and "admin" in parts:
        entity_type = "Tenant Account"
        if method.upper() == "DELETE":
            action = "Deleted tenant account"
        else:
            action = "Updated tenant registration"
    elif root == "properties":
        entity_type = "Property"
        if tail == "import-xlsx":
            action = "Imported properties"
        elif tail == "flush":
            action = "Flushed properties"
    elif root == "compliance":
        entity_type = "Compliance Record"
        if "records" in parts:
            action = f"{action} compliance record"
    elif root == "rent-tracker":
        entity_type = "Rent Tracker Item"
        if tail == "import-xlsx":
            action = "Imported rent tracker"
    elif root == "lease-renewals":
        entity_type = "Lease Renewal"
        if "records" in parts:
            action = f"{action} lease renewal"
        if tail == "status":
            action = "Updated lease renewal status"
        elif tail == "notes":
            action = "Added lease renewal note"
    elif root == "my-space":
        entity_type = "My Space"
        if "staff-guides" in parts and method.upper() == "POST":
            action = "Uploaded staff guide"
        elif tail == "note":
            action = "Updated private note"
    elif root == "user-auth":
        entity_type = "User Account"
        if tail == "logout":
            action = "Logged out"
        elif tail == "avatar":
            action = "Updated staff avatar"
        elif tail == "password":
            action = "Changed password"
        elif "role-page-access" in parts:
            action = "Updated access control"
    elif root == "blacklist":
        entity_type = "Blacklist Entry"
        action = "Updated blacklist" if method.upper() != "DELETE" else "Deleted blacklist entry"
    elif root == "settings":
        entity_type = "Setting"
        action = "Updated settings"
    elif root == "sync":
        entity_type = "Email Sync"
        action = "Ran email sync"

    return {
        "action": action,
        "area": area,
        "entity_type": entity_type,
        "entity_id": entity_id,
    }


def record_activity(
    db: Session,
    *,
    actor: User | None,
    action: str,
    area: str,
    mailbox: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    entity_label: str | None = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    detail: dict[str, Any] | None = None,
    commit: bool = False,
) -> ActivityLog:
    row = ActivityLog(
        mailbox=(mailbox or "").strip().lower() or None,
        actor_user_id=actor.id if actor else None,
        actor_name=actor.name if actor else None,
        actor_email=actor.email if actor else None,
        actor_role=_role_value(actor),
        action=(action or "Changed")[:255],
        area=(area or "Portal")[:255],
        entity_type=(entity_type or "")[:255] or None,
        entity_id=(entity_id or "")[:255] or None,
        entity_label=(entity_label or "")[:255] or None,
        method=(method or "")[:20] or None,
        path=(path or "")[:500] or None,
        status_code=status_code,
        request_id=(request_id or "")[:120] or None,
        ip_address=(ip_address or "")[:120] or None,
        user_agent=(user_agent or "")[:1000] or None,
        detail=_safe_detail(detail),
        created_at=datetime.utcnow(),
    )
    db.add(row)
    if commit:
        db.commit()
    return row
