"""
Mine follow-up question suggestions from each document in parsed_documents.json.

Reads the full corpus, sends each document's text through a LOCAL LLM (LM Studio)
with the SUGGESTION_MINING_PROMPT, and writes JSONL output files
(suggestions.<worker>.jsonl).

Requires LM Studio running locally on :1234 (or set LOCAL_BASE_URL env var).
Default model: qwen/qwen3.5-9b (override with LOCAL_MODEL env var).

Usage (from backend/):
    python -m ingestion.mine_suggestions [--limit N] [--only DOC_CODE_SUBSTRING]
    python -m ingestion.mine_suggestions --start 0 --end 10   # process docs 0-9
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from openai import OpenAI

from app.core.config import BACKEND_DIR, PARSED_DOCUMENTS_FILE

WORKER_ID = os.environ.get("MINING_WORKER", "w0")
OUTPUT_FILE = BACKEND_DIR / f"suggestions.{WORKER_ID}.jsonl"

# LM Studio local model — free, no API costs
LOCAL_BASE_URL = os.environ.get("LOCAL_BASE_URL", "http://localhost:1234/v1")
MODEL = os.environ.get("LOCAL_MODEL", "qwen/qwen3.5-9b")

client = OpenAI(base_url=LOCAL_BASE_URL, api_key="lm-studio")

MINING_PROMPT = """\
You mine user-facing follow-up questions from ONE UAE health-regulation document \
for the ReguLense Q&A system ("Continue exploring" suggestions).

### Input you receive
- DOC_CODE, TITLE, AUTHORITY, TIER (official or research), VERSION
- The document's FULL TEXT with [PAGE n] markers and original section headings \
preserved. Page markers are your only location source — never invent one.

### Task
Write 3–5 questions a real user (clinician, licensing officer, facility admin, \
researcher) would plausibly ask next, each answerable from THIS document alone.

### Rules
1. Grounded. Every question must be answerable using only the input text. If \
the answer needs outside knowledge, drop the question.
2. Specific. Name the concrete topic ("How long is a DHA professional license \
valid before renewal?"). BANNED: "What is this document about?", "Summarize \
this document", "What are the key points?", anything answerable without \
reading this doc.
3. Located. Every question carries: pages (list of page numbers where the \
answer lives), section (nearest heading), and anchor_quote (≤200 \
characters copied EXACTLY from the input — character-for-character, no \
paraphrase, no ellipsis trimming inside the quote).
4. Spread + varied. Cover different sections of the doc, not one paragraph. \
Mix forms (what / how / when / how-long / list / requirements). At most ONE \
yes/no question per document.
5. Scoped. UAE health regulation only. No questions about the Q&A system \
itself ("how does the system decide…"), no legal advice beyond the text, no \
current-events knowledge.
6. Self-contained. The question must make sense to someone who has never seen \
this document (no "in section 3 above…", no "this regulation…", no acronyms \
the question itself doesn't expand or anchor).
7. Tier honesty. If TIER is research, questions must be phrased as \
research findings ("What did the study find about…?"), never as binding \
rules. Never imply a research paper carries regulatory authority.
8. Skip loudly. If the input is cover pages, templates, or noise with no \
substantive content, output exactly one line: SKIP: <one-line reason> and \
nothing else.

### Output — strict JSONL
One JSON object per line. No markdown fences, no commentary, no trailing commas. \
Every line MUST have exactly these fields:

{"question": "...", "doc_code": "...", "authority": "...", "tier": "...", \
"pages": [...], "section": "...", "anchor_quote": "...", "form": "..."}

- form is one of: what, how, when, how-long, list, requirements, yes-no.
- pages must all appear as [PAGE n] markers in the input.
- One line per question, 3–5 lines per document (or the single SKIP line).

### Self-check before emitting (do this silently, then output only the JSONL)
- Could a reader answer each question using ONLY the quoted anchor + its page?
- Does any question duplicate another's answer? Drop the weaker one.
- Is every anchor_quote verbatim from the input? If unsure, shorten the quote \
until it is.
"""


def format_document_text(doc: dict) -> str:
    """Format a parsed document into the prompt's expected input format."""
    parts = []
    parts.append(f"DOC_CODE: {doc.get('doc_code', 'UNKNOWN')}")
    parts.append(f"TITLE: {doc.get('title', 'UNKNOWN')}")
    parts.append(f"AUTHORITY: {doc.get('authority', 'UNKNOWN')}")
    parts.append(f"TIER: official")  # all corpus docs are official regulations
    parts.append(f"VERSION: {doc.get('version', '1')}")
    parts.append("")
    parts.append("=== DOCUMENT TEXT ===")
    parts.append("")
    for i, page_text in enumerate(doc.get("pages", []), start=1):
        parts.append(f"[PAGE {i}]")
        parts.append(page_text)
        parts.append("")
    return "\n".join(parts)


def mine_document(doc: dict, retries: int = 2) -> list[str]:
    """Call the LLM to mine suggestions for one document. Returns JSONL lines."""
    doc_code = doc.get("doc_code", "UNKNOWN")
    user_content = format_document_text(doc)

    messages = [
        {"role": "system", "content": MINING_PROMPT},
        {"role": "user", "content": user_content},
    ]

    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=0,
                max_tokens=2000,
            )
            raw = resp.choices[0].message.content.strip()
            return parse_jsonl_output(raw, doc_code)
        except Exception as e:
            if attempt < retries:
                wait = 2 ** (attempt + 1)
                print(f"  [RETRY {attempt+1}/{retries}] {doc_code}: {e} — waiting {wait}s")
                time.sleep(wait)
            else:
                print(f"  [FAILED] {doc_code}: {e}")
                return []


