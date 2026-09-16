"""HMAC-signed, short-lived eviction tokens for the answer/diff/cross-check caches.

Why tokens instead of accepting raw key parts from the client: the remove control
travels with a cached response, and the client already knows the question/document
ids it sent -- without a signature, anyone could delete arbitrary entries (ids are
sequential) or force cache misses at will. A signed token binds deletion to the exact
entry the user was shown: the payload carries only the key parts needed to delete
that one row (plus the matched answer-cache id for semantic hits), and the signature
makes it tamper-proof. Tokens expire, so a leaked token can't evict forever.

Secret: `CACHE_EVICT_SECRET` when set (recommended -- tokens then survive process
restarts, which Render's free tier does routinely); otherwise a per-process random
secret is generated, and tokens minted before a restart simply fail verification
(the UI keeps showing the note, no security impact).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

_ENV_SECRET = os.environ.get("CACHE_EVICT_SECRET", "").strip()
_SECRET = _ENV_SECRET.encode("utf-8") if _ENV_SECRET else secrets.token_bytes(32)

TOKEN_TTL_SECONDS = 3600


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    return _b64e(hmac.new(_SECRET, body.encode("utf-8"), hashlib.sha256).digest())


def sign_cache_token(kind: str, **parts) -> str:
    """Mints a token for one specific cache entry. `parts` are the key fields the
    evict endpoint needs (e.g. q/s/a/id for answer, cur/prev/q for diff)."""
    payload = {"k": kind, "exp": int(time.time()) + TOKEN_TTL_SECONDS, **parts}
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def verify_cache_token(token: str) -> dict | None:
    """Returns the payload dict when the signature is valid and unexpired, else None."""
    try:
        body, signature = token.split(".", 1)
        if not hmac.compare_digest(_sign(body), signature):
            return None
        payload = json.loads(_b64d(body))
        if not isinstance(payload, dict):
            return None
        if int(payload.get("exp", 0)) < time.time():
            return None
        return payload
    except Exception:
        return None
