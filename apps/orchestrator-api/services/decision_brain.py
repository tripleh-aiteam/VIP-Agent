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
                             "signal": "BUY" if up >= 3 else "SELL" if dn >= 3 else "HOLD"}
    except Exception:
        pass
    return out


# ---- live point-in-time reads of the two scalp strategies (stateless, from
#      1-min candles) so the advice can show what ALL THREE algorithms say now ---- #
def _candle_now(code: str) -> tuple[str, str, str]:
    """Algorithm 3 · Candle verdict RIGHT NOW (3 up → buy, 3 down → sell)."""
    try:
        from services.scalp_trader import _streaks_1m
        up, dn, n = _streaks_1m(code)
        if not n:
            return "WAIT", "1분봉 데이터 대기", "waiting for 1-min data"
        if up >= 3:
            return "BUY", f"1분봉 {up}연속 양봉 → 매수 신호", f"{up} up 1-min candles → BUY"
        if dn >= 3:
            return "SELL", f"1분봉 {dn}연속 음봉 → 매도 신호", f"{dn} down 1-min candles → SELL"
        return "WAIT", f"양봉 {up}·음봉 {dn} — 3연속 대기", f"up {up}·down {dn} — waiting for 3 in a row"
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


_DIV = "━━━━━━━━━━━━━━━━━━━━"


def _ripple_detail(code: str, lang: str) -> list[str]:
    """Ripple's strategy + current live read + what triggers a buy (boss wants detail)."""
    en = str(lang or "").lower().startswith("en")
    sig, ko, en_r = _ripple_now(code)
    try:
        from services.scalp_trader import _candles_1m
        cs = _candles_1m(code, n=12)
        closes = [b.get("close") for b in cs if b.get("close")]
        cur = closes[-1] if closes else None
        lo = min(closes) if closes else None
        bounce = round((cur / lo - 1) * 100, 2) if (cur and lo) else None
    except Exception:
        bounce = None
    if en:
        out = ["   • How it works: buys when price lifts +0.10~0.45% off a recent low with "
               "consecutive rises → sells +0.4% (net +0.17% after fees) / −1% stop / flat 15:18."]
        if bounce is not None:
            out.append(f"   • Right now: {bounce:+.2f}% off the recent low — "
                       + ("in the buy window, watching for the rise to confirm." if 0.10 <= bounce <= 0.45
                          else "already past the +0.45% chase limit, so it waits for a pullback." if bounce > 0.45
                          else "not enough of a bounce yet (needs +0.10%)."))
        else:
            out.append("   • Right now: 1-min data unavailable (Kiwoom feed) — waiting.")
        out.append("   • Buys when: a fresh +0.10~0.45% bounce starts. Best on choppy, ranging tape.")
        return out
    out = ["   • 작동 방식: 최근 저점에서 +0.10~0.45% 반등하며 연속 상승하면 매수 → +0.4% 익절"
           "(수수료 후 실속 +0.17%) / −1% 손절 / 15:18 정리."]
    if bounce is not None:
        out.append(f"   • 지금: 최근 저점 대비 {bounce:+.2f}% — "
                   + ("매수 구간, 상승 확정 대기 중." if 0.10 <= bounce <= 0.45
                      else "이미 +0.45% 추격 한도를 넘어 눌림을 기다립니다." if bounce > 0.45
                      else "아직 반등이 부족합니다(+0.10% 필요)."))
    else:
        out.append("   • 지금: 1분봉 데이터 없음(키움 피드) — 대기 중.")
    out.append("   • 매수 조건: 새로운 +0.10~0.45% 반등 시작. 박스권·출렁이는 장에서 유리.")
    return out


