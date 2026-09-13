"""Tests for the answer_cache table schema and app.core.answer_cache's dual-layer
(Redis L1 exact-key + Postgres L2 semantic) lookup/store behavior.

Redis is faked via the `redis_conn` fixture (tests/conftest.py) rather than requiring
a real Redis instance -- see that fixture's docstring. Embeddings are fixed vectors
from tests/conftest.py's `vec()`/`QUERY_VEC`/`orthogonal_vec()` helpers, never a live
OpenAI call -- semantic similarity between "questions" is controlled purely by which
fixed vector each test passes as query_vec, exactly like the rest of this test suite
controls retrieval relevance (see stub_llm's docstring).
"""
from unittest.mock import MagicMock

import pytest
import redis as redis_module

from app.core import answer_cache, retrieval
from app.core.answer_cache import (
    MATCH_EXACT,
    MATCH_SEMANTIC,
    decorate_cached_result,
    lookup_answer_cache,
    normalize_question,
    store_answer_cache,
)
from app.core.config import CACHE_HIT_THRESHOLD
from tests.conftest import QUERY_VEC, orthogonal_vec, seed_official_doc


@pytest.fixture(autouse=True)
def _clean_answer_cache(conn):
    """store_answer_cache() commits its own Postgres writes (intentionally -- a cache
    entry must survive regardless of what the caller's own read-transaction later does),
    which means the `conn` fixture's usual rollback-on-teardown can't undo them. Without
    this, one test's cached row would leak into the next test's row-count assertions in
    the same real test_regulense database.

    Task 4's tests additionally seed real documents/chunks and then exercise the actual
    answer_question()/answer_question_stream() pipeline on the SAME connection, and that
    pipeline's cache-store call also commits -- which permanently commits whatever else
    was inserted earlier in that same transaction (the seeded documents/chunks), not
    just the answer_cache row. Truncating `documents` (ON DELETE CASCADE takes `chunks`
    and `diff_cache` with it) alongside `answer_cache`, both before AND after each test
    in this file, keeps that leakage from reaching other test files' row-count
    assertions (e.g. test_api_tier.py's corpus-stats test) regardless of run order."""
    def _reset():
        with conn.cursor() as cur:
            cur.execute("TRUNCATE answer_cache RESTART IDENTITY")
            cur.execute("TRUNCATE documents RESTART IDENTITY CASCADE")
        conn.commit()

    _reset()
    yield
    _reset()

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


# ---------------------------------------------------------------------------
# Task 4: wiring the cache into retrieval.answer_question / answer_question_stream
# so a hit genuinely short-circuits the pipeline before any OpenAI call.
#
# spy_llm below deliberately mirrors conftest.py's stub_llm (embed() always returns
# QUERY_VEC) but as call-counting MagicMocks -- the whole point of these tests is
# proving generate_answer/embed were, or were NOT, invoked, which a plain lambda
# stub can't tell you.
# ---------------------------------------------------------------------------


class _Spies:
    def __init__(self, embed, generate_answer):
        self.embed = embed
        self.generate_answer = generate_answer


@pytest.fixture
def spy_llm(monkeypatch):
    embed_spy = MagicMock(side_effect=lambda text: QUERY_VEC)
    generate_spy = MagicMock(return_value=("Stub answer text.", retrieval.CHAT_MODEL))
    monkeypatch.setattr(retrieval, "embed", embed_spy)
    monkeypatch.setattr(retrieval, "generate_answer", generate_spy)
    return _Spies(embed_spy, generate_spy)


def _run_stream(gen) -> tuple[list[dict], dict]:
    """Drains an answer_question_stream() generator and returns (all_events, the
    "done" step's result dict)."""
    events = list(gen)
    for event in events:
        if event.get("step") == "done":
            return events, event["result"]
    raise AssertionError(f"stream never yielded a 'done' step; got: {events}")


RATIO_QUESTION = "What is the nurse-to-patient ratio?"
RATIO_PARAPHRASE = "What's the required nurse to patient ratio?"


