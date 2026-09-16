# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ReguLense: hybrid-retrieval Q&A over UAE health regulation (DHA/DoH/MOHAP) with version
awareness (tracks which regulation version is in force vs. superseded), jurisdiction
awareness (authority filter), tiered retrieval confidence, and mandatory
citation-or-abstain behavior — it never generates a fluent but ungrounded answer. See
`README.md` for the product pitch and `ARCHITECTURE.md` for the full technical
walkthrough with the reasoning behind nearly every non-obvious design decision — read it
before making non-trivial changes to `backend/app/core/retrieval.py` or the ingestion
pipeline.

## Commands

Backend (Python), from `backend/` with the venv active (the venv directory is
`venv/` at the repo root — not `.venv`):
```
..\venv\Scripts\Activate.ps1
python -m app.main                # serves API on :8000 (no --reload: see app/main.py's comment on why)
python -m cli.demo                # CLI naive-vs-ReguLense side-by-side comparison
python -m cli.ask "question"      # CLI single-question query
..\venv\Scripts\python.exe -m pytest   # backend test suite (Postgres+pgvector required)
```

**Windows pytest gotcha:** bare `pytest` resolves the system Anaconda Python, which
dies with `ssl.SSLError: [ASN1: NOT_ENOUGH_DATA]` importing openai's vendored
`aiohttp` (a Windows cert-store bug, unrelated to this codebase). The project venv
(openai 3.3.1, no aiohttp) imports cleanly — always invoke pytest via
`..\venv\Scripts\python.exe -m pytest` from `backend/`.

Frontend, from `frontend/`:
```
npm run dev        # Vite dev server on :5173
npm run build       # tsc -b && vite build
npm run lint         # oxlint
npx tsc --noEmit    # type-check only, no test suite configured
```

Full local run: `docker compose up` (Postgres+pgvector, backend, frontend, one command) —
see `RUN.md` for env vars and the quick-start. Manually: start Postgres
(`docker compose up -d db` from the repo root, then create the test database once
with `docker compose exec db createdb -U regulense test_regulense`), then
`python -m app.main` from `backend/` (terminal 1) and `npm run dev` in `frontend/`
(terminal 2). See `RUN.md` for first-time setup. Do NOT use
`docker start temporal_note-db` — that container belongs to a different project, uses
different credentials, and squats on port 5433, so the test suite fails with
`password authentication failed for user "regulense"` while it is running.

## Architecture

### Ingestion pipeline

**On corpus size: quote `GET /corpus-stats`, never a local file count.** The funnel is
68 PDFs downloaded per `corpus_urls.txt` → 40 parsed automatically (37 official + 3
research) → 28 refused into `needs_manual.json` because their templates carry no
printed metadata footer for `ingest.py` to read → 24 of those 28 recovered by hand via
OCR + 3 already-parsed official documents restored (they were parsed correctly but
never loaded into production) = 64 documents in the corpus, live and in local dev. 4
of the original 28 stay out, dropped with reasons recorded in
`ingestion/ocr_extraction.json` (see `ARCHITECTURE.md` §2.2a for the full breakdown,
including the one recovered document later judged content-free after loading and left
in place since production is insert-only). Still quote the live endpoint, not this
paragraph, when stating a number — it's the authority.

Five standalone scripts (plus one shared module, `supersession.py`), run in this order
for a from-scratch corpus load, from `backend/`:

```
ingestion.download → ingestion.ingest → ingestion.apply_manual_fixes → ingestion.load_db → ingestion.rechunk
```

- `ingestion/download.py` — pulls PDFs listed in `corpus_urls.txt` into `dataset/`.
- `ingestion/ingest.py` — extracts text via pdfplumber, parses `doc_code`/`version`/
  `effective_date`/`authority` from the document's own printed metadata (never the
  filename or URL — a document's footer is the only trustworthy source), resolves
  supersession via `ingestion/supersession.py`. Writes `parsed_documents.json`
  (gitignored). Documents whose metadata can't be auto-parsed (different template)
  land in `needs_manual.json`.
- `ingestion/supersession.py` — `resolve_supersession(docs)`: newest `effective_date`
  per `doc_code` wins (version as a numeric tiebreak), everything older flagged
  `superseded=true`. **Shared, and must be re-run over the COMPLETE list every time
  `parsed_documents.json` is written** — which document is current is a property of
  the whole group. It used to live inline in `ingest.py`, so hand-added documents sat
  permanently outside resolution; since the "exclude outdated" filter only hides what
  is *flagged*, an unflagged stale document was served as current with the filter on,
  in its default state. Silent, and exactly the failure the product exists to prevent.