def _candle_detail(code: str, lang: str) -> list[str]:
    """Algorithm 3 candle strategy + current 1-min streak + what triggers buy/sell."""
    en = str(lang or "").lower().startswith("en")
    try:
        from services.scalp_trader import _streaks_1m
        up, dn, n = _streaks_1m(code)
    except Exception:
        up = dn = n = 0
    if en:
        out = ["   • How it works: 3 up 1-min candles → BUY · 3 down candles → SELL · "
               "−1% stop · flat 15:18. Also checks the partner stock + volume."]
        if n:
            out.append(f"   • Right now: {up} up / {dn} down candles in a row — "
                       + ("BUY signal is live." if up >= 3 else "SELL signal is live." if dn >= 3
                          else f"needs {3-up} more up candle(s) to buy." if up else "no clear streak yet."))
        else:
            out.append("   • Right now: 1-min candle data unavailable (Kiwoom feed) — waiting.")
        out.append("   • Buys when: 3 green 1-min candles in a row. Best on a clean, trending push.")
        return out
    out = ["   • 작동 방식: 1분봉 3연속 양봉 → 매수 · 3연속 음봉 → 매도 · −1% 손절 · "
           "15:18 정리. 짝꿍 종목과 거래량도 함께 확인합니다."]
    if n:
        out.append(f"   • 지금: 양봉 {up}개 / 음봉 {dn}개 연속 — "
                   + ("매수 신호 발생." if up >= 3 else "매도 신호 발생." if dn >= 3
                      else f"매수까지 양봉 {3-up}개 더 필요." if up else "뚜렷한 연속 흐름 없음."))
    else:
        out.append("   • 지금: 1분봉 데이터 없음(키움 피드) — 대기 중.")
    out.append("   • 매수 조건: 1분봉 3연속 양봉. 깔끔한 추세 상승에서 유리.")
    return out


def _algo1_synthesis(d: dict[str, Any], lang: str) -> str:
    """2-3 sentence plain explanation of WHY Algorithm 1 reached its decision."""
    en = str(lang or "").lower().startswith("en")
    sigs = [s for s in (d.get("signals_breakdown") or []) if s.get("vote") != "HOLD"]
    nb = sum(1 for s in sigs if s["vote"] == "BUY")
    ns = sum(1 for s in sigs if s["vote"] == "SELL")
    dec = (d.get("decision") or "HOLD").upper()
    tech = d.get("technicals") or {}
    flows = d.get("flows") or {}
    an = d.get("method2_analysis") or {}
    reasons = (an.get("reasons") or [])[:3]
    tsum = tech.get("summary_en" if en else "summary_ko") or ""
    ftag = (flows.get("tag_en") if en else flows.get("tag")) or ""
    if en:
        head = (f"{nb} method(s) lean buy, {ns} lean sell. ")
        why = (f"Chart: {tsum}. " if tsum else "") + (f"Flows: {ftag}. " if ftag else "")
        if reasons:
            why += "Order-book/box: " + "; ".join(reasons) + ". "
        concl = {"BUY": "The weight of evidence supports buying.",
                 "SELL": "The weight of evidence says reduce/avoid.",
                 "HOLD": "Signals conflict, so the brain waits for a clearer setup."}[dec]
        return "📌 Summary: " + head + why + concl
    head = f"매수 성향 {nb}개 · 매도 성향 {ns}개. "
    why = (f"차트: {tsum}. " if tsum else "") + (f"수급: {ftag}. " if ftag else "")
    if reasons:
        why += "호가/박스권: " + "; ".join(reasons) + ". "
    concl = {"BUY": "증거의 무게가 매수를 지지합니다.",
             "SELL": "증거의 무게가 비중 축소/회피를 가리킵니다.",
             "HOLD": "신호가 엇갈려 더 뚜렷한 자리를 기다립니다."}[dec]
    return "📌 종합 해설: " + head + why + concl


