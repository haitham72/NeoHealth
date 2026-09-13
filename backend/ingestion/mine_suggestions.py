"""
Mine follow-up question suggestions from the in-force corpus in Postgres.

Source is the database itself -- documents (WHERE superseded = false) and their
chunks (ORDER BY id) -- not parsed_documents.json: load_suggestions.py verifies
every anchor_quote VERBATIM (whitespace-collapsed) against chunks.text, and the
rechunk step's Docling extraction differs from parsed_documents.json's pdfplumber
text, so a quote valid in one can be absent from the other. Mining from the exact
text the app indexes makes every kept anchor resolvable by construction.

Each chunk is rendered under its real page marker -- [PAGE n], or [PAGE n-m] for
a chunk spanning pages (chunks.page_start/page_end) -- and the model is told those
are its only allowed pages. After each LLM call the output is self-verified: the
anchor must occur verbatim (whitespace-collapsed, same normalization the loader
uses) in the document's chunks, and every listed page must actually exist in the
document. Entries that fail are dropped and logged; one retry that feeds the
failing quotes back to the model gives it a chance to copy them exactly.

Each document's terminal outcome is recorded in backend/mining_state.json.
Documents already finished (mined or SKIPped) are excluded from later runs, so
re-running the same command resumes at the first unfinished document instead of
restarting from position 1 -- the [i/N] progress numbers always reflect the
command's scope (e.g. resuming a 35-doc scope at doc 34 continues showing
[34/35]). --force re-mines finished documents anyway; failed API calls stay
retryable automatically.

Requires Postgres (backend/.env) and LM Studio on :1234 (or LOCAL_BASE_URL).
Default model: qwen/qwen3-4b-2507 (override with LOCAL_MODEL env var).

Usage (from backend/):
    python -m ingestion.mine_suggestions [--limit N] [--only DOC_CODE_SUBSTRING]
    python -m ingestion.mine_suggestions --start 21 --end 36  # positions 21-35
    python -m ingestion.mine_suggestions --force              # re-mine finished docs
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

from openai import OpenAI

from app.core.answer_cache import normalize_question
from app.core.config import BACKEND_DIR
from app.core.db import get_connection, release_connection

WORKER_ID = os.environ.get("MINING_WORKER", "w0")
OUTPUT_FILE = BACKEND_DIR / f"suggestions.{WORKER_ID}.jsonl"

# Terminal per-document outcomes, so later runs resume instead of redoing work.
STATE_FILE = BACKEND_DIR / "mining_state.json"
FINISHED_STATUSES = {"done", "skip"}

# LM Studio local model — free, no API costs
LOCAL_BASE_URL = os.environ.get("LOCAL_BASE_URL", "http://localhost:1234/v1")
MODEL = os.environ.get("LOCAL_MODEL", "qwen/qwen3-4b-2507")

client = OpenAI(base_url=LOCAL_BASE_URL, api_key="lm-studio")

FORM_ALLOW = {"what", "how", "when", "how-long", "list", "requirements", "yes-no"}
REQUIRED_FIELDS = ["question", "doc_code", "authority", "tier", "pages",
                   "section", "anchor_quote", "form"]

MINING_PROMPT = """\
You mine user-facing follow-up questions from ONE UAE health-regulation document \
for the ReguLense Q&A system ("Continue exploring" suggestions).

### Input you receive
- DOC_CODE, TITLE, AUTHORITY, TIER (official or research), VERSION
- The document's text as consecutive chunks, each preceded by a page marker: \
[PAGE n], or [PAGE n-m] for a chunk that spans pages. These markers are the ONLY \
real page numbers in the document -- never cite a page that has no marker.

### Task
Write 1-2 questions a real user (clinician, licensing officer, facility admin, \
researcher) would plausibly ask next, each answerable from THIS document alone. \
Every question must earn its slot -- do not pad.

