# ReguLense — how to run

## Quick start: `docker compose up`

The fastest path to a running stack (Postgres+pgvector, backend, frontend) with no local
Python/Node setup at all:

```
cp backend/.env.example backend/.env   # then fill in OPENAI_API_KEY (required) and
                                        # LANGSMITH_* (optional) — see backend/.env.example
docker compose up --build
```

- Frontend: `http://localhost:8080`
- Backend API: `http://localhost:8000` (`/healthz`)
- Postgres: `localhost:5433` (`regulense` / `regulense_dev_password` / db `regulense`)

The 2 `VITE_SUPABASE_*` build args (Google Sign-In) come from your shell environment or a
root `.env` file (`docker compose` auto-loads one if present) — see `frontend/.env.example`
for the variable names. Without them, the frontend still builds and serves, but sign-in
won't work. See `docker-compose.yml` for the full service wiring.

Everything below is the manual, non-Docker setup — useful for active development (hot
reload, debugging, running the ingestion pipeline) rather than just running the app.

## setup .venv Mac

```
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-ingestion.txt -r backend/requirements-dev.txt
```

# CLI demo

python -m cli.demo

# Web UI

cd backend
python -m app.main # terminal 1 — localhost:8000
cd 'frontend'; npm run dev # terminal 2 — localhost:5173

## One-time setup (already done on this machine)

- `.venv/` — Python 3.14 venv with requests, pdfplumber, psycopg2-binary, openai, python-dotenv
- `backend/.env` — `OPENAI_API_KEY` + Postgres connection (`PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE=regulense`)
- Docker container `temporal_note-db` — Postgres+pgvector on port 5433, `regulense` database created,
  `vector` extension enabled
- `backend/dataset/` — 65 source PDFs already downloaded
- `backend/parsed_documents.json` — 34 documents auto-parsed with metadata + page text (~30 remain
  in `backend/needs_manual.json`, not yet fixed by hand — a known, deliberate gap, not a bug)
- Postgres already loaded: 40 documents (37 official + 3 research), 860 chunks with
  embeddings — chunked by Docling (structure-aware: real headers/sections, cross-page,
  exact bbox provenance for PDF highlighting), not the old one-chunk-per-page split
- (Optional) LM Studio running locally on port 1234 with a Qwen chat model loaded, for the
  ChatGPT/Local provider switcher in the web UI — not required, OpenAI is the default

**If starting from scratch on a new machine**, run the pipeline in this order, from
`backend/` with the venv active:

```
python -m ingestion.download            # step 1: pull the PDFs into dataset/
python -m ingestion.ingest               # step 2+3: parse metadata, resolve supersession
python -m ingestion.apply_manual_fixes   # hand-fix docs ingest.py can't auto-parse
python -m ingestion.load_db              # step 4: insert documents (no chunks yet)
python -m ingestion.rechunk              # step 5: Docling structure-aware chunking + embed
```

`ingestion.rechunk` needs `documents` rows to already exist (matches by sha256, from
`ingestion.load_db`) — it only replaces `chunks`. Not idempotent the same way as the others:
re-running it deletes and recomputes a document's chunks every time (accepted, since a
one-time migration script doesn't need incremental resume logic). Docling downloads
layout-model weights on first run and needs `do_ocr=False` + MPS/CUDA acceleration
configured in the script to run at a reasonable speed — see `ingestion/rechunk.py`'s own
comments.

Each script is idempotent — safe to re-run (download skips existing files, load_db skips
already-loaded documents by sha256).

## Before every run — make sure Docker Postgres is up

```
docker start temporal_note-db
```

(Docker Desktop itself must be running first. If `docker ps` fails with a pipe error,
Docker Desktop is closed — start it and wait ~20s.)

## The actual demo (this is what you show Friday)

```
python -m cli.demo
```

Runs one licensing question through naive RAG (no supersession awareness) and ReguLense
(supersession-filtered) side by side. Naive cites the superseded v1.2 manual; ReguLense
cites v1.3 (in force) and flags 2 excluded superseded versions. No arguments needed.

## Ask arbitrary questions

```
python -m cli.ask "What are the requirements for licensing a healthcare professional in Dubai?"
```

Quote the question. Below the confidence threshold it prints
`I don't have current guidance on that.` instead of generating an answer — try an
off-topic question (e.g. "What is the capital of France?") to see that path.

## Web UI (screenshare-able version of the same demo)

Two processes, both must be running:

