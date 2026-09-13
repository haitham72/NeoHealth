"""Eager, unconditional load of every Postgres cache row into Redis at process boot.

Why on top of answer_cache.py's existing reactive Redis population: a live cache write
(store_answer_cache) or a Postgres L2 hit already backfills Redis, but only for the
questions someone happens to ask *this* session. The interviewer's Redis instance is
only live for a few minutes per interview and idle for days or weeks in between --
CACHE_REDIS_TTL_SECONDS (7 days) can itself expire keys across a long enough gap -- so
its state at any given boot can't be trusted. This module makes the very first request
of a new interview session already a Redis hit for every previously-answered question,
not just the second time each one is asked.

diff_cache is intentionally out of scope here: the table exists (Task 1) but holds no
rows yet, and diff_cache.py -- which will own that table's Redis-write path -- doesn't
exist until Task 6. `diff_rows_loaded` is stubbed at 0 until that module lands and this
function is extended to call it.
"""
from __future__ import annotations

import json
import logging

from app.core.answer_cache import write_redis_cache

logger = logging.getLogger(__name__)


def warm_all_caches(conn) -> dict:
    """Reads every row already in Postgres's answer_cache table (no LIMIT, no recency
    filter -- all of it) and writes each into Redis via the same key/TTL/stripping
    logic a live store already uses, so a warm-loaded row is indistinguishable from a
    freshly-stored one to a later lookup.

    Never raises: a Redis outage, or even a Postgres read failure, at boot must not
    prevent the app from serving traffic. The reused setter already guards its own
    Redis call; the try/except here is the outer guard for everything else (the SELECT
    itself, JSON decoding, iteration).
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
        logger.warning("Cache warm-load failed: %s", exc)

    result = {"ask_rows_loaded": ask_rows_loaded, "diff_rows_loaded": 0}
    logger.info(
        "Cache warm-load complete: %s ask rows, %s diff rows",
        result["ask_rows_loaded"],
        result["diff_rows_loaded"],
    )
    return result
