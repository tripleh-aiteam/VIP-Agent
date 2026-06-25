"""backtest.py — economic backtest: do the model's UP (BUY) signals actually earn
more than just holding? Walk-forward, out-of-sample, per stock.

For each test fold we train the stock's chosen algorithm, predict, and compare the
average forward 5-day return ON DAYS THE MODEL SAID 'UP' vs the average forward
return across all days (buy-and-hold proxy). Edge = strategy − buy&hold; it must
clear round-trip costs (~0.3%) to be real.

Usage: python ml/models/backtest.py --horizon 5
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))      # ml/models
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/
from bakeoff import FEATURES_X, _models, _pipe, _clone  # noqa: E402
from _db import get_conn                                # noqa: E402

COST = 0.003   # ~0.3% round-trip (fees+slippage) assumed per trade


def backtest_ticker(conn, ticker: str, algo: str, horizon: int = 5) -> dict | None:
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.preprocessing import LabelEncoder
    # Train the signal on OUTPERFORMANCE (label_excess_5d) but measure profit on the
    # ABSOLUTE forward return (fwd_ret) — select outperformers, gate on real P&L after cost.
    label = "label_excess_5d" if horizon == 5 else f"label_dir_{horizon}d"
    fwd = f"fwd_ret_{horizon}d"
    cols = ", ".join(FEATURES_X + [label, fwd])
    df = pd.read_sql(
        f"SELECT {cols} FROM stock_features_daily WHERE ticker=%s AND {label} IS NOT NULL "
        f"AND {fwd} IS NOT NULL ORDER BY date", conn, params=(ticker,)).dropna(subset=FEATURES_X)
    if len(df) < 500:
        return None
    X = df[FEATURES_X].astype(float).values
    y = df[label].astype(int).values
    fr = df[fwd].astype(float).values
    le = LabelEncoder(); ye = le.fit_transform(y)
    models = _models()
    if algo not in models:
        algo = "xgboost" if "xgboost" in models else "random_forest"
    est, scale = models[algo]

    strat, bh = [], []
    for tr, te in TimeSeriesSplit(n_splits=5).split(X):
        m = _pipe(_clone(est), scale); m.fit(X[tr], ye[tr])
        pred = le.inverse_transform(m.predict(X[te]))
        for p, r in zip(pred, fr[te]):
            bh.append(r)
            if p == 1:                      # model says UP -> we'd BUY
                strat.append(r - COST)      # net of cost
    if not strat:
        return {"ticker": ticker, "trades": 0}
    return {
        "ticker": ticker, "algo": algo, "trades": len(strat),
        "strat_ret": round(float(np.mean(strat)) * 100, 3),   # avg net 5d return per BUY, %
        "bh_ret": round(float(np.mean(bh)) * 100, 3),         # avg 5d return all days, %
        "edge": round((float(np.mean(strat)) - float(np.mean(bh))) * 100, 3),
        "win_rate": round(float(np.mean([1 if s > 0 else 0 for s in strat])) * 100, 1),
    }


def _save_econ(conn, ticker: str, horizon: int, r: dict):
    """Merge economic-backtest results into model_registry.metrics."""
    import json
    with conn.cursor() as cur:
        cur.execute("SELECT metrics FROM model_registry WHERE ticker=%s AND horizon=%s",
                    (ticker, horizon))
        row = cur.fetchone()
        if not row:
            return
        m = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        m["econ_edge"] = r["edge"]            # net % vs buy&hold per 5d trade
        m["econ_strat_ret"] = r["strat_ret"]
        m["econ_win_rate"] = r["win_rate"]
        cur.execute("UPDATE model_registry SET metrics=%s WHERE ticker=%s AND horizon=%s",
                    (json.dumps(m), ticker, horizon))
    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--save", action="store_true", help="write econ_edge into model_registry")
    args = ap.parse_args()
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT ticker, model_name FROM model_registry WHERE horizon=%s AND is_active",
                (args.horizon,))
    pairs = cur.fetchall()

    rows = []
    for t, algo in pairs:
        try:
            r = backtest_ticker(conn, t, algo, args.horizon)
        except Exception as e:
            print(f"  {t} ERR {str(e)[:60]}"); continue
        if r and r.get("trades"):
            rows.append(r)
            if args.save:
                _save_econ(conn, t, args.horizon, r)
    conn.close()

    rows.sort(key=lambda r: -r["edge"])
    print(f"\n=== ECONOMIC BACKTEST (horizon {args.horizon}d, net of {COST*100:.1f}% cost) ===")
    print(f"{'stock':<9}{'algo':<14}{'trades':>7}{'strat%':>8}{'b&h%':>8}{'edge%':>8}{'win%':>7}")
    print("-" * 62)
    for r in rows:
        print(f"{r['ticker']:<9}{r['algo']:<14}{r['trades']:>7}{r['strat_ret']:>8}"
              f"{r['bh_ret']:>8}{r['edge']:>+8}{r['win_rate']:>7}")
    if rows:
        prof = [r for r in rows if r["edge"] > 0]
        print(f"\nbeat buy&hold (after cost): {len(prof)}/{len(rows)} | "
              f"avg edge {np.mean([r['edge'] for r in rows]):+.3f}% per 5d trade")
    return 0


if __name__ == "__main__":
    sys.exit(main())
