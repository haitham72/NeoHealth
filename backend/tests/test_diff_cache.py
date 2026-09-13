"""Tests for the diff_cache table schema and app.core.diff_cache's dual-layer
(Redis L1 exact-key + Postgres L2 exact-key) lookup/store behavior, plus route-level
coverage proving a cache hit skips chat_completion entirely (POST /diff-followup).

Unlike answer_cache, this cache is exact-key only -- the question is already anchored
to a specific (current_document_id, previous_document_id) pair, so there is no
embedding/semantic layer at all. Redis is faked via the `redis_conn` fixture
(tests/conftest.py), which patches both answer_cache.py's and diff_cache.py's
_get_redis() to the same fake client.
"""
from unittest.mock import MagicMock

import pytest

from app.api.routers import diff as diff_router
from app.api.schemas.diff import DiffFollowupRequest
from app.core.diff_cache import lookup_diff_cache, store_diff_cache
from tests.conftest import fake_request, seed_document


@pytest.fixture(autouse=True)
def _clean_diff_cache(conn):
    """diff_cache has FK columns (current_document_id, previous_document_id) into
    documents, so TRUNCATE documents ... CASCADE already clears diff_cache rows too --
    confirmed directly against the test DB rather than assumed (TRUNCATE's CASCADE
    keyword truncates any table with an FK into the named table, independent of that
    FK's own ON DELETE behavior). No separate `TRUNCATE diff_cache` is needed.

    Writes in this file commit directly (store_diff_cache commits its own writes, same
    as store_answer_cache), so this must run outside the `conn` fixture's usual
    rollback-on-teardown, both before AND after each test -- same rationale as
    test_answer_cache.py's `_clean_answer_cache`."""
    def _reset():
        with conn.cursor() as cur:
            cur.execute("TRUNCATE documents RESTART IDENTITY CASCADE")
        conn.commit()

    _reset()
    yield
    _reset()


SAMPLE_RESULT = {
    "available": True,
    "previous_version": "3",
    "previous_effective_date": "2024-01-01",
    "explanation": "The renewal period changed from 2 years to 3 years.",
}


def _seed_pair(conn) -> tuple[int, int]:
    """A current (in-force) doc and a superseded previous version of the same doc_code,
    matching find_previous_version()'s expected shape."""
    current_id = seed_document(conn, doc_code="DHA/HRS/HPSD/ST-14", version="4", superseded=False)
    previous_id = seed_document(
        conn, doc_code="DHA/HRS/HPSD/ST-14", version="3", superseded=True,
        effective_date="2024-01-01", sha256="sha-diff-prev",
    )
    return current_id, previous_id


