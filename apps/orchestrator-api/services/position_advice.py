"""position_advice.py — Milestone 2.2: advise on a holding the user already has.

Given a parsed position (ticker, shares, entry/P&L) it fuses the 3-method `decide`
(trend + support/resistance + news) with the user's P&L to recommend
버티기(hold) / 손절(cut) / 물타기(add) / 익절(take-profit), with exact trigger prices.

Responsible guardrail: NEVER recommend 물타기(averaging down) into a broken trend —
only when support holds AND the trend is intact AND buyers are returning.
Bilingual (KO/EN). Advisory only.
"""
from __future__ import annotations

from typing import Any, Optional


def _f(v) -> str:
    try:
        return f"{int(round(float(v))):,}원"
    except Exception:
        return "-"


def _fe(v) -> str:
    try:
        return f"₩{int(round(float(v))):,}"
    except Exception:
        return "-"


def advise(db, position: dict) -> dict[str, Any]:
    """position = {ticker, name, shares, entry_price, pnl_pct}. Returns render-ready advice."""
    from services.decision_agent import decide
    ticker = str(position.get("ticker") or "").zfill(6)
    if not position.get("ticker"):
        return {"ok": False, "reason": "no ticker"}
    d = decide(db, ticker)
    name = position.get("name") or d.get("name") or ticker
    cur = d.get("price")
    tech = d.get("technicals") or {}
    sup, res = tech.get("support"), tech.get("resistance")
    wv = (d.get("method3_wave") or {}).get("verdict")
    m2 = (d.get("method2_analysis") or {}).get("signal")
    decision = (d.get("decision") or "").upper()
    flows = d.get("flows") or {}

    # derive P&L / entry from whatever the user gave
    entry = position.get("entry_price")
    pnl = position.get("pnl_pct")
    if entry and cur and pnl is None:
        pnl = (cur - entry) / entry * 100.0
    if pnl is not None and cur and not entry:
        entry = cur / (1 + pnl / 100.0)

    # trend health
    broken = (decision == "SELL") or (wv == "AVOID") or (bool(sup) and bool(cur) and cur < sup * 0.97)
    trend_ok = (decision in ("BUY", "HOLD")) and (wv != "AVOID") and (not sup or not cur or cur >= sup * 0.98)
    buyers_back = (m2 == "BUY") or (flows.get("tag") == "강력매집") or ((flows.get("foreign_5d") or 0) + (flows.get("inst_5d") or 0) > 0)
    # ACTIONABLE levels: stop capped at ~3% below current (not the far 20-day low), and a
    # realistic near-term target (nearest of resistance / +4%), so advice is tradable.
    if cur:
        stop_lv = round(max(sup * 0.98, cur * 0.97)) if sup else round(cur * 0.97)
        near_target = round(min(res, cur * 1.04)) if (res and res > cur) else round(cur * 1.04)
    else:
        stop_lv = round(sup * 0.98) if sup else None
        near_target = round(res) if res else None

    inprofit = pnl is not None and pnl >= 0.5
    inloss = pnl is not None and pnl <= -0.5

    # ---- decide the action + build KO/EN prose ----
    if inprofit:
        if trend_ok:
            action = "TAKE_PROFIT_PARTIAL"
            ko = (f"현재 +{pnl:.1f}% 수익 중이고 추세도 아직 살아 있습니다. **일부 익절 + 나머지 보유**를 권합니다 — "
                  f"다음 목표 {_f(near_target)}까지 노려보되, 손절선 {_f(stop_lv)}(약 -3%) 이탈 시 전량 정리하세요.")
            en = (f"You're up +{pnl:.1f}% and the trend is still intact. **Take partial profit and hold the rest** — "
                  f"aim for {_fe(near_target)} next, but exit fully if it drops to {_fe(stop_lv)} (~-3%).")
        else:
            action = "TAKE_PROFIT"
            ko = (f"현재 +{pnl:.1f}% 수익이지만 추세가 약해지고 있습니다. **익절(차익 실현)**을 권합니다. 재진입은 지지 {_f(sup)} 확인 후.")
            en = (f"You're up +{pnl:.1f}% but the trend is weakening. **Take profit.** Re-enter only after support {_fe(sup)} confirms.")
    elif inloss:
        if broken:
            action = "CUT"
            ko = (f"현재 {pnl:.1f}% 손실이고 추세가 이미 훼손됐습니다(방법 신호 약세/지지 이탈). **손절로 손실을 제한**하는 것을 권합니다 — "
                  f"반등해도 {_f(res)} 회복 전엔 보수적으로 보세요. ⚠️ 지금은 물타기 금지(떨어지는 칼날).")
            en = (f"You're down {pnl:.1f}% and the trend is broken (weak method signals / support lost). "
                  f"**Cut to limit the loss** — stay cautious until it reclaims {_fe(res)}. ⚠️ Do NOT average down here (falling knife).")
        else:
            if buyers_back:
                action = "HOLD_OR_ADD"
                ko = (f"현재 {pnl:.1f}% 손실이지만 추세는 유지 중이고 지지 {_f(sup)}에서 매수세가 들어오고 있습니다. "
                      f"**보유(버티기)**하되, 지지 {_f(sup)} 확인되면 **소량 물타기로 평단 낮추기**도 가능합니다. "
                      f"손절선 {_f(stop_lv)}(약 -3%) 이탈 시 정리. 추세 깨지면 물타기 금지.")
                en = (f"You're down {pnl:.1f}% but the trend holds and buyers are stepping in at support {_fe(sup)}. "
                      f"**Hold**; if support {_fe(sup)} confirms you may **add a small amount to lower your average**. "
                      f"Stop at {_fe(stop_lv)} (~-3%). No averaging if the trend breaks.")
            else:
                action = "HOLD"
                ko = (f"현재 {pnl:.1f}% 손실이나 추세는 아직 유지 중입니다. **보유(버티기)** — 지지 {_f(sup)}를 지키는지 지켜보세요. "
                      f"손절선 {_f(stop_lv)} 이탈 시에만 정리. 아직 물타기는 이르니 지지 확인 후 판단하세요.")
                en = (f"You're down {pnl:.1f}% but the trend still holds. **Hold** — watch whether support {_fe(sup)} holds. "
                      f"Cut only if it loses {_fe(stop_lv)}. Too early to average down — wait for support to confirm.")
    else:
        action = "HOLD"
        _p = f"{pnl:+.1f}%" if pnl is not None else "약보합"
        ko = (f"현재 손익 {_p} 수준입니다. 방향이 뚜렷하지 않아 **보유·관망**을 권합니다 — 저항 {_f(res)} 돌파 시 추가, 지지 {_f(sup)} 이탈 시 정리.")
        en = (f"You're around {_p}. Direction is unclear — **hold / watch**: add above resistance {_fe(res)}, cut below support {_fe(sup)}.")

    hold_head_ko = {"CUT": "🔴 손절 권장", "HOLD_OR_ADD": "🟡 보유 (조건부 물타기)", "HOLD": "🟡 보유",
                    "TAKE_PROFIT": "🟢 익절 권장", "TAKE_PROFIT_PARTIAL": "🟢 일부 익절 + 보유"}.get(action, "🟡 보유")
    hold_head_en = {"CUT": "🔴 Cut (stop-loss)", "HOLD_OR_ADD": "🟡 Hold (add only if support holds)",
                    "HOLD": "🟡 Hold", "TAKE_PROFIT": "🟢 Take profit",
                    "TAKE_PROFIT_PARTIAL": "🟢 Take partial profit + hold"}.get(action, "🟡 Hold")
    from services.stock_resolver import display_name_en
    name_en = display_name_en(ticker)
    shares = position.get("shares")
    pos_ko = f"{name} {shares}주 보유" + (f" · 현재 {pnl:+.1f}%" if pnl is not None else "")
    pos_en = f"Holding {name_en}" + (f" {shares} shares" if shares else "") + (f" · {pnl:+.1f}%" if pnl is not None else "")

    # --- 3-method breakdown (each method's read) so the advice shows its reasoning ---
    m1c = (d.get("method1_ml") or {}).get("call") or "HOLD"
    m1_ko = {"BUY": "매수", "SELL": "매도", "HOLD": "보유"}.get(m1c, "보유")
    m1_why = {"BUY": "시장 대비 상대강세 예측", "SELL": "시장 대비 상대약세 예측",
              "HOLD": "뚜렷한 우위 없음(신호 약함)"}.get(m1c, "신호 약함")
    m2_ko = {"BUY": "매수 우위", "SELL": "매도 우위", "WATCH": "관망", "HOLD": "관망"}.get((m2 or "").upper(), "관망")
    m2_reasons = " · ".join(((d.get("method2_analysis") or {}).get("reasons") or [])[:2])
    wv_ko = {"BUY": "매수", "WATCH": "관망", "AVOID": "회피"}.get((wv or "").upper(), "데이터 없음")
    wv_why = {"BUY": "강한 파동 후 눌림목 매수권", "WATCH": "상승 파동이나 아직 매수 자리 아님",
              "AVOID": "추세 약화/무효"}.get((wv or "").upper(), "유효 파동 미검출")
    techk = (d.get("technicals") or {}).get("summary_ko", "중립")
    m1e = {"BUY": "outperformance expected", "SELL": "underperformance expected",
           "HOLD": "no clear edge (weak signal)"}.get(m1c, "weak signal")
    m2e = {"BUY": "buy-side", "SELL": "sell-side", "WATCH": "neutral", "HOLD": "neutral"}.get((m2 or "").upper(), "neutral")
    m2re = " · ".join(((d.get("method2_analysis") or {}).get("reasons_en") or [])[:2])
    wve = {"BUY": "buy (deep-pullback zone)", "WATCH": "watch (not a buy yet)",
           "AVOID": "avoid (trend weak)"}.get((wv or "").upper(), "no data")
    teche = (d.get("technicals") or {}).get("summary_en", "neutral")

    methods_ko = ("**방법별 진단**\n"
                  f"🤖 방법 1 (머신러닝): **{m1_ko}** — {m1_why}\n"
                  f"📈 방법 2 (분석·수급/호가): **{m2_ko}**" + (f" — {m2_reasons}" if m2_reasons else "") + "\n"
                  f"🌊 방법 3 (파동·엘리엇): **{wv_ko}** — {wv_why}\n"
                  f"📉 기술적: {techk}")
    methods_en = ("**Method-by-method**\n"
                  f"🤖 Method 1 (ML): **{m1c}** — {m1e}\n"
                  f"📈 Method 2 (Analysis): **{m2e}**" + (f" — {m2re}" if m2re else "") + "\n"
                  f"🌊 Method 3 (Wave): **{wve}**\n"
                  f"📉 Technicals: {teche}")

    reasoning_ko = (f"**📌 {pos_ko} — {hold_head_ko}**\n\n"
                    f"{methods_ko}\n\n"
                    f"**➡️ 종합 결론:** {ko}\n\n"
                    f"※ 3가지 방법(머신러닝·분석·파동) + 뉴스·기술적 지표를 종합한 참고 의견이며, 투자 권유가 아닙니다.")
    reasoning_en = (f"**📌 {pos_en} — {hold_head_en}**\n\n"
                    f"{methods_en}\n\n"
                    f"**➡️ Bottom line:** {en}\n\n"
                    f"※ Synthesis of the 3 methods + news/technicals. Reference only, not investment advice.")
    return {"ok": True, "ticker": ticker, "name": name, "action": action,
            "pnl_pct": round(pnl, 2) if pnl is not None else None, "price": cur,
            "support": sup, "resistance": res, "stop": stop_lv,
            "reasoning_ko": reasoning_ko, "reasoning_en": reasoning_en}
