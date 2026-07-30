from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    String,
    Date,
    DateTime,
    Boolean,
    Integer,
    Float,
    LargeBinary,
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


class RentTrackStatus(str, Enum):
    DUE = "DUE"
    PAID = "PAID"
    PARTIAL = "PARTIAL"
    VACANT = "VACANT"
    AWAITING_CLEARANCE = "AWAITING_CLEARANCE"


class ComplianceState(str, Enum):
    CURRENT = "CURRENT"
    DUE_SOON = "DUE_SOON"
    OVERDUE = "OVERDUE"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    UNKNOWN = "UNKNOWN"


class ComplianceType(str, Enum):
    GAS = "GAS"
    SMOKE = "SMOKE"
    ELECTRICAL = "ELECTRICAL"
    MRS = "MRS"
    POOL = "POOL"
    POWERBAND = "POWERBAND"
    DISCLOSURE = "DISCLOSURE"
    OTHER = "OTHER"


class ComplianceRecordStatus(str, Enum):
    OPEN = "OPEN"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    COMPLETED = "COMPLETED"
    WAIVED = "WAIVED"


class MaintenanceOrderStatus(str, Enum):
    NEW = "NEW"
    WAITING_OWNER_APPROVAL = "WAITING_OWNER_APPROVAL"
    OWNER_APPROVED = "OWNER_APPROVED"
    OWNER_DECLINED = "OWNER_DECLINED"
    OWNER_ARRANGING = "OWNER_ARRANGING"
    QUOTE_REQUESTED = "QUOTE_REQUESTED"
    QUOTE_RECEIVED = "QUOTE_RECEIVED"
    TRADIE_ARRANGED = "TRADIE_ARRANGED"
    TENANT_NOTIFIED = "TENANT_NOTIFIED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class LeaseRenewalStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    PREPARING_RENEWAL = "PREPARING_RENEWAL"
    SENT_TO_OWNER = "SENT_TO_OWNER"
    OWNER_SIGNED = "OWNER_SIGNED"
    SENT_TO_TENANT = "SENT_TO_TENANT"
    TENANT_SIGNED = "TENANT_SIGNED"
    PARTIALLY_SIGNED = "PARTIALLY_SIGNED"
    FULLY_SIGNED = "FULLY_SIGNED"
    PERIODIC_CONFIRMED = "PERIODIC_CONFIRMED"
    TENANT_VACATING = "TENANT_VACATING"
    ADVERTISED = "ADVERTISED"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"


class InspectionPlanStatus(str, Enum):
    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


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
    mailbox: Mapped[str] = mapped_column(String, index=True)
    email: Mapped[str] = mapped_column(String, index=True)
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
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    owned_tickets = relationship("ThreadTicket", foreign_keys="ThreadTicket.owner_user_id", back_populates="owner")
    assigned_tickets = relationship(
        "ThreadTicket", foreign_keys="ThreadTicket.assignee_user_id", back_populates="assignee"
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    requested_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")


class TenantAccount(Base):
    __tablename__ = "tenant_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    preferred_contact_method: Mapped[str | None] = mapped_column(String, nullable=True)
    password_hash: Mapped[str] = mapped_column(String)
    property_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("managed_properties.id"), nullable=True, index=True)
    property_address: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    suburb: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    state_code: Mapped[str | None] = mapped_column(String, nullable=True)
    postcode: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    property = relationship("ManagedProperty")


class TenantPasswordResetToken(Base):
    __tablename__ = "tenant_password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenant_accounts.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    requested_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    tenant = relationship("TenantAccount")


class ThreadTicketNote(Base):
    __tablename__ = "thread_ticket_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    thread_id: Mapped[str] = mapped_column(String, index=True)
    author_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)

    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    author = relationship("User")


class ThreadTicketAudit(Base):
    __tablename__ = "thread_ticket_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    thread_id: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[AuditAction] = mapped_column(SAEnum(AuditAction), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # JSON-like payload stored as text for simplicity
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    actor = relationship("User")


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    actor_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    actor_email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    actor_role: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    action: Mapped[str] = mapped_column(String, index=True)
    area: Mapped[str] = mapped_column(String, index=True)
    entity_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    entity_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    entity_label: Mapped[str | None] = mapped_column(String, nullable=True)

    method: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    path: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    actor = relationship("User")


class MySpaceTodo(Base):
    __tablename__ = "my_space_todos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String, default="normal", index=True)
    bucket: Mapped[str] = mapped_column(String, default="today", index=True)
    item_type: Mapped[str] = mapped_column(String, default="task", index=True)
    follow_up_with: Mapped[str | None] = mapped_column(String, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    user = relationship("User")


class MySpaceNote(Base):
    __tablename__ = "my_space_notes"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    body: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")


class MySpaceQuickLink(Base):
    __tablename__ = "my_space_quick_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")


class MySpaceSnippet(Base):
    __tablename__ = "my_space_snippets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")


