# ReguLense — Technical Guide

Comprehensive walkthrough of the system: pipeline, algorithms, schema, every non-obvious
design decision and the reasoning behind it, known limitations, and a Q&A section for
questions a technical reviewer is likely to ask. Written to be defensible under
questioning, not just descriptive.

---

## 1. System overview

ReguLense is a hybrid-retrieval question-answering system over UAE health regulation (DHA +
DoH + MOHAP public PDFs) with two properties that ordinary RAG lacks: **version
awareness** and **jurisdiction awareness**. Every regulation document is tracked with an
effective date; when a newer version of the same document exists, older versions are
flagged `superseded` and excluded from retrieval by default. Every document also carries
its issuing `authority` (Dubai Health Authority, Department of Health - Abu Dhabi, or the
federal Ministry of Health and Prevention, which is the actual regulator for the "Northern
Emirates" — Ajman, Umm Al Quwain, Ras Al Khaimah, Fujairah — since none of them has a
separate emirate-level health authority); an optional `authority_filter` restricts
retrieval to one jurisdiction, so a Dubai-context question can't silently get grounded in
an Abu Dhabi regulation or vice versa. The system also **tiers its own confidence** —
high/medium/low based on the top retrieved chunk's semantic score — answering normally at
high confidence, answering with an explicit in-text certainty caveat at medium/low, and
abstaining entirely below the lowest floor, rather than generating a fluent but ungrounded
answer with no signal either way (§4.3).

The corpus also carries a `tier` per document (`official` / `research` / `commentary`) —
37 official DHA/DoH/MOHAP regulations plus 3 on-topic academic papers by one of the
interviewing firm's own co-founders sit in the same index, but the research papers are
never dressed up with regulation styling (§6.3). Generation can run against OpenAI
(`gpt-4o-mini`) or fully locally via LM Studio's OpenAI-compatible server (§4.5) —
retrieval and embeddings always stay on OpenAI regardless, since the corpus is already
embedded at 1536-dim and a local embedding model would need a different dimension and a
full re-embed.

Five moving parts:

1. **Ingestion** (`backend/ingestion/download.py`, `backend/ingestion/ingest.py`, `backend/ingestion/apply_manual_fixes.py`) — PDF → text →
   parsed metadata → supersession resolution.
2. **Storage** (`backend/app/core/db.py`, `backend/ingestion/load_db.py`) — Postgres + pgvector, chunked and embedded.
3. **Retrieval** (`backend/app/core/retrieval.py`) — hybrid search, fusion, filtering, abstention, grounded
   generation. This is the core contract every other layer builds on.
4. **API** (`backend/app/`) — a thin FastAPI wrapper exposing `backend/app/core/retrieval.py` over HTTP.
5. **Frontend** (`frontend/`) — React UI consuming the API.

---

## 2. Ingestion pipeline

### 2.1 Download

