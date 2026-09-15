"""Request schema for POST /cross-check-regulation."""
from typing import Literal

from pydantic import BaseModel, Field


class CrossCheckRegulationRequest(BaseModel):
    doc_code: str
    current_document_id: int
    cited_text: str
    cited_page: int
    question: str
    provider: Literal["openai", "local"] = "openai"
    model: str | None = Field(default=None, max_length=200)  # local model id; ignored when provider == "openai"
