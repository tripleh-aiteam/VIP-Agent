"""
chatbot_mode_detector — Boss-IN / Boss-OUT mode resolution.

Logic:
- Auto-detect based on Korean working hours (Mon-Fri 09:00-18:00 KST)
  unless a manual override is in effect.
- Manual overrides live in `chatbot_mode_overrides` (in-memory for now;
  can be promoted to a DB table or Redis-backed if multi-pod is needed).
- Per-agent override — VIP could be IN while Real Estate is OUT.

Decisions affecting Boss-IN vs Boss-OUT behavior:
- Boss-IN: bot drafts replies, persists to suggested_reply, does NOT send.
           Boss reviews + approves on dashboard.
- Boss-OUT: bot sends autonomously, escalates urgent items via the
            agent's escalationChannel.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional


BossMode = Literal["in", "out"]

# In-memory per-agent overrides. Maps agent_id → (mode, expires_at)
# expires_at is None for indefinite manual override.
_overrides: dict[str, tuple[BossMode, Optional[datetime]]] = {}


# ============================================================================
#  Public API
# ============================================================================

def get_mode(agent_id: str, now: Optional[datetime] = None) -> tuple[BossMode, bool]:
    """Return (mode, auto_detected) for an agent.
    `auto_detected` is True when no manual override is active."""
    now = now or datetime.utcnow()

    override = _overrides.get(agent_id)
    if override:
        mode, expires_at = override
        if expires_at is None or expires_at > now:
            return mode, False
        # Override expired — clean it up
        _overrides.pop(agent_id, None)

    return auto_detect_mode(now), True


def set_manual_mode(
    agent_id: str,
    mode: BossMode,
    *,
    expires_in_hours: Optional[float] = None,
) -> None:
    """Set a manual mode override. Caller can either pin indefinitely
    (expires_in_hours=None) or until a deadline (e.g. 'I'm out for 2 hours')."""
    expires_at = None
    if expires_in_hours is not None:
        expires_at = datetime.utcnow() + timedelta(hours=expires_in_hours)
    _overrides[agent_id] = (mode, expires_at)


def clear_manual_mode(agent_id: str) -> None:
    """Return to auto-detect for an agent."""
    _overrides.pop(agent_id, None)


def auto_detect_mode(now: Optional[datetime] = None) -> BossMode:
    """Korean working hours: Mon-Fri 09:00-18:00 KST → IN, else OUT.

    Hours and timezone are configurable via env for non-KR agents:
      CHATBOT_WORKING_HOURS_START (default 9)
      CHATBOT_WORKING_HOURS_END   (default 18)
      CHATBOT_WORKING_TZ_OFFSET   (default 9 = KST = UTC+9)
    """
    now = now or datetime.utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    tz_offset_hours = int(os.getenv("CHATBOT_WORKING_TZ_OFFSET", "9"))
    start_hour = int(os.getenv("CHATBOT_WORKING_HOURS_START", "9"))
    end_hour = int(os.getenv("CHATBOT_WORKING_HOURS_END", "18"))

    local_time = now + timedelta(hours=tz_offset_hours)
    weekday = local_time.weekday()              # Mon=0 ... Sun=6
    hour = local_time.hour

    is_weekday = weekday < 5
    is_working_hour = start_hour <= hour < end_hour
    return "in" if (is_weekday and is_working_hour) else "out"


def is_urgent_keyword(text: str) -> bool:
    """Quick check for Korean + English keywords that immediately
    flag a message as urgent. Caller can combine with LLM classification
    for nuanced cases."""
    if not text:
        return False
    lower = text.lower()
    urgent_kr = ["계약금", "긴급", "지금 당장", "당장", "응급", "사고", "위급", "법적", "고소", "민원"]
    urgent_en = ["urgent", "asap", "right now", "emergency", "legal", "lawsuit", "complaint"]
    for k in urgent_kr + urgent_en:
        if k in lower:
            return True
    return False
