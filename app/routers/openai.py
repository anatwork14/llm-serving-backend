import json
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, get_session
from app.security import get_request_identity, require_backend_api_key
from app.services.admission import (
    AdmissionQueueFull,
    AdmissionTimeout,
    llm_admission,
)
from app.services.context import build_augmented_messages
from app.services.conversations import add_message, ensure_user_and_conversation
from app.services.llama import llama_client
from app.services.summarizer import refresh_summary_background
from app.services.text import (
    flatten_message_content,
    latest_user_text,
    normalize_system_messages,
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


async def _acquire_llm_slot(*, background: bool):
    try:
        lease = await llm_admission.acquire(background=background)
    except AdmissionQueueFull as exc:
        raise HTTPException(
            status_code=429,
            detail="Local model queue is full. Retry shortly.",
            headers={"Retry-After": "5"},
        ) from exc
    except AdmissionTimeout as exc:
        raise HTTPException(
            status_code=429,
            detail="Timed out waiting for the local model. Retry shortly.",
            headers={"Retry-After": "5"},
        ) from exc

    if lease.waited_ms >= 100:
        logger.info(
            "llm_admission_wait",
            waited_ms=round(lease.waited_ms, 2),
            background=background,
            **llm_admission.snapshot().as_dict(),
        )
    return lease


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
    background_tasks: BackgroundTasks,
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

        augmented_messages = await build_augmented_messages(
            session,
            user_id=identity.user_id,
            conversation_id=conversation_id,
            messages=messages,
        )
        await session.commit()

    upstream = sanitize_upstream_payload(payload)
    upstream["messages"] = normalize_system_messages(augmented_messages)
    if settings.upstream_model:
        upstream["model"] = settings.upstream_model
    else:
        upstream["model"] = payload.get("model") or settings.model_alias
    upstream = apply_tool_policy(upstream)

    if is_background_task:
        upstream.setdefault("reasoning_effort", "none")
        template_kwargs = upstream.get("chat_template_kwargs")
        if not isinstance(template_kwargs, dict):
            template_kwargs = {}
        else:
            template_kwargs = dict(template_kwargs)
        template_kwargs.setdefault("enable_thinking", False)
        upstream["chat_template_kwargs"] = template_kwargs

    stream = bool(upstream.get("stream"))

    if not stream:
        lease = await _acquire_llm_slot(background=is_background_task)
        try:
            try:
                response = await llama_client.chat(upstream)
            except httpx.HTTPError as exc:
                raise _upstream_error(exc) from exc
        finally:
            await lease.release()

        if not is_background_task:
            await _store_assistant(
                conversation_id=conversation_id,
                user_id=identity.user_id,
                content=_assistant_text(response),
                metadata={"source": "llama.cpp"},
            )
            background_tasks.add_task(
                refresh_summary_background,
                conversation_id,
                identity.user_id,
            )
        return JSONResponse(content=response)

    lease = await _acquire_llm_slot(background=is_background_task)
    try:
        upstream_response = await llama_client.open_chat_stream(upstream)
    except httpx.HTTPError as exc:
        await lease.release()
        raise _upstream_error(exc) from exc
    except Exception:
        await lease.release()
        raise

    async def event_stream():
        parts: list[str] = []
        completed = False
        try:
            async for line in upstream_response.aiter_lines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        completed = True
                    elif raw:
                        try:
                            chunk = json.loads(raw)
                            choices = chunk.get("choices") or []
                            if choices:
                                choice = choices[0]
                                delta = choice.get("delta") or {}
                                piece = flatten_message_content(delta.get("content"))
                                if piece:
                                    parts.append(piece)
                                if choice.get("finish_reason") is not None:
                                    completed = True
                        except (json.JSONDecodeError, TypeError, AttributeError):
                            logger.debug("unparsed_sse_chunk")

                # llama.cpp uses standard OpenAI SSE. Ignore blank separator
                # lines and emit one separator after each data/event line.
                if line:
                    yield (line + "\n\n").encode("utf-8")
        finally:
            await upstream_response.aclose()
            await lease.release()
            if not is_background_task and completed:
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

    if not is_background_task:
        background_tasks.add_task(
            refresh_summary_background,
            conversation_id,
            identity.user_id,
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
