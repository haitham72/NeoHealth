# Follow-up Question Mining Prompt — ReguLense corpus

How to use this file: give the PROMPT section below to any light LLM worker along
with ONE document's text. Each worker appends output lines to its own
`suggestions.<worker>.jsonl`. Load them with
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
- The document's FULL TEXT with `[PAGE n]` markers and original section headings
  preserved. Page markers are your only location source — never invent one.

### Task
Write 3–5 questions a real user (clinician, licensing officer, facility admin,
researcher) would plausibly ask next, **each answerable from THIS document alone**.

### Rules
1. **Grounded.** Every question must be answerable using only the input text. If
   the answer needs outside knowledge, drop the question.
2. **Specific.** Name the concrete topic ("How long is a DHA professional license
   valid before renewal?"). BANNED: "What is this document about?", "Summarize
   this document", "What are the key points?", anything answerable without
   reading this doc.
3. **Located.** Every question carries: `pages` (list of page numbers where the
   answer lives), `section` (nearest heading), and `anchor_quote` (≤200
   characters copied EXACTLY from the input — character-for-character, no
   paraphrase, no ellipsis trimming inside the quote).
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
- `pages` must all appear as `[PAGE n]` markers in the input.
- One line per question, 3–5 lines per document (or the single SKIP line).

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
