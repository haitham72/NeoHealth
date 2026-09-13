"""POST /cross-check-regulation."""
import logging

from fastapi import APIRouter, HTTPException, Request
from slowapi.util import get_remote_address

from app.api.schemas.cross_check import CrossCheckRegulationRequest
from app.core.config import DAILY_OPENAI_CALL_CAP
from app.core.cross_check_cache import lookup_cross_check_cache, store_cross_check_cache
from app.core.db import get_connection, increment_daily_usage, release_connection
from app.core.limiter import limiter
from app.core.retrieval import chat_completion, embed, local_client, resolve_model
from app.services.cross_reference import find_related_official_docs

logger = logging.getLogger(__name__)

router = APIRouter()


def _safe_lookup_cross_check_cache(conn, current_document_id: int, cited_page: int, question: str) -> dict | None:
    """Best-effort wrapper around cross_check_cache.lookup_cross_check_cache -- mirrors
    diff.py's _safe_lookup_diff_cache exactly (same bug class, same fix). A transient
    Redis or Postgres failure during the cache gate must never break the actual
    cross-check response; any exception is logged and treated as a plain cache miss,
    with a rollback first so a real Postgres-level failure doesn't leave `conn`'s
    transaction aborted for the later statements in this request."""
    try:
        return lookup_cross_check_cache(conn, current_document_id, cited_page, question)
    except Exception:
        logger.warning("lookup_cross_check_cache failed; treating as a cache miss", exc_info=True)
        try:
            conn.rollback()
        except Exception:
            logger.warning("conn.rollback() after failed cross-check cache lookup also failed", exc_info=True)
        return None


@router.post("/cross-check-regulation")
@limiter.limit("10/minute;30/hour")
def cross_check_regulation(request: Request, req: CrossCheckRegulationRequest):
    """On-demand only, research-tier citations only -- never called automatically
    alongside /ask. Finds the official standard(s) most related to the question and asks
    the model to explain how the cited research relates to them, in one merged step
    rather than a separate 'find' and 'compare' action."""
    conn = get_connection()
    try:
        # Cache check comes before increment_daily_usage: that counter tracks real
        # LLM calls, and a cache hit makes none (not even the embed call below), so
        # it must not be charged against the daily cap. Same convention as diff.py.
        hit = _safe_lookup_cross_check_cache(conn, req.current_document_id, req.cited_page, req.question)
        if hit:
            # Same convention as /ask's cached answers: mark the hit so the UI can
            # show its tiny "Served from cache" note. Copied so the stored payload
            # stays clean and fresh generations never carry the marker into store.
            result = dict(hit["result"])
            result["cache_hit"] = True
            return result

        count = increment_daily_usage(conn)
        if count > DAILY_OPENAI_CALL_CAP:
            raise HTTPException(429, "Daily usage cap reached -- try again tomorrow.")

        query_vec = embed(req.question)
        related = find_related_official_docs(conn, query_vec)
        if not related:
            return {"available": False, "reason": "no related official standard found in the corpus"}

        excerpts = "\n\n".join(
            f"=== {r['doc_code']} (v{r['version']}, {r['authority']}), page {r['page']} ===\n{r['text']}"
            for r in related
        )
        # model is only meaningful for the local provider (same convention as
        # /ask in routers/ask.py) -- a caller-supplied OpenAI model id must never
        # pass straight through to a paid completion call unchecked.
        model = req.model if req.provider == "local" else None
        messages = [
            {
                "role": "system",
                "content": (
                    "You are given an excerpt from a research paper and one or more excerpts from "
                    "OFFICIAL UAE health regulations on a related topic. Explain in 2-4 plain sentences "
                    "how the research finding relates to the official standard(s) -- where they align, "
                    "and where the research covers ground the regulation doesn't (or vice versa). Never "
                    "state or imply that the research paper carries regulatory authority. Never invent a "
                    "connection that isn't supported by the excerpts."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User's original question: {req.question}\n\n"
                    f"=== RESEARCH EXCERPT ({req.doc_code}, page {req.cited_page}) ===\n{req.cited_text}\n\n"
                    f"{excerpts}"
                ),
            },
        ]
        if req.provider != "openai":
            # Local/LM Studio path -- mirrors generate_answer()'s provider branch in
            # retrieval.py, so "local" mode works here exactly like the main /ask
            # pipeline instead of falling into chat_completion (OpenAI->NaraRouter
            # only) and erroring about missing keys.
            resp = local_client.chat.completions.create(
                model=resolve_model(req.provider, model), messages=messages, temperature=0
            )
        else:
            resp, _ = chat_completion(messages, client_ip=get_remote_address(request))
        explanation = (resp.choices[0].message.content or "").strip()

        result = {
            "available": True,
            "explanation": explanation,
            "documents": [
                {"doc_code": r["doc_code"], "title": r["title"], "version": r["version"], "authority": r["authority"]}
                for r in related
            ],
        }
        store_cross_check_cache(conn, req.current_document_id, req.cited_page, req.question, result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        release_connection(conn)
