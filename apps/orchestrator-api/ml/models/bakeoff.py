"""bakeoff.py — per-stock model comparison → pick best → model_registry.

For each ticker, trains many algorithms on stock_features_daily and evaluates on
a CHRONOLOGICAL hold-out (no shuffle — avoids look-ahead leakage), comparing
direction accuracy against naive baselines (majority-class, persistence). The
winner per (ticker, horizon) is saved to model_registry with its metrics + a
pickled model blob (used later by predict.py).

This proves, per stock, whether ML beats the coin-flip/baseline before we trust
it. Honest by design: every result is shown next to its baseline.

Usage:
    python ml/models/bakeoff.py --horizon 5
    python ml/models/bakeoff.py --horizon 5 --tickers 005930 000660 --verbose

Needs: scikit-learn, xgboost, lightgbm (ml/requirements.txt). Runs OFF Render.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/ on path
from _db import get_conn  # noqa: E402

# Mostly-stationary features (oscillators/ratios/returns). Raw level columns
# (close, sma, ema, obv, bb_upper/lower) are excluded — non-stationary.
FEATURES_X = [
    "ret_1d", "ret_5d", "ret_20d", "gap_open", "price_vs_sma20",
    "rsi_14", "stoch_k", "stoch_d", "roc_10", "bb_pct",
    "macd", "macd_signal", "atr_14", "realized_vol_20", "volume_z_20",
    # market/macro regime context (same across stocks per date) — gives each model
    # awareness of market-wide moves the per-stock technicals can't see.
    "mkt_kospi_ret5", "mkt_kospi_vs_sma20", "mkt_usdkrw_ret5", "mkt_breadth",
    # relative strength (stock vs KODEX200) — leadership/momentum signal that price-alone
    # technicals miss; complements the beta-adjusted (outperformance) target.
    "rel_ret_5d", "rel_ret_20d", "rel_vs_sma20",
    # 수급 / investor-flow features are collected (raw_investor_flows + flow_features.py)
    # but DISABLED from the live model: an isolated ablation on real-flow rows showed
    # daily 외국인/기관 net buying adds ~0 at 5d (Δacc -0.003, Δbuy +0.07%) and only a
    # weak edge at 1d (helped 22/33 stocks, +0.06%/trade — below the 0.3% cost). With
    # full-history imputation it slightly HURT the live model (17→15 beat b&h). We keep
    # collecting flows daily to build real history; revisit once we have program-trading/
    # 금투 + intraday flows (Kiwoom), which the YouTube method actually relies on. Re-enable:
    # "flow_for_net1", "flow_inst_net1", "flow_for_net5", "flow_inst_net5",
    # "flow_smart_net5", "flow_for_hold_chg20",
]


def _models():
    """name -> (estimator, needs_scaling)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.ensemble import (RandomForestClassifier, GradientBoostingClassifier,
                                  AdaBoostClassifier)
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.naive_bayes import GaussianNB
    from sklearn.svm import SVC
    from sklearn.neural_network import MLPClassifier

    bal = "balanced"
    RS = 42   # fixed seed -> daily retrains are REPRODUCIBLE (no winner wobble between runs)
    m = {
        "logreg": (LogisticRegression(max_iter=1000, C=0.5, class_weight=bal,
                                      random_state=RS), True),
        "decision_tree": (DecisionTreeClassifier(max_depth=5, min_samples_leaf=30,
                                                 class_weight=bal, random_state=RS), False),
        "random_forest": (RandomForestClassifier(n_estimators=300, max_depth=8,
                                                 min_samples_leaf=20, n_jobs=-1,
                                                 class_weight=bal, random_state=RS), False),
        "grad_boost": (GradientBoostingClassifier(n_estimators=200, max_depth=3,
                                                  random_state=RS), False),
        "adaboost": (AdaBoostClassifier(n_estimators=200, random_state=RS), False),
        "knn": (KNeighborsClassifier(n_neighbors=25), True),
        "gaussian_nb": (GaussianNB(), True),
        "svm_rbf": (SVC(C=1.0, kernel="rbf", class_weight=bal, random_state=RS), True),
        "mlp": (MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=400,
                              early_stopping=True, random_state=RS), True),
    }
    try:
        from xgboost import XGBClassifier
        m["xgboost"] = (XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05,
                                      subsample=0.8, colsample_bytree=0.8,
                                      eval_metric="mlogloss", n_jobs=-1,
                                      random_state=RS), False)
    except Exception:
        pass
    try:
        from lightgbm import LGBMClassifier
        m["lightgbm"] = (LGBMClassifier(n_estimators=400, max_depth=5, learning_rate=0.05,
                                        subsample=0.8, colsample_bytree=0.8,
                                        n_jobs=-1, verbose=-1, random_state=RS), False)
    except Exception:
        pass
    return m


