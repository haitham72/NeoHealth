"""Dual cache for /cross-check-regulation: Redis L1 (exact key) + Postgres L2 (exact key).

Mirrors app.core.diff_cache deliberately rather than factoring into a shared module,
per this codebase's existing one-file-per-cache convention. Same shape: exact-key
only (the question is already anchored to a specific citing document, so no
embedding or semantic layer), lazy Redis client with connect-failure cooldown,
best-effort never-raises stores.

Key: (current_document_id, cited_page, normalized-question). cited_text is
derivable from doc+page, so it stays out of the key -- two citations of the same
page with slightly different excerpts share one entry.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from app.core.answer_cache import normalize_question
from app.core.config import CACHE_REDIS_TTL_SECONDS, REDIS_URL

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "regulense:cross_check_cache:v1:"

_redis_client = None
# Timestamped cooldown, not a permanent latch -- same idiom as diff_cache.py and
# answer_cache.py (a single transient connect failure must not disable the Redis
# layer for the rest of the process's lifetime).
REDIS_COOLDOWN_SECONDS = 60
_redis_degraded_until = 0.0


def _redis_is_degraded() -> bool:
    return time.time() < _redis_degraded_until


def _mark_redis_degraded() -> None:
    global _redis_degraded_until
    _redis_degraded_until = time.time() + REDIS_COOLDOWN_SECONDS


def redis_cache_key(current_document_id: int, cited_page: int, question: str) -> str:
    raw = f"{current_document_id}|{cited_page}|{normalize_question(question)}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_PREFIX}{digest}"


def _get_redis():
    """Lazy Redis client. Returns None when REDIS_URL is unset, Redis is unreachable, or
    a prior failure's cooldown hasn't lapsed yet -- so L2 Postgres still works alone."""
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
        logger.warning("Redis unavailable (%s); continuing with Postgres cross-check cache only", exc)
        _mark_redis_degraded()
        return None


def _hit_payload(result: dict, question_raw: str) -> dict:
    return {"result": result, "question_raw": question_raw}


def _redis_get(current_document_id: int, cited_page: int, question: str) -> dict | None:
    client = _get_redis()
    if client is None:
        return None
    try:
        raw = client.get(redis_cache_key(current_document_id, cited_page, question))
        if not raw:
            return None
        data = json.loads(raw)
        return _hit_payload(data["result"], data.get("question_raw"))
    except Exception as exc:
        logger.warning("Redis get failed: %s", exc)
        return None


def _redis_set(current_document_id: int, cited_page: int, question: str, result: dict) -> bool:
    client = _get_redis()
    if client is None:
        return False
    try:
        payload = json.dumps({"question_raw": question, "result": result}, default=str)
        client.setex(
            redis_cache_key(current_document_id, cited_page, question),
            CACHE_REDIS_TTL_SECONDS,
            payload,
        )
        return True
    except Exception as exc:
        logger.warning("Redis set failed: %s", exc)
        return False


def _postgres_lookup(conn, current_document_id: int, cited_page: int, question: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, question_raw, result_json
            FROM cross_check_cache
            WHERE current_document_id = %s
              AND cited_page = %s
              AND question_normalized = %s
            """,
            (current_document_id, cited_page, normalize_question(question)),
        )
        row = cur.fetchone()
    if not row:
        return None
    cache_id, question_raw, result_json = row
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE cross_check_cache
            SET hit_count = hit_count + 1, last_hit_at = now()
            WHERE id = %s
            """,
            (cache_id,),
        )
    conn.commit()
    result = result_json if isinstance(result_json, dict) else json.loads(result_json)
    return _hit_payload(result, question_raw)


def _postgres_store(conn, current_document_id: int, cited_page: int, question: str, result: dict) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cross_check_cache (
                    current_document_id, cited_page, question_normalized,
                    question_raw, result_json
                )
                VALUES (%s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (current_document_id, cited_page, question_normalized)
                DO UPDATE SET result_json = EXCLUDED.result_json, question_raw = EXCLUDED.question_raw
                """,
                (
                    current_document_id,
                    cited_page,
                    normalize_question(question),
                    question,
                    json.dumps(result, default=str),
                ),
            )
        conn.commit()
    except Exception as exc:
        logger.warning("Postgres cross_check_cache store failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass


def lookup_cross_check_cache(conn, current_document_id: int, cited_page: int, question: str) -> dict | None:
    """L1 Redis exact, then L2 Postgres exact. Backfills Redis on an L2 hit."""
    hit = _redis_get(current_document_id, cited_page, question)
    if hit is not None:
        return hit

    hit = _postgres_lookup(conn, current_document_id, cited_page, question)
    if hit is not None:
        _redis_set(current_document_id, cited_page, question, hit["result"])
        return hit
    return None


def store_cross_check_cache(conn, current_document_id: int, cited_page: int, question: str, result: dict) -> None:
    """Best-effort write to Postgres then Redis. Never raises to callers. Only meant
    to be called on a genuinely successful ("available": true) cross-check -- the
    caller in cross_check.py already only reaches this call on that path, but this
    guard keeps the invariant true even if a future caller isn't as careful."""
    if not result.get("available"):
        return
    _postgres_store(conn, current_document_id, cited_page, question, result)
    _redis_set(current_document_id, cited_page, question, result)
