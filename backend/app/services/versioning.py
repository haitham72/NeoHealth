"""Version-comparison helpers backing POST /diff-followup. Additive-only, same
pattern as app.services.enrichment -- never touches app.core.retrieval's contract
or /ask's response."""


def load_full_document_text(conn, document_id: int) -> str:
    """Full document text, reconstructed from Postgres rather than parsed_documents.json.
    Chunking is page-level (load_db.py: one chunk per PDF page, see rag-prompts.md), so
    every chunk row already holds one full page's text -- concatenating them in page
    order reconstructs the complete document. Originally read parsed_documents.json
    directly, which is simpler locally but is gitignored and never reaches Render --
    confirmed live via a 502/FileNotFoundError in production. Querying Postgres instead
    means this works wherever the app runs, since the DB is the actual source of truth
    in production, not a local build artifact."""
    with conn.cursor() as cur:
        cur.execute("SELECT text FROM chunks WHERE document_id = %s ORDER BY page", (document_id,))
        rows = cur.fetchall()
    return "\n".join(row[0] for row in rows)


def find_previous_version(conn, doc_code: str, current_document_id: int) -> dict | None:
    """Most recent superseded sibling of the currently-cited document. Additive-only,
    same pattern as get_sibling_versions -- a new lookup, never modifies answer_question's
    result or retrieval.py's contract."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, version, effective_date
            FROM documents
            WHERE doc_code = %s AND id != %s AND superseded = true
            ORDER BY effective_date DESC
            LIMIT 1
            """,
            (doc_code, current_document_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "version": row[1], "effective_date": row[2].isoformat()}
