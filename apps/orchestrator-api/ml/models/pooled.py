"""pooled.py — ONE cross-sectional model trained on ALL stocks at once (binary UP/DOWN).

Per-stock models each saw only ~2,500 rows (small -> noisy). A pooled model trains on
ALL ~94k rows together, so it has far more data and learns patterns that generalize
across stocks. Target is BINARY (fwd_ret_5d > 0 = UP) which is easier than 3-class.

Walk-forward BY DATE (train on all stocks before T, test after) — no leakage.
Reports accuracy vs majority baseline + economic edge (avg 5d return when it says UP).

Usage: python ml/models/pooled.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bakeoff import FEATURES_X  # noqa: E402
from _db import get_conn        # noqa: E402

COST = 0.003


def _models():
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import RandomForestClassifier
    m = {
        "logreg": LogisticRegression(max_iter=1000, C=0.5, class_weight="balanced"),
        "random_forest": RandomForestClassifier(n_estimators=300, max_depth=10,
                                                 min_samples_leaf=50, n_jobs=-1,
                                                 class_weight="balanced"),
    }
    try:
        from xgboost import XGBClassifier
        m["xgboost"] = XGBClassifier(n_estimators=400, max_depth=5, learning_rate=0.05,
                                     subsample=0.8, colsample_bytree=0.8, n_jobs=-1,
                                     eval_metric="logloss")
    except Exception:
        pass
    try:
        from lightgbm import LGBMClassifier
        m["lightgbm"] = LGBMClassifier(n_estimators=500, max_depth=6, learning_rate=0.05,
                                       subsample=0.8, colsample_bytree=0.8, n_jobs=-1, verbose=-1)
    except Exception:
        pass
    return m


def main() -> int:
    conn = get_conn()
    cols = ", ".join(FEATURES_X)
    df = pd.read_sql(
        f"SELECT ticker, date, {cols}, fwd_ret_5d FROM stock_features_daily "
        f"WHERE fwd_ret_5d IS NOT NULL ORDER BY date", conn).dropna(subset=FEATURES_X)
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    df["y"] = (df["fwd_ret_5d"] > 0).astype(int)
    print(f"[pooled] {len(df):,} rows across {df.ticker.nunique()} stocks, "
          f"UP rate {df.y.mean()*100:.1f}%")

    dates = np.array(sorted(df["date"].unique()))
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    results = {}
    for name, est in _models().items():
        accs, base, strat, bh = [], [], [], []
        for tr_idx, te_idx in TimeSeriesSplit(n_splits=5).split(dates):
            tr_dates, te_dates = set(dates[tr_idx]), set(dates[te_idx])
            tr = df[df["date"].isin(tr_dates)]; te = df[df["date"].isin(te_dates)]
            Xtr, ytr = tr[FEATURES_X].values, tr["y"].values
            Xte, yte = te[FEATURES_X].values, te["y"].values
            scale = name in ("logreg",)
            model = Pipeline([("sc", StandardScaler()), ("m", est)]) if scale else est
            model.fit(Xtr, ytr)
            pred = model.predict(Xte)
            accs.append((pred == yte).mean())
            base.append(max(yte.mean(), 1 - yte.mean()))   # majority baseline
            fr = te["fwd_ret_5d"].values
            up = pred == 1
            if up.sum():
                strat.append(np.mean(fr[up]) - COST)
            bh.append(np.mean(fr))
        results[name] = {
            "acc": float(np.mean(accs)), "base": float(np.mean(base)),
            "edge": float(np.mean(accs) - np.mean(base)),
            "strat_ret": float(np.mean(strat)) * 100 if strat else 0,
            "bh_ret": float(np.mean(bh)) * 100,
            "econ_edge": (float(np.mean(strat)) - float(np.mean(bh))) * 100 if strat else 0,
        }

    print(f"\n=== POOLED MODEL (binary UP/DOWN, walk-forward by date, net {COST*100:.1f}% cost) ===")
    print(f"{'model':<16}{'acc':>7}{'base':>7}{'acc_edge':>10}{'strat%':>8}{'b&h%':>8}{'econ_edge%':>11}")
    print("-" * 67)
    for name, r in sorted(results.items(), key=lambda kv: -kv[1]["acc"]):
        print(f"{name:<16}{r['acc']:>7.3f}{r['base']:>7.3f}{r['edge']:>+10.3f}"
              f"{r['strat_ret']:>8.3f}{r['bh_ret']:>8.3f}{r['econ_edge']:>+11.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
