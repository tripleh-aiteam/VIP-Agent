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

    last = v["last"]; exp_move = v.get("expected_day_move_pct") or 0
    realized = v.get("realized_range_pct") or 0; pos = v.get("pos_in_range")

    # support = nearest large bid wall below price (remembered), else day low
    wall = None
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

    net_pct = round(target_pct - 0.25, 2)     # ~0.25% round-trip cost (세금+수수료)
    off_ko = " ⚠️ 실시간 수집기 꺼짐 — 일봉 변동성 기준(참고)." if collector_off else ""
    off_en = " ⚠️ Live collector off — using daily volatility (reference)." if collector_off else ""
    head = {"ENTER": "🟢 진입 적합", "WAIT": "🟡 대기 (눌림목까지)", "SKIP": "⚪ 오늘은 부적합",
            "AVOID": "🔴 단타 롱 비권장 (상위 추세 약세)"}[entry]
    head_en = {"ENTER": "🟢 Good entry now", "WAIT": "🟡 Wait (for a pullback)",
               "SKIP": "⚪ Not suitable today", "AVOID": "🔴 Scalp-long not advised (bearish trend)"}[entry]
    feas_ko = {"yes": "충분히 가능", "marginal": "제한적으로 가능", "unlikely": "오늘은 어려움"}[feasible]
    feas_en = {"yes": "clearly feasible", "marginal": "marginally feasible", "unlikely": "unlikely today"}[feasible]

    if entry == "AVOID":
        ko = (f"{name} — {head}. 방법1/3 기준 상위 추세가 약세({'ML 매도' if ml_adv=='SELL' else ''}{' · 파동 회피' if wv=='AVOID' else ''})라 "
              f"지금 단타 매수는 권하지 않습니다. 반등·추세 회복 확인 후 재검토.{off_ko}")
        en = (f"{name_en} — {head_en}. The higher-timeframe trend is bearish, so a scalp-long isn't advised now. "
              f"Reassess after a rebound/trend recovery.{off_en}")
    else:
        entry_word_ko = {"ENTER": "지금 진입 양호", "WAIT": "눌림목(매수구간)까지 대기", "SKIP": "오늘은 진입 보류"}[entry]
        entry_word_en = {"ENTER": "good to enter now", "WAIT": "wait for a pullback to the buy zone", "SKIP": "hold off today"}[entry]
        ko = (f"{name} — {head}. +{target_pct}% 목표는 오늘 {feas_ko}(변동폭이 목표의 {round(exp_move/target_pct,1)}배). "
              f"매수 {buy_lo:,}~{buy_hi:,}{' (대량 매수벽)' if wall else ''} · 목표 {target_price:,} · 손절 {stop_price:,} (손익비 {rr}). "
              f"{entry_word_ko}"
              + (f", 목표까지 예상 ~{est_min}분." if est_min else ".")
              + f" 비용 감안 실수익 ≈ +{net_pct}%.{off_ko}")
        en = (f"{name_en} — {head_en}. A +{target_pct}% target is {feas_en} today ({round(exp_move/target_pct,1)}x the target). "
              f"Buy {buy_lo:,}~{buy_hi:,}{' (bid wall)' if wall else ''} · target {target_price:,} · stop {stop_price:,} (R:R {rr}). "
              f"{entry_word_en}"
              + (f", ~{est_min} min to target." if est_min else ".")
              + f" Net ≈ +{net_pct}% after costs.{off_en}")

    # 3-method backdrop — ties the day-trade call to the decision phase (M1 & M3 already in
    # hand; M2 = the cached live-analysis signal). Single-stock route only (watchlist skips).
    m2sig = None
    if with_backdrop:
        try:
            from services.trading_brief import analysis_batch
            m2sig = ((analysis_batch(db, [tk]) or {}).get("results", {}).get(tk) or {}).get("signal")
        except Exception:
            pass
        _m1 = {"BUY": "매수", "SELL": "매도", "HOLD": "보유"}.get((ml_adv or "").upper(), "보유")
        _m2 = {"BUY": "매수", "SELL": "매도", "WATCH": "관망", "HOLD": "관망"}.get((m2sig or "").upper(), "관망")
        _m3 = {"BUY": "매수", "WATCH": "관망", "AVOID": "회피"}.get((wv or "").upper(), "데이터없음")
        _m1e = {"BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD"}.get((ml_adv or "").upper(), "HOLD")
        _m2e = {"BUY": "BUY", "SELL": "SELL", "WATCH": "WATCH", "HOLD": "WATCH"}.get((m2sig or "").upper(), "WATCH")
        _m3e = {"BUY": "BUY", "WATCH": "WATCH", "AVOID": "AVOID"}.get((wv or "").upper(), "n/a")
        ko += (f"\n\n📊 배경(3-method): 방법1(ML) {_m1} · 방법2(분석) {_m2} · 방법3(파동) {_m3}. "
               f"단타는 상위 방향과 같을 때 성공률이 높아요 — 종합 판단은 '살까?'로 물어보세요.")
        en += (f"\n\n📊 Backdrop (3 methods): M1(ML) {_m1e} · M2(Analysis) {_m2e} · M3(Wave) {_m3e}. "
               f"Scalps work best in the same direction as the higher-timeframe view.")

    return {"ticker": tk, "name": name, "entry": entry, "feasible": feasible,
            "target_pct": target_pct, "current": last, "buy_zone": [buy_lo, buy_hi],
            "target_price": target_price, "stop_price": stop_price, "rr": rr,
            "est_minutes": est_min, "net_pct": net_pct, "ml_bias": ml_adv, "wave_bias": wv,
            "m2_bias": m2sig, "collector_off": collector_off, "reasoning_ko": ko, "reasoning_en": en}


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
