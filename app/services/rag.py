from dataclasses import dataclass

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Document, DocumentChunk
from app.services.embeddings import embedding_service
from app.services.text import chunk_text


@dataclass(slots=True)
class RetrievedChunk:
    content: str
    score: float
    document_id: str


async def ingest_document(
    session: AsyncSession,
    *,
    title: str,
    content: str,
    source: str | None,
    owner_user_id: str | None,
    metadata: dict,
) -> Document:
    document = Document(
        title=title,
        content=content,
        source=source,
        owner_user_id=owner_user_id,
        meta=metadata,
    )
    session.add(document)
    await session.flush()

    chunks = chunk_text(content)
    vectors = await embedding_service.embed_many(chunks)

    for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
        session.add(
            DocumentChunk(
                document_id=document.id,
                owner_user_id=owner_user_id,
                chunk_index=index,
                content=chunk,
                embedding=vector,
            )
        )

    await session.flush()
    return document


async def search_documents(
    session: AsyncSession,
    *,
    user_id: str,
    query: str,
) -> list[RetrievedChunk]:
    settings = get_settings()
    if not query.strip():
        return []

    visibility = or_(
        DocumentChunk.owner_user_id.is_(None),
        DocumentChunk.owner_user_id == user_id,
    )
    query_vector = await embedding_service.embed_one(query)

    if query_vector is not None:
        distance = DocumentChunk.embedding.cosine_distance(query_vector).label("distance")
        rows = (
            await session.execute(
                select(DocumentChunk, distance)
                .where(visibility, DocumentChunk.embedding.is_not(None))
                .order_by(distance)
                .limit(settings.rag_top_k)
            )
        ).all()
        return [
            RetrievedChunk(
                content=chunk.content,
                score=1.0 - float(distance_value),
                document_id=chunk.document_id,
            )
            for chunk, distance_value in rows
        ]

    ts_query = func.plainto_tsquery("simple", query)
    search_vector = func.to_tsvector("simple", DocumentChunk.content)
    rank = func.ts_rank_cd(search_vector, ts_query).label("rank")
    rows = (
        await session.execute(
            select(DocumentChunk, rank)
            .where(visibility, search_vector.op("@@")(ts_query))
            .order_by(desc(rank))
            .limit(settings.rag_top_k)
        )
    ).all()

    return [
        RetrievedChunk(
            content=chunk.content,
            score=float(rank_value),
            document_id=chunk.document_id,
        )
        for chunk, rank_value in rows
    ]
