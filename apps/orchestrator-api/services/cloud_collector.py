"""cloud_collector.py — SERVER-SIDE data collection: no PC required.

The PC collector exists because Kiwoom only answers registered IPs — but Render's
outbound IPs ARE registered (verified live: the tape check reads Kiwoom from Render).
So the server can run the very same collection pass itself. This wraps the PC
collector's `_one_pass` for on-server use, fired by the external cron every ~2 min
during market hours (ping-driven: each ping wakes a sleeping instance, the pass runs
in a background thread, and the lock plus a freshness check keep it single-flight).

Failover logic: if a snapshot is FRESH (< 90s — some collector, PC or cloud, just
wrote), the pass SKIPS. Net effect: PC collector running → cloud does nothing;
PC off/asleep/dead → cloud takes over within one ping. The PC becomes an optional
accelerator (30s cadence vs ~2 min), never a requirement.
"""
from __future__ import annotations

import importlib.util
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.logger import log

KST = timezone(timedelta(hours=9))
FRESH_SKIP_SEC = 90            # someone else wrote within this window → skip

_lock = threading.Lock()
_rc_module = None


def _rc():
    """Load the collector module once (it lives under ml/, not the services package)."""
    global _rc_module
    if _rc_module is None:
        p = Path(__file__).resolve().parents[1] / "ml" / "realtime" / "rt_snapshot_collector.py"
        spec = importlib.util.spec_from_file_location("rt_snapshot_collector_cloud", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _rc_module = mod
    return _rc_module


def _market_open() -> bool:
    n = datetime.now(KST)
    return n.weekday() < 5 and 540 <= (n.hour * 60 + n.minute) <= 931


def _snapshot_age_sec() -> float | None:
    try:
        from sqlalchemy import text

        from db.base import SessionLocal
        s = SessionLocal()
        try:
            v = s.execute(text(
                "SELECT EXTRACT(EPOCH FROM (now() - max(ts))) FROM realtime_snapshot")).scalar()
            return float(v) if v is not None else None
        finally:
            s.close()
    except Exception:
        return None


def _run_locked() -> None:
    try:
        from db.base import engine
        conn = engine.raw_connection()
        try:
            rc = _rc()
            rc._ensure(conn)
            n = rc._one_pass(conn)
            log.info(f"cloud collector pass wrote {n} tickers", extra={"action": "collector.cloud_pass"})
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"cloud collector pass failed: {str(e)[:160]}")
    finally:
        _lock.release()


def run_pass(force: bool = False) -> dict[str, Any]:
    """Start one collection pass in the background (returns immediately for the cron)."""
    if not force and not _market_open():
        return {"started": False, "skipped": "market closed"}
    age = _snapshot_age_sec()
    if not force and age is not None and age < FRESH_SKIP_SEC:
        return {"started": False, "skipped": "collector already active",
                "snapshot_age_sec": round(age)}
    if not _lock.acquire(blocking=False):
        return {"started": False, "skipped": "a pass is already running"}
    threading.Thread(target=_run_locked, daemon=True).start()
    return {"started": True, "snapshot_age_sec": (round(age) if age is not None else None)}
