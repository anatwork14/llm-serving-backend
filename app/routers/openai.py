import json
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, get_session
from app.security import get_request_identity, require_backend_api_key
from app.services.context import build_augmented_messages
from app.services.conversations import add_message, ensure_user_and_conversation
from app.services.llama import llama_client
from app.services.summarizer import maybe_refresh_summary
from app.services.text import (
    flatten_message_content,
    latest_user_text,
    sanitize_upstream_payload,
)
from app.services.tools import apply_tool_policy

logger = structlog.get_logger(__name__)
router = APIRouter(
    prefix="/v1",
    tags=["openai"],
    dependencies=[Depends(require_backend_api_key)],
)


def _upstream_error(exc: httpx.HTTPError) -> HTTPException:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text[:2000]
        return HTTPException(
            status_code=502,
            detail=f"llama.cpp returned {exc.response.status_code}: {body}",
        )
    return HTTPException(status_code=502, detail=f"Cannot reach llama.cpp: {exc}")


def _assistant_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return flatten_message_content(message.get("content")).strip()


async def _store_assistant(
    *,
    conversation_id: str,
    user_id: str,
    content: str,
    metadata: dict | None = None,
) -> None:
    if not content:
        return
    async with SessionLocal() as session:
        await add_message(
            session,
            conversation_id=conversation_id,
            user_id=user_id,
            role="assistant",
            content=content,
            metadata=metadata,
            embed=False,
        )
        await session.commit()


@router.get("/models")
async def list_models() -> dict:
    settings = get_settings()
    # A stable alias keeps Open WebUI independent from the GGUF file name/path.
    return {
        "object": "list",
        "data": [
            {
                "id": settings.model_alias,
                "object": "model",
                "created": 0,
                "owned_by": "local",
            }
        ],
    }


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Request body must be JSON") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="'messages' must be a non-empty list")

    identity = get_request_identity(request)
    is_background_task = bool(identity.task)

    conversation_id = (
        identity.chat_id
        or request.headers.get("x-conversation-id")
        or f"adhoc:{identity.user_id}"
    )

    if is_background_task:
        augmented_messages = messages
    else:
        try:
            await ensure_user_and_conversation(session, identity, conversation_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        user_text = latest_user_text(messages).strip()
        if user_text:
            await add_message(
                session,
                conversation_id=conversation_id,
                user_id=identity.user_id,
                role="user",
                content=user_text,
                metadata={"source": "openwebui"},
                embed=True,
            )

        # Summaries are best-effort. A temporarily unavailable model should not
        # prevent the real user request from being sent.
        try:
            await maybe_refresh_summary(
                session,
                conversation_id=conversation_id,
                user_id=identity.user_id,
            )
        except (httpx.HTTPError, KeyError, ValueError):
            logger.exception(
                "conversation_summary_failed",
                user_id=identity.user_id,
                conversation_id=conversation_id,
            )

        augmented_messages = await build_augmented_messages(
            session,
            user_id=identity.user_id,
            conversation_id=conversation_id,
            messages=messages,
        )
        await session.commit()

    upstream = sanitize_upstream_payload(payload)
    upstream["messages"] = augmented_messages
    if settings.upstream_model:
        upstream["model"] = settings.upstream_model
    else:
        upstream["model"] = payload.get("model") or settings.model_alias
    upstream = apply_tool_policy(upstream)

    stream = bool(upstream.get("stream"))

    if not stream:
        try:
            response = await llama_client.chat(upstream)
        except httpx.HTTPError as exc:
            raise _upstream_error(exc) from exc

        if not is_background_task:
            await _store_assistant(
                conversation_id=conversation_id,
                user_id=identity.user_id,
                content=_assistant_text(response),
                metadata={"source": "llama.cpp"},
            )
        return JSONResponse(content=response)

    try:
        upstream_response = await llama_client.open_chat_stream(upstream)
    except httpx.HTTPError as exc:
        raise _upstream_error(exc) from exc

    async def event_stream():
        parts: list[str] = []
        try:
            async for line in upstream_response.aiter_lines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    if raw and raw != "[DONE]":
                        try:
                            chunk = json.loads(raw)
                            choices = chunk.get("choices") or []
                            if choices:
                                delta = choices[0].get("delta") or {}
                                piece = flatten_message_content(delta.get("content"))
                                if piece:
                                    parts.append(piece)
                        except (json.JSONDecodeError, TypeError, AttributeError):
                            logger.debug("unparsed_sse_chunk")

                # llama.cpp uses standard OpenAI SSE. Preserve every upstream
                # event line and restore the SSE event separator.
                yield (line + "\n\n").encode("utf-8")
        finally:
            await upstream_response.aclose()
            if not is_background_task:
                assistant = "".join(parts).strip()
                if assistant:
                    try:
                        await _store_assistant(
                            conversation_id=conversation_id,
                            user_id=identity.user_id,
                            content=assistant,
                            metadata={"source": "llama.cpp", "streamed": True},
                        )
                    except Exception:
                        logger.exception(
                            "assistant_persistence_failed",
                            user_id=identity.user_id,
                            conversation_id=conversation_id,
                        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
