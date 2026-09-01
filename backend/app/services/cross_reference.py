"""Backs POST /cross-check-regulation: finds official-tier documents related to a
research-tier citation."""


def find_related_official_docs(conn, query_vec, k: int = 2) -> list[dict]:
    """Top official-tier documents by embedding similarity to the query, one chunk per
    document (its best-matching page). DISTINCT ON picks each document's closest chunk;
    the Python-side sort then ranks across documents so the k limit applies to documents,
    not chunks. Standalone query, same additive spirit as find_previous_version -- doesn't
    touch retrieval.py's semantic_search() or answer_question()'s contract."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (d.id) d.id, d.doc_code, d.title, d.version, d.authority,
                   c.text, c.page, 1 - (c.embedding <=> %s::vector) AS score
            FROM chunks c JOIN documents d ON d.id = c.document_id
            WHERE d.tier = 'official' AND d.superseded = false
            ORDER BY d.id, c.embedding <=> %s::vector
            """,
            (query_vec, query_vec),
        )
        rows = cur.fetchall()
    rows.sort(key=lambda r: r[7], reverse=True)
    return [
        {"id": r[0], "doc_code": r[1], "title": r[2], "version": r[3], "authority": r[4], "text": r[5], "page": r[6]}
        for r in rows[:k]
    ]
