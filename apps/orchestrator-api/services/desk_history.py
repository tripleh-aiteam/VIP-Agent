"""desk_history — MENU 3's TRADING HISTORY, READ FROM THE ORDER BOOK.

Boss 2026-09-09: "in the trading history part it is considering only yesterday
and today, not other days - please check and restore it."

He was right, and the rows were not hidden, they were gone. Menu 3's history has
always been drawn from `approval_desk.json`'s `log`, a rolling 200-row window.
The send-time guard writes a 보류 row on every scan cycle it refuses a stock, so
a quiet morning can spend the whole window on notes about trades that never
happened - and the oldest-first trim then threw the real trades away. Yesterday
that eviction was stopped for good (_trim_log now protects 승인/취소 rows and
folds the repeats), but the days already lost - 09-03, 09-04, 09-07 - were gone
from the live file AND from its backup by the time it was fixed.

They are not gone from the desk's actual record. Every order Menu 3 ever sent
went through place_order and sits in `paper_desk_orders` with its clock, its
price and its fill. This module rebuilds the history from there, so the page no
longer depends on a window that noise can fill:

  · Menu 3's own orders are the ones it sent - source semi (the approval desk),
    chat / chatbot / manual (his own orders, which the desk mirrors).
  · A SELL, though, does not always carry that stamp: the desk's exits can go
    out through the engine's hand (2026-09-07, HD현대중공업 22주 sold at 13:46
    under source algo2). So a sell is matched to an open Menu 3 lot by ticker
    and EXACT quantity - the desk trades odd lots (79, 312, 22, 100) while the
    engine trades tens of thousands, so the match is unambiguous.
  · Buys and sells are paired FIFO per stock, and each closed leg carries the
    round trip: buy clock, buy price, % and won.

Nothing here writes anything. It is a reader over orders that already exist.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

# the sources that are Menu 3's own hand
DESK_SRC = ("semi", "chat", "chatbot", "manual", "algo2-chat", "chat-test")

_CACHE: dict = {}
_TTL = 120.0


def _q(db, sql: str, **kw):
    from sqlalchemy import text
    return db.execute(text(sql), kw).fetchall()


def days(db, back: int = 30) -> list[str]:
    """Every day Menu 3 actually sent an order, newest first."""
    key = ("days", back)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    rows = _q(db, """
        SELECT DISTINCT (created_at AT TIME ZONE 'Asia/Seoul')::date AS d
        FROM paper_desk_orders
        WHERE source = ANY(:src) AND status = 'FILLED'
          AND created_at > now() - (:back || ' days')::interval
        ORDER BY d DESC""", src=list(DESK_SRC), back=str(int(back)))
    out = [r[0].strftime("%Y-%m-%d") for r in rows]
    _CACHE[key] = (time.time(), out)
    return out


def rows(db, day: str) -> list[dict]:
    """One day of Menu 3 trading, in the shape the history panel already reads."""
    day = str(day)[:10]
    key = ("rows", day)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    ours = _q(db, """
        SELECT id, ticker, name, side, qty, fill_price, source,
               to_char(created_at AT TIME ZONE 'Asia/Seoul','HH24:MI') AS hhmm,
               to_char(COALESCE(filled_at, created_at) AT TIME ZONE 'Asia/Seoul','HH24:MI') AS fill_t
        FROM paper_desk_orders
        WHERE source = ANY(:src) AND status = 'FILLED'
          AND (created_at AT TIME ZONE 'Asia/Seoul')::date = :d
        ORDER BY created_at""", src=list(DESK_SRC), d=day)
    if not ours:
        _CACHE[key] = (time.time(), [])
        return []
    # every FILLED order of that day, whatever hand sent it - a desk exit can
    # wear the engine's stamp, and is recognised by its odd lot size
    everything = _q(db, """
        SELECT id, ticker, name, side, qty, fill_price, source,
               to_char(created_at AT TIME ZONE 'Asia/Seoul','HH24:MI') AS hhmm
        FROM paper_desk_orders
        WHERE status = 'FILLED'
          AND (created_at AT TIME ZONE 'Asia/Seoul')::date = :d
        ORDER BY created_at""", d=day)
    used = {r[0] for r in ours}

    out: list[dict] = []
    open_lots: dict[str, list[dict]] = {}

    def _row(r, side, extra=None):
        d = {"id": int(r[0]), "code": str(r[1]), "name": r[2] or str(r[1]),
             "side": side, "qty": int(r[4] or 0),
             "price": float(r[5] or 0), "fill": float(r[5] or 0),
             "hhmm": r[7], "at": r[7], "day": day, "decision": "승인",
             "dealt": True, "source": r[6], "from_orders": True}
        if extra:
            d.update(extra)
        return d

    for r in ours:
        code, side, qty = str(r[1]), str(r[3]).upper(), int(r[4] or 0)
        if side == "BUY":
            row = _row(r, "BUY")
            out.append(row)
            open_lots.setdefault(code, []).append(
                {"qty": qty, "px": float(r[5] or 0), "at": r[7]})
        else:
            row = _row(r, "SELL")
            _close(open_lots, code, qty, float(r[5] or 0), row)
            out.append(row)

    # ...and the exits that went out under another hand: same stock, exactly the
    # quantity we are still holding, later in the day
    for r in everything:
        if r[0] in used:
            continue
        code, side, qty = str(r[1]), str(r[3]).upper(), int(r[4] or 0)
        if side != "SELL" or not open_lots.get(code):
            continue
        if not any(l["qty"] == qty for l in open_lots[code]):
            continue
        row = _row((r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[7]), "SELL",
                   {"via": "desk", "matched": True})
        _close(open_lots, code, qty, float(r[5] or 0), row)
        out.append(row)

    out.sort(key=lambda x: (x.get("hhmm") or "", x.get("id") or 0))
    _CACHE[key] = (time.time(), out)
    return out


def _close(open_lots: dict, code: str, qty: int, px: float, row: dict) -> None:
    """Attach the round trip to a sell, FIFO over the lots we still hold."""
    lots = open_lots.get(code) or []
    left, cost, first_at = qty, 0.0, None
    while left > 0 and lots:
        lot = lots[0]
        take = min(left, lot["qty"])
        cost += take * lot["px"]
        first_at = first_at or lot["at"]
        lot["qty"] -= take
        left -= take
        if lot["qty"] <= 0:
            lots.pop(0)
    matched = qty - left
    if matched > 0 and cost:
        bp = cost / matched
        row["buy_price"] = round(bp, 2)
        row["buy_at"] = first_at
        row["pnl_pct"] = round((px / bp - 1) * 100, 2) if bp else None
        row["pnl_won"] = round((px - bp) * matched)
    open_lots[code] = lots