`backend/ingestion/download.py` reads `corpus_urls.txt`, downloads each PDF into `dataset/`, skips files
already present (checked by filename), and prints a pass/fail summary. Nothing clever — no
pagination or crawling logic is built into the script itself; URL *discovery* at scale (65
URLs currently, up from an initial 17) was done externally by prompting search-capable LLM
agents to crawl DHA/DoH's public standards pages, then **individually curl-verifying every
returned URL as a live `application/pdf` response before trusting it** — one such batch (31
URLs from one agent) came back 0% real (fabricated paths and sequential-hex `.ashx` GUIDs
that aren't real GUIDs), while a second batch from a different agent came back 100% real.
The lesson generalizes beyond this project: an LLM asked to "find URLs" will confidently
emit plausible-looking ones from training-data patterns unless it actually browses and
checks each one, and downstream verification has to assume that by default, not as a
courtesy.

### 2.2 Metadata parsing — the part that actually matters

**The core design constraint: never trust the filename or URL.** One PDF in the corpus
sits at a URL path containing `/uploads/012023/` (implying January 2023), but the document
itself is **Issue 4, effective 26 November 2025**. If metadata were derived from the URL,
every downstream fact — version, effective date, in-force status — would be wrong for that
document, silently.

Instead, `backend/ingestion/ingest.py` extracts full text per page (`pdfplumber`) and regex-parses the
document's own printed metadata, which DHA renders as a consistent footer line on content
pages (not the cover page):

```
Code: DHA/HRS/HLD/MA-2  Issue Nu: version 1.3  Issue Date: 18/07/2025
Effective Date: 18/07/2025  Revision Date: 18/07/2030  Page 2 of 54
```

Four independent regexes pull `doc_code`, `version`, `effective_date`, and the title (the
line immediately preceding the `Code:` line). They're independent rather than one combined
pattern because the separator punctuation is inconsistent across documents (some use plain
spaces, some use ` - ` between fields, one has a stray double colon `Revision Date::`) —
matching each field on its own keyword is more robust than trying to anchor a single regex
to the whole line's exact shape.

**Documents that don't match this footer format are not guessed at.** DHA's footer
convention doesn't cover every source in the corpus, and there turned out to be a second,
genuinely distinct template worth automating rather than hand-fixing one-off: DoH's newer
(2025/2026) "Standard"/"Policy" documents carry a structured key-value block on **page 2**
instead of a per-page footer:

```
Document Ref. Number: DoH/SD/ED-ECC-SPHC/V2/2025  Version: V2
New / Revised: Revised from June 2021
Publication Date: October, 2025      Effective Date: December, 2025
```

`backend/ingestion/ingest.py` tries the DHA-style parser first; if any required field is still missing, it
falls back to a second parser (`parse_metadata_doh`) built specifically for this template,
with its own date parser tolerant of the three date-text variants actually observed
("April, 2026", "September2025" — the space dropped by PDF text extraction — and "June 11,
2025"). This auto-parsed roughly half of a 45-document bulk-discovery batch that would
otherwise have needed hand-typing. **Whatever neither parser can handle is still not
guessed at** — it goes to `needs_manual.json` with whatever partial metadata was found, for
a human to finish by hand (`backend/ingestion/apply_manual_fixes.py`) or leave out. At the current scale
(65 candidate URLs, 34 successfully auto-parsed as `official`), roughly 30 documents remain
unfixed in `needs_manual.json` — a known, deliberate gap, not an oversight: hand-fixing
every remaining document wasn't worth the time against a Friday deadline once the corpus
had already grown past what the original 17-document demo needed.

Two more parsing safeguards, both added once the corpus started coming from multiple
external sources rather than one hand-picked list:
- **Duplicate detection by content, not just by file.** `sha256` of the raw PDF bytes
  catches a literal re-download of the same file, but not the same document re-served
  under a different filename/URL (new PDF metadata or timestamp embedded, identical text).
  A second hash — of the normalized, whitespace-collapsed *extracted text* — catches that
  case; a match is logged and the duplicate is skipped rather than double-counted as two
  documents.
- **A real near-duplicate ended up in the corpus regardless**: two different DoH GUID URLs
  parsed to the exact same `doc_code`, `version`, and `effective_date` ("Quality and
  Patient Safety Policy," `DoH/STD/HQS/QPS/V2/2026`, both dated 2026-04-01) but with
  slightly different extracted text (13 pages vs. 12), so the text-hash check didn't catch
  it. Supersession's `ORDER BY effective_date DESC` with no tiebreaker gave one of them an
  arbitrary "in force" status over the other — see §8's tie-handling question, since this
  moved from hypothetical to actually observed.

### 2.3 Supersession resolution

Grouping is pure and stateless: group parsed documents by `doc_code`, sort each group by
`effective_date` descending, mark everything except index `0` as `superseded = true`. A
document with no siblings (only one version exists) is never superseded.

**Verification, not assumption:** `backend/ingestion/ingest.py` prints an explicit milestone check after
resolution — the three `DHA/HRS/HLD/MA-2` documents must resolve to v1.3 (2025-07-18) in
force, v1.2 and v1.0 superseded — and exits loudly if that's wrong. This exists because
supersession is the entire value proposition; silently getting it wrong would be worse than
crashing.

### 2.4 Insertion (`backend/ingestion/load_db.py`) and chunking (`backend/ingestion/rechunk.py`)

`backend/ingestion/load_db.py` inserts `documents` rows only, matched by `sha256` — idempotent, safe to
re-run after fixing a parsing bug without duplicating data. It no longer chunks; that
moved to a separate script once chunking stopped being page-level.

**Chunking was originally per-page** (one chunk = one PDF page's raw text, via
`page.extract_text()`) — simple, and page-level chunks let a citation say "page 12"
meaningfully. But it was structure-blind: no awareness of headers, sections, or bullets,
and it produced the wrong retrieval/highlighting unit — a citation's "page 12" might mix
the tail of one clause with the start of an unrelated one, and PDF highlighting had to
fall back to a fuzzy, fixed-size heuristic to guess which lines actually mattered (see
§6.4). `backend/ingestion/rechunk.py` replaced this with **Docling**-based structural chunking:

1. Each PDF is converted via Docling's `DocumentConverter` (`do_ocr=False` — these PDFs
   already have real text layers, same assumption the old pdfplumber pipeline made —
   plus MPS/CUDA acceleration; OCR-on made this ~15x slower for no benefit here).
2. Chunked via `docling_core`'s `HybridChunker`, token-budgeted and structure-respecting
   — chunks follow real section/heading boundaries and **can span page breaks** when a
   section does.
3. A custom **merge pass** joins whole *runs* of 2+ consecutive tiny (<100-word) chunks
   into one. This was empirically justified, not assumed: testing on 5 real corpus
   documents (including all 3 `DHA/HRS/HLD/MA-2` versions) found the same
   `Executive Summary → Abbreviations → Background → Scope → Purpose → Applicability`
   cluster of tiny front-matter chunks recurring in every one — a real structural
   template pattern, not a one-off. Chunks already a reasonable size are left exactly as
   Docling produced them; this is not a blanket word-count floor.
4. A **semantic re-split pass** handles the other end: chunks still over ~700 words
   after merging are split at Docling doc-item boundaries only (never mid-paragraph),
   preferring the boundary with the lowest embedding similarity between neighbors — a
   genuine topic shift, not an arbitrary cut. Splitting only at doc-item boundaries is
   what keeps bounding-box provenance (next point) exact for every resulting piece.
5. Each final chunk's doc_items carry real PDF provenance (page number **and bounding
   box**) from Docling — resolved via `RefItem(cref=...).resolve(doc)`, since
   `chunk.meta.doc_items` entries are unresolved reference stubs, not text objects
   directly. Boxes are normalized to top-left origin, 0-1 fractions of page size (via
   `bbox.to_top_left_origin().normalized()`) so rendering is scale-independent — the
   frontend just multiplies by whatever size it renders the page at (§6.4).

Each chunk still gets an embedding via `text-embedding-3-small` (1536-dim — justified
empirically in §4.3, not just for cost) and a `tsvector` built from **both** an English
and an Arabic `to_tsvector` call on the same text, weighted equally and concatenated —
the corpus is bilingual (DHA publishes some content in Arabic) and a single-language
tsvector would silently fail lexical matches on Arabic passages.

`backend/ingestion/rechunk.py` requires `documents` rows to already exist (matched by `sha256`) — it only
replaces a document's `chunks`. It is **not** idempotent the same way as the rest of the
pipeline: re-running it deletes and recomputes a document's chunks from scratch every
time, since a one-time migration script doesn't need incremental-resume logic.

---

## 3. Storage schema

```sql
documents(
  id, title, doc_code, version, effective_date, authority,
  source_url, sha256, ingested_at, superseded boolean, tier text
)
chunks(
  id, document_id, page, page_start, page_end, heading_path text[], bboxes jsonb,
  text, embedding vector(1536), tsv tsvector
)
```

`page` is kept as a compatibility shim equal to `page_start`, so single-page call sites
didn't all need simultaneous updates when chunking moved to Docling (§2.4) — `page_end`
only differs from `page_start` for a chunk that genuinely spans a page break.
`heading_path` is the section heading(s) a chunk falls under (an array, since a merged
tiny-section run can span several distinct headings). `bboxes` is a JSON array of
`{page_no, l, t, r, b}` entries, one per constituent PDF element, normalized 0-1 top-left
origin — the exact provenance `PdfOverlay.tsx` renders highlights from (§6.4).

`superseded` is a plain boolean, not a computed view or a separate "current version" table.
This is deliberate: the property that has to be trustworthy under scrutiny should be the
simplest possible representation. A GIN index backs `tsv`; `documents.doc_code` is indexed
for the supersession-sibling lookup. **No ANN index (ivfflat/HNSW) is built on the
embedding column** — at ~860 rows, a sequential scan with exact cosine distance is faster
than tuning an approximate index would be worth, and exact recall matters more than query
latency at this scale. This is a scale-appropriate choice, explicitly not "the tool wasn't
available" — the schema comment says so.

`tier` (`'official'` / `'research'` / `'commentary'`, default `'official'`) was added via
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` after the corpus grew to include non-regulation
sources — every pre-existing row defaulted to `'official'` with no backfill needed. It's a
free-text field rather than a Postgres enum deliberately: adding a value later (say,
`'guideline'`) is a data change, not a migration. The retrieval pipeline (§4) treats every
tier identically — the same hybrid search, fusion, and abstention logic applies regardless
of tier, since a research paper genuinely can be the best-matching source for a telehealth
question. Only the frontend's `AuthorityBadge` (§6.3) branches on it, so a non-`official`
citation never gets dressed up as if it carried a regulator's authority.

---

## 4. Retrieval — the core algorithm

Everything downstream (CLI, API, frontend) calls one function:
`retrieval.answer_question(conn, question, superseded_filter: bool, provider: str = "openai", model: str | None = None, authority_filter: str | None = None) -> dict`
(`answer_question_stream()` mirrors this signature, yielding progress events for the
frontend's live reasoning trace instead of returning once at the end). `provider`/`model`
only affect §4.5 (answer generation) — retrieval, fusion, filtering, and abstention below
are identical regardless of which model eventually generates the prose.

### 4.1 Hybrid search

Two independent ranked lists are pulled per question, each capped at 15 candidates:

- **Semantic**: `1 - (embedding <=> query_vector)` (pgvector cosine distance → similarity),
  filtered to `superseded = false` when `superseded_filter` is true, and to
  `authority = authority_filter` when a specific jurisdiction is selected (both filters are
  independent boolean/optional clauses, composable, applied identically to both lists
  before fusion).
- **Lexical**: `ts_rank(tsv, websearch_to_tsquery('english', question))`, same filters.

### 4.2 Reciprocal Rank Fusion (RRF)

The two lists are merged by **rank**, not raw score:

```
rrf_score(chunk) = Σ  1 / (60 + rank_in_list)   for each list the chunk appears in
```

**Why RRF instead of a weighted blend of the two raw scores:** cosine similarity and
`ts_rank` live on incompatible scales (cosine is bounded [0,1] and clusters tightly around
0.05–0.75 in practice; `ts_rank` is an unbounded, corpus-frequency-dependent float that can
range from near-zero to several). Any fixed linear weighting (`0.6 × cosine + 0.4 × rank`)
requires either normalizing both to a common scale per-query — extra complexity — or
accepting that one signal silently dominates depending on absolute magnitudes. RRF sidesteps
this entirely by only caring about *ordinal position* in each list, which is scale-free by
construction. `k = 60` is the standard constant from the original RRF paper — it's a mild
damping factor that keeps rank-1 vs rank-2 from swinging the fused score too sharply.

### 4.3 Confidence tiering (formerly a single abstention threshold)

The gate uses the **semantic score of the single top-fused chunk** (`fused[0]`), bucketed
into three tiers instead of a single abstain/answer binary:

```python
CONFIDENCE_HIGH = 0.55    # comfortably inside the observed on-topic range
CONFIDENCE_MEDIUM = 0.35  # captures weaker-but-real matches down to the observed floor
CONFIDENCE_LOW = 0.15     # abstain below this
```

This supersedes the original single `ABSTAIN_THRESHOLD = 0.35`, whose documented
calibration ("relevant ~0.6–0.75, off-topic ~0.07–0.10") turned out to be optimistic —
re-measured against 10 real queries run directly through `embed()`/`semantic_search()`
(no LLM call needed), genuinely on-topic questions actually ranged **0.40–0.83**
(the weakest real match, "Can a nurse practice independently without physician
supervision?", scored 0.402), while off-topic controls ("What is the capital of France?",
"Write me a poem about the ocean.") clustered tightly at **0.09–0.123**. The gap between
those two clusters (0.123 → 0.402) is real and wide, which is where `CONFIDENCE_LOW`
sits, with margin. The medium/high split inside the on-topic range (0.35–0.55–up) is a
reasoned first cut of that 10-question sample, **not** a validated operating curve — same
caveat as before, just now grounded in real numbers instead of a two-point estimate (see
§8's "abstention false-negative/false-positive rate" answer).

Below `CONFIDENCE_LOW`: abstain, exactly as before. At "high": answered normally, no
change from prior behavior. At "medium" or "low": still answered, but the prompt is told
which tier applies and is required to end the answer with a `Certainty:` line stating the
confidence level honestly — "low" explicitly says the findings are inconclusive. This
replaces a silent binary (confident answer vs. flat "I don't know") with a graded signal:
a weak-but-real match no longer either gets treated identically to a strong one, or
refused outright.

**Per-chunk filtering.** Separately from the query-level tier, each of the (up to 7) fused
chunks is checked individually before being sent to the LLM: any chunk scoring below
`CONFIDENCE_LOW` is dropped from the context entirely (`filter_weak_chunks()`), rather
than force-fed alongside genuinely relevant ones. One exception: a chunk that only matched
via lexical search (never appeared in the semantic top-k) carries a hardcoded
`semantic_score = 0.0` sentinel (§4.2's fusion code) — that's "not scored by this method,"
not a real low-relevance judgment, so it's exempted from the drop. This is also most of
what fixes a formatting problem: the prompt used to implicitly pressure the model into
writing one bullet per provided excerpt regardless of whether all 7 were actually good
matches, producing a near-constant ~6–7 bullets. With weak chunks excluded before they
ever reach the prompt, bullet count now more often reflects genuine relevance instead of
a fixed quota (see §4.5).

**Operator visibility.** A "low" confidence tier also tags the current LangSmith trace
(`run.add_tags(["low-confidence"])` + `top_score` metadata) so these queries are
filterable for review in the LangSmith dashboard — reusing the existing tracing
infrastructure rather than adding new database persistence (§7 explicitly scopes out
question history/persistence).

**Why semantic score specifically, and not the fused RRF score:** RRF scores are
rank-based and have no absolute meaning outside a single query's own two lists — a
completely irrelevant question can still produce a "top-ranked" RRF result, because RRF
always has *a* rank 1. Only the underlying cosine similarity carries information about
whether that top result is actually related to the question at all.

### 4.4 Supersession and authority filtering

Both the semantic and lexical queries carry `WHERE d.superseded = false` (added
conditionally based on the `superseded_filter` argument) — filtering happens **before**
fusion and before the abstention check, not as a post-hoc step. This matters: if filtering
happened after ranking, a heavily superseded-dominated top-K could produce false
abstentions even when a valid in-force answer exists further down the list.

`authority_filter` is a sibling mechanism, same shape: an optional `AND d.authority = %s`
clause, applied at the same point, composable with the supersession filter (both can be
active at once — e.g. "only in-force DHA documents"). Unlike supersession, there's no
default exclusion here — `authority_filter` is `None` unless the caller explicitly
narrows to one jurisdiction, since most questions don't name an emirate and retrieval
across the whole corpus is the sane default.

### 4.5 Answer generation

Up to **7** fused chunks (`TOP_N_FOR_ANSWER`) survive to `rrf_fuse()`, but only the ones
that clear `CONFIDENCE_LOW` (§4.3's `filter_weak_chunks()`) are actually passed to the
chat model — often fewer than 7 for a query with a sharp relevance drop-off partway down
the list. The model runs at `temperature=0` with a system prompt that restricts it to
only the provided excerpts, explicitly forbidding invented facts. The output format is
deliberately structured, not a short paragraph: one bold direct-answer sentence, a
`Findings:` section with as many bullets as the excerpts genuinely support (the prompt
explicitly bans treating any specific count as a target *or ceiling*, after early testing
showed the model anchoring on "at least 4" as if it were both a floor and a ceiling — and
after per-chunk filtering removed the practical need to force one bullet per excerpt
regardless of relevance), a `Summary:` sentence that adds a synthesizing "so what" rather
than repeating the opening line, an optional `Not covered:` line when the excerpts only
partially answer the question, and — new — a `Certainty:` line, present only at
medium/low confidence tier (§4.3), stating the confidence level plainly. When the model
directly quotes an excerpt verbatim rather than paraphrasing (exact wording matters — a
defined term, a specific obligation), it's instructed to wrap the quotation in a Markdown
blockquote (`> "..."`) using plain straight quotes; the frontend renders that with a left
border and CSS-generated curly quote marks (`AnswerCard.tsx`, `tokens.css`) — purely
presentational, the backend's ASCII-only output contract (below) is untouched. Citation
references in the prose itself (`[Source 1]`, `(Source 2)`, "according to the second
excerpt," etc.) are explicitly and exhaustively banned — citation is rendered separately
by the interface (§5) from data the model never sees, and early testing caught the model
leaking bracket-style references anyway despite being told not to, which is why the ban
enumerates every literal form rather than one example.

**Generation-only local model support.** `provider` (`"openai"` | `"local"`) and an
optional `model` override route the same call to either OpenAI (`gpt-4o-mini` by default)
or a locally-running LM Studio server via its OpenAI-compatible endpoint — retrieval and
embeddings always stay on OpenAI regardless (the corpus is embedded at 1536-dim; a local
embedding model would need a different dimension and a full re-embed, a separate task).
The privacy tradeoff is stated plainly, not implied: the question text and retrieved
excerpts already went to OpenAI once, to compute the query embedding and find the
excerpts — switching generation to local keeps the final answer synthesis on-machine, it
does not make retrieval itself local.

One real failure mode surfaced during testing, worth knowing: a small local model
(Gemma) with LM Studio's "thinking" forced on either burned its entire response budget on
unparsed reasoning (empty `content`) or leaked that reasoning straight into the visible
answer, because it doesn't share Qwen3's trained `<think>...</think>` delimiter
convention — the only format LM Studio's server actually knows how to split into a
separate `reasoning_content` field. `generate_answer()` strips any leaked `<think>` block
on the local path and raises a clear error instead of returning blank or garbled text; the
practical fix was model choice (default to a Qwen model), not a prompt tweak.

### 4.6 Supersession bookkeeping

Once a document is chosen as the answer's source, a second query counts how many *other*
documents share its `doc_code` and are `superseded = true` — this becomes
`superseded_excluded` in the response, e.g. "2 superseded versions of this document were
excluded from retrieval." This count is computed unconditionally (regardless of whether
filtering was actually on) — it's the frontend/CLI's job to only *display* it when
filtering was actually active, since showing "excluded" language on an unfiltered (naive)
result would be a factually false claim (see §6.2).

---

## 5. API layer (`backend/app/`)

`POST /ask` and `POST /ask-stream` are the two routes that matter; their job is bridging
the browser and `backend/app/core/retrieval.py`, adding as little logic of their own as possible:

1. **Necessary security boundary.** A browser cannot hold `OPENAI_API_KEY` or reach
   Postgres directly; some server process has to sit in between regardless of how thin.
2. **Two additive extras**, each wrapped in its own bare `try/except: pass` so a failure
   in either never breaks the core answer: `sibling_versions` (every version of the cited
   `doc_code`, for the frontend's version-ledger display) and per-chunk `document`
   enrichment on `retrieved_chunks` (so the source panel can show title/authority/link
   without a second round trip).
3. `/ask-stream` reuses the exact same pipeline via `answer_question_stream()`, differing
   only in yielding a Server-Sent Event between each pipeline stage
   (`embedding_query` → `searching_sources` → `aggregating_results` →
   `checking_supersession` → `citing_source` → `generating_answer` → `done`) instead of
   returning once at the end — this is what backs the frontend's live reasoning trace, not
   a simulated/timed spinner. It is not a second implementation of the pipeline; both
   routes share every retrieval/generation function unchanged.

Two smaller supporting routes: `GET /local-models` queries LM Studio for its currently
loaded chat models (filtering out embedding models), returning an empty list rather than
an error if LM Studio isn't running, so the frontend's provider switcher can show "no local
models available" instead of a broken request. `GET /corpus-stats` computes the live
document/chunk counts backing the frontend's footer — deliberately computed per-request
rather than hardcoded, after the original hardcoded string ("16 documents, 726 chunks")
had already gone stale once the corpus grew past its initial size.

CORS is restricted to `http://localhost:5173` (the dev frontend's origin) rather than left
open — there's no reason a locally-run compliance tool should accept cross-origin requests
from arbitrary pages.

The response is the `answer_question()` dict passed straight through, letting FastAPI's
default JSON encoder handle `datetime.date → ISO string` conversion automatically. No
Pydantic response model was written deliberately — a response model would be a second copy
of the contract that could drift from `backend/app/core/retrieval.py`'s actual return shape over time.

### Observability (LangSmith)

No LangChain or LangGraph anywhere in this project — `backend/app/core/retrieval.py` calls the OpenAI SDK
directly. Tracing is added on top of that, not instead of it, via two separate mechanisms
in `backend/app/core/retrieval.py`:

1. `client = wrap_openai(OpenAI(...))` — LangSmith's wrapper around the raw client. **This
   only patches `chat.completions.create` and friends** (verified against the installed
   package's own source, `langsmith/wrappers/_openai.py`) — it does **not** cover
   `embeddings.create`. That's a real, confirmed gap in that specific helper, not a
   guess.
2. `@traceable(...)` on `embed()` (to close that gap) and on `answer_question()` /
   `answer_question_stream()` (so the embedding call and the generation call nest under
   one parent run per question, instead of showing up as two disconnected leaf traces).
   `process_inputs=_without_conn` strips the raw psycopg2 connection object out of what
   gets logged — it isn't meaningfully serializable and is pure noise in the trace.

Verified by querying LangSmith's own API after a live request (`client.list_runs(...)`),
not just by trusting the dashboard: a chain run with two children, `embed_query` (tool) and
`ChatOpenAI` (llm). If `LANGSMITH_TRACING` isn't set, both mechanisms are harmless no-ops —
calls behave identically, nothing is sent anywhere, so this never becomes a hard dependency
for the CLI or the demo.

**The root chain run's Name is set dynamically to the model that answered** (`gpt-4o-mini`,
`qwen/qwen3-4b-2507`, etc.) via `get_current_run_tree().name = ...` at the top of
`answer_question()`/`answer_question_stream()`, rather than staying the static
`"answer_question"` string. This looks similar to an earlier attempt (auto-generating a
per-query semantic title from the question) that was deliberately reverted — that one
duplicated Input by restating the question in different words. This one doesn't: model
identity is a genuinely different, low-cardinality axis (a handful of possible values) that
Input's full field dump doesn't summarize at a glance, which is precisely the industry-
standard use for a trace Name field (compare: an HTTP trace named by endpoint, not by full
request body).

---

## 6. Frontend

### 6.1 State machine

One mutation, consuming `/ask-stream`'s Server-Sent Events (`streamAsk()` in `client.ts`)
rather than a single blocking POST: idle → pending (with live `ThinkingTrace` steps
appended as they arrive) → (error | abstained | answered). No client-side history, no chat
thread — every question is a fresh, isolated request, matching the CLI's own
statelessness. `ThinkingTrace` is real instrumentation, not a simulated/timed spinner — it
renders exactly the SSE events `/ask-stream` yields (§5), so a step only appears once that
pipeline stage has actually run.

### 6.2 The naive/ReguLense toggle — and the bug it exposed

The "Exclude outdated regulations" checkbox literally is the `superseded_filter` argument,
wired straight through. Flipping it off reproduces `backend/cli/demo.py`'s naive-RAG failure mode live,
in the browser, for free — no separate "naive mode" had to be built.

**Two real bugs were caught and fixed during build**, worth knowing in case they come up.

First: the citation UI initially showed the "N superseded versions excluded" line **even
with the filter off** — technically true in a vacuous sense (other superseded siblings
exist) but misleading, since nothing was actually excluded when the filter is off, and it
partially undercut the demo's own point (a naive citation should look confidently
unflagged, exactly because naive RAG doesn't know it's wrong). Fixed by passing which mode
was actually used for the last request down to the citation component, and only rendering
the "excluded" line when the filter was genuinely on.

