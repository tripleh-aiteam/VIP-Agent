# -*- coding: utf-8 -*-
"""giveup_rule — THE GIVE-UP LAW for unfilled limit offers (boss 2026-09-03:
"if we offer price and it will not reach, it should give up and cancel...
make a table for give up price, different for each stock, check with our
historical data and find the best efficient limitation").

Studied on a full year of 1-minute bars for the six (scratchpad giveup_study2):
for every runaway distance D we measured (a) the chance an offer left ₩D
behind still fills the same day, and (b) what those LATE fills earn by the
close — because a limit that fills after a big runaway fills while the price
is crashing back through it (the falling knife). The give-up point is the
smallest D where waiting stops paying: late fills earn <= 0 on average
(sustained, not a one-tick blip), or the comeback chance drops under 40%.

  SK하이닉스     ₩2,000 (2 ticks)  — beyond 2 ticks, late fills avg -0.08%..-0.23%
  삼성전자       ₩400   (4 ticks)  — heavy mean-reverter, but waiting earns 0 past 4t
  NAVER          ₩3,000 (6 ticks)  — late fills stay profitable, but comeback <40%
  SK텔레콤       ₩1,100 (11 ticks) — defensive; patience pays longest of the six
  한화오션       ₩400   (4 ticks)
  두산에너빌리티 ₩500   (5 ticks)

Any other stock (top-4 rotators, the algo universe) defaults to 4 ticks of its
own price band — the median of the studied six.

The rule is symmetric: a BUY gives up when the live price runs ₩D ABOVE the
limit; a SELL gives up when it falls ₩D BELOW. Enforced at the single
chokepoint every menu and algo shares: paper_desk.check_limit_orders.
"""
from __future__ import annotations

# per-stock give-up distance in won, from the year study (2026-09-03)
GIVEUP_WON: dict[str, float] = {
    "000660": 2000.0,   # SK하이닉스
    "005930": 400.0,    # 삼성전자
    "035420": 3000.0,   # NAVER
    "017670": 1100.0,   # SK텔레콤
    "042660": 400.0,    # 한화오션
    "034020": 500.0,    # 두산에너빌리티
}
DEFAULT_TICKS = 4       # median of the studied six, for every other stock


def tick_size(price: float) -> float:
    """KRX tick bands."""
    if price < 2000: return 1
    if price < 5000: return 5
    if price < 20000: return 10
    if price < 50000: return 50
    if price < 200000: return 100
    if price < 500000: return 500
    return 1000


def giveup_won(code: str, ref_price: float) -> float:
    d = GIVEUP_WON.get(str(code))
    if d is not None:
        return d
    return DEFAULT_TICKS * tick_size(float(ref_price or 0) or 1)


def should_give_up(side: str, limit_price: float, live_price: float, code: str) -> bool:
    """True when the live price has run away from the offer beyond the stock's
    give-up distance — the order is a falling-knife catcher now, cancel it."""
    try:
        d = giveup_won(code, limit_price)
        if str(side).upper() == "BUY":
            return float(live_price) >= float(limit_price) + d
        return float(live_price) <= float(limit_price) - d
    except Exception:
        return False


def table(price_of=None) -> list[dict]:
    """The law as rows for display. price_of(code) may supply live prices so
    the default-rule line can be resolved per stock; studied six are fixed."""
    names = {"000660": "SK하이닉스", "005930": "삼성전자", "035420": "NAVER",
             "017670": "SK텔레콤", "042660": "한화오션", "034020": "두산에너빌리티"}
    rows = []
    for code, won in GIVEUP_WON.items():
        rows.append({"code": code, "name": names.get(code, code), "won": won,
                     "studied": True})
    return rows
