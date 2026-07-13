"""pattern_layer.py — Layer ⑩ 과거 패턴 (analog forecasting), boss 2026-07-13.

His idea, his words: "you have a history of the movement — according to the new
movement the decision engine needs to take info." Professional name: analog
forecasting. For a stock RIGHT NOW:

  1. take its last ~2 hours of movement (24 five-minute returns, shape-normalized)
  2. search its OWN full year of 5-minute history for the k=50 most similar windows
     (same-day windows only — no overnight gaps polluting the outcome)
  3. report what happened in the NEXT hour those 50 times: up-rate + average move
  4. plus the ⏰ time-of-day personality: how this stock historically behaved in
     the current 30-minute slot of the day

Used as a WEIGHTED VOICE (confidence boost/penalty in the scanner, a plain-words
line on the panels) — never a veto. Honest expectations: price-shape analogs are
a modest edge; the record grades whether it earns its seat.

Perf: the year of closes per ticker is cached in-process (~150KB each, refreshed
daily); per call it's pure numpy — milliseconds. First scan after a deploy warms
the caches (one ~19k-row query per ticker, once).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger("vip.pattern_layer")

WIN = 24          # the "current movement" = last 24 five-minute bars (~2 hours)
HORIZON = 12      # the outcome window = next 12 bars (~60 minutes)
K = 50            # number of most-similar historical windows to consult
UP_BAR = 0.001    # +0.1% forward move counts as "rose" (clears noise, not costs)

# code → {"day": date, "closes": np.ndarray, "tods": np.ndarray(minutes-of-day)}
_hist: dict[str, dict[str, Any]] = {}


def _load_history(db, code: str) -> Optional[dict[str, Any]]:
    h = _hist.get(code)
    if h and h["day"] == date.today():
        return h
    try:
        import numpy as np
        rows = db.execute(text(
            "SELECT ts, close FROM minute_bars_hist WHERE ticker=:t ORDER BY ts"),
            {"t": code}).fetchall()
        if len(rows) < WIN + HORIZON + 50:
            return None
        closes = np.array([float(r[1]) for r in rows], dtype=np.float64)
        tods = np.array([r[0].hour * 60 + r[0].minute for r in rows], dtype=np.int32)
        days = np.array([r[0].toordinal() for r in rows], dtype=np.int32)
        h = {"day": date.today(), "closes": closes, "tods": tods, "days": days}
        _hist[code] = h
        return h
    except Exception as e:
        db.rollback()
        logger.warning("pattern history load failed %s: %s", code, str(e)[:80])
        return None


def pattern_vote(db, code: str) -> Optional[dict[str, Any]]:
    """The layer-⑩ vote for one stock, or None when data is insufficient."""
    try:
        import numpy as np

        from services.cycle_scalp import _bars
        code = str(code).zfill(6)
        cur_bars = _bars(db, code, limit=WIN + 1)
        if len(cur_bars) < WIN + 1:
            return None
        cur_close = np.array([b["close"] for b in cur_bars], dtype=np.float64)
        cur_ret = np.diff(cur_close) / cur_close[:-1]
        cstd = float(cur_ret.std())
        if cstd < 1e-6:
            return None
        cur_norm = (cur_ret - cur_ret.mean()) / cstd

        h = _load_history(db, code)
        if not h:
            return None
        closes, tods, days = h["closes"], h["tods"], h["days"]
        rets = np.diff(closes) / closes[:-1]
        n = len(rets)
        if n < WIN + HORIZON + 50:
            return None
        try:
            from numpy.lib.stride_tricks import sliding_window_view
            windows = sliding_window_view(rets, WIN)[: n - WIN - HORIZON]
        except Exception:
            return None
        # forward outcome per window: close[i+WIN+HORIZON] vs close[i+WIN]
        idx = np.arange(len(windows))
        fwd = (closes[idx + WIN + HORIZON] - closes[idx + WIN]) / closes[idx + WIN]
        # same-day only: the window's start and the outcome's end share the date
        same_day = days[idx] == days[np.minimum(idx + WIN + HORIZON, len(days) - 1)]
        windows, fwd, idx = windows[same_day], fwd[same_day], idx[same_day]
        if len(windows) < K * 2:
            return None
        mean = windows.mean(axis=1, keepdims=True)
        std = windows.std(axis=1, keepdims=True)
        std[std < 1e-9] = 1e-9
        wn = (windows - mean) / std
        dist = np.linalg.norm(wn - cur_norm, axis=1)
        near = np.argsort(dist)[:K]
        up_rate = float((fwd[near] > UP_BAR).mean() * 100)
        avg_fwd = float(fwd[near].mean() * 100)

        # ⏰ time-of-day personality: outcomes of windows ending near the current slot
        now_tod = cur_bars[-1]["ts"].hour * 60 + cur_bars[-1]["ts"].minute \
            if hasattr(cur_bars[-1]["ts"], "hour") else None
        tod_up = tod_avg = tod_n = None
        slot_txt = None
        if now_tod is not None:
            slot_start = (now_tod // 30) * 30
            end_tods = tods[np.minimum(idx + WIN, len(tods) - 1)]
            in_slot = (end_tods >= slot_start) & (end_tods < slot_start + 30)
            if int(in_slot.sum()) >= 30:
                tod_up = float((fwd[in_slot] > UP_BAR).mean() * 100)
                tod_avg = float(fwd[in_slot].mean() * 100)
                tod_n = int(in_slot.sum())
                # timestamps are stored UTC — matching is UTC-to-UTC (consistent),
                # but the LABEL must read in Korean time
                _k = (slot_start + 540) % 1440
                slot_txt = f"{_k // 60:02d}:{_k % 60:02d}~" \
                           f"{(_k + 30) // 60:02d}:{(_k + 30) % 60:02d}"

        out: dict[str, Any] = {
            "n": int(K), "up_rate": round(up_rate), "avg_fwd_pct": round(avg_fwd, 2),
            "tod_slot": slot_txt, "tod_up": round(tod_up) if tod_up is not None else None,
            "tod_avg_pct": round(tod_avg, 2) if tod_avg is not None else None,
            "tod_n": tod_n,
        }
        out["line_ko"] = (f"지난 1년 중 지금과 가장 비슷했던 움직임 {K}번 → 그중 {out['up_rate']}% 상승"
                          f" (다음 1시간 평균 {out['avg_fwd_pct']:+.2f}%)"
                          + (f" · 이 시간대({slot_txt})는 역사적으로 {out['tod_up']}% 확률로 상승"
                             if out["tod_up"] is not None else ""))
        out["line_en"] = (f"of the {K} most similar past movements this year, {out['up_rate']}% rose"
                          f" (next-hour avg {out['avg_fwd_pct']:+.2f}%)"
                          + (f" · this time slot ({slot_txt}) historically rose {out['tod_up']}% of the time"
                             if out["tod_up"] is not None else ""))
        return out
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("pattern_vote failed %s: %s", code, str(e)[:100])
        return None
