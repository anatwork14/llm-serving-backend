import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from structlog.contextvars import bind_contextvars, clear_contextvars

from app import models as _models  # noqa: F401 - registers SQLAlchemy metadata
from app.config import get_settings
from app.db import SessionLocal, engine, init_db
from app.logging_config import configure_logging
from app.routers import admin, openai
from app.services.admission import llm_admission
from app.services.llama import llama_client

configure_logging()
logger = structlog.get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    logger.info("application_started", llama_base_url=settings.llama_base_url)
    yield
    await llama_client.close()
    await engine.dispose()
    logger.info("application_stopped")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(openai.router)
app.include_router(admin.router)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    clear_contextvars()
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    bind_contextvars(
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        user_id=request.headers.get("x-openwebui-user-id"),
        chat_id=request.headers.get("x-openwebui-chat-id"),
    )

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request_failed", duration_ms=(time.perf_counter() - started) * 1000)
        raise

    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_complete",
        status_code=response.status_code,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return response


@app.get("/health/live", tags=["health"])
async def live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def ready():
    db_ok = False
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        logger.exception("database_healthcheck_failed")

    llama_ok = await llama_client.reachable()
    body = {
        "status": "ok" if db_ok and llama_ok else "degraded",
        "database": "ok" if db_ok else "unavailable",
        "llama_cpp": "ok" if llama_ok else "unavailable",
        "admission": llm_admission.snapshot().as_dict(),
    }
    return JSONResponse(status_code=200 if db_ok and llama_ok else 503, content=body)