class StaffGuide(Base):
    __tablename__ = "staff_guides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    filename: Mapped[str] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String, default="application/pdf")
    content_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    uploaded_by = relationship("User")


class BinduConversation(Base):
    __tablename__ = "bindu_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String, default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")
    messages = relationship("BinduMessage", back_populates="conversation", cascade="all, delete-orphan")


class BinduMessage(Base):
    __tablename__ = "bindu_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(Integer, ForeignKey("bindu_conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String, index=True)
    content: Mapped[str] = mapped_column(Text)
    sources_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    conversation = relationship("BinduConversation", back_populates="messages")


class ThreadTicket(Base):
    __tablename__ = "thread_tickets"

    # Internal unique ticket identifier. We namespace by mailbox to prevent cross-mailbox collisions.
    thread_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Original Gmail thread id (without mailbox namespace)
    gmail_thread_id: Mapped[str] = mapped_column(String, index=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)

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

    @property
    def assignee_name(self) -> str | None:
        return self.assignee.name if self.assignee else None

    @property
    def assignee_email(self) -> str | None:
        return self.assignee.email if self.assignee else None

    @property
    def assignee_avatar_url(self) -> str | None:
        return self.assignee.avatar_url if self.assignee else None


class RentDueTracker(Base):
    __tablename__ = "rent_due_tracker"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)

    property_address: Mapped[str] = mapped_column(String, index=True)
    frequency: Mapped[str] = mapped_column(String, default="MONTHLY", index=True)  # MONTHLY / FORTNIGHTLY

    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    due_day: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    period_label: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_sheet: Mapped[str | None] = mapped_column(String, nullable=True)

    status: Mapped[RentTrackStatus] = mapped_column(SAEnum(RentTrackStatus), default=RentTrackStatus.DUE, index=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    paid_on: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    partial_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ManagedProperty(Base):
    __tablename__ = "managed_properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    property_address: Mapped[str] = mapped_column(String, index=True)
    address_line_2: Mapped[str | None] = mapped_column(String, nullable=True)
    suburb: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    state_code: Mapped[str | None] = mapped_column(String, nullable=True)
    postcode: Mapped[str | None] = mapped_column(String, nullable=True)
    crm_property_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    property_type: Mapped[str | None] = mapped_column(String, nullable=True)
    rental_type: Mapped[str | None] = mapped_column(String, nullable=True)
    key_number: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_is_company: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    tenancy_status: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    owners_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tenants_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class InspectionGeocodeCache(Base):
    __tablename__ = "inspection_geocode_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    cache_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    query_address: Mapped[str] = mapped_column(String)
    formatted_address: Mapped[str | None] = mapped_column(String, nullable=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String, default="vicmap", index=True)
    provider_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class InspectionPlan(Base):
    __tablename__ = "inspection_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[InspectionPlanStatus] = mapped_column(
        SAEnum(InspectionPlanStatus),
        default=InspectionPlanStatus.PLANNED,
        index=True,
    )
    plan_date: Mapped[date] = mapped_column(Date, index=True)
    day_start: Mapped[str] = mapped_column(String)
    day_end: Mapped[str] = mapped_column(String)
    timezone: Mapped[str] = mapped_column(String, default="Australia/Melbourne")
    start_address: Mapped[str] = mapped_column(String)
    allow_agent_overlap: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    optimization_result_json: Mapped[str] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    created_by = relationship("User")
    visits = relationship("InspectionVisit", back_populates="plan", cascade="all, delete-orphan")


