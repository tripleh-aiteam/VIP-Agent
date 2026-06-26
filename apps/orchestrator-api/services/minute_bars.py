"""minute_bars.py — per-minute intraday OHLCV capture + the day's VOLATILITY BASELINE.

The collector (PC, Kiwoom) fetches each monitored stock's 1-min candles and upserts them
into raw_minute_prices. Render reads them to (a) draw a true intraday minute chart and
(b) compute the day's volatility baseline — the input the day-trade feasibility answer
needs: "given how this stock is moving today, is a +1% scalp realistic, and where's the
sensible stop?"

Write path (record/ensure_tables): psycopg2 connection (collector).
Read path (intraday_vol/read_bars): SQLAlchemy session (orchestrator endpoint).
"""
from __future__ import annotations

from typing import Any


def ensure_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS raw_minute_prices (
                ticker  TEXT NOT NULL,
                ts      TIMESTAMP NOT NULL,        -- KST minute (naive)
                open    BIGINT, high BIGINT, low BIGINT, close BIGINT,
                volume  BIGINT,
                PRIMARY KEY (ticker, ts)
            )""")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_min_ticker_ts ON raw_minute_prices (ticker, ts)")
    conn.commit()


def record(conn, ticker: str, bars: list[dict]) -> int:
    """Upsert 1-min bars ({ts:'YYYY-MM-DD HH:MM', open,high,low,close,volume})."""
    if not bars:
        return 0
    with conn.cursor() as cur:
        from psycopg2.extras import execute_values
        execute_values(cur,
            "INSERT INTO raw_minute_prices (ticker, ts, open, high, low, close, volume) VALUES %s "
            "ON CONFLICT (ticker, ts) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, "
            "low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume",
            [(ticker, b["ts"], b.get("open"), b.get("high"), b.get("low"),
              b.get("close"), b.get("volume")) for b in bars if b.get("close")])
        # keep ~10 trading days of minute history
        cur.execute("DELETE FROM raw_minute_prices WHERE ticker=%s AND ts < now() - interval '10 days'",
                    (ticker,))
    conn.commit()
    return len(bars)


def read_bars(db, ticker: str, day: str | None = None, limit: int = 400) -> list[dict]:
    """Minute bars for a chart — today's session (or `day`), oldest→newest."""
    from sqlalchemy import text
    q = ("SELECT ts, open, high, low, close, volume FROM raw_minute_prices WHERE ticker=:t "
         + ("AND ts::date = :d " if day else "AND ts::date = (SELECT max(ts::date) FROM raw_minute_prices WHERE ticker=:t) ")
         + "ORDER BY ts")
    p = {"t": ticker, **({"d": day} if day else {})}
    rows = db.execute(text(q), p).fetchall()
    return [dict(ts=str(r[0]), open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows][-limit:]


def intraday_vol(db, ticker: str) -> dict[str, Any]:
    """The day's VOLATILITY BASELINE from today's 1-min bars. Returns the numbers the
    day-trade feasibility answer needs — all real, computed from captured minutes."""
    bars = read_bars(db, ticker)
    if len(bars) < 5:
        return {"available": False, "bars": len(bars)}
    closes = [float(b["close"]) for b in bars if b["close"]]
    highs = [float(b["high"]) for b in bars if b["high"]]
    lows = [float(b["low"]) for b in bars if b["low"]]
    day_open = float(bars[0]["open"] or closes[0])
    day_high, day_low, last = max(highs), min(lows), closes[-1]
    # realized intraday range so far (% of open)
    realized_range_pct = round((day_high - day_low) / day_open * 100, 2) if day_open else None
    # 1-min return volatility → projected over a full 390-min session (% daily move estimate)
    rets = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1]]
    if rets:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        minute_sd = var ** 0.5
        expected_day_move_pct = round(minute_sd * (390 ** 0.5) * 100, 2)   # ~1σ full-day move
    else:
        expected_day_move_pct = None
    # opening-range (first 30 bars) as a quick volatility tell
    o = bars[:30]
    or_hi = max(float(b["high"]) for b in o if b["high"])
    or_lo = min(float(b["low"]) for b in o if b["low"])
    opening_range_pct = round((or_hi - or_lo) / day_open * 100, 2) if day_open else None
    return {
        "available": True, "bars": len(bars), "as_of": bars[-1]["ts"],
        "day_open": round(day_open), "day_high": round(day_high), "day_low": round(day_low),
        "last": round(last),
        "realized_range_pct": realized_range_pct,        # how much it's ranged today
        "expected_day_move_pct": expected_day_move_pct,  # 1σ full-day move from minute vol
        "opening_range_pct": opening_range_pct,
        "pos_in_range": round((last - day_low) / (day_high - day_low) * 100) if day_high > day_low else None,
    }
