from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import get_settings


class LlamaClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = httpx.AsyncClient(
            base_url=self.settings.llama_base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(self.settings.request_timeout_seconds),
            headers={"Authorization": f"Bearer {self.settings.llama_api_key}"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def list_models(self) -> dict[str, Any]:
        response = await self.client.get("models")
        response.raise_for_status()
        return response.json()

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post("chat/completions", json=payload)
        response.raise_for_status()
        return response.json()

    async def stream_chat(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        async with self.client.stream("POST", "chat/completions", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    yield line

    async def reachable(self) -> bool:
        try:
            response = await self.client.get("models")
            return response.status_code < 500
        except httpx.HTTPError:
            return False


llama_client = LlamaClient()
