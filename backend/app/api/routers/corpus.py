"""GET /corpus-stats. Imports get_connection/release_connection from app.core.db
at module level -- corpus_stats() resolves them via this module's own globals at
call time, which is what tests monkeypatch (see tests/test_api_tier.py)."""
from fastapi import APIRouter, Request

from app.core.db import get_connection, release_connection
from app.core.limiter import limiter

router = APIRouter()


@router.get("/corpus-stats")
@limiter.limit("30/minute")
def corpus_stats(request: Request):
    """Backs the frontend footer's document/chunk count -- computed live instead of
    hardcoded, so it can't go stale again the way the original static string already
    did once the corpus grew past its initial 16 documents."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.tier, count(DISTINCT d.id), count(c.id)
                FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
                GROUP BY d.tier
                """
            )
            by_tier = {tier: (docs, chunks) for tier, docs, chunks in cur.fetchall()}
        official_docs, official_chunks = by_tier.get("official", (0, 0))
        research_docs, _ = by_tier.get("research", (0, 0))
        return {
            "official_documents": official_docs,
            "official_chunks": official_chunks,
            "research_documents": research_docs,
        }
    finally:
        release_connection(conn)
