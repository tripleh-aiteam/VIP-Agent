"""hourly_model.py — live predictor for the M5.6 1-hour model (RANKING VOICE ONLY).

HONEST STATUS (2026-07-08, trained on ALL 1yr of Kiwoom 5-min history, 207k samples,
62 unseen test days): direction skill is REAL (+12pp over base: 44.5% vs 32.8%) but
NOT enough to trade solo after 0.23% costs (time-exit ≈ breakeven, touch-exits
negative). Therefore this model:
  • RANKS scanner candidates + shows its probability in the answer (transparency),
  • does NOT gate/veto and does NOT generate trades by itself,
  • is re-evaluated as richer features accumulate (imbalance/short/tick history only
    began July 2026 — the model has barely seen its best features). Promotion gate:
    ≥55% precision + positive money-sim on unseen days.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("vip.hourly_model")
_MODEL_PATH = Path(__file__).resolve().parent.parent / "ml" / "models_hourly" / "hourly_up.joblib"
_cache: dict[str, Any] = {"bundle": None, "mtime": None}


def _bundle():
    try:
        m = _MODEL_PATH.stat().st_mtime
    except OSError:
        return None
    if _cache["bundle"] is None or _cache["mtime"] != m:
        try:
            import joblib
            # SECURITY: joblib deserializes pickle. Safe here — this file is produced
            # exclusively by OUR OWN trainer (ml/hourly_model.py) on the same machine,
            # lives inside the repo tree, and is never downloaded from external sources.
            _cache["bundle"] = joblib.load(_MODEL_PATH)
            _cache["mtime"] = m
        except Exception as e:
            logger.warning("hourly_model load failed: %s", str(e)[:80])
            return None
    return _cache["bundle"]


def prob_up_1h(db, ticker: str) -> Optional[float]:
    """P(this stock is up >=0.3% one hour from now) — live features, same recipe as
    training. None when the model/file/data is unavailable (callers must degrade)."""
    b = _bundle()
    if not b:
        return None
    try:
        import numpy as np
        import pandas as pd

        from services.cycle_scalp import _bars, _rsi as _rsi_list
        code = str(ticker).zfill(6)
        bars = _bars(db, code, limit=90)
        if len(bars) < 40:
            return None
        c = [x["close"] for x in bars]
        rsis = _rsi_list(c)
        i = len(c) - 1

        def ret(k):
            return (c[i] / c[i - k] - 1) * 100 if i - k >= 0 and c[i - k] else np.nan
        rets5 = pd.Series(c[-31:]).pct_change().dropna()
        vol1h = float(rets5.std() * np.sqrt(12) * 100) if len(rets5) > 5 else np.nan
        from datetime import datetime, timedelta, timezone
        kst = datetime.now(timezone(timedelta(hours=9)))
        mins_open = (kst.hour * 60 + kst.minute) - 540
        # market + peer context
        from services.micro_trend import _market_chg_pct
        mkt = _market_chg_pct()
        from services.peer_cluster import _chg_pct
        _PEER = {"005930": "000660", "000660": "005930", "009150": "005930",
                 "402340": "000660", "091160": "000660"}   # same map as training
        peer = _PEER.get(code)
        feats = {
            "r5": ret(1), "r15": ret(3), "r30": ret(6), "r60": ret(12), "vol1h": vol1h,
            "rsi": rsis[i], "rsi_slope": (rsis[i] - rsis[i - 1]) if rsis[i - 1] is not None else np.nan,
            "dist_sma2h": (c[i] / (sum(c[-25:]) / len(c[-25:])) - 1) * 100,
            "pos_day_range": 0.5, "vol_surge": np.nan,
            "mins_open": float(mins_open), "dow": float(kst.weekday()),
            "mkt_r15": mkt if mkt is not None else np.nan, "mkt_r60": mkt if mkt is not None else np.nan,
            "peer_r15": _chg_pct(peer) if peer else np.nan,
            "imbalance": np.nan, "short_ratio": np.nan,
        }
        try:      # live imbalance/short from the latest snapshot
            from sqlalchemy import text
            r = db.execute(text(
                "SELECT imbalance, short_ratio FROM realtime_snapshot WHERE ticker=:t"),
                {"t": code}).first()
            if r:
                feats["imbalance"] = float(r[0]) if r[0] is not None else np.nan
                feats["short_ratio"] = float(r[1]) if r[1] is not None else np.nan
        except Exception:
            db.rollback()
        X = pd.DataFrame([[feats.get(f, np.nan) for f in b["features"]]], columns=b["features"])
        return float(b["model"].predict_proba(X)[0, 1])
    except Exception as e:
        logger.warning("hourly_model predict failed %s: %s", ticker, str(e)[:80])
        return None
