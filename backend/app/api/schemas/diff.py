"""Request schema for POST /diff-followup."""
from typing import Literal

from pydantic import BaseModel, Field


class DiffFollowupRequest(BaseModel):
    doc_code: str
    current_document_id: int
    cited_text: str
    cited_page: int
    question: str
    provider: Literal["openai", "local"] = "openai"
    model: str | None = Field(default=None, max_length=200)  # local model id; ignored when provider == "openai"
