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


def _hour_forecast(db, code: str, cur: Optional[float], d: dict) -> dict:
    """The next-~1-hour directional forecast for a HELD stock: UP / DOWN / FLAT, an
    expected price band (±1σ from volatility), and the HONEST measured accuracy of our
    1-hour calls. Direction = the live 5-min flow (micro_trend), backed by the fused
    decision when the flow is flat."""
    direction = "FLAT"
    try:
        from services.micro_trend import micro_read
        m = micro_read(db, code) or {}
        v = m.get("verdict")
        if v in ("UP", "DOWN"):
            direction = v
        elif d.get("score") is not None:          # flat flow → lean on the fused score
            sc = d["score"]
            direction = "UP" if sc >= 2 else "DOWN" if sc <= -2 else "FLAT"
    except Exception:
        pass
    sigma = None
    try:
        from services.cycle_scalp import _bars
        from services.intraday_setup import _vol_1h_pct
        closes = [b["close"] for b in _bars(db, code, limit=60)]
        sigma = _vol_1h_pct(closes[-30:])
    except Exception:
        pass
    sigma = sigma or 1.0
    low = round(cur * (1 - sigma / 100)) if cur else None
    high = round(cur * (1 + sigma / 100)) if cur else None
    acc = n = None
    try:
        from services.method_weights import intraday_stats
        s = intraday_stats(db) or {}
        ml = s.get("ml") or {}
        if ml.get("n"):
            acc, n = ml["acc"], ml["n"]
    except Exception:
        pass
    # 🤖 M5.6 — the SAME 1-hour AI the buy answers show (audit 2026-07-09: sell answers
    # used only micro/fused for the hour view — two different 1h brains). Decisive AI
    # (≥60/≤40) breaks a FLAT read; it never overrides a live micro UP/DOWN.
    ai_prob = None
    try:
        from services.hourly_model import prob_up_1h
        p = prob_up_1h(db, code)
        if p is not None:
            ai_prob = round(p * 100)
            if direction == "FLAT":
                direction = "UP" if ai_prob >= 60 else "DOWN" if ai_prob <= 40 else "FLAT"
    except Exception:
        pass
    return {"direction": direction, "low": low, "high": high, "sigma": round(sigma, 1),
            "acc": acc, "n": n, "ai_prob": ai_prob}


