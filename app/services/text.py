from typing import Any


def flatten_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                item_type = item.get("type")
                if item_type in {"text", "input_text"}:
                    value = item.get("text") or item.get("content")
                    if isinstance(value, str):
                        parts.append(value)
        return "\n".join(parts)

    if content is None:
        return ""

    return str(content)


def latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return flatten_message_content(message.get("content"))
    return ""


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 180) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + chunk_size, len(cleaned))

        if end < len(cleaned):
            break_at = cleaned.rfind("\n", start, end)
            if break_at <= start + chunk_size // 2:
                break_at = cleaned.rfind(" ", start, end)
            if break_at > start:
                end = break_at

        chunk = cleaned[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(cleaned):
            break
        start = max(end - overlap, start + 1)

    return chunks


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def sanitize_upstream_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # These keys are sometimes added by UIs/gateways but are not part of the
    # OpenAI Chat Completions request accepted by llama.cpp.
    ui_only_keys = {
        "chat_id",
        "conversation_id",
        "session_id",
        "metadata",
        "user_message_id",
    }
    return {key: value for key, value in payload.items() if key not in ui_only_keys}
