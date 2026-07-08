"""day_trade.py — intraday day-trade FEASIBILITY answer.

Answers the user's morning question directly: "I want to buy {stock} near its intraday
bottom and sell today for +{target}%. Feasible? Where's my stop?" — using the day's real
volatility baseline (minute candles) + the remembered order-book support wall.

Honest by construction: the verdict is driven by whether today's measured volatility leaves
room for the target after a sensible stop. No promises — a feasibility read, not a guarantee.
"""
from __future__ import annotations

from typing import Any, Optional


def _daily_vol_fallback(db, tk: str) -> Optional[dict]:
    """When the live minute collector is OFF, approximate today's volatility budget from
    recent DAILY candles (ATR%) + the live price — clearly labelled as a fallback."""
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT high, low, close FROM raw_daily_prices WHERE ticker=:t "
        "ORDER BY date DESC LIMIT 15"), {"t": tk}).fetchall()
    if len(rows) < 5:
        return None
    trs = [float(r.high) - float(r.low) for r in rows if r.high and r.low]
    closes = [float(r.close) for r in rows if r.close]
    if not trs or not closes:
        return None
    atr_pct = round(sum(trs) / len(trs) / closes[0] * 100, 2)   # avg daily range %
    live = None
    try:
        from services.assistant_agent import _live_price_for_code
        from services.stock_resolver import display_name
        q = _live_price_for_code(tk, display_name(tk))
        live = float(q["price"]) if q and q.get("price") else None
    except Exception:
        pass
    last = live or closes[0]
    return {"available": True, "source": "daily_fallback", "last": last,
            "day_open": last, "day_high": last, "day_low": round(last * (1 - atr_pct / 200)),
            "expected_day_move_pct": atr_pct, "realized_range_pct": 0, "pos_in_range": 50}


