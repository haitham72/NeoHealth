"""
ReguLense API composition root: a thin HTTP wrapper around
app.core.retrieval.answer_question() for the frontend.

A browser can't hold OPENAI_API_KEY or talk to Postgres directly, so this exists purely
as plumbing between the two. It does not reinterpret answer_question()'s contract --
the dict it returns is passed straight through as JSON.

Run: python -m app.main   (from backend/, serves on http://localhost:8000)
"""
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.routers import ask, corpus, cross_check, diff, feedback, health, models, pdf
from app.core.config import ALLOWED_ORIGINS, STATIC_DIR
from app.core.db import ensure_schema, get_connection, release_connection
from app.core.limiter import limiter

app = FastAPI(title="ReguLense API")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Production is same-origin (single Render service serves both frontend and backend --
# see CLAUDE.md's "Containers & deploy"). ALLOWED_ORIGINS exists for local dev:
# docker-compose (nginx on :8080 talking to uvicorn on :8000) and Vite dev (:5173
# talking to :8000) unless proxied.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.on_event("startup")
def _run_schema_migrations():
    import os
    print(f"RENDER_DEBUG: STATIC_DIR={STATIC_DIR} exists={STATIC_DIR.is_dir()}", flush=True)
    if STATIC_DIR.is_dir():
        print(f"RENDER_DEBUG: files={os.listdir(STATIC_DIR)}", flush=True)
    else:
        print("RENDER_DEBUG: STATIC_DIR does not exist!", flush=True)
        # Try to find where the files actually are
        backend_dir = STATIC_DIR.parent
        print(f"RENDER_DEBUG: backend_dir={backend_dir} contents={os.listdir(backend_dir) if backend_dir.is_dir() else 'N/A'}", flush=True)
    # SCHEMA_SQL is all CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS, so this is
    # safe to run on every boot. Without this, a fresh deploy's Postgres would be
    # missing the daily_usage table (only ever created locally via load_db.py) and
    # every /ask call would 500 until someone ran a migration by hand.
    conn = get_connection()
    try:
        ensure_schema(conn)
    finally:
        release_connection(conn)


# No authentication required - removed Google Sign-In for production deployment
app.include_router(health.router)
app.include_router(ask.router)
app.include_router(models.router)
app.include_router(corpus.router)
app.include_router(diff.router)
app.include_router(cross_check.router)
app.include_router(feedback.router)
app.include_router(pdf.router)


# --- Static files: serve the built React frontend (production only) ---
# This mount MUST be last — it's a catch-all that would shadow API routes
# if placed above them. In dev, the Vite proxy handles frontend requests
# so this directory can be empty or absent without breaking anything.
if STATIC_DIR.is_dir() and any(STATIC_DIR.iterdir()):
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    # reload=True was tried and reverted: on this Windows setup, WatchFiles sometimes
    # detects a change, logs "Reloading...", and then the respawn silently fails --
    # the OLD worker process keeps running and keeps serving stale code with no error
    # of any kind. That's worse than the manual restart it was meant to save, two days
    # out from a demo: silently-stale-but-looks-fine beats "add a flag," every time.
    uvicorn.run(app, host="0.0.0.0", port=8000)
