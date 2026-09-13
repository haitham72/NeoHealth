# Follow-up Question Mining Prompt — ReguLense corpus

How to use this file: give the PROMPT section below to any light LLM worker along
with ONE document's chunk text from the database (`documents WHERE superseded =
false`, `chunks ORDER BY id`) — the same text `load_suggestions.py` verifies
anchors against, so a quote valid here is resolvable there.
`ingestion/mine_suggestions.py` does this end-to-end: it renders each chunk under
its real page marker, self-verifies every anchor and page before writing (dropping
unverifiable entries, retrying once with the failing quotes as feedback), and
writes `suggestions.<worker>.jsonl`. Load them with
`python -m ingestion.load_suggestions [--mined-by NAME] suggestions.<worker>.jsonl`,
which validates lines, resolves `anchor_quote` text to chunk IDs, embeds the
questions, and upserts into the `suggested_questions` table (idempotent per
`(question_normalized, document_id)`). See "PARALLEL PROTOCOL" for multi-worker rules.

---

## PROMPT (paste everything below this line into the worker)

You mine user-facing follow-up questions from ONE UAE health-regulation document
for the ReguLense Q&A system ("Continue exploring" suggestions).

### Input you receive
- `DOC_CODE`, `TITLE`, `AUTHORITY` (e.g. Dubai Health Authority), `TIER`
  (`official` regulation or `research` paper), `VERSION`
- The document's text as consecutive chunks, each preceded by a page marker:
  `[PAGE n]`, or `[PAGE n-m]` for a chunk spanning pages. These markers are the
  ONLY real page numbers in the document — never cite a page that has no marker.

### Task
Write 1-2 questions a real user (clinician, licensing officer, facility admin,
researcher) would plausibly ask next, **each answerable from THIS document alone**.
Every question must earn its slot — do not pad.

### Rules
1. **Grounded.** Every question must be answerable using only the input text. If
   the answer needs outside knowledge, drop the question.
2. **Specific.** Name the concrete topic ("How long is a DHA professional license
   valid before renewal?"). BANNED: "What is this document about?", "Summarize
   this document", "What are the key points?", anything answerable without
   reading this doc.
3. **Located.** Every question carries: `pages` (list of page numbers that appear
   in `[PAGE ...]` markers, where the answer lives), `section` (nearest heading),
   and `anchor_quote` (≤200 characters copied EXACTLY from the text between the
   page markers — character-for-character, same wording and punctuation, no
   paraphrase, no ellipsis). Quotes are checked automatically: a quote that is
   not found verbatim is rejected.
4. **Spread + varied.** Cover different sections of the doc, not one paragraph.
   Mix forms (what / how / when / how-long / list / requirements). At most ONE
   yes/no question per document.
5. **Scoped.** UAE health regulation only. No questions about the Q&A system
   itself ("how does the system decide…"), no legal advice beyond the text, no
   current-events knowledge.
6. **Self-contained.** The question must make sense to someone who has never seen
   this document (no "in section 3 above…", no "this regulation…", no acronyms
   the question itself doesn't expand or anchor).
7. **Tier honesty.** If `TIER` is `research`, questions must be phrased as
   research findings ("What did the study find about…?"), never as binding
   rules. Never imply a research paper carries regulatory authority.
8. **Skip loudly.** If the input is cover pages, templates, or noise with no
   substantive content, output exactly one line: `SKIP: <one-line reason>` and
   nothing else.

### Output — strict JSONL
One JSON object per line. No markdown fences, no commentary, no trailing commas.
Every line MUST have exactly these fields:

```json
{"question": "How long is a DHA professional license valid before renewal?",
 "doc_code": "DHA/HRS/HPSD/ST-14",
 "authority": "Dubai Health Authority",
 "tier": "official",
 "pages": [12, 13],
 "section": "License validity and renewal",
 "anchor_quote": "A professional license shall remain valid for a period of two years from the date of issuance",
 "form": "how-long"}
```

- `form` is one of: what, how, when, how-long, list, requirements, yes-no.
- `pages` must be page numbers that appear in `[PAGE ...]` markers in the input.
- One line per question, 1-2 lines per document (or the single SKIP line).

### Self-check before emitting (do this silently, then output only the JSONL)
- Could a reader answer each question using ONLY the quoted anchor + its page?
- Does any question duplicate another's answer? Drop the weaker one.
- Is every `anchor_quote` verbatim from the input? If unsure, shorten the quote
  until it is.

---

## PARALLEL PROTOCOL (for the orchestrator, not the worker LLM)

- **Partition by document, never by page.** Workers claim disjoint `doc_code`
  sets from a claim list; two workers never touch the same file (dedup across
  halves of one doc is unreliable).
- **Append-only outputs.** Each worker writes ONLY its own
  `suggestions.<worker>.jsonl`. Never edit another worker's file.
- **Resumable runs.** `mine_suggestions.py` records each document's terminal
  status (`done`/`skip`/`failed`) in `backend/mining_state.json` (gitignored).
  Later runs skip finished documents at their original scope positions — restart
  at doc 34 of 35 and the log still numbers it `[34/35]` — and retry `failed`
  ones. `--force` re-mines finished documents deliberately.
- **Sensitive docs: skip immediately, never retry.** If a document trips the
  provider's sensitive-content filter, record `status: skip` for it in
  `mining_state.json` right away and never resend it to a cloud worker. Local
  fallback only if explicitly requested; the current corpus policy is skip
  outright (max 2 cloud attempts ever for a flagged document).
- **One doc per call.** If a document exceeds the worker's context, split into
  front/back halves in SEPARATE calls and dedup overlapping questions manually
  before appending.
- **Loader contract (enforced at load, not by the worker):** every line must
  parse as JSON with all 8 fields; every `anchor_quote` must occur verbatim in
  the source text (reject the line otherwise); dedup key is
  (normalized question, doc_code) so re-running a file safely overwrites that
  doc's rows; the loader resolves anchors to `chunk_ids` via phrase match
  against `chunks.text` and embeds questions with `text-embedding-3-small`
  into a `question_embedding vector(1536)` column.
- **Runtime use (implemented):** `app/services/suggestions.py` cosine-matches the
  live `query_vec` (already computed by the pipeline, so matching is free) against
  `suggested_questions.question_embedding` (same-authority first, exclude the asked
  question), takes top 1-4, and returns them as `suggested_followups`. Clicking a
  suggestion re-enters the normal pipeline (supersession/authority filters and
  freshness intact); the stored `document_id`/`chunk_ids`/`pages` travel as
  provenance metadata (e.g. "from DHA/ST-14 §3"), NOT as a retrieval bypass —
  answering purely from stored chunk IDs would serve superseded versions after
  the corpus moves on.

## Table (created by `db.SCHEMA_SQL`, no separate migration step)

```sql
CREATE TABLE IF NOT EXISTS suggested_questions (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    question_normalized TEXT NOT NULL,
    question_embedding vector(1536) NOT NULL,
    doc_code TEXT NOT NULL,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    chunk_ids INTEGER[] NOT NULL DEFAULT '{}',
    pages INTEGER[] NOT NULL DEFAULT '{}',
    section TEXT,
    authority TEXT,
    tier TEXT NOT NULL DEFAULT 'official',
    mined_by TEXT,               -- worker/model id that produced it
    use_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (question_normalized, document_id)
);
```
