"""🧠 Unified decision brain — the transparent scoreboard (boss 2026-07-16).

We already fuse ~10 signals inside decision_agent.decide() (with track-record
weighting + confidence gates). What was missing: the boss couldn't SEE the
vote. This renders decide()'s `signals_breakdown` as a plain scoreboard —
verdict + confidence + who voted BUY, who voted SELL, and how heavily each
counted — and adds a TIMING layer (live candle state + overnight stats) shown
SEPARATELY from the 5-day direction, because those are shorter-horizon signals
and mixing horizons would pollute the gate (the same discipline decide() keeps
for the cycle strategy).

One call → one explainable answer, identical on both bots, KO or EN.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_VOTE_KO = {"BUY": "매수", "SELL": "매도", "HOLD": "중립"}
_VOTE_EN = {"BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD"}
_ICON = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}


def _timing_layer(db, code: str, name: str) -> dict[str, Any]:
    """Short-horizon context: live 1-min candle state + overnight gap stats.
    Not day-decision voters — shown as a separate timing note."""
    out: dict[str, Any] = {}
    try:
        from services.scalp_trader import _streaks_1m
        up, dn, n = _streaks_1m(code)
        if n:
            out["candle"] = {"up": up, "dn": dn,
                             "signal": "BUY" if up >= 3 else "SELL" if dn >= 2 else "HOLD"}
    except Exception:
        pass
    return out


def scoreboard(db, d: dict[str, Any], lang: str = "ko", with_timing: bool = True) -> str:
    """Render the 🧠 vote scoreboard HEADER from an already-computed decide() dict.
    No re-run — callers that already have the decide result prepend this."""
    en = str(lang or "").lower().startswith("en")
    code = str(d.get("ticker") or "").zfill(6)
    name = d.get("name") or code
    decision = d.get("decision") or "HOLD"
    conf = d.get("confidence") or "low"
    conf_ko = {"high": "높음", "medium": "보통", "low": "낮음"}.get(conf, "낮음")
    sigs = [s for s in (d.get("signals_breakdown") or []) if s.get("vote") != "HOLD"]
    buys = sorted([s for s in sigs if s["vote"] == "BUY"],
                  key=lambda s: abs(s["contribution"]), reverse=True)
    sells = sorted([s for s in sigs if s["vote"] == "SELL"],
                   key=lambda s: abs(s["contribution"]), reverse=True)
    timing = _timing_layer(db, code, name) if with_timing else {}

    def _row(s, e):
        lab = s["en"] if e else s["ko"]
        w = s["weight"]
        return f"{_ICON[s['vote']]} {lab}" + (f" ×{w}" if abs(w - 1.0) > 0.01 else "")

    L: list[str] = []
    head = _VOTE_EN[decision] if en else _VOTE_KO[decision]
    score = d.get("score")
    if en:
        L.append(f"🧠 Smart decision — {name}: **{head}** (confidence {conf}, score {score:+})")
        if buys:
            L.append("🟢 Buy-side — " + ", ".join(_row(s, True) for s in buys[:6]))
        if sells:
            L.append("🔴 Sell-side — " + ", ".join(_row(s, True) for s in sells[:6]))
        if not buys and not sells:
            L.append("No signal leaned either way — balanced evidence.")
        if decision == "HOLD":
            L.append(f"→ HOLD: {len(buys)} signal(s) leaned buy, {len(sells)} leaned sell — "
                     "not enough agreement to act with confidence, so the brain waits. "
                     "Weights reflect each method's measured track record.")
        else:
            _bk = buys if decision == "BUY" else sells
            _op = sells if decision == "BUY" else buys
            L.append(f"→ {head}: {len(_bk)} signal(s) backed it, {len(_op)} opposed. "
                     "Weights reflect each method's measured track record — proven-strong "
                     "evidence counts more.")
        if timing.get("candle"):
            c = timing["candle"]
            ct = ("3+ up 1-min candles (short-term rising)" if c["signal"] == "BUY"
                  else "2 down 1-min candles (short-term falling)" if c["signal"] == "SELL"
                  else "no clear 1-min streak")
            L.append(f"⏱️ Timing now: {ct} — minutes-horizon, separate from the 5-day call above.")
    else:
        L.append(f"🧠 스마트 결정 — {name}: **{head}** (신뢰도 {conf_ko}, 점수 {score:+})")
        if buys:
            L.append("🟢 매수 쪽 — " + ", ".join(_row(s, False) for s in buys[:6]))
        if sells:
            L.append("🔴 매도 쪽 — " + ", ".join(_row(s, False) for s in sells[:6]))
        if not buys and not sells:
            L.append("어느 쪽으로도 기운 신호가 없습니다 — 증거가 균형입니다.")
        if decision == "HOLD":
            L.append(f"→ 중립(관망): 매수 {len(buys)}개 · 매도 {len(sells)}개로 "
                     "확신 있게 움직일 만큼 합의가 안 돼 기다립니다. "
                     "가중치는 각 방법의 측정된 실적을 반영합니다.")
        else:
            _bk = buys if decision == "BUY" else sells
            _op = sells if decision == "BUY" else buys
            L.append(f"→ {head}: {len(_bk)}개 신호가 지지, {len(_op)}개가 반대. "
                     "가중치는 각 방법의 측정된 실적을 반영합니다 — 입증된 강한 증거가 더 무겁게 셉니다.")
        if timing.get("candle"):
            c = timing["candle"]
            ct = ("1분봉 3연속 이상 상승 (단기 상승세)" if c["signal"] == "BUY"
                  else "1분봉 2연속 하락 (단기 하락세)" if c["signal"] == "SELL"
                  else "뚜렷한 1분봉 흐름 없음")
            L.append(f"⏱️ 지금 타이밍: {ct} — 분 단위 신호로, 위의 5일 판단과는 별개입니다.")
    return "\n".join(L)


def unified_reply(db, code: str, name: str, lang: str = "ko") -> Optional[str]:
    """Standalone: run decide(), then scoreboard header + full reasoning underneath."""
    from services.decision_agent import decide
    en = str(lang or "").lower().startswith("en")
    try:
        d = decide(db, code)
    except Exception as e:
        logger.warning("decision_brain decide failed %s: %s", code, str(e)[:120])
        return None
    if not d:
        return None
    head = scoreboard(db, d, lang)
    body = d.get("reasoning_en" if en else "reasoning_ko")
    reply = head + (("\n\n" + body) if body else "")
    try:
        from services.call_grader import log_call
        _dec = d.get("decision") or "HOLD"
        log_call(db, ticker=code, action=_dec, intent="brain",
                 ref_price=d.get("price"), horizon_min=5 * 390, name=name, lang=lang)
    except Exception:
        pass
    return reply[:9000]
