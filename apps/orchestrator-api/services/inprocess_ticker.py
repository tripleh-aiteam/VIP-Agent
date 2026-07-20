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


def _loop() -> None:
    from db.base import SessionLocal
    logger.info("inprocess_ticker: heartbeat thread started (15s, market hours)")
    _i = 0
    while True:
        _i += 1
        try:
            if _market_hours():
                # 1) live Algorithm-2 scalp engine (buys/sells its own book)
                db = SessionLocal()
                try:
                    from services.scalp_trader import tick as _scalp_tick
                    _scalp_tick(db)
                except Exception as e:
                    db.rollback(); logger.warning(f"ticker scalp: {str(e)[:100]}")
                finally:
                    db.close()
                # 2) Algorithm-1 auto exits every 15s (cheap, protects positions);
                #    ENTRIES only every ~60s — auto_trader.tick runs a full-universe
                #    scan that pegged the CPU and failed Render's 5s health check when
                #    run every 15s (boss 2026-07-20 instance-failed). Algo 1 is a 1-hour
                #    engine, so a 60s entry cadence is plenty.
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
                # 3) the 3-strategy shadow tournament
                db = SessionLocal()
                try:
                    from services.strategy_tournament import tick as _tt
                    _tt(db)
                except Exception as e:
                    db.rollback(); logger.warning(f"ticker tournament: {str(e)[:100]}")
                finally:
                    db.close()
                # 4) Algorithm 3 — the candle trader (3 up → buy, 3 down → sell)
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