### Rules
1. Grounded. Every question must be answerable using only the input text. If \
the answer needs outside knowledge, drop the question.
2. Specific. Name the concrete topic ("How long is a DHA professional license \
valid before renewal?"). BANNED: "What is this document about?", "Summarize \
this document", "What are the key points?", anything answerable without \
reading this doc.
3. Located. Every question carries: pages (list of page numbers that appear in \
[PAGE ...] markers, where the answer lives), section (nearest heading), and \
anchor_quote (<=200 characters copied EXACTLY from the text between the page \
markers -- character-for-character, same wording and punctuation, no paraphrase, \
no ellipsis). Quotes are checked automatically: a quote that is not found \
verbatim is rejected.
4. Spread + varied. Cover different sections of the doc, not one paragraph. \
Mix forms (what / how / when / how-long / list / requirements). At most ONE \
yes/no question per document.
5. Scoped. UAE health regulation only. No questions about the Q&A system \
itself ("how does the system decide..."), no legal advice beyond the text, no \
current-events knowledge.
6. Self-contained. The question must make sense to someone who has never seen \
this document (no "in section 3 above...", no "this regulation...", no acronyms \
the question itself doesn't expand or anchor).
7. Tier honesty. If TIER is research, questions must be phrased as \
research findings ("What did the study find about...?"), never as binding \
rules. Never imply a research paper carries regulatory authority.
8. Skip loudly. If the input is cover pages, templates, or noise with no \
substantive content, output exactly one line: SKIP: <one-line reason> and \
nothing else.

### Output -- strict JSONL
One JSON object per line. No markdown fences, no commentary, no trailing commas. \
Every line MUST have exactly these fields:

{"question": "...", "doc_code": "...", "authority": "...", "tier": "...", \
"pages": [...], "section": "...", "anchor_quote": "...", "form": "..."}

- form is one of: what, how, when, how-long, list, requirements, yes-no.
- pages must be page numbers that appear in [PAGE ...] markers.
- One line per question, 1-2 lines per document (or the single SKIP line).

### Self-check before emitting (do this silently, then output only the JSONL)
- Could a reader answer each question using ONLY the quoted anchor + its page?
- Does any question duplicate another's answer? Drop the weaker one.
- Is every anchor_quote verbatim from the input? If unsure, shorten the quote \
until it is.
"""


def collapse(text: str) -> str:
    """Same whitespace normalization the loader uses to verify anchors."""
    return re.sub(r"\s+", " ", text or "").strip()


# --- resume state -----------------------------------------------------------


def load_state() -> dict:
    """Per-document terminal outcomes from previous runs. A missing or corrupt
    file is treated as empty state (a fresh mine), never a hard error -- state is
    an optimization, not a source of truth."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def record_state(doc_code: str, **fields) -> None:
    """Merge one document's outcome into the state file, written atomically
    (tmp + os.replace). Read-modify-write assumes runs are sequential, which the
    local-model workflow is; parallel workers would need a lock."""
    state = load_state()
    entry = state.get(doc_code, {})
    entry.update(fields)
    entry["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state[doc_code] = entry
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, STATE_FILE)


# --- database source --------------------------------------------------------


def fetch_documents(conn, only: str, start: int, end: int) -> list[dict]:
    """In-force documents in stable id order, filtered the same way --start/--end/
    --only always worked on parsed_documents.json. These positions are the
    command's scope: resume numbering is relative to them, so a finished document
    stays visible as a skipped position instead of shifting the count."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, doc_code, version, authority, tier
            FROM documents
            WHERE superseded = false
            ORDER BY id
            """
        )
        rows = cur.fetchall()
    docs = [
        {"id": r[0], "title": r[1], "doc_code": r[2], "version": r[3],
         "authority": r[4], "tier": r[5]}
        for r in rows
    ]
    if only:
        docs = [d for d in docs if only.lower() in d["doc_code"].lower()]
    end = end if end > 0 else len(docs)
    return docs[start:end]


def fetch_document(conn, doc_code: str) -> dict | None:
    """One in-force document by doc_code, or None if unknown/superseded-only."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, doc_code, version, authority, tier
            FROM documents
            WHERE doc_code = %s AND superseded = false
            ORDER BY id DESC LIMIT 1
            """,
            (doc_code,),
        )
        r = cur.fetchone()
    if r is None:
        return None
    return {"id": r[0], "title": r[1], "doc_code": r[2], "version": r[3],
            "authority": r[4], "tier": r[5]}


