"""POST /cache/evict request schema."""
from pydantic import BaseModel, Field


class CacheEvictRequest(BaseModel):
    # Opaque signed token minted with a cache-hit response (app/core/cache_evict.py).
    token: str = Field(min_length=1, max_length=2000)
