from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from app.authz import get_current_user, has_page_access
from app.config import settings
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import (
    ActivityLog,
    ComplianceRecord,
    LeaseRenewalRecord,
    MaintenanceOrder,
    ManagedProperty,
    RentDueTracker,
    ThreadTicket,
    User,
)
from app.services.ai_client import openai_text_completion


router = APIRouter(prefix="/bindu", tags=["bindu"])
MAX_RESULTS = 12


class BinduAskIn(BaseModel):
    message: str = Field(min_length=2, max_length=1000)
    current_page: str | None = Field(default=None, max_length=64)


class BinduSource(BaseModel):
    kind: str
    title: str
    detail: str
    page: str
    record_id: str | int | None = None


class BinduAskOut(BaseModel):
    answer: str
    sources: list[BinduSource]
    searched_pages: list[str]
    ai_enabled: bool


def _value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(getattr(value, "value", value))


def _compact(*parts: Any) -> str:
    return " · ".join(_value(part).strip() for part in parts if _value(part).strip())


def _words(query: str) -> list[str]:
    ignored = {"about", "find", "from", "have", "show", "that", "the", "these", "what", "where", "which", "with", "please", "bindu"}
    return [word for word in re.findall(r"[a-z0-9@._'-]+", query.lower()) if len(word) > 2 and word not in ignored][:16]


def _score(source: dict[str, Any], words: list[str], current_page: str | None) -> int:
    haystack = f"{source['kind']} {source['page']} {source['title']} {source['detail']}".lower()
    score = sum(4 if word in source["title"].lower() else 2 for word in words if word in haystack)
    if current_page and source["page"] == current_page:
        score += 1
    return score


def _source(kind: str, title: Any, detail: str, page: str, record_id: Any = None) -> dict[str, Any]:
    return {"kind": kind, "title": _value(title) or kind, "detail": detail[:700], "page": page, "record_id": record_id}


