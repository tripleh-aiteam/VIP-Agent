"""
VIP AI Platform — Meeting Rate Limiter (Sprint 6)
Protects against runaway costs / abuse:
- Max concurrent meetings per twin
- Max meeting joins per twin per hour
- Max listener sessions per meeting

Stored in-memory (per process). For multi-worker deployments swap the
in-memory counters for Redis — kept module-local here so deploying
behind one uvicorn process Just Works.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import MeetingParticipant


# Defaults override-able via env
_MAX_CONCURRENT_MEETINGS_PER_TWIN = int(os.getenv("MEETING_MAX_CONCURRENT_PER_TWIN", "3"))
_MAX_JOINS_PER_HOUR_PER_TWIN = int(os.getenv("MEETING_MAX_JOINS_PER_HOUR", "20"))
_MAX_LISTENERS_PER_MEETING = int(os.getenv("MEETING_MAX_LISTENERS", "5"))


# join_history[twin_id] = deque of UTC timestamps in seconds (sliding window 1h)
_join_history: dict[str, deque[float]] = {}
_join_lock = threading.Lock()


class RateLimitError(Exception):
    """Raised when a rate limit would be exceeded."""
    pass


def check_join_allowed(db: Session, twin_id: UUID) -> None:
    """Raise RateLimitError if this twin can't join another meeting now.
    Call inside /twins/{id}/meetings/join BEFORE persisting the participant.
    """
    # Hard cap on concurrent active meetings — query DB
    active = (
        db.query(MeetingParticipant)
        .filter(
            MeetingParticipant.twin_id == twin_id,
            MeetingParticipant.session_status.in_(("active", "escalated")),
        )
        .count()
    )
    if active >= _MAX_CONCURRENT_MEETINGS_PER_TWIN:
        raise RateLimitError(
            f"Twin already in {active} concurrent meetings "
            f"(max {_MAX_CONCURRENT_MEETINGS_PER_TWIN})"
        )

    # Sliding-window: joins per hour
    now = time.time()
    key = str(twin_id)
    with _join_lock:
        window = _join_history.setdefault(key, deque())
        cutoff = now - 3600
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= _MAX_JOINS_PER_HOUR_PER_TWIN:
            raise RateLimitError(
                f"Twin exceeded {_MAX_JOINS_PER_HOUR_PER_TWIN} joins/hour "
                f"(current window: {len(window)})"
            )
        window.append(now)


def check_listener_allowed(active_listeners_in_meeting: int) -> None:
    """Cap on concurrent voice listeners attached to one meeting."""
    if active_listeners_in_meeting >= _MAX_LISTENERS_PER_MEETING:
        raise RateLimitError(
            f"Meeting already has {active_listeners_in_meeting} listeners "
            f"(max {_MAX_LISTENERS_PER_MEETING})"
        )


def current_limits() -> dict:
    """Read-only snapshot for monitoring / config display."""
    return {
        "max_concurrent_meetings_per_twin": _MAX_CONCURRENT_MEETINGS_PER_TWIN,
        "max_joins_per_hour_per_twin": _MAX_JOINS_PER_HOUR_PER_TWIN,
        "max_listeners_per_meeting": _MAX_LISTENERS_PER_MEETING,
        "tracked_twins": len(_join_history),
    }
