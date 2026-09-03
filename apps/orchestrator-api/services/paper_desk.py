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
import threading
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

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
# ⚡ FAST PRICE LANE (boss 2026-07-22: prices must tick ~1s like the Kiwoom app).
# Kiwoom REST is ~1.5s/quote, so we NEVER block a request on it: fast_price() serves
# the cache instantly and kicks a background refresh (single-flight per code). During
# market hours the cache target is 1s; the continuous 1s polls keep it that fresh.
_KST = ZoneInfo("Asia/Seoul")
_src_cache: dict[str, str] = {}          # ticker -> "kiwoom" | "naver"
_refresh_inflight: set[str] = set()      # single-flight guard (no overlapping fetch/code)
_refresh_lock = threading.Lock()


def _mkt_open() -> bool:
    n = datetime.now(_KST)
    return n.weekday() < 5 and (9 * 60) <= (n.hour * 60 + n.minute) <= (15 * 60 + 30)


def _kick_price_refresh(ticker: str) -> None:
    """Refresh one code's quote in the background (single-flight). Never blocks."""
    with _refresh_lock:
        if ticker in _refresh_inflight:
            return
        _refresh_inflight.add(ticker)

    def _work():
        try:
            _live_price(ticker)          # updates _price_cache/_chg_cache/_src_cache
        except Exception:
            pass
        finally:
            with _refresh_lock:
                _refresh_inflight.discard(ticker)
    threading.Thread(target=_work, name=f"px-{ticker}", daemon=True).start()


def fast_price(ticker: str) -> tuple[Optional[float], Optional[float], float, Optional[str]]:
    """Serve the cached quote INSTANTLY (never blocks on Kiwoom); kick a background
    refresh when the cache is older than the market-hours target (~1s open / 30s closed).
    Returns (price, change_pct, ts, source). First-ever load does ONE sync fetch so the
    price isn't blank."""
    import time as _t
    ticker = str(ticker).zfill(6)
    hit = _price_cache.get(ticker)
    now = _t.time()
    target = 1.0 if _mkt_open() else 30.0
    if hit is None:
        _live_price(ticker)              # cold: one synchronous fetch, then cached
        hit = _price_cache.get(ticker)
    elif now - hit[0] >= target:
        _kick_price_refresh(ticker)      # stale: refresh in background, serve stale now
    px = hit[1] if hit else None
    ts = hit[0] if hit else now
    return px, _chg_cache.get(ticker), ts, _src_cache.get(ticker)


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
    # WHO placed it — 'manual' (boss) / 'algo1' / 'guard' / 'algo2' (boss 2026-07-15:
    # "did the machine trade while OFF?" must be answerable from the record, not memory)
    "ALTER TABLE paper_desk_orders ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual'",
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


_chg_cache: dict[str, float] = {}       # ticker -> today's change_pct (same freshness)

# 체결 fallback (boss 2026-07-15: Kiwoom's tick-execution API returns nothing on
# Render) — we derive deals from the 2s price+cumulative-volume stream that the
# fast price lane already pulls: volume jumped = trades happened; price direction
# vs the last sample approximates the aggressor side. Builds while any page polls.
_deal_hist: dict[str, dict] = {}        # ticker -> {last_vol, last_px, rows: deque}


def _note_deal(ticker: str, px: Optional[float], vol) -> None:
    if px is None or vol is None:
        return
    try:
        from collections import deque as _dq
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _zi
        st = _deal_hist.setdefault(ticker, {"last_vol": None, "last_px": None,
                                            "rows": _dq(maxlen=40)})
        vol = int(vol)
        if st["last_vol"] is not None and vol > st["last_vol"]:
            lp = st["last_px"]
            st["rows"].appendleft({
                "time": _dt.now(_zi("Asia/Seoul")).strftime("%H:%M:%S"),
                "price": px, "qty": vol - st["last_vol"],
                "dir": 1 if (lp is not None and px > lp) else (-1 if (lp is not None and px < lp) else 0),
                "acc_volume": vol})
        st["last_vol"] = vol
        st["last_px"] = px
    except Exception:
        pass


