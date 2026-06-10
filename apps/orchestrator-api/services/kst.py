"""kst — Korea Standard Time (UTC+9) helpers for report timestamps.

The server runs in UTC; reports are Korea-facing, so dates/times shown in the
reports and emails must be KST. Scheduled runs fire at 21:30 UTC = 06:30 KST the
next day, so using the UTC date would show the wrong (previous) day."""

from __future__ import annotations

from datetime import datetime, timedelta

_KST = timedelta(hours=9)


def kst_now() -> datetime:
    return datetime.utcnow() + _KST


def kst_date() -> str:
    """KST calendar date, e.g. '2026-06-11'."""
    return kst_now().strftime("%Y-%m-%d")


def kst_label(fmt: str = "%Y-%m-%d %H:%M") -> str:
    """KST timestamp label with a KST suffix, e.g. '2026-06-11 06:30 KST'."""
    return kst_now().strftime(fmt) + " KST"
