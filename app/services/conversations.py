from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, UserProfile
from app.security import RequestIdentity
from app.services.embeddings import embedding_service


def _user_profile_insert(identity: RequestIdentity):
    return (
        pg_insert(UserProfile)
        .values(
            user_id=identity.user_id,
            display_name=identity.name,
            instructions="",
        )
        .on_conflict_do_nothing(index_elements=[UserProfile.user_id])
    )


def _conversation_insert(identity: RequestIdentity, conversation_id: str):
    return (
        pg_insert(Conversation)
        .values(id=conversation_id, user_id=identity.user_id)
        .on_conflict_do_nothing(index_elements=[Conversation.id])
    )


async def ensure_user_and_conversation(
    session: AsyncSession,
    identity: RequestIdentity,
    conversation_id: str,
) -> None:
    # Open WebUI can issue overlapping requests for the same new chat
    # (for example, a user completion plus background/title tasks). A
    # check-then-insert race here used to make the second request fail with a
    # conversations_pkey UniqueViolation. Let PostgreSQL serialize creation
    # atomically instead.
    await session.execute(_user_profile_insert(identity))

    profile = await session.get(UserProfile, identity.user_id)
    if profile is not None and identity.name and profile.display_name != identity.name:
        profile.display_name = identity.name

    await session.execute(_conversation_insert(identity, conversation_id))

    owner_result = await session.execute(
        select(Conversation.user_id).where(Conversation.id == conversation_id)
    )
    owner_user_id = owner_result.scalar_one()

    if owner_user_id != identity.user_id:
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
