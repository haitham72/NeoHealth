"""tests/test_research_citation.py -- tier-aware get_document/search behavior for
research-tier documents, and the API's tier-grouped corpus stats."""
from app.api.routers import corpus as corpus_router
from app.core import retrieval
from tests.conftest import QUERY_VEC, seed_official_doc, seed_research_doc


def test_get_document_returns_tier_for_research(test_conn):
    seeded = seed_research_doc(test_conn)
    document = retrieval.get_document(test_conn, seeded["document_id"])
    assert document["tier"] == "research"


def test_get_document_returns_journal_as_authority(test_conn):
    seeded = seed_research_doc(test_conn)
    document = retrieval.get_document(test_conn, seeded["document_id"])
    assert document["authority"] == "Asian Journal of Psychiatry"


def test_semantic_search_finds_research_chunks(test_conn):
    """Research-tier chunks aren't filtered out of the normal retrieval pool -- there's
    no tier clause in semantic_search's SQL, so a research chunk surfaces exactly like
    an official one."""
    seeded = seed_research_doc(test_conn, score=0.9)
    rows = retrieval.semantic_search(test_conn, QUERY_VEC, superseded_filter=True, k=10)
    assert seeded["chunk_ids"][0] in [row[0] for row in rows]


def test_lexical_search_finds_research_chunks(test_conn):
    seeded = seed_research_doc(test_conn)
    # seed_research_doc's first chunk text contains "Telepsychiatry" -- search on that term.
    rows = retrieval.lexical_search(test_conn, "telepsychiatry", superseded_filter=True, k=10)
    assert seeded["chunk_ids"][0] in [row[0] for row in rows]


def test_corpus_stats_includes_research_count(test_conn, monkeypatch):
    seed_official_doc(test_conn)
    seed_research_doc(test_conn)
    monkeypatch.setattr(corpus_router, "get_connection", lambda: test_conn)
    monkeypatch.setattr(corpus_router, "release_connection", lambda conn: None)
    stats = corpus_router.corpus_stats()
    assert stats["research_documents"] == 1