def parse_jsonl_output(raw: str, doc_code: str) -> list[str]:
    """Parse LLM output into validated JSONL lines. Returns valid lines only."""
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip markdown fences the LLM might wrap around
        if line.startswith("```"):
            continue
        # Handle SKIP
        if line.upper().startswith("SKIP:"):
            print(f"  [SKIP] {doc_code}: {line}")
            return []
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            print(f"  [BAD JSON] {doc_code}: {line[:80]}...")
            continue
        # Validate required fields
        required = ["question", "doc_code", "authority", "tier", "pages",
                     "section", "anchor_quote", "form"]
        if not all(k in obj for k in required):
            missing = [k for k in required if k not in obj]
            print(f"  [MISSING FIELDS] {doc_code}: missing {missing}")
            continue
        if not isinstance(obj["pages"], list) or not obj["pages"]:
            print(f"  [BAD PAGES] {doc_code}: {obj['pages']}")
            continue
        if obj["form"] not in ("what", "how", "when", "how-long", "list",
                               "requirements", "yes-no"):
            print(f"  [BAD FORM] {doc_code}: {obj['form']}")
            continue
        lines.append(json.dumps(obj, ensure_ascii=False))
    return lines


def main():
    parser = argparse.ArgumentParser(description="Mine follow-up question suggestions")
    parser.add_argument("--limit", type=int, default=0, help="Max docs to process (0=all)")
    parser.add_argument("--start", type=int, default=0, help="Start index in corpus")
    parser.add_argument("--end", type=int, default=0, help="End index (0=to end)")
    parser.add_argument("--only", type=str, default="", help="Only docs whose doc_code contains this")
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE), help="Output JSONL path")
    args = parser.parse_args()

    with open(PARSED_DOCUMENTS_FILE) as f:
        docs = json.load(f)

    # Filter superseded docs — no point mining suggestions for outdated versions
    docs = [d for d in docs if not d.get("superseded", False)]

    if args.only:
        docs = [d for d in docs if args.only.lower() in d.get("doc_code", "").lower()]

    # Apply start/end/limit
    start = args.start
    end = args.end if args.end > 0 else len(docs)
    docs = docs[start:end]
    if args.limit > 0:
        docs = docs[:args.limit]

    print(f"Mining suggestions for {len(docs)} documents (worker={WORKER_ID})")
    print(f"Output: {args.output}")
    print()

    output_path = Path(args.output)
    total_questions = 0
    skipped = 0
    failed = 0

    with open(output_path, "w") as out:
        for i, doc in enumerate(docs):
            doc_code = doc.get("doc_code", "UNKNOWN")
            title = doc.get("title", "")[:60]
            pages = len(doc.get("pages", []))
            print(f"[{i+1}/{len(docs)}] {doc_code} — {title} ({pages} pages)")

            lines = mine_document(doc)
            if not lines:
                # Check if it was a SKIP or a failure
                # Re-run to distinguish (quick skip check)
                skipped += 1
            else:
                for line in lines:
                    out.write(line + "\n")
                total_questions += len(lines)
                print(f"  → {len(lines)} suggestions")

            # Small delay to avoid saturating local model
            time.sleep(0.5)

    print()
    print(f"Done. {total_questions} questions from {len(docs) - skipped} docs "
          f"({skipped} skipped, {failed} failed)")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
