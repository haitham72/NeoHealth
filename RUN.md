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

# to kill the backend
lsof -ti:8000 | xargs kill      # kill whatever holds port 8000 (stable, PID-independent)
pkill -f "python -m app.main"   # or kill by command
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

**Confirmed live 2026-09-15** (`Warmed cache: N ask rows, M diff rows` with N/M > 0 in
the boot log — 0/0 with no error means Redis silently isn't connected, not that the
cache is empty; check the two gotchas below before assuming a real outage). Two real
mistakes hit wiring this up, worth knowing before you repeat them:
- **`redis` missing from the root `requirements.txt`.** Render's `buildCommand`
  installs from the repo-root `requirements.txt`, not `backend/requirements.txt` —
  the package was added to the latter only, so Render's build silently never
  installed it. No crash, no error: `_get_redis()`'s `try/except` degrades straight
  to Postgres-only, indistinguishable from `REDIS_URL` simply being unset. Keep both
  requirements files in sync; nothing currently enforces this automatically.
- **Upstash gives you two different credentials for the same database — only one
  works here.** The dashboard's "REST API" tab (`UPSTASH_REDIS_REST_URL` +
  `UPSTASH_REDIS_REST_TOKEN`) is for Upstash's own HTTP SDK, not the standard `redis`
  Python package this app uses. You need the **native protocol** connection string
  instead — usually shown as a `redis-cli --tls -u redis://...` connect command on a
  separate tab. Take just the `-u` value, and change its scheme from `redis://` to
  `rediss://` (TLS) — the `--tls` flag doesn't survive being extracted from the
  `redis-cli` command into a plain `REDIS_URL` env var; the scheme is what carries
  that requirement for `redis.from_url()`.

**Every message is cache-eligible, including "Continue exploring" follow-ups** —
conversation history is passed to the LLM for phrasing context only, never used to
select or cite chunks, so the same question+filters always ground the same facts
regardless of prior turns. Follow-ups hit cache exactly like a fresh first message.

To pre-bake known demo questions ahead of time (so the first live ask is already a
cache hit): edit `backend/scripts/demo_questions.json`, then `cd backend && python -m
scripts.seed_answer_cache` (makes real, billed LLM calls — run deliberately, not in CI).
