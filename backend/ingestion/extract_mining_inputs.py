"""
Render each unfinished in-force document from the database into a standalone text
file under mining_inputs/ for subagent-based mining.

Uses exactly the same header + [PAGE ...] chunk rendering as
ingestion/mine_suggestions.py, so an anchor quote copied from a file is verbatim
in chunks.text and passes load_suggestions.py's anchor check. Documents already
finished (mining_state.json status done/skip) are skipped unless --all, so
re-extraction also resumes instead of restarting.

Usage (from backend/):
    python -m ingestion.extract_mining_inputs [--all] [--only SUBSTRING]
"""
import argparse

from app.core.config import BACKEND_DIR
from app.core.db import get_connection, release_connection
from ingestion.mine_suggestions import (
    FINISHED_STATUSES,
    fetch_chunks,
    fetch_documents,
    format_document_text,
    load_state,
)

OUT_DIR = BACKEND_DIR / "mining_inputs"


def safe_name(position: int, doc: dict) -> str:
    code = (doc.get("doc_code") or "UNKNOWN").replace("/", "_")
    return f"{position:02d}_{code}.txt"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Include finished documents")
    parser.add_argument("--only", default="", help="Only docs whose doc_code contains this")
    args = parser.parse_args()

    conn = get_connection()
    try:
        docs = fetch_documents(conn, args.only, 0, 0)
        state = load_state()
        OUT_DIR.mkdir(exist_ok=True)
        written = skipped = 0
        for position, doc in enumerate(docs):
            status = state.get(doc["doc_code"], {}).get("status")
            if not args.all and status in FINISHED_STATUSES:
                skipped += 1
                continue
            chunks = fetch_chunks(conn, doc["id"])
            path = OUT_DIR / safe_name(position, doc)
            path.write_text(format_document_text(doc, chunks), encoding="utf-8")
            written += 1
            print(f"{path.name}  ({len(chunks)} chunks, {path.stat().st_size // 1024} KB)")
        print(f"\n{written} input files written, {skipped} finished docs skipped")
    finally:
        release_connection(conn)


if __name__ == "__main__":
    main()