# --- answer_question (non-streaming) ---------------------------------------------


def test_answer_question_redis_hit_skips_llm(conn, redis_conn, spy_llm):
    store_answer_cache(conn, RATIO_QUESTION, QUERY_VEC, False, None, SAMPLE_RESULT)

    result = retrieval.answer_question(conn, RATIO_QUESTION, superseded_filter=False, authority_filter=None)

    assert result["cache_hit"] is True
    assert result["cache_layer"] == "redis"
    assert result["answer"] == SAMPLE_RESULT["answer"]
    spy_llm.embed.assert_not_called()
    spy_llm.generate_answer.assert_not_called()


def test_answer_question_postgres_hit_skips_llm_and_backfills_redis(conn, redis_conn, spy_llm):
    store_answer_cache(conn, RATIO_QUESTION, QUERY_VEC, False, None, SAMPLE_RESULT)

    result = retrieval.answer_question(conn, RATIO_PARAPHRASE, superseded_filter=False, authority_filter=None)

    assert result["cache_hit"] is True
    assert result["cache_layer"] == "postgres"
    assert result["cache_similarity"] >= CACHE_HIT_THRESHOLD
    assert result["answer"] == SAMPLE_RESULT["answer"]
    spy_llm.generate_answer.assert_not_called()
    # Redis-only probe missed on the paraphrase's own key -- embed() was needed once
    # to run the full (Redis-then-Postgres) lookup.
    spy_llm.embed.assert_called_once()

    # The Postgres hit must have backfilled Redis under the paraphrase's own key.
    backfilled = lookup_answer_cache(conn, RATIO_PARAPHRASE, None, False, None)
    assert backfilled is not None
    assert backfilled["cache_layer"] == "redis"


def test_answer_question_full_miss_calls_llm_and_stores(conn, redis_conn, spy_llm):
    seed_official_doc(conn, score=0.6)

    result = retrieval.answer_question(
        conn, "What are the telehealth standards?", superseded_filter=False, provider="local",
    )

    assert result["abstained"] is False
    assert "cache_hit" not in result
    spy_llm.generate_answer.assert_called_once()
    spy_llm.embed.assert_called_once()

    # A successful non-abstained answer must have been stored -- a fresh Redis-only
    # probe for the same question/filters now hits without touching the LLM again.
    stored = lookup_answer_cache(conn, "What are the telehealth standards?", None, False, None)
    assert stored is not None
    assert stored["cache_layer"] == "redis"


def test_answer_question_filter_mismatch_calls_llm(conn, redis_conn, spy_llm):
    """A cache entry under one filter combo must not leak into a different one --
    the mismatch must fall through to a real (LLM) answer, not an empty/None result."""
    question = "What are the telehealth standards?"
    seed_official_doc(conn, score=0.6)
    store_answer_cache(conn, question, QUERY_VEC, False, None, SAMPLE_RESULT)

    result = retrieval.answer_question(conn, question, superseded_filter=True, provider="local")

    assert result["abstained"] is False
    assert "cache_hit" not in result
    spy_llm.generate_answer.assert_called_once()


def test_answer_question_history_present_still_uses_cache(conn, redis_conn, spy_llm):
    # Reversed by explicit user request: history no longer disqualifies caching, since
    # retrieval never reads history anyway (see retrieval.py's cache-gate docstrings) --
    # a history-bearing follow-up should hit an existing cache entry just like a fresh ask.
    question = "What are the telehealth standards?"
    seed_official_doc(conn, score=0.6)
    store_answer_cache(conn, question, QUERY_VEC, False, None, SAMPLE_RESULT)

    result = retrieval.answer_question(
        conn, question, superseded_filter=False, provider="local",
        history=[{"role": "user", "content": "earlier turn"}],
    )

    assert result["abstained"] is False
    assert result["cache_hit"] is True
    spy_llm.generate_answer.assert_not_called()
    spy_llm.embed.assert_not_called()


# --- answer_question_stream --------------------------------------------------------


