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
        # Open the upstream stream before returning FastAPI's StreamingResponse.
        # This lets the gateway return a real 502 if llama.cpp is unavailable
        # instead of failing after HTTP 200 headers have already been sent.
        request = self.client.build_request("POST", "chat/completions", json=payload)
        response = await self.client.send(request, stream=True)
        response.raise_for_status()
        return response

    async def reachable(self) -> bool:
        try:
            response = await self.client.get("models")
            return response.status_code < 500
        except httpx.HTTPError:
            return False


llama_client = LlamaClient()
