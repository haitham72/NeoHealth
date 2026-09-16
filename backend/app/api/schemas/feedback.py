"""Request schema for POST /report-answer."""
from typing import Literal

from pydantic import BaseModel, Field


class ReportAnswerRequest(BaseModel):
    run_id: str
    reason: Literal["wrong_citation", "unrelated", "incorrect_abstention", "other"]
    comment: str | None = Field(default=None, max_length=500)
