"""Mined follow-up suggestions ("Continue exploring").

Workers crawl documents offline (see backend/ingestion/SUGGESTION_MINING_PROMPT.md)
and ingestion/load_suggestions.py stores grounded questions with embeddings. This
module matches them at answer time: cosine distance between the live query vector
(which the pipeline already computed -- matching is essentially free) and stored
suggestion embeddings, same-authority answers first, never suggesting the question
just asked.

Clicking a suggestion re-enters the normal /ask pipeline as a fresh question. The
stored document/chunk anchors travel as provenance metadata only, never as a
retrieval bypass -- answering purely from stored chunk IDs would serve superseded
versions after the corpus moves on.
"""
from app.core.answer_cache import normalize_question

SUGGESTION_LIMIT = 3


def find_suggested_followups(
    conn, query_vec: list[float], exclude_question: str,
    authority: str | None = None, limit: int = SUGGESTION_LIMIT,
) -> list[dict]:
    """Returns up to `limit` suggestions as {question, doc_code, document_id, pages,
    section}. Never raises to callers -- on any failure returns [] so a suggestions
    outage degrades to the static frontend bank, never a broken answer."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT question, doc_code, document_id, pages, section,
                       question_embedding <=> %s::vector AS distance
                FROM suggested_questions
                WHERE question_normalized != %s
                ORDER BY (authority = %s) DESC NULLS LAST,
                         distance ASC
                LIMIT %s
                """,
                (query_vec, normalize_question(exclude_question), authority, limit),
            )
            rows = cur.fetchall()
        return [
            {
                "question": q,
                "doc_code": code,
                "document_id": did,
                "pages": list(pages or []),
                "section": section,
            }
            for q, code, did, pages, section, _ in rows
        ]
    except Exception:
        return []
