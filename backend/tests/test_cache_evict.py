"""Tests for the per-result "Remove from cache" flow: signed token round-trip,
tamper/expiry rejection, and eviction actually forcing a miss on the next lookup.

Covers all three caches the UI exposes the control for (answer / diff / cross-check),
including the semantic-hit case where the served Postgres row holds a DIFFERENT
question than the one asked -- eviction there must remove that matched row id, not
just the asked question's exact key.
"""
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from app.api.routers import cache as cache_router
from app.api.schemas.cache import CacheEvictRequest
from app.core import cache_evict
from app.core.answer_cache import evict_answer_cache, lookup_answer_cache, store_answer_cache
from app.core.cross_check_cache import (
    evict_cross_check_cache,
    lookup_cross_check_cache,
    store_cross_check_cache,
)
from app.core.diff_cache import evict_diff_cache, lookup_diff_cache, store_diff_cache
from tests.conftest import QUERY_VEC, fake_request, seed_document

RESULT = {"abstained": False, "answer": "Stub answer."}


@pytest.fixture(autouse=True)
def _clean_cache_state(conn):
    def _reset():
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE answer_cache, diff_cache, cross_check_cache, documents "
                "RESTART IDENTITY CASCADE"
            )
        conn.commit()

    _reset()
    yield
    _reset()


@pytest.fixture
def evict_route_env(conn, redis_conn, monkeypatch):
    """Points the cache router at the shared test connection (same convention as
    test_diff_cache.py's diff_route_env)."""
    monkeypatch.setattr(cache_router, "get_connection", lambda: conn)
    monkeypatch.setattr(cache_router, "release_connection", lambda c: None)


# --- token unit tests (no DB) ------------------------------------------------


def test_token_round_trip():
    token = cache_evict.sign_cache_token("answer", q="q?", s=True, a="", id=7)

    payload = cache_evict.verify_cache_token(token)

    assert payload is not None
    assert payload["k"] == "answer"
    assert payload["q"] == "q?"
    assert payload["id"] == 7


def test_token_tamper_rejected():
    token = cache_evict.sign_cache_token("diff", cur=1, prev=2, q="q")
    body, signature = token.split(".", 1)

    assert cache_evict.verify_cache_token(f"{body}x.{signature}") is None


def test_token_expiry_rejected(monkeypatch):
    monkeypatch.setattr(cache_evict.time, "time", lambda: 1000.0)
    token = cache_evict.sign_cache_token("cross", cur=1, page=2, q="q")
    monkeypatch.setattr(
        cache_evict.time, "time",
        lambda: 1000.0 + cache_evict.TOKEN_TTL_SECONDS + 1,
    )

    assert cache_evict.verify_cache_token(token) is None


# --- eviction semantics ------------------------------------------------------


def test_answer_cache_evict_blocks_next_lookup(conn, redis_conn):
    question = "How long is the license valid?"
    store_answer_cache(conn, question, QUERY_VEC, True, None, RESULT)
    assert lookup_answer_cache(conn, question, QUERY_VEC, True, None) is not None

    assert evict_answer_cache(conn, question, True, None) is True

    assert lookup_answer_cache(conn, question, QUERY_VEC, True, None) is None


def test_answer_cache_evict_removes_semantic_matched_row(conn, redis_conn):
    """A semantic hit serves a row holding a different question; eviction must delete
    that matched row (cache_id) plus the Redis key backfilled for the asked question."""
    stored_question = "What are the licensing validity rules?"
    asked_question = "how long is my license valid?"
    store_answer_cache(conn, stored_question, QUERY_VEC, True, None, RESULT)
    hit = lookup_answer_cache(conn, asked_question, QUERY_VEC, True, None)
    assert hit is not None and hit["cache_id"] is not None

    assert evict_answer_cache(conn, asked_question, True, None, hit["cache_id"]) is True

    assert lookup_answer_cache(conn, asked_question, QUERY_VEC, True, None) is None
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM answer_cache")
        assert cur.fetchone()[0] == 0


def test_diff_cache_evict_blocks_next_lookup(conn, redis_conn):
    current_id = seed_document(conn, doc_code="DHA/EVICT/01", version="2", superseded=False)
    previous_id = seed_document(conn, doc_code="DHA/EVICT/01", version="1", superseded=True)
    result = {"available": True, "explanation": "Something changed."}
    store_diff_cache(conn, current_id, previous_id, "What changed?", result)
    assert lookup_diff_cache(conn, current_id, previous_id, "What changed?") is not None

    assert evict_diff_cache(conn, current_id, previous_id, "What changed?") is True

    assert lookup_diff_cache(conn, current_id, previous_id, "What changed?") is None


def test_cross_check_cache_evict_blocks_next_lookup(conn, redis_conn):
    current_id = seed_document(conn, doc_code="RESEARCH/EVICT", version="1", superseded=False)
    result = {"available": True, "explanation": "Related.", "documents": []}
    store_cross_check_cache(conn, current_id, 3, "How does this relate?", result)
    assert lookup_cross_check_cache(conn, current_id, 3, "How does this relate?") is not None

    assert evict_cross_check_cache(conn, current_id, 3, "How does this relate?") is True

    assert lookup_cross_check_cache(conn, current_id, 3, "How does this relate?") is None


# --- route ---------------------------------------------------------------


def test_evict_route_rejects_bad_token(evict_route_env):
    with pytest.raises(HTTPException) as exc:
        cache_router.evict_cache(fake_request(), CacheEvictRequest(token="not.a.token"))

    assert exc.value.status_code == 400


def test_evict_route_evicts_answer_entry(evict_route_env, conn):
    question = "How long is the license valid?"
    store_answer_cache(conn, question, QUERY_VEC, True, None, RESULT)
    token = cache_evict.sign_cache_token("answer", q=question, s=True, a="", id=None)

    response = cache_router.evict_cache(fake_request(), CacheEvictRequest(token=token))

    assert response == {"evicted": True, "kind": "answer"}
    assert lookup_answer_cache(conn, question, QUERY_VEC, True, None) is None


def test_answer_flow_mints_token_that_evicts(evict_route_env, conn, redis_conn, monkeypatch):
    """Closes the loop: a real cache hit through answer_question carries a token, and
    that token evicts the very entry that served it."""
    from app.core import retrieval

    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    monkeypatch.setattr(
        retrieval, "generate_answer",
        MagicMock(side_effect=AssertionError("cache hit must not reach generation")),
    )
    question = "How long is the license valid?"
    store_answer_cache(conn, question, QUERY_VEC, True, None, RESULT)

    result = retrieval.answer_question(conn, question, superseded_filter=True)

    assert result["cache_hit"] is True
    token = result.get("cache_token")
    assert token

    response = cache_router.evict_cache(fake_request(), CacheEvictRequest(token=token))
    assert response == {"evicted": True, "kind": "answer"}
    assert lookup_answer_cache(conn, question, QUERY_VEC, True, None) is None