def _pipe(est, scale: bool):
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    if scale:
        return Pipeline([("sc", StandardScaler()), ("m", est)])
    return est


def _metrics(y_true, y_pred) -> dict:
    from sklearn.metrics import accuracy_score, f1_score, recall_score
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "f1_macro": round(float(f1_score(y_true, y_pred, average="macro")), 4),
        "up_recall": round(float(recall_score(y_true, y_pred, labels=[1],
                                              average="macro", zero_division=0)), 4),
    }


def _load(conn, ticker: str, horizon: int) -> pd.DataFrame:
    # BETA-ADJUSTED target at 5d: predict OUTPERFORMANCE vs KODEX200 (label_excess_5d)
    # instead of absolute direction — removes beta so the model learns stock-picking skill.
    label = "label_excess_5d" if horizon == 5 else f"label_dir_{horizon}d"
    cols = ", ".join(FEATURES_X + [label])
    df = pd.read_sql(
        f"SELECT date, {cols} FROM stock_features_daily WHERE ticker=%s "
        f"AND {label} IS NOT NULL ORDER BY date", conn, params=(ticker,))
    return df.dropna(subset=FEATURES_X).rename(columns={label: "y"})


def bakeoff_ticker(conn, ticker: str, horizon: int, verbose: bool) -> dict | None:
    from collections import Counter
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.preprocessing import LabelEncoder

    df = _load(conn, ticker, horizon)
    if len(df) < 500:
        return None
    X = df[FEATURES_X].astype(float).values
    y_raw = df["y"].astype(int).values
    le = LabelEncoder(); y = le.fit_transform(y_raw)       # -1,0,1 -> 0,1,2 (xgboost-safe)
    up_cls = le.transform([1])[0] if 1 in le.classes_ else None
    rcol = FEATURES_X.index("ret_1d")

    # Walk-forward: 5 expanding folds — far more honest than one split.
    tss = TimeSeriesSplit(n_splits=5)
    models = _models()
    fold_acc = {nm: [] for nm in models}
    fold_f1 = {nm: [] for nm in models}
    fold_uprec = {nm: [] for nm in models}
    base_scores = []

    from sklearn.metrics import accuracy_score, f1_score, recall_score
    for tr, te in tss.split(X):
        Xtr, Xte, ytr, yte = X[tr], X[te], y[tr], y[te]
        maj = Counter(ytr).most_common(1)[0][0]
        b_maj = (yte == maj).mean()
        persist_raw = np.sign(Xte[:, rcol]).astype(int).clip(-1, 1)
        b_per = (y_raw[te] == persist_raw).mean()
        base_scores.append(max(b_maj, b_per))
        for nm, (est, scale) in models.items():
            try:
                model = _pipe(_clone(est), scale)
                model.fit(Xtr, ytr)
                pred = model.predict(Xte)
                fold_acc[nm].append(accuracy_score(yte, pred))
                fold_f1[nm].append(f1_score(yte, pred, average="macro"))
                if up_cls is not None:
                    fold_uprec[nm].append(recall_score(yte, pred, labels=[up_cls],
                                                       average="macro", zero_division=0))
            except Exception as e:
                if verbose:
                    print(f"    {nm} failed: {str(e)[:60]}")

    baseline = round(float(np.mean(base_scores)), 4)
    results = {}
    for nm in models:
        if fold_acc[nm]:
            results[nm] = {
                "accuracy": round(float(np.mean(fold_acc[nm])), 4),
                "f1_macro": round(float(np.mean(fold_f1[nm])), 4),
                "up_recall": round(float(np.mean(fold_uprec[nm])) if fold_uprec[nm] else 0.0, 4),
            }
    if not results:
        return None
    ranked = sorted(results.items(), key=lambda kv: kv[1]["accuracy"], reverse=True)
    best_name, best = ranked[0]
    edge = round(best["accuracy"] - baseline, 4)

    if verbose:
        print(f"  {ticker} (n={len(df)}) walk-fwd baseline={baseline:.3f}")
        for nm, r in ranked:
            print(f"    {nm:<14} acc={r['accuracy']:.3f} f1={r['f1_macro']:.3f} up_rec={r['up_recall']:.3f}")

    # Refit the winner on ALL data for the saved model (used by predict.py).
    best_est, best_scale = models[best_name]
    final = _pipe(_clone(best_est), best_scale)
    final.fit(X, y)
    return {
        "ticker": ticker, "horizon": horizon, "best": best_name,
        "metrics": best, "baseline": baseline, "edge": edge,
        "all": results, "_model": {"model": final, "le": le, "features": FEATURES_X},
        "n": len(df),
    }