def test_stream_redis_hit_skips_llm(conn, redis_conn, spy_llm):
    store_answer_cache(conn, RATIO_QUESTION, QUERY_VEC, False, None, SAMPLE_RESULT)

    events, result = _run_stream(
        retrieval.answer_question_stream(conn, RATIO_QUESTION, superseded_filter=False, authority_filter=None)
    )

    assert result["cache_hit"] is True
    assert result["cache_layer"] == "redis"
    steps = [e["step"] for e in events]
    assert "cache_hit" in steps
    assert "embedding_query" not in steps
    assert "searching_sources" not in steps
    spy_llm.embed.assert_not_called()
    spy_llm.generate_answer.assert_not_called()


def test_stream_postgres_hit_skips_llm_and_backfills_redis(conn, redis_conn, spy_llm):
    store_answer_cache(conn, RATIO_QUESTION, QUERY_VEC, False, None, SAMPLE_RESULT)

    events, result = _run_stream(
        retrieval.answer_question_stream(conn, RATIO_PARAPHRASE, superseded_filter=False, authority_filter=None)
    )

    assert result["cache_hit"] is True
    assert result["cache_layer"] == "postgres"
    steps = [e["step"] for e in events]
    assert "searching_sources" not in steps
    spy_llm.generate_answer.assert_not_called()
    spy_llm.embed.assert_called_once()

    backfilled = lookup_answer_cache(conn, RATIO_PARAPHRASE, None, False, None)
    assert backfilled is not None
    assert backfilled["cache_layer"] == "redis"


def test_stream_full_miss_calls_llm_and_stores(conn, redis_conn, spy_llm):
    seed_official_doc(conn, score=0.6)

    events, result = _run_stream(
        retrieval.answer_question_stream(
            conn, "What are the telehealth standards?", superseded_filter=False, provider="local",
        )
    )

    assert result["abstained"] is False
    assert "cache_hit" not in result
    steps = [e["step"] for e in events]
    assert "cache_miss" in steps
    spy_llm.generate_answer.assert_called_once()
    spy_llm.embed.assert_called_once()

    stored = lookup_answer_cache(conn, "What are the telehealth standards?", None, False, None)
    assert stored is not None
    assert stored["cache_layer"] == "redis"


def test_stream_filter_mismatch_calls_llm(conn, redis_conn, spy_llm):
    question = "What are the telehealth standards?"
    seed_official_doc(conn, score=0.6)
    store_answer_cache(conn, question, QUERY_VEC, False, None, SAMPLE_RESULT)

    events, result = _run_stream(
        retrieval.answer_question_stream(conn, question, superseded_filter=True, provider="local")
    )

    assert result["abstained"] is False
    assert "cache_hit" not in result
    spy_llm.generate_answer.assert_called_once()


def test_stream_history_present_still_uses_cache(conn, redis_conn, spy_llm):
    # Reversed by explicit user request: see test_answer_question_history_present_still_uses_cache.
    question = "What are the telehealth standards?"
    seed_official_doc(conn, score=0.6)
    store_answer_cache(conn, question, QUERY_VEC, False, None, SAMPLE_RESULT)

    events, result = _run_stream(
        retrieval.answer_question_stream(
            conn, question, superseded_filter=False, provider="local",
            history=[{"role": "user", "content": "earlier turn"}],
        )
    )

    assert result["abstained"] is False
    assert result["cache_hit"] is True
    steps = [e["step"] for e in events]
    assert "checking_cache" in steps
    assert "cache_hit" in steps
    spy_llm.generate_answer.assert_not_called()


# --- cache-read failure must degrade to a normal answer, never propagate -----------
#
# Code review finding on the first version of this task's wiring: lookup_answer_cache
# was called bare at all four call sites (stream x2, non-stream x2) -- store_answer_cache
# was wrapped in try/except per the brief's Step 9, but the read side had no equivalent
# guard, so a transient Redis/Postgres failure (lock timeout, schema drift, whatever)
# during the cache gate would propagate all the way out of answer_question()/
# answer_question_stream() and surface as a 500 to the user, instead of degrading to a
# plain cache miss. Fixed via _safe_lookup_answer_cache(), a shared wrapper around
# lookup_answer_cache() that catches, logs, and returns None. These two tests are the
# regression coverage for that fix -- monkeypatching retrieval.lookup_answer_cache
# itself (the name _safe_lookup_answer_cache calls) to raise on every invocation, then
# confirming the pipeline still produces a normal, non-abstained, LLM-generated answer.


