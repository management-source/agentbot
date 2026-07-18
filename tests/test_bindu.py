import os

os.environ["DEBUG"] = "false"
os.environ.setdefault("DATABASE_URL", "sqlite:///./app.db")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, BinduConversation, BinduMessage, User, UserRole
from app.routers.bindu import (
    BinduAskIn,
    ask_bindu,
    conversation_messages,
    create_conversation,
    list_conversations,
    _conversation_or_404,
)


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _user(db, email: str):
    row = User(email=email, name=email.split("@")[0], role=UserRole.ADMIN, is_active=True, password_hash="test")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_bindu_history_is_isolated_per_staff_member():
    db = _session()
    first = _user(db, "first@example.com")
    second = _user(db, "second@example.com")

    conversation = create_conversation(user=first, mailbox="admin@example.com", db=db)
    db.add(BinduMessage(conversation_id=conversation.id, role="user", content="private question"))
    db.commit()

    assert [row.id for row in list_conversations(user=first, mailbox="admin@example.com", db=db)] == [conversation.id]
    assert list_conversations(user=second, mailbox="admin@example.com", db=db) == []
    assert conversation_messages(conversation.id, user=first, db=db)[0].content == "private question"

    try:
        _conversation_or_404(db, second, conversation.id)
        assert False, "A staff member must not access another staff member's Bindu history"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404


def test_bindu_ask_creates_and_continues_conversation(monkeypatch):
    db = _session()
    user = _user(db, "staff@example.com")
    monkeypatch.setattr("app.routers.bindu._collect_sources", lambda *args: ([], ["portal"]))
    monkeypatch.setattr("app.routers.bindu._grounded_answer", lambda *args: "Grounded response")

    first = ask_bindu(BinduAskIn(message="Find the remote email"), user=user, mailbox="admin@example.com", db=db)
    second = ask_bindu(BinduAskIn(message="Who sent it?", conversation_id=first.conversation_id), user=user, mailbox="admin@example.com", db=db)

    assert second.conversation_id == first.conversation_id
    conversation = db.get(BinduConversation, first.conversation_id)
    assert conversation.user_id == user.id
    assert conversation.title == "Find the remote email"
    assert [row.role for row in conversation.messages] == ["user", "assistant", "user", "assistant"]
