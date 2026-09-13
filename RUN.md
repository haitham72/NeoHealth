# ReguLense — how to run

## Manual setup — native Postgres + Redis (this machine's default, no Docker)

Docker Desktop's networking proved unreliable for local dev here (container
port-forwarding silently dropped mid-session), so Postgres 17 + pgvector and Redis run
natively via Homebrew instead. One-time setup already done: `brew install postgresql@17
pgvector redis`, the `haitham`/`reglens` role+database created with `vector` enabled,
and the corpus + cache data migrated in from the old Docker volume. `backend/.env`
already points at both (`PGPORT=5432`, `REDIS_URL=redis://localhost:6379/0`) — nothing
to pass on the command line.

```bash
brew services start postgresql@17
brew services start redis
source .venv/bin/activate
cd backend
python -m app.main                 # Terminal 1
```

```bash
cd frontend
npm run dev                        # Terminal 2
```

Frontend: http://localhost:5173

Since `.env` points at the native ports (`5432`/`6379`), this always uses the native
services regardless of whether any Docker containers happen to be running — there's no
overlap to worry about (the old Docker Postgres container, if still around, sits on a
different port, `5433`). `brew services list` shows both services' status;
`brew services stop postgresql@17` / `brew services stop redis` to shut them down.

## Quick start (Docker) — alternative, not used on this machine right now

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and add OPENAI_API_KEY
docker compose up --build
```

Frontend: http://localhost:8080 | Backend: http://localhost:8000

This spins up its own Postgres/Redis/backend/frontend containers, entirely separate
from the native setup above (different `.env`, different ports) — pick one or the
other, don't mix.

## Semantic answer cache

`/ask` and `/diff-followup` answers are cached (Postgres `answer_cache`/`diff_cache` =
durable source of truth; Redis = fast exact-key layer). Every boot unconditionally
reloads every cached row from Postgres into Redis (`Warmed cache: N ask rows, M diff
rows` in the startup log) — Redis can be wiped or absent entirely and nothing is lost.
Redis is optional locally (skipped cleanly if `REDIS_URL` is unset or unreachable —
Postgres alone still serves cache hits, just without the Redis speed); in production,
running Redis is a deliberate, required part of the setup, not a nice-to-have —
`REDIS_URL` must point at Upstash (or an equivalent managed Redis), never at anything
living on the sleeping Render web dyno itself. Two env-overridable knobs:
`CACHE_HIT_THRESHOLD` (cosine similarity floor for a Postgres L2 paraphrase hit, default
`0.92`) and `CACHE_REDIS_TTL_SECONDS` (Redis key TTL, default 7 days).

**Every message is cache-eligible, including "Continue exploring" follow-ups** —
conversation history is passed to the LLM for phrasing context only, never used to
select or cite chunks, so the same question+filters always ground the same facts
regardless of prior turns. Follow-ups hit cache exactly like a fresh first message.

To pre-bake known demo questions ahead of time (so the first live ask is already a
cache hit): edit `backend/scripts/demo_questions.json`, then `cd backend && python -m
scripts.seed_answer_cache` (makes real, billed LLM calls — run deliberately, not in CI).
