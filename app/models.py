from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Boolean, Integer, Enum as SAEnum, Text
from sqlalchemy.orm import Mapped, mapped_column, declarative_base

Base = declarative_base()


class TicketStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    RESPONDED = "RESPONDED"
    NO_REPLY_NEEDED = "NO_REPLY_NEEDED"


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

    ack_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
