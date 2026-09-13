"""Tests for the answer_cache table schema and app.core.answer_cache's dual-layer
(Redis L1 exact-key + Postgres L2 semantic) lookup/store behavior.

Redis is faked via the `redis_conn` fixture (tests/conftest.py) rather than requiring
a real Redis instance -- see that fixture's docstring. Embeddings are fixed vectors
from tests/conftest.py's `vec()`/`QUERY_VEC`/`orthogonal_vec()` helpers, never a live
OpenAI call -- semantic similarity between "questions" is controlled purely by which
fixed vector each test passes as query_vec, exactly like the rest of this test suite
controls retrieval relevance (see stub_llm's docstring).
"""
import pytest

from app.core.answer_cache import (
    MATCH_EXACT,
    MATCH_SEMANTIC,
    decorate_cached_result,
    lookup_answer_cache,
    normalize_question,
    store_answer_cache,
)
from app.core.config import CACHE_HIT_THRESHOLD
from tests.conftest import QUERY_VEC, orthogonal_vec


@pytest.fixture(autouse=True)
def _clean_answer_cache(conn):
    """store_answer_cache() commits its own Postgres writes (intentionally -- a cache
    entry must survive regardless of what the caller's own read-transaction later does),
    which means the `conn` fixture's usual rollback-on-teardown can't undo them. Without
    this, one test's cached row would leak into the next test's row-count assertions in
    the same real test_regulense database. Truncate before each test in this file rather
    than changing the shared `conn` fixture (other test files never hit this table)."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE answer_cache RESTART IDENTITY")
    conn.commit()
    yield

# A real-shaped answered result, mirroring retrieval.answer_question()'s non-abstained
# return dict (app/core/retrieval.py) closely enough to exercise storage/stripping
# faithfully, without pulling in the real pipeline.
SAMPLE_RESULT = {
    "abstained": False,
    "answer": "Findings: ...\n\nSummary: The nurse-to-patient ratio is 1:4.",
    "model_used": "gpt-4o-mini",
    "top_score": 0.81,
    "confidence_tier": "high",
    "document": {
        "id": 1,
        "title": "Standards for Telehealth Services",
        "doc_code": "DHA/HRS/HPSD/ST-14",
        "version": "4",
        "effective_date": "2025-11-26",
        "authority": "Dubai Health Authority",
        "source_url": None,
        "superseded": False,
        "tier": "official",
    },
    "page": 83,
    "page_end": 83,
    "heading_path": ["Telehealth", "Standards"],
    "bboxes": [{"page": 83, "x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.3}],
    "superseded_excluded": 0,
    "retrieved_chunks": [
        {"chunk_id": 1, "document_id": 1, "page": 83, "semantic_score": 0.81, "used_for_answer": True},
    ],
    # run-scoped field: store_answer_cache must strip this before persisting.
    "run_id": "run-original-abc",
}

ABSTAINED_RESULT = {
    "abstained": True,
    "reason": "below retrieval confidence threshold",
    "top_score": 0.4,
    "run_id": "run-abstain-xyz",
}


def test_answer_cache_table_exists(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'answer_cache'
            """
        )
        assert cur.fetchone() is not None


def test_normalize_question_lowercases_and_collapses_whitespace():
    assert (
        normalize_question("  What Is   the Nurse-to-Patient   Ratio?  ")
        == "what is the nurse-to-patient ratio?"
    )


def test_redis_only_probe_returns_none_without_embedding(conn, redis_conn):
    """query_vec=None on an empty cache must miss cleanly -- no embedding call is
    possible here (none is passed in, and answer_cache.py never computes one itself),
    and it must not fall through to a Postgres query with a None vector either."""
    result = lookup_answer_cache(conn, "What is the nurse-to-patient ratio?", None, False, None)

    assert result is None
    assert redis_conn.dbsize() == 0


def test_redis_exact_hit_after_store(conn, redis_conn):
    question = "What is the nurse-to-patient ratio?"
    store_answer_cache(conn, question, QUERY_VEC, False, None, SAMPLE_RESULT)

    hit = lookup_answer_cache(conn, question, None, False, None)

    assert hit is not None
    assert hit["cache_layer"] == "redis"
    assert hit["match_mode"] == MATCH_EXACT
    assert hit["similarity"] == 1.0
    assert hit["matched_question"] == question
    assert hit["result"]["answer"] == SAMPLE_RESULT["answer"]
    # run-scoped / cache-meta fields must not have been persisted
    assert "run_id" not in hit["result"]
    assert "cache_hit" not in hit["result"]


