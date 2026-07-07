"""turn_engine.py — intraday TURN timing: when a fall ends (buy) and a rise ends (sell).

The boss's strategy (2026-07-07): catch the bottom-turn, ride the up-leg, exit at the
top-turn, repeat. Built in three layers, all measured from REAL stored 5-min bars:
  1) RHYTHM prior  — each stock's typical down/up-leg duration & depth (A1) + the hour-of-day
                     turn quality measured across 12 days (09h: 85% real turns, 14h: 66%).
  2) CONDITION     — bottom-turn score (RSI turn-up + volume climax + micro higher-low +
                     leg-maturity) and top-turn score (RSI divergence + momentum fade +
                     leg-maturity). Deterministic, per-stock calibrated.
  3) HONESTY       — backtest() replays the full loop on stored bars MINUS costs
                     (COST_RT ≈ commission+tax+slippage per round trip). GO/NO-GO per stock.

No invented numbers: every stat comes from stored bars; live extras (order-book flip) are
added on top at serve time but never used in the backtest (no historical book data).
"""
from __future__ import annotations

import statistics as st
from collections import defaultdict
from typing import Any, Optional

REV = 0.003            # zigzag reversal filter (0.3%) — defines legs
COST_RT = 0.25         # % round-trip cost: commission ~0.03 + tax 0.15~0.20 + slippage
STOP_PCT = 0.7         # emergency stop below entry (%)
FIRE_BOTTOM = 0.75     # bottom-turn score needed to buy (v2: demand real confirmation)
FIRE_TOP = 0.5         # top-turn score needed to exit
MIN_EXIT_GAIN = 0.45   # v2: never pay round-trip costs for micro-gains — hold until
                       # the move can beat costs, unless the stop/EOD forces out
CRASH_DAY_PCT = -3.0   # v2: skip days the stock itself fell >3% (mirror the live index veto)
# hour-of-day weights measured in A2 (UTC hours in DB: 0=09KST best, 5=14KST trap)
HOUR_W = {0: 1.1, 1: 1.0, 2: 0.95, 3: 0.95, 4: 1.0, 5: 0.8, 6: 0.9}


