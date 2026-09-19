import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings


@dataclass(slots=True)
class RequestIdentity:
    user_id: str
    name: str | None
    email: str | None
    role: str
    chat_id: str | None
    task: str | None


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


async def require_backend_api_key(
    authorization: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    token = _extract_bearer(authorization)
    if not token or not hmac.compare_digest(token, settings.backend_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid backend API key",
        )


async def require_admin_key(
    authorization: str | None = Header(default=None),
    x_admin_key: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    bearer = _extract_bearer(authorization)
    supplied = x_admin_key or bearer
    if not supplied or not hmac.compare_digest(supplied, settings.admin_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin API key",
        )


def get_request_identity(request: Request) -> RequestIdentity:
    settings = get_settings()
    headers = request.headers

    user_id = (
        headers.get("x-openwebui-user-id")
        or headers.get("x-user-id")
        or settings.default_user_id
    )
    role = (headers.get("x-openwebui-user-role") or "user").lower()

    if settings.allowed_user_id_set and user_id not in settings.allowed_user_id_set:
        raise HTTPException(status_code=403, detail="User is not allowed")

    if settings.allowed_role_set and role not in settings.allowed_role_set:
        raise HTTPException(status_code=403, detail="Role is not allowed")

    return RequestIdentity(
        user_id=user_id,
        name=headers.get("x-openwebui-user-name"),
        email=headers.get("x-openwebui-user-email"),
        role=role,
        chat_id=headers.get("x-openwebui-chat-id") or headers.get("x-conversation-id"),
        task=headers.get("x-openwebui-task"),
    )