- `ingestion/apply_manual_fixes.py` — hand-written metadata entries for documents
  `ingest.py` can't parse (e.g. MOHAP federal docs, research papers — no
  `DHA/...`-style code). Calls `resolve_supersession()` over the merged list before
  writing, so each entry's hardcoded `superseded` is a starting point, not the last
  word.
- `ingestion/load_db.py` — inserts `documents` rows only (no chunking). Matches existing
  rows by `sha256`; idempotent.
- `ingestion/rechunk.py` — the actual chunking step. Converts each PDF via **Docling**
  (structure-aware: real headers/sections, chunks can span page boundaries) with
  `HybridChunker`, then a custom merge pass (merges whole *runs* of consecutive tiny
  chunks — confirmed empirically as a real recurring template pattern across this
  corpus, not a blanket word-count floor) and a semantic re-split pass for oversized
  chunks (splits at Docling doc-item boundaries only, preferring the point of lowest
  embedding similarity between neighbors — never mid-paragraph, so bounding-box
  provenance stays exact for every resulting piece). Embeds via OpenAI
  `text-embedding-3-small` and stores exact per-element bounding boxes
  (`chunks.bboxes`, normalized 0-1, top-left origin) used to render precise PDF
  highlights client-side — no text search involved. Requires `documents` rows to
  already exist (matches by `sha256`). Not idempotent the same way as the others:
  re-running it deletes and recomputes a document's chunks from scratch.

### Retrieval and answering

Everything downstream calls one pair of functions:
`app.core.retrieval.answer_question()` / `answer_question_stream()` (identical pipeline;
the streaming variant yields progress events for the frontend's live reasoning trace).
Both take `superseded_filter`, `authority_filter`, `provider`/`model`.

**Preamble, above everything including the cache probe** (`_screen_and_route()`):
first `app/core/safety.py`'s `screen_question()` — regex-only, no model call. Prompt
injection fails *closed* (fixed refusal, `blocked: true`, zero embeddings/LLM calls/
cache reads/cache writes); PII fails *open*, redacting emails, UAE phones and Emirates
IDs to typed tokens and continuing on the redacted text, which is what the whole
pipeline then runs on — so raw identifiers never reach the embedding call, the cache
key, the trace or persisted history. Its patterns are deliberately tight: every
override pattern needs a deictic ("your", "previous", "above") alongside the verb,
because this corpus is full of "ignore", "rules", "instructions", "system" — an
over-eager screen that eats real questions is worse than no screen. Then
`app/core/conversation.py`'s `route()`: a deterministic small-talk router matching the
*entire* normalized input against a fixed phrase table (word ceiling 8), so `hi`
answers instantly for free while `hi, what are the DHA licensing fees?` falls straight
through. Responses are fixed strings; the `model` one is composed from the real model
constants so it can't drift. Both return additive-flagged abstentions
(`smalltalk`/`blocked`) that render without `ReportAnswer`.

Pipeline: embed query → hybrid search (pgvector cosine + Postgres full-text, both
filtered by supersession/authority) → Reciprocal Rank Fusion → confidence tiering on
the top-fused chunk's semantic score (`CONFIDENCE_HIGH`/`MEDIUM`/`LOW` constants near
the top of `app/core/retrieval.py`, recalibrated against real query sampling — read the
comment above them before changing the numbers) → abstain below the floor (free, no LLM
spend); otherwise an LLM relevance guardrail (`app/core/guardrail.py` — question + top-3
chunk texts, capped 400-token verdict, ALWAYS gpt-4o-mini via the OpenAI→NaraRouter
chain, never the answer's provider: measured live, local qwen 4B misjudged specific
operational questions gpt-4o-mini accepted with identical evidence; exact mined
questions skip the judge entirely via `is_mined_question`) runs before the
expensive generation call, because embedding proximity genuinely misfires on playful
off-topic questions (measured: "What shoe size is Messi?" scores ~0.23). OFF_TOPIC
abstains with a backend-composed fixed sentence + optional clickable alternatives
(`off_topic: true`, `suggested_followups`), no sources, no generation. Those
alternatives are the *mined* questions (same semantic match as the answered-path
suggestions), returned as the closest 3 regardless of score -- `SUGGESTION_MIN_SIMILARITY`
defaults to 0 (raise it, e.g. 0.62, to filter weak matches) -- and every question
already asked earlier in the conversation is excluded, so clicking through suggestions
doesn't loop the same questions. The guardrail's own invented list stays in the payload
as `suggested_questions` for API compatibility but is no longer rendered: it isn't
scope-checked and once recommended a question the same guard then rejected.
Fail-open at every level (exception/unparseable verdict/off-allowlist redirect → old numeric path),
and deliberately *after* the answer-cache gate so cached answers still cost zero calls.
Otherwise drop chunks that didn't individually clear the floor (`filter_weak_chunks`, with an
exemption for lexical-only-hit chunks that carry a `0.0` sentinel score) and generate a
grounded answer from what's left. Medium/low-confidence answers get a `Certainty:` line
appended by the prompt; low-confidence queries additionally get tagged in LangSmith for
review (`_flag_low_confidence`).