def fetch_chunks(conn, document_id: int) -> list[dict]:
    """Every chunk of one document in id order -- the exact rows (and order) the
    loader concatenates when it verifies an anchor."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, page, page_start, page_end, text FROM chunks "
            "WHERE document_id = %s ORDER BY id",
            (document_id,),
        )
        rows = cur.fetchall()
    chunks = []
    for cid, page, page_start, page_end, text in rows:
        first = page_start if page_start is not None else page
        last = page_end if page_end is not None else first
        chunks.append({"id": cid, "page_start": first, "page_end": last,
                       "text": text or ""})
    return chunks


def page_marker(chunk: dict) -> str:
    if chunk["page_end"] == chunk["page_start"]:
        return f"[PAGE {chunk['page_start']}]"
    return f"[PAGE {chunk['page_start']}-{chunk['page_end']}]"


def format_document_text(doc: dict, chunks: list[dict]) -> str:
    """Render one document exactly as the prompt expects: real metadata header,
    then chunks each under their real page marker(s)."""
    parts = [
        f"DOC_CODE: {doc['doc_code']}",
        f"TITLE: {doc['title']}",
        f"AUTHORITY: {doc['authority']}",
        f"TIER: {doc['tier']}",
        f"VERSION: {doc['version']}",
        "",
        "=== DOCUMENT TEXT ===",
        "",
    ]
    for chunk in chunks:
        parts.append(page_marker(chunk))
        parts.append(chunk["text"])
        parts.append("")
    return "\n".join(parts)


# --- LLM call + parsing -----------------------------------------------------


def request_llm(messages: list[dict], doc_code: str, retries: int = 2) -> str | None:
    """One chat completion; retries transport/API errors with backoff. None when
    every attempt fails (a hard failure, not a SKIP)."""
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL, messages=messages, temperature=0, max_tokens=4000,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            if attempt < retries:
                wait = 2 ** (attempt + 1)
                print(f"  [RETRY {attempt + 1}/{retries}] {doc_code}: {e} — waiting {wait}s")
                time.sleep(wait)
            else:
                print(f"  [FAILED] {doc_code}: {e}")
                return None


def _structural_rejection(entry: dict) -> str | None:
    """The loader's _validate_entry, mirrored so nothing structurally doomed gets
    written. Returns a reason, or None if the entry can proceed to anchor/page
    verification."""
    missing = [k for k in REQUIRED_FIELDS if k not in entry]
    if missing:
        return f"missing fields {missing}"
    if not isinstance(entry["question"], str) or not entry["question"].strip():
        return "empty question"
    if (not isinstance(entry["pages"], list) or not entry["pages"]
            or not all(isinstance(p, int) for p in entry["pages"])):
        return "pages must be a non-empty int list"
    if not isinstance(entry["anchor_quote"], str) or not entry["anchor_quote"].strip():
        return "empty anchor_quote"
    if entry["form"] not in FORM_ALLOW:
        return f"form must be one of {sorted(FORM_ALLOW)}"
    return None


def parse_output(raw: str, doc_code: str) -> tuple[list[dict], str | None, set[str]]:
    """Extract every complete top-level JSON object from the model's reply.

    Smaller local models often ignore strict JSONL instructions (pretty-printed
    multi-line JSON, markdown fences), so scan with JSONDecoder.raw_decode instead
    of parsing line-by-line. Returns (structurally valid entries, SKIP line or
    None, normalized questions dropped for structural reasons)."""
    text = raw.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()

    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if first_line.upper().startswith("SKIP:"):
        return [], first_line, set()

    text = "\n".join(
        ln for ln in text.splitlines() if not ln.strip().upper().startswith("SKIP:")
    )

    decoder = json.JSONDecoder()
    entries: list[dict] = []
    dropped: set[str] = set()
    i, n = 0, len(text)
    while i < n:
        if text[i] in " \t\r\n,":
            i += 1
            continue
        try:
            obj, i = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            nxt = text.find("{", i + 1)
            if nxt == -1:
                tail = text[i:i + 80].strip()
                if tail:
                    print(f"  [PARSE] {doc_code}: unparseable output near {tail!r}")
                break
            print(f"  [PARSE] {doc_code}: skipping non-JSON output before next object")
            i = nxt
            continue
        if not isinstance(obj, dict):
            continue
        reason = _structural_rejection(obj)
        if reason:
            print(f"  [DROP] {doc_code}: {reason}")
            q = obj.get("question")
            dropped.add(normalize_question(q) if isinstance(q, str) and q.strip()
                        else f"<unnamed entry {len(dropped)}>")
            continue
        entries.append(obj)
    return entries, None, dropped


# --- verification -----------------------------------------------------------


def verify_entries(entries: list[dict], doc_text: str, pages_present: set[int]):
    """Anchors must occur verbatim (whitespace-collapsed, the loader's exact
    normalization) in the document's chunk text; every listed page must be a page
    the document actually has. Returns (kept, [(entry, reason), ...])."""
    kept, bad = [], []
    for entry in entries:
        if collapse(entry["anchor_quote"]) not in doc_text:
            bad.append((entry, f"anchor not found verbatim: {entry['anchor_quote'][:100]!r}"))
            continue
        bad_pages = [p for p in entry["pages"] if p not in pages_present]
        if bad_pages:
            bad.append((entry, f"pages {bad_pages} not present in document"))
            continue
        kept.append(entry)
    return kept, bad


def dedupe(entries: list[dict]) -> list[dict]:
    """One question per normalized question string per document -- matches the
    loader's upsert key so a re-emitted fix never duplicates a kept entry."""
    seen: set[str] = set()
    out = []
    for entry in entries:
        key = normalize_question(entry["question"])
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def build_feedback(bad: list[tuple[dict, str]]) -> str:
    """The retry message: hand the model its rejected quotes back and ask for a
    corrected JSONL containing only those entries."""
    lines = []
    for entry, reason in bad:
        if reason.startswith("anchor"):
            quote = entry.get("anchor_quote", "")
            lines.append(
                f'This quote was not found verbatim in the document: "{quote}". '
                "Copy it exactly from the text, or drop the question."
            )
        elif reason.startswith("pages"):
            lines.append(
                f"These pages do not appear in the document: {entry.get('pages')}. "
                "Use only pages shown in [PAGE ...] markers, or drop the question."
            )
        else:
            lines.append(f"Entry rejected ({reason}). Fix it or drop the question.")
    return (
        "Some entries were rejected by automatic verification:\n\n"
        + "\n".join(lines)
        + "\n\nReturn a corrected strict JSONL containing ONLY these entries "
        "(drop any you cannot fix). No commentary."
    )


