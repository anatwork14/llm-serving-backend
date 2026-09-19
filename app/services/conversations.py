from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, UserProfile
from app.security import RequestIdentity
from app.services.embeddings import embedding_service


async def ensure_user_and_conversation(
    session: AsyncSession,
    identity: RequestIdentity,
    conversation_id: str,
) -> None:
    profile = await session.get(UserProfile, identity.user_id)
    if profile is None:
        profile = UserProfile(
            user_id=identity.user_id,
            display_name=identity.name,
            instructions="",
        )
        session.add(profile)
    elif identity.name and profile.display_name != identity.name:
        profile.display_name = identity.name

    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        session.add(Conversation(id=conversation_id, user_id=identity.user_id))
    elif conversation.user_id != identity.user_id:
        raise PermissionError("Conversation belongs to another user")

    await session.flush()


async def add_message(
    session: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
    role: str,
    content: str,
    metadata: dict | None = None,
    embed: bool = False,
) -> Message:
    vector = None
    if embed and content.strip():
        vector = await embedding_service.embed_one(content)

    message = Message(
        conversation_id=conversation_id,
        user_id=user_id,
        role=role,
        content=content,
        embedding=vector,
        meta=metadata or {},
    )
    session.add(message)
    await session.flush()
    return message


async def recent_messages(
    session: AsyncSession,
    conversation_id: str,
    limit: int,
) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))