"Continue exploring" suggestions are pre-mined offline (LLM workers crawl each doc per
`backend/ingestion/SUGGESTION_MINING_PROMPT.md`; `ingestion/load_suggestions.py`
validates, resolves anchors to `chunk_ids`, embeds, upserts into `suggested_questions`).
At answer time `app/services/suggestions.py` cosine-matches the already-computed
`query_vec` against those embeddings (same-authority first, asked question excluded) and
the result rides along as `suggested_followups`; best-effort, so a miss just falls back
to the frontend's static bank (`lib/followUpQuestions.ts`). Clicks re-enter the normal
pipeline — anchors are provenance, never a retrieval bypass (that would serve superseded
versions). **Suggestions are never stored in the answer cache** (`_strip_for_storage`)
and are re-attached at serve time — including cache hits, where `retrieval.
_attach_suggested_followups` recovers the stored `query_embedding` from Postgres with
zero LLM calls. Freezing them in the cache once made repeats serve pre-crawl/empty
suggestions ("same questions over and over"); the mined table keeps growing, so anything
derived from it must stay live.

Chat generation (not embeddings, which always stay on OpenAI) goes through
`chat_completion()`: OpenAI first, falling back to NaraRouter (`laguna-s-2.1`,
`router.bynara.id`) on any OpenAI failure — a failure trips a 60s cooldown so
follow-up questions skip straight to NaraRouter instead of re-hitting an
already-rate-limited OpenAI. Inside that OpenAI attempt (and inside `embed()`) sits a
tenacity retry: 2 further attempts, 1s then 2s, and **only** on genuinely transient
errors (`RateLimitError`, `APITimeoutError`, `APIConnectionError`, 5xx
`APIStatusError`) — never auth errors or 400s, which can't succeed on a second try.
It does not replace or fight the fallback: once exhausted, the existing `except` fires
unchanged, one `_mark_openai_degraded()` for the episode, cooldown and per-IP soft-cap
semantics untouched. ~3s worst-case added latency keeps it inside the 10s heartbeat. Separately, a per-client soft cap (3 calls per rolling
60s) routes a single IP's overflow to NaraRouter too, without hard-blocking with a
429. NaraRouter's own streaming API is unreliable — a trailing chunk with an empty
`choices` list, and occasional mid-stream `APIError`s, both confirmed by testing
directly against it — so it's always called non-streaming and delivered as one
instant chunk instead of token-by-token; the frontend has a matching `answer_reset`
event for the rarer case OpenAI itself drops mid-stream before falling back. Because
those non-streaming calls (and local LM Studio generation) can block for minutes,
`answer_question_stream` wraps them in `_call_with_heartbeats`: a `heartbeat` event
every 10s that the router emits as an SSE comment (`: heartbeat`) — keeps the
connection and the frontend's 60s idle timer alive without appearing in the trace.
`DEFAULT_LOCAL_MODEL` is `qwen/qwen3-4b-2507` (the 9b was unloaded/slow in practice).

`app/api/routers/` holds thin FastAPI route handlers — the dict `answer_question()`
returns is passed straight through as JSON, never reinterpreted. `app/services/
enrichment.py`'s `enrich_result()` bolts on two additive-only extras (sibling version
list for the version ledger, per-chunk document info for the source panel) that must
never break the core answer if they fail.

### Chat history

