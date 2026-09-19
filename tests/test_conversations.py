from sqlalchemy.dialects import postgresql

from app.security import RequestIdentity
from app.services.conversations import _conversation_insert, _user_profile_insert


def _identity() -> RequestIdentity:
    return RequestIdentity(
        user_id="user-123",
        name="Dad",
        email=None,
        role="user",
        chat_id="chat-123",
        task=None,
    )


def test_conversation_insert_is_idempotent() -> None:
    statement = _conversation_insert(_identity(), "chat-123")
    compiled = statement.compile(dialect=postgresql.dialect())

    assert "ON CONFLICT (id) DO NOTHING" in str(compiled)
    assert compiled.params["id"] == "chat-123"
    assert compiled.params["user_id"] == "user-123"


def test_user_profile_insert_is_idempotent() -> None:
    statement = _user_profile_insert(_identity())
    compiled = statement.compile(dialect=postgresql.dialect())

    assert "ON CONFLICT (user_id) DO NOTHING" in str(compiled)
    assert compiled.params["user_id"] == "user-123"
    assert compiled.params["display_name"] == "Dad"
