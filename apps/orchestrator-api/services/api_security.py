"""
VIP AI Platform — API Security
API key authentication for webhook endpoints + rate limiting.
"""

import os
import time
from collections import defaultdict
from functools import wraps

from fastapi import Request, HTTPException, Depends
from fastapi.security import APIKeyHeader

from services.logger import log


# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------

# Keys loaded from env: comma-separated list
_API_KEYS_ENV = os.getenv("VIP_API_KEYS", "")
VALID_API_KEYS: set[str] = {k.strip() for k in _API_KEYS_ENV.split(",") if k.strip()}

# Default development key (only used if no keys configured)
DEV_KEY = "vip-dev-key-2026"
if not VALID_API_KEYS:
    VALID_API_KEYS.add(DEV_KEY)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Depends(api_key_header)):
    """Dependency: verify API key for protected endpoints."""
    if not api_key or api_key not in VALID_API_KEYS:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Include X-API-Key header.",
        )
    return api_key


async def optional_api_key(api_key: str = Depends(api_key_header)):
    """Dependency: log API key usage but don't block (for monitoring)."""
    if api_key and api_key in VALID_API_KEYS:
        return api_key
    return None


# ---------------------------------------------------------------------------
# Rate Limiting (in-memory, per-IP)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple in-memory sliding window rate limiter."""

    def __init__(self, requests_per_minute: int = 60):
        self.rpm = requests_per_minute
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_id: str) -> bool:
        """Check if request is allowed. Cleans up old entries."""
        now = time.time()
        window_start = now - 60

        # Clean old entries
        self._requests[client_id] = [
            t for t in self._requests[client_id] if t > window_start
        ]

        if len(self._requests[client_id]) >= self.rpm:
            return False

        self._requests[client_id].append(now)
        return True

    def remaining(self, client_id: str) -> int:
        """How many requests remaining in current window."""
        now = time.time()
        window_start = now - 60
        recent = [t for t in self._requests[client_id] if t > window_start]
        return max(0, self.rpm - len(recent))


# Global rate limiters
_general_limiter = RateLimiter(requests_per_minute=120)   # 120/min for general API
_webhook_limiter = RateLimiter(requests_per_minute=30)    # 30/min for webhooks
_compose_limiter = RateLimiter(requests_per_minute=10)    # 10/min for report composition


def _get_client_id(request: Request) -> str:
    """Get client identifier from request (IP or forwarded header)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit_general(request: Request):
    """Rate limit: 120 requests/minute per IP."""
    client_id = _get_client_id(request)
    if not _general_limiter.is_allowed(client_id):
        remaining = _general_limiter.remaining(client_id)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. {remaining} requests remaining. Try again in 60 seconds.",
        )


async def rate_limit_webhook(request: Request):
    """Rate limit: 30 requests/minute per IP for webhooks."""
    client_id = _get_client_id(request)
    if not _webhook_limiter.is_allowed(client_id):
        raise HTTPException(
            status_code=429,
            detail="Webhook rate limit exceeded (30/min). Try again shortly.",
        )


async def rate_limit_compose(request: Request):
    """Rate limit: 10 requests/minute per IP for report composition."""
    client_id = _get_client_id(request)
    if not _compose_limiter.is_allowed(client_id):
        raise HTTPException(
            status_code=429,
            detail="Compose rate limit exceeded (10/min). Try again shortly.",
        )