def advise(db, position: dict) -> dict[str, Any]:
    """position = {ticker, name, shares, entry_price, pnl_pct}. Returns render-ready advice."""
    from services.decision_agent import decide
    ticker = str(position.get("ticker") or "").zfill(6)
    if not position.get("ticker"):
        return {"ok": False, "reason": "no ticker"}
    # focus='sell' → the detailed report is framed for someone who HOLDS (exit timing),
    # not "should I buy" — so it reads correctly for a position question.
    d = decide(db, ticker, focus="sell")
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
    pos_ko = f"{name}" + (f" {shares}주" if shares else "") + " 보유" + (f" · 현재 {pnl:+.1f}%" if pnl is not None else "")
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

    # --- CLEAR EXIT PLAN — so the holder knows EXACTLY when to sell (no missed chance) ---
    sell_now = action in ("CUT", "TAKE_PROFIT")
    # 1-hour forecast FIRST — the plan below must AGREE with it (boss caught a
    # contradiction: '1h DOWN → sell' next to 'plan → keep holding').
    fc = _hour_forecast(db, ticker, cur, d)
    _dir = fc["direction"]

    plan_ko = ["**🎯 매매 계획 (지금 무엇을 해야 하나요?):**"]
    plan_en = ["**🎯 Your action plan (what to do right now?):**"]
    if action == "CUT":
        plan_ko += [f"· 🔴 **지금 매도(손절) 권장** — 추세가 깨져 더 내릴 위험이 큽니다. 지금 팔아 손실을 제한하세요.",
                    f"· 재진입은 저항 {_f(res)} 회복을 확인한 뒤에만."]
        plan_en += [f"· 🔴 **SELL now (cut)** — the trend is broken, more downside likely. Sell now to limit the loss.",
                    f"· Re-enter only after it reclaims resistance {_fe(res)}."]
        if _dir == "UP":
            plan_ko += ["· 팁: 1시간 내 반등 예상이니 **반등 시 조금 더 나은 가격에 매도**해도 좋아요 (기다리다 놓치진 마세요)."]
            plan_en += ["· Tip: a bounce is expected within the hour — you may **sell into the bounce at a slightly better price** (just don't wait too long)."]
    elif action in ("TAKE_PROFIT", "TAKE_PROFIT_PARTIAL"):
        _part = "일부 " if action == "TAKE_PROFIT_PARTIAL" else ""
        _partE = "part of it " if action == "TAKE_PROFIT_PARTIAL" else ""
        plan_ko += [f"· 🟢 **지금 {_part}매도(익절) 권장** — 수익 중이니 이익을 실현하세요.",
                    f"· 나머지 보유 시: {_f(near_target)} 도달하면 추가 익절 / {_f(stop_lv)} 이탈 시 전량 정리."]
        plan_en += [f"· 🟢 **SELL {_partE}now (take profit)** — you're in profit, lock it in.",
                    f"· If holding the rest: take more profit at {_fe(near_target)} / exit all if it loses {_fe(stop_lv)}."]
    elif _dir == "DOWN":
        # HOLD-family verdict but the NEXT HOUR looks down → the consistent advice is
        # 'trim now, rebuy/keep core' — not a bare '그냥 보유'.
        action = "TRIM"
        plan_ko += [f"· 🟠 **단기 하락 예상 → 일부(예: 절반) 매도해 보유를 줄이는 것을 고려** — 더 빠지면 싸게 다시 담을 수 있어요.",
                    f"· 남긴 물량: 지지 {_f(sup)} 위를 지키면 계속 보유.",
                    f"· 🔴 {_f(stop_lv)} 아래로 떨어지면 → 남은 것도 정리(손절).",
                    f"· 🟢 예상과 달리 오르면: {_f(near_target)}에서 익절."]
        plan_en += [f"· 🟠 **Short-term drop expected → consider selling part (e.g. half) to reduce your holding** — if it falls further you can buy back cheaper.",
                    f"· For what you keep: hold while it stays above support {_fe(sup)}.",
                    f"· 🔴 If it drops below {_fe(stop_lv)} → sell the rest (cut).",
                    f"· 🟢 If it rises instead: take profit at {_fe(near_target)}."]
    else:  # HOLD / HOLD_OR_ADD with flat/up hour → consistent 'hold'
        plan_ko += [f"· ✅ **보유 유지** — 가격이 지지선 {_f(sup)} 위를 지키는 동안엔 계속 보유.",
                    f"· 🟢 **익절(매도) 시점:** {_f(near_target)} 도달하면 → 수익 실현하고 파세요. (오를 때 이 가격 놓치지 마세요)",
                    f"· 🔴 **손절(매도) 시점:** {_f(stop_lv)} 아래로 떨어지면 → 즉시 팔아 손실을 막으세요."]
        plan_en += [f"· ✅ **Keep holding** — while price stays above support {_fe(sup)}.",
                    f"· 🟢 **SELL to take profit** when it reaches {_fe(near_target)} → lock in the gain (don't miss this level on the way up).",
                    f"· 🔴 **SELL to cut loss** if it drops below {_fe(stop_lv)} → sell right away to stop the bleeding."]
    plan_ko_s = "\n".join(plan_ko)
    plan_en_s = "\n".join(plan_en)
    if action == "TRIM":       # headline must match the reconciled advice
        hold_head_ko = "🟠 일부 매도(단기 축소) 고려"
        hold_head_en = "🟠 Consider trimming (short-term reduce)"

    # --- FULL detailed analysis: reuse decide()'s complete method-by-method report so the
    # holder sees HOW each method predicts (ML accuracy/expected move, analysis reasons,
    # wave levels, market/news/5-min) — same depth as 'should I buy X?'. ---
    detail_ko = d.get("reasoning_ko") or ""
    detail_en = d.get("reasoning_en") or ""

    # --- ⏱️ 1-HOUR FORECAST section (fc computed above, plan already reconciled) ---
    _lo, _hi = fc["low"], fc["high"]
    _accs_ko = (f"(우리 1시간 방향 예측 정확도: 약 {fc['acc']:.0f}% · 실측 {fc['n']}건 — "
                f"완벽하지 않으니 참고용)") if fc["acc"] else ""
    _accs_en = (f"(our 1-hour direction accuracy: ~{fc['acc']:.0f}% over {fc['n']} graded "
                f"calls — not perfect, use as a guide)") if fc["acc"] else ""
    if _dir == "DOWN":
        fc_ko = (f"**⏱️ 앞으로 1시간 예측: 하락 가능성** (예상 범위 {_f(_lo)} ~ {_f(cur)})\n"
                 f"→ 지금 값이 더 빠지기 전에 **일부/전량 매도해 보유를 줄이는 것**을 고려하세요. "
                 f"손실을 줄이고, 더 낮은 가격에서 다시 담을 기회를 노립니다. {_accs_ko}")
        fc_en = (f"**⏱️ Next ~1 hour: likely DOWN** (expected {_fe(_lo)} ~ {_fe(cur)})\n"
                 f"→ Consider **selling some/all to reduce your holding** before it drops further — "
                 f"limit the loss now and aim to buy back lower. {_accs_en}")
    elif _dir == "UP":
        if action in ("CUT", "TAKE_PROFIT", "TAKE_PROFIT_PARTIAL"):
            # selling is the verdict — an UP hour means: sell INTO the bounce, don't flip to hold
            fc_ko = (f"**⏱️ 앞으로 1시간 예측: 반등 가능성** (예상 범위 {_f(cur)} ~ {_f(_hi)})\n"
                     f"→ 매도 방침은 유지하되, **반등을 이용해 조금 더 나은 가격에 파세요.** {_accs_ko}")
            fc_en = (f"**⏱️ Next ~1 hour: a bounce is likely** (expected {_fe(cur)} ~ {_fe(_hi)})\n"
                     f"→ The sell call stands — **use the bounce to sell at a slightly better price.** {_accs_en}")
        else:
            fc_ko = (f"**⏱️ 앞으로 1시간 예측: 상승 가능성** (예상 범위 {_f(cur)} ~ {_f(_hi)})\n"
                     f"→ **지금 팔지 말고 보유하세요.** 오르면 더 높은 가격에 매도해 이익을 키울 수 있어요 "
                     f"— 목표 {_f(near_target)} 도달 시 익절. {_accs_ko}")
            fc_en = (f"**⏱️ Next ~1 hour: likely UP** (expected {_fe(cur)} ~ {_fe(_hi)})\n"
                     f"→ **Don't sell yet — hold.** If it rises you can sell higher for a bigger gain "
                     f"— take profit when it reaches {_fe(near_target)}. {_accs_en}")
    else:
        if action in ("CUT", "TAKE_PROFIT", "TAKE_PROFIT_PARTIAL"):
            fc_ko = (f"**⏱️ 앞으로 1시간 예측: 큰 변화 없음** (예상 범위 {_f(_lo)} ~ {_f(_hi)})\n"
                     f"→ 기다려도 가격이 크게 좋아질 가능성이 낮아요 — **계획대로 매도를 진행하세요.** {_accs_ko}")
            fc_en = (f"**⏱️ Next ~1 hour: little change expected** (range {_fe(_lo)} ~ {_fe(_hi)})\n"
                     f"→ Waiting is unlikely to get you a much better price — **proceed with the sell plan.** {_accs_en}")
        else:
            fc_ko = (f"**⏱️ 앞으로 1시간 예측: 큰 변화 없음** (예상 범위 {_f(_lo)} ~ {_f(_hi)})\n"
                     f"→ 급한 움직임이 없을 가능성이 커요. **그냥 보유(관망)** 하며 위 매매 계획의 "
                     f"익절/손절 가격을 지켜보세요. {_accs_ko}")
            fc_en = (f"**⏱️ Next ~1 hour: little change expected** (range {_fe(_lo)} ~ {_fe(_hi)})\n"
                     f"→ No sharp move likely. **Just hold** and watch the take-profit / cut levels "
                     f"in the plan above. {_accs_en}")
    # 🤖 same AI probability the buy answers show — one 1-hour brain everywhere
    if fc.get("ai_prob") is not None:
        fc_ko += f"\n· 🤖 AI 1시간 상승확률 {fc['ai_prob']}% (참고용 랭킹)"
        fc_en += f"\n· 🤖 AI 1-hour up-probability {fc['ai_prob']}% (ranking only)"

    _how_ko = ("**🧠 결정 엔진은 이렇게 예측해요:** 머신러닝(과거 패턴 학습)·분석(수급·호가·박스권)·"
               "파동(엘리엇/피보나치) 3가지 방법에 + 뉴스·시장 흐름·5분봉 실시간 흐름·동종 그룹까지 "
               "종합해 방향을 냅니다. 여러 신호가 **같은 방향으로 일치할 때** 신뢰도가 높아져요.")
    _how_en = ("**🧠 How the engine predicts:** it fuses 3 methods — Machine Learning (learned "
               "patterns), Analysis (order-flow/box levels), Wave (Elliott/Fibonacci) — plus news, "
               "market trend, the live 5-minute flow and the peer group. Confidence is higher when "
               "several signals **agree on the same direction.**")

    reasoning_ko = (f"**📌 {pos_ko} — {hold_head_ko}**\n\n"
                    f"{fc_ko}\n\n"
                    f"{plan_ko_s}\n\n"
                    f"**➡️ 종합 결론:** {ko}\n\n"
                    f"{_how_ko}\n\n"
                    f"───────────── 상세 분석 (각 방법이 어떻게 예측했나) ─────────────\n\n"
                    f"{detail_ko}")
    reasoning_en = (f"**📌 {pos_en} — {hold_head_en}**\n\n"
                    f"{fc_en}\n\n"
                    f"{plan_en_s}\n\n"
                    f"**➡️ Bottom line:** {en}\n\n"
                    f"{_how_en}\n\n"
                    f"───────────── Full analysis (how each method predicted) ─────────────\n\n"
                    f"{detail_en}")
    return {"ok": True, "ticker": ticker, "name": name, "action": action,
            "pnl_pct": round(pnl, 2) if pnl is not None else None, "price": cur,
            "support": sup, "resistance": res, "stop": stop_lv,
            "reasoning_ko": reasoning_ko, "reasoning_en": reasoning_en}
