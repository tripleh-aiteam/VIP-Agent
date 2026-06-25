"""build_features.py — raw_daily_prices → stock_features_daily (the gold table).

Computes technical indicators + forward-looking labels per ticker and upserts one
row per (ticker, date). Indicators are computed manually with pandas/numpy (no
pandas-ta, which breaks on numpy>=2). Sentiment + fundamental columns are left
NULL here and filled by later steps (news/DART join); price features alone are
enough to start the bake-off (papers 1 & 4 used technical-only).

Point-in-time: every feature uses only data up to `date`; labels look FORWARD.

Usage:
    python ml/features/build_features.py                 # all tickers in raw_daily_prices
    python ml/features/build_features.py --tickers 005930 000660
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/ on path
from _db import get_conn, upsert  # noqa: E402

# flat/up/down deadband per horizon (|fwd_ret| below -> FLAT(0))
FLAT_BAND = {1: 0.005, 5: 0.015, 20: 0.03}
# deadband for the MARKET-RELATIVE (excess vs KODEX200) 5-day label — smaller, since
# excess returns are tighter. |excess| < 0.5% -> FLAT (in-line with the market).
EXCESS_BAND = 0.005
MARKET_PROXY = "069500"          # KODEX 200 ETF — tracks KOSPI200, in our universe

FEATURE_COLS = [
    "ticker", "date", "close", "volume",
    "ret_1d", "ret_5d", "ret_20d", "gap_open",
    "sma_5", "sma_20", "sma_60", "ema_12", "ema_26", "macd", "macd_signal",
    "price_vs_sma20",
    "rsi_14", "stoch_k", "stoch_d", "roc_10",
    "bb_upper", "bb_lower", "bb_pct", "atr_14", "realized_vol_20",
    "volume_z_20", "obv",
    "fwd_ret_1d", "fwd_ret_5d", "fwd_ret_20d",
    "label_dir_1d", "label_dir_5d", "label_dir_20d",
    # market-relative (beta-adjusted) 5-day: stock fwd return MINUS KODEX200 fwd return.
    # label_excess_5d = +1 outperform / 0 in-line / -1 underperform.
    "fwd_excess_5d", "label_excess_5d",
]


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _dir(fwd: pd.Series, band: float) -> pd.Series:
    out = pd.Series(0, index=fwd.index, dtype="float")
    out[fwd > band] = 1
    out[fwd < -band] = -1
    out[fwd.isna()] = np.nan
    return out


def compute(df: pd.DataFrame, mkt_fwd5: pd.Series | None = None) -> pd.DataFrame:
    """df: columns open,high,low,close,volume indexed/sorted by date ascending."""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["volume"] = v

    # returns
    out["ret_1d"] = c.pct_change(1)
    out["ret_5d"] = c.pct_change(5)
    out["ret_20d"] = c.pct_change(20)
    out["gap_open"] = df["open"] / c.shift(1) - 1

    # trend
    out["sma_5"] = c.rolling(5).mean()
    out["sma_20"] = c.rolling(20).mean()
    out["sma_60"] = c.rolling(60).mean()
    out["ema_12"] = _ema(c, 12)
    out["ema_26"] = _ema(c, 26)
    out["macd"] = out["ema_12"] - out["ema_26"]
    out["macd_signal"] = _ema(out["macd"], 9)
    out["price_vs_sma20"] = c / out["sma_20"] - 1

    # momentum
    out["rsi_14"] = _rsi(c, 14)
    ll = l.rolling(14).min(); hh = h.rolling(14).max()
    out["stoch_k"] = 100 * (c - ll) / (hh - ll).replace(0, np.nan)
    out["stoch_d"] = out["stoch_k"].rolling(3).mean()
    out["roc_10"] = c.pct_change(10) * 100

    # volatility
    mid = out["sma_20"]; sd = c.rolling(20).std()
    out["bb_upper"] = mid + 2 * sd
    out["bb_lower"] = mid - 2 * sd
    out["bb_pct"] = (c - out["bb_lower"]) / (out["bb_upper"] - out["bb_lower"]).replace(0, np.nan)
    out["atr_14"] = _atr(df, 14)
    logret = np.log(c / c.shift(1))
    out["realized_vol_20"] = logret.rolling(20).std() * np.sqrt(252) * 100

    # volume
    vmean = v.rolling(20).mean(); vstd = v.rolling(20).std()
    out["volume_z_20"] = (v - vmean) / vstd.replace(0, np.nan)
    out["obv"] = (np.sign(c.diff()).fillna(0) * v).cumsum()

    # labels (forward)
    for hh_ in (1, 5, 20):
        fwd = c.shift(-hh_) / c - 1
        out[f"fwd_ret_{hh_}d"] = fwd
        out[f"label_dir_{hh_}d"] = _dir(fwd, FLAT_BAND[hh_])

    # market-relative (beta-adjusted) 5-day label: stock 5d forward return MINUS the
    # market's (KODEX200) 5d forward return over the SAME window. Predicting THIS instead
    # of absolute direction removes beta -> the model learns which stocks OUTPERFORM.
    if mkt_fwd5 is not None:
        mkt = mkt_fwd5.reindex(out.index)
        excess = out["fwd_ret_5d"] - mkt
        out["fwd_excess_5d"] = excess
        out["label_excess_5d"] = _dir(excess, EXCESS_BAND)
    else:
        out["fwd_excess_5d"] = np.nan
        out["label_excess_5d"] = np.nan

    return out.replace([np.inf, -np.inf], np.nan)


def _to_rows(ticker: str, feat: pd.DataFrame) -> list[tuple]:
    rows = []
    for d, r in feat.iterrows():
        rec = {"ticker": ticker, "date": d}
        for col in FEATURE_COLS[2:]:
            val = r.get(col)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                rec[col] = None
            elif col.startswith("label_dir") or col == "label_excess_5d":
                rec[col] = int(val)
            elif col in ("volume", "obv"):
                rec[col] = int(val)
            else:
                rec[col] = float(val)
        rows.append(tuple(rec[c] for c in FEATURE_COLS))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+")
    ap.add_argument("--min-rows", type=int, default=80, help="skip tickers with too little history")
    args = ap.parse_args()

    conn = get_conn()
    cur = conn.cursor()
    # ensure the beta-adjusted columns exist (idempotent)
    cur.execute("ALTER TABLE stock_features_daily ADD COLUMN IF NOT EXISTS fwd_excess_5d NUMERIC")
    cur.execute("ALTER TABLE stock_features_daily ADD COLUMN IF NOT EXISTS label_excess_5d SMALLINT")
    conn.commit()
    # market (KODEX200) 5-day FORWARD return by date — the benchmark each stock is judged
    # against for the excess/relative label.
    cur.execute("SELECT date, close FROM raw_daily_prices WHERE ticker=%s ORDER BY date", (MARKET_PROXY,))
    mrecs = cur.fetchall()
    mkt_fwd5 = None
    if len(mrecs) > 10:
        ms = pd.DataFrame(mrecs, columns=["date", "close"]).set_index("date")["close"].astype(float)
        mkt_fwd5 = ms.shift(-5) / ms - 1                     # forward 5d market return
    if args.tickers:
        tickers = args.tickers
    else:
        cur.execute("SELECT DISTINCT ticker FROM raw_daily_prices ORDER BY ticker")
        tickers = [r[0] for r in cur.fetchall()]
    print(f"[features] {len(tickers)} tickers -> stock_features_daily (market-relative label: "
          f"{'on' if mkt_fwd5 is not None else 'OFF — no KODEX200 data'})")

    ok = skip = total = 0
    for i, t in enumerate(tickers, 1):
        cur.execute(
            "SELECT date, open, high, low, close, volume FROM raw_daily_prices "
            "WHERE ticker=%s ORDER BY date", (t,))
        recs = cur.fetchall()
        if len(recs) < args.min_rows:
            skip += 1
            continue
        df = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume"])
        df = df.set_index("date").astype(float)
        feat = compute(df, mkt_fwd5=mkt_fwd5)
        # keep only rows with the core features present (warm-up drops first ~60)
        feat = feat[feat["sma_60"].notna()]
        rows = _to_rows(t, feat)
        n = upsert(conn, "stock_features_daily", FEATURE_COLS, rows,
                   conflict_cols=["ticker", "date"])
        total += n; ok += 1
        if i % 10 == 0:
            print(f"[features] {i}/{len(tickers)} ok={ok} skip={skip} rows={total}")
    conn.close()
    print(f"[features] DONE: ok={ok} skip={skip} total_rows={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
