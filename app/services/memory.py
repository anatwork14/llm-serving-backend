from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Memory, Message
from app.services.embeddings import embedding_service


@dataclass(slots=True)
class RetrievedMemory:
    content: str
    score: float
    source: str


async def _semantic_search(
    session: AsyncSession,
    *,
    user_id: str,
    query_vector: list[float],
    exclude_conversation_id: str | None,
    limit: int,
) -> list[RetrievedMemory]:
    memory_distance = Memory.embedding.cosine_distance(query_vector).label("distance")
    memory_rows = (
        await session.execute(
            select(Memory, memory_distance)
            .where(Memory.user_id == user_id, Memory.embedding.is_not(None))
            .order_by(memory_distance)
            .limit(limit)
        )
    ).all()

    message_distance = Message.embedding.cosine_distance(query_vector).label("distance")
    message_stmt = select(Message, message_distance).where(
        Message.user_id == user_id,
        Message.role == "user",
        Message.embedding.is_not(None),
    )
    if exclude_conversation_id:
        message_stmt = message_stmt.where(Message.conversation_id != exclude_conversation_id)

    message_rows = (
        await session.execute(message_stmt.order_by(message_distance).limit(limit))
    ).all()

    candidates: list[RetrievedMemory] = []
    for memory, distance in memory_rows:
        distance_value = float(distance)
        candidates.append(
            RetrievedMemory(
                content=memory.content,
                score=(1.0 - distance_value) + (memory.importance * 0.05),
                source=f"memory:{memory.source}",
            )
        )

    for message, distance in message_rows:
        candidates.append(
            RetrievedMemory(
                content=message.content,
                score=1.0 - float(distance),
                source="past_conversation",
            )
        )

    return sorted(candidates, key=lambda item: item.score, reverse=True)[:limit]


async def _lexical_search(
    session: AsyncSession,
    *,
    user_id: str,
    query: str,
    exclude_conversation_id: str | None,
    limit: int,
) -> list[RetrievedMemory]:
    ts_query = func.plainto_tsquery("simple", query)

    memory_vector = func.to_tsvector("simple", Memory.content)
    memory_rank = func.ts_rank_cd(memory_vector, ts_query).label("rank")
    memory_rows = (
        await session.execute(
            select(Memory, memory_rank)
            .where(Memory.user_id == user_id, memory_vector.op("@@")(ts_query))
            .order_by(desc(memory_rank))
            .limit(limit)
        )
    ).all()

    message_vector = func.to_tsvector("simple", Message.content)
    message_rank = func.ts_rank_cd(message_vector, ts_query).label("rank")
    message_stmt = select(Message, message_rank).where(
        Message.user_id == user_id,
        Message.role == "user",
        message_vector.op("@@")(ts_query),
    )
    if exclude_conversation_id:
        message_stmt = message_stmt.where(Message.conversation_id != exclude_conversation_id)

    message_rows = (
        await session.execute(message_stmt.order_by(desc(message_rank)).limit(limit))
    ).all()

    candidates = [
        RetrievedMemory(memory.content, float(rank), f"memory:{memory.source}")
        for memory, rank in memory_rows
    ]
    candidates.extend(
        RetrievedMemory(message.content, float(rank), "past_conversation")
        for message, rank in message_rows
    )
    return sorted(candidates, key=lambda item: item.score, reverse=True)[:limit]


async def search_memories(
    session: AsyncSession,
    *,
    user_id: str,
    query: str,
    exclude_conversation_id: str | None = None,
) -> list[RetrievedMemory]:
    settings = get_settings()
    if not query.strip():
        return []

    query_vector = await embedding_service.embed_one(query)
    if query_vector is not None:
        return await _semantic_search(
            session,
            user_id=user_id,
            query_vector=query_vector,
            exclude_conversation_id=exclude_conversation_id,
            limit=settings.memory_top_k,
        )

    return await _lexical_search(
        session,
        user_id=user_id,
        query=query,
        exclude_conversation_id=exclude_conversation_id,
        limit=settings.memory_top_k,
    )


async def create_memory(
    session: AsyncSession,
    *,
    user_id: str,
    content: str,
    importance: float = 0.5,
    source: str = "manual",
) -> Memory:
    vector = await embedding_service.embed_one(content)
    memory = Memory(
        user_id=user_id,
        content=content,
        embedding=vector,
        importance=importance,
        source=source,
    )
    session.add(memory)
    await session.flush()
    return memory


async def touch_memory(session: AsyncSession, memory: Memory) -> None:
    memory.last_used_at = datetime.now(timezone.utc)
    await session.flush()