`app/api/routers/chats.py` + two tables (`chats`, `chat_messages` in `app/core/db.py`'s
`SCHEMA_SQL`) give each anonymous visitor real, persisted chat history — a sidebar
list, switchable, like a normal chat product. There is no login: `frontend/src/api/
clientId.ts` generates a UUID once into `localStorage` and every `/chats*` call is
scoped to it server-side (a mismatched `client_id` 404s). A chat is created lazily on
its first message, not on "New Chat" click, so idle visits don't clutter the list.
Deliberately decoupled from `/ask` and `/ask-stream` — persistence calls are
fire-and-forget from the frontend (`saveChatMessage()` swallows its own errors) so a
DB hiccup can never break the live conversation, same spirit as `enrich_result()`.

### Frontend

`frontend/src/`, React 19 + TypeScript + Vite + Tailwind v4. One page, no routing —
but *not* stateless anymore: see Chat history above. The CLI (`cli/ask.py`, `cli/
demo.py`) remains genuinely stateless; only the web frontend persists.

- `AnswerCard.tsx` renders the answer as real Markdown (`react-markdown`, not custom
  string parsing) with component overrides for the structured format (`Findings:`/
  `Summary:`/`Certainty:` headings, blockquoted direct citations).
- `CitationBlock.tsx` / `VersionLedger.tsx` render the mandatory citation and the
  version history — the ledger highlights whichever version was *actually* cited for
  the live query (not just the newest), and only shows red/excluded styling for a
  version genuinely excluded by an active filter that query.
- `SourcePanel.tsx` lists every retrieved chunk, visually distinguishing which ones
  actually cleared the confidence floor and were used for generation.
