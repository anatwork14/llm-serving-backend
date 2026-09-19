from typing import Any

from pydantic import BaseModel, Field


class UserInstructionsUpdate(BaseModel):
    instructions: str = Field(max_length=20_000)
    display_name: str | None = Field(default=None, max_length=255)


class MemoryCreate(BaseModel):
    user_id: str
    content: str = Field(min_length=1, max_length=20_000)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    source: str = Field(default="manual", max_length=100)


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)
    source: str | None = Field(default=None, max_length=1000)
    owner_user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryOut(BaseModel):
    id: str
    user_id: str
    content: str
    importance: float
    source: str


class DocumentOut(BaseModel):
    id: str
    title: str
    source: str | None
    owner_user_id: str | None
