"""GET /local-models."""
import requests
from fastapi import APIRouter, Request

from app.core.limiter import limiter
from app.core.retrieval import LOCAL_BASE_URL

router = APIRouter()


@router.get("/local-models")
@limiter.limit("30/minute")
def local_models(request: Request):
    """Live list of chat models currently loaded in LM Studio, for the frontend's
    provider switcher. Embedding models are filtered out -- they're not valid chat
    completion targets and would just be confusing noise in the dropdown. Returns an
    empty list (not an error) if LM Studio isn't running, so the switcher can show
    "no local models available" instead of a broken request."""
    try:
        resp = requests.get(f"{LOCAL_BASE_URL}/models", timeout=3)
        resp.raise_for_status()
        ids = [m["id"] for m in resp.json().get("data", [])]
        return {"models": [m for m in ids if "embed" not in m.lower()]}
    except requests.RequestException:
        return {"models": []}
