from datetime import datetime
from enum import Enum

from sqlalchemy import (
    String,
    DateTime,
    Boolean,
    Integer,
    Enum as SAEnum,
    Text,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, declarative_base, relationship

Base = declarative_base()


class TicketStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    RESPONDED = "RESPONDED"
    NO_REPLY_NEEDED = "NO_REPLY_NEEDED"


class UserRole(str, Enum):
    ADMIN = "ADMIN"
    PM = "PM"  # Property Management
    LEASING = "LEASING"
    SALES = "SALES"
    ACCOUNTS = "ACCOUNTS"
    READONLY = "READONLY"


class TicketCategory(str, Enum):
    MAINTENANCE = "MAINTENANCE"
    RENT_ARREARS = "RENT_ARREARS"
    LEASING = "LEASING"
    COMPLIANCE = "COMPLIANCE"
    SALES = "SALES"
    GENERAL = "GENERAL"


class AuditAction(str, Enum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    ASSIGNED = "ASSIGNED"
    CATEGORY_SET = "CATEGORY_SET"
    NOTE_ADDED = "NOTE_ADDED"
    ESCALATED = "ESCALATED"
    REPLIED = "REPLIED"


class BlacklistedSender(Base):
    __tablename__ = "blacklisted_senders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OAuthToken(Base):
    """
    MVP: single-row token store.
    """
    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String, default="google", index=True)

    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    token_uri: Mapped[str] = mapped_column(String, default="https://oauth2.googleapis.com/token")
    client_id: Mapped[str] = mapped_column(String)
    client_secret: Mapped[str] = mapped_column(String)
    scopes: Mapped[str] = mapped_column(Text)  # comma-separated

    expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppState(Base):
    """Small key/value store for application state.

    Used for incremental sync watermarks (e.g., Gmail historyId).
    """

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.PM, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    owned_tickets = relationship("ThreadTicket", foreign_keys="ThreadTicket.owner_user_id", back_populates="owner")
    assigned_tickets = relationship(
        "ThreadTicket", foreign_keys="ThreadTicket.assignee_user_id", back_populates="assignee"
    )


class ThreadTicketNote(Base):
    __tablename__ = "thread_ticket_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String, index=True)
    author_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)

    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    author = relationship("User")


class ThreadTicketAudit(Base):
    __tablename__ = "thread_ticket_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[AuditAction] = mapped_column(SAEnum(AuditAction), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # JSON-like payload stored as text for simplicity
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    actor = relationship("User")


class ThreadTicket(Base):
    __tablename__ = "thread_tickets"

    thread_id: Mapped[str] = mapped_column(String, primary_key=True)
    last_message_id: Mapped[str | None] = mapped_column(String, nullable=True)

    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)

    from_name: Mapped[str | None] = mapped_column(String, nullable=True)
    from_email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # Used heavily for ordering; index helps a lot.
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_from_me: Mapped[bool] = mapped_column(Boolean, default=False)

    is_unread: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_not_replied: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    priority: Mapped[str] = mapped_column(String, default="medium")  # low/medium/high
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    # Store enum values; index for fast tab filtering
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus),
        default=TicketStatus.PENDING,
        index=True,
    )

    category: Mapped[TicketCategory] = mapped_column(
        SAEnum(TicketCategory),
        default=TicketCategory.GENERAL,
        index=True,
    )

    # Ownership & assignment
    owner_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    assignee_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # SLA support
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)

    ack_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # --- AI metadata (triage + drafts) ---
    # Populated on-demand (e.g., when listing tickets or requesting a draft).
    ai_category: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    ai_urgency: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Stored as integer percent (0..100) for compatibility across DBs.
    ai_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_source_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    ai_last_scored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    ai_draft_subject: Mapped[str | None] = mapped_column(String, nullable=True)
    ai_draft_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_draft_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    owner = relationship("User", foreign_keys=[owner_user_id], back_populates="owned_tickets")
    assignee = relationship("User", foreign_keys=[assignee_user_id], back_populates="assigned_tickets")
