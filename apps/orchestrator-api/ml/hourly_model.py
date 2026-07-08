"""hourly_model.py — M5.6: the 1-HOUR direction model (the boss's short-trade brain).

Question it learns: "from THIS 5-minute moment, will the stock be UP ≥ +0.3% one hour
later?" (+0.3% ≈ covers round-trip costs — a 'worth-buying' hour, matching the boss's
'small rise is enough' style). Secondary diagnostic label: CLEAN rise (up ≥0.3% AND
never dipping below −0.5% on the way — his 'must not decrease' wish).

Data: minute_bars_hist (Kiwoom ka10080 5-min bars) + intraday_snapshot_history
(order-book imbalance / short ratio, joined as-of ≤ t) + KODEX200 as the market
context + a peer stock's move (co-movement cluster). All features use ONLY data ≤ t
(leakage-safe); labels use ONLY (t, t+60min].

Training: LogReg baseline vs LightGBM vs XGBoost, walk-forward split BY DAY (test
days never seen in training). Honest gate: UP-precision on unseen days must make the
money-math work; otherwise we do NOT ship.

Run:  python ml/hourly_model.py build   # dataset -> ml/hourly_dataset.parquet
      python ml/hourly_model.py train   # bake-off + walk-forward report + save model
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

DATASET = HERE / "hourly_dataset.parquet"
MODEL_OUT = HERE / "models_hourly"
MARKET_TK = "069500"                     # KODEX200 = market context
PEER = {"005930": "000660", "000660": "005930", "009150": "005930",
        "402340": "000660", "091160": "000660"}    # else market is the peer
STRIDE = 3                               # sample every 15 min (reduce overlap)
FWD_BARS = 12                            # 60 minutes ahead
UP_TH = 0.3                              # label: fwd close return ≥ +0.3%
DIP_TH = -0.5                            # 'clean' = never below −0.5% en route


def _rsi(closes: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(closes, prepend=closes[0])
    up = np.clip(d, 0, None)
    dn = np.clip(-d, 0, None)
    ru = pd.Series(up).rolling(n).mean().to_numpy()
    rd = pd.Series(dn).rolling(n).mean().to_numpy()
    rs = np.divide(ru, rd, out=np.full_like(ru, np.nan), where=rd > 0)
    return 100 - 100 / (1 + rs)


def build() -> None:
    from sqlalchemy import text

    from db.base import SessionLocal
    db = SessionLocal()
    print("loading 5-min bars…")
    bars = pd.read_sql(text(
        "SELECT ticker, ts, open, high, low, close, volume FROM minute_bars_hist "
        "WHERE close IS NOT NULL ORDER BY ticker, ts"), db.bind)
    bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
    print(f"  {len(bars):,} rows · {bars['ticker'].nunique()} tickers")

    print("loading snapshots (imbalance/short)…")
    snap = pd.read_sql(text(
        "SELECT ticker, ts, imbalance, short_ratio FROM intraday_snapshot_history "
        "WHERE price IS NOT NULL ORDER BY ticker, ts"), db.bind)
    snap["ts"] = pd.to_datetime(snap["ts"], utc=True)
    db.close()

    # per-ticker 5-min grid
    frames = []
    grid = {}
    for tk, g in bars.groupby("ticker"):
        g = (g.set_index("ts").resample("5min")
             .agg({"open": "first", "high": "max", "low": "min",
                   "close": "last", "volume": "sum"}).dropna(subset=["close"]))
        grid[tk] = g
    mkt = grid.get(MARKET_TK)

    for tk, g in grid.items():
        if tk == MARKET_TK or len(g) < 60:
            continue
        c = g["close"].to_numpy()
        h = g["high"].to_numpy()
        lo = g["low"].to_numpy()
        v = g["volume"].to_numpy().astype(float)
        idx = g.index
        rsi = _rsi(c)
        rows = []
        # KST session anchors
        kst = idx.tz_convert("Asia/Seoul")
        day = kst.date
        mins_open = (kst.hour * 60 + kst.minute) - 540
        # market series aligned
        mc = mkt["close"].reindex(idx).ffill().to_numpy() if mkt is not None else None
        ptk = PEER.get(tk)
        pg = grid.get(ptk) if ptk else None
        pc = pg["close"].reindex(idx).ffill().to_numpy() if pg is not None else None
        # as-of snapshot join (imbalance/short) per ticker
        s = snap[snap["ticker"] == tk].set_index("ts").sort_index()
        simb = s["imbalance"].astype(float).reindex(idx, method="ffill").to_numpy() if len(s) else None
        ssr = s["short_ratio"].astype(float).reindex(idx, method="ffill").to_numpy() if len(s) else None

        def ret(a, i, k):
            j = i - k
            return (a[i] / a[j] - 1) * 100 if j >= 0 and a[j] else np.nan

        for i in range(36, len(g) - FWD_BARS, STRIDE):
            # same trading day for the label window (avoid overnight jumps)
            if day[i + FWD_BARS] != day[i]:
                continue
            if not (0 <= mins_open[i] <= 330):        # 09:00–14:30 entries only
                continue
            fwd = (c[i + FWD_BARS] / c[i] - 1) * 100
            fwd_min = (lo[i + 1:i + 1 + FWD_BARS].min() / c[i] - 1) * 100
            fwd_max = (h[i + 1:i + 1 + FWD_BARS].max() / c[i] - 1) * 100
            rets5 = pd.Series(c[max(0, i - 30):i + 1]).pct_change().dropna()
            vol1h = float(rets5.std() * np.sqrt(12) * 100) if len(rets5) > 5 else np.nan
            day_mask = np.array([day[j] == day[i] for j in range(max(0, i - 78), i + 1)])
            dslice = slice(max(0, i - 78), i + 1)
            dh = h[dslice][day_mask].max()
            dl = lo[dslice][day_mask].min()
            vbase = v[max(0, i - 36):i - 2].mean() if i > 40 else np.nan
            rows.append({
                "ticker": tk, "ts": idx[i], "day": str(day[i]),
                "r5": ret(c, i, 1), "r15": ret(c, i, 3), "r30": ret(c, i, 6), "r60": ret(c, i, 12),
                "vol1h": vol1h,
                "rsi": rsi[i], "rsi_slope": rsi[i] - rsi[i - 1] if not np.isnan(rsi[i - 1]) else np.nan,
                "dist_sma2h": (c[i] / c[max(0, i - 24):i + 1].mean() - 1) * 100,
                "pos_day_range": (c[i] - dl) / (dh - dl) if dh > dl else 0.5,
                "vol_surge": (v[i - 2:i + 1].mean() / vbase) if vbase and vbase > 0 else np.nan,
                "mins_open": float(mins_open[i]), "dow": float(kst[i].dayofweek),
                "mkt_r15": ret(mc, i, 3) if mc is not None else np.nan,
                "mkt_r60": ret(mc, i, 12) if mc is not None else np.nan,
                "peer_r15": ret(pc, i, 3) if pc is not None else np.nan,
                "imbalance": simb[i] if simb is not None else np.nan,
                "short_ratio": ssr[i] if ssr is not None else np.nan,
                "fwd_ret": fwd, "fwd_min": fwd_min, "fwd_max": fwd_max,
                "y_up": int(fwd >= UP_TH),
                "y_clean": int(fwd >= UP_TH and fwd_min > DIP_TH),
            })
        if rows:
            frames.append(pd.DataFrame(rows))
        print(f"  {tk}: {len(rows)} samples")
    ds = pd.concat(frames, ignore_index=True)
    ds.to_parquet(DATASET)
    print(f"\nDATASET: {len(ds):,} samples · {ds['ticker'].nunique()} tickers · "
          f"days {ds['day'].min()} → {ds['day'].max()}")
    print(f"label balance: UP {ds['y_up'].mean()*100:.1f}% · CLEAN {ds['y_clean'].mean()*100:.1f}%")


FEATS = ["r5", "r15", "r30", "r60", "vol1h", "rsi", "rsi_slope", "dist_sma2h",
         "pos_day_range", "vol_surge", "mins_open", "dow", "mkt_r15", "mkt_r60",
         "peer_r15", "imbalance", "short_ratio"]


def train() -> None:
    ds = pd.read_parquet(DATASET)
    days = sorted(ds["day"].unique())
    n_test = max(3, len(days) // 4)
    test_days = days[-n_test:]
    tr = ds[~ds["day"].isin(test_days)]
    te = ds[ds["day"].isin(test_days)]
    print(f"walk-forward: train {len(tr):,} ({days[0]}→{days[-n_test-1]}) · "
          f"test {len(te):,} on UNSEEN days {test_days}")
    Xtr, ytr = tr[FEATS], tr["y_up"]
    Xte, yte = te[FEATS], te["y_up"]
    base_rate = yte.mean()
    print(f"test base rate (always-guess-UP accuracy): {base_rate*100:.1f}%")

    results = {}
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    lr = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                       LogisticRegression(max_iter=1000, class_weight="balanced"))
    lr.fit(Xtr, ytr)
    results["logreg"] = (lr, lr.predict_proba(Xte)[:, 1])

    import lightgbm as lgb
    gbm = lgb.LGBMClassifier(n_estimators=400, learning_rate=0.03, num_leaves=31,
                             min_child_samples=40, subsample=0.8, colsample_bytree=0.8,
                             class_weight="balanced", random_state=42, verbosity=-1)
    gbm.fit(Xtr, ytr)
    results["lightgbm"] = (gbm, gbm.predict_proba(Xte)[:, 1])

    import xgboost as xgb
    xg = xgb.XGBClassifier(n_estimators=400, learning_rate=0.03, max_depth=5,
                           subsample=0.8, colsample_bytree=0.8, random_state=42,
                           eval_metric="logloss",
                           scale_pos_weight=float((ytr == 0).sum() / max((ytr == 1).sum(), 1)))
    xg.fit(Xtr, ytr)
    results["xgboost"] = (xg, xg.predict_proba(Xte)[:, 1])

    print("\n=== UNSEEN-DAYS RESULTS (the honest numbers) ===")
    best_name, best_val = None, -1
    for name, (mdl, proba) in results.items():
        for th in (0.5, 0.6, 0.65):
            pred = (proba >= th).astype(int)
            n_sig = int(pred.sum())
            if n_sig == 0:
                print(f"{name:9s} th={th}: no signals")
                continue
            prec = float(yte[pred == 1].mean())          # of predicted-UP, how many really rose ≥0.3%
            # money sim: buy each signal, exit at t+60min close, cost 0.23%
            pnl = te.loc[pred == 1, "fwd_ret"] - 0.23
            clean = float(te.loc[pred == 1, "y_clean"].mean())
            print(f"{name:9s} th={th}: signals {n_sig:4d} · UP-precision {prec*100:5.1f}% "
                  f"(base {base_rate*100:.1f}%) · avg net {pnl.mean():+.3f}%/trade · "
                  f"total {pnl.sum():+.1f}% · clean-rise {clean*100:.0f}%")
            if th == 0.6 and prec > best_val:
                best_val, best_name = prec, name
    # feature importances of the best GBM
    try:
        imp = pd.Series(results["lightgbm"][0].feature_importances_, index=FEATS).sort_values(ascending=False)
        print("\ntop features (lightgbm):", ", ".join(f"{k}:{v}" for k, v in imp.head(8).items()))
    except Exception:
        pass
    # save the best model
    MODEL_OUT.mkdir(exist_ok=True)
    import joblib
    joblib.dump({"model": results[best_name or 'lightgbm'][0], "features": FEATS,
                 "trained_days": [str(d) for d in days if d not in test_days],
                 "test_days": [str(d) for d in test_days]},
                MODEL_OUT / "hourly_up.joblib")
    print(f"\nsaved {best_name or 'lightgbm'} → {MODEL_OUT/'hourly_up.joblib'}")


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        from _db import load_env
        load_env()
    except Exception:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build()
    elif cmd == "train":
        train()
    else:
        build()
        train()