def test_answer_question_survives_cache_lookup_failure(conn, redis_conn, spy_llm, monkeypatch):
    seed_official_doc(conn, score=0.6)
    # _safe_lookup_answer_cache now rolls back `conn` on ANY failure here (see the
    # real-Postgres-failure test below for why) -- a real Postgres ROLLBACK undoes
    # everything in the current transaction, not just the statement that failed, so
    # this test's own uncommitted seed data must be committed first or the rollback
    # below would silently wipe it out too.
    conn.commit()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated Redis/Postgres failure")

    monkeypatch.setattr(retrieval, "lookup_answer_cache", _boom)

    result = retrieval.answer_question(
        conn, "What are the telehealth standards?", superseded_filter=False, provider="local",
    )

    assert result["abstained"] is False
    assert "cache_hit" not in result
    spy_llm.generate_answer.assert_called_once()
    spy_llm.embed.assert_called_once()

    # The store side is unaffected by a *read* failure -- confirms the pipeline ran
    # all the way through rather than aborting partway.
    stored = lookup_answer_cache(conn, "What are the telehealth standards?", None, False, None)
    assert stored is not None


def test_stream_survives_cache_lookup_failure(conn, redis_conn, spy_llm, monkeypatch):
    seed_official_doc(conn, score=0.6)
    # See test_answer_question_survives_cache_lookup_failure's comment -- the rollback
    # _safe_lookup_answer_cache now performs would otherwise wipe out this uncommitted
    # seed data too.
    conn.commit()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated Redis/Postgres failure")

    monkeypatch.setattr(retrieval, "lookup_answer_cache", _boom)

    events, result = _run_stream(
        retrieval.answer_question_stream(
            conn, "What are the telehealth standards?", superseded_filter=False, provider="local",
        )
    )

    assert result["abstained"] is False
    assert "cache_hit" not in result
    spy_llm.generate_answer.assert_called_once()
    spy_llm.embed.assert_called_once()

    stored = lookup_answer_cache(conn, "What are the telehealth standards?", None, False, None)
    assert stored is not None


# --- real Postgres-level failure must not leave the connection aborted -------------
#
# Code review finding on the fix above: monkeypatching the whole lookup_answer_cache
# function to raise (the two tests above) only proves graceful degradation for a pure
# in-Python exception that never touches the connection's transaction state. It does
# NOT prove the fix handles a genuine Postgres-level failure mid-lookup (e.g. a lock
# timeout during _postgres_lookup's SELECT or its hit_count UPDATE/commit), which
# leaves `conn` in Postgres's aborted-transaction state ("current transaction is
# aborted, commands ignored until end of transaction block"). Catching that and
# returning None is not enough on its own: the very next statement on that same
# connection within this request (semantic_search, called right after this "graceful"
# fallback) would itself fail against the still-aborted transaction -- which is what
# would actually surface as a 500 to the user. (The connection pool's own _putconn()
# already rolls back a non-idle connection -- or closes it -- before it can reach a
# later, unrelated request, so this is a within-request concern, not a cross-request
# poisoning risk.)
#
# This test triggers a REAL SQL error against the real test Postgres connection (not a
# mock of the whole function) to leave `conn` genuinely aborted, then confirms both
# that the pipeline still produces a normal answer AND that a subsequent real query on
# the SAME conn object succeeds afterward -- proving _safe_lookup_answer_cache's
# rollback actually cleared the aborted state, not just that the exception was
# swallowed.