def scalp_signal(db, ticker: str, target_pct: float = 1.0,
                 with_backdrop: bool = False) -> dict[str, Any]:
    """M3.2 — live scalp read: 진입 NOW vs 대기 + 매수가/목표(+target%)/손절 + 예상 보유시간 +
    가능성, gated by Method 1 & 3 bias (don't scalp-long a bearish stock). Falls back to
    daily volatility (labelled) when the minute collector is off.

    with_backdrop=True appends a compact 3-method line (M1/M2/M3) so the day-trade answer is
    tied to the decision phase. OFF by default so the watchlist (loops this) stays fast."""
    from services.minute_bars import intraday_vol
    from services.orderbook_memory import read_memory
    from services.stock_resolver import display_name, display_name_en

    tk = str(ticker).zfill(6)
    name = display_name(tk); name_en = display_name_en(tk)
    v = intraday_vol(db, tk)
    collector_off = not v.get("available")
    if collector_off:
        v = _daily_vol_fallback(db, tk)
        if not v:
            return {"ticker": tk, "name": name, "feasible": "unknown",
                    "reasoning_ko": "실시간 분봉·일봉 데이터가 모두 부족합니다.",
                    "reasoning_en": "No live minute or daily data available."}

    # watchdog — bars exist but the collector DIED mid-session: warn on stale minutes
    stale_min = None
    if not collector_off:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            as_of = datetime.strptime(str(v.get("as_of"))[:16], "%Y-%m-%d %H:%M")
            now_kst = datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
            age = (now_kst - as_of).total_seconds() / 60
            from services.assistant_agent import _kr_market_open_now
            if _kr_market_open_now() and age > 10:
                stale_min = round(age)
        except Exception:
            pass

    last = v["last"]; exp_move = v.get("expected_day_move_pct") or 0
    realized = v.get("realized_range_pct") or 0; pos = v.get("pos_in_range")

    # support = nearest large bid wall below price (remembered), else day low
    wall = None
    mem = {}
    try:
        mem = read_memory(db, tk, depth=30)
        walls = [b for b in mem.get("bids", []) if b.get("is_large") and b["price"] <= last]
        wall = max(walls, key=lambda x: x["price"]) if walls else None
    except Exception:
        pass
    support = wall["price"] if wall else v.get("day_low") or round(last * 0.99)
    buy_lo = support; buy_hi = round(support * 1.002)
    target_price = round(buy_hi * (1 + target_pct / 100)); stop_price = round(support * 0.997)
    rr = round((target_price - buy_hi) / (buy_hi - stop_price), 2) if buy_hi > stop_price else None

    # feasibility from today's volatility budget
    if exp_move >= target_pct * 2.5:
        feasible = "yes"
    elif exp_move >= target_pct * 1.3:
        feasible = "marginal"
    else:
        feasible = "unlikely"
    est_min = max(10, min(240, round(target_pct * 390 / exp_move))) if exp_move else None

    # Method 1 & 3 bias gate — don't recommend a scalp-LONG when the trend is bearish
    ml_adv = wv = None
    try:
        from services import prediction_service as ps
        ml_adv = (ps.get_ticker(db, tk) or {}).get("advice")
    except Exception:
        pass
    try:
        from services.wave_method import wave_for
        wv = (wave_for(db, tk) or {}).get("verdict")
    except Exception:
        pass
    bias_bearish = (ml_adv == "SELL") or (wv == "AVOID")
    near_bottom = pos is not None and pos <= 35
    in_zone = last <= buy_hi * 1.002          # price already AT/below the buy zone → enter now

    if bias_bearish:
        entry = "AVOID"
    elif feasible == "unlikely":
        entry = "SKIP"
    elif near_bottom or in_zone:
        entry = "ENTER"
    else:
        entry = "WAIT"

    # ---- MARKET-REGIME VETO (2026-07-07 live forward test: 3 ENTERs advised on a −4.5%
    # KOSPI morning → 2 stops hit; the one refusal was the right call). Index crashing →
    # refuse NEW long scalps outright, like the wave method's index-plunge filter. ----
    regime = None
    try:
        from services.market_regime import crash_veto
        regime = crash_veto()
    except Exception:
        regime = None
    if regime and regime.get("veto") and entry in ("ENTER", "WAIT", "SKIP"):
        entry = "AVOID"

    # ---- B1: live order-flow check (PC snapshot, fresh ≤4min) — the 매수/매도 tape
    # either CONFIRMS the entry or vetoes it. Deterministic, always shown with reasons.
    rt = None
    try:
        from services.trading_brief import realtime_for
        rt = realtime_for(tk, db=db)
    except Exception:
        pass
    ask_wall = None
    try:
        thr_ok = lambda a: a.get("is_large") and last < a["price"] <= target_price
        overhead = [a for a in mem.get("asks", []) if thr_ok(a)]
        ask_wall = min(overhead, key=lambda x: x["price"]) if overhead else None
    except Exception:
        pass
    flow_ko, flow_en = [], []
    if rt and rt.get("live"):
        imb = rt.get("imbalance")
        if rt.get("pressure"):
            _i = f" ({imb:+.2f})" if isinstance(imb, (int, float)) else ""
            flow_ko.append(f"호가 {rt['pressure']}{_i}")
            flow_en.append(f"book {rt.get('pressure_en') or rt['pressure']}{_i}")
        pn = rt.get("program_net")
        if isinstance(pn, (int, float)) and pn:
            flow_ko.append("프로그램 순매수 유입" if pn > 0 else "프로그램 순매도")
            flow_en.append("program flow net-buying" if pn > 0 else "program flow net-selling")
        if entry == "ENTER" and rt.get("pressure") == "매도우위":
            entry = "WAIT"                     # tape veto: don't buy into a seller-heavy book
            flow_ko.append("매도우위 확인 → 진입 보류(대기)로 하향")
            flow_en.append("ask-heavy tape → entry downgraded to WAIT")
    if ask_wall:
        flow_ko.append(f"목표 아래 매도벽 {ask_wall['price']:,} — 목표 도달 저항 주의")
        flow_en.append(f"large ask wall at {ask_wall['price']:,} below target — resistance on the way")

    net_pct = round(target_pct - 0.25, 2)     # ~0.25% round-trip cost (세금+수수료)
    stop_pct = round((buy_hi - stop_price) / buy_hi * 100, 2) if buy_hi and buy_hi > stop_price else None
    ratio = round(exp_move / target_pct, 1) if exp_move else None

    # market backdrop (cached 60s) — a scalp on a crash day needs to say so
    mkt = None
    try:
        from services.trading_brief import _mkt_ret_today
        mkt = _mkt_ret_today(db)
    except Exception:
        pass

    # Method 2 live signal (single-stock route only; the watchlist loop skips this)
    m2sig = None
    if with_backdrop:
        try:
            from services.trading_brief import analysis_batch
            m2sig = ((analysis_batch(db, [tk]) or {}).get("results", {}).get(tk) or {}).get("signal")
        except Exception:
            pass

    # 5-minute micro flow (boss: daily trading = 5-min analysis): shown in the evidence
    # section so the entry decision reads the live tape at bar resolution.
    micro_ko = micro_en = None
    if with_backdrop:
        try:
            from services.micro_trend import micro_read
            _mi = micro_read(db, tk)
            if _mi:
                micro_ko = f"· 5분봉 흐름: {_mi['line_ko']}"
                micro_en = f"- 5-min flow: {_mi['line_en']}"
        except Exception:
            pass

    # M5.7 TIER read (replay-validated): this stock's latest hourly forecasts — when both
    # methods AGREE the historical hit-rate jumps (~80%, n small); conflicts are coin-flips.
    # Plus the measured bad-hour caution (e.g. 10시 KST ran sub-45%).
    tier_ko = tier_en = None
    _bad_hour_ko = _bad_hour_en = None
    try:
        from services.method_weights import intraday_tier
        _tr = intraday_tier(db, tk)
        if _tr:
            if _tr["tier"] == "agree" and _tr.get("agree_acc") is not None:
                _d_ko = {"UP": "상승", "DOWN": "하락"}.get(_tr.get("ml"), "")
                tier_ko = (f"🎯 시간별 예측 일치: ML·분석 모두 '{_d_ko}' — 과거 일치 구간 적중률 "
                           f"{_tr['agree_acc']}% (n={_tr['agree_n']}), 상대적으로 신뢰도 높은 구간.")
                tier_en = (f"🎯 Hourly forecasts AGREE ({_tr.get('ml')}) — agreement zones have hit "
                           f"{_tr['agree_acc']}% historically (n={_tr['agree_n']}).")
            elif _tr["tier"] == "conflict":
                tier_ko = "⚠️ 시간별 예측 엇갈림(ML vs 분석) — 확신 낮은 구간, 수량 축소 권장."
                tier_en = "⚠️ Hourly forecasts conflict (ML vs Analysis) — low-conviction zone; smaller size."
            from datetime import datetime as _dt2
            from datetime import timedelta as _td2
            from datetime import timezone as _tz2
            _kst_h = _dt2.now(_tz2(_td2(hours=9))).hour
            if _kst_h in (_tr.get("bad_hours") or []):
                _bad_hour_ko = f"{_kst_h}시대는 과거 예측 적중률이 낮았던 시간대입니다 — 진입은 평소보다 신중히."
                _bad_hour_en = f"{_kst_h}:00 KST has historically been a low-accuracy hour — extra caution on entries."
    except Exception:
        pass

    warns_ko, warns_en = [], []
    if _bad_hour_ko:
        warns_ko.append(_bad_hour_ko)
        warns_en.append(_bad_hour_en)
    if collector_off:
        warns_ko.append("실시간 분봉 수집기가 꺼져 있어 일봉 변동성 기준의 참고 수치입니다 — 수집기를 켜면 정확도가 올라갑니다.")
        warns_en.append("The live minute-bar collector is OFF, so these are daily-volatility reference numbers — turn it on for precision.")
    if stale_min:
        warns_ko.append(f"분봉이 {stale_min}분 전 데이터입니다 — PC 수집기 상태를 확인하세요.")
        warns_en.append(f"Minute data is {stale_min} min old — check the PC collector.")
    # zone built from an OLD session's bars (collector hasn't run today) — say so even
    # outside market hours; a plan anchored days back must never look like today's.
    if not collector_off:
        try:
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo as _ZI
            _data_date = str(v.get("as_of") or "")[:10]
            _today = _dt.now(_ZI("Asia/Seoul")).date().isoformat()
            if _data_date and _data_date != _today:
                warns_ko.append(f"매매 계획(매수 구간·손절)이 {_data_date} 세션 분봉 기준입니다 — 오늘 세션 데이터가 아직 없어요 (PC 수집기 확인). 현재가와 구간이 멀다면 이 때문입니다.")
                warns_en.append(f"The plan (buy zone/stop) is based on the {_data_date} session's bars — no data for today's session yet (check the PC collector). If the zone looks far from the current price, this is why.")
        except Exception:
            pass
    if mkt is not None and mkt <= -1.5:
        warns_ko.append(f"오늘 시장이 급락 중(KODEX200 {mkt:+.2f}%)이라 반등 단타도 실패 확률이 평소보다 높습니다 — 수량을 줄이거나 쉬는 것도 전략입니다.")
        warns_en.append(f"The market is plunging today (KODEX200 {mkt:+.2f}%) — even bounce scalps fail more often; smaller size (or sitting out) is a valid play.")
    warns_ko.append("단타는 확률 게임입니다 — 목표 도달 시 욕심 없이 매도, 손절가 도달 시 예외 없이 정리하는 규칙이 수익률보다 중요합니다.")
    warns_en.append("Scalping is a probabilities game — sell at target without greed, cut at the stop without exceptions; the rules matter more than any single trade.")

    head = {"ENTER": "🟢 진입 적합", "WAIT": "🟡 대기 (눌림목까지)", "SKIP": "⚪ 오늘은 부적합",
            "AVOID": "🔴 단타 롱 비권장 (상위 추세 약세)"}[entry]
    head_en = {"ENTER": "🟢 Good entry now", "WAIT": "🟡 Wait (for a pullback)",
               "SKIP": "⚪ Not suitable today", "AVOID": "🔴 Scalp-long not advised (bearish trend)"}[entry]
    feas_ko = {"yes": "충분히 가능", "marginal": "제한적으로 가능", "unlikely": "오늘은 어려움"}[feasible]
    feas_en = {"yes": "clearly feasible", "marginal": "marginally feasible", "unlikely": "unlikely today"}[feasible]

    # ---- per-method one-liners with the WHY, for the evidence section ----
    m1_ko = {"BUY": "매수 — 시장 대비 상승 우위를 예측 → 단타에 순풍",
             "SELL": "매도 — 시장 대비 하락 우위를 예측 → 단타 롱에 역풍",
             "HOLD": "보유 — 5일 기준 뚜렷한 방향 우위 없음 → 방향 중립"}.get((ml_adv or "").upper(), "데이터 없음")
    m1_en = {"BUY": "BUY — predicts market outperformance → tailwind for a scalp",
             "SELL": "SELL — predicts underperformance → headwind for a scalp-long",
             "HOLD": "HOLD — no clear 5-day edge either way → directionally neutral"}.get((ml_adv or "").upper(), "no data")
    m2_ko = {"BUY": "매수 우위 — 호가·수급이 매수 쪽",
             "SELL": "매도 우위 — 호가·수급이 매도 쪽 (반등 단타 주의)",
             "WATCH": "관망 — 호가·수급 뚜렷한 우위 없음", "HOLD": "관망"}.get((m2sig or "").upper())
    m2_en = {"BUY": "buy-side — order book & flows lean buy",
             "SELL": "sell-side — book & flows lean sell (careful with bounce scalps)",
             "WATCH": "neutral — no clear edge in book/flows", "HOLD": "neutral"}.get((m2sig or "").upper())
    m3_ko = {"BUY": "매수 — 강한 상승 파동 후 깊은 눌림(매수 자리)",
             "WATCH": "관망 — 상승 파동은 있으나 아직 매수 구간 아님",
             "AVOID": "회피 — 상승 파동이 약하거나 무너짐"}.get((wv or "").upper(), "데이터 없음")
    m3_en = {"BUY": "BUY — deep pullback after a strong up-wave (a buy spot)",
             "WATCH": "WATCH — up-wave exists but not in the buy zone yet",
             "AVOID": "AVOID — the up-wave is weak or broken"}.get((wv or "").upper(), "no data")
    tape_ko = " · ".join(flow_ko) if flow_ko else ("호가/프로그램 스냅샷 없음 (수집기 확인)" if not collector_off else "수집기 꺼짐 — 실시간 호가 확인 불가")
    tape_en = " · ".join(flow_en) if flow_en else ("no order-book/program snapshot (check collector)" if not collector_off else "collector off — no live tape")

    # direction synthesis: is the higher timeframe helping or hurting this scalp?
    _bull = sum(1 for s in ((ml_adv or "").upper(), (wv or "").upper(), (m2sig or "").upper()) if s == "BUY")
    _bear = sum(1 for s in ((ml_adv or "").upper(), (m2sig or "").upper()) if s == "SELL") + (1 if (wv or "").upper() == "AVOID" else 0)
    if _bull > _bear:
        syn_ko = "→ 상위 방향이 우호적이라, 눌림 매수 단타의 성공 확률이 평소보다 높은 편입니다."
        syn_en = "→ The higher-timeframe direction is supportive, so a pullback-buy scalp has better-than-usual odds."
    elif _bear > _bull:
        syn_ko = "→ 상위 방향이 비우호적이라, 방향 베팅이 아닌 '지지선 반등'만 짧게 노리고 손절을 더 엄격히 지키세요."
        syn_en = "→ The higher timeframe is against you — only play quick support bounces and keep the stop strict."
    else:
        syn_ko = "→ 상위 방향이 중립이라, 방향 베팅보다는 지지선 반등을 짧게 먹는 접근이 적합합니다."
        syn_en = "→ The higher timeframe is neutral — treat this as a support-bounce play, not a direction bet."

    title_ko = f"**{name} — {head}**  ·  현재가 {last:,.0f}원" + (f" · KODEX200 {mkt:+.2f}%" if mkt is not None else "")
    title_en = f"**{name_en} — {head_en}**  ·  now ₩{last:,.0f}" + (f" · KODEX200 {mkt:+.2f}%" if mkt is not None else "")

    def _join(parts):
        # drop only None (skipped lines) — keep "" (intentional blank lines between sections)
        return "\n".join(p for p in parts if p is not None)

    if entry == "AVOID":
        _mv = bool(regime and regime.get("veto"))
        why_ko = " / ".join(filter(None, [
            "시장 급락일 필터 발동" if _mv else None,
            "머신러닝이 '매도'" if ml_adv == "SELL" else None,
            "파동이 '회피'" if wv == "AVOID" else None])) or "상위 추세 약세"
        why_en = " / ".join(filter(None, [
            "market-crash filter triggered" if _mv else None,
            "ML says SELL" if ml_adv == "SELL" else None,
            "Wave says AVOID" if wv == "AVOID" else None])) or "bearish higher-timeframe trend"
        ko = _join([
            title_ko, "",
            "**① 한 줄 결론**",
            f"지금은 단타 매수를 권하지 않습니다. {why_ko} — 떨어지는 종목의 반등을 잡으려는 단타는 성공 확률이 낮습니다. "
            f"추세 회복(반등 확인) 후 다시 물어보세요.",
            (regime.get("line_ko") if _mv else None), "",
            "**② 근거 (3가지 방법 + 실시간)**",
            f"· 방법1 머신러닝: {m1_ko}",
            (f"· 방법2 분석: {m2_ko}" if m2_ko else None),
            f"· 방법3 파동: {m3_ko}",
            f"· 실시간 체크: {tape_ko}",
            micro_ko,
            tier_ko,
            syn_ko, "",
            "**③ 주의**", *[f"⚠️ {w}" for w in warns_ko]])
        en = _join([
            title_en, "",
            "**① Bottom line**",
            f"A scalp-long isn't advised right now. {why_en} — catching bounces on a falling stock is a low-odds trade. "
            f"Ask again after the trend recovers.",
            (regime.get("line_en") if _mv else None), "",
            "**② Evidence (3 methods + live tape)**",
            f"- Method 1 ML: {m1_en}",
            (f"- Method 2 Analysis: {m2_en}" if m2_en else None),
            f"- Method 3 Wave: {m3_en}",
            f"- Live tape: {tape_en}",
            micro_en,
            tier_en,
            syn_en, "",
            "**③ Caution**", *[f"⚠️ {w}" for w in warns_en]])
    else:
        concl_ko = {"ENTER": f"지금 바로 진입해도 좋은 자리입니다 — 현재가가 매수 구간({buy_lo:,}~{buy_hi:,}원) 안이거나 바로 위입니다.",
                    "WAIT": f"지금 가격({last:,.0f}원)에 쫓아 사지 말고, 매수 구간({buy_lo:,}~{buy_hi:,}원)까지 눌림을 기다렸다가 진입하세요 — 같은 +1%라도 싸게 사야 손절 폭이 줄어듭니다.",
                    "SKIP": "오늘은 변동성이 목표에 못 미쳐 진입을 보류하는 것이 낫습니다 — 무리하면 수수료만 나갑니다."}[entry]
        concl_en = {"ENTER": f"This is a good spot to enter now — price is in (or just above) the buy zone ₩{buy_lo:,}–{buy_hi:,}.",
                    "WAIT": f"Don't chase at ₩{last:,.0f} — wait for the pullback into the buy zone (₩{buy_lo:,}–{buy_hi:,}). The same +1% bought cheaper means a smaller stop.",
                    "SKIP": "Better to sit this one out today — the volatility doesn't cover the target, and forcing it just pays fees."}[entry]
        feas_expl_ko = (f"오늘 이 종목의 예상 일중 변동폭은 ±{exp_move}%로, 목표(+{target_pct}%)의 {ratio}배입니다. "
                        + ({"yes": "장중에 +1% 구간이 나올 여지가 충분합니다.",
                            "marginal": "가능은 하지만 여유가 크지 않아 목표를 꽉 채우기보다 일찍 익절하는 편이 안전합니다.",
                            "unlikely": "오늘 움직임으로는 목표까지 가기 어렵습니다."}[feasible])) if exp_move else "변동성 데이터가 부족합니다."
        feas_expl_en = (f"Today's expected intraday range is ±{exp_move}%, {ratio}x the +{target_pct}% target. "
                        + ({"yes": "There's ample room for a +1% leg during the session.",
                            "marginal": "Doable but tight — taking profit early beats squeezing the full target.",
                            "unlikely": "Today's movement likely won't reach the target."}[feasible])) if exp_move else "Not enough volatility data."
        ko = _join([
            title_ko, "",
            "**① 한 줄 결론**", concl_ko, "",
            f"**② 오늘 +{target_pct}% 가능성 — {feas_ko}**", feas_expl_ko, "",
            "**③ 매매 계획**",
            f"· 매수 구간: {buy_lo:,}~{buy_hi:,}원" + (" — 대량 매수벽(지지)이 받치는 자리입니다" if wall else " — 장중 저점 부근입니다"),
            f"· 목표가: {target_price:,}원 (+{target_pct}%) — 도달하면 욕심 없이 매도",
            f"· 손절가: {stop_price:,}원" + (f" (−{stop_pct}%)" if stop_pct else "") + f" — 지지 이탈 시 기계적으로 정리 · 손익비 {rr}",
            (f"· 예상 소요: 약 {est_min}분 (오늘 속도 기준)" if est_min else None),
            f"· 실수익: 세금·수수료 ~0.25% 차감 후 약 +{net_pct}%", "",
            "**④ 근거 (3가지 방법 + 실시간)**",
            f"· 방법1 머신러닝: {m1_ko}",
            (f"· 방법2 분석: {m2_ko}" if m2_ko else None),
            f"· 방법3 파동: {m3_ko}",
            f"· 실시간 체크: {tape_ko}",
            micro_ko,
            tier_ko,
            syn_ko, "",
            "**⑤ 주의**", *[f"⚠️ {w}" for w in warns_ko]])
        en = _join([
            title_en, "",
            "**① Bottom line**", concl_en, "",
            f"**② Odds of +{target_pct}% today — {feas_en}**", feas_expl_en, "",
            "**③ Trade plan**",
            f"- Buy zone: ₩{buy_lo:,}–{buy_hi:,}" + (" — backed by a large bid wall (support)" if wall else " — near the intraday low"),
            f"- Target: ₩{target_price:,} (+{target_pct}%) — sell there, no greed",
            f"- Stop: ₩{stop_price:,}" + (f" (−{stop_pct}%)" if stop_pct else "") + f" — cut mechanically if support breaks · R:R {rr}",
            (f"- Expected time: ~{est_min} min at today's pace" if est_min else None),
            f"- Net after ~0.25% costs: ≈ +{net_pct}%", "",
            "**④ Evidence (3 methods + live tape)**",
            f"- Method 1 ML: {m1_en}",
            (f"- Method 2 Analysis: {m2_en}" if m2_en else None),
            f"- Method 3 Wave: {m3_en}",
            f"- Live tape: {tape_en}",
            micro_en,
            tier_en,
            syn_en, "",
            "**⑤ Caution**", *[f"⚠️ {w}" for w in warns_en]])

    return {"ticker": tk, "name": name, "entry": entry, "feasible": feasible,
            "target_pct": target_pct, "current": last, "buy_zone": [buy_lo, buy_hi],
            "target_price": target_price, "stop_price": stop_price, "rr": rr,
            "exp_move_pct": exp_move, "regime_veto": bool(regime and regime.get("veto")),
            "est_minutes": est_min, "net_pct": net_pct, "ml_bias": ml_adv, "wave_bias": wv,
            "m2_bias": m2sig, "collector_off": collector_off, "stale_min": stale_min,
            "rt_check": ({"imbalance": rt.get("imbalance"), "pressure": rt.get("pressure"),
                          "program_net": rt.get("program_net")} if rt and rt.get("live") else None),
            "ask_wall": (ask_wall["price"] if ask_wall else None),
            "reasoning_ko": ko, "reasoning_en": en}