def _collect_sources(db: Session, user: User, mailbox: str, query: str, current_page: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    sources: list[dict[str, Any]] = []
    searched: list[str] = []

    if has_page_access(user.role, "inbox", db):
        searched.append("inbox")
        rows = db.query(ThreadTicket).filter(ThreadTicket.mailbox == mailbox).order_by(ThreadTicket.last_message_at.desc()).limit(80).all()
        for row in rows:
            sources.append(_source("Email", row.subject or "No subject", _compact(row.from_name or row.from_email, row.status, row.category, row.priority, row.ai_summary or row.snippet, row.last_message_at), "inbox", row.thread_id))

    if has_page_access(user.role, "maintenance", db):
        searched.append("maintenance")
        rows = db.query(MaintenanceOrder).filter(MaintenanceOrder.mailbox == mailbox).order_by(MaintenanceOrder.updated_at.desc()).limit(80).all()
        for row in rows:
            sources.append(_source("Maintenance", f"{row.property_address}: {row.title}", _compact(row.status, row.priority, row.category, row.description, f"Assigned to {row.assignee.name}" if row.assignee else "Unassigned", row.due_by), "maintenance", row.id))

    if has_page_access(user.role, "properties", db):
        searched.append("properties")
        rows = db.query(ManagedProperty).filter(ManagedProperty.mailbox == mailbox).order_by(ManagedProperty.updated_at.desc()).limit(80).all()
        for row in rows:
            contacts = " ".join(filter(None, [row.owners_json, row.tenants_json]))
            sources.append(_source("Property", row.property_address, _compact(row.suburb, row.state_code, row.postcode, row.tenancy_status, row.property_type, row.key_number, contacts), "properties", row.id))

    if has_page_access(user.role, "rent", db):
        searched.append("rent")
        rows = db.query(RentDueTracker).filter(RentDueTracker.mailbox == mailbox).order_by(RentDueTracker.updated_at.desc()).limit(80).all()
        for row in rows:
            sources.append(_source("Rent", row.property_address, _compact(row.status, row.period_label, row.due_date, f"Partial ${row.partial_amount:.2f}" if row.partial_amount is not None else None, row.notes), "rent", row.id))

    if has_page_access(user.role, "lease_renewals", db):
        searched.append("lease_renewals")
        rows = db.query(LeaseRenewalRecord).options(selectinload(LeaseRenewalRecord.property)).filter(LeaseRenewalRecord.mailbox == mailbox).order_by(LeaseRenewalRecord.updated_at.desc()).limit(80).all()
        for row in rows:
            address = row.property.property_address if row.property else f"Property #{row.property_id}"
            sources.append(_source("Lease renewal", address, _compact(row.status, f"Current lease ends { _value(row.current_lease_end) }" if row.current_lease_end else None, f"Renewal due { _value(row.renewal_due_date) }" if row.renewal_due_date else None, f"Current rent ${row.current_rent:.2f}" if row.current_rent is not None else None, f"Proposed rent ${row.proposed_rent:.2f}" if row.proposed_rent is not None else None, row.notes), "lease_renewals", row.id))

    if has_page_access(user.role, "compliance", db):
        searched.append("compliance")
        rows = db.query(ComplianceRecord).options(selectinload(ComplianceRecord.property)).filter(ComplianceRecord.mailbox == mailbox).order_by(ComplianceRecord.updated_at.desc()).limit(80).all()
        for row in rows:
            address = row.property.property_address if row.property else f"Property #{row.property_id}"
            sources.append(_source("Compliance", f"{address}: {_value(row.compliance_type)}", _compact(row.status, row.due_date, row.provider_name, row.result_text, row.notes), "compliance", row.id))

    if has_page_access(user.role, "team", db):
        searched.append("team")
        for row in db.query(User).filter(User.is_active == True).order_by(User.name.asc()).limit(50).all():
            sources.append(_source("Staff", row.name, _compact(row.role, row.email, row.phone), "team", row.id))

    if has_page_access(user.role, "activity", db):
        searched.append("activity")
        rows = db.query(ActivityLog).filter((ActivityLog.mailbox == mailbox) | (ActivityLog.mailbox.is_(None))).order_by(ActivityLog.created_at.desc()).limit(60).all()
        for row in rows:
            sources.append(_source("Activity", row.entity_label or row.area, _compact(row.actor_name, row.action, row.area, row.entity_type, row.created_at), "activity", row.id))

    words = _words(query)
    ranked = sorted(sources, key=lambda item: (_score(item, words, current_page), str(item.get("record_id") or "")), reverse=True)
    if words:
        matches = [item for item in ranked if _score(item, words, current_page) > 0]
        if matches:
            ranked = matches
    return ranked[:MAX_RESULTS], searched


def _fallback_answer(sources: list[dict[str, Any]], searched: list[str]) -> str:
    if not sources:
        return "I couldn't find a matching record in the areas you can access. Try an address, person, status, or a more specific phrase."
    areas = ", ".join(page.replace("_", " ") for page in searched)
    return f"I found {len(sources)} relevant result{'s' if len(sources) != 1 else ''} across the areas you can access ({areas}). The closest matches are listed below."


def _grounded_answer(question: str, sources: list[dict[str, Any]], searched: list[str]) -> str:
    if not settings.OPENAI_API_KEY or not sources:
        return _fallback_answer(sources, searched)
    evidence = json.dumps(sources, ensure_ascii=False, default=str)
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        answer = openai_text_completion(
            client,
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are Bindu, a concise and friendly read-only assistant for a property management portal. Answer only from the supplied records. Never claim to change, send, assign, update, or delete anything. If evidence is incomplete, say so. Mention record titles naturally and keep the answer under 180 words."},
                {"role": "user", "content": f"Question: {question}\n\nAccessible records:\n{evidence}"},
            ],
            temperature=0.1,
            max_tokens=320,
        )
        return answer.strip() or _fallback_answer(sources, searched)
    except Exception:
        return _fallback_answer(sources, searched)


@router.post("/ask", response_model=BinduAskOut)
def ask_bindu(payload: BinduAskIn, user: User = Depends(get_current_user), mailbox: str = Depends(get_current_mailbox), db: Session = Depends(get_db)):
    question = re.sub(r"\s+", " ", payload.message).strip()
    sources, searched = _collect_sources(db, user, mailbox, question, payload.current_page)
    return BinduAskOut(answer=_grounded_answer(question, sources, searched), sources=[BinduSource(**item) for item in sources], searched_pages=searched, ai_enabled=bool(settings.OPENAI_API_KEY))
