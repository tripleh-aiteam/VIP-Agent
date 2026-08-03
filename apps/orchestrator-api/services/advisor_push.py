"""Push real Kiwoom bars OUT to the AI Advisor.

The Advisor runs in public cloud; VIP runs on the company server and holds the Kiwoom
key. Rather than opening a way in — a tunnel, a forwarded port, a token guarding an
inbound door — VIP dials out and posts what it chooses to share. The company server needs
no inbound path at all, and a compromise of the Advisor yields no route back here.

Because the direction is outbound, the permission boundary is this file: whatever
`SYMBOLS` lists is what the other side can ever see. Nothing else is reachable.

Off unless configured. Set on the server that should send:

    ADVISOR_PUSH_ENABLED=true
    ADVISOR_PUSH_URL=https://stock-advisor-agent-9qwi.onrender.com
    ADVISOR_PUSH_KEY=<the same secret as the Advisor's SNAPSHOT_PUSH_KEY>
    ADVISOR_PUSH_SYMBOLS=005930,000660,035420      # optional, defaults below

Read at call time, not import time, so a key added to .env takes effect on the next
restart without touching this file.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.logger import log

KST = timezone(timedelta(hours=9))

# What the Advisor is allowed to see. Deliberately a short, explicit list rather than
# "whatever is in the watchlist" — this is the permission grant, so it should be
# something a person reads and agrees to, not something that grows on its own.
DEFAULT_SYMBOLS = ["005930", "000660", "035420"]

SCHEMA = "vip.market-snapshot.v1"
_TIMEOUT = 20.0


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def enabled() -> bool:
    return (_env("ADVISOR_PUSH_ENABLED").lower() in ("1", "true", "yes", "on")
            and bool(_env("ADVISOR_PUSH_URL")) and bool(_env("ADVISOR_PUSH_KEY")))


def symbols() -> list[str]:
    raw = _env("ADVISOR_PUSH_SYMBOLS")
    if not raw:
        return list(DEFAULT_SYMBOLS)
    out = [c.strip().zfill(6) for c in raw.split(",") if c.strip().isdigit()]
    return out[:20] or list(DEFAULT_SYMBOLS)


def _name_for(code: str) -> str:
    try:
        from services.scalp_trader import _name
        return _name(code) or code
    except Exception:
        return code


def build_snapshot(code: str, tic: str = "1", count: int = 600) -> Optional[dict[str, Any]]:
    """One ticker's session as the wire contract the Advisor validates.

    ONE session, never a rolling window. Kiwoom returns the most recent `count` bars,
    which spills across days — and a multi-day tape breaks the far side twice. The
    Advisor files a snapshot under `{code}/{session}`, so a window labelled with its last
    day would overwrite a different window tomorrow. And the rules read closes in
    sequence, so the jump from one day's 15:30 to the next day's 09:00 would be counted
    as an ordinary one-minute move — inventing a signal out of an overnight gap.

    So: take the date of the newest bar and keep only that day. `count` is well over the
    391 minutes of a KRX session, which is what makes a full day available to trim from.

    Returns None when Kiwoom gives nothing — pushing an empty session would look on the
    far side like a real day in which nothing traded.
    """
    from services.kiwoom_rest import minute_bars
    rows = minute_bars(code, tic=tic, count=count)
    if not rows:
        return None
    bars = [{"ts": r["ts"], "open": r.get("open"), "high": r.get("high"),
             "low": r.get("low"), "close": r["close"], "volume": r.get("volume") or 0}
            for r in rows if r.get("close")]
    if not bars:
        return None
    # the session is the DATE OF THE BARS, never today's date: a push retried after
    # midnight would otherwise file yesterday's tape under today
    session = str(bars[-1]["ts"])[:10]
    bars = [b for b in bars if str(b["ts"])[:10] == session]
    if not bars:
        return None
    return {"schema": SCHEMA, "code": str(code).zfill(6), "name": _name_for(code),
            "interval": f"{tic}m", "session": session, "source": "kiwoom",
            "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
            "bars": bars}


def push_one(code: str, tic: str = "1", count: int = 400) -> dict[str, Any]:
    """Build and POST one ticker. Never raises — a failed push must not disturb trading."""
    if not enabled():
        return {"ok": False, "code": code, "skipped": "not configured"}
    try:
        snap = build_snapshot(code, tic=tic, count=count)
        if not snap:
            return {"ok": False, "code": code, "skipped": "no bars"}
        import httpx
        url = _env("ADVISOR_PUSH_URL").rstrip("/") + "/market-snapshots/push"
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(url, json=snap,
                            headers={"X-Snapshot-Key": _env("ADVISOR_PUSH_KEY")})
        if r.status_code in (200, 201):
            return {"ok": True, "code": code, "bars": len(snap["bars"]),
                    "session": snap["session"]}
        # the body carries the validation reason; without it a 422 is unactionable
        return {"ok": False, "code": code, "status": r.status_code,
                "detail": r.text[:200]}
    except Exception as e:
        return {"ok": False, "code": code, "error": f"{type(e).__name__}: {str(e)[:160]}"}


def push_all(tic: str = "1", count: int = 400) -> dict[str, Any]:
    """Every shared ticker, one request each. Called by the scheduler after the close."""
    if not enabled():
        return {"ok": False, "skipped": "ADVISOR_PUSH_* not configured"}
    results = [push_one(c, tic=tic, count=count) for c in symbols()]
    sent = sum(1 for r in results if r.get("ok"))
    if sent:
        log.info(f"advisor push: {sent}/{len(results)} snapshots sent",
                 extra={"action": "advisor.push"})
    failed = [r for r in results if not r.get("ok")]
    if failed:
        log.warning(f"advisor push: {len(failed)} failed — {failed[:3]}",
                    extra={"action": "advisor.push.failed"})
    return {"ok": sent > 0, "sent": sent, "total": len(results), "results": results}
