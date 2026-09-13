"""Provider-routing tests for POST /cross-check-regulation.

Mirrors test_diff_cache.py's diff_route_env convention: the router is pointed at the
shared test connection and chat_completion is spied on, so tests can assert which LLM
edge a request actually used. Covers the same reported bug as the diff-followup local
fix -- provider="local" called chat_completion() directly (OpenAI->NaraRouter only)
and failed without keys, instead of routing to LM Studio's local_client.
"""
from unittest.mock import MagicMock

import pytest

from app.api.routers import cross_check as cross_check_router
from app.api.schemas.cross_check import CrossCheckRegulationRequest
from tests.conftest import fake_request, seed_document


@pytest.fixture(autouse=True)
def _clean_cross_check_cache(conn):
    """Store paths commit (increment_daily_usage, _postgres_store), so rows survive
    the `conn` fixture's teardown rollback and would leak into later tests and
    re-runs (same fixed question strings + RESTART IDENTITY doc ids = false hits).
    Truncate before AND after each test -- same rationale as test_diff_cache.py's
    `_clean_diff_cache`. CASCADE also clears cross_check_cache/diff_cache rows via
    their FKs into documents."""
    def _reset():
        with conn.cursor() as cur:
            cur.execute("TRUNCATE documents RESTART IDENTITY CASCADE")
        conn.commit()

    _reset()
    yield
    _reset()


@pytest.fixture
def cross_check_route_env(conn, redis_conn, monkeypatch):
    """Points the cross-check router at the shared test connection, stubs the
    retrieval edges (embed + official-doc lookup), and spies on chat_completion."""
    monkeypatch.setattr(cross_check_router, "get_connection", lambda: conn)
    monkeypatch.setattr(cross_check_router, "release_connection", lambda c: None)
    monkeypatch.setattr(cross_check_router, "embed", lambda text: [0.0] * 1536)
    monkeypatch.setattr(
        cross_check_router,
        "find_related_official_docs",
        lambda conn, query_vec, k=2: [
            {
                "doc_code": "DHA/HRS/HPSD/ST-14",
                "title": "Standards for Telehealth Services",
                "version": "4",
                "authority": "Dubai Health Authority",
                "page": 1,
                "text": "Official standard text.",
            }
        ],
    )

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Fresh cross-check from the LLM."))]
    spy = MagicMock(return_value=(mock_response, "gpt-4o-mini"))
    monkeypatch.setattr(cross_check_router, "chat_completion", spy)
    return spy


def _mock_local_client(monkeypatch, content: str = "Local cross-check."):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=content))]
    fake_local = MagicMock()
    fake_local.chat.completions.create.return_value = mock_response
    monkeypatch.setattr(cross_check_router, "local_client", fake_local)
    return fake_local.chat.completions.create


def _seed_research_doc(conn) -> int:
    return seed_document(
        conn, doc_code="RESEARCH/ELHAYEK-01", title="Telepsychiatry in the Arab World",
        version="1", authority="Asian Journal of Psychiatry", tier="research",
        effective_date="2021-06-01",
    )


def test_cross_check_local_provider_uses_local_client(cross_check_route_env, conn, monkeypatch):
    """provider="local" must call local_client with the requested model and never
    touch chat_completion (the OpenAI->NaraRouter path)."""
    create_mock = _mock_local_client(monkeypatch)
    research_id = _seed_research_doc(conn)
    req = CrossCheckRegulationRequest(
        doc_code="RESEARCH/ELHAYEK-01", current_document_id=research_id,
        cited_text="research excerpt", cited_page=5, question="How does this relate?",
        provider="local", model="test-local-model",
    )

    result = cross_check_router.cross_check_regulation(fake_request(), req)

    assert result["available"] is True
    assert result["explanation"] == "Local cross-check."
    create_mock.assert_called_once()
    assert create_mock.call_args.kwargs["model"] == "test-local-model"
    cross_check_route_env.assert_not_called()