def clean_recommendation(db, d: dict[str, Any], lang: str = "ko") -> str:
    """The boss's exact recommendation layout (2026-07-20): ONE-line final decision →
    Algorithm 1 (decision + 1h prediction + ML/news/YT/chart/Kiwoom/orderbook/wave
    detail) → Algorithm 2 Ripple (decision + detail) → Algorithm 2 Candle (decision
    + detail) → final answer from the 3 cases. Clean and short — nothing more."""
    en = str(lang or "").lower().startswith("en")
    code = str(d.get("ticker") or "").zfill(6)
    name = d.get("name") or code
    dec = (d.get("decision") or "HOLD").upper()
    conf = d.get("confidence") or "low"
    conf_ko = {"high": "높음", "medium": "보통", "low": "낮음"}.get(conf, "낮음")
    vk = {"BUY": "매수", "SELL": "매도", "HOLD": "보유/관망", "WAIT": "대기"}
    ve = {"BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD", "WAIT": "WAIT"}
    ic = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪", "WAIT": "⚪"}

    def _v(x):
        return (ve if en else vk).get(x, x)

    ml = d.get("method1_ml") or {}
    an = d.get("method2_analysis") or {}
    wv = d.get("method3_wave") or {}
    news = d.get("news") or {}
    flows = d.get("flows") or {}
    tech = d.get("technicals") or {}
    yt = d.get("youtube") or {}
    setup = d.get("intraday_setup") or {}
    tier = d.get("hourly_tier") or {}
    ai1h = setup.get("ai_1h_prob")
    if ai1h is None:                 # boss wants Algo-1's 1-hour prediction shown always
        try:
            from services.hourly_model import prob_up_1h
            _pu = prob_up_1h(db, code)
            if _pu is not None:
                ai1h = round(float(_pu) * 100) if _pu <= 1 else round(float(_pu))
        except Exception:
            ai1h = None

    def _pl(v):
        try:
            return f"{int(v):,}"
        except Exception:
            return "-"

    def _sc(x):
        s = x.get("score") if isinstance(x, dict) else x
        return "🟢" if (s or 0) > 0 else "🔴" if (s or 0) < 0 else "⚪"

    L: list[str] = []
    if en:
        # 1) one-line final
        L.append(f"✅ Our final decision: **{ve.get(dec, dec)}** — {name} (confidence {conf})")
        # 2) Algorithm 1
        L.append(_DIV)
        ml_call = (ml.get("call") or "HOLD").upper()
        h1 = f" · 1-hour prediction: {'UP' if (ai1h or 0) >= 50 else 'DOWN'} {ai1h}%" if ai1h is not None else ""
        L.append(f"🤖 Algorithm 1 — combined brain (ML · News · YouTube · Chart · Kiwoom · Orderbook · Wave)")
        L.append(f"Decision: {ic.get(dec,'⚪')} {ve.get(dec,dec)}{h1}")
        L.append("Detail:")
        L.append(f" • ML (M1): {_v(ml_call)} — accuracy {ml.get('accuracy_pct','n/a')}%"
                 + (f", 5-day ±{abs(ml['expected_move_pct'])}%" if ml.get('expected_move_pct') is not None else ""))
        L.append(f" • Wave (Elliott/Fibonacci): {_v((wv.get('verdict') or 'HOLD').upper())}"
                 + (f" — entry ₩{_pl(wv.get('entry'))}/stop ₩{_pl(wv.get('stop'))}/target ₩{_pl(wv.get('target'))}" if wv.get('entry') else ""))
        L.append(f" • Chart/Technicals: {_sc(tech)} " + (tech.get("summary_en") or "neutral")
                 + (f" (support ₩{_pl(tech.get('support'))} · resistance ₩{_pl(tech.get('resistance'))})" if tech.get('support') else ""))
        L.append(f" • Kiwoom supply/demand: {_sc(flows)} " + (flows.get("tag_en") or flows.get("tag") or "neutral"))
        L.append(f" • News: {_sc(news)} " + (f"{news.get('count',0)} items" if news.get('count') else "neutral"))
        L.append(f" • YouTube: {_sc(yt)} " + (f"{yt.get('count',0)} mentions" if yt.get('count') else "neutral"))
        L.append(_algo1_synthesis(d, lang))
        # 3) Ripple
        rp_sig, rp_ko, rp_en = _ripple_now(code)
        L.append(_DIV)
        L.append("⚡ Algorithm 2 · Ripple (scalp, minutes)")
        L.append(f"Decision: {ic.get(rp_sig,'⚪')} {ve.get(rp_sig,rp_sig)} — {rp_en}")
        L.extend(_ripple_detail(code, lang))
        # 4) Candle
        cd_sig, cd_ko, cd_en = _candle_now(code)
        L.append(_DIV)
        L.append("🕯️ Algorithm 3 · Candle (1-min chart)")
        L.append(f"Decision: {ic.get(cd_sig,'⚪')} {ve.get(cd_sig,cd_sig)} — {cd_en}")
        L.extend(_candle_detail(code, lang))
        # 5) final from the 3
        L.append(_DIV)
        L.append("🎯 Final answer (from the 3 cases):")
        L.append(_synthesis_en(dec, rp_sig, cd_sig, name))
    else:
        L.append(f"✅ 최종 결정: **{vk.get(dec, dec)}** — {name} (신뢰도 {conf_ko})")
        L.append(_DIV)
        ml_call = (ml.get("call") or "HOLD").upper()
        h1 = f" · 1시간 예측: {'상승' if (ai1h or 0) >= 50 else '하락'} {ai1h}%" if ai1h is not None else ""
        L.append("🤖 알고리즘 1 — 종합 브레인 (ML · 뉴스 · 유튜브 · 차트 · 키움 · 호가 · 파동)")
        L.append(f"결정: {ic.get(dec,'⚪')} {vk.get(dec,dec)}{h1}")
        L.append("상세 설명:")
        L.append(f" • 머신러닝(M1): {_v(ml_call)} — 정확도 {ml.get('accuracy_pct','n/a')}%"
                 + (f", 5일 예상 ±{abs(ml['expected_move_pct'])}%" if ml.get('expected_move_pct') is not None else ""))
        L.append(f" • 파동(엘리엇/피보나치): {_v((wv.get('verdict') or 'HOLD').upper())}"
                 + (f" — 진입 ₩{_pl(wv.get('entry'))}/손절 ₩{_pl(wv.get('stop'))}/목표 ₩{_pl(wv.get('target'))}" if wv.get('entry') else ""))
        L.append(f" • 차트/기술: {_sc(tech)} " + (tech.get("summary_ko") or "중립")
                 + (f" (지지 ₩{_pl(tech.get('support'))} · 저항 ₩{_pl(tech.get('resistance'))})" if tech.get('support') else ""))
        L.append(f" • 키움 수급: {_sc(flows)} " + (flows.get("tag") or "중립"))
        L.append(f" • 뉴스: {_sc(news)} " + (f"{news.get('count',0)}건" if news.get('count') else "중립"))
        L.append(f" • 유튜브: {_sc(yt)} " + (f"{yt.get('count',0)}건 언급" if yt.get('count') else "중립"))
        L.append(_algo1_synthesis(d, lang))
        rp_sig, rp_ko, rp_en = _ripple_now(code)
        L.append(_DIV)
        L.append("⚡ 알고리즘 2 · 잔물결 (초단타·분 단위)")
        L.append(f"결정: {ic.get(rp_sig,'⚪')} {vk.get(rp_sig,rp_sig)} — {rp_ko}")
        L.extend(_ripple_detail(code, lang))
        cd_sig, cd_ko, cd_en = _candle_now(code)
        L.append(_DIV)
        L.append("🕯️ 알고리즘 3 · 캔들 (1분봉)")
        L.append(f"결정: {ic.get(cd_sig,'⚪')} {vk.get(cd_sig,cd_sig)} — {cd_ko}")
        L.extend(_candle_detail(code, lang))
        L.append(_DIV)
        L.append("🎯 종합 최종 답변 (3가지 종합):")
        L.append(_synthesis_ko(dec, rp_sig, cd_sig, name))
    return "\n".join(L)


