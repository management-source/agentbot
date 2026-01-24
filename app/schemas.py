from datetime import datetime
from pydantic import BaseModel
from pydantic import ConfigDict
from app.models import TicketStatus, TicketCategory, UserRole
from typing import Optional


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    name: str
    role: UserRole
    is_active: bool


class TicketNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    thread_id: str
    author_user_id: int
    author_name: str | None = None
    body: str
    created_at: datetime


class TicketAuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    thread_id: str
    action: str
    actor_user_id: int | None
    actor_name: str | None = None
    detail: str | None
    created_at: datetime

class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    thread_id: str
    subject: str | None
    snippet: str | None
    from_name: str | None
    from_email: str | None
    last_message_at: datetime | None
    is_unread: bool
    is_not_replied: bool
    priority: str
    due_at: datetime | None
    category: TicketCategory
    owner_user_id: int | None = None
    assignee_user_id: int | None = None
    sla_due_at: datetime | None = None
    escalated_at: datetime | None = None
    escalation_level: int = 0
    status: TicketStatus

    # --- AI metadata (optional) ---
    ai_category: str | None = None
    ai_urgency: int | None = None
    ai_confidence: int | None = None  # 0..100
    ai_reasons: str | None = None
    ai_summary: str | None = None

class TicketListOut(BaseModel):
    items: list[TicketOut]
    counts: dict[str, int]


class AiAnalyzeOut(BaseModel):
    ai_category: str
    ticket_category: TicketCategory
    ai_urgency: int
    ai_confidence: int  # 0..100
    ai_reasons: list[str] = []
    ai_summary: str = ""


class DraftAiIn(BaseModel):
    tone: str | None = None
    additional_info: str | None = None

class DraftAiReplyOut(BaseModel):
    subject: str
    body: str
    meta: dict[str, object] | None = None

class StatusUpdateIn(BaseModel):
    status: TicketStatus

class DraftAckOut(BaseModel):
    subject: str
    body: str

class SendAckIn(BaseModel):
    subject: str
    body: str
    mark_as_responded: bool = True
