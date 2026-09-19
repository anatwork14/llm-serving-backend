from app.services.text import (
    chunk_text,
    flatten_message_content,
    latest_user_text,
    normalize_system_messages,
    sanitize_upstream_payload,
)


def test_flatten_multimodal_text_content() -> None:
    content = [
        {"type": "text", "text": "hello"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        {"type": "input_text", "text": "world"},
    ]
    assert flatten_message_content(content) == "hello\nworld"


def test_latest_user_text() -> None:
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "latest"},
    ]
    assert latest_user_text(messages) == "latest"


def test_chunk_text_covers_document() -> None:
    text = " ".join(f"word{i}" for i in range(500))
    chunks = chunk_text(text, chunk_size=300, overlap=40)
    assert len(chunks) > 2
    assert all(chunk.strip() for chunk in chunks)
    assert chunks[0].startswith("word0")


def test_sanitize_upstream_payload_removes_ui_fields() -> None:
    payload = {
        "model": "bonsai-2-27b",
        "messages": [{"role": "user", "content": "hello"}],
        "chat_id": "abc",
        "metadata": {"foo": "bar"},
        "temperature": 0.4,
        "tools": [{"type": "function", "function": {"name": "weather"}}],
    }
    cleaned = sanitize_upstream_payload(payload)

    assert "chat_id" not in cleaned
    assert "metadata" not in cleaned
    assert cleaned["temperature"] == 0.4
    assert cleaned["tools"] == payload["tools"]


def test_normalize_system_messages_moves_and_merges_system_content() -> None:
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "system", "content": "second system"},
        {"role": "assistant", "content": "hi"},
        {"role": "system", "content": "third system"},
        {"role": "user", "content": "question"},
    ]

    normalized = normalize_system_messages(messages)

    assert normalized == [
        {"role": "system", "content": "second system\n\nthird system"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "question"},
    ]


def test_normalize_system_messages_keeps_non_system_order() -> None:
    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
    ]

    assert normalize_system_messages(messages) == messages