def test_cross_check_openai_provider_still_uses_chat_completion(cross_check_route_env, conn, monkeypatch):
    """Default provider="openai" keeps the existing chat_completion path."""
    monkeypatch.setattr(
        cross_check_router, "local_client",
        MagicMock(chat=MagicMock(completions=MagicMock(
            create=MagicMock(side_effect=AssertionError("local_client must not be called"))))),
    )
    research_id = _seed_research_doc(conn)
    req = CrossCheckRegulationRequest(
        doc_code="RESEARCH/ELHAYEK-01", current_document_id=research_id,
        cited_text="research excerpt", cited_page=5, question="How does this relate?",
    )

    result = cross_check_router.cross_check_regulation(fake_request(), req)

    assert result["available"] is True
    assert result["explanation"] == "Fresh cross-check from the LLM."
    cross_check_route_env.assert_called_once()


# --- cross-check cache: miss stores, repeat is a marked hit -----------------------
#
# Same convention as /diff-followup: the second identical call must not touch the
# LLM (or embed) again and must carry cache_hit for the UI's "Served from cache"
# note. Questions are unique per test because store paths commit (same reason
# test_diff_cache.py's fixture truncates rather than relying on rollback).


def test_cross_check_second_call_is_marked_cache_hit(cross_check_route_env, conn, monkeypatch):
    embed_spy = MagicMock(return_value=[0.0] * 1536)
    monkeypatch.setattr(cross_check_router, "embed", embed_spy)
    research_id = _seed_research_doc(conn)

    def _req():
        return CrossCheckRegulationRequest(
            doc_code="RESEARCH/ELHAYEK-01", current_document_id=research_id,
            cited_text="research excerpt", cited_page=5, question="Cache me once?",
        )

    first = cross_check_router.cross_check_regulation(fake_request(), _req())
    assert first["available"] is True
    assert "cache_hit" not in first
    cross_check_route_env.assert_called_once()
    embed_spy.assert_called_once()

    second = cross_check_router.cross_check_regulation(fake_request(), _req())
    assert second["available"] is True
    assert second["explanation"] == first["explanation"]
    assert second["cache_hit"] is True
    cross_check_route_env.assert_called_once()  # still once -- cache hit
    embed_spy.assert_called_once()  # the embed call is skipped on a hit too


def test_cross_check_different_page_is_not_a_hit(cross_check_route_env, conn):
    research_id = _seed_research_doc(conn)

    def _req(page: int):
        return CrossCheckRegulationRequest(
            doc_code="RESEARCH/ELHAYEK-01", current_document_id=research_id,
            cited_text="research excerpt", cited_page=page, question="Page-sensitive?",
        )

    cross_check_router.cross_check_regulation(fake_request(), _req(5))
    result = cross_check_router.cross_check_regulation(fake_request(), _req(6))

    assert result["available"] is True
    assert "cache_hit" not in result  # cited_page is part of the cache key
    assert cross_check_route_env.call_count == 2


def test_cross_check_unavailable_result_is_never_cached(cross_check_route_env, conn, monkeypatch):
    """No related official standard -> available:false must neither call the LLM nor
    write a cache row (mirrors store_cross_check_cache's available-guard)."""
    monkeypatch.setattr(cross_check_router, "find_related_official_docs", lambda conn, qv, k=2: [])
    research_id = _seed_research_doc(conn)
    req = CrossCheckRegulationRequest(
        doc_code="RESEARCH/ELHAYEK-01", current_document_id=research_id,
        cited_text="research excerpt", cited_page=5, question="Nothing related?",
    )

    result = cross_check_router.cross_check_regulation(fake_request(), req)

    assert result["available"] is False
    cross_check_route_env.assert_not_called()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM cross_check_cache WHERE question_raw = %s", ("Nothing related?",))
        assert cur.fetchone()[0] == 0