def _synthesis_ko(a1: str, rp: str, cd: str, name: str) -> str:
    scalp_buy = rp == "BUY" or cd == "BUY"
    scalp_sell = rp == "SELL" or cd == "SELL"
    if a1 == "BUY" and scalp_buy:
        return f"중기(알고1)와 단기 신호가 모두 매수 → 지금 진입 자리로 좋습니다."
    if a1 == "BUY" and not scalp_buy:
        return f"중기(알고1)는 매수지만 단기 진입 타이밍은 대기 중 → 며칠 보유 관점이면 지금 매수 가능, 초단타면 눌림 후 진입하세요."
    if a1 == "SELL" and scalp_sell:
        return f"중기·단기 모두 매도/하락 신호 → 보유 중이면 정리, 신규 매수는 피하세요."
    if a1 == "SELL":
        return f"중기(알고1)는 매도 우세 → 신규 매수는 권하지 않습니다. 단기 반등이 있어도 리스크가 큽니다."
    # HOLD anchor
    if scalp_buy:
        return f"중기(알고1)는 관망이나 단기 전략에서 매수 신호 → 초단타 진입은 가능하되 짧게, 손절 -1% 지키세요."
    return f"세 방법 모두 뚜렷한 신호가 없어 관망이 최종 결론입니다 — 자리가 잡히면 다시 물어보세요."


