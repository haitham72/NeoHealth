"""Liveness and readiness probes for the API."""
from fastapi import APIRouter, HTTPException

from app.core.db import get_connection, release_connection

router = APIRouter()


@router.get("/healthz")
def healthz():
    """Cheap liveness probe -- deliberately does not touch the database."""
    return {"status": "ok"}


@router.get("/health")
def health():
    """Cheap liveness probe -- deliberately does not touch the database."""
    return {"ok": True}


@router.get("/ready")
def ready():
    """Readiness probe that confirms the database is reachable."""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("select 1")
            cur.fetchone()
        return {"ok": True}
    except Exception:
        raise HTTPException(status_code=503, detail="backend is not ready")
    finally:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                release_connection(conn)
            except Exception:
                pass
