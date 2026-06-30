"""wave_backtest.py — does METHOD 3 ("Wave") actually have an edge?

Replays history: at each date, for each stock, compute the wave verdict (reusing
wave_features.compute_wave point-in-time). On a BUY (strong wave + deep-pullback Fib
zone), simulate the trade forward — does price hit TARGET (prior swing-high) before
STOP (below swing-low) within HOLD bars? Record win/return.

Reports win-rate, average return per trade, expectancy, and compares against the
BASELINE (the average HOLD-bar forward return of all stocks = market drift). If the
strategy doesn't clearly beat the baseline, we say so — no fake +1,900%.

Usage:  python ml/models/wave_backtest.py            # all tickers, since 2018
        python ml/models/wave_backtest.py --since 2015-01-01 --hold 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/ on path
from _db import get_conn  # noqa: E402
from features.wave_features import compute_wave, MARKET_PROXY, MIN_AMPLITUDE  # noqa: E402

# mirror wave_strategy thresholds
MIN_SCORE = 0.55
ENTRY_LO, ENTRY_HI = 0.50, 0.80
MIN_RR = 1.5


def _load(conn, ticker, since):
    df = pd.read_sql(
        "SELECT date, open, high, low, close, volume FROM raw_daily_prices "
        f"WHERE ticker='{ticker}' AND date >= '{since}' ORDER BY date", conn)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").astype(float)


def backtest(since="2018-01-01", hold=20, step=1) -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM raw_daily_prices")
    tickers = [r[0] for r in cur.fetchall() if r[0] != MARKET_PROXY]
    mkt = _load(conn, MARKET_PROXY, since)
    mkt_ret = mkt["close"].pct_change() if not mkt.empty else None

    trades, base_rets = [], []
    for tk in tickers:
        bars = _load(conn, tk, since)
        if len(bars) < 60 + hold:
            continue
        high = bars["high"].to_numpy(float); low = bars["low"].to_numpy(float)
        close = bars["close"].to_numpy(float)
        i = 50
        while i < len(bars) - hold:
            # baseline sample: forward HOLD-bar return of every step (market drift)
            base_rets.append(close[i + hold] / close[i] - 1.0)
            w = compute_wave(bars.iloc[:i + 1], mkt_ret)
            if not w:
                i += step; continue
            lo, hi = w["swing_low"], w["swing_high"]
            rng = (hi - lo) or 1.0
            px = close[i]
            retrace = (hi - px) / rng
            entry = w["fib_618"]; stop = round(lo * 0.98); target = round(hi)
            rr = (target - entry) / (entry - stop) if entry > stop else 0
            buy = (w["wave_score"] >= MIN_SCORE and ENTRY_LO <= retrace <= ENTRY_HI
                   and rr >= MIN_RR and px > stop)
            if not buy:
                i += step; continue
            # simulate forward: hit target or stop first within `hold` bars?
            ret, outcome = None, "open"
            for j in range(i + 1, min(i + 1 + hold, len(bars))):
                if low[j] <= stop:
                    ret = stop / px - 1.0; outcome = "stop"; break
                if high[j] >= target:
                    ret = target / px - 1.0; outcome = "target"; break
            if ret is None:                       # neither hit → exit at HOLD close
                ret = close[min(i + hold, len(bars) - 1)] / px - 1.0; outcome = "timeout"
            trades.append({"ticker": tk, "ret": ret, "outcome": outcome, "rr": rr})
            i += hold                              # no overlapping trades
    conn.close()

    if not trades:
        return {"trades": 0}
    rets = np.array([t["ret"] for t in trades])
    base = np.array(base_rets)
    wins = rets > 0
    return {
        "trades": len(trades),
        "win_rate": round(float(wins.mean()), 3),
        "avg_return_per_trade": round(float(rets.mean()), 4),
        "median_return": round(float(np.median(rets)), 4),
        "avg_win": round(float(rets[wins].mean()), 4) if wins.any() else 0,
        "avg_loss": round(float(rets[~wins].mean()), 4) if (~wins).any() else 0,
        "expectancy": round(float(rets.mean()), 4),
        "target_hit_rate": round(float(np.mean([t["outcome"] == "target" for t in trades])), 3),
        "stop_hit_rate": round(float(np.mean([t["outcome"] == "stop" for t in trades])), 3),
        "BASELINE_avg_fwd_return": round(float(base.mean()), 4),
        "EDGE_vs_baseline": round(float(rets.mean() - base.mean()), 4),
        "hold_bars": hold, "since": since,
    }


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2018-01-01")
    ap.add_argument("--hold", type=int, default=20)
    args = ap.parse_args()
    import json
    print("[wave_backtest]", json.dumps(backtest(since=args.since, hold=args.hold), indent=2))
