"""Single source of truth for filesystem path constants and a couple of shared
env-var-derived settings. Every path here is computed once, relative to this
file's own location (backend/app/core/config.py -> backend/), so ingestion/,
cli/, and app/api code never re-derive `HERE = Path(__file__).parent`
themselves the way the old flat root-level scripts did -- moving a file one
directory deeper no longer breaks its paths by one level.
"""
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

DATASET_DIR = BACKEND_DIR / "dataset"
STATIC_DIR = BACKEND_DIR / "static"
CORPUS_URLS_FILE = BACKEND_DIR / "corpus_urls.txt"
PARSED_DOCUMENTS_FILE = BACKEND_DIR / "parsed_documents.json"
NEEDS_MANUAL_FILE = BACKEND_DIR / "needs_manual.json"

# Render is API-only now; the Vercel frontend calls it cross-origin, so this must list
# every real frontend origin. ALLOWED_ORIGINS overrides this default entirely -- if
# Render has that env var set to something stale, this default is never even reached,
# so a stale live deployment needs the dashboard value updated directly, not just this.
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,https://frontend-tawny-kappa-10.vercel.app",
).split(",")

# Vercel's generated deployment URLs change on each deploy
# (for example, frontend-k6q7eskv2-system722-1077s-projects.vercel.app).
# Keep this scoped to this project/account shape rather than all of vercel.app.
ALLOWED_ORIGIN_REGEX = os.environ.get(
    "ALLOWED_ORIGIN_REGEX",
    r"https://frontend-[a-z0-9]+-system722-1077s-projects\.vercel\.app",
)

# Worst-case ceiling on API calls/day (every /ask or /ask-stream call, including
# provider="local", costs at least one embeddings call -- see app.core.retrieval.embed()).
# Not a precision budget, just a bound on the blast radius of a runaway client.
DAILY_OPENAI_CALL_CAP = int(os.environ.get("DAILY_OPENAI_CALL_CAP", "300"))

# Optional L1 exact-key cache. When unset, ask still uses Postgres L2 semantic cache.
# Local: redis://localhost:6379/0 (see docker-compose redis). Prod demo: Upstash REDIS_URL
# so data survives Render free web sleep (the web dyno sleeping does not wipe Upstash).
REDIS_URL = os.environ.get("REDIS_URL", "").strip() or None

# Cosine similarity floor for Postgres L2 paraphrase hits (1 - embedding <=> query).
CACHE_HIT_THRESHOLD = float(os.environ.get("CACHE_HIT_THRESHOLD", "0.92"))

# Redis key TTL in seconds (default 7 days). Postgres rows have no TTL.
CACHE_REDIS_TTL_SECONDS = int(os.environ.get("CACHE_REDIS_TTL_SECONDS", str(7 * 24 * 3600)))
