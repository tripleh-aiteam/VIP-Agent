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


# ---- live point-in-time reads of the two scalp strategies (stateless, from
#      1-min candles) so the advice can show what ALL THREE algorithms say now ---- #
def _candle_now(code: str) -> tuple[str, str, str]:
    """Algo2 · Candle 3-2 verdict RIGHT NOW → (signal, ko, en)."""
    try:
        from services.scalp_trader import _streaks_1m
        up, dn, n = _streaks_1m(code)
        if not n:
            return "WAIT", "1분봉 데이터 대기", "waiting for 1-min data"
        if up >= 3:
            return "BUY", f"1분봉 {up}연속 양봉 → 매수 신호", f"{up} up 1-min candles → BUY"
        if dn >= 2:
            return "SELL", f"1분봉 {dn}연속 음봉 → 매도 신호", f"{dn} down 1-min candles → SELL"
        return "WAIT", f"양봉 {up}·음봉 {dn} — 3연속 양봉 대기", f"up {up}·down {dn} — waiting for 3 up"
    except Exception:
        return "WAIT", "데이터 없음", "no data"


def _ripple_now(code: str) -> tuple[str, str, str]:
    """Algo2 · Ripple verdict RIGHT NOW (bounce off a recent low) → (signal, ko, en)."""
    try:
        from services.scalp_trader import _candles_1m
        cs = _candles_1m(code, n=12)
        closes = [b.get("close") for b in cs if b.get("close")]
        if len(closes) < 4:
            return "WAIT", "1분봉 데이터 대기", "waiting for 1-min data"
        lo = min(closes)
        if lo <= 0:
            return "WAIT", "데이터 없음", "no data"
        bounce = (closes[-1] / lo - 1) * 100
        rising = closes[-1] > closes[-2] > closes[-3]
        if rising and 0.10 <= bounce <= 0.45:
            return "BUY", f"저점 대비 +{bounce:.2f}% 반등·연속 상승 → 매수", \
                   f"+{bounce:.2f}% off the low, rising → BUY"
        if bounce > 0.45:
            return "WAIT", f"이미 +{bounce:.2f}% 상승 — 추격 금지(관망)", \
                   f"already +{bounce:.2f}% up — no chase (wait)"
        return "WAIT", f"저점 대비 +{bounce:.2f}% — 반등 시작(+0.10%~) 대기", \
               f"+{bounce:.2f}% off the low — waiting for the turn (+0.10%~)"
    except Exception:
        return "WAIT", "데이터 없음", "no data"


def three_algo_block(db, d: dict[str, Any], lang: str = "ko") -> str:
    """Show what ALL THREE approaches say (boss 2026-07-20: don't answer from Algo 1
    only). 🤖 Algorithm 1 = the daily 5-day decide() verdict; ⚡ Ripple + 🕯️ Candle
    = the two Algorithm-2 scalp strategies read live off the 1-min chart."""
    en = str(lang or "").lower().startswith("en")
    code = str(d.get("ticker") or "").zfill(6)
    a1 = (d.get("decision") or "HOLD").upper()
    a1_conf = d.get("confidence") or "low"
    rp_sig, rp_ko, rp_en = _ripple_now(code)
    cd_sig, cd_ko, cd_en = _candle_now(code)
    ic = _ICON
    if en:
        conf_e = {"high": "high", "medium": "medium", "low": "low"}.get(a1_conf, a1_conf)
        return (
            "🧭 What each algorithm says right now:\n"
            f"  🤖 Algorithm 1 (daily · 5-day, chart+수급+news+ML): {ic.get(a1,'⚪')} {a1} (confidence {conf_e})\n"
            f"  ⚡ Algorithm 2 · Ripple (scalp · minutes): {ic.get(rp_sig,'⚪')} {rp_sig} — {rp_en}\n"
            f"  🕯️ Algorithm 2 · Candle 3-2 (1-min): {ic.get(cd_sig,'⚪')} {cd_sig} — {cd_en}\n"
            "  → Different horizons: Algo 1 = hold days; Ripple/Candle = in-and-out in minutes. "
            "They can disagree on purpose — pick the one that matches how long you'll hold.")
    conf_k = {"high": "높음", "medium": "보통", "low": "낮음"}.get(a1_conf, a1_conf)
    _vk = _VOTE_KO
    return (
        "🧭 지금 각 알고리즘의 판단:\n"
        f"  🤖 알고리즘 1 (일봉·5일, 차트+수급+뉴스+ML): {ic.get(a1,'⚪')} {_vk.get(a1,a1)} (신뢰도 {conf_k})\n"
        f"  ⚡ 알고리즘 2 · 잔물결 (초단타·분 단위): {ic.get(rp_sig,'⚪')} {_vk.get(rp_sig,rp_sig)} — {rp_ko}\n"
        f"  🕯️ 알고리즘 2 · 캔들 3-2 (1분봉): {ic.get(cd_sig,'⚪')} {_vk.get(cd_sig,cd_sig)} — {cd_ko}\n"
        "  → 시간 지평이 다릅니다: 알고1 = 며칠 보유 / 잔물결·캔들 = 분 단위 진입·청산. "
        "서로 다를 수 있어요 — 얼마나 들고 갈지에 맞는 걸 고르세요.")


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
        L.append("📈 Based on real-time chart analysis (1-min / 5-min / daily) fused across "
                 "our algorithms — not a hunch. The vote:")
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
        L.append("📈 실시간 차트 분석(1분봉·5분봉·일봉)을 여러 알고리즘으로 종합한 결과입니다 "
                 "— 감이 아니라 데이터 기반. 투표 내역:")
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
