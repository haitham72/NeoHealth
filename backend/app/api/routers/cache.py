"""POST /cache/evict -- remove ONE served cache entry.

The token is HMAC-signed and minted by whichever cache-hit path produced the response
the user clicked "Remove from cache" on (app/core/cache_evict.py), so a client can only
evict the exact entry it was shown -- no id enumeration, no whole-cache purge. Expired
or tampered tokens 400; evicting an already-gone entry is fine (the user-visible
outcome is identical), so the endpoint stays idempotent.
"""
from fastapi import APIRouter, HTTPException, Request

from app.api.schemas.cache import CacheEvictRequest
from app.core.answer_cache import evict_answer_cache
from app.core.cache_evict import verify_cache_token
from app.core.cross_check_cache import evict_cross_check_cache
from app.core.db import get_connection, release_connection
from app.core.diff_cache import evict_diff_cache
from app.core.limiter import limiter

router = APIRouter()


@router.post("/cache/evict")
@limiter.limit("10/minute;30/hour")
def evict_cache(request: Request, req: CacheEvictRequest):
    payload = verify_cache_token(req.token)
    if payload is None:
        raise HTTPException(400, "invalid or expired cache token")

    kind = payload.get("k")
    conn = get_connection()
    try:
        if kind == "answer":
            raw_id = payload.get("id")
            evict_answer_cache(
                conn,
                str(payload.get("q") or ""),
                bool(payload.get("s")),
                payload.get("a") or None,
                int(raw_id) if raw_id is not None else None,
            )
        elif kind == "diff":
            evict_diff_cache(conn, int(payload["cur"]), int(payload["prev"]),
                             str(payload.get("q") or ""))
        elif kind == "cross":
            evict_cross_check_cache(conn, int(payload["cur"]), int(payload["page"]),
                                    str(payload.get("q") or ""))
        else:
            raise HTTPException(400, "unknown cache kind")
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "malformed cache token payload")
    finally:
        release_connection(conn)

    return {"evicted": True, "kind": kind}
