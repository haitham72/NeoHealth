"""Request schemas for POST /ask and POST /ask-stream."""
from typing import Literal

from pydantic import BaseModel, Field


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    superseded_filter: bool = True
    provider: Literal["openai", "local"] = "openai"
    model: str | None = Field(default=None, max_length=200)  # local model id; ignored when provider == "openai"
    authority_filter: str | None = Field(default=None, max_length=200)  # e.g. "Dubai Health Authority"
    history: list[HistoryTurn] | None = None  # prior conversation turns, for LLM context only
