"""Dual cache for /diff-followup: Redis L1 (exact key) + Postgres L2 (exact key).

Unlike answer_cache.py, there is no semantic layer here -- a diff-followup question is
already anchored to a specific (current_document_id, previous_document_id) pair, so
both layers are exact-key lookups (document ids + normalized question text). No
embedding is computed or stored for this cache.

Lookup order: Redis exact -> Postgres exact -> miss. On a Postgres hit, backfill Redis.
On a miss after a successful diff explanation, write both. Same lazy-client,
connect-failure-caching, best-effort-try/except shape as answer_cache.py -- mirrored
deliberately rather than factored into a shared module, per this codebase's existing
one-file-per-cache convention.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from app.core.answer_cache import normalize_question
from app.core.config import CACHE_REDIS_TTL_SECONDS, REDIS_URL

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "regulense:diff_cache:v1:"

_redis_client = None
# A connect failure is very often transient (a flaky Upstash TLS hiccup at boot, not a
# permanently-dead Redis), so this is a timestamped cooldown rather than a permanent
# latch -- mirrors app.core.retrieval's OPENAI_COOLDOWN_SECONDS/_openai_degraded_until
# idiom exactly (and answer_cache.py's identical copy of the same idiom). Without this,
# a single bad connect attempt (e.g. during the boot warm-load, the very first thing
# that touches Redis) would disable the whole Redis layer for the rest of the
# process's lifetime even if Redis recovers seconds later.
REDIS_COOLDOWN_SECONDS = 60
_redis_degraded_until = 0.0


def _redis_is_degraded() -> bool:
    return time.time() < _redis_degraded_until


def _mark_redis_degraded() -> None:
    global _redis_degraded_until
    _redis_degraded_until = time.time() + REDIS_COOLDOWN_SECONDS


def redis_cache_key(current_document_id: int, previous_document_id: int, question: str) -> str:
    raw = f"{current_document_id}|{previous_document_id}|{normalize_question(question)}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_PREFIX}{digest}"


def _get_redis():
    """Lazy Redis client. Returns None when REDIS_URL is unset, Redis is unreachable, or
    a prior failure's cooldown hasn't lapsed yet -- so L2 Postgres still works alone
    (Render free without Upstash, tests without Redis). socket_timeout bounds every
    subsequent blocking call (get/setex) too, not just the initial connect -- without
    it a stalled mid-operation connection could hang the calling request forever, a
    failure mode the surrounding try/except in _redis_get/_redis_set can't catch since
    it never raises. Deliberately a separate module-scoped client/cooldown pair from
    answer_cache.py's -- each cache module owns its own connect-failure state, same
    one-file-per-cache convention as the rest of this codebase."""
    global _redis_client
    if not REDIS_URL or _redis_is_degraded():
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis

        client = redis.from_url(
            REDIS_URL, decode_responses=True, socket_connect_timeout=1.0, socket_timeout=2.0,
        )
        client.ping()
        _redis_client = client
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable (%s); continuing with Postgres diff cache only", exc)
        _mark_redis_degraded()
        return None


def _hit_payload(result: dict, question_raw: str) -> dict:
    return {"result": result, "question_raw": question_raw}


def _redis_get(current_document_id: int, previous_document_id: int, question: str) -> dict | None:
    client = _get_redis()
    if client is None:
        return None
    try:
        raw = client.get(redis_cache_key(current_document_id, previous_document_id, question))
        if not raw:
            return None
        data = json.loads(raw)
        return _hit_payload(data["result"], data.get("question_raw"))
    except Exception as exc:
        logger.warning("Redis get failed: %s", exc)
        return None


def _redis_set(current_document_id: int, previous_document_id: int, question: str, result: dict) -> bool:
    client = _get_redis()
    if client is None:
        return False
    try:
        payload = json.dumps({"question_raw": question, "result": result}, default=str)
        client.setex(
            redis_cache_key(current_document_id, previous_document_id, question),
            CACHE_REDIS_TTL_SECONDS,
            payload,
        )
        return True
    except Exception as exc:
        logger.warning("Redis set failed: %s", exc)
        return False


def write_redis_cache(current_document_id: int, previous_document_id: int, question: str, result: dict) -> bool:
    """Public wrapper around the private Redis setter, for callers outside this module
    that need to write a row into Redis under the exact same key/TTL logic as a live
    store -- currently just cache_warmup.py's boot warm-load, which replays rows
    already in Postgres so they're indistinguishable from a freshly-stored diff to a
    later lookup. Returns True iff the write actually reached Redis, so a caller
    counting "rows loaded" can report an honest 0 under a total Redis outage rather
    than counting rows it merely attempted."""
    return _redis_set(current_document_id, previous_document_id, question, result)


def _postgres_lookup(conn, current_document_id: int, previous_document_id: int, question: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, question_raw, result_json
            FROM diff_cache
            WHERE current_document_id = %s
              AND previous_document_id = %s
              AND question_normalized = %s
            """,
            (current_document_id, previous_document_id, normalize_question(question)),
        )
        row = cur.fetchone()
    if not row:
        return None
    cache_id, question_raw, result_json = row
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE diff_cache
            SET hit_count = hit_count + 1, last_hit_at = now()
            WHERE id = %s
            """,
            (cache_id,),
        )
    conn.commit()
    result = result_json if isinstance(result_json, dict) else json.loads(result_json)
    return _hit_payload(result, question_raw)


def _postgres_store(conn, current_document_id: int, previous_document_id: int, question: str, result: dict) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO diff_cache (
                    current_document_id, previous_document_id, question_normalized,
                    question_raw, result_json
                )
                VALUES (%s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (current_document_id, previous_document_id, question_normalized)
                DO UPDATE SET result_json = EXCLUDED.result_json, question_raw = EXCLUDED.question_raw
                """,
                (
                    current_document_id,
                    previous_document_id,
                    normalize_question(question),
                    question,
                    json.dumps(result, default=str),
                ),
            )
        conn.commit()
    except Exception as exc:
        logger.warning("Postgres diff_cache store failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass


def lookup_diff_cache(conn, current_document_id: int, previous_document_id: int, question: str) -> dict | None:
    """L1 Redis exact, then L2 Postgres exact. Backfills Redis on an L2 hit. Both
    layers key on the same (current_document_id, previous_document_id,
    normalized-question) triple -- there is no paraphrase/semantic matching here."""
    hit = _redis_get(current_document_id, previous_document_id, question)
    if hit is not None:
        return hit

    hit = _postgres_lookup(conn, current_document_id, previous_document_id, question)
    if hit is not None:
        _redis_set(current_document_id, previous_document_id, question, hit["result"])
        return hit
    return None


def store_diff_cache(conn, current_document_id: int, previous_document_id: int, question: str, result: dict) -> None:
    """Best-effort write to Postgres then Redis. Never raises to callers. Only meant
    to be called on a genuinely successful ("available": true) diff explanation -- the
    caller in diff.py already only reaches this call on that path, but this guard keeps
    the invariant true even if a future caller isn't as careful, mirroring
    store_answer_cache's abstained-result guard."""
    if not result.get("available"):
        return
    _postgres_store(conn, current_document_id, previous_document_id, question, result)
    _redis_set(current_document_id, previous_document_id, question, result)


def evict_diff_cache(conn, current_document_id: int, previous_document_id: int, question: str) -> bool:
    """Removes the exact-key entry from both layers (this cache has no semantic
    layer). Best-effort; a missing row still counts as successfully gone."""
    removed = False
    client = _get_redis()
    if client is not None:
        try:
            removed = bool(client.delete(redis_cache_key(current_document_id, previous_document_id, question)))
        except Exception as exc:
            logger.warning("Redis diff_cache delete failed: %s", exc)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM diff_cache
                WHERE current_document_id = %s AND previous_document_id = %s
                  AND question_normalized = %s
                """,
                (current_document_id, previous_document_id, normalize_question(question)),
            )
            removed = cur.rowcount > 0 or removed
        conn.commit()
    except Exception as exc:
        logger.warning("Postgres diff_cache delete failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
    return removed
