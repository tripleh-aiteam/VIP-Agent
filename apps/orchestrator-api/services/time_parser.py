"""
VIP AI Platform — Natural-language Time Parser (v3 redesign)
Parses Korean + English meeting-time expressions into UTC datetime.

Supported patterns:
  EN:  "in 10 minutes", "after 30 mins", "in 1 hour",
       "at 3pm", "at 15:00", "at 3:30 pm", "tomorrow at 2pm",
       "tonight at 8", "in 2 hours"
  KR:  "10분 후", "30분 뒤", "30분 뒤에", "1시간 후",
       "오후 3시", "오후 3시 30분", "오전 10시",
       "내일 오후 2시", "지금"

Returns (utc_datetime, kind, original_phrase) where kind is
"relative" | "absolute_today" | "absolute_tomorrow" | "now".
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional


_NOW_TRIGGERS = re.compile(r"\b(?:right now|now|immediately|즉시|지금|바로)\b", re.IGNORECASE)


# Relative — English
_REL_EN = re.compile(
    r"\b(?:in|after|after\s+about)\s+"
    r"(\d{1,3})\s*"
    r"(minute|min|mins|minutes|hour|hr|hrs|hours)\b",
    re.IGNORECASE,
)

# Relative — Korean
_REL_KR_MIN = re.compile(r"(\d{1,3})\s*분(?:\s*(?:후|뒤|이후))?(?:에)?")
_REL_KR_HOUR = re.compile(r"(\d{1,2})\s*시간(?:\s*(?:후|뒤|이후))?(?:에)?")


# Absolute — English (today)
_ABS_EN = re.compile(
    r"\bat\s+"
    r"(\d{1,2})(?::(\d{2}))?\s*"
    r"(am|pm|a\.m\.|p\.m\.)?",
    re.IGNORECASE,
)
_TOMORROW_EN = re.compile(r"\btomorrow\b", re.IGNORECASE)
_TONIGHT_EN = re.compile(r"\btonight\b", re.IGNORECASE)


# Absolute — Korean
# "오후 3시", "오후 3시 30분", "오전 10시", "15시 30분"
_ABS_KR = re.compile(
    r"(오전|오후|아침|저녁|밤)?\s*"
    r"(\d{1,2})\s*시"
    r"(?:\s*(\d{1,2})\s*분)?",
)
_TOMORROW_KR = re.compile(r"내일")
_TODAY_KR = re.compile(r"오늘")


def parse_meeting_time(text: str, now: Optional[datetime] = None) -> Optional[dict]:
    """Extract a meeting time from text. Returns None if no time mentioned.
    `now` defaults to datetime.utcnow() — pass an explicit value in tests.

    Returns:
      {
        "scheduled_at_utc": datetime,
        "kind": "now" | "relative" | "absolute_today" | "absolute_tomorrow",
        "matched": str,            # the substring that matched
        "delta_seconds": int,      # seconds from `now` to scheduled time
      }
    """
    if not text:
        return None
    base = now or datetime.utcnow()

    # 1. NOW
    if _NOW_TRIGGERS.search(text):
        return _result(base, "now", "now", 0)

    # 2. Relative — English
    m = _REL_EN.search(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        delta = timedelta(hours=n) if unit.startswith("h") else timedelta(minutes=n)
        when = base + delta
        return _result(when, "relative", m.group(0), int(delta.total_seconds()))

    # 3. Relative — Korean (minutes)
    m = _REL_KR_MIN.search(text)
    if m:
        n = int(m.group(1))
        when = base + timedelta(minutes=n)
        return _result(when, "relative", m.group(0), n * 60)

    # 3b. Relative — Korean (hours)
    m = _REL_KR_HOUR.search(text)
    if m:
        n = int(m.group(1))
        when = base + timedelta(hours=n)
        return _result(when, "relative", m.group(0), n * 3600)

    # 4. Absolute — English
    is_tomorrow_en = bool(_TOMORROW_EN.search(text))
    is_tonight_en = bool(_TONIGHT_EN.search(text))
    m = _ABS_EN.search(text)
    if m:
        h = int(m.group(1))
        mm = int(m.group(2) or 0)
        ampm = (m.group(3) or "").lower().replace(".", "")
        if ampm in ("pm", "p m") and h < 12:
            h += 12
        elif ampm in ("am", "a m") and h == 12:
            h = 0
        elif not ampm and is_tonight_en and h < 12:
            h += 12
        target = base.replace(hour=h, minute=mm, second=0, microsecond=0)
        if is_tomorrow_en:
            target = target + timedelta(days=1)
            kind = "absolute_tomorrow"
        else:
            if target <= base:
                target = target + timedelta(days=1)
                kind = "absolute_tomorrow"
            else:
                kind = "absolute_today"
        return _result(target, kind, m.group(0), int((target - base).total_seconds()))

    # 5. Absolute — Korean
    is_tomorrow_kr = bool(_TOMORROW_KR.search(text))
    m = _ABS_KR.search(text)
    if m:
        period = m.group(1) or ""
        h = int(m.group(2))
        mm = int(m.group(3) or 0)
        # 오후/저녁/밤 → PM, 오전/아침 → AM
        if period in ("오후", "저녁", "밤") and h < 12:
            h += 12
        elif period in ("오전", "아침") and h == 12:
            h = 0
        if h >= 24:
            return None
        target = base.replace(hour=h, minute=mm, second=0, microsecond=0)
        if is_tomorrow_kr:
            target = target + timedelta(days=1)
            kind = "absolute_tomorrow"
        else:
            if target <= base:
                target = target + timedelta(days=1)
                kind = "absolute_tomorrow"
            else:
                kind = "absolute_today"
        return _result(target, kind, m.group(0), int((target - base).total_seconds()))

    return None


def _result(when: datetime, kind: str, matched: str, delta: int) -> dict:
    return {
        "scheduled_at_utc": when,
        "kind": kind,
        "matched": matched,
        "delta_seconds": delta,
        "human": _humanize(delta, kind),
    }


def _humanize(delta_sec: int, kind: str) -> str:
    if kind == "now" or delta_sec < 30:
        return "right now"
    mins = delta_sec // 60
    if mins < 60:
        return f"in {mins} minute(s)"
    hours = mins / 60
    if hours < 24:
        return f"in {hours:.1f} hour(s)"
    days = hours / 24
    return f"in {days:.1f} day(s)"