def _live_price(ticker: str) -> tuple[Optional[float], Optional[str]]:
    """LIVE price + name for ANY 6-digit code: Kiwoom first, Naver fallback.
    2s per-ticker micro-cache — caps upstream amplification from request spam.
    Side effect: today's change_pct lands in _chg_cache for the /prices lane."""
    import time as _t
    hit = _price_cache.get(ticker)
    # ~1s freshness during market hours (fast lane), 2s off-hours (caps upstream spam)
    ttl = 1.0 if _mkt_open() else _PRICE_TTL
    if hit and _t.time() - hit[0] < ttl:
        return hit[1], hit[2]
    name = None
    px: Optional[float] = None
    chg: Optional[float] = None
    prev_close = None
    src: Optional[str] = None
    try:
        from services import kiwoom_rest as kr
        q = kr.current_price(ticker)
        if q and q.get("price"):
            px, name = float(q["price"]), (q.get("name") or None)
            chg = q.get("change_pct")
            prev_close = q.get("prev_close")
            src = "kiwoom"
            _note_deal(ticker, px, q.get("volume"))   # feeds the 체결 fallback
    except Exception:
        pass
    if px is None:
        try:
            from services.naver_stock import realtime_quote
            q = realtime_quote(ticker)
            if q and q.get("price"):
                px = float(q["price"])
                chg = q.get("change_pct")
                src = "naver"
        except Exception:
            pass
    # ⚡ LIVE-BAND CLAMP (boss 2026-07-15: "order book moves but the price doesn't"):
    # ka10001/Naver quotes can lag; the order book (ka10004) is the freshest feed
    # that works on Render. The true last-trade price always sits inside the live
    # bid/ask band — so pull our quote into it. Result: the displayed price moves
    # exactly when the book moves.
    try:
        from services import kiwoom_rest as kr3
        ob = kr3.order_book(ticker, ttl=2.0) or {}
        bb, ba = ob.get("best_bid"), ob.get("best_ask")
        old_px = px
        # SANITY BAND (2026-07-28): only pull the price INTO the book when the gap is tiny
        # (<=0.5%). A normal spread is <0.3%; a wide/stale/auction book (bid far above, or
        # ask far below, the last trade) must NOT drag the fill to an impossible price —
        # that's what recorded sells ABOVE the day's high (SK하이닉스 @1,834,000 was +0.99%,
        # 한화에어로 @893,000 was +1.48%). Beyond the band, keep the last-trade price.
        _bb = float(bb) if bb else None
        _ba = float(ba) if ba else None
        if px is not None and _bb and px < _bb and (_bb / px - 1) <= 0.005:
            px = _bb
        if px is not None and _ba and px > _ba and (px / _ba - 1) <= 0.005:
            px = _ba
        if px is None and _bb and _ba:
            px = (_bb + _ba) / 2
        if px is not None and old_px and px != old_px and chg is not None:
            chg = round(float(chg) + (px / old_px - 1) * 100, 2)
    except Exception:
        pass
    if px is not None and prev_close:
        chg = round((px / float(prev_close) - 1) * 100, 2)
    _price_cache[ticker] = (_t.time(), px, name)
    if src:
        _src_cache[ticker] = src
    if chg is not None:
        try:
            _chg_cache[ticker] = float(chg)
        except Exception:
            pass
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
            # 💬 the boss's own chat orders know no ceiling — it is fake money (boss
            # 2026-08-26: "with fake money there should not be limitations"). Print the
            # shortfall into BOTH cash and start_cash so every P&L number stays honest.
            _src9 = db.execute(text(
                "SELECT source FROM paper_desk_orders WHERE id=:i"), {"i": order_id}).scalar()
            if str(_src9 or "") in ("chat", "chatbot"):
                _short = cost - cash
                db.execute(text(
                    "UPDATE paper_desk_account SET cash=cash+:s, start_cash=start_cash+:s "
                    "WHERE id=1"), {"s": _short})
                cash += _short
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
                order_type: str = "market", limit_price: Optional[float] = None,
                source: str = "manual", ref_price: Optional[float] = None,
                direct: bool = False) -> dict:
    _ensure(db)
    ticker = str(ticker).strip().zfill(6)
    side = side.upper()
    # 🤝 SEMI-AUTO (boss 2026-08-25): in "semi" mode an ALGO BUY on a RECO stock becomes
    # a pending suggestion for the human to approve — the final click is his. SELLs
    # always execute (a stop/harvest must never wait), the six always auto-trade, and
    # `direct=True` is the approval path itself.
    if (not direct and side == "BUY" and str(source).startswith("algo")):
        try:
            from services.trade_suggestions import is_reco_stock, suggest, trade_mode
            if trade_mode() == "semi" and is_reco_stock(ticker):
                return suggest(ticker, side, qty, order_type, limit_price, source, ref_price)
        except Exception:
            pass                      # suggestion layer must never block real trading
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
    # 🛡️ CLOSING-AUCTION GUARD (2026-07-28): KRX continuous trading ends 15:20; 15:20-15:30 is
    # a single-price call auction with NO live quote — the order book shows estimated prices
    # that dragged market fills to IMPOSSIBLE values (sells above the day's high). Refuse it.
    if order_type == "market":
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        _t = _dt.now(_tz(_td(hours=9)))
        if 15 * 60 + 20 <= _t.hour * 60 + _t.minute < 15 * 60 + 30:
            return {"ok": False, "error": "closing auction (15:20-15:30) — no live price; order refused"}
    px, kw_name = _live_price(ticker)
    if px is None:
        return {"ok": False, "error": f"no live price for {ticker} — check the code"}
    # WYSIWYG paper fill (boss 2026-07-23): a MARKET order fills at the price the user was
    # LOOKING AT (ref_price = the card's live price) when it's within ±3% of the server's
    # live price — so the realized % matches the % they clicked, not a fresh price fetched a
    # moment later. Outside the band (stale/bad value) we ignore it and use the live price.
    if order_type == "market" and ref_price:
        try:
            rp = float(ref_price)
            if rp > 0 and abs(rp / px - 1) <= 0.01:   # ±1% (was 3%): a stale on-screen price
                px = rp                                # further from the live price is ignored
        except (TypeError, ValueError):
            pass
    name = _name_for(ticker, kw_name)
    oid = db.execute(text(
        "INSERT INTO paper_desk_orders (ticker, name, side, qty, order_type, limit_price, source) "
        "VALUES (:t, :n, :s, :q, :ot, :lp, :src) RETURNING id"),
        {"t": ticker, "n": name, "s": side, "q": qty, "ot": order_type,
         "lp": limit_price, "src": (source or "manual")[:16]}).scalar()
    db.commit()
    if order_type == "market":
        res = _fill(db, oid, ticker, name, side, qty, px)
        return {"ok": res.get("status") == "FILLED", "order_id": oid, "live_price": px, **res}
    return {"ok": True, "order_id": oid, "status": "OPEN", "live_price": px,
            "note": f"{'매수' if side == 'BUY' else '매도'} 대기: 현재가가 {limit_price:,.0f}에 "
                    f"{'내려오면' if side == 'BUY' else '올라가면'} 체결"}