def test_diff_cache_table_exists(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'diff_cache'
            """
        )
        assert cur.fetchone() is not None


# --- diff_cache.py unit tests -------------------------------------------------------


def test_diff_lookup_hit_same_docs_and_question(conn, redis_conn):
    current_id, previous_id = _seed_pair(conn)
    question = "What changed about license renewal?"
    store_diff_cache(conn, current_id, previous_id, question, SAMPLE_RESULT)

    hit = lookup_diff_cache(conn, current_id, previous_id, question)

    assert hit is not None
    assert hit["result"] == SAMPLE_RESULT
    assert hit["question_raw"] == question


def test_diff_lookup_hit_ignores_case_and_whitespace(conn, redis_conn):
    """Redis key is derived from normalize_question(), same as answer_cache -- a
    differently-cased/spaced repeat of the same question must still hit."""
    current_id, previous_id = _seed_pair(conn)
    store_diff_cache(conn, current_id, previous_id, "What changed about license renewal?", SAMPLE_RESULT)

    hit = lookup_diff_cache(conn, current_id, previous_id, "  what CHANGED about   license renewal?  ")

    assert hit is not None
    assert hit["result"] == SAMPLE_RESULT


def test_diff_lookup_miss_different_question(conn, redis_conn):
    current_id, previous_id = _seed_pair(conn)
    store_diff_cache(conn, current_id, previous_id, "What changed about license renewal?", SAMPLE_RESULT)

    hit = lookup_diff_cache(conn, current_id, previous_id, "What changed about staffing ratios?")

    assert hit is None


def test_diff_lookup_miss_different_document_pair(conn, redis_conn):
    """Same question text, different document pair -- must not leak across pairs."""
    current_id, previous_id = _seed_pair(conn)
    other_current_id = seed_document(conn, doc_code="DHA/OTHER/CODE", version="2", sha256="sha-other-current")
    question = "What changed about license renewal?"
    store_diff_cache(conn, current_id, previous_id, question, SAMPLE_RESULT)

    assert lookup_diff_cache(conn, other_current_id, previous_id, question) is None
    assert lookup_diff_cache(conn, current_id, other_current_id, question) is None


def test_diff_lookup_miss_empty_cache(conn, redis_conn):
    current_id, previous_id = _seed_pair(conn)

    assert lookup_diff_cache(conn, current_id, previous_id, "anything") is None
    assert redis_conn.dbsize() == 0


def test_diff_postgres_hit_backfills_redis(conn, redis_conn):
    """Redis is faked via redis_conn, so a raw Postgres-only insert (bypassing
    store_diff_cache's Redis write) then a lookup must still hit via L2 and backfill
    L1, exactly like answer_cache's Postgres-backfill behavior."""
    current_id, previous_id = _seed_pair(conn)
    question = "What changed about license renewal?"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO diff_cache (
                current_document_id, previous_document_id, question_normalized,
                question_raw, result_json
            )
            VALUES (%s, %s, %s, %s, %s::jsonb)
            """,
            (current_id, previous_id, "what changed about license renewal?", question, '{"available": true, "explanation": "x"}'),
        )
    conn.commit()
    assert redis_conn.dbsize() == 0

    hit = lookup_diff_cache(conn, current_id, previous_id, question)
    assert hit is not None
    assert hit["result"]["explanation"] == "x"

    # Backfilled -- a second lookup after clearing Postgres would still hit via Redis.
    assert redis_conn.dbsize() == 1


def test_store_diff_cache_upserts_on_conflict(conn, redis_conn):
    """A second store for the same (current_id, previous_id, question) must update the
    existing row (per the UNIQUE constraint + ON CONFLICT), not create a duplicate or
    raise an IntegrityError."""
    current_id, previous_id = _seed_pair(conn)
    question = "What changed about license renewal?"
    store_diff_cache(conn, current_id, previous_id, question, SAMPLE_RESULT)

    updated_result = {**SAMPLE_RESULT, "explanation": "Updated explanation."}
    store_diff_cache(conn, current_id, previous_id, question, updated_result)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM diff_cache")
        assert cur.fetchone()[0] == 1

    hit = lookup_diff_cache(conn, current_id, previous_id, question)
    assert hit["result"]["explanation"] == "Updated explanation."


# --- route-level tests: POST /diff-followup -----------------------------------------


@pytest.fixture
def diff_route_env(conn, redis_conn, monkeypatch):
    """Points the diff router at the shared test connection (same convention as
    test_api_tier.py's corpus_router monkeypatching) and spies on chat_completion so
    tests can assert it was, or was not, called."""
    monkeypatch.setattr(diff_router, "get_connection", lambda: conn)
    monkeypatch.setattr(diff_router, "release_connection", lambda c: None)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Fresh explanation from the LLM."))]
    spy = MagicMock(return_value=(mock_response, "gpt-4o-mini"))
    monkeypatch.setattr(diff_router, "chat_completion", spy)
    return spy


def _seed_pair_with_text(conn) -> tuple[int, int]:
    current_id, previous_id = _seed_pair(conn)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chunks (document_id, page, page_start, page_end, text, embedding, tsv) "
            "VALUES (%s, 1, 1, 1, %s, %s, to_tsvector('english', %s))",
            (current_id, "Current version text.", [0.0] * 1536, "Current version text."),
        )
        cur.execute(
            "INSERT INTO chunks (document_id, page, page_start, page_end, text, embedding, tsv) "
            "VALUES (%s, 1, 1, 1, %s, %s, to_tsvector('english', %s))",
            (previous_id, "Previous version text.", [0.0] * 1536, "Previous version text."),
        )
    conn.commit()
    return current_id, previous_id


def test_diff_followup_route_skips_llm_on_cache_hit(diff_route_env, conn):
    current_id, previous_id = _seed_pair_with_text(conn)
    req = DiffFollowupRequest(
        doc_code="DHA/HRS/HPSD/ST-14", current_document_id=current_id,
        cited_text="some cited text", cited_page=1, question="What changed?",
    )

    first = diff_router.diff_followup(fake_request(), req)
    assert first["available"] is True
    diff_route_env.assert_called_once()

    second = diff_router.diff_followup(fake_request(), req)
    assert second == first
    diff_route_env.assert_called_once()  # still just once -- second call was a cache hit


def test_diff_followup_route_calls_llm_on_miss(diff_route_env, conn):
    current_id, previous_id = _seed_pair_with_text(conn)
    req = DiffFollowupRequest(
        doc_code="DHA/HRS/HPSD/ST-14", current_document_id=current_id,
        cited_text="some cited text", cited_page=1, question="What changed?",
    )

    result = diff_router.diff_followup(fake_request(), req)

    assert result["available"] is True
    assert result["explanation"] == "Fresh explanation from the LLM."
    diff_route_env.assert_called_once()


def test_diff_followup_route_no_previous_version_never_cached(diff_route_env, conn):
    """No superseded sibling exists -- the route must return early without ever
    touching chat_completion or writing a cache row."""
    current_id = seed_document(conn, doc_code="DHA/SOLO/CODE", version="1", superseded=False)
    req = DiffFollowupRequest(
        doc_code="DHA/SOLO/CODE", current_document_id=current_id,
        cited_text="x", cited_page=1, question="What changed?",
    )

    result = diff_router.diff_followup(fake_request(), req)

    assert result["available"] is False
    diff_route_env.assert_not_called()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM diff_cache")
        assert cur.fetchone()[0] == 0
