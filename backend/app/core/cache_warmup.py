"""Eager, unconditional load of every Postgres cache row into Redis at process boot.

Why on top of answer_cache.py's existing reactive Redis population: a live cache write
(store_answer_cache) or a Postgres L2 hit already backfills Redis, but only for the
questions someone happens to ask *this* session. The interviewer's Redis instance is
only live for a few minutes per interview and idle for days or weeks in between --
CACHE_REDIS_TTL_SECONDS (7 days) can itself expire keys across a long enough gap -- so
its state at any given boot can't be trusted. This module makes the very first request
of a new interview session already a Redis hit for every previously-answered question,
not just the second time each one is asked.

diff_cache gets the same treatment as of Task 6: diff_cache.py now owns that table's
Redis-write path, so this module reads every diff_cache row too and warm-loads it
under its own exact-key scheme, reporting a real `diff_rows_loaded` count instead of
the stub 0 from before that module existed.
"""
from __future__ import annotations

import json
import logging

from app.core.answer_cache import write_redis_cache
from app.core.diff_cache import write_redis_cache as write_diff_redis_cache

logger = logging.getLogger(__name__)


def warm_all_caches(conn) -> dict:
    """Reads every row already in Postgres's answer_cache and diff_cache tables (no
    LIMIT, no recency filter -- all of it) and writes each into Redis via the same
    key/TTL/stripping logic a live store already uses, so a warm-loaded row is
    indistinguishable from a freshly-stored one to a later lookup.

    Never raises: a Redis outage, or even a Postgres read failure, at boot must not
    prevent the app from serving traffic. Each cache type gets its own try/except so a
    failure loading one (e.g. a schema issue isolated to one table) doesn't also zero
    out the other's count; the reused setters already guard their own Redis calls.
    """
    ask_rows_loaded = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT question_raw, superseded_filter, authority_filter, result_json
                FROM answer_cache
                """
            )
            rows = cur.fetchall()

        for question_raw, superseded_filter, authority_filter, result_json in rows:
            result = result_json if isinstance(result_json, dict) else json.loads(result_json)
            if write_redis_cache(question_raw, superseded_filter, authority_filter, result):
                ask_rows_loaded += 1
    except Exception as exc:
        logger.warning("Cache warm-load failed (answer_cache): %s", exc)

    diff_rows_loaded = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT current_document_id, previous_document_id, question_raw, result_json
                FROM diff_cache
                """
            )
            rows = cur.fetchall()

        for current_document_id, previous_document_id, question_raw, result_json in rows:
            result = result_json if isinstance(result_json, dict) else json.loads(result_json)
            if write_diff_redis_cache(current_document_id, previous_document_id, question_raw, result):
                diff_rows_loaded += 1
    except Exception as exc:
        logger.warning("Cache warm-load failed (diff_cache): %s", exc)

    result = {"ask_rows_loaded": ask_rows_loaded, "diff_rows_loaded": diff_rows_loaded}
    logger.info(
        "Cache warm-load complete: %s ask rows, %s diff rows",
        result["ask_rows_loaded"],
        result["diff_rows_loaded"],
    )
    return result
