# -*- coding: utf-8 -*-
"""price_ladder — WHERE TO REST AN ORDER WHEN HE DOES NOT NAME A PRICE.

Boss 2026-09-10: "if we do not tell the price our chatbot should recommend 5
different efficient prices according to market and our historical data for
buying. For selling it should be one down of the highest volume price."

Two rules, one module:

BUY  — five rungs, each anchored on something real rather than spaced by
       arithmetic. The old ladder just divided the gap between the live price
       and today's low into equal steps, which puts rungs at prices nothing
       happens at. These anchor on the order book (the walls other people are
       queuing behind) and on our own session data (the level the day actually
       traded at, the day's low, the recent dip) — market and history, which is
       what he asked for. Each rung carries the reason it was chosen, because a
       price he cannot explain is a price he will not trust.

SELL — one tick below the HIGHEST-VOLUME price of the session. That price is
       where the most shares changed hands, so it is where the buyers are; a
       tick under it sells into that demand instead of queuing behind it. This
       is the traded-volume profile, NOT the order-book wall that `_book_offer`
       reads — the book is what is merely *offered*, this is what was actually
       *done*.

Every price returned is already rounded to its KRX tick.
"""
from __future__ import annotations

from typing import Any, Optional

from services.giveup_rule import tick_size
from services.logger import log

# How far under the market the deepest BUY rung may sit. A dip-buying ladder is
# for today; a rung 12% down is a level, not an order.
MAX_DEPTH = 0.03


def round_tick(price: float, up: bool = False) -> float:
    """Snap to a legal KRX price."""
    t = tick_size(float(price))
    n = (float(price) + (t - 1e-9) if up else float(price)) // t
    return float(int(n) * t)


# ---------------------------------------------------------------------------
# the traded-volume profile
# ---------------------------------------------------------------------------

def volume_profile(code: str, bars: Optional[list[dict]] = None) -> list[tuple[float, int]]:
    """[(price, volume)] for the latest session, biggest volume first.

    Volume is attributed to each 1-minute bar's CLOSE — the price that minute
    actually finished trading at. Only the most recent session counts: a profile
    smeared across two days points at a level yesterday cared about."""
    try:
        if bars is None:
            from services.kiwoom_rest import minute_bars
            bars = minute_bars(code, "1", 400) or []
        if not bars:
            return []
        # bars arrive oldest-first; keep only the last session's date
        last_day = str(bars[-1].get("ts") or "")[:10]
        buckets: dict[float, int] = {}
        for b in bars:
            if str(b.get("ts") or "")[:10] != last_day:
                continue
            px, vol = b.get("close"), b.get("volume")
            if not px or not vol:
                continue
            k = round_tick(float(px))
            buckets[k] = buckets.get(k, 0) + int(vol)
        return sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)
    except Exception as e:
        log.warning(f"volume_profile failed for {code}: {str(e)[:120]}")
        return []


def poc(code: str) -> Optional[dict]:
    """The highest-volume price of the session (point of control), with the share
    of the session's volume that traded there."""
    prof = volume_profile(code)
    if not prof:
        return None
    price, vol = prof[0]
    total = sum(v for _p, v in prof) or 1
    return {"price": float(price), "volume": int(vol),
            "share_pct": round(vol / total * 100, 1), "levels": len(prof)}


def sell_price(code: str, live: Optional[float] = None) -> Optional[dict]:
    """HIS SELL RULE: one tick below the highest-volume price."""
    p = poc(code)
    if not p:
        return None
    px = round_tick(float(p["price"]) - tick_size(float(p["price"])))
    return {"price": px, "poc": float(p["price"]), "poc_volume": int(p["volume"]),
            "share_pct": p["share_pct"],
            "ko": (f"오늘 거래가 가장 많이 몰린 가격 ₩{p['price']:,.0f}"
                   f"(거래량 {p['volume']:,}주 · 세션의 {p['share_pct']}%)보다 "
                   f"한 틱 아래입니다 — 매수세가 두터운 자리에 먼저 내놓습니다."),
            "en": (f"one tick under ₩{p['price']:,.0f}, the price the most shares "
                   f"traded at today ({p['volume']:,} sh · {p['share_pct']}% of the "
                   f"session) — selling into that demand instead of queuing behind it.")}


# ---------------------------------------------------------------------------
# the five efficient BUY rungs
# ---------------------------------------------------------------------------

def _bid_walls(code: str, top: int = 3) -> list[dict]:
    """The biggest resting BID levels, largest first."""
    try:
        from services.kiwoom_rest import order_book
        ob = order_book(code, ttl=2) or {}
        rows = [l for l in (ob.get("levels") or [])
                if l.get("side") == "bid" and l.get("price")]
        rows.sort(key=lambda l: l.get("qty") or 0, reverse=True)
        return rows[:top]
    except Exception:
        return []


