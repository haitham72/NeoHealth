"""Request/response schemas for the per-visitor chat history endpoints."""
from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateChatRequest(BaseModel):
    client_id: str = Field(..., min_length=1, max_length=100)


class ChatSummary(BaseModel):
    id: str
    title: str | None
    created_at: str
    updated_at: str


class SaveMessageRequest(BaseModel):
    client_id: str = Field(..., min_length=1, max_length=100)
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=8000)
    response: dict[str, Any] | None = None


class ChatMessageOut(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    response: dict[str, Any] | None
    created_at: str


class ChatDetail(ChatSummary):
    messages: list[ChatMessageOut]
