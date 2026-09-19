from sqlalchemy.dialects import postgresql

from app.services.summarizer import _summary_upsert


def test_summary_upsert_is_race_safe_and_monotonic() -> None:
    statement = _summary_upsert(
        conversation_id="chat-123",
        user_id="user-123",
        summary="summary",
        summarized_message_count=20,
    )
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "ON CONFLICT (conversation_id) DO UPDATE" in sql
    assert "summarized_message_count" in sql
    assert "<= excluded.summarized_message_count" in sql
    assert compiled.params["conversation_id"] == "chat-123"
    assert compiled.params["user_id"] == "user-123"
