"""Single module-level slowapi Limiter instance. Every router that uses
@limiter.limit(...) imports THIS object; app.main registers it on app.state and
registers the RateLimitExceeded exception handler."""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
