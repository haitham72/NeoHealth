"""Dual answer cache: Redis L1 (exact key) + Postgres L2 (semantic / paraphrase).

Lookup order: Redis exact → Postgres cosine under the same non-LLM filters → miss.
On Postgres hit, backfill Redis. On full miss after a successful answer, write both.

Provider/model are intentionally NOT part of the cache key (LLM choice must not
split or poison the cache). History-bearing asks skip the cache entirely.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from app.core.config import CACHE_HIT_THRESHOLD, CACHE_REDIS_TTL_SECONDS, REDIS_URL

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "regulense:answer_cache:v1:"

# LangSmith / API: how the hit was decided
MATCH_EXACT = "exact_key_plus_filters"
MATCH_SEMANTIC = "query_plus_filters"

_redis_client = None
# A connect failure is very often transient (a flaky Upstash TLS hiccup at boot, not a
# permanently-dead Redis), so this is a timestamped cooldown rather than a permanent
# latch -- mirrors app.core.retrieval's OPENAI_COOLDOWN_SECONDS/_openai_degraded_until
# idiom exactly. Without this, a single bad connect attempt (e.g. during the boot
# warm-load, the very first thing that touches Redis) would disable the whole Redis
# layer for the rest of the process's lifetime even if Redis recovers seconds later.
REDIS_COOLDOWN_SECONDS = 60
_redis_degraded_until = 0.0


def _redis_is_degraded() -> bool:
    return time.time() < _redis_degraded_until


def _mark_redis_degraded() -> None:
    global _redis_degraded_until
    _redis_degraded_until = time.time() + REDIS_COOLDOWN_SECONDS


def normalize_question(question: str) -> str:
    return " ".join(question.strip().lower().split())


def cache_filters_key(superseded_filter: bool, authority_filter: str | None) -> str:
    """Stable filter segment for Redis keys — all non-LLM ask filters."""
    auth = authority_filter if authority_filter else ""
    return f"{int(bool(superseded_filter))}|{auth}"


def redis_cache_key(question: str, superseded_filter: bool, authority_filter: str | None) -> str:
    raw = f"{normalize_question(question)}|{cache_filters_key(superseded_filter, authority_filter)}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_PREFIX}{digest}"


def _get_redis():
    """Lazy Redis client. Returns None when REDIS_URL is unset, Redis is unreachable, or
    a prior failure's cooldown hasn't lapsed yet -- so L2 Postgres still works alone
    (Render free without Upstash, tests without Redis). socket_timeout bounds every
    subsequent blocking call (get/setex) too, not just the initial connect -- without
    it a stalled mid-operation connection could hang the calling request forever, a
    failure mode the surrounding try/except in _redis_get/_redis_set can't catch since
    it never raises."""
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
        logger.warning("Redis unavailable (%s); continuing with Postgres answer cache only", exc)
        _mark_redis_degraded()
        return None


def _strip_for_storage(result: dict) -> dict:
    """Persist the answer payload without run-scoped / cache-meta fields.

    suggested_followups is stripped deliberately: it is a LIVE annotation derived
    from the current corpus (mined suggestions are re-loaded over time), not part
    of the answer itself. Freezing it into the cache made repeated questions serve
    suggestions mined/loaded before the crawl -- or none at all, falling back to
    the static bank -- which read as "the same questions over and over". It is
    re-attached at serve time instead (see retrieval._attach_suggested_followups).
    """
    skip = {"run_id", "cache_hit", "cache_similarity", "cache_match_mode", "cache_layer",
            "suggested_followups", "cache_token"}
    return {k: v for k, v in result.items() if k not in skip}


def _hit_payload(
    *,
    result: dict,
    layer: str,
    match_mode: str,
    similarity: float | None,
    matched_question: str | None,
    cache_id: int | None = None,
) -> dict[str, Any]:
    return {
        "result": result,
        "cache_layer": layer,
        "match_mode": match_mode,
        "similarity": similarity,
        "matched_question": matched_question,
        "cache_id": cache_id,
    }


def _redis_get(question: str, superseded_filter: bool, authority_filter: str | None) -> dict | None:
    client = _get_redis()
    if client is None:
        return None
    try:
        raw = client.get(redis_cache_key(question, superseded_filter, authority_filter))
        if not raw:
            return None
        data = json.loads(raw)
        return _hit_payload(
            result=data["result"],
            layer="redis",
            match_mode=MATCH_EXACT,
            similarity=1.0,
            matched_question=data.get("question_raw"),
        )
    except Exception as exc:
        logger.warning("Redis get failed: %s", exc)
        return None


def _redis_set(
    question: str,
    superseded_filter: bool,
    authority_filter: str | None,
    result: dict,
) -> bool:
    client = _get_redis()
    if client is None:
        return False
    try:
        payload = json.dumps(
            {"question_raw": question, "result": _strip_for_storage(result)},
            default=str,
        )
        client.setex(
            redis_cache_key(question, superseded_filter, authority_filter),
            CACHE_REDIS_TTL_SECONDS,
            payload,
        )
        return True
    except Exception as exc:
        logger.warning("Redis set failed: %s", exc)
        return False


def write_redis_cache(
    question: str,
    superseded_filter: bool,
    authority_filter: str | None,
    result: dict,
) -> bool:
    """Public wrapper around the private Redis setter, for callers outside this module
    that need to write a row into Redis under the exact same key/TTL/stripping logic as
    a live store -- currently just cache_warmup.py's boot warm-load, which replays rows
    already in Postgres so they're indistinguishable from a freshly-stored answer to a
    later lookup. Returns True iff the write actually reached Redis, so a caller counting
    "rows loaded" can report an honest 0 under a total Redis outage rather than counting
    rows it merely attempted."""
    return _redis_set(question, superseded_filter, authority_filter, result)


def _postgres_lookup(
    conn,
    question: str,
    query_vec: list[float],
    superseded_filter: bool,
    authority_filter: str | None,
    threshold: float,
) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, question_raw, result_json,
                   1 - (query_embedding <=> %s::vector) AS similarity
            FROM answer_cache
            WHERE superseded_filter = %s
              AND authority_filter IS NOT DISTINCT FROM %s
            ORDER BY query_embedding <=> %s::vector
            LIMIT 1
            """,
            (query_vec, superseded_filter, authority_filter, query_vec),
        )
        row = cur.fetchone()
    if not row:
        return None
    cache_id, question_raw, result_json, similarity = row
    if similarity is None or float(similarity) < threshold:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE answer_cache
            SET hit_count = hit_count + 1, last_hit_at = now()
            WHERE id = %s
            """,
            (cache_id,),
        )
    conn.commit()
    result = result_json if isinstance(result_json, dict) else json.loads(result_json)
    return _hit_payload(
        result=result,
        layer="postgres",
        match_mode=MATCH_SEMANTIC,
        similarity=float(similarity),
        matched_question=question_raw,
        cache_id=cache_id,
    )


def _postgres_store(
    conn,
    question: str,
    query_vec: list[float],
    superseded_filter: bool,
    authority_filter: str | None,
    result: dict,
) -> int | None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO answer_cache (
                    question_normalized, question_raw, superseded_filter, authority_filter,
                    query_embedding, result_json
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (
                    normalize_question(question),
                    question,
                    superseded_filter,
                    authority_filter,
                    query_vec,
                    json.dumps(_strip_for_storage(result), default=str),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("Postgres answer_cache store failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def lookup_answer_cache(
    conn,
    question: str,
    query_vec: list[float] | None,
    superseded_filter: bool,
    authority_filter: str | None,
    threshold: float | None = None,
) -> dict | None:
    """L1 Redis exact, then L2 Postgres semantic. Backfills Redis on L2 hit.

    `query_vec` may be None only when Redis hits (exact path). Callers that miss
    Redis must pass an embedding for L2.
    """
    hit = _redis_get(question, superseded_filter, authority_filter)
    if hit is not None:
        return hit

    if query_vec is None:
        return None

    hit = _postgres_lookup(
        conn,
        question,
        query_vec,
        superseded_filter,
        authority_filter,
        threshold if threshold is not None else CACHE_HIT_THRESHOLD,
    )
    if hit is not None:
        _redis_set(question, superseded_filter, authority_filter, hit["result"])
        return hit
    return None


def store_answer_cache(
    conn,
    question: str,
    query_vec: list[float],
    superseded_filter: bool,
    authority_filter: str | None,
    result: dict,
) -> None:
    """Best-effort write to Postgres then Redis. Never raises to callers."""
    if result.get("abstained"):
        return
    _postgres_store(conn, question, query_vec, superseded_filter, authority_filter, result)
    _redis_set(question, superseded_filter, authority_filter, result)


def fetch_cached_query_embedding(
    conn,
    question: str,
    superseded_filter: bool,
    authority_filter: str | None,
) -> Any:
    """Best-effort lookup of a cached row's stored query embedding.

    Exists so the exact-key Redis hit path can still serve FRESH mined follow-ups
    without paying an embedding call -- the one piece of live data (see
    _strip_for_storage) that would otherwise need one. Zero OpenAI calls, one
    indexed-by-normalized-question Postgres read. Never raises; None on any
    failure so the caller just falls back to the static suggestion bank.
    (The vector column comes back as a string like '[0.1,0.2,...]' -- psycopg2
    with no pgvector adapter -- which is still directly usable as a %s::vector
    parameter, so callers must not assume a Python list.)"""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT query_embedding FROM answer_cache
                WHERE question_normalized = %s
                  AND superseded_filter = %s
                  AND authority_filter IS NOT DISTINCT FROM %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (normalize_question(question), superseded_filter, authority_filter),
            )
            row = cur.fetchone()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("cached query-embedding fetch failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def decorate_cached_result(hit: dict, run_id: str | None) -> dict:
    """Merge cache metadata onto a stored answer for the API / SSE payload."""
    out = dict(hit["result"])
    out["cache_hit"] = True
    out["cache_layer"] = hit["cache_layer"]
    out["cache_match_mode"] = hit["match_mode"]
    out["cache_similarity"] = hit["similarity"]
    out["run_id"] = run_id
    return out


def evict_answer_cache(
    conn,
    question: str,
    superseded_filter: bool,
    authority_filter: str | None,
    cache_id: int | None = None,
) -> bool:
    """Removes one served entry from both layers: the exact-key Redis entry for the
    asked question+filters, and its Postgres row -- by id when the hit was semantic
    (there the matched row can hold a *different* question), plus any row matching
    the asked normalized question+filters. Best-effort; returns True when any layer
    was touched, so a missing row (already evicted, TTL lapsed) is not an error."""
    removed = False
    client = _get_redis()
    if client is not None:
        try:
            removed = bool(client.delete(redis_cache_key(question, superseded_filter, authority_filter)))
        except Exception as exc:
            logger.warning("Redis answer_cache delete failed: %s", exc)
    try:
        with conn.cursor() as cur:
            if cache_id is not None:
                cur.execute("DELETE FROM answer_cache WHERE id = %s", (cache_id,))
                removed = cur.rowcount > 0 or removed
            cur.execute(
                """
                DELETE FROM answer_cache
                WHERE question_normalized = %s
                  AND superseded_filter = %s
                  AND authority_filter IS NOT DISTINCT FROM %s
                """,
                (normalize_question(question), superseded_filter, authority_filter),
            )
            removed = cur.rowcount > 0 or removed
        conn.commit()
    except Exception as exc:
        logger.warning("Postgres answer_cache delete failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
    return removed
