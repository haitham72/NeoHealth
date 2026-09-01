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

# Frontend and API are same-origin in production (render.yaml builds the frontend
# into static/, served by app.main -- see the StaticFiles mount there), so this only
# matters for local dev (Vite on :5173 talking to uvicorn on :8000) unless
# ALLOWED_ORIGINS is set.
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

# Worst-case ceiling on API calls/day (every /ask or /ask-stream call, including
# provider="local", costs at least one embeddings call -- see app.core.retrieval.embed()).
# Not a precision budget, just a bound on the blast radius of a runaway client.
DAILY_OPENAI_CALL_CAP = int(os.environ.get("DAILY_OPENAI_CALL_CAP", "300"))
