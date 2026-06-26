"""day_trade.py — intraday day-trade FEASIBILITY answer.

Answers the user's morning question directly: "I want to buy {stock} near its intraday
bottom and sell today for +{target}%. Feasible? Where's my stop?" — using the day's real
volatility baseline (minute candles) + the remembered order-book support wall.

Honest by construction: the verdict is driven by whether today's measured volatility leaves
room for the target after a sensible stop. No promises — a feasibility read, not a guarantee.
"""
from __future__ import annotations

from typing import Any


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
