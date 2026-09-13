"""Tests for app.core.cache_warmup's unconditional boot warm-load of every Postgres
answer_cache and diff_cache row into Redis, so the first request of a new interview
session is already a Redis hit for every previously-answered question / diff-followup
rather than only the second ask.

Redis is faked via the `redis_conn` fixture (tests/conftest.py), which patches both
answer_cache.py's and diff_cache.py's _get_redis(); the Redis-unavailable test instead
monkeypatches answer_cache._get_redis directly to simulate a total outage, exactly like
test_answer_cache.py's own tests do for the equivalent scenario.
"""
import json

import pytest

from app.core import answer_cache
from app.core.answer_cache import lookup_answer_cache
from app.core.cache_warmup import warm_all_caches
from app.core.diff_cache import lookup_diff_cache
from tests.conftest import seed_document, vec


@pytest.fixture(autouse=True)
def _clean_answer_cache(conn):
    """Same rationale as test_answer_cache.py's fixture of the same name: writes in
    this file commit directly to the shared test_regulense database, outside the
    `conn` fixture's rollback-on-teardown."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE answer_cache RESTART IDENTITY")
    conn.commit()
    yield


@pytest.fixture(autouse=True)
def _clean_diff_cache(conn):
    """diff_cache rows require FK-valid document ids, so isolating them means clearing
    `documents` too. TRUNCATE documents ... CASCADE already clears diff_cache along
    with it (confirmed directly against the test DB; TRUNCATE's CASCADE truncates any
    table with an FK into the named table, independent of that FK's own ON DELETE
    behavior) -- no separate `TRUNCATE diff_cache` needed, same as test_diff_cache.py's
    equivalent fixture."""
    def _reset():
        with conn.cursor() as cur:
            cur.execute("TRUNCATE documents RESTART IDENTITY CASCADE")
        conn.commit()

    _reset()
    yield
    _reset()


def _insert_answer_cache_row(
    conn,
    *,
    question_raw: str,
    superseded_filter: bool = False,
    authority_filter: str | None = None,
    answer_text: str = "An answer.",
) -> None:
    """Inserts directly via SQL rather than through store_answer_cache() -- this
    simulates rows that were written in a *previous* process's lifetime (the whole
    point of a boot warm-load), so going through today's live-write path would be
    testing the wrong thing."""
    result = {"abstained": False, "answer": answer_text}
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO answer_cache (
                question_normalized, question_raw, superseded_filter, authority_filter,
                query_embedding, result_json
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                answer_cache.normalize_question(question_raw),
                question_raw,
                superseded_filter,
                authority_filter,
                vec(1.0),
                json.dumps(result),
            ),
        )
    conn.commit()


def _insert_diff_cache_row(
    conn,
    *,
    current_document_id: int,
    previous_document_id: int,
    question_raw: str,
    explanation: str = "An explanation.",
) -> None:
    """Inserts directly via SQL rather than through store_diff_cache() -- same
    rationale as _insert_answer_cache_row: this simulates a row written in a *previous*
    process's lifetime, which is the whole point of a boot warm-load."""
    result = {
        "available": True,
        "previous_version": "3",
        "previous_effective_date": "2024-01-01",
        "explanation": explanation,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO diff_cache (
                current_document_id, previous_document_id, question_normalized,
                question_raw, result_json
            )
            VALUES (%s, %s, %s, %s, %s::jsonb)
            """,
            (
                current_document_id,
                previous_document_id,
                answer_cache.normalize_question(question_raw),
                question_raw,
                json.dumps(result),
            ),
        )
    conn.commit()


def test_warm_all_caches_loads_every_answer_cache_row(conn, redis_conn):
    """Three rows, not one -- the point of this task is "every row", not "the first
    row". Each must be retrievable afterward via a Redis-only probe (query_vec=None),
    proving the warm-load wrote real, correctly-keyed entries, not just a count."""
    _insert_answer_cache_row(conn, question_raw="What is the nurse-to-patient ratio?", answer_text="Answer one.")
    _insert_answer_cache_row(conn, question_raw="What is the DHA license renewal period?", answer_text="Answer two.")
    _insert_answer_cache_row(
        conn,
        question_raw="What is telehealth eligibility?",
        authority_filter="DHA",
        answer_text="Answer three.",
    )

    result = warm_all_caches(conn)

    assert result == {"ask_rows_loaded": 3, "diff_rows_loaded": 0}

    hit1 = lookup_answer_cache(conn, "What is the nurse-to-patient ratio?", None, False, None)
    assert hit1 is not None
    assert hit1["cache_layer"] == "redis"
    assert hit1["result"]["answer"] == "Answer one."

    hit2 = lookup_answer_cache(conn, "What is the DHA license renewal period?", None, False, None)
    assert hit2 is not None
    assert hit2["result"]["answer"] == "Answer two."

    # Filters are part of the Redis key -- confirm the warm-loaded row landed under
    # its own authority_filter, not the default.
    assert lookup_answer_cache(conn, "What is telehealth eligibility?", None, False, None) is None
    hit3 = lookup_answer_cache(conn, "What is telehealth eligibility?", None, False, "DHA")
    assert hit3 is not None
    assert hit3["result"]["answer"] == "Answer three."


def test_warm_all_caches_loads_diff_cache_rows(conn, redis_conn):
    """A diff_cache row must survive warm-load the same way an answer_cache row does --
    retrievable afterward via lookup_diff_cache (Redis-backed, since warm-load writes
    straight to Redis)."""
    current_id = seed_document(
        conn, doc_code="DHA/HRS/HPSD/ST-14", version="4", superseded=False, sha256="sha-warm-current",
    )
    previous_id = seed_document(
        conn, doc_code="DHA/HRS/HPSD/ST-14", version="3", superseded=True,
        effective_date="2024-01-01", sha256="sha-warm-previous",
    )
    _insert_diff_cache_row(
        conn, current_document_id=current_id, previous_document_id=previous_id,
        question_raw="What changed about license renewal?", explanation="It changed.",
    )

    result = warm_all_caches(conn)

    assert result == {"ask_rows_loaded": 0, "diff_rows_loaded": 1}

    hit = lookup_diff_cache(conn, current_id, previous_id, "What changed about license renewal?")
    assert hit is not None
    assert hit["result"]["explanation"] == "It changed."


def test_warm_all_caches_empty_table_returns_zero(conn, redis_conn):
    result = warm_all_caches(conn)

    assert result == {"ask_rows_loaded": 0, "diff_rows_loaded": 0}


def test_warm_all_caches_survives_redis_unavailable(conn, monkeypatch):
    """A total Redis outage at boot must degrade to a zero count, not a crash -- the
    app still needs to finish booting and serve traffic on Postgres alone."""
    _insert_answer_cache_row(conn, question_raw="What is the nurse-to-patient ratio?")
    monkeypatch.setattr(answer_cache, "_get_redis", lambda: None)

    result = warm_all_caches(conn)

    assert result == {"ask_rows_loaded": 0, "diff_rows_loaded": 0}


class _BoomConnection:
    """Stands in for a connection whose Postgres read itself fails at boot (e.g.
    Postgres unreachable). psycopg2's real connection.cursor is a read-only C-level
    attribute that can't be monkeypatched on an instance, so a minimal fake is the
    simplest way to exercise this path without mutating the shared connection type."""

    def cursor(self, *args, **kwargs):
        raise RuntimeError("boom")


def test_warm_all_caches_survives_postgres_read_failure():
    """Even a broken Postgres read at boot (the SELECT itself raising) must not crash
    startup -- the top-level try/except in warm_all_caches is the last line of defense
    described in the plan, on top of the reused setter's own inner guard."""
    result = warm_all_caches(_BoomConnection())

    assert result == {"ask_rows_loaded": 0, "diff_rows_loaded": 0}