def buy_rungs(db, code: str, live: float, n: int = 5) -> list[dict]:
    """`n` BUY prices, highest first, each with the reason it was chosen.

    Anchors, in the order they are trusted:
      · one tick IN FRONT of each of the biggest bid walls   (market — his 08-26 rule)
      · the session's highest-volume price                   (market — the magnet)
      · today's low                                          (history)
      · the historical dip suggestion (smart_price)          (history)
      · the 5-day low                                        (history)
    Anything at or above the live price is dropped — a resting BUY that is not
    below the market is just a market order wearing a limit's clothes. Gaps are
    filled by even steps only when the anchors run out.
    """
    live = float(live or 0)
    if live <= 0:
        return []
    t = tick_size(live)
    cands: list[tuple[float, str, str]] = []

    for i, w in enumerate(_bid_walls(code, top=2), 1):
        try:
            wp = float(w["price"])
            cands.append((round_tick(wp + tick_size(wp)),
                          f"{i}번째로 두꺼운 매수벽(₩{wp:,.0f} · {int(w.get('qty') or 0):,}주) "
                          f"바로 한 틱 위 — 그 줄보다 먼저 체결됩니다",
                          f"one tick in front of the #{i} bid wall (₩{wp:,.0f}, "
                          f"{int(w.get('qty') or 0):,} sh) — fills ahead of that queue"))
        except Exception:
            continue

    p = poc(code)
    if p:
        cands.append((round_tick(float(p["price"])),
                      f"오늘 거래가 가장 많이 몰린 가격 (거래량 {p['volume']:,}주 · "
                      f"세션의 {p['share_pct']}%) — 시장이 인정한 자리",
                      f"the session's highest-volume price ({p['volume']:,} sh · "
                      f"{p['share_pct']}%) — the level the market agreed on"))

    try:
        from services.chat_trade import _today_low, smart_price
        lo = _today_low(code)
        if lo:
            cands.append((round_tick(float(lo)), "오늘 저가 — 하루 중 가장 싼 자리",
                          "today's low — the cheapest the day has been"))
        sm = smart_price(code, live)
        if sm:
            cands.append((round_tick(float(sm)),
                          "과거 데이터 기준 눌림목 추천가", "our historical dip suggestion"))
    except Exception:
        pass

    try:
        from services.price_history import rows as _hrows
        h, _src = _hrows(db, code, 5)
        lows = [float(r["low"]) for r in (h or []) if r.get("low")]
        if lows:
            cands.append((round_tick(min(lows)), "최근 5거래일 최저가 — 강한 지지선",
                          "the 5-day low — the firmest support nearby"))
    except Exception:
        pass

    # A RUNG HAS TO BE REACHABLE. An anchor 12% under the market is a real level
    # and a useless order — SK하이닉스' 5-day low was the only survivor once and
    # the whole ladder ended up parked there. Nothing deeper than MAX_DEPTH.
    floor = round_tick(live * (1.0 - MAX_DEPTH))

    out: list[dict] = []
    seen: set[float] = set()
    for px, ko, en in sorted(cands, key=lambda c: -c[0]):
        if px <= 0 or px >= live or px < floor or px in seen:
            continue
        seen.add(px)
        out.append({"price": px, "ko": ko, "en": en})
        if len(out) >= n:
            break

    # the top rung must sit near the market, or the ladder cannot start working
    near = round_tick(live - t)
    if near > 0 and (not out or (live - out[0]["price"]) > live * 0.005) and near not in seen:
        seen.add(near)
        out.insert(0, {"price": near, "ko": "현재가 바로 아래 — 첫 눌림을 바로 받는 자리",
                       "en": "just under the live price — catches the very first dip"})

    # still short: split the widest gap, so the rungs spread across the real
    # range instead of bunching a tick apart under the deepest anchor
    guard = 0
    while len(out) < n and guard < 40:
        guard += 1
        edges = [r["price"] for r in out] + [floor]
        gaps = [(edges[i] - edges[i + 1], i) for i in range(len(edges) - 1)]
        gap, i = max(gaps)
        if gap <= t:
            break
        mid = round_tick(edges[i] - gap / 2)
        if mid <= 0 or mid in seen or mid < floor:
            break
        seen.add(mid)
        out.append({"price": mid, "ko": "아래 단계 — 더 깊은 눌림을 받는 자리",
                    "en": "a deeper rung — catches a bigger dip"})
        out.sort(key=lambda r: -r["price"])
    return out[:n]


def sell_rungs(code: str, live: float, n: int = 5) -> list[dict]:
    """`n` SELL prices, lowest first, starting at his rule (one tick under the
    highest-volume price) and stepping up from there."""
    live = float(live or 0)
    t = tick_size(live or 1)
    base = sell_price(code, live)
    out: list[dict] = []
    if base:
        out.append({"price": base["price"], "ko": base["ko"], "en": base["en"]})
    else:
        out.append({"price": round_tick(live + t, up=True),
                    "ko": "현재가 바로 위", "en": "just above the live price"})
    px = out[0]["price"]
    step = max(t, round_tick(px * 0.002) or t)
    while len(out) < n:
        px = round_tick(px + step, up=True)
        out.append({"price": px, "ko": "위 단계 — 더 오를 때 파는 자리",
                    "en": "a higher rung — sells into more strength"})
    return out[:n]
