"""GET /healthz -- a cheap liveness probe distinct from any DB-touching route,
useful for container/orchestration health checks. No DB touch, no rate limit."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz():
    return {"status": "ok"}
