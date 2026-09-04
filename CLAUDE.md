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

Backend (Python), from `backend/` with the venv active:
```
source ../.venv/bin/activate
python -m app.main                # serves API on :8000 (no --reload: see app/main.py's comment on why)
python -m cli.demo                # CLI naive-vs-ReguLense side-by-side comparison
python -m cli.ask "question"      # CLI single-question query
pytest                            # backend test suite (Postgres+pgvector required)
```

Frontend, from `frontend/`:
```
npm run dev        # Vite dev server on :5173
npm run build       # tsc -b && vite build
npm run lint         # oxlint
npx tsc --noEmit    # type-check only, no test suite configured
```

Full local run: `docker compose up` (Postgres+pgvector, backend, frontend, one command) —
see `RUN.md` for env vars and the quick-start. Manually: start Postgres
(`docker start temporal_note-db`), then `python -m app.main` from `backend/` (terminal 1)
and `npm run dev` in `frontend/` (terminal 2). See `RUN.md` for first-time setup.

## Architecture

### Ingestion pipeline

Five standalone scripts, run in this order for a from-scratch corpus load, from `backend/`:

```
ingestion.download → ingestion.ingest → ingestion.apply_manual_fixes → ingestion.load_db → ingestion.rechunk
```

- `ingestion/download.py` — pulls PDFs listed in `corpus_urls.txt` into `dataset/`.
- `ingestion/ingest.py` — extracts text via pdfplumber, parses `doc_code`/`version`/
  `effective_date`/`authority` from the document's own printed metadata (never the
  filename or URL — a document's footer is the only trustworthy source), resolves
  supersession (newest `effective_date` per `doc_code` wins; older versions flagged
  `superseded=true`). Writes `parsed_documents.json` (gitignored). Documents whose
  metadata can't be auto-parsed (different template) land in `needs_manual.json`.
- `ingestion/apply_manual_fixes.py` — hand-written metadata entries for documents
  `ingest.py` can't parse (e.g. MOHAP federal docs, research papers — no
  `DHA/...`-style code).
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

Pipeline: embed query → hybrid search (pgvector cosine + Postgres full-text, both
filtered by supersession/authority) → Reciprocal Rank Fusion → confidence tiering on
the top-fused chunk's semantic score (`CONFIDENCE_HIGH`/`MEDIUM`/`LOW` constants near
the top of `app/core/retrieval.py`, recalibrated against real query sampling — read the
comment above them before changing the numbers) → abstain below the floor; otherwise
drop chunks that didn't individually clear the floor (`filter_weak_chunks`, with an
exemption for lexical-only-hit chunks that carry a `0.0` sentinel score) and generate a
grounded answer from what's left. Medium/low-confidence answers get a `Certainty:` line
appended by the prompt; low-confidence queries additionally get tagged in LangSmith for
review (`_flag_low_confidence`).

Chat generation (not embeddings, which always stay on OpenAI) goes through
`chat_completion()`: OpenAI first, falling back to NaraRouter (`laguna-s-2.1`,
`router.bynara.id`) on any OpenAI failure — a failure trips a 60s cooldown so
follow-up questions skip straight to NaraRouter instead of re-hitting an
already-rate-limited OpenAI. Separately, a per-client soft cap (3 calls per rolling
60s) routes a single IP's overflow to NaraRouter too, without hard-blocking with a
429. NaraRouter's own streaming API is unreliable — a trailing chunk with an empty
`choices` list, and occasional mid-stream `APIError`s, both confirmed by testing
directly against it — so it's always called non-streaming and delivered as one
instant chunk instead of token-by-token; the frontend has a matching `answer_reset`
event for the rarer case OpenAI itself drops mid-stream before falling back.

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
- `OnboardingWelcome.tsx` — shown once per browser session (`sessionStorage`,
  `regulense-onboarding-v2`), reopenable any time via the ReguLense logo in
  `Sidebar.tsx`. A blocking modal wizard (dimmed backdrop, centered two-panel dialog,
  3 horizontally-sliding steps), not a full-page takeover — that was tried
  (`docs/ONBOARDING_REDESIGN_HANDOFF.md`) and deliberately replaced. Never gates on
  backend readiness; purely local UI state.
- `FeaturePopup.tsx` — shown once per browser (`localStorage`,
  `regulense-feature-popup-v1`), reopenable from the `?` in `ChatHeader.tsx`. Same
  blocking-modal-with-sliding-deck mechanic as onboarding, explaining three real UI
  affordances (supersession filter, version diff, PDF citation trail) against the
  components that actually implement them, per `docs/VERCEL_ONBOARDING_PLAN.md` §4.
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

**This section describes the target/in-progress split-deploy architecture, not yet
fully live as of 2026-09-04 — see `docs/VERCEL_ONBOARDING_PLAN.md` for the full plan
and current known gaps.** Render still also serves the old same-origin build
(`uvicorn api:app` from the repo root, `api.py` a thin `sys.path` shim importing
`app.main:app`, `render.yaml` copying the frontend's `dist/` into `backend/static/`)
until it's redeployed with the current branch.

The intended production split: Vercel serves the static frontend
(`https://frontend-tawny-kappa-10.vercel.app`, Vercel project `frontend` under account
`system722-1077` — no `vercel.json` in the repo, so it's not git-linked; it was
deployed directly via `vercel --prod` from a local `frontend/` tree), and Render
serves the API at `https://neohealth-gpzo.onrender.com`. This is a genuine
cross-origin split, so **CORS is real now**, not local-dev-only:
`app/core/config.py`'s `ALLOWED_ORIGINS` (set via `render.yaml`, `sync: false` for
secrets but a literal `value:` for this one) must list the exact live Vercel URL or
every request silently fails with no visible reason in the failing app — confirmed
live 2026-09-04 (see `docs/VERCEL_ONBOARDING_PLAN.md` §0.2's note). `frontend/src/
api/url.ts`'s `apiUrl()` + `VITE_API_URL` make the frontend's calls absolute instead
of relative, precisely so it can be hosted on a different origin than the API.

### Containers & deploy

- `docker-compose.yml` (repo root) — one-command local dev: Postgres+pgvector, backend,
  frontend. See `RUN.md`'s quick-start.
- `backend/Dockerfile`, `frontend/Dockerfile` — multi-stage builds for each service;
  used for local `docker compose` parity. Production doesn't use either directly --
  Render builds and runs the backend from source (`render.yaml`), with the frontend
  build folded into that same build step.
- `.github/workflows/deploy.yml` — CI/CD test gate only (pytest, frontend
  typecheck/lint/test) on push to `main`. Render deploys independently via its own
  auto-deploy-on-push, not through this workflow.