- `CitationPopover.tsx` shows a text excerpt plus a "View in PDF" control that opens
  `PdfOverlay.tsx`, which renders precise highlight rectangles directly from
  `backend/ingestion/rechunk.py`'s stored bounding boxes — no client-side text matching.
  It imports the **legacy** pdf.js build (`pdfjs-dist/legacy/build/…`, see
  `src/pdfjs-legacy.d.ts`): v6 uses `Map.prototype.getOrInsertComputed` (ES2025)
  unconditionally and breaks on older browsers with that exact error string — the
  legacy bundle self-polyfills it (upstream's prescribed fix, verified in dist).
  The popover also hosts both on-demand follow-ups (`DiffFollowup`, `CrossCheckRegulation`),
  each carrying the current `provider`/`model` (threaded from `App.tsx`'s `lastFilters`)
  so local mode reaches LM Studio; all three cache-aware surfaces (the main answer too)
  render the shared `CacheNotice` on `cache_hit` (`diff_cache` exact-key,
  `cross_check_cache` on doc/page/question — provider is never part of any cache key).
  Its light-red "Remove from cache" control POSTs the signed `cache_token` minted with
  that hit to `/cache/evict` (`app/core/cache_evict.py` HMAC, ~1h TTL) and deletes ONLY
  that entry — Redis key plus its Postgres row (including the matched `cache_id` on a
  semantic answer-cache hit), never a whole-cache purge. `CACHE_EVICT_SECRET` should be
  set on Render, or tokens die on restart/sleep (the control then just does nothing).
  **Known, accepted for the demo:** this control is visible to every anonymous
  visitor, so anyone served a cache hit can evict that one entry. Deliberately not
  hardened — the token is HMAC-signed and entry-scoped (no enumeration, no bulk
  purge), the route is rate-limited 10/min + 30/hour, and the blast radius is a cache
  row that regenerates on the next ask, so no data is lost and the spend amplification
  is ~1:1. The real objection is product, not security: "Remove from cache" is
  infrastructure vocabulary aimed at a user who has no cache. If this ever goes beyond
  a demo, rename it **"Regenerate answer"** (same code path, eviction becomes an
  implementation detail) or hide it behind an `ENABLE_CACHE_EVICT` flag — do not build
  an auth layer to protect a cache row.
- `AppShell.tsx` / `Sidebar.tsx` — below `md` (768px) the sidebar is an off-canvas
  drawer (scrim, Escape to close); at `md`+ it's a static column, collapsible via the
  same header toggle button (`ChatHeader.tsx`'s hamburger, visible at every width now).
  One `onToggleSidebar` handler drives both: it reads `window.matchMedia("(min-width:
  768px)")` at click time to decide whether to flip the mobile drawer's open state or
  the desktop collapse state — CSS breakpoints, not React state, are what actually
  decide which mechanism is visible, so the handler has to ask the same question CSS
  is answering. Desktop collapse is remembered in `localStorage`
  (`regulense-sidebar-collapsed`) as a layout preference, unlike the once-per-session
  onboarding keys below. **Gotcha fixed here:** the sidebar's mobile z-index (50) used
  to apply unscoped, including at `md`+ — a flex/grid item's `z-index` takes effect
  even at `position: static` (a real CSS special case), so despite being "just a
  static column" on desktop, it still out-ranked the full-page onboarding/home
  overlays (z-index 40) and rendered on top of them, letting the sidebar's buttons be
  clicked straight through a modal that was supposed to block the page. Fixed by
  scoping the z-index to mobile only (`md:z-auto`) — any *new* full-page overlay only
  needs to clear the existing 40, not guess how high the sidebar might reach.
- `OnboardingWelcome.tsx` — shown once per browser session (`sessionStorage`,
  `regulense-onboarding-v2`), reopenable any time via the ReguLense logo in
  `Sidebar.tsx`. A blocking modal wizard (dimmed backdrop, centered two-panel dialog,
  3 horizontally-sliding steps), not a full-page takeover — that was tried
  (`docs/ONBOARDING_REDESIGN_HANDOFF.md`) and deliberately replaced. Never gates on
  backend readiness; purely local UI state.
- `FeaturePopup.tsx` — shown once per browser session (`sessionStorage`,
  `regulense-feature-popup-v1`, matching `OnboardingWelcome`'s reset behavior — it used
  to be `localStorage`/once-ever, which read as "stopped showing" after the first
  session), reopenable any time from the `?` in `ChatHeader.tsx`. Same
  blocking-modal-with-sliding-deck mechanic as onboarding, explaining three real UI
affordances (supersession filter, version diff, PDF citation trail) against the
components that actually implement them.
- Both onboarding and the feature popup share a "case file" visual language (ink
  stamps for jurisdiction, an animated SVG confidence gauge, a highlighted doc-leaf
  citation mockup) on the OS system-font stack (`--font-system` in `tokens.css`) so a
  Mac/iOS visitor renders real SF Pro — Apple restricts embedding the font file
  itself, so `-apple-system`/`BlinkMacSystemFont` is the actual correct way to get it
  in a browser.

Design tokens (`frontend/src/tokens.css`) are used semantically, never decoratively:
brass = in force / medium confidence, rust = superseded / low confidence, amber =
medium confidence specifically, plus one institutional color per authority (DHA
steel-blue, DoH teal, MOHAP plum).

**Production is a single same-origin deployment on Render.** There is no second
frontend deployment — do not link one, and do not assume the
`ALLOWED_ORIGINS`/CORS setup below still needs a non-local origin in production.

**Render** (`https://neohealth-gpzo.onrender.com`) is a complete, self-contained,
same-origin deployment: `uvicorn api:app` from the repo root (`api.py` a thin
`sys.path` shim importing `app.main:app`), building the frontend and copying its
`dist/` into `backend/static/`, which FastAPI mounts at `/` as a catch-all *after*
every API route. It needs no CORS to work standalone, and it's kept this way on
purpose — do not strip the frontend build back out of it.
`frontend/src/api/url.ts`'s `apiUrl()` + `VITE_API_URL` still make the frontend's
calls absolute instead of relative when set, but Render's own build gets
`VITE_API_URL=""` (relative paths), matching its same-origin setup — that
cross-origin plumbing is now vestigial unless a second frontend deployment returns.

**Important gotcha for anyone editing `render.yaml` or `ALLOWED_ORIGINS`'s default in
`config.py`:** this Render service does not appear to be Blueprint-synced to
`render.yaml` — editing the file alone did not change the live service's actual
Build/Start Command or environment variables in testing. Confirm any deploy-config
change actually took effect (`curl` the live URL) rather than trusting the file;
if it didn't, the fix has to go through the Render dashboard directly.

### Containers & deploy

- `docker-compose.yml` (repo root) — one-command local dev: Postgres+pgvector, backend,
  frontend. See `RUN.md`'s quick-start.
- `backend/Dockerfile`, `frontend/Dockerfile` — multi-stage builds for each service;
  used for local `docker compose` parity. Render doesn't use either directly -- it
  builds and runs the backend from source per `render.yaml` (or its own dashboard
  config, per the gotcha above), with the frontend build folded into that same step.
- `.github/workflows/deploy.yml` — CI/CD test gate only (pytest, frontend
  typecheck/lint/test) on push to `main`. Render deploys independently via its own
  auto-deploy-on-push, not through this workflow.