_CHAT_MGR_DDL_DONE = {"v": False}


def manage_chat_positions(db) -> list[dict]:
    """알고2 SELL management for the boss's chat positions (boss 2026-08-26:
    'please manage selling case in the Algo 2 case — if we gain 1% sell 10%
    like this'). SELL side only — buying stays the boss's own hand:
      · +1% ladder: rungs at +0.85%, +1.85%, +2.85%... each sells 10% of the
        managed size, once (알고2's exact band rule)
      · −1% guard: price at base×0.99 → sell everything, management ends
    Applies to positions bought through the chatbot (order source chat/chatbot).
    Re-buying more re-bases the ladder on the new blended average. Fills are
    stamped source='algo2-chat' so the history shows the machine's hand."""
    if not _mkt_open():
        return []
    if not _CHAT_MGR_DDL_DONE["v"]:
        # PER-LOT (boss 2026-08-26: "I checked in both menus it shows more
        # than 1%" — the boards measure each chat buy from ITS OWN fill price,
        # so the manager must too; one buy = one managed lot, its own ladder)
        db.execute(text(
            "CREATE TABLE IF NOT EXISTS paper_desk_chat_lots ("
            "order_id INT PRIMARY KEY, ticker TEXT, base DOUBLE PRECISION, "
            "qty0 INT, sold_qty INT DEFAULT 0, k_up INT DEFAULT 0, "
            "done BOOLEAN DEFAULT FALSE, updated_at TIMESTAMPTZ DEFAULT now())"))
        db.commit()
        _CHAT_MGR_DDL_DONE["v"] = True
    fills: list[dict] = []
    lots = db.execute(text(
        "SELECT o.id, o.ticker, o.name, o.qty, o.fill_price FROM paper_desk_orders o "
        "WHERE o.side='BUY' AND o.status='FILLED' AND o.source IN ('chat','chatbot') "
        "ORDER BY o.id")).fetchall()
    for oid, tk, nm, oqty, fpx in lots:
        if not fpx:
            continue
        st9 = db.execute(text(
            "SELECT base, qty0, sold_qty, k_up, done FROM paper_desk_chat_lots "
            "WHERE order_id=:i"), {"i": oid}).first()
        if st9 is None:
            db.execute(text(
                "INSERT INTO paper_desk_chat_lots (order_id, ticker, base, qty0) "
                "VALUES (:i, :t, :b, :q) ON CONFLICT DO NOTHING"),
                {"i": oid, "t": tk, "b": float(fpx), "q": int(oqty)})
            db.commit()
            base, qty0, sold, k_up, done = float(fpx), int(oqty), 0, 0, False
        else:
            base, qty0, sold, k_up, done = (float(st9[0]), int(st9[1]),
                                            int(st9[2]), int(st9[3]), bool(st9[4]))
        if done or sold >= qty0:
            continue
        held = int(db.execute(text(
            "SELECT qty FROM paper_desk_positions WHERE ticker=:t"),
            {"t": tk}).scalar() or 0)
        if held <= 0:
            db.execute(text(
                "UPDATE paper_desk_chat_lots SET done=TRUE, updated_at=now() "
                "WHERE order_id=:i"), {"i": oid})
            db.commit()
            continue
        px, _src = _live_price(tk)
        if not px:
            continue
        remain = min(qty0 - sold, held)
        # 🔔 THE BELL (boss 2026-08-26: "need to sell 15:20 ok" — chat lots keep
        # the desk's discipline): at 15:19, one minute before the closing
        # auction kills the live price, every managed chat lot goes flat.
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        _nk9 = _dt.now(_tz(_td(hours=9)))
        if _nk9.hour * 60 + _nk9.minute >= 15 * 60 + 19:
            r = place_order(db, tk, "SELL", remain, "market",
                            source="algo2-chat", direct=True)
            if r.get("ok"):
                fills.append({"ticker": tk, "name": nm, "why": "15:19 bell",
                              "qty": remain, "px": r.get("live_price")})
                db.execute(text(
                    "UPDATE paper_desk_chat_lots SET sold_qty=sold_qty+:q, "
                    "done=TRUE, updated_at=now() WHERE order_id=:i"),
                    {"q": remain, "i": oid})
                db.commit()
            continue
        # −1% guard (vs THIS lot's own buy price): sell the lot's remainder
        if px <= base * 0.99:
            r = place_order(db, tk, "SELL", remain, "market",
                            source="algo2-chat", direct=True)
            if r.get("ok"):
                fills.append({"ticker": tk, "name": nm, "why": "-1% guard",
                              "qty": remain, "px": r.get("live_price")})
                db.execute(text(
                    "UPDATE paper_desk_chat_lots SET sold_qty=sold_qty+:q, "
                    "done=TRUE, updated_at=now() WHERE order_id=:i"),
                    {"q": remain, "i": oid})
                db.commit()
            continue
        # +1% ladder rung vs the lot's own price (알고2 band +0.85/+1.85/...)
        lvl = base * (1 + ((k_up + 1) * 1.0 - 0.15) / 100)
        if px >= lvl:
            q9 = min(max(1, int(qty0 * 0.10)), remain)
            r = place_order(db, tk, "SELL", q9, "market",
                            source="algo2-chat", direct=True)
            if r.get("ok"):
                fills.append({"ticker": tk, "name": nm, "why": f"+{k_up + 1}% rung",
                              "qty": q9, "px": r.get("live_price")})
                db.execute(text(
                    "UPDATE paper_desk_chat_lots SET sold_qty=sold_qty+:q, "
                    "k_up=k_up+1, done=(sold_qty+:q >= qty0), updated_at=now() "
                    "WHERE order_id=:i"), {"q": q9, "i": oid})
                db.commit()
    return fills


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
            continue
        # THE GIVE-UP LAW (boss 2026-09-03: "if we offer price and it will not
        # reach, it should give up and cancel"): once the live price runs away
        # beyond the stock's studied give-up distance, a comeback fill is a
        # falling knife on average — cancel instead of waiting all day.
        try:
            from services.giveup_rule import giveup_won, should_give_up
            if should_give_up(side, lp, px, ticker):
                d = giveup_won(ticker, lp)
                db.execute(text(
                    "UPDATE paper_desk_orders SET status='CANCELLED', "
                    "note=:n WHERE id=:i AND status='OPEN'"),
                    {"i": oid,
                     "n": f"🏳 포기 (give-up rule): 현재가 ₩{px:,.0f}가 제안가 "
                          f"₩{lp:,.0f}에서 ₩{d:,.0f} 이상 멀어짐"})
                db.commit()
        except Exception:
            db.rollback()
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
    _guard_alerts: list = []
    try:
        from services.position_guard import run as _guard_run
        _guard_alerts = (_guard_run(db) or {}).get("alerts") or []
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
        # NET-OF-FEES unrealized (boss 2026-07-13: clicked sell at +2.0%, realized
        # +1.81% — the display was GROSS while realized subtracts the 0.23% costs;
        # what he sees must equal what a sell would actually bank right now)
        upnl = upct = None
        if px:
            _proceeds = px * qty * (1 - SELL_COST_PCT / 100)
            _basis = avg * qty * (1 + BUY_COST_PCT / 100)
            upnl = _proceeds - _basis
            upct = (upnl / _basis * 100) if _basis else None
        positions.append({
            "ticker": t, "name": name, "qty": qty, "avg_price": round(avg, 0),
            "live_price": px, "value": round(val, 0),
            "unrealized_pnl": round(upnl, 0) if upnl is not None else None,
            "unrealized_pnl_pct": round(upct, 2) if upct is not None else None,
        })
    open_orders = [dict(r._mapping) for r in db.execute(text(
        "SELECT id, ticker, name, side, qty, order_type, limit_price, created_at "
        "FROM paper_desk_orders WHERE status='OPEN' ORDER BY id DESC"))]
    history = [dict(r._mapping) for r in db.execute(text(
        "SELECT id, ticker, name, side, qty, order_type, limit_price, status, fill_price, "
        "realized_pnl, realized_pnl_pct, note, created_at, filled_at, source "
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
        "guard_alerts": _guard_alerts,
        "reset_at": str(acct[2]),
        "costs": {"buy_pct": BUY_COST_PCT, "sell_pct": SELL_COST_PCT},
    }
