"""
Rechunk the corpus: replace load_db.py's page-level chunking with Docling's
structure-aware chunking (real headers/sections, cross-page chunks, exact per-element
bbox provenance for precise PDF highlighting).

Reuses existing `documents` rows (matched by sha256, from parsed_documents.json) --
only a document's `chunks` rows are replaced. Re-embeds every chunk (chunk boundaries
changed, old embeddings don't apply).

Two-pass design so bbox precision is never lost:
1. Docling's HybridChunker produces chunks respecting a generous token ceiling.
2. merge_tiny_runs() merges whole RUNS of consecutive tiny (<TINY_WORDS) chunks into
   one chunk each -- confirmed via pilot testing on 5 real corpus documents that this
   is a recurring structural pattern (front-matter clusters like
   Summary/Abbreviations/Scope/Purpose), not a one-off. Chunks already a reasonable
   size are left untouched -- this is not a blanket floor.
3. semantic_resplit() splits anything still over CEILING_WORDS at doc-item boundaries
   (never mid-paragraph, so every resulting piece's bboxes stay exact), preferring the
   item-boundary with the lowest embedding similarity to its neighbor -- a real
   semantic topic-shift, not an arbitrary word-count cut.

Run (from backend/): python -m ingestion.rechunk [--only FILENAME_SUBSTRING] [--limit N]
"""
import argparse
import os
import sys
import time
from pathlib import Path

from openai import OpenAI
import tiktoken

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, AcceleratorOptions, AcceleratorDevice
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
from docling_core.types.doc.document import RefItem

from app.core.config import DATASET_DIR, PARSED_DOCUMENTS_FILE
from app.core.db import ensure_schema, get_connection, release_connection

EMBED_MODEL = "text-embedding-3-small"  # 1536-dim, matches schema -- unchanged

TINY_WORDS = 100     # runs of 2+ consecutive chunks under this get merged into one
CEILING_WORDS = 700  # chunks over this (after merging) get semantically re-split
MAX_TOKENS = 1400    # generous soft ceiling for Docling's own token-based splitter

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

_pipeline_options = PdfPipelineOptions()
_pipeline_options.do_ocr = False  # these PDFs have real text layers -- same assumption
_pipeline_options.do_table_structure = True
_pipeline_options.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.AUTO)
_converter = DocumentConverter(
    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=_pipeline_options)}
)

_enc = tiktoken.get_encoding("cl100k_base")
_tokenizer = OpenAITokenizer(tokenizer=_enc, max_tokens=MAX_TOKENS)
_chunker = HybridChunker(tokenizer=_tokenizer)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    out = []
    batch_size = 50
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
        out.extend(d.embedding for d in resp.data)
    return out


def _item_dict(item, doc) -> dict:
    # chunk.meta.doc_items entries are unresolved reference stubs (self_ref like
    # "#/texts/40", typed as the generic DocItem base class -- no .text of their own)
    # -- must resolve against the source DoclingDocument to reach the real item.
    resolved = RefItem(cref=item.self_ref).resolve(doc)
    text = getattr(resolved, "text", "") or ""
    provs = resolved.prov or []
    bboxes = []
    pages = []
    for p in provs:
        pages.append(p.page_no)
        page_size = doc.pages[p.page_no].size
        # Normalize to top-left origin, 0-1 fractions of page size -- render-scale
        # independent, so the frontend just multiplies by whatever size it renders at.
        norm = p.bbox.to_top_left_origin(page_height=page_size.height).normalized(page_size)
        bboxes.append({"page_no": p.page_no, "l": norm.l, "t": norm.t, "r": norm.r, "b": norm.b})
    return {"text": text, "pages": sorted(set(pages)), "bboxes": bboxes}


def docling_chunks(pdf_path: Path) -> list[dict]:
    """Convert + chunk one PDF. Each returned chunk keeps its constituent doc_items
    (not just flattened text), so later merge/split passes can recombine bboxes
    exactly instead of guessing."""
    result = _converter.convert(str(pdf_path))
    doc = result.document
    raw = []
    for c in _chunker.chunk(doc):
        items = [_item_dict(item, doc) for item in c.meta.doc_items]
        items = [it for it in items if it["text"].strip()]
        if not items:
            continue
        raw.append({
            "items": items,
            "headings": list(c.meta.headings) if c.meta.headings else [],
        })
    return raw


def _chunk_words(chunk: dict) -> int:
    return sum(len(it["text"].split()) for it in chunk["items"])


