"""Request schema for POST /diff-followup."""
from pydantic import BaseModel


class DiffFollowupRequest(BaseModel):
    doc_code: str
    current_document_id: int
    cited_text: str
    cited_page: int
    question: str