class InspectionVisit(Base):
    __tablename__ = "inspection_visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("inspection_plans.id", ondelete="CASCADE"),
        index=True,
    )
    client_id: Mapped[str] = mapped_column(String, index=True)
    property_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("managed_properties.id"),
        index=True,
    )
    property_address: Mapped[str] = mapped_column(String, index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    agent_ids_json: Mapped[str] = mapped_column(Text)
    agent_names_json: Mapped[str] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime, index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    travel_minutes: Mapped[int] = mapped_column(Integer, default=0)
    distance_km: Mapped[float] = mapped_column(Float, default=0.0)
    conflicts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    plan = relationship("InspectionPlan", back_populates="visits")
    property = relationship("ManagedProperty")


class MaintenanceOrder(Base):
    __tablename__ = "maintenance_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    property_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("managed_properties.id"), nullable=True, index=True)
    property_address: Mapped[str] = mapped_column(String, index=True)
    suburb: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    state_code: Mapped[str | None] = mapped_column(String, nullable=True)
    postcode: Mapped[str | None] = mapped_column(String, nullable=True)

    title: Mapped[str] = mapped_column(String, index=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    priority: Mapped[str] = mapped_column(String, default="normal", index=True)
    description: Mapped[str] = mapped_column(Text)
    access_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    owner_name: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    owner_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    tenant_name: Mapped[str | None] = mapped_column(String, nullable=True)
    tenant_email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    tenant_phone: Mapped[str | None] = mapped_column(String, nullable=True)

    status: Mapped[MaintenanceOrderStatus] = mapped_column(
        SAEnum(MaintenanceOrderStatus),
        default=MaintenanceOrderStatus.NEW,
        index=True,
    )
    assignee_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    due_by: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    owner_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    owner_decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    owner_decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    tradie_name: Mapped[str | None] = mapped_column(String, nullable=True)
    tradie_company: Mapped[str | None] = mapped_column(String, nullable=True)
    tradie_email: Mapped[str | None] = mapped_column(String, nullable=True)
    tradie_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    tradie_scheduled_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    tradie_arranged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    quoted_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    quote_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    tenant_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String, default="staff", index=True)
    tenant_account_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("tenant_accounts.id"), nullable=True, index=True)
    tenant_submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    property = relationship("ManagedProperty")
    tenant_account = relationship("TenantAccount")
    assignee = relationship("User", foreign_keys=[assignee_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    attachments = relationship("MaintenanceAttachment", back_populates="order", cascade="all, delete-orphan")
    events = relationship("MaintenanceEvent", back_populates="order", cascade="all, delete-orphan")


class MaintenanceAttachment(Base):
    __tablename__ = "maintenance_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("maintenance_orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String, default="GENERAL", index=True)
    filename: Mapped[str] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String, default="application/octet-stream")
    content_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    uploaded_by_tenant_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("tenant_accounts.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    order = relationship("MaintenanceOrder", back_populates="attachments")
    uploaded_by = relationship("User")
    uploaded_by_tenant = relationship("TenantAccount")


class MaintenanceEvent(Base):
    __tablename__ = "maintenance_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("maintenance_orders.id"), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    order = relationship("MaintenanceOrder", back_populates="events")
    actor = relationship("User")


class MaintenanceTradie(Base):
    __tablename__ = "maintenance_tradies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    company: Mapped[str] = mapped_column(String, index=True)
    contact_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    trade_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class LeaseRenewalRecord(Base):
    __tablename__ = "lease_renewal_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("managed_properties.id"), index=True)

    status: Mapped[LeaseRenewalStatus] = mapped_column(
        SAEnum(LeaseRenewalStatus),
        default=LeaseRenewalStatus.NOT_STARTED,
        index=True,
    )
    current_lease_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    current_lease_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    renewal_due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    lease_sent_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_resent_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    proposed_lease_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    proposed_lease_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    proposed_term: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    current_rent: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_rent: Mapped[float | None] = mapped_column(Float, nullable=True)
    rent_increase_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    owner_signed_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    tenant_signed_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    follow_up_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    assigned_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    property = relationship("ManagedProperty")
    assigned_user = relationship("User", foreign_keys=[assigned_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    events = relationship("LeaseRenewalEvent", back_populates="record", cascade="all, delete-orphan")


class LeaseRenewalEvent(Base):
    __tablename__ = "lease_renewal_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    record_id: Mapped[int] = mapped_column(Integer, ForeignKey("lease_renewal_records.id"), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    record = relationship("LeaseRenewalRecord", back_populates="events")
    actor = relationship("User")


class ComplianceProvider(Base):
    __tablename__ = "compliance_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    contact_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ComplianceRecord(Base):
    __tablename__ = "compliance_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("managed_properties.id"), index=True)
    compliance_type: Mapped[ComplianceType] = mapped_column(SAEnum(ComplianceType), index=True)
    status: Mapped[ComplianceRecordStatus] = mapped_column(SAEnum(ComplianceRecordStatus), default=ComplianceRecordStatus.OPEN, index=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    provider_name: Mapped[str | None] = mapped_column(String, nullable=True)
    result_text: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    property = relationship("ManagedProperty")


class ComplianceProperty(Base):
    __tablename__ = "compliance_properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox: Mapped[str] = mapped_column(String, index=True)

    property_address: Mapped[str] = mapped_column(String, index=True)
    address_line_2: Mapped[str | None] = mapped_column(String, nullable=True)
    suburb: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    state_code: Mapped[str | None] = mapped_column(String, nullable=True)
    postcode: Mapped[str | None] = mapped_column(String, nullable=True)
    source_sheet: Mapped[str | None] = mapped_column(String, nullable=True)

    mrs_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    gas_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    smoke_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    electrical_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    pool_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    powerband_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    disclosure_raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    gas_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    gas_next_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    smoke_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    smoke_next_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    electrical_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    electrical_next_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    work_order_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    completed_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    report_result: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    invoice_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    invoice_payment_status: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    electrical_faults_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    gas_faults_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    smoke_faults_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    mrs_faults_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    quoted_electrical_payment_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    quoted_gas_payment_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    quoted_smoke_payment_raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    compliance_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    overall_state: Mapped[ComplianceState] = mapped_column(SAEnum(ComplianceState), default=ComplianceState.UNKNOWN, index=True)
    overall_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
