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
from app.core.config import SUGGESTION_MIN_SIMILARITY

SUGGESTION_LIMIT = 3


def is_mined_question(conn, question: str) -> bool:
    """True when the exact question is one of the pre-vetted mined suggestions.
    Such questions are corpus-grounded by construction (every anchor was resolved
    verbatim to real chunks at load time), so the pipeline skips the LLM relevance
    guardrail for them: otherwise a weak judge -- measured live with the local
    qwen 4B model -- rejects questions the product itself recommended. Best-effort;
    any failure returns False so the normal guardrail path still runs."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM suggested_questions WHERE question_normalized = %s LIMIT 1",
                (normalize_question(question),),
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def find_suggested_followups(
    conn, query_vec: list[float], exclude_question: str,
    authority: str | None = None, limit: int = SUGGESTION_LIMIT,
    min_similarity: float | None = None,
    exclude_questions: list[str] | None = None,
) -> list[dict]:
    """Returns up to `limit` suggestions as {question, doc_code, document_id, pages,
    section}. Never raises to callers -- on any failure returns [] so a suggestions
    outage degrades to the static frontend bank, never a broken answer.

    `exclude_questions` additionally drops questions already asked earlier in the
    conversation (the pipeline's history), so clicking a suggestion doesn't rotate
    the same three questions forever. `min_similarity` (cosine, 1 - distance)
    defaults to config.SUGGESTION_MIN_SIMILARITY -- 0 out of the box, i.e. always
    the closest 3 regardless of how weak the match."""
    floor = SUGGESTION_MIN_SIMILARITY if min_similarity is None else min_similarity
    exclude = {normalize_question(exclude_question)}
    for prior in exclude_questions or []:
        if isinstance(prior, str) and prior.strip():
            exclude.add(normalize_question(prior))
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT question, doc_code, document_id, pages, section,
                       question_embedding <=> %s::vector AS distance
                FROM suggested_questions
                WHERE question_normalized <> ALL(%s)
                  AND 1 - (question_embedding <=> %s::vector) >= %s
                ORDER BY (authority = %s) DESC NULLS LAST,
                         distance ASC
                LIMIT %s
                """,
                (query_vec, list(exclude), query_vec, floor, authority, limit),
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