def canonical_entry(entry: dict, doc: dict) -> dict:
    """Force every metadata field from the DB row (the model cannot invent a
    doc_code/authority/tier the loader would resolve differently) and emit the
    canonical 8-field order."""
    section = entry.get("section")
    return {
        "question": entry["question"].strip(),
        "doc_code": doc["doc_code"],
        "authority": doc["authority"],
        "tier": doc["tier"],
        "pages": sorted(set(entry["pages"])),
        "section": section if isinstance(section, str) else "",
        "anchor_quote": entry["anchor_quote"],
        "form": entry["form"],
    }


# --- per-document orchestration ---------------------------------------------


def mine_document(doc: dict, chunks: list[dict]) -> dict:
    """Returns {"entries": [...], "dropped": int, "skip": str | None,
    "failed": str | None}. One retry with the failing quotes as feedback."""
    result = {"entries": [], "dropped": 0, "skip": None, "failed": None}
    if not chunks:
        result["failed"] = "document has no chunks"
        return result

    doc_text = " ".join(collapse(c["text"]) for c in chunks)
    pages_present: set[int] = set()
    for chunk in chunks:
        pages_present.update(range(chunk["page_start"], chunk["page_end"] + 1))

    messages = [
        {"role": "system", "content": MINING_PROMPT},
        {"role": "user", "content": format_document_text(doc, chunks)},
    ]

    raw = request_llm(messages, doc["doc_code"])
    if raw is None:
        result["failed"] = "LLM call failed"
        return result

    entries, skip, dropped_norms = parse_output(raw, doc["doc_code"])
    if skip is not None:
        result["skip"] = skip
        return result

    kept, bad = verify_entries(entries, doc_text, pages_present)
    kept = dedupe(kept)
    for entry, reason in bad:
        print(f"  [DROP] {doc['doc_code']}: {reason}")

    if bad:
        retry_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": build_feedback(bad)},
        ]
        raw2 = request_llm(retry_messages, doc["doc_code"])
        if raw2 is not None:
            entries2, _skip2, dropped_norms2 = parse_output(raw2, doc["doc_code"])
            kept2, bad2 = verify_entries(entries2, doc_text, pages_present)
            kept2 = dedupe(kept2)
            seen = {normalize_question(e["question"]) for e in kept}
            for entry in kept2:
                key = normalize_question(entry["question"])
                if key not in seen:
                    kept.append(entry)
                    seen.add(key)
            for entry, reason in bad2:
                print(f"  [DROP] {doc['doc_code']}: {reason}")
            dropped_norms |= {normalize_question(e["question"]) for e, _ in bad2}
            dropped_norms |= dropped_norms2

    kept_keys = {normalize_question(e["question"]) for e in kept}
    result["entries"] = kept
    result["dropped"] = len(dropped_norms - kept_keys)
    return result


