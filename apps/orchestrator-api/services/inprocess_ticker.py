"""⏱️ In-process heartbeat thread (boss 2026-07-20: market open but NO trading).

Diagnosis: on prod the interval ticks were NOT firing — the live scalp engine +
tournament stayed frozen for minutes while the instance was warm, yet a manual
/scalp/tick worked instantly. The engines are fine; the HEARTBEAT that drives
them was dead (APScheduler interval jobs not executing and/or the external
cron-job.org pinger paused).

This is a dead-simple daemon thread — independent of APScheduler's scheduler
loop — that ticks the live scalp engine, Algorithm-1 exits, and the 3-strategy
tournament every 15s during KST market hours. All ticks are lock-/ON-CONFLICT-
guarded, so it is safe even if the external cron ALSO pings.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
_started = False
_KST = timezone(timedelta(hours=9))


def _market_hours() -> bool:
    n = datetime.now(_KST)
    if n.weekday() >= 5:
        return False
    m = n.hour * 60 + n.minute
    return 9 * 60 <= m <= 15 * 60 + 25


# boss 2026-07-21: at market open, running all engines every 15s over the 21-stock fleet
# starved Render's 5s health check → the instance crash-looped → NOTHING traded. Two guards:
#   1) STARTUP GRACE — do NO tick work for the first 45s after boot, so a freshly (re)started
#      instance can pass health checks and finish deploying BEFORE the heartbeat hammers it
#      (this is what breaks the crash-loop).
#   2) THROTTLE the heavier/less-urgent jobs to 60s (tournament + candle); keep Algo-1 exits
#      at 15s (cheap, protects positions) and Algo-1 entries at 60s. Scalp/candle ticks return
#      immediately when their engine is disabled, so they cost almost nothing when OFF.
_GRACE_SEC = 45
# boss 2026-07-21: the instance crash-looped at market open running all 3 engines × 21 stocks
# (Render's 5s health check starved → restart loop → nothing traded). Test ALGO 1 ONLY for
# now; Algo 2 & 3 are HIDDEN and do NOT run (their DB tables/data are untouched — flip this
# back to False to re-enable them). This keeps the heartbeat as light as possible.
_ONLY_ALGO1 = True


def _loop() -> None:
    from db.base import SessionLocal
    logger.info("inprocess_ticker: heartbeat started (15s, market hours, 45s grace, algo1-only=%s)", _ONLY_ALGO1)
    _start = time.time()
    _i = 0
    while True:
        _i += 1
        try:
            if time.time() - _start < _GRACE_SEC:
                pass                          # startup grace — let the instance stabilize
            elif _market_hours():
                # Algorithm-1 auto exits every 15s (cheap, protects positions); ENTRIES every
                # ~60s (the full-universe scan is heavy — a 60s cadence keeps health fast).
                db = SessionLocal()
                try:
                    from services.auto_trader import exit_pulse as _a1
                    _a1(db)
                    if _i % 4 == 0:
                        from services.auto_trader import tick as _a1_tick
                        _a1_tick(db)
                except Exception as e:
                    db.rollback(); logger.warning(f"ticker algo1: {str(e)[:100]}")
                finally:
                    db.close()
                if not _ONLY_ALGO1:
                    # Algo-2 scalp (cheap early-return when disabled)
                    db = SessionLocal()
                    try:
                        from services.scalp_trader import tick as _scalp_tick
                        _scalp_tick(db)
                    except Exception as e:
                        db.rollback(); logger.warning(f"ticker scalp: {str(e)[:100]}")
                    finally:
                        db.close()
                    if _i % 4 == 0:            # tournament + Algo-3 candle throttled to 60s
                        db = SessionLocal()
                        try:
                            from services.strategy_tournament import tick as _tt
                            _tt(db)
                        except Exception as e:
                            db.rollback(); logger.warning(f"ticker tournament: {str(e)[:100]}")
                        finally:
                            db.close()
                        db = SessionLocal()
                        try:
                            from services.candle_trader import tick as _c3
                            _c3(db)
                        except Exception as e:
                            db.rollback(); logger.warning(f"ticker algo3: {str(e)[:100]}")
                        finally:
                            db.close()
        except Exception as e:
            logger.warning(f"inprocess_ticker loop: {str(e)[:120]}")
        time.sleep(15)


def start_ticker() -> None:
    """Idempotent — starts the single daemon heartbeat thread."""
    global _started
    if _started:
        return
    _started = True
    t = threading.Thread(target=_loop, name="inprocess-ticker", daemon=True)
    t.start()