def test_redis_exact_hit_ignores_case_and_whitespace(conn, redis_conn):
    """The Redis key is derived from normalize_question(), so a differently-cased /
    spaced repeat of the same question must still be an exact hit."""
    store_answer_cache(
        conn, "What is the nurse-to-patient ratio?", QUERY_VEC, False, None, SAMPLE_RESULT
    )

    hit = lookup_answer_cache(
        conn, "  what IS the nurse-to-patient ratio?  ", None, False, None
    )

    assert hit is not None
    assert hit["cache_layer"] == "redis"
    assert hit["match_mode"] == MATCH_EXACT


def test_redis_miss_different_filters(conn, redis_conn):
    """Filters are part of the Redis key (cache_filters_key) -- a superseded_filter or
    authority_filter change must not accidentally hit another filter combo's entry."""
    question = "What is the nurse-to-patient ratio?"
    store_answer_cache(conn, question, QUERY_VEC, False, "DHA", SAMPLE_RESULT)

    assert lookup_answer_cache(conn, question, None, True, "DHA") is None
    assert lookup_answer_cache(conn, question, None, False, "DoH") is None
    assert lookup_answer_cache(conn, question, None, False, None) is None


def test_postgres_semantic_hit_backfills_redis(conn, redis_conn):
    original_question = "What is the nurse-to-patient ratio?"
    paraphrase = "What's the required nurse to patient ratio?"
    store_answer_cache(conn, original_question, QUERY_VEC, False, None, SAMPLE_RESULT)

    # The paraphrase normalizes to a different string, so its Redis exact key misses;
    # it carries the *same* embedding as the stored question, so Postgres cosine
    # similarity is 1.0 -- comfortably over CACHE_HIT_THRESHOLD -- and should hit L2.
    hit = lookup_answer_cache(conn, paraphrase, QUERY_VEC, False, None)

    assert hit is not None
    assert hit["cache_layer"] == "postgres"
    assert hit["match_mode"] == MATCH_SEMANTIC
    assert hit["similarity"] >= CACHE_HIT_THRESHOLD
    assert hit["matched_question"] == original_question
    assert hit["cache_id"] is not None
    assert hit["result"]["answer"] == SAMPLE_RESULT["answer"]

    # The Postgres hit must have backfilled Redis under the paraphrase's own key, so a
    # second lookup -- even a Redis-only probe -- now hits without touching Postgres.
    hit2 = lookup_answer_cache(conn, paraphrase, None, False, None)
    assert hit2 is not None
    assert hit2["cache_layer"] == "redis"
    assert hit2["match_mode"] == MATCH_EXACT


def test_lookup_miss_different_authority_filter(conn):
    """Pure Postgres-layer test (no redis_conn): REDIS_URL is unset in the test env, so
    _get_redis() naturally returns None and this only exercises the L2 SQL filter."""
    question = "What is the nurse-to-patient ratio?"
    store_answer_cache(conn, question, QUERY_VEC, False, "DHA", SAMPLE_RESULT)

    hit = lookup_answer_cache(conn, question, QUERY_VEC, False, "DoH")

    assert hit is None


def test_lookup_miss_below_threshold(conn):
    question = "What is the nurse-to-patient ratio?"
    store_answer_cache(conn, question, QUERY_VEC, False, None, SAMPLE_RESULT)

    # orthogonal_vec() has ~0 cosine similarity to QUERY_VEC -- nowhere near
    # CACHE_HIT_THRESHOLD (0.92 by default) -- so this must miss even though a row
    # exists under matching filters.
    hit = lookup_answer_cache(conn, "an unrelated question", orthogonal_vec(), False, None)

    assert hit is None


def test_store_answer_cache_skips_abstained_results(conn, redis_conn):
    question = "What is the nurse-to-patient ratio?"
    store_answer_cache(conn, question, QUERY_VEC, False, None, ABSTAINED_RESULT)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM answer_cache")
        assert cur.fetchone()[0] == 0
    assert redis_conn.dbsize() == 0
    assert lookup_answer_cache(conn, question, QUERY_VEC, False, None) is None


def test_decorate_cached_result_merges_cache_metadata():
    hit = {
        "result": {"abstained": False, "answer": "The ratio is 1:4."},
        "cache_layer": "redis",
        "match_mode": MATCH_EXACT,
        "similarity": 1.0,
        "matched_question": "what is the nurse-to-patient ratio?",
        "cache_id": None,
    }

    decorated = decorate_cached_result(hit, run_id="run-live-999")

    assert decorated["answer"] == "The ratio is 1:4."
    assert decorated["cache_hit"] is True
    assert decorated["cache_layer"] == "redis"
    assert decorated["cache_match_mode"] == MATCH_EXACT
    assert decorated["cache_similarity"] == 1.0
    assert decorated["run_id"] == "run-live-999"
    # decorate_cached_result must not mutate the stored result in place
    assert "cache_hit" not in hit["result"]