def verify_file(conn, path: Path, doc_code: str) -> dict:
    """Verify an externally mined JSONL file (one document per file) against the
    document's DB chunks -- the same checks mine_document applies to its own
    output -- then rewrite the file with only loadable lines and record terminal
    state so the document is excluded from later runs. Returns
    {"kept": n, "dropped": n, "skip": bool}."""
    doc = fetch_document(conn, doc_code)
    if doc is None:
        print(f"{path.name}: unknown in-force doc_code {doc_code!r} -- nothing recorded")
        return {"kept": 0, "dropped": 0, "skip": False}
    chunks = fetch_chunks(conn, doc["id"])
    doc_text = " ".join(collapse(c["text"]) for c in chunks)
    pages_present: set[int] = set()
    for chunk in chunks:
        pages_present.update(range(chunk["page_start"], chunk["page_end"] + 1))

    kept, dropped, skip_line = [], 0, None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("SKIP:"):
            skip_line = skip_line or line
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            print(f"{path.name}: reject (bad JSON: {line[:70]!r})")
            dropped += 1
            continue
        reason = "not a JSON object" if not isinstance(entry, dict) else _structural_rejection(entry)
        if reason:
            print(f"{path.name}: reject ({reason})")
            dropped += 1
            continue
        entry = canonical_entry(entry, doc)  # force DB metadata
        ok, bad = verify_entries([entry], doc_text, pages_present)
        if bad:
            print(f"{path.name}: reject ({bad[0][1]})")
            dropped += 1
            continue
        kept.append(ok[0])

    kept = dedupe(kept)
    out_lines = [json.dumps(e, ensure_ascii=False) for e in kept]
    if skip_line is not None:
        out_lines.append(skip_line)
    path.write_text(("\n".join(out_lines) + "\n") if out_lines else "", encoding="utf-8")

    if kept:
        record_state(doc_code, status="done", kept=len(kept), dropped=dropped,
                     verified="external", output=str(path))
    elif skip_line is not None:
        record_state(doc_code, status="skip", reason=skip_line,
                     verified="external", output=str(path))
    else:
        record_state(doc_code, status="failed",
                     reason="no valid entries after verification", output=str(path))

    stats = {"kept": len(kept), "dropped": dropped, "skip": skip_line is not None}
    print(f"{path.name} [{doc_code}]: {stats}")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Mine follow-up question suggestions")
    parser.add_argument("--limit", type=int, default=0, help="Max unfinished docs to process (0=all)")
    parser.add_argument("--start", type=int, default=0, help="Start position in scope")
    parser.add_argument("--end", type=int, default=0, help="End position (0=to end)")
    parser.add_argument("--only", type=str, default="", help="Only docs whose doc_code contains this")
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE), help="Output JSONL path")
    parser.add_argument("--force", action="store_true",
                        help="Re-mine documents the state file already marks finished")
    parser.add_argument("--verify", nargs="+", default=None, metavar="FILE:DOC_CODE",
                        help="Verify externally mined JSONL files (one document each), "
                             "rewrite with only loadable lines, and record state")
    args = parser.parse_args()

    if args.verify:
        conn = get_connection()
        try:
            totals = {"kept": 0, "dropped": 0, "skip": 0}
            for pair in args.verify:
                if ":" not in pair:
                    print(f"--verify expects FILE:DOC_CODE, got {pair!r}")
                    continue
                file_str, doc_code = pair.rsplit(":", 1)
                stats = verify_file(conn, Path(file_str), doc_code)
                totals["kept"] += stats["kept"]
                totals["dropped"] += stats["dropped"]
                totals["skip"] += 1 if stats["skip"] else 0
            print(f"VERIFY TOTAL: {totals}")
        finally:
            release_connection(conn)
        return

    conn = get_connection()
    try:
        scoped = fetch_documents(conn, args.only, args.start, args.end)
        state = load_state()
        finished = {
            d["doc_code"]: state[d["doc_code"]].get("status")
            for d in scoped
            if state.get(d["doc_code"], {}).get("status") in FINISHED_STATUSES
        }
        if args.force:
            pending = list(scoped)
        else:
            pending = [d for d in scoped if d["doc_code"] not in finished]
        if args.limit > 0:
            pending = pending[:args.limit]
        pending_codes = {d["doc_code"] for d in pending}

        print(f"Mining suggestions: {len(pending)} pending, {len(finished)} already "
              f"finished, {len(scoped)} in scope (worker={WORKER_ID})")
        if args.force:
            print("--force: finished documents will be re-mined")
        if not pending:
            print("Nothing to do -- every document in scope is finished "
                  "(use --force to re-mine).")
        print(f"Output: {args.output}")
        print()

        totals = {"kept": 0, "dropped": 0, "skipped": 0, "failed": 0}
        output_path = Path(args.output)
        with open(output_path, "w") as out:
            for i, doc in enumerate(scoped):
                label = f"[{i + 1}/{len(scoped)}] {doc['doc_code']}"
                if doc["doc_code"] not in pending_codes:
                    print(f"{label} — finished ({finished.get(doc['doc_code'])}), skipping")
                    continue
                chunks = fetch_chunks(conn, doc["id"])
                print(f"{label} — {doc['title'][:60]} ({len(chunks)} chunks, tier={doc['tier']})")
                try:
                    result = mine_document(doc, chunks)
                except Exception as e:
                    print(f"  [FAILED] {doc['doc_code']}: {e!r}")
                    totals["failed"] += 1
                    record_state(doc["doc_code"], status="failed", reason=repr(e),
                                 output=str(output_path))
                    continue

                if result["skip"] is not None:
                    out.write(f"SKIP: {result['skip']}\n")
                    out.flush()  # durable before the state file says "done"
                    totals["skipped"] += 1
                    print(f"  [SKIP] {result['skip']}")
                    record_state(doc["doc_code"], status="skip",
                                 reason=result["skip"], output=str(output_path))
                elif result["failed"] is not None:
                    totals["failed"] += 1
                    print(f"  [FAILED] {result['failed']}")
                    record_state(doc["doc_code"], status="failed",
                                 reason=result["failed"], output=str(output_path))
                else:
                    for entry in result["entries"]:
                        out.write(json.dumps(canonical_entry(entry, doc), ensure_ascii=False) + "\n")
                    out.flush()  # durable before the state file says "done"
                    totals["kept"] += len(result["entries"])
                    totals["dropped"] += result["dropped"]
                    if result["entries"]:
                        print(f"  → kept {len(result['entries'])}, dropped {result['dropped']} unverifiable")
                        record_state(doc["doc_code"], status="done",
                                     kept=len(result["entries"]), dropped=result["dropped"],
                                     output=str(output_path))
                    else:
                        # Zero valid entries after the retry pass is a failure, not a
                        # finished mine -- keep it retryable (e.g. with a stronger model).
                        totals["failed"] += 1
                        print(f"  [FAILED] no valid entries after retry "
                              f"({result['dropped']} dropped unverifiable)")
                        record_state(doc["doc_code"], status="failed",
                                     reason="no valid entries after retry",
                                     dropped=result["dropped"], output=str(output_path))

                # Small delay to avoid saturating local model
                time.sleep(0.5)

        print()
        print(f"Done. kept={totals['kept']} dropped={totals['dropped']} "
              f"skipped={totals['skipped']} failed={totals['failed']} (docs={len(scoped)})")
        print(f"Output: {output_path}")
        print(f"State:  {STATE_FILE}")
    finally:
        release_connection(conn)


if __name__ == "__main__":
    main()
