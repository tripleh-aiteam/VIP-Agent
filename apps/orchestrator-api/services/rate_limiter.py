"""
rate_limiter — in-memory sliding-window rate limiter for the Kakao
chatbot webhook (and any other endpoint that needs abuse protection).

Two layers:

  1. Per-IP limit — protects against attackers hammering the webhook URL
     directly. Default: 30 requests per minute → temp block for 10 min.

  2. Per-Kakao-user limit — protects against a single customer flooding
     messages (whether by accident or on purpose). Default: 12 messages
     per minute → drop with 429.

In-memory only. State resets on orchestrator restart, which is fine —
Render restarts ~weekly, and a malicious actor can't outlast a 10-min
block window.

Future upgrade: persist long blocks to Redis (already configured) so
they survive restarts and are shared across multi-instance deployments.

API:
    rate_limit_ip(ip, limit_per_min, block_minutes) → (ok, retry_after_seconds)
    rate_limit_user(user_id, limit_per_min)         → (ok, retry_after_seconds)
"""

from __future__ import annotations

import time
import threading
from collections import deque
from typing import Optional


# ============================================================================
#  Sliding window store
# ============================================================================

_lock = threading.Lock()
_ip_window: dict[str, deque[float]] = {}      # ip → request timestamps
_user_window: dict[str, deque[float]] = {}    # user_id → request timestamps
_ip_blocked_until: dict[str, float] = {}      # ip → unix ts when block lifts

# Cap memory so a flood doesn't OOM the process
_MAX_KEYS = 5000


# ============================================================================
#  Core
# ============================================================================

def _now() -> float:
    return time.time()


def _gc_if_needed(store: dict, max_keys: int = _MAX_KEYS) -> None:
    """Cheap cleanup: if the store grows too big, drop the oldest 25%."""
    if len(store) <= max_keys:
        return
    # Sort keys by their last-seen timestamp and drop the oldest 25%
    items = list(store.items())
    items.sort(key=lambda kv: (kv[1][-1] if isinstance(kv[1], deque) and kv[1] else 0))
    drop = items[: max_keys // 4]
    for k, _ in drop:
        store.pop(k, None)


def rate_limit_ip(
    ip: str,
    limit_per_min: int = 30,
    block_minutes: int = 10,
) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds).

    If IP exceeds limit_per_min, it's blocked for block_minutes minutes.
    Subsequent calls during the block return (False, remaining_seconds).
    """
    if not ip:
        return True, 0

    now = _now()
    with _lock:
        # Already blocked?
        block_until = _ip_blocked_until.get(ip)
        if block_until and block_until > now:
            return False, max(1, int(block_until - now))

        # Get or create window
        window = _ip_window.get(ip)
        if window is None:
            window = deque(maxlen=limit_per_min + 1)
            _ip_window[ip] = window
            _gc_if_needed(_ip_window)

        # Drop timestamps outside the 60s window
        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()

        # Record this request
        window.append(now)

        # Exceeded? → block
        if len(window) > limit_per_min:
            block_until = now + block_minutes * 60
            _ip_blocked_until[ip] = block_until
            window.clear()  # don't retain — wasted memory
            return False, block_minutes * 60

        return True, 0


def rate_limit_user(
    user_id: str,
    limit_per_min: int = 12,
) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds).

    Per-user throttle — used to drop floods from a single Kakao customer
    without blocking other customers from the same IP (Kakao's IPs are
    shared). Lighter penalty than IP blocking: no persistent block,
    just drop the offending message.
    """
    if not user_id:
        return True, 0

    now = _now()
    with _lock:
        window = _user_window.get(user_id)
        if window is None:
            window = deque(maxlen=limit_per_min + 1)
            _user_window[user_id] = window
            _gc_if_needed(_user_window)

        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()

        window.append(now)

        if len(window) > limit_per_min:
            # Compute how long until the oldest in-window timestamp ages out
            retry = max(1, int(60 - (now - window[0])))
            return False, retry

        return True, 0


def get_block_status(ip: str) -> Optional[dict]:
    """Return current block info for an IP (for /health or diagnostics).
    Returns None when the IP isn't blocked."""
    now = _now()
    with _lock:
        until = _ip_blocked_until.get(ip)
        if not until or until <= now:
            return None
        return {
            "ip": ip,
            "blocked_until_unix": until,
            "remaining_seconds": int(until - now),
        }


def unblock_ip(ip: str) -> bool:
    """Manual override — admin can lift a block via API/console."""
    with _lock:
        if ip in _ip_blocked_until:
            del _ip_blocked_until[ip]
            return True
        return False


def stats() -> dict:
    """Snapshot of current rate-limiter state for monitoring."""
    with _lock:
        return {
            "tracked_ips": len(_ip_window),
            "tracked_users": len(_user_window),
            "currently_blocked_ips": sum(
                1 for ts in _ip_blocked_until.values() if ts > _now()
            ),
        }
