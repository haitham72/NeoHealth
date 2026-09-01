"""
Chunk parsed_documents.json (by page) and load into Postgres: documents + chunks
with OpenAI embeddings and a tsvector column for hybrid retrieval.

Idempotent on sha256: re-running skips documents already loaded.
"""
import json
import os
import time

from openai import OpenAI

from app.core.config import PARSED_DOCUMENTS_FILE
from app.core.db import ensure_schema, get_connection

EMBED_MODEL = "text-embedding-3-small"  # 1536-dim, matches schema
MIN_PAGE_CHARS = 20  # skip near-empty pages (cover pages, blank pages)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch-embed; OpenAI allows up to ~2048 inputs per call, but we keep batches
    small so one failure doesn't waste a large batch's cost/time."""
    out = []
    batch_size = 50
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
        out.extend(d.embedding for d in resp.data)
    return out


def main():
    documents = json.loads(PARSED_DOCUMENTS_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(documents)} documents from {PARSED_DOCUMENTS_FILE.name}")

    conn = get_connection()
    ensure_schema(conn)

    with conn.cursor() as cur:
        cur.execute("SELECT sha256 FROM documents")
        already_loaded = {row[0] for row in cur.fetchall()}

    total_chunks_inserted = 0

    for doc in documents:
        if doc["sha256"] in already_loaded:
            print(f"  skip (already loaded): {doc['title']} v{doc['version']}")
            continue

        pages = doc["pages"]
        page_records = [
            (page_num, text.strip())
            for page_num, text in enumerate(pages, start=1)
            if text and len(text.strip()) >= MIN_PAGE_CHARS
        ]
        if not page_records:
            print(f"  SKIP (no usable page text): {doc['title']}")
            continue

        print(f"  embedding {len(page_records)} pages: {doc['title']} v{doc['version']} ...", end=" ")
        t0 = time.time()
        embeddings = embed_texts([text for _, text in page_records])
        print(f"done in {time.time() - t0:.1f}s")

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (title, doc_code, version, effective_date,
                    authority, source_url, sha256, superseded, tier)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    doc["title"],
                    doc["doc_code"],
                    doc["version"],
                    doc["effective_date"],
                    doc["authority"],
                    doc.get("source_url"),
                    doc["sha256"],
                    doc["superseded"],
                    doc.get("tier", "official"),
                ),
            )
            document_id = cur.fetchone()[0]

            for (page_num, text), embedding in zip(page_records, embeddings):
                cur.execute(
                    """
                    INSERT INTO chunks (document_id, page, text, embedding, tsv)
                    VALUES (%s, %s, %s, %s,
                        setweight(to_tsvector('english', %s), 'A') ||
                        setweight(to_tsvector('arabic', %s), 'A'))
                    """,
                    (document_id, page_num, text, embedding, text, text),
                )
                total_chunks_inserted += 1

        conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM documents")
        doc_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM chunks")
        chunk_count = cur.fetchone()[0]

    conn.close()
    print(f"\nInserted {total_chunks_inserted} new chunks this run.")
    print(f"Database totals: {doc_count} documents, {chunk_count} chunks.")


if __name__ == "__main__":
    main()
