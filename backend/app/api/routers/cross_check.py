"""POST /cross-check-regulation."""
from fastapi import APIRouter, HTTPException, Request
from slowapi.util import get_remote_address

from app.api.schemas.cross_check import CrossCheckRegulationRequest
from app.core.config import DAILY_OPENAI_CALL_CAP
from app.core.db import get_connection, increment_daily_usage, release_connection
from app.core.limiter import limiter
from app.core.retrieval import chat_completion, embed
from app.services.cross_reference import find_related_official_docs

router = APIRouter()


@router.post("/cross-check-regulation")
@limiter.limit("10/minute;30/hour")
def cross_check_regulation(request: Request, req: CrossCheckRegulationRequest):
    """On-demand only, research-tier citations only -- never called automatically
    alongside /ask. Finds the official standard(s) most related to the question and asks
    the model to explain how the cited research relates to them, in one merged step
    rather than a separate 'find' and 'compare' action."""
    conn = get_connection()
    try:
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
        resp, _ = chat_completion([
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
        ], client_ip=get_remote_address(request))
        explanation = (resp.choices[0].message.content or "").strip()

        return {
            "available": True,
            "explanation": explanation,
            "documents": [
                {"doc_code": r["doc_code"], "title": r["title"], "version": r["version"], "authority": r["authority"]}
                for r in related
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        release_connection(conn)
