from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Document, Memory, UserProfile
from app.schemas import DocumentCreate, MemoryCreate, UserInstructionsUpdate
from app.security import require_admin_key
from app.services.memory import create_memory
from app.services.rag import ingest_document

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_key)],
)


@router.get("/users/{user_id}")
async def get_user_profile(
    user_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    profile = await session.get(UserProfile, user_id)
    if profile is None:
        return {"user_id": user_id, "display_name": None, "instructions": ""}
    return {
        "user_id": profile.user_id,
        "display_name": profile.display_name,
        "instructions": profile.instructions,
    }


@router.put("/users/{user_id}")
async def update_user_profile(
    user_id: str,
    body: UserInstructionsUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    profile = await session.get(UserProfile, user_id)
    if profile is None:
        profile = UserProfile(
            user_id=user_id,
            display_name=body.display_name,
            instructions=body.instructions,
        )
        session.add(profile)
    else:
        profile.instructions = body.instructions
        if body.display_name is not None:
            profile.display_name = body.display_name

    await session.commit()
    return {
        "user_id": profile.user_id,
        "display_name": profile.display_name,
        "instructions": profile.instructions,
    }


@router.get("/memories")
async def list_memories(
    user_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (
        await session.execute(
            select(Memory)
            .where(Memory.user_id == user_id)
            .order_by(Memory.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": item.id,
            "user_id": item.user_id,
            "content": item.content,
            "importance": item.importance,
            "source": item.source,
            "created_at": item.created_at,
        }
        for item in rows
    ]


@router.post("/memories", status_code=status.HTTP_201_CREATED)
async def add_memory(
    body: MemoryCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    memory = await create_memory(
        session,
        user_id=body.user_id,
        content=body.content,
        importance=body.importance,
        source=body.source,
    )
    await session.commit()
    return {
        "id": memory.id,
        "user_id": memory.user_id,
        "content": memory.content,
        "importance": memory.importance,
        "source": memory.source,
    }


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    memory = await session.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    await session.delete(memory)
    await session.commit()


@router.get("/documents")
async def list_documents(
    owner_user_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    stmt = select(Document).order_by(Document.created_at.desc())
    if owner_user_id is not None:
        stmt = stmt.where(Document.owner_user_id == owner_user_id)

    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": item.id,
            "title": item.title,
            "source": item.source,
            "owner_user_id": item.owner_user_id,
            "metadata": item.meta,
            "created_at": item.created_at,
        }
        for item in rows
    ]


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def add_document(
    body: DocumentCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    document = await ingest_document(
        session,
        title=body.title,
        content=body.content,
        source=body.source,
        owner_user_id=body.owner_user_id,
        metadata=body.metadata,
    )
    await session.commit()
    return {
        "id": document.id,
        "title": document.title,
        "source": document.source,
        "owner_user_id": document.owner_user_id,
    }


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Child chunks are deleted by the FK cascade in PostgreSQL.
    await session.execute(delete(Document).where(Document.id == document_id))
    await session.commit()
