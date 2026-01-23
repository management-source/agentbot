from datetime import datetime
from pydantic import BaseModel
from pydantic import ConfigDict
from app.models import TicketStatus, TicketCategory, UserRole
from typing import Optional, Any
from pydantic import field_validator

PRIORITY_MAP = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "urgent": 4,
    "critical": 5,
}

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

    from_name: str = ""          # allow missing
    from_email: str

    last_message_at: datetime
    is_unread: bool
    is_not_replied: bool

    priority: int = 2            # default medium
    due_at: Optional[datetime] = None
    category: str
    status: str

    # AI fields (optional)
    ai_category: Optional[str] = None
    ai_urgency: Optional[int] = None
    ai_confidence: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("from_name", mode="before")
    @classmethod
    def _from_name_none_to_empty(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)

    @field_validator("priority", mode="before")
    @classmethod
    def _priority_to_int(cls, v: Any) -> int:
        # already int-like
        if v is None:
            return 2
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s.isdigit():
                return int(s)
            if s in PRIORITY_MAP:
                return PRIORITY_MAP[s]
        # safe fallback
        return 2

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