Second: the version ledger's brass/rust styling was originally a static per-document
property — whichever sibling had `superseded = false` always got the brass/checkmark
treatment, and every other sibling always rendered rust/struck-through, regardless of the
toggle or of which version the answer actually cited. With the filter off, that meant a
genuinely-cited older version (e.g. v1.2, naive mode picking a superseded regulation) was
shown struck-through as if excluded — while the merely-newest, uncited version (v1.3) kept
the checkmark. That's backwards: nothing was excluded that query, so nothing should read
as excluded, and the version actually grounding the answer is the one that should stand
out. The ledger now derives its styling live, per query: a sibling renders rust/struck-
through only when the filter was genuinely on AND it's superseded (i.e. really excluded
this time); the checkmark and border go on whichever sibling `is_current` (the version the
answer was actually generated from), and that chip is sorted first. In filter-on mode this
is a no-op (the cited version is always the newest one, since only non-superseded docs are
retrievable) — the fix only changes behavior in filter-off/naive mode, where it now
correctly highlights the outdated version that got cited instead of the uninvolved latest
one.

### 6.3 Design system

Token-based (`tokens.css`), light, cool-neutral ("paper," not cream, not stark white),
Public Sans for UI chrome (the USWDS government-design-system typeface — a deliberate
choice for a government-regulation product) and Source Serif 4 for the generated answer
text only (mirroring how legal/regulatory publishing typesets long-form reading text).
Accent colors are used **semantically, never decoratively**: brass means "in force" (a
document's own legal status) and — since confidence tiering (§4.3) — also marks the
`Certainty:` line at "medium" confidence, since "grounded in fewer/weaker sources than
usual" is the same underlying "don't fully trust this at face value" signal a rust chip
already carries; rust itself means "superseded" and also marks `Certainty:` at "low"
confidence, the stronger version of that same signal. A third, separate `--caution-amber`
token (visually distinct from both) exists specifically so medium and low confidence don't
collapse into the same color and lose the distinction. Authority provenance (DHA / DoH /
MOHAP) is shown as a typographic chip with a double-rule border — deliberately not a
scraped government seal image, both to avoid any trademark-usage ambiguity and because a
hand-built element is more honest than an image that could be mistaken for an official
mark; each authority gets its own institutional color (`--dha-steel`, `--doh-teal`,
`--mohap-plum`) so a citation's jurisdiction is legible at a glance, separately from the
brass/rust legal-status and confidence signals. That seal-style chip is reserved for
`tier === "official"` specifically: a research paper or (if ever ingested) a commentary
source gets a plain dashed-border chip labeled "RESEARCH" or "COMMENTARY" in a muted
neutral tone instead — visually unmistakable from an official regulator badge at a
glance, so a citation's legal weight is never ambiguous. The provider
switcher (ChatGPT/Local segmented control, §4.5) follows the same restraint: a plain
two-state control next to the query bar, no settings panel, with a live-populated model
dropdown only appearing once "Local" is selected.

### 6.4 PDF citation highlighting

`PdfOverlay.tsx` renders highlight rectangles directly from `chunks.bboxes` (§3, §2.4) —
normalized coordinates scaled by whatever size PDF.js renders the current page at, no
text matching involved. This replaced an earlier client-side heuristic that searched the
rendered PDF's text layer for the best-scoring fixed 3-line window, using word overlap
against the **whole generated answer** (not the cited chunk's own text) as the matching
signal. That approach was structurally wrong, not just under-tuned: with no idea where
the actually-cited passage began or ended, it routinely produced a highlight that cut off
mid-sentence at both ends. Storing exact provenance at ingest time (once, cheaply) instead
of re-deriving an approximate location client-side (every time, expensively and
unreliably) is the same shape of fix as supersession itself (§1) — trustworthy behavior
comes from storing the true signal, not guessing it downstream.

### 6.5 Source count and the confidence filter

`SourcePanel.tsx`'s "View N sources" reflects only chunks that actually cleared the
confidence floor and were sent to the model (`used_for_answer`, set in
`answer_question()`/`answer_question_stream()` from `filter_weak_chunks()`'s output) —
not the full raw candidate list. The panel still shows every retrieved chunk for
transparency (same philosophy as the version ledger, §6.2: show what's real, mark what's
excluded, don't hide information), with chunks that didn't clear the floor visually
muted and labeled "Not used" rather than removed from the list.

---

## 7. Explicitly out of scope

No auth — public access, rate-limited per IP (10/min, 30/hour on the question
endpoints) plus a global daily API-call cap, no question history/persistence beyond
the current browser session, and no multi-tenant data isolation of any kind — the
regulation corpus is shared across every visitor equally (auth was implemented once,
via Google Sign-In + Supabase, then deliberately removed for this deployment).
Token-by-token streaming of the answer text *does* exist for the OpenAI path (`/ask-
stream`'s SSE `answer_delta` events render live as they arrive) — but only for OpenAI:
the NaraRouter fallback (§ CLAUDE.md's "Retrieval and answering") is deliberately
called non-streaming and delivered as one instant chunk, since its own streaming API
was confirmed unreliable in testing (a trailing empty-`choices` chunk, occasional
mid-stream errors). No cross-encoder reranking stage (considered — see §8 — but LM
Studio can't actually serve the one local reranker model tested as a real reranker,
and RRF fusion already covers the accuracy this demo needs). No full Arabic UI
localization (only font-stack fallback coverage so a stray Arabic excerpt doesn't
render as tofu boxes). A backend pytest suite and a GitHub Actions CI/CD test gate
exist (pytest, frontend typecheck/lint/test) — deploy is Render's own
auto-deploy-on-push (see CLAUDE.md's "Containers & deploy"), not this workflow; the
Cloud Run and Vercel deploy jobs that used to sit alongside the test gate targeted
infrastructure this project never actually ran on and have been removed entirely.
No eval harness yet (gold-question scorecard — citation
accuracy, stale-citation rate, abstention precision/recall — is a deliberately
separate, later task).

---

## 8. Anticipated questions

**"How do you know retrieval isn't just getting lucky on your one demo question?"**
The demo question was deliberately *chosen* by testing several candidate questions against
the corpus and picking one where naive and filtered retrieval diverge — most licensing
questions actually converge on the same (current) version by coincidence, since versions
are highly similar text. The underlying mechanism (filter before fusion) is
question-independent; the choice of demo question is about making a real, low-frequency
divergence visible, not manufacturing one. A proper gold-question scorecard (not yet built)
would measure this at scale rather than via one cherry-picked example.

**"What if two versions of a document have the same effective date?"**
Not currently handled, and this moved from hypothetical to actually observed once the
corpus grew: two different DoH GUID URLs turned out to be near-duplicate documents that
parsed to the identical `doc_code`, `version`, and `effective_date` ("Quality and Patient
Safety Policy," both dated 2026-04-01, with slightly different extracted text so the
text-hash duplicate check didn't catch them as the same file). `ORDER BY effective_date
DESC` with no tiebreaker gave one of them an arbitrary "in force" status via Python's
stable sort. A real implementation would need a documented tiebreak rule (e.g. flag exact
ties for manual review rather than silently picking one) rather than the current behavior.

**"What's your abstention false-negative/false-positive rate?"**
Not measured yet — that's precisely what the (not-yet-built) eval harness is for. The
three confidence tiers (§4.3) are now calibrated on a 10-question real sample (on-topic
0.40–0.83, off-topic 0.09–0.123) rather than the original two-point estimate, which is
better grounded but still not a validated operating curve — 10 questions isn't a
distribution, just enough to catch that the original documented calibration was
optimistic. A real eval harness would need many more labeled queries, ideally spanning
the medium/low tier boundary specifically, to know if 0.35/0.55 are actually the right
split points or just a reasonable-looking one.

**"How would this scale to thousands of documents?"**
The sequential-scan-on-embeddings choice (§3) stops being appropriate; an HNSW index would
be the natural next step. Ingestion would also need incremental/streaming support rather
than the current batch script, and the metadata-parsing regexes would need to handle more
template variance than three authorities' worth (a fourth, Sharjah Health Authority, is a
confirmed real regulator whose documents are Arabic-only — deferred, since the pipeline
has no RTL/multilingual support today).

**"What happens if the OpenAI API is unavailable?"**
The API surfaces a `500` with the raw exception message; the frontend shows it verbatim in
an error state. There's no fallback model or retry/backoff currently implemented.

**"Is this compliant with UAE data protection law (PDPL) or healthcare data rules?"**
Not addressed — the corpus is entirely public regulatory documents (no patient data), so
PDPL's sensitive-data provisions aren't triggered by this demo's content, but a production
version handling any patient-linked query context would need data-residency and audit-log
work this project doesn't attempt.

**"Why Postgres+pgvector instead of a dedicated vector database?"**
At ~1,050 chunks, a dedicated vector store (Pinecone, Weaviate, etc.) would add infrastructure
without a corresponding benefit — Postgres already holds the relational metadata
(supersession, authority, dates), and pgvector lets a single transactional query join
vector similarity with that metadata directly, rather than round-tripping between two
systems and reconciling consistency between them.

**"Why hybrid retrieval instead of pure semantic search?"**
Compliance questions often reference exact codes or specific terms (`DHA/HRS/HLD/MA-2`,
specific drug/procedure names) where lexical matching is more precise than semantic
similarity — an embedding model can miss an exact-code lookup that full-text search catches
trivially, and vice versa for conceptual/paraphrased questions. Fusing both covers more of
the real question distribution than either alone.

**"You built a custom reasoning-trace UI and also added LangSmith — why both?"**
Different audiences. The in-app `ThinkingTrace` is for the person using ReguLense right now —
it answers "what did this specific answer just do" inline, no login, no separate tool.
LangSmith is for the person operating ReguLense over time — historical runs, latency and
token-cost trends across many questions, and (if a gold-question dataset were uploaded)
automated eval scoring. One is a UI feature; the other is ops tooling. Neither replaces
the other, and neither is a hard dependency of the other — the trace UI works with
`LANGSMITH_TRACING` unset.

**"Would you add a reranking stage?"**
Considered and deliberately not built yet — RRF fusion of semantic + lexical is already a
legitimate, defensible retrieval design, and a cross-encoder rerank stage is a natural "v2"
improvement, not something this demo's accuracy needs today. It was investigated concretely
rather than left as a vague maybe: LM Studio was tested directly and confirmed to have no
`/v1/rerank` endpoint at all, and it classifies a downloaded `jina-reranker-v3.5-GGUF`
checkpoint as a generic chat model (`type: "llm", arch: "qwen3"`) rather than a real
cross-encoder — querying it through LM Studio's chat endpoint with an unrelated prompt
produced incoherent output, confirming the GGUF conversion doesn't carry a usable scoring
head. A real implementation would either prompt a chat model to score candidates directly
("LLM-as-judge," zero new dependencies but an extra round-trip) or run a dedicated
cross-encoder locally via `sentence-transformers`, bypassing LM Studio entirely.

**"Why would a UAE health-regulation RAG cite an academic paper?"**
Three of the interviewing firm's co-founder's own papers (telepsychiatry/telehealth policy
during COVID, on-topic for a corpus that already includes DHA's Telehealth Standards) are
ingested at `tier = "research"` rather than `"official"` — same retrieval pipeline, same
hybrid search and fusion, but the frontend never dresses a research citation up with
regulation styling (§6.3). The bar for inclusion was deliberately narrow: the co-founder
has 60+ published papers, and only these 3 are actually about health regulation/policy
rather than general clinical psychiatry — padding the corpus with clinically-relevant-but-
off-topic papers would have violated the same "strictly relevant" principle the regulation
corpus itself is held to, and diluted retrieval quality on real regulation questions.
