from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ConversationSummary, Message
from app.services.llama import llama_client
from app.services.text import flatten_message_content


async def maybe_refresh_summary(
    session: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
) -> ConversationSummary | None:
    settings = get_settings()

    total = (
        await session.execute(
            select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
        )
    ).scalar_one()

    existing = await session.get(ConversationSummary, conversation_id)
    summarized_count = existing.summarized_message_count if existing else 0

    target_count = max(0, total - settings.summary_keep_recent)
    unsummarized = target_count - summarized_count

    if target_count <= 0:
        return existing
    if existing is None and total < settings.summary_trigger_messages:
        return None

    # Once a summary exists, refresh before unsummarized messages can fall
    # outside the recent request tail. With the defaults (recent=10, keep=8),
    # this refreshes after at most two newly summarizable messages.
    if existing is not None:
        safe_unsummarized = max(
            0,
            settings.recent_message_limit - settings.summary_keep_recent,
        )
        if unsummarized <= safe_unsummarized:
            return existing

    rows = (
        await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .offset(summarized_count)
            .limit(unsummarized)
        )
    ).scalars().all()

    if not rows:
        return existing

    transcript = "\n".join(f"{item.role}: {item.content}" for item in rows)
    prior = existing.summary if existing else "(none)"

    payload = {
        "model": settings.upstream_model or settings.model_alias,
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 700,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Maintain a compact factual conversation summary for future context. "
                    "Preserve user preferences, commitments, named entities, unresolved tasks, "
                    "and important facts. Do not invent details."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Previous summary:\n"
                    f"{prior}\n\n"
                    "New conversation segment:\n"
                    f"{transcript}\n\n"
                    "Return only the updated summary."
                ),
            },
        ],
    }

    response = await llama_client.chat(payload)
    choices = response.get("choices") or []
    if not choices:
        return existing

    summary_text = flatten_message_content(
        (choices[0].get("message") or {}).get("content")
    ).strip()
    if not summary_text:
        return existing

    if existing is None:
        existing = ConversationSummary(
            conversation_id=conversation_id,
            user_id=user_id,
            summary=summary_text,
            summarized_message_count=target_count,
        )
        session.add(existing)
    else:
        existing.summary = summary_text
        existing.summarized_message_count = target_count

    await session.flush()
    return existing
