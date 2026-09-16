"""tests/test_api_tier.py -- api.py's tier-aware surface: corpus stats, chunk document
enrichment, and sibling-version enrichment."""
from app.api.routers import corpus as corpus_router
from app.services.enrichment import attach_chunk_documents, enrich_result
from tests.conftest import fake_request, seed_official_doc, seed_research_doc


def test_corpus_stats_groups_by_tier(test_conn, monkeypatch):
    seed_official_doc(test_conn)
    seed_research_doc(test_conn)
    seed_research_doc(test_conn, score=0.5)
    monkeypatch.setattr(corpus_router, "get_connection", lambda: test_conn)
    monkeypatch.setattr(corpus_router, "release_connection", lambda conn: None)
    stats = corpus_router.corpus_stats(fake_request())
    assert stats["official_documents"] == 1
    assert stats["official_chunks"] == 2
    assert stats["research_documents"] == 2


def test_attach_chunk_documents_includes_tier(test_conn):
    seeded = seed_research_doc(test_conn)
    chunks = [{"document_id": seeded["document_id"]}]
    attach_chunk_documents(test_conn, chunks)
    assert chunks[0]["document"]["tier"] == "research"


def test_enrich_result_includes_sibling_versions_for_official(test_conn):
    seeded = seed_official_doc(test_conn)
    document = {
        "id": seeded["document_id"], "doc_code": "DHA/HRS/HPSD/ST-14", "title": "Standards for Telehealth Services",
        "version": "4", "effective_date": "2025-11-26", "authority": "Dubai Health Authority",
        "source_url": None, "superseded": False, "tier": "official",
    }
    result = {
        "abstained": False, "document": document,
        "retrieved_chunks": [{"document_id": seeded["document_id"]}],
    }
    enrich_result(test_conn, result)
    assert isinstance(result["sibling_versions"], list)
    assert result["sibling_versions"][0]["id"] == seeded["document_id"]
