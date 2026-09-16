"""tests/test_citation_edge_cases.py -- supersession x tier edge cases in the
citation path."""
from app.core import retrieval
from tests.conftest import orthogonal_vec, seed_chunk, seed_document, seed_official_doc


def test_supersession_filter_preserves_citation(test_conn, stub_llm):
    old_id = seed_document(test_conn, doc_code="DHA/HRS/HLD/MA-2", version="1.2", superseded=True, effective_date="2024-01-01")
    seed_chunk(test_conn, old_id, page=1, text="Old telehealth rule.", score=0.6)
    new_id = seed_document(test_conn, doc_code="DHA/HRS/HLD/MA-2", version="1.3", superseded=False, effective_date="2025-07-18")
    seed_chunk(test_conn, new_id, page=1, text="Current telehealth rule.", score=0.6)

    result = retrieval.answer_question(test_conn, "telehealth rule question", superseded_filter=True)
    assert result["abstained"] is False
    assert result["document"]["version"] == "1.3"


def test_supersession_filter_excludes_old_versions(test_conn, stub_llm):
    old_id = seed_document(test_conn, doc_code="DHA/HRS/HLD/MA-2", version="1.2", superseded=True, effective_date="2024-01-01")
    old_chunk_id = seed_chunk(test_conn, old_id, page=1, text="Old telehealth rule.", score=0.6)
    new_id = seed_document(test_conn, doc_code="DHA/HRS/HLD/MA-2", version="1.3", superseded=False, effective_date="2025-07-18")
    seed_chunk(test_conn, new_id, page=1, text="Current telehealth rule.", score=0.6)

    result = retrieval.answer_question(test_conn, "telehealth rule question", superseded_filter=True)
    retrieved_ids = {c["chunk_id"] for c in result["retrieved_chunks"]}
    assert old_chunk_id not in retrieved_ids


def test_answer_includes_tier_in_document_payload(test_conn, stub_llm):
    seed_official_doc(test_conn, score=0.6)
    result = retrieval.answer_question(test_conn, "telehealth question", superseded_filter=True)
    assert "tier" in result["document"]


def test_abstain_for_off_topic(test_conn, monkeypatch):
    seed_official_doc(test_conn, score=0.6)
    monkeypatch.setattr(retrieval, "embed", lambda text: orthogonal_vec())
    monkeypatch.setattr(retrieval, "generate_answer", lambda *a, **kw: "Stub answer text.")
    result = retrieval.answer_question(test_conn, "What is the capital of France?", superseded_filter=True)
    assert result["abstained"] is True
