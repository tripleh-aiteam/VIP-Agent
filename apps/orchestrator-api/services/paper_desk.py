"""paper_desk.py — the boss's MANUAL fake-money trading desk (Testing menu).

He tests the chatbot + decision engine with his own hands: ask the bot, place a
virtual BUY/SELL on ANY 6-digit code at the LIVE Kiwoom price (Naver fallback), or
park a limit order ("if it reaches this price, buy/sell") that fills when the live
price touches it. The desk tracks cash, positions, realized/unrealized P&L and a
win/loss record — the human-in-the-loop counterpart of the automatic paper_trader.

Fills are honest: market orders fill at the current live price; limit BUYs fill when
price <= limit, limit SELLs when price >= limit (fill price = the limit). Costs use
the same round-trip constant as the auto paper trader, split per side.

Single account (id=1) — this is the boss's desk, not a multi-user product.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger("vip.paper_desk")

START_CASH = 100_000_000        # ₩1억 default fake bankroll
BUY_COST_PCT = 0.015            # commission per side (%)
SELL_COST_PCT = 0.215           # commission + transaction tax (%)

# --- abuse guards (2026-07-07 security review): these endpoints are public like the
# rest of /predictions, but they WRITE and they fan out to live Kiwoom/Naver quotes —
# unthrottled spam could burn the Kiwoom rate limit (breaking the real data feed) and
# grow the orders table unboundedly. In-process caps keep the blast radius tiny.
MAX_QTY = 1_000_000             # shares per order
MAX_OPEN_ORDERS = 30
_PRICE_TTL = 2.0                # per-ticker live-quote micro-cache (seconds)
_price_cache: dict[str, tuple[float, Optional[float], Optional[str]]] = {}
_rate: dict[str, list[float]] = {}


def _allow(bucket: str, per_min: int) -> bool:
    """Tiny in-process rate limiter (per instance). True = allowed."""
    import time as _t
    now = _t.time()
    q = [ts for ts in _rate.get(bucket, []) if now - ts < 60]
    if len(q) >= per_min:
        _rate[bucket] = q
        return False
    q.append(now)
    _rate[bucket] = q
    return True

_DDL = (
    "CREATE TABLE IF NOT EXISTS paper_desk_account ("
    " id INT PRIMARY KEY DEFAULT 1, cash DOUBLE PRECISION, start_cash DOUBLE PRECISION,"
    " created_at TIMESTAMPTZ DEFAULT now(), reset_at TIMESTAMPTZ DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS paper_desk_positions ("
    " ticker TEXT PRIMARY KEY, name TEXT, qty BIGINT, avg_price DOUBLE PRECISION,"
    " opened_at TIMESTAMPTZ DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS paper_desk_orders ("
    " id SERIAL PRIMARY KEY, ticker TEXT, name TEXT, side TEXT, qty BIGINT,"
    " order_type TEXT, limit_price DOUBLE PRECISION, status TEXT DEFAULT 'OPEN',"
    " fill_price DOUBLE PRECISION, realized_pnl DOUBLE PRECISION,"
    " realized_pnl_pct DOUBLE PRECISION, note TEXT,"
    " created_at TIMESTAMPTZ DEFAULT now(), filled_at TIMESTAMPTZ)",
)


def _ensure(db) -> None:
    for ddl in _DDL:
        db.execute(text(ddl))
    r = db.execute(text("SELECT 1 FROM paper_desk_account WHERE id=1")).first()
    if not r:
        db.execute(text(
            "INSERT INTO paper_desk_account (id, cash, start_cash) VALUES (1, :c, :c)"),
            {"c": START_CASH})
    db.commit()


def _live_price(ticker: str) -> tuple[Optional[float], Optional[str]]:
    """LIVE price + name for ANY 6-digit code: Kiwoom first, Naver fallback.
    2s per-ticker micro-cache — caps upstream amplification from request spam."""
    import time as _t
    hit = _price_cache.get(ticker)
    if hit and _t.time() - hit[0] < _PRICE_TTL:
        return hit[1], hit[2]
    name = None
    px: Optional[float] = None
    try:
        from services import kiwoom_rest as kr
        q = kr.current_price(ticker)
        if q and q.get("price"):
            px, name = float(q["price"]), (q.get("name") or None)
    except Exception:
        pass
    if px is None:
        try:
            from services.naver_stock import realtime_quote
            q = realtime_quote(ticker)
            if q and q.get("price"):
                px = float(q["price"])
        except Exception:
            pass
    _price_cache[ticker] = (_t.time(), px, name)
    if len(_price_cache) > 500:          # bound the cache itself
        _price_cache.pop(next(iter(_price_cache)))
    return px, name


def _name_for(ticker: str, fallback: Optional[str] = None) -> str:
    try:
        from services.prediction_service import NAMES
        if ticker in NAMES:
            return NAMES[ticker]
    except Exception:
        pass
    # krx_stocks covers ALL 2,873 listed names (positions/history must never show a bare code)
    try:
        from sqlalchemy import text as _sql

        from db.base import engine
        with engine.connect() as c:
            r = c.execute(_sql("SELECT name FROM krx_stocks WHERE code=:c"),
                          {"c": ticker}).first()
        if r and r[0]:
            return str(r[0])
    except Exception:
        pass
    try:
        from services.stock_resolver import name_of  # optional helper
        n = name_of(ticker)
        if n:
            return n
    except Exception:
        pass
    return fallback or ticker


def _apply_buy(db, ticker: str, name: str, qty: int, px: float) -> None:
    pos = db.execute(text(
        "SELECT qty, avg_price FROM paper_desk_positions WHERE ticker=:t"),
        {"t": ticker}).first()
    if pos:
        new_qty = int(pos[0]) + qty
        new_avg = (float(pos[1]) * int(pos[0]) + px * qty) / new_qty
        db.execute(text(
            "UPDATE paper_desk_positions SET qty=:q, avg_price=:a, name=:n WHERE ticker=:t"),
            {"q": new_qty, "a": new_avg, "n": name, "t": ticker})
    else:
        db.execute(text(
            "INSERT INTO paper_desk_positions (ticker, name, qty, avg_price) "
            "VALUES (:t, :n, :q, :a)"), {"t": ticker, "n": name, "q": qty, "a": px})


def _fill(db, order_id: int, ticker: str, name: str, side: str, qty: int, px: float) -> dict:
    """Execute a fill: move cash, update the position, close the order. Returns fill info."""
    cash = float(db.execute(text(
        "SELECT cash FROM paper_desk_account WHERE id=1")).scalar() or 0)
    realized = realized_pct = None
    if side == "BUY":
        cost = px * qty * (1 + BUY_COST_PCT / 100)
        if cost > cash + 1e-6:
            db.execute(text(
                "UPDATE paper_desk_orders SET status='REJECTED', note='잔고 부족 (insufficient cash)' "
                "WHERE id=:i"), {"i": order_id})
            db.commit()
            return {"status": "REJECTED", "reason": "insufficient cash"}
        db.execute(text("UPDATE paper_desk_account SET cash=cash-:c WHERE id=1"), {"c": cost})
        _apply_buy(db, ticker, name, qty, px)
    else:                                   # SELL
        pos = db.execute(text(
            "SELECT qty, avg_price FROM paper_desk_positions WHERE ticker=:t"),
            {"t": ticker}).first()
        held = int(pos[0]) if pos else 0
        if held < qty:
            db.execute(text(
                "UPDATE paper_desk_orders SET status='REJECTED', note='보유 수량 부족 (not enough shares)' "
                "WHERE id=:i"), {"i": order_id})
            db.commit()
            return {"status": "REJECTED", "reason": "not enough shares"}
        avg = float(pos[1])
        proceeds = px * qty * (1 - SELL_COST_PCT / 100)
        buy_cost = avg * qty * (1 + BUY_COST_PCT / 100)
        realized = round(proceeds - buy_cost, 0)
        realized_pct = round((proceeds - buy_cost) / buy_cost * 100, 2)
        db.execute(text("UPDATE paper_desk_account SET cash=cash+:c WHERE id=1"), {"c": proceeds})
        if held == qty:
            db.execute(text("DELETE FROM paper_desk_positions WHERE ticker=:t"), {"t": ticker})
        else:
            db.execute(text(
                "UPDATE paper_desk_positions SET qty=qty-:q WHERE ticker=:t"),
                {"q": qty, "t": ticker})
    db.execute(text(
        "UPDATE paper_desk_orders SET status='FILLED', fill_price=:p, filled_at=now(), "
        "realized_pnl=:r, realized_pnl_pct=:rp WHERE id=:i"),
        {"p": px, "r": realized, "rp": realized_pct, "i": order_id})
    db.commit()
    return {"status": "FILLED", "fill_price": px,
            "realized_pnl": realized, "realized_pnl_pct": realized_pct}


def place_order(db, ticker: str, side: str, qty: int,
                order_type: str = "market", limit_price: Optional[float] = None) -> dict:
    _ensure(db)
    ticker = str(ticker).strip().zfill(6)
    side = side.upper()
    order_type = order_type.lower()
    if side not in ("BUY", "SELL") or qty <= 0:
        return {"ok": False, "error": "side must be BUY/SELL and qty > 0"}
    if qty > MAX_QTY:
        return {"ok": False, "error": f"qty > {MAX_QTY:,} not allowed"}
    if order_type == "limit" and not limit_price:
        return {"ok": False, "error": "limit order needs limit_price"}
    if not _allow("order", per_min=20):
        return {"ok": False, "error": "too many orders — wait a moment"}
    n_open = db.execute(text(
        "SELECT count(*) FROM paper_desk_orders WHERE status='OPEN'")).scalar() or 0
    if order_type == "limit" and int(n_open) >= MAX_OPEN_ORDERS:
        return {"ok": False, "error": f"open limit orders capped at {MAX_OPEN_ORDERS}"}
    px, kw_name = _live_price(ticker)
    if px is None:
        return {"ok": False, "error": f"no live price for {ticker} — check the code"}
    name = _name_for(ticker, kw_name)
    oid = db.execute(text(
        "INSERT INTO paper_desk_orders (ticker, name, side, qty, order_type, limit_price) "
        "VALUES (:t, :n, :s, :q, :ot, :lp) RETURNING id"),
        {"t": ticker, "n": name, "s": side, "q": qty, "ot": order_type,
         "lp": limit_price}).scalar()
    db.commit()
    if order_type == "market":
        res = _fill(db, oid, ticker, name, side, qty, px)
        return {"ok": res.get("status") == "FILLED", "order_id": oid, "live_price": px, **res}
    return {"ok": True, "order_id": oid, "status": "OPEN", "live_price": px,
            "note": f"{'매수' if side == 'BUY' else '매도'} 대기: 현재가가 {limit_price:,.0f}에 "
                    f"{'내려오면' if side == 'BUY' else '올라가면'} 체결"}


def check_limit_orders(db) -> int:
    """Fill OPEN limit orders whose trigger the live price has touched. Returns fills."""
    _ensure(db)
    rows = db.execute(text(
        "SELECT id, ticker, name, side, qty, limit_price FROM paper_desk_orders "
        "WHERE status='OPEN' AND order_type='limit' ORDER BY id")).fetchall()
    fills = 0
    price_cache: dict[str, Optional[float]] = {}
    for oid, ticker, name, side, qty, lp in rows:
        if ticker not in price_cache:
            price_cache[ticker], _ = _live_price(ticker)
        px = price_cache[ticker]
        if px is None:
            continue
        lp = float(lp)
        if (side == "BUY" and px <= lp) or (side == "SELL" and px >= lp):
            # fill at the LIVE price, not the limit: when the trigger is crossed the
            # market price is what's actually available (and it's never worse than
            # the limit — px<=lp for BUYs, px>=lp for SELLs)
            res = _fill(db, oid, ticker, name, side, int(qty), px)
            if res.get("status") == "FILLED":
                fills += 1
    return fills


def cancel_order(db, order_id: int) -> dict:
    n = db.execute(text(
        "UPDATE paper_desk_orders SET status='CANCELLED' WHERE id=:i AND status='OPEN'"),
        {"i": order_id}).rowcount
    db.commit()
    return {"ok": n > 0}


def reset(db, cash: float = START_CASH) -> dict:
    if not (1_000_000 <= cash <= 10_000_000_000):
        return {"ok": False, "error": "cash must be between ₩1M and ₩100억"}
    if not _allow("reset", per_min=2):
        return {"ok": False, "error": "reset throttled — wait a minute"}
    _ensure(db)
    # a reset = a FRESH experiment: clear the whole trade history too (boss saw the
    # build-time test orders polluting his record, 2026-07-07)
    db.execute(text("DELETE FROM paper_desk_orders"))
    db.execute(text("DELETE FROM paper_desk_positions"))
    db.execute(text("UPDATE paper_desk_orders SET status='CANCELLED' WHERE status='OPEN'"))
    db.execute(text(
        "UPDATE paper_desk_account SET cash=:c, start_cash=:c, reset_at=now() WHERE id=1"),
        {"c": cash})
    db.commit()
    return {"ok": True, "cash": cash}


def day_report(db) -> dict[str, Any]:
    """📊 Today's per-stock trading summary (boss 2026-07-10: 'after market it should
    show per stock result'): for each stock traded today (KST) — buys/sells, money in
    and out, realized ₩ and avg %, win/lose count; plus account totals."""
    _ensure(db)
    rows = db.execute(text(
        "SELECT ticker, name, side, qty, fill_price, realized_pnl, realized_pnl_pct "
        "FROM paper_desk_orders WHERE status='FILLED' "
        "AND COALESCE(filled_at, created_at)::date = (now() AT TIME ZONE 'Asia/Seoul')::date"
    )).fetchall()
    agg: dict[str, dict[str, Any]] = {}
    for tk, name, side, qty, px, rl, rlp in rows:
        a = agg.setdefault(tk, {"ticker": tk, "name": name, "buys": 0, "sells": 0,
                                "bought_value": 0.0, "sold_value": 0.0, "realized": 0.0,
                                "pcts": [], "wins": 0, "losses": 0})
        val = float(qty or 0) * float(px or 0)
        if side == "BUY":
            a["buys"] += 1
            a["bought_value"] += val
        else:
            a["sells"] += 1
            a["sold_value"] += val
            if rl is not None:
                a["realized"] += float(rl)
                if float(rl) > 0:
                    a["wins"] += 1
                elif float(rl) < 0:
                    a["losses"] += 1
            if rlp is not None:
                a["pcts"].append(float(rlp))
    stocks = []
    for a in agg.values():
        pcts = a.get("pcts") or []
        stocks.append({**{k: v for k, v in a.items() if k != "pcts"},
                       "avg_pct": round(sum(pcts) / len(pcts), 2) if pcts else None,
                       "realized": round(a["realized"], 0),
                       "bought_value": round(a["bought_value"], 0),
                       "sold_value": round(a["sold_value"], 0)})
    stocks.sort(key=lambda x: x["realized"])
    tot_realized = round(sum(s["realized"] for s in stocks), 0)
    return {"date_kst": None, "stocks": stocks,
            "totals": {"stocks_traded": len(stocks),
                       "buys": sum(s["buys"] for s in stocks),
                       "sells": sum(s["sells"] for s in stocks),
                       "wins": sum(s["wins"] for s in stocks),
                       "losses": sum(s["losses"] for s in stocks),
                       "realized": tot_realized}}


def deposit(db, amount: float) -> dict:
    """Add fake money (boss 2026-07-10: a 'fill money' button). start_cash rises by the
    same amount so the P&L% stays honest — a deposit is not a profit."""
    try:
        amount = float(amount)
    except Exception:
        return {"ok": False, "error": "bad amount"}
    if not (1_000_000 <= amount <= 1_000_000_000):
        return {"ok": False, "error": "amount must be between ₩100만 and ₩10억"}
    if not _allow("deposit", per_min=3):
        return {"ok": False, "error": "deposit throttled — wait a minute"}
    _ensure(db)
    db.execute(text(
        "UPDATE paper_desk_account SET cash=cash+:a, start_cash=start_cash+:a WHERE id=1"),
        {"a": amount})
    db.commit()
    r = db.execute(text("SELECT cash FROM paper_desk_account WHERE id=1")).first()
    return {"ok": True, "added": amount, "cash": float(r[0]) if r else None}


def state(db) -> dict[str, Any]:
    """Everything the Testing page renders. Also triggers pending limit fills (so the
    page's poll IS the fill engine — no separate cron needed) and the POSITION GUARD
    (boss 2026-07-10: focus holdings auto-sell at -1% / peak-1% — 'immediately', and
    this 4s poll is the fastest heartbeat we have)."""
    try:
        from services.position_guard import run as _guard_run
        _guard_run(db)
    except Exception:
        db.rollback()
    _ensure(db)
    try:
        check_limit_orders(db)
    except Exception:
        db.rollback()
    acct = db.execute(text(
        "SELECT cash, start_cash, reset_at FROM paper_desk_account WHERE id=1")).first()
    cash, start_cash = float(acct[0]), float(acct[1])
    positions = []
    pos_value = 0.0
    for t, name, qty, avg in db.execute(text(
            "SELECT ticker, name, qty, avg_price FROM paper_desk_positions ORDER BY opened_at")):
        px, _ = _live_price(t)
        qty = int(qty)
        avg = float(avg)
        val = (px or avg) * qty
        pos_value += val
        upnl = (px - avg) * qty if px else None
        positions.append({
            "ticker": t, "name": name, "qty": qty, "avg_price": round(avg, 0),
            "live_price": px, "value": round(val, 0),
            "unrealized_pnl": round(upnl, 0) if upnl is not None else None,
            "unrealized_pnl_pct": round((px - avg) / avg * 100, 2) if px else None,
        })
    open_orders = [dict(r._mapping) for r in db.execute(text(
        "SELECT id, ticker, name, side, qty, order_type, limit_price, created_at "
        "FROM paper_desk_orders WHERE status='OPEN' ORDER BY id DESC"))]
    history = [dict(r._mapping) for r in db.execute(text(
        "SELECT id, ticker, name, side, qty, order_type, limit_price, status, fill_price, "
        "realized_pnl, realized_pnl_pct, note, created_at, filled_at "
        "FROM paper_desk_orders WHERE status IN ('FILLED','REJECTED','CANCELLED') "
        "ORDER BY COALESCE(filled_at, created_at) DESC LIMIT 400"))]
    sells = [h for h in history if h["side"] == "SELL" and h["status"] == "FILLED"
             and h.get("realized_pnl") is not None]
    wins = sum(1 for h in sells if h["realized_pnl"] > 0)
    realized_total = round(sum(float(h["realized_pnl"]) for h in sells), 0)
    equity = cash + pos_value
    return {
        "cash": round(cash, 0), "start_cash": round(start_cash, 0),
        "positions_value": round(pos_value, 0), "equity": round(equity, 0),
        "total_pnl": round(equity - start_cash, 0),
        "total_pnl_pct": round((equity - start_cash) / start_cash * 100, 2),
        "realized_pnl": realized_total,
        "record": {"trades": len(sells), "wins": wins,
                   "win_rate": round(wins / len(sells) * 100, 1) if sells else None},
        "positions": positions, "open_orders": open_orders, "history": history,
        "reset_at": str(acct[2]),
        "costs": {"buy_pct": BUY_COST_PCT, "sell_pct": SELL_COST_PCT},
    }