def feasibility(db, ticker: str, target_pct: float = 1.0) -> dict[str, Any]:
    from services.minute_bars import intraday_vol
    from services.orderbook_memory import read_memory
    from services.prediction_service import NAMES

    tk = str(ticker).zfill(6)
    name = NAMES.get(tk, tk)
    v = intraday_vol(db, tk)
    if not v.get("available"):
        return {"ticker": tk, "name": name, "feasible": "unknown", "target_pct": target_pct,
                "reasoning_ko": "오늘 분봉 데이터가 아직 부족합니다 (수집기 가동 후 가능).",
                "reasoning_en": "Not enough minute data yet today (needs the collector running)."}

    last = v["last"]; day_low = v["day_low"]; day_high = v["day_high"]
    exp_move = v.get("expected_day_move_pct") or 0           # 1σ full-day move %
    realized = v.get("realized_range_pct") or 0              # range so far %
    pos = v.get("pos_in_range")                              # 0..100, where price sits

    # support = nearest LARGE bid wall below price (remembered), else today's low
    mem = read_memory(db, tk, depth=30)
    bid_walls = [b for b in mem.get("bids", []) if b.get("is_large") and b["price"] <= last]
    wall = max(bid_walls, key=lambda x: x["price"]) if bid_walls else None   # closest below
    support = wall["price"] if wall else day_low

    # buy zone = near the intraday bottom / support wall
    buy_lo = support
    buy_hi = round(support * 1.002)
    target_price = round(buy_hi * (1 + target_pct / 100))
    stop_price = round(support * 0.997)                      # just below support
    risk_pct = round((buy_lo - stop_price) / buy_lo * 100, 2) if buy_lo else None
    rr = round((target_price - buy_hi) / (buy_hi - stop_price), 2) if buy_hi > stop_price else None

    # verdict: does today's measured volatility leave room for target + a stop?
    headroom = exp_move - target_pct                          # remaining move budget
    if exp_move >= target_pct * 2.5 and realized >= target_pct:
        verdict = "yes"
    elif exp_move >= target_pct * 1.3:
        verdict = "marginal"
    else:
        verdict = "unlikely"

    near_bottom = (pos is not None and pos <= 35)
    ko = (f"{name} — 오늘 변동성: 장중 변동폭 {realized}%, 예상 일중 변동(1σ) {exp_move}%. "
          f"목표 +{target_pct}% 는 {'충분히 가능' if verdict=='yes' else '제한적으로 가능' if verdict=='marginal' else '오늘은 어려움'}"
          f" (변동폭이 목표의 {round(exp_move/target_pct,1)}배). "
          f"매수 구간 {buy_lo:,}~{buy_hi:,}{' (대량 매수벽 지지)' if wall else ' (장중 저점)'}, "
          f"목표 {target_price:,}, 손절 {stop_price:,} (위험 {risk_pct}%, 손익비 {rr}). "
          f"{'현재가가 저점 근처라 진입 양호.' if near_bottom else '현재가가 고점권 — 눌림목(매수구간)까지 대기 권장.'}")
    en = (f"{name} — today's vol: intraday range {realized}%, expected 1σ day move {exp_move}%. "
          f"A +{target_pct}% target is {'clearly feasible' if verdict=='yes' else 'marginally feasible' if verdict=='marginal' else 'unlikely today'} "
          f"({round(exp_move/target_pct,1)}x the target). "
          f"Buy zone {buy_lo:,}~{buy_hi:,}{' (large bid-wall support)' if wall else ' (intraday low)'}, "
          f"target {target_price:,}, stop {stop_price:,} (risk {risk_pct}%, R:R {rr}). "
          f"{'Price is near the low — good entry.' if near_bottom else 'Price is high in range — wait for a pullback to the buy zone.'}")

    return {
        "ticker": tk, "name": name, "target_pct": target_pct, "feasible": verdict,
        "current": last, "day_open": v["day_open"], "day_high": day_high, "day_low": day_low,
        "expected_day_move_pct": exp_move, "realized_range_pct": realized, "pos_in_range": pos,
        "buy_zone": [buy_lo, buy_hi], "target_price": target_price, "stop_price": stop_price,
        "risk_pct": risk_pct, "rr": rr,
        "support_wall": ({"price": wall["price"], "max_qty": wall["max_qty"]} if wall else None),
        "reasoning_ko": ko, "reasoning_en": en,
    }