def _rsi(closes: list[float], period: int = 14) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) <= period:
        return out
    g = l = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        g += max(d, 0); l += max(-d, 0)
    ag, al = g / period, l / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def _day_bars(db, ticker: str, include_live: bool = True) -> dict[str, list[dict]]:
    from sqlalchemy import text
    src = ("SELECT ts, open, high, low, close, volume FROM minute_bars_hist WHERE ticker=:t"
           + (" UNION SELECT ts, open, high, low, close, volume FROM raw_minute_prices WHERE ticker=:t"
              if include_live else ""))
    rows = db.execute(text(f"SELECT * FROM ({src}) u ORDER BY ts"), {"t": str(ticker).zfill(6)}).fetchall()
    # TZ NORMALIZE + DEDUPE: minute_bars_hist stores UTC-labeled hours (00-06 = 09-15 KST)
    # while raw_minute_prices stores KST-labeled hours (09-15). Mixing them put ~24h of bars
    # into one 'day' and broke leg-duration math (1,455-min legs). Shift KST-labeled rows
    # back 9h so everything is on the hist convention; last row per ts wins.
    import datetime as _dt
    dedup: dict[str, dict] = {}
    for r in rows:
        ts = r[0]
        hh = int(str(ts)[11:13])
        if hh >= 8:                                   # KST-labeled row → normalize to UTC label
            try:
                ts = ts - _dt.timedelta(hours=9)
                hh = int(str(ts)[11:13])
            except Exception:
                pass
        dedup[str(ts)] = {"ts": ts, "hour": hh,
                          "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                          "close": float(r[4]), "volume": float(r[5] or 0)}
    # RESAMPLE to uniform 5-min buckets: hist is 5-min but the live collector writes 1-MIN
    # rows (60/hour) — mixing granularities broke the bars×5 duration math (1,455-min legs).
    buckets: dict[str, dict] = {}
    for ts in sorted(dedup):
        b = dedup[ts]
        mm = (int(ts[14:16]) // 5) * 5
        key = f"{ts[:14]}{mm:02d}"
        cur = buckets.get(key)
        if cur is None:
            buckets[key] = {"ts": b["ts"], "hour": b["hour"], "open": b["open"], "high": b["high"],
                            "low": b["low"], "close": b["close"], "volume": b["volume"]}
        else:
            cur["high"] = max(cur["high"], b["high"])
            cur["low"] = min(cur["low"], b["low"])
            cur["close"] = b["close"]
            cur["volume"] += b["volume"]
    by: dict[str, list[dict]] = defaultdict(list)
    for key in sorted(buckets):
        by[key[:10]].append(buckets[key])
    return by


def rhythm(db, ticker: str) -> dict[str, Any]:
    """A1 rhythm card — the stock's typical legs, measured from all stored bars."""
    by = _day_bars(db, ticker)
    dn_d, dn_p, up_d, up_p = [], [], [], []
    for day, bars in by.items():
        closes = [b["close"] for b in bars]
        piv_i, piv_p, ext_i, ext_p, direction = 0, closes[0] if closes else 0, 0, closes[0] if closes else 0, 0
        for i, p in enumerate(closes[1:], 1):
            if direction >= 0 and p > ext_p: ext_i, ext_p = i, p
            if direction <= 0 and p < ext_p: ext_i, ext_p = i, p
            if direction == 0:
                if p >= closes[0] * (1 + REV): direction, ext_i, ext_p = 1, i, p
                elif p <= closes[0] * (1 - REV): direction, ext_i, ext_p = -1, i, p
                continue
            if direction == 1 and p <= ext_p * (1 - REV):
                if ext_i > piv_i: up_d.append((ext_i - piv_i) * 5); up_p.append((ext_p / piv_p - 1) * 100)
                piv_i, piv_p, direction, ext_i, ext_p = ext_i, ext_p, -1, i, p
            elif direction == -1 and p >= ext_p * (1 + REV):
                if ext_i > piv_i: dn_d.append((ext_i - piv_i) * 5); dn_p.append((ext_p / piv_p - 1) * 100)
                piv_i, piv_p, direction, ext_i, ext_p = ext_i, ext_p, 1, i, p
    if len(up_d) < 8 or len(dn_d) < 8:
        return {"ok": False}
    return {"ok": True, "ticker": str(ticker).zfill(6), "days": len(by),
            "dn_min": round(st.mean(dn_d)), "dn_pct": round(st.mean(dn_p), 2),
            "up_min": round(st.mean(up_d)), "up_pct": round(st.mean(up_p), 2),
            "ups_per_day": round(len(up_d) / max(len(by), 1), 1),
            "regularity": round(1 / (1 + st.pstdev(up_d) / max(st.mean(up_d), 1)), 2)}


def _bottom_score(i: int, bars: list[dict], rsis: list, vol_avg: float,
                  dn_run_min: float, dn_run_pct: float, rh: dict) -> float:
    """Bottom-turn (BUY timing) score at bar i. Deterministic components, 0..~1.2."""
    s = 0.0
    r_now, r_prev = rsis[i], rsis[i - 1] if i >= 1 else None
    if r_now is None or r_prev is None:
        return 0.0
    if r_prev < 38 and r_now > r_prev:
        s += 0.30 + (0.10 if r_prev < 30 else 0.0)          # RSI turning up from a low
    if any(bars[j]["volume"] >= 2 * vol_avg for j in range(max(0, i - 2), i + 1)) and vol_avg > 0:
        s += 0.20                                            # seller-exhaustion volume climax
    if i >= 2 and bars[i]["low"] > bars[i - 1]["low"] and bars[i]["close"] > bars[i - 1]["close"]:
        s += 0.20                                            # micro higher-low + up close
    if rh.get("ok"):
        if dn_run_min >= 0.8 * rh["dn_min"]:
            s += 0.15                                        # the fall has run its typical time
        if dn_run_pct <= 0.8 * rh["dn_pct"]:                 # dn_pct negative — deep enough
            s += 0.15
    return s * HOUR_W.get(bars[i]["hour"], 1.0)


def _top_score(i: int, bars: list[dict], rsis: list, up_run_min: float, up_gain_pct: float,
               peak_rsi: Optional[float], rh: dict) -> float:
    """Top-turn (SELL timing) score at bar i while holding."""
    s = 0.0
    r_now = rsis[i]
    if r_now is None:
        return 0.0
    if peak_rsi is not None and bars[i]["high"] >= max(b["high"] for b in bars[max(0, i - 3):i + 1]) \
            and r_now < peak_rsi - 2:
        s += 0.30                                            # price high, RSI lower = divergence
    if i >= 2 and bars[i]["close"] < bars[i - 1]["close"]:
        s += 0.20                                            # momentum fading
    if rh.get("ok"):
        if up_run_min >= rh["up_min"]:
            s += 0.25                                        # rise has run its typical time
        if up_gain_pct >= 0.9 * rh["up_pct"]:
            s += 0.25                                        # gain near its typical size
    return s


def backtest(db, ticker: str) -> dict[str, Any]:
    """C2 — replay the full bottom-turn→top-turn loop on stored bars, costs subtracted."""
    rh = rhythm(db, ticker)
    by = _day_bars(db, ticker, include_live=False)
    trades: list[dict] = []
    for day in sorted(by):
        bars = by[day]
        if len(bars) < 20:
            continue
        closes = [b["close"] for b in bars]
        # v2 regime filter: skip the stock's own crash days (live serving uses the index veto)
        if (closes[-1] / bars[0]["open"] - 1) * 100 <= CRASH_DAY_PCT:
            continue
        rsis = _rsi(closes)
        vols = [b["volume"] for b in bars]
        pos = None                                          # {entry, entry_i, peak, peak_rsi}
        dn_start_i, dn_start_p = 0, closes[0]
        for i in range(15, len(bars)):
            vol_avg = st.mean(vols[max(0, i - 20):i]) if i > 0 else 0
            if pos is None:
                if closes[i] > closes[i - 1] and closes[i - 1] <= min(closes[max(0, i - 4):i]):
                    pass                                     # potential pivot; scoring decides
                if closes[i] >= closes[dn_start_i]:
                    dn_start_i, dn_start_p = i, closes[i]    # reset down-leg anchor on new highs
                dn_run_min = (i - dn_start_i) * 5
                dn_run_pct = (closes[i] / dn_start_p - 1) * 100
                if _bottom_score(i, bars, rsis, vol_avg, dn_run_min, dn_run_pct, rh) >= FIRE_BOTTOM:
                    pos = {"entry": closes[i], "entry_i": i, "peak": closes[i], "peak_rsi": rsis[i]}
                continue
            # holding — track peak + exits
            if closes[i] > pos["peak"]:
                pos["peak"], pos["peak_rsi"] = closes[i], rsis[i]
            gain = (closes[i] / pos["entry"] - 1) * 100
            up_run = (i - pos["entry_i"]) * 5
            if bars[i]["low"] <= pos["entry"] * (1 - STOP_PCT / 100):
                trades.append({"pnl": -STOP_PCT - COST_RT, "min": up_run, "exit": "stop"})
                pos, dn_start_i, dn_start_p = None, i, closes[i]
                continue
            if gain >= MIN_EXIT_GAIN and _top_score(i, bars, rsis, up_run, gain, pos.get("peak_rsi"), rh) >= FIRE_TOP:
                trades.append({"pnl": gain - COST_RT, "min": up_run, "exit": "top_turn"})
                pos, dn_start_i, dn_start_p = None, i, closes[i]
                continue
        if pos is not None:                                  # end of day close-out
            gain = (closes[-1] / pos["entry"] - 1) * 100
            trades.append({"pnl": gain - COST_RT, "min": (len(bars) - 1 - pos["entry_i"]) * 5, "exit": "eod"})
    if not trades:
        return {"ok": False, "ticker": str(ticker).zfill(6), "trades": 0}
    pnls = [t["pnl"] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    return {"ok": True, "ticker": str(ticker).zfill(6), "days": len(by), "rhythm": rh,
            "trades": len(trades), "wins": wins, "losses": len(pnls) - wins,
            "win_rate_pct": round(100 * wins / len(pnls), 1),
            "net_pnl_pct": round(sum(pnls), 2),
            "avg_pnl_pct": round(st.mean(pnls), 3),
            "avg_hold_min": round(st.mean(t["min"] for t in trades)),
            "cost_rt_pct": COST_RT,
            "go": bool(sum(pnls) > 0 and wins / len(pnls) >= 0.55 and len(pnls) >= 12)}


def turn_reply(db, ticker: str, name: str, lang: str = "ko") -> Optional[str]:
    """Chat answer for '언제 반등해? / when will it turn?' — rhythm prior + live turn score.
    HONEST LABEL: backtest is not yet profitable (v2 NO-GO), so this is informational timing
    context, not a trade signal. Numbers are real (stored bars + live collector)."""
    s = live_turn_status(db, ticker)
    rh = (s.get("rhythm") or {}) if s.get("ok") else rhythm(db, ticker)
    if not rh.get("ok"):
        return None
    en = str(lang or "").lower().startswith("en")
    L = []
    if en:
        L.append(f"**⏱ {name} — turn timing (measured rhythm + live read)**")
        L.append("")
        L.append(f"**This stock's rhythm ({rh['days']} trading days measured):** falls typically run "
                 f"**{rh['dn_min']} min / {rh['dn_pct']}%**, rises **{rh['up_min']} min / +{rh['up_pct']}%**, "
                 f"about {rh['ups_per_day']} up-waves per day.")
        if s.get("ok") and s.get("price"):
            leg = "falling" if s.get("leg") == "down" else "rising/flat"
            L.append(f"**Right now:** ₩{s['price']:,.0f} · current leg: {leg} "
                     f"({s.get('leg_run_min')} min, {s.get('leg_run_pct')}%) · 5-min RSI {s.get('rsi')}")
            sc = s.get("bottom_turn_score") or 0
            if s.get("fire"):
                L.append(f"**Bottom-turn signal: FIRING (score {sc})** — RSI turning up out of the low; "
                         f"if this is a real turn, the typical rise from here is ~{rh['up_min']} min / +{rh['up_pct']}%.")
            elif s.get("leg") == "down":
                _left = max(rh["dn_min"] - (s.get("leg_run_min") or 0), 0)
                L.append(f"**Bottom-turn signal: not yet (score {sc})** — statistically ~{_left} min "
                         f"of typical fall time remains; watch for the RSI upturn + buyer step-in.")
            else:
                L.append(f"**No fall in progress** — turn signals arm on the next down-leg (score {sc}).")
        L.append("")
        L.append(f"_Best turn hours (measured): 09:00–10:00 (85% real) · worst: 14:00–15:00 (66%). "
                 f"⚠️ Honesty: the turn strategy's backtest is NOT yet profitable after costs "
                 f"(verdict NO-GO, sample still small) — use this as timing context, not a signal._")
        return "\n".join(L)
    L.append(f"**⏱ {name} — 턴(반등/고점) 타이밍 (실측 리듬 + 실시간)**")
    L.append("")
    L.append(f"**이 종목의 리듬 (실측 {rh['days']}거래일):** 하락 레그 평균 **{rh['dn_min']}분 / {rh['dn_pct']}%**, "
             f"상승 레그 평균 **{rh['up_min']}분 / +{rh['up_pct']}%**, 하루 상승파동 약 {rh['ups_per_day']}회.")
    if s.get("ok") and s.get("price"):
        leg = "하락 중" if s.get("leg") == "down" else "상승/보합"
        L.append(f"**지금 상태:** {s['price']:,.0f}원 · 현재 레그: {leg} "
                 f"({s.get('leg_run_min')}분째, {s.get('leg_run_pct')}%) · 5분봉 RSI {s.get('rsi')}")
        sc = s.get("bottom_turn_score") or 0
        if s.get("fire"):
            L.append(f"**바닥 턴 신호: 점등 (점수 {sc})** — RSI가 저점에서 돌아서는 중이에요. 진짜 턴이라면 "
                     f"여기서 평균 ~{rh['up_min']}분 / +{rh['up_pct']}% 상승 레그가 통계적 기대치예요.")
        elif s.get("leg") == "down":
            _left = max(rh["dn_min"] - (s.get("leg_run_min") or 0), 0)
            L.append(f"**바닥 턴 신호: 아직 (점수 {sc})** — 평균 하락 시간까지 통계적으로 ~{_left}분 남았어요. "
                     f"RSI 반전 + 매수세 유입이 확인되면 알려드릴 수 있는 신호가 켜져요.")
        else:
            L.append(f"**지금은 하락 레그가 아니에요** — 다음 눌림에서 턴 신호가 작동해요 (점수 {sc}).")
    L.append("")
    L.append(f"_턴이 잘 맞는 시간대(실측): 09~10시(85% 진짜) · 가짜 턴 많은 시간대: 14~15시(66%). "
             f"⚠️ 정직 고지: 이 턴 전략의 백테스트는 아직 비용 차감 후 수익이 아닙니다(판정 NO-GO, 표본 부족) — "
             f"매매 신호가 아니라 타이밍 참고 정보로 쓰세요._")
    return "\n".join(L)


def live_turn_status(db, ticker: str) -> dict[str, Any]:
    """D1 — live turn read for NOW (today's bars incl. the live collector)."""
    rh = rhythm(db, ticker)
    by = _day_bars(db, ticker, include_live=True)
    if not by:
        return {"ok": False}
    today = sorted(by)[-1]
    bars = by[today]
    if len(bars) < 16:
        return {"ok": False, "note": "not enough bars today yet"}
    closes = [b["close"] for b in bars]
    rsis = _rsi(closes)
    vols = [b["volume"] for b in bars]
    i = len(bars) - 1
    vol_avg = st.mean(vols[max(0, i - 20):i])
    # current leg state
    dn_start_i = max(range(len(closes)), key=lambda j: closes[j] if j <= i else -1)
    dn_run_min = (i - dn_start_i) * 5
    dn_run_pct = (closes[i] / closes[dn_start_i] - 1) * 100
    b_score = _bottom_score(i, bars, rsis, vol_avg, dn_run_min, dn_run_pct, rh)
    falling = dn_run_pct < -0.15
    out = {"ok": True, "ticker": str(ticker).zfill(6), "price": closes[i],
           "rsi": round(rsis[i], 1) if rsis[i] is not None else None,
           "leg": "down" if falling else "up/flat",
           "leg_run_min": dn_run_min, "leg_run_pct": round(dn_run_pct, 2),
           "bottom_turn_score": round(min(b_score, 1.0), 2),
           "fire": b_score >= FIRE_BOTTOM, "asof": str(bars[i]["ts"]), "rhythm": rh}
    return out
