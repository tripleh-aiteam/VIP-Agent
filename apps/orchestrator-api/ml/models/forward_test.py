"""forward_test.py — the intuitive 'hide recent data, predict, check reality' test.

Pick a CUTOFF date in the recent past. For each stock we train ONLY on data strictly
before the cutoff (no leakage), predict the 5-day direction AS OF the cutoff, then
compare to what ACTUALLY happened (the real fwd_ret_5d we already have). This is a
true out-of-sample forward test on held-out days.

Usage:
    python ml/models/forward_test.py                 # auto: most recent testable day
    python ml/models/forward_test.py --cutoff 2026-06-13
    python ml/models/forward_test.py --days-back 10
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bakeoff import FEATURES_X, _models, _pipe, _clone  # noqa: E402
from _db import get_conn                                # noqa: E402
from predict import NAMES                               # noqa: E402

FLAT = 0.015  # 5-day flat band (matches build_features label)


def test_ticker(conn, ticker: str, algo: str, cutoff, horizon: int = 5) -> dict | None:
    from sklearn.preprocessing import LabelEncoder
    label, fwd = f"label_dir_{horizon}d", f"fwd_ret_{horizon}d"
    cols = ", ".join(FEATURES_X + [label, fwd])
    df = pd.read_sql(
        f"SELECT date, {cols} FROM stock_features_daily WHERE ticker=%s AND {label} IS NOT NULL "
        f"ORDER BY date", conn, params=(ticker,)).dropna(subset=FEATURES_X)
    if len(df) < 400:
        return None
    df["date"] = pd.to_datetime(df["date"])
    cutoff = pd.to_datetime(cutoff)

    train = df[df["date"] < cutoff]
    # the test row = last available day on/before cutoff that has a known outcome
    cand = df[(df["date"] <= cutoff) & (df[fwd].notna())]
    if len(train) < 300 or cand.empty:
        return None
    row = cand.iloc[-1]

    Xtr = train[FEATURES_X].astype(float).values
    ytr = train[label].astype(int).values
    le = LabelEncoder(); ytr_e = le.fit_transform(ytr)
    est, scale = _models().get(algo, _models()["random_forest"])
    model = _pipe(_clone(est), scale)
    model.fit(Xtr, ytr_e)

    xrow = row[FEATURES_X].astype(float).values.reshape(1, -1)
    pred_dir = int(le.inverse_transform(model.predict(xrow))[0])
    actual_ret = float(row[fwd]) * 100
    actual_dir = 1 if actual_ret / 100 > FLAT else (-1 if actual_ret / 100 < -FLAT else 0)

    return {
        "ticker": ticker, "name": NAMES.get(ticker, ticker),
        "as_of": row["date"].date().isoformat(),
        "pred_dir": pred_dir, "actual_ret": round(actual_ret, 2), "actual_dir": actual_dir,
        "dir_hit": pred_dir == actual_dir,
        "up_call_correct": (pred_dir == 1 and actual_ret > 0),
        "pred_up": pred_dir == 1,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default=None)
    ap.add_argument("--days-back", type=int, default=None)
    ap.add_argument("--horizon", type=int, default=5)
    args = ap.parse_args()

    conn = get_conn(); cur = conn.cursor()
    if args.cutoff:
        cutoff = args.cutoff
    else:
        cur.execute(f"SELECT max(date) FROM stock_features_daily WHERE fwd_ret_{args.horizon}d IS NOT NULL")
        maxd = cur.fetchone()[0]
        cutoff = str(maxd)
        if args.days_back:
            cur.execute("SELECT DISTINCT date FROM stock_features_daily WHERE date<=%s ORDER BY date DESC LIMIT %s",
                        (maxd, args.days_back + 1))
            ds = [r[0] for r in cur.fetchall()]
            cutoff = str(ds[-1]) if ds else cutoff

    cur.execute("SELECT ticker, model_name FROM model_registry WHERE horizon=%s AND is_active",
                (args.horizon,))
    pairs = cur.fetchall()
    print(f"\n=== FORWARD TEST — trained on data BEFORE {cutoff}, checked vs reality ({args.horizon}d) ===\n")

    rows = []
    for t, algo in pairs:
        try:
            r = test_ticker(conn, t, algo, cutoff, args.horizon)
        except Exception as e:
            print(f"  {t} ERR {str(e)[:50]}"); continue
        if r:
            rows.append(r)
    conn.close()

    rows.sort(key=lambda r: -r["actual_ret"])
    dirmap = {1: "▲UP", 0: "→FLAT", -1: "▼DOWN"}
    print(f"{'stock':<14}{'as_of':<12}{'predicted':<10}{'actual_5d':>10}{'real':<8}{'hit':>5}")
    print("-" * 60)
    for r in rows:
        print(f"{r['name']:<14}{r['as_of']:<12}{dirmap[r['pred_dir']]:<10}"
              f"{r['actual_ret']:>9}%  {dirmap[r['actual_dir']]:<8}{'O' if r['dir_hit'] else 'X':>4}")

    if rows:
        dir_hits = sum(r["dir_hit"] for r in rows)
        ups = [r for r in rows if r["pred_up"]]
        up_correct = sum(r["up_call_correct"] for r in ups)
        up_ret = np.mean([r["actual_ret"] for r in ups]) if ups else 0
        all_ret = np.mean([r["actual_ret"] for r in rows])
        print(f"\nDirection hit rate: {dir_hits}/{len(rows)} = {dir_hits/len(rows)*100:.0f}%")
        if ups:
            print(f"Model's UP/BUY calls: {len(ups)} | actually positive: {up_correct}/{len(ups)} "
                  f"= {up_correct/len(ups)*100:.0f}% | their avg 5d return: {up_ret:+.2f}%")
        print(f"(vs all-stocks avg 5d return: {all_ret:+.2f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