def _synthesis_en(a1: str, rp: str, cd: str, name: str) -> str:
    scalp_buy = rp == "BUY" or cd == "BUY"
    scalp_sell = rp == "SELL" or cd == "SELL"
    if a1 == "BUY" and scalp_buy:
        return "Mid-term (Algo 1) and short-term signals both say BUY → a good entry right now."
    if a1 == "BUY" and not scalp_buy:
        return "Algo 1 (mid-term) says BUY but the short-term timing is still waiting → buy now if you'll hold days; if scalping, wait for a dip to enter."
    if a1 == "SELL" and scalp_sell:
        return "Both mid- and short-term point down → trim if you hold, avoid new buys."
    if a1 == "SELL":
        return "Algo 1 (mid-term) leans SELL → a new buy isn't advised; short-term bounces carry real risk."
    if scalp_buy:
        return "Algo 1 is neutral but a short-term strategy fires BUY → a quick scalp is possible, keep it small with a −1% stop."
    return "No clear signal from any of the three — HOLD is the conclusion. Ask again once a setup forms."


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
            f"  🕯️ Algorithm 3 · Candle (1-min): {ic.get(cd_sig,'⚪')} {cd_sig} — {cd_en}\n"
            "  → Different horizons: Algo 1 = hold days; Ripple/Candle = in-and-out in minutes. "
            "They can disagree on purpose — pick the one that matches how long you'll hold.")
    conf_k = {"high": "높음", "medium": "보통", "low": "낮음"}.get(a1_conf, a1_conf)
    _vk = _VOTE_KO
    return (
        "🧭 지금 각 알고리즘의 판단:\n"
        f"  🤖 알고리즘 1 (일봉·5일, 차트+수급+뉴스+ML): {ic.get(a1,'⚪')} {_vk.get(a1,a1)} (신뢰도 {conf_k})\n"
        f"  ⚡ 알고리즘 2 · 잔물결 (초단타·분 단위): {ic.get(rp_sig,'⚪')} {_vk.get(rp_sig,rp_sig)} — {rp_ko}\n"
        f"  🕯️ 알고리즘 3 · 캔들 (1분봉): {ic.get(cd_sig,'⚪')} {_vk.get(cd_sig,cd_sig)} — {cd_ko}\n"
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
                  else "3 down 1-min candles (short-term falling)" if c["signal"] == "SELL"
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
                  else "1분봉 3연속 하락 (단기 하락세)" if c["signal"] == "SELL"
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
