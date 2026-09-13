"""
Load mined follow-up questions (JSONL per backend/ingestion/SUGGESTION_MINING_PROMPT.md)
into the suggested_questions table.

Pipeline per line: parse JSON -> validate 8 fields -> resolve doc_code to the
in-force document -> verify anchor_quote occurs VERBATIM (whitespace-collapsed) in
that document's chunk texts -> resolve matching chunk_ids (fall back to the listed
pages) -> embed the question -> upsert on (question_normalized, document_id), so
re-running a worker's file for the same doc safely overwrites instead of duplicating.

Usage: python -m ingestion.load_suggestions [--mined-by NAME] suggestions.a.jsonl [...]
"""
import argparse
import json
import re
import sys
from pathlib import Path

from app.core.answer_cache import normalize_question
from app.core.db import ensure_schema, get_connection

REQUIRED_FIELDS = {"question", "doc_code", "authority", "tier", "pages", "section", "anchor_quote", "form"}
FORM_ALLOW = {"what", "how", "when", "how-long", "list", "requirements", "yes-no"}


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _resolve_document(conn, doc_code: str) -> int | None:
    """In-force document wins; any version as fallback; None if unknown doc_code."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM documents WHERE doc_code = %s ORDER BY superseded ASC, id DESC LIMIT 1",
            (doc_code,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _resolve_chunks(conn, document_id: int, pages: list[int], anchor: str) -> list[int] | None:
    """Resolves an anchor to chunk ids. Returns None when the anchor occurs NOWHERE
    in the document (fabricated: the caller rejects the line). A real anchor
    straddling a chunk boundary matches no single chunk -- those resolve via the
    listed pages instead. Empty list likewise means bogus location metadata."""
    collapsed_anchor = _collapse(anchor)
    with conn.cursor() as cur:
        cur.execute("SELECT id, page, text FROM chunks WHERE document_id = %s", (document_id,))
        chunks = cur.fetchall()
    if collapsed_anchor not in " ".join(_collapse(text) for _, _, text in chunks):
        return None
    hits = [cid for cid, _, text in chunks if collapsed_anchor in _collapse(text)]
    if hits:
        return hits
    return [cid for cid, page, _ in chunks if page in set(pages)]


def _validate_entry(entry: dict) -> str | None:
    """Returns a rejection reason, or None if the entry is loadable."""
    if not isinstance(entry, dict):
        return "not a JSON object"
    missing = REQUIRED_FIELDS - set(entry)
    if missing:
        return f"missing fields: {sorted(missing)}"
    if not isinstance(entry["question"], str) or not entry["question"].strip():
        return "empty question"
    if not isinstance(entry["pages"], list) or not entry["pages"] or not all(
        isinstance(p, int) for p in entry["pages"]
    ):
        return "pages must be a non-empty int list"
    if not isinstance(entry["anchor_quote"], str) or not entry["anchor_quote"].strip():
        return "empty anchor_quote"
    if entry["form"] not in FORM_ALLOW:
        return f"form must be one of {sorted(FORM_ALLOW)}"
    return None


def load_suggestions_file(conn, path: Path, mined_by: str, embed_fn=None) -> dict:
    """Loads one worker JSONL file. Returns stats {loaded, skipped_docs, rejected}.
    embed_fn defaults to retrieval.embed (lazy import -- keeps this module importable
    without OpenAI credentials, and lets tests inject a fake)."""
    if embed_fn is None:
        from app.core.retrieval import embed as embed_fn

    stats = {"loaded": 0, "skipped_docs": 0, "rejected": 0}
    lines = path.read_text(encoding="utf-8").splitlines()
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("SKIP:"):
            stats["skipped_docs"] += 1
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"{path}:{lineno}: reject (bad JSON: {e})")
            stats["rejected"] += 1
            continue
        reason = _validate_entry(entry)
        if reason is not None:
            print(f"{path}:{lineno}: reject ({reason})")
            stats["rejected"] += 1
            continue
        document_id = _resolve_document(conn, entry["doc_code"])
        if document_id is None:
            print(f"{path}:{lineno}: reject (unknown doc_code {entry['doc_code']!r})")
            stats["rejected"] += 1
            continue
        chunk_ids = _resolve_chunks(conn, document_id, entry["pages"], entry["anchor_quote"])
        if not chunk_ids:
            print(f"{path}:{lineno}: reject (anchor not found in doc {entry['doc_code']!r})")
            stats["rejected"] += 1
            continue
        vec = embed_fn(entry["question"])
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO suggested_questions (
                    question, question_normalized, question_embedding,
                    doc_code, document_id, chunk_ids, pages, section,
                    authority, tier, mined_by
                )
                VALUES (%s, %s, %s::vector, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (question_normalized, document_id)
                DO UPDATE SET question = EXCLUDED.question,
                              question_embedding = EXCLUDED.question_embedding,
                              chunk_ids = EXCLUDED.chunk_ids,
                              pages = EXCLUDED.pages,
                              section = EXCLUDED.section,
                              authority = EXCLUDED.authority,
                              tier = EXCLUDED.tier,
                              mined_by = EXCLUDED.mined_by
                """,
                (
                    entry["question"],
                    normalize_question(entry["question"]),
                    vec,
                    entry["doc_code"],
                    document_id,
                    chunk_ids,
                    entry["pages"],
                    entry["section"],
                    entry["authority"],
                    entry["tier"],
                    mined_by,
                ),
            )
        stats["loaded"] += 1
    conn.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load mined follow-up question JSONL files.")
    parser.add_argument("files", nargs="+", help="suggestions.<worker>.jsonl files")
    parser.add_argument("--mined-by", default=None, help="worker id (defaults to file stem)")
    args = parser.parse_args(argv)

    conn = get_connection()
    try:
        ensure_schema(conn)
        total = {"loaded": 0, "skipped_docs": 0, "rejected": 0}
        for f in args.files:
            path = Path(f)
            mined_by = args.mined_by or path.stem
            stats = load_suggestions_file(conn, path, mined_by)
            print(f"{path}: {stats}")
            for k in total:
                total[k] += stats[k]
        print(f"TOTAL: {total}")
    finally:
        from app.core.db import release_connection

        release_connection(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
