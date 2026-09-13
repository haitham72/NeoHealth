"""POST /diff-followup."""
import logging

from fastapi import APIRouter, HTTPException, Request
from slowapi.util import get_remote_address

from app.api.schemas.diff import DiffFollowupRequest
from app.core.config import DAILY_OPENAI_CALL_CAP
from app.core.db import get_connection, increment_daily_usage, release_connection
from app.core.diff_cache import lookup_diff_cache, store_diff_cache
from app.core.limiter import limiter
from app.core.retrieval import chat_completion
from app.services.versioning import find_previous_version, load_full_document_text

logger = logging.getLogger(__name__)

router = APIRouter()


def _safe_lookup_diff_cache(conn, current_document_id: int, previous_document_id: int, question: str) -> dict | None:
    """Best-effort wrapper around diff_cache.lookup_diff_cache -- mirrors
    retrieval.py's _safe_lookup_answer_cache exactly (same bug class, same fix). A
    transient Redis or Postgres failure during the cache gate must never break the
    actual diff-followup response; any exception here is logged and treated as a plain
    cache miss so the route falls straight through to the real pipeline.

    A real Postgres-level failure mid-lookup leaves `conn`'s transaction aborted --
    every later statement on this same connection (the `SELECT version FROM documents`
    right after this call, load_full_document_text() x2, etc.) would then fail too, and
    the connection would go back to the pool via release_connection() still aborted
    (release_connection() itself never rolls back -- see app/core/db.py), silently
    poisoning a later, unrelated request that checks it out next. So any failure here
    rolls back before returning None, not just logs-and-swallows. The rollback itself
    is wrapped too, since a sufficiently broken connection can raise on rollback as
    well, and that must not become a new uncaught exception in the fallback path."""
    try:
        return lookup_diff_cache(conn, current_document_id, previous_document_id, question)
    except Exception:
        logger.warning("lookup_diff_cache failed; treating as a cache miss", exc_info=True)
        try:
            conn.rollback()
        except Exception:
            logger.warning("conn.rollback() after failed diff cache lookup also failed", exc_info=True)
        return None


@router.post("/diff-followup")
@limiter.limit("10/minute;30/hour")
def diff_followup(request: Request, req: DiffFollowupRequest):
    """On-demand only -- never called automatically alongside /ask. Sends the FULL text
    of both the current and previous document versions to the model, layered as:
    system prompt (behavior) + the user's original question (scope) + a page-number hint
    from the chunk /ask actually cited (an anchor, not a limit -- the model can still see
    the whole document, so it can't falsely claim something was removed just because it
    fell outside a narrow excerpt window, which is what happened with the earlier
    chunk-matching approach). Read-only; does not touch retrieval.py or /ask's contract.

    Deliberately simple for this corpus's scale (largest doc here is ~55 pages, well
    under context limits) -- not a general-purpose design for arbitrarily large corpora."""
    conn = get_connection()
    try:
        prev = find_previous_version(conn, req.doc_code, req.current_document_id)
        if not prev:
            return {"available": False, "reason": "no earlier version of this document exists"}

        # Cache check comes before increment_daily_usage: that counter tracks real
        # OpenAI calls, and a cache hit makes none, so it must not be charged against
        # the daily cap. It also comes after resolving `prev` (needed for the cache
        # key) but before any other paid work, so an "unavailable" response is never
        # what gets cached below either.
        hit = _safe_lookup_diff_cache(conn, req.current_document_id, prev["id"], req.question)
        if hit:
            return hit["result"]

        count = increment_daily_usage(conn)
        if count > DAILY_OPENAI_CALL_CAP:
            raise HTTPException(429, "Daily usage cap reached -- try again tomorrow.")

        with conn.cursor() as cur:
            cur.execute("SELECT version FROM documents WHERE id = %s", (req.current_document_id,))
            current_version = cur.fetchone()[0]

        current_full = load_full_document_text(conn, req.current_document_id)
        previous_full = load_full_document_text(conn, prev["id"])
        if not current_full or not previous_full:
            return {"available": False, "reason": "no indexed page text found for one of the versions"}

        resp, _ = chat_completion([
            {
                "role": "system",
                "content": (
                    "You are given the FULL text of two versions of a UAE health regulation "
                    "manual -- not excerpts. Compare them specifically with respect to the "
                    "user's question below, and explain what changed between the previous "
                    "and current version, in 2-5 plain sentences. If nothing relevant to the "
                    "question actually changed, say so plainly instead of inventing a change. "
                    "Never state a fact that is not present in the text. Because you have the "
                    "full document, you can and should check the whole thing before claiming "
                    "something was added or removed -- do not rely only on the page hint below."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User's original question: {req.question}\n\n"
                    f"The current version's answer to this question was drawn primarily from "
                    f"around page {req.cited_page} of v{current_version} -- use this as a "
                    f"starting point, not a boundary.\n\n"
                    f"=== CURRENT VERSION (v{current_version}, in force) ===\n{current_full}\n\n"
                    f"=== PREVIOUS VERSION (v{prev['version']}, effective {prev['effective_date']}) ===\n{previous_full}"
                ),
            },
        ], client_ip=get_remote_address(request))
        explanation = (resp.choices[0].message.content or "").strip()

        result = {
            "available": True,
            "previous_version": prev["version"],
            "previous_effective_date": prev["effective_date"],
            "explanation": explanation,
        }
        store_diff_cache(conn, req.current_document_id, prev["id"], req.question, result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        release_connection(conn)
