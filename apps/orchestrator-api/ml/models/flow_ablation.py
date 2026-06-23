"""flow_ablation.py — does 수급/flow signal actually help, isolated from imputation?

The full-history bake-off imputes flow=0 for the ~10yr pre-Naver period, so 60%+ of
training rows are zero — which dilutes/masks any real flow signal. This script removes
that confound: for each ticker it trains on ONLY the rows where flows are real (the
recent Naver window), and compares the SAME models WITH vs WITHOUT the 6 flow features
on the SAME rows, walk-forward. That's a clean paired test of flow predictive value.

Reports, averaged across tickers:
  - direction accuracy  (with flows  vs  without)
  - BUY-pick 5d return  (with flows  vs  without)   <- the selector metric we care about

Usage: python ml/models/flow_ablation.py --horizon 5
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import get_conn                              # noqa: E402
from bakeoff import FEATURES_X, _models, _pipe, _clone  # noqa: E402

FLOW = ["flow_for_net1", "flow_inst_net1", "flow_for_net5", "flow_inst_net5",
        "flow_smart_net5", "flow_for_hold_chg20"]
BASE = [c for c in FEATURES_X if c not in FLOW]


def _eval(df: pd.DataFrame, feats: list[str], algo: str) -> tuple[float, float, int]:
    """Walk-forward on the given rows. Returns (dir_acc, buy_pick_5d_ret, n_buys)."""
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.preprocessing import LabelEncoder
    from sklearn.metrics import accuracy_score

    X = df[feats].astype(float).values
    y_raw = df["y"].astype(int).values
    fwd = df["fwd"].astype(float).values
    le = LabelEncoder(); y = le.fit_transform(y_raw)
    est, scale = _models().get(algo, _models()["random_forest"])

    accs, buy_rets, nbuy = [], [], 0
    for tr, te in TimeSeriesSplit(n_splits=4).split(X):
        m = _pipe(_clone(est), scale)
        m.fit(X[tr], y[tr])
        pred = m.predict(X[te])
        accs.append(accuracy_score(y[te], pred))
        pred_dir = le.inverse_transform(pred)
        up = pred_dir == 1
        if up.any():
            buy_rets.extend((fwd[te][up] * 100).tolist())
            nbuy += int(up.sum())
    return (float(np.mean(accs)) if accs else np.nan,
            float(np.mean(buy_rets)) if buy_rets else np.nan, nbuy)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--min-rows", type=int, default=220)
    args = ap.parse_args()
    label, fwdcol = f"label_dir_{args.horizon}d", f"fwd_ret_{args.horizon}d"

    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT ticker, model_name FROM model_registry WHERE horizon=%s AND is_active",
                (args.horizon,))
    pairs = cur.fetchall()
    cols = ", ".join(FEATURES_X + [label, fwdcol])

    print(f"=== FLOW ABLATION (horizon {args.horizon}d) — real-flow rows only ===\n")
    print(f"{'stock':<8}{'algo':<14}{'n':>5}{'acc_base':>10}{'acc_flow':>10}"
          f"{'buy_base':>10}{'buy_flow':>10}{'Δbuy':>8}")
    print("-" * 75)
    rows_acc_b, rows_acc_f, rows_buy_b, rows_buy_f = [], [], [], []
    for t, algo in pairs:
        df = pd.read_sql(
            f"SELECT {cols} FROM stock_features_daily WHERE ticker=%s AND {label} IS NOT NULL "
            f"AND flow_for_net1 <> 0 ORDER BY date", conn, params=(t,)).dropna(subset=FEATURES_X)
        if len(df) < args.min_rows:
            continue
        df = df.rename(columns={label: "y", fwdcol: "fwd"})
        ab, bb, _ = _eval(df, BASE, algo)
        af, bf, nb = _eval(df, FEATURES_X, algo)
        if np.isnan(ab) or np.isnan(af):
            continue
        rows_acc_b.append(ab); rows_acc_f.append(af)
        if not np.isnan(bb) and not np.isnan(bf):
            rows_buy_b.append(bb); rows_buy_f.append(bf)
        dbuy = (bf - bb) if (not np.isnan(bb) and not np.isnan(bf)) else float("nan")
        print(f"{t:<8}{algo:<14}{len(df):>5}{ab:>10.3f}{af:>10.3f}"
              f"{bb:>+9.2f}%{bf:>+9.2f}%{dbuy:>+7.2f}")
    conn.close()

    if rows_acc_b:
        print("-" * 75)
        print(f"AVG accuracy   base={np.mean(rows_acc_b):.3f}  flow={np.mean(rows_acc_f):.3f}  "
              f"Δ={np.mean(rows_acc_f)-np.mean(rows_acc_b):+.3f}")
        print(f"AVG BUY 5d ret base={np.mean(rows_buy_b):+.2f}%  flow={np.mean(rows_buy_f):+.2f}%  "
              f"Δ={np.mean(rows_buy_f)-np.mean(rows_buy_b):+.2f}%   (n={len(rows_buy_b)} tickers)")
        wins = sum(1 for a, b in zip(rows_buy_f, rows_buy_b) if a > b)
        print(f"flow improved BUY-pick return on {wins}/{len(rows_buy_b)} stocks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