```
cd backend && python -m app.main    # terminal 1 — serves http://localhost:8000
cd frontend && npm run dev          # terminal 2 — serves http://localhost:5173
```

If `frontend/package.json` has changed (new dependency added — e.g. react-markdown,
pdfjs-dist, @supabase/supabase-js) and the dev server errors on a missing module, run
`npm install` inside `frontend/` first. **Never `pip install` a frontend package** —
`pip` and `npm` are two separate dependency trees for two separate processes (`.venv`
for the Python API, `frontend/node_modules` for the Vite dev server); pip has no idea
what a JS package is and will error or install the wrong thing.

Google Sign-In needs `frontend/.env` populated from `frontend/.env.example` (the 2
`VITE_SUPABASE_*` vars) and `backend/.env`'s `SUPABASE_JWT_SECRET` set to the same
project's JWT signing secret (Supabase dashboard: Authentication > API Settings) —
without both, the sign-in screen will render but authentication will fail. The Google
provider itself also needs to be enabled in that Supabase project's Authentication >
Providers settings (needs a Google Cloud OAuth Client ID/secret) — a fresh Supabase
project doesn't have it on by default.

Open `http://localhost:5173`. Sign in with Google, then: type a question (or click an
example chip — they auto-submit), watch the live reasoning trace, see the cited answer,
the authority badge (DHA/DoH steel/teal for regulation, a dashed "RESEARCH" chip for the 3
academic papers), and the version ledger (in-force version in brass, superseded ones struck
through in rust). The "Exclude outdated regulations" checkbox is the naive-vs-ReguLense
toggle from `cli.demo`, live: turn it off and re-ask the same question to watch the citation
swap to a superseded version. A ChatGPT/Local segmented control next to the query bar
switches generation to a locally-running LM Studio model (needs LM Studio running with a
chat model loaded — the dropdown populates live from whatever's actually loaded there).

First-time frontend setup: `cd frontend && npm install` (already done on this machine).

`app.main` is a thin FastAPI wrapper around `app.core.retrieval` — it doesn't change any
retrieval/answer logic. `POST /ask-stream` (what the web UI actually uses) streams
pipeline-stage progress as Server-Sent Events, ending with the same payload `POST /ask`
returns in one shot; `GET /local-models` and `GET /corpus-stats` back the provider
switcher and the footer count respectively. `frontend/vite.config.ts` proxies each API
path (`/ask`, `/ask-stream`, `/pdf`, etc.) → `localhost:8000` so there's no CORS setup to
worry about in dev.

## Troubleshooting

- **`KeyError: 'PGHOST'` or similar** — you're not running from inside `backend/`, or
  `backend/.env` is missing. `app.core.db` loads `.env` via `python-dotenv`, which resolves
  relative to the current working directory.
- **`OpenAI AuthenticationError`** — `.env`'s `OPENAI_API_KEY` is a template placeholder
  rather than a real key — swap in a real key from your OpenAI account.
- **Garbled/crashing output with special characters** — scripts avoid non-ASCII in
  printed strings specifically to survive Windows' default cp1252 console. If you see
  a `UnicodeEncodeError`, something reintroduced a smart quote/em-dash into a `print()`.
- **Docker unreachable** (`open //./pipe/dockerDesktopLinuxEngine`) — Docker Desktop
  isn't running. Start it, wait for the whale icon to settle, then `docker start temporal_note-db`.
- **Frontend shows "The request didn't complete"** — `app.main` isn't running, or it is but
  can't reach Postgres/OpenAI (its error message is passed through verbatim — same causes
  as the CLI troubleshooting above apply).
- **Frontend loads but questions hang forever** — check `app.main`'s terminal for a
  traceback; also confirm nothing else is bound to port 8000 (`app.main` doesn't auto-pick
  a free port).
- **`401 Unauthorized` on every request** — no Supabase session token was attached, or
  it expired/failed verification. Confirm you're signed in (check the browser console
  for a `getSession()` error) and that `backend/.env`'s `SUPABASE_JWT_SECRET` matches
  the same Supabase project the frontend's `VITE_SUPABASE_*` vars target.
- **`429` with "You've reached today's limit of 5 questions"** — the per-user daily
  quota (`daily_user_usage`, `USER_DAILY_QUESTION_CAP`) tripped for the signed-in user,
  not the global `DAILY_OPENAI_CALL_CAP`. Expected behavior, not a bug — resets at
  midnight UTC (`CURRENT_DATE` in Postgres).