def _clone(est):
    from sklearn.base import clone
    return clone(est)


def _save(conn, res: dict):
    import joblib
    buf = io.BytesIO(); joblib.dump(res["_model"], buf)
    import psycopg2
    metrics = {**res["metrics"], "baseline": res["baseline"], "edge": res["edge"],
               "n_train_test": res["n"]}
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO model_registry (ticker,horizon,model_name,metrics,feature_list,model_blob,is_active) "
            "VALUES (%s,%s,%s,%s,%s,%s,true) "
            "ON CONFLICT (ticker,horizon) DO UPDATE SET "
            "model_name=EXCLUDED.model_name, metrics=EXCLUDED.metrics, "
            "feature_list=EXCLUDED.feature_list, model_blob=EXCLUDED.model_blob, "
            "trained_at=now(), is_active=true",
            (res["ticker"], res["horizon"], res["best"], json.dumps(metrics),
             json.dumps(FEATURES_X), psycopg2.Binary(buf.getvalue())))
    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5, choices=[1, 5, 20])
    ap.add_argument("--tickers", nargs="+")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    conn = get_conn(); cur = conn.cursor()
    if args.tickers:
        tickers = args.tickers
    else:
        cur.execute("SELECT DISTINCT ticker FROM stock_features_daily ORDER BY ticker")
        tickers = [r[0] for r in cur.fetchall()]
    print(f"[bakeoff] horizon={args.horizon}d  {len(tickers)} tickers")

    summary = []
    for t in tickers:
        try:
            res = bakeoff_ticker(conn, t, args.horizon, args.verbose)
        except Exception as e:
            print(f"[bakeoff] {t} ERROR: {str(e)[:80]}"); continue
        if not res:
            continue
        if not args.no_save:
            _save(conn, res)
        summary.append(res)
        print(f"  {t}: best={res['best']:<13} acc={res['metrics']['accuracy']:.3f} "
              f"baseline={res['baseline']:.3f} edge={res['edge']:+.3f}")

    conn.close()
    if summary:
        beats = [r for r in summary if r["edge"] > 0]
        avg_acc = np.mean([r["metrics"]["accuracy"] for r in summary])
        avg_edge = np.mean([r["edge"] for r in summary])
        print(f"\n[bakeoff] {len(summary)} models | beat baseline: {len(beats)}/{len(summary)} "
              f"| avg acc={avg_acc:.3f} | avg edge={avg_edge:+.3f}")
        from collections import Counter
        print("[bakeoff] winning algos:", dict(Counter(r["best"] for r in summary)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