def test_answer_question_survives_real_postgres_failure_and_rolls_back(conn, spy_llm):
    """No redis_conn fixture here on purpose -- REDIS_URL is unset in the test env, so
    _get_redis() naturally returns None (see test_lookup_miss_below_threshold's
    docstring for the same pattern elsewhere in this file) and the cache gate's second
    probe (with a real query_vec) falls through to a genuine Postgres SELECT, which is
    exactly the statement that needs to observe the aborted transaction."""
    seed_official_doc(conn, score=0.6)
    # Commit the seed before deliberately aborting the transaction below -- otherwise
    # the rollback that _safe_lookup_answer_cache performs to recover would also wipe
    # out this test's own uncommitted seed data (a real Postgres ROLLBACK undoes
    # everything in the current transaction, not just the statement that failed).
    conn.commit()

    # Simulate a real, already-aborted transaction -- a genuine SQL error against the
    # real test Postgres, not a mock of lookup_answer_cache. This is exactly the state
    # a real lock-timeout/etc. failure inside _postgres_lookup would leave `conn` in.
    with pytest.raises(Exception):
        with conn.cursor() as cur:
            cur.execute("SELECT 1/0")

    result = retrieval.answer_question(
        conn, "What are the telehealth standards?", superseded_filter=False, provider="local",
    )

    assert result["abstained"] is False
    assert "cache_hit" not in result
    spy_llm.generate_answer.assert_called_once()

    # The real proof: a subsequent real query on the SAME conn object must succeed --
    # if _safe_lookup_answer_cache had only caught-and-logged without rolling back,
    # this would still raise InFailedSqlTransaction.
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)


# --- Redis connect-failure cooldown must lapse, not latch permanently --------------
#
# Final-review finding: _get_redis() used to set a permanent `_redis_failed = True`
# latch on any connect failure -- once tripped, Redis stayed disabled for the rest of
# the process's lifetime even if it recovered seconds later (e.g. a cold-start TLS
# hiccup against Upstash during the boot warm-load, the very first thing that touches
# Redis). Replaced with a timestamped cooldown (_redis_degraded_until), mirroring
# retrieval.py's OPENAI_COOLDOWN_SECONDS/_openai_degraded_until idiom exactly. This
# test proves the cooldown actually lapses (time.time() is monkeypatched -- via the
# module's own `time` binding -- rather than sleeping for real) and that a genuinely
# recovered Redis starts working again afterward without a process restart.


def test_get_redis_cooldown_resets_after_window_elapses(monkeypatch):
    monkeypatch.setattr(answer_cache, "REDIS_URL", "redis://fake-host:6379/0")
    monkeypatch.setattr(answer_cache, "_redis_client", None)
    monkeypatch.setattr(answer_cache, "_redis_degraded_until", 0.0)

    class _FakeTime:
        now = 1_000_000.0

        def time(self):
            return self.now

    fake_time = _FakeTime()
    monkeypatch.setattr(answer_cache, "time", fake_time)

    def _boom(*args, **kwargs):
        raise ConnectionError("simulated connect failure")

    monkeypatch.setattr(redis_module, "from_url", _boom)

    # First call: the connect attempt fails and trips the cooldown.
    assert answer_cache._get_redis() is None
    assert answer_cache._redis_is_degraded() is True

    # Still inside the cooldown window: _get_redis() must short-circuit on the
    # timestamp check alone -- redis.from_url stays patched to _boom for the whole
    # test, so a real reconnect attempt here would raise instead of cleanly
    # returning None if the latch weren't actually being honored.
    fake_time.now += answer_cache.REDIS_COOLDOWN_SECONDS - 1
    assert answer_cache._get_redis() is None

    # Past the cooldown window: the degraded state must have lifted on its own, and a
    # now-healthy reconnect attempt succeeds -- proving recovery needs no process
    # restart, unlike the old permanent `_redis_failed` latch.
    fake_time.now += 2
    fake_client = MagicMock()
    monkeypatch.setattr(redis_module, "from_url", lambda *a, **kw: fake_client)

    assert answer_cache._redis_is_degraded() is False
    client = answer_cache._get_redis()
    assert client is fake_client
    fake_client.ping.assert_called_once()
