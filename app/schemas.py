from datetime import datetime
from pydantic import BaseModel
from pydantic import ConfigDict
from app.models import TicketStatus, TicketCategory, UserRole


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: UserRole
    is_active: bool


class TicketNoteOut(BaseModel):
    id: int
    thread_id: str
    author_user_id: int
    author_name: str | None = None
    body: str
    created_at: datetime


class TicketAuditOut(BaseModel):
    id: int
    thread_id: str
    action: str
    actor_user_id: int | None
    actor_name: str | None = None
    detail: str | None
    created_at: datetime

class TicketOut(BaseModel):
    thread_id: str
    subject: str
    snippet: str
    from_name: str
    from_email: str
    last_message_at: datetime
    is_unread: bool
    is_not_replied: bool
    priority: int
    due_at: datetime | None
    category: str
    status: str

    # AI fields (optional)
    ai_category: str | None = None
    ai_urgency: int | None = None
    ai_confidence: float | None = None

    model_config = ConfigDict(from_attributes=True)

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
