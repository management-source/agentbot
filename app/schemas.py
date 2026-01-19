from datetime import datetime
from pydantic import BaseModel
from app.models import TicketStatus

class TicketOut(BaseModel):
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
    status: TicketStatus

class TicketListOut(BaseModel):
    items: list[TicketOut]
    counts: dict[str, int]

class StatusUpdateIn(BaseModel):
    status: TicketStatus

class DraftAckOut(BaseModel):
    subject: str
    body: str

class SendAckIn(BaseModel):
    subject: str
    body: str
    mark_as_responded: bool = True
