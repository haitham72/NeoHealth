"""Additive-only enrichment helpers layered on top of app.core.retrieval's
answer_question() result -- never reinterpret or modify its core contract, only
attach extra data for the frontend (VersionLedger, SourcePanel). enrich_result is
used by both the ask and ask-stream routers, so it lives here where both can
import it."""


def get_sibling_versions(conn, doc_code: str, current_document_id: int) -> list[dict]:
    """Additive-only extra: every version of doc_code, for the frontend's VersionLedger.
    Never touches retrieval.py's contract — this is purely extra data appended alongside it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, version, effective_date, superseded
            FROM documents
            WHERE doc_code = %s
            ORDER BY effective_date DESC
            """,
            (doc_code,),
        )
        rows = cur.fetchall()
    return [
        {
            "id": row[0],
            "version": row[1],
            "effective_date": row[2].isoformat(),
            "superseded": row[3],
            "is_current": row[0] == current_document_id,
        }
        for row in rows
    ]


def attach_chunk_documents(conn, retrieved_chunks: list[dict]) -> None:
    """Additive-only extra: look up each chunk's source document once (batched by the
    unique document_ids present) and attach it inline, so the frontend's source panel
    can show title/code/version/link without a second round trip. Mutates in place."""
    doc_ids = sorted({c["document_id"] for c in retrieved_chunks})
    if not doc_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, doc_code, version, effective_date, authority,
                   source_url, superseded, tier
            FROM documents WHERE id = ANY(%s)
            """,
            (doc_ids,),
        )
        by_id = {
            row[0]: {
                "id": row[0],
                "title": row[1],
                "doc_code": row[2],
                "version": row[3],
                "effective_date": row[4].isoformat(),
                "authority": row[5],
                "source_url": row[6],
                "superseded": row[7],
                "tier": row[8],
            }
            for row in cur.fetchall()
        }
    for chunk in retrieved_chunks:
        chunk["document"] = by_id.get(chunk["document_id"])


def enrich_result(conn, result: dict) -> None:
    """Shared by /ask and /ask-stream: attach the two additive-only extras. Mutates in
    place; each extra is independently wrapped so a failure in one never breaks the
    other or the core answer."""
    if result["abstained"]:
        return
    try:
        result["sibling_versions"] = get_sibling_versions(
            conn, result["document"]["doc_code"], result["document"]["id"]
        )
    except Exception:
        pass  # VersionLedger is a stretch feature; never let it break the core answer
    try:
        attach_chunk_documents(conn, result["retrieved_chunks"])
    except Exception:
        pass  # source panel is a stretch feature; never let it break the core answer