def merge_tiny_runs(chunks: list[dict]) -> list[dict]:
    """Merge whole runs of 2+ consecutive tiny chunks into one chunk each. Chunks
    already a reasonable size are left exactly as Docling produced them."""
    merged = []
    i = 0
    while i < len(chunks):
        if _chunk_words(chunks[i]) < TINY_WORDS:
            run = [chunks[i]]
            j = i + 1
            while j < len(chunks) and _chunk_words(chunks[j]) < TINY_WORDS:
                run.append(chunks[j])
                j += 1
            if len(run) >= 2:
                merged.append({
                    "items": [it for c in run for it in c["items"]],
                    "headings": list(dict.fromkeys(h for c in run for h in c["headings"])),
                })
                i = j
                continue
        merged.append(chunks[i])
        i += 1
    return merged


def _cos_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 1.0


def semantic_resplit(chunk: dict) -> list[dict]:
    """Split an oversized chunk at doc-item boundaries only (never mid-item, so bbox
    provenance stays exact), preferring the boundary with the lowest embedding
    similarity between neighbors -- a real topic shift, not an arbitrary word cut."""
    items = chunk["items"]
    if _chunk_words(chunk) <= CEILING_WORDS or len(items) < 2:
        return [chunk]

    texts = [it["text"] for it in items]
    embeds = embed_texts(texts)

    pieces: list[list[dict]] = []
    start = 0
    acc_words = 0
    for i, it in enumerate(items):
        acc_words += len(it["text"].split())
        if acc_words >= CEILING_WORDS and i < len(items) - 1:
            best_j, best_sim = i, 2.0
            for j in range(start, i + 1):
                if j + 1 < len(embeds):
                    sim = _cos_sim(embeds[j], embeds[j + 1])
                    if sim < best_sim:
                        best_sim, best_j = sim, j
            pieces.append(items[start : best_j + 1])
            start = best_j + 1
            acc_words = sum(len(items[k]["text"].split()) for k in range(start, i + 1))
    pieces.append(items[start:])
    pieces = [p for p in pieces if p]

    return [{"items": p, "headings": chunk["headings"]} for p in pieces]


def finalize_chunk(chunk: dict) -> dict:
    items = chunk["items"]
    return {
        "text": "\n\n".join(it["text"] for it in items),
        "heading_path": chunk["headings"],
        "bboxes": [b for it in items for b in it["bboxes"]],
        "pages": sorted({p for it in items for p in it["pages"]}),
    }


def process_document(pdf_path: Path) -> list[dict]:
    raw = docling_chunks(pdf_path)
    merged = merge_tiny_runs(raw)
    resplit = [piece for c in merged for piece in semantic_resplit(c)]
    return [finalize_chunk(c) for c in resplit]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="only process filenames containing this substring")
    parser.add_argument("--limit", type=int, help="stop after N documents")
    args = parser.parse_args()

    import json
    documents = json.loads(PARSED_DOCUMENTS_FILE.read_text(encoding="utf-8"))
    if args.only:
        documents = [d for d in documents if args.only in d["filename"]]
    if args.limit:
        documents = documents[: args.limit]
    print(f"Processing {len(documents)} documents")

    conn = get_connection()
    ensure_schema(conn)

    total_new_chunks = 0
    for doc in documents:
        pdf_path = DATASET_DIR / doc["filename"]
        if not pdf_path.exists():
            print(f"  SKIP (PDF missing on disk): {doc['filename']}")
            continue

        with conn.cursor() as cur:
            cur.execute("SELECT id FROM documents WHERE sha256 = %s", (doc["sha256"],))
            row = cur.fetchone()
        if row is None:
            print(f"  SKIP (not in documents table -- run load_db.py first): {doc['title']}")
            continue
        document_id = row[0]

        t0 = time.time()
        print(f"  chunking: {doc['title']} v{doc['version']} ({doc['filename']}) ...", end=" ", flush=True)
        chunks = process_document(pdf_path)
        convert_time = time.time() - t0

        texts = [c["text"] for c in chunks]
        t1 = time.time()
        embeddings = embed_texts(texts)
        embed_time = time.time() - t1
        print(f"{len(chunks)} chunks (convert {convert_time:.1f}s, embed {embed_time:.1f}s)")

        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
            for c, embedding in zip(chunks, embeddings):
                pages = c["pages"] or [1]
                page_start, page_end = min(pages), max(pages)
                cur.execute(
                    """
                    INSERT INTO chunks
                        (document_id, page, page_start, page_end, heading_path, bboxes,
                         text, embedding, tsv)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                        setweight(to_tsvector('english', %s), 'A') ||
                        setweight(to_tsvector('arabic', %s), 'A'))
                    """,
                    (
                        document_id, page_start, page_start, page_end,
                        c["heading_path"], json.dumps(c["bboxes"]),
                        c["text"], embedding, c["text"], c["text"],
                    ),
                )
                total_new_chunks += 1
        conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM documents")
        doc_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM chunks")
        chunk_count = cur.fetchone()[0]
    release_connection(conn)

    print(f"\nInserted {total_new_chunks} new chunks this run.")
    print(f"Database totals: {doc_count} documents, {chunk_count} chunks.")


if __name__ == "__main__":
    main()
