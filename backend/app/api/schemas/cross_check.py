"""Request schema for POST /cross-check-regulation."""
from pydantic import BaseModel


class CrossCheckRegulationRequest(BaseModel):
    doc_code: str
    current_document_id: int
    cited_text: str
    cited_page: int
    question: str
