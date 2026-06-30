"""wave_features.py — raw_daily_prices → wave_features_daily  (METHOD 3: "Wave").

The objective, rule-based core of the Wave/Fibonacci strategy (Isaac-deck style),
with NO subjective Elliott wave-counting:

  1. Find the most recent meaningful RALLY (swing-low → swing-high) in a lookback window.
  2. Score its strength on 6 dimensions (amplitude, duration, velocity, consistency,
     relative-strength vs KOSPI200, volume surge) → a 0..1 composite wave_score.
  3. Measure the CURRENT pullback from the swing-high, and compute dynamic Fibonacci
     retracement entry levels (38.2 / 50 / 61.8 / 75%).

Point-in-time: every value uses only bars up to `as_of` (the swing-high must be
confirmed by a later local peak inside the window). Output one row per (ticker, date).
Reused by wave_strategy.py (live verdict) and wave_backtest.py (replay).

Usage:  python ml/features/wave_features.py            # latest date, all tickers
        python ml/features/wave_features.py --all-dates  # backfill history (slow)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/ on path
from _db import get_conn, upsert  # noqa: E402

MARKET_PROXY = "069500"     # KODEX 200 — relative-strength benchmark
LOOKBACK = 120              # bars to search for the most recent rally
PIVOT = 5                   # a swing-high/low is a local extreme over ±PIVOT bars
MIN_AMPLITUDE = 0.05        # ignore rallies smaller than 5% (noise)
FIB = [0.382, 0.5, 0.618, 0.75]

OUT_COLS = ["ticker", "date", "wave_amplitude", "wave_duration", "wave_velocity",
            "wave_consistency", "wave_rs", "wave_volume", "wave_score",
            "swing_low", "swing_high", "pullback_pct",
            "fib_382", "fib_500", "fib_618", "fib_750"]


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def compute_wave(bars: pd.DataFrame, mkt_ret: pd.Series | None) -> dict | None:
    """bars: OHLCV up to and including `as_of` (ascending date), index = date.
    mkt_ret: market daily returns aligned by date (for relative strength). Returns the
    wave feature dict for the LAST row, or None if no qualifying rally is found."""
    if len(bars) < PIVOT * 2 + 5:
        return None
    win = bars.iloc[-LOOKBACK:]
    high = win["high"].to_numpy(dtype=float)
    low = win["low"].to_numpy(dtype=float)
    close = win["close"].to_numpy(dtype=float)
    vol = win["volume"].to_numpy(dtype=float)
    n = len(win)

    # swing-high = highest high whose peak is CONFIRMED (>= PIVOT bars before the end,
    # i.e. it's a local max over ±PIVOT). Take the most recent such confirmed peak.
    sh_idx = None
    for i in range(n - 1 - PIVOT, PIVOT - 1, -1):
        lo, hi = max(0, i - PIVOT), min(n, i + PIVOT + 1)
        if high[i] >= high[lo:hi].max():
            sh_idx = i
            break
    if sh_idx is None or sh_idx == 0:
        return None
    # swing-low = lowest low BEFORE the peak (the rally start)
    sl_idx = int(np.argmin(low[:sh_idx + 1]))
    if sl_idx >= sh_idx:
        return None

    swing_low, swing_high = float(low[sl_idx]), float(high[sh_idx])
    amplitude = (swing_high - swing_low) / swing_low if swing_low > 0 else 0.0
    if amplitude < MIN_AMPLITUDE:
        return None
    duration = sh_idx - sl_idx                                   # bars in the rally
    velocity = amplitude / duration if duration else 0.0         # % per bar

    rally_close = close[sl_idx:sh_idx + 1]
    up_days = np.mean(np.diff(rally_close) > 0) if len(rally_close) > 1 else 0.0  # consistency
    rally_vol = vol[sl_idx:sh_idx + 1].mean()
    base_vol = vol[max(0, sl_idx - 20):sl_idx].mean() if sl_idx >= 5 else vol[:sl_idx + 1].mean()
    vol_mult = (rally_vol / base_vol) if base_vol and base_vol > 0 else 1.0

    # relative strength: stock rally return minus market return over the same window
    rs = amplitude
    if mkt_ret is not None:
        dates = win.index[sl_idx:sh_idx + 1]
        mkt_window = mkt_ret.reindex(dates).dropna()
        if len(mkt_window) > 1:
            mkt_cum = float((1 + mkt_window).prod() - 1)
            rs = amplitude - mkt_cum

    cur_close = float(close[-1])
    pullback_pct = (swing_high - cur_close) / swing_high if swing_high > 0 else 0.0

    # 6-D → 0..1 each (simple, monotonic scalings), composite = mean
    d_amp = _clip01(amplitude / 0.5)            # 50% rally → 1.0
    d_dur = _clip01(duration / 40.0)            # ~2 months → 1.0
    d_vel = _clip01(velocity / 0.03)            # 3%/bar → 1.0
    d_con = _clip01(up_days)                    # fraction of up-days
    d_rs = _clip01((rs + 0.1) / 0.4)            # outperform up to +30%
    d_vol = _clip01((vol_mult - 1) / 1.5)       # 2.5x volume → 1.0
    wave_score = float(np.mean([d_amp, d_dur, d_vel, d_con, d_rs, d_vol]))

    rng = swing_high - swing_low
    fib = {f"fib_{int(r*1000)}": round(swing_high - r * rng) for r in FIB}

    return {
        "wave_amplitude": round(amplitude, 4), "wave_duration": int(duration),
        "wave_velocity": round(velocity, 5), "wave_consistency": round(float(up_days), 3),
        "wave_rs": round(float(rs), 4), "wave_volume": round(float(vol_mult), 3),
        "wave_score": round(wave_score, 4),
        "swing_low": round(swing_low), "swing_high": round(swing_high),
        "pullback_pct": round(float(pullback_pct), 4),
        "fib_382": fib["fib_382"], "fib_500": fib["fib_500"],
        "fib_618": fib["fib_618"], "fib_750": fib["fib_750"],
    }


DDL = """
CREATE TABLE IF NOT EXISTS wave_features_daily (
    ticker text, date date,
    wave_amplitude double precision, wave_duration integer, wave_velocity double precision,
    wave_consistency double precision, wave_rs double precision, wave_volume double precision,
    wave_score double precision,
    swing_low double precision, swing_high double precision, pullback_pct double precision,
    fib_382 double precision, fib_500 double precision, fib_618 double precision, fib_750 double precision,
    PRIMARY KEY (ticker, date)
);
"""


def _load(conn, ticker: str) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT date, open, high, low, close, volume FROM raw_daily_prices "
        f"WHERE ticker='{ticker}' ORDER BY date", conn)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").astype({c: float for c in ["open", "high", "low", "close", "volume"]})


def build(all_dates: bool = False) -> dict:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute("SELECT DISTINCT ticker FROM raw_daily_prices")
            tickers = [r[0] for r in cur.fetchall()]
        mkt = _load(conn, MARKET_PROXY)
        mkt_ret = mkt["close"].pct_change() if not mkt.empty else None

        rows, built = [], 0
        for tk in tickers:
            bars = _load(conn, tk)
            if bars.empty:
                continue
            # which as_of dates to compute: just the latest, or every date (backfill)
            idxs = range(PIVOT * 2 + 5, len(bars)) if all_dates else [len(bars)]
            for end in idxs:
                sub = bars.iloc[:end]
                w = compute_wave(sub, mkt_ret)
                if w:
                    rows.append({"ticker": tk, "date": sub.index[-1].date(), **w})
                    built += 1
        if rows:
            df = pd.DataFrame(rows)[OUT_COLS]
            upsert(conn, "wave_features_daily", OUT_COLS,
                   list(df.itertuples(index=False, name=None)),
                   conflict_cols=["ticker", "date"])
        return {"rows": built, "tickers": len(tickers)}
    finally:
        conn.close()


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-dates", action="store_true", help="backfill every date (slow)")
    args = ap.parse_args()
    print("[wave_features]", build(all_dates=args.all_dates))
