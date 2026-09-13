# ReguLense — how to run

## Quick start (Docker)

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and add OPENAI_API_KEY
docker compose up --build
```

Frontend: http://localhost:8080 | Backend: http://localhost:8000

## Manual setup

```bash
docker start temporal_note-db
source .venv/bin/activate
cd backend && python -m app.main  # Terminal 1
cd ..
cd frontend && npm run dev         # Terminal 2
```

Frontend: http://localhost:5173

## Semantic answer cache

`/ask` and `/diff-followup` answers are cached (Postgres `answer_cache`/`diff_cache` =
durable source of truth; Redis = fast exact-key layer, optional). Every boot
unconditionally reloads every cached row from Postgres into Redis (`Warmed cache: N ask
rows, M diff rows` in the startup log) — Redis can be wiped or absent entirely and
nothing is lost. `docker compose up` brings up Redis automatically; for the manual
setup, `export REDIS_URL=redis://localhost:6379/0` before starting the backend if you
want the Redis layer locally (it's skipped cleanly if unset — Postgres alone still
serves cache hits, just without the Redis speed). In prod, `REDIS_URL` should point at
Upstash (or an equivalent managed Redis) — never at anything living on the sleeping
Render web dyno itself.

To pre-bake known demo questions ahead of time (so the first live ask is already a
cache hit): edit `backend/scripts/demo_questions.json`, then `cd backend && python -m
scripts.seed_answer_cache` (makes real, billed LLM calls — run deliberately, not in CI).
