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

    async def open_chat_stream(self, payload: dict[str, Any]) -> httpx.Response:
        # Open upstream before returning FastAPI's StreamingResponse so an
        # unreachable llama.cpp server can still become a normal HTTP 502.
        request = self.client.build_request("POST", "chat/completions", json=payload)
        response = await self.client.send(request, stream=True)
        try:
            response.raise_for_status()
        except Exception:
            await response.aclose()
            raise
        return response

    async def reachable(self) -> bool:
        try:
            response = await self.client.get("models")
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            return False


llama_client = LlamaClient()
