import asyncio
from collections.abc import Sequence

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


class EmbeddingService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._model = None
        self._init_lock = asyncio.Lock()

    async def _ensure_model(self):
        if not self.settings.embeddings_enabled:
            return None
        if self._model is not None:
            return self._model

        async with self._init_lock:
            if self._model is not None:
                return self._model

            def load():
                from sentence_transformers import SentenceTransformer

                return SentenceTransformer(
                    self.settings.embedding_model,
                    device=self.settings.embedding_device,
                )

            logger.info(
                "embedding_model_loading",
                model=self.settings.embedding_model,
                device=self.settings.embedding_device,
            )
            self._model = await asyncio.to_thread(load)
            logger.info("embedding_model_loaded", model=self.settings.embedding_model)

        return self._model

    async def embed_many(self, texts: Sequence[str]) -> list[list[float] | None]:
        if not texts:
            return []

        model = await self._ensure_model()
        if model is None:
            return [None for _ in texts]

        def encode() -> list[list[float]]:
            vectors = model.encode(
                list(texts),
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return [vector.tolist() for vector in vectors]

        try:
            return await asyncio.to_thread(encode)
        except Exception:
            logger.exception("embedding_failed")
            return [None for _ in texts]

    async def embed_one(self, text: str) -> list[float] | None:
        results = await self.embed_many([text])
        return results[0] if results else None


embedding_service = EmbeddingService()
