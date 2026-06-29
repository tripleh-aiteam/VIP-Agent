"""orderbook_memory.py — deep order-book MEMORY across time.

The exchange only publishes 10 bid + 10 ask levels. As price moves, levels scroll
OUT of that 10-deep window and vanish. This module remembers them: every collector
pass we snapshot the 10+10 visible levels, and UPSERT each price level's last-seen
size into `orderbook_memory`. Over minutes we accumulate a 20-30+ level picture
(the user's "disappearing price levels" memory) — used as a trading decision factor.

Also flags UNUSUALLY LARGE orders (walls) per stock — e.g. 삼성전자 ≥10,000 shares,
SK하이닉스 ≥1,000 shares — a big bid wall = support, big ask wall = resistance.

Write path (record/ensure_tables) takes a psycopg2 connection (PC collector).
Read path (read_memory) takes a SQLAlchemy session (orchestrator endpoint).
"""
from __future__ import annotations

from typing import Any

# Per-stock large-order thresholds in SHARES (from the user's day-trading rules).
LARGE_ORDER_SHARES: dict[str, int] = {
    "005930": 1000,    # 삼성전자  (user rule: show ≥1,000 shares)
    "000660": 100,     # SK하이닉스 (user rule: show ≥100 — pricey stock, fewer shares)
}
# default = a ~300M KRW notional wall (shares = 3e8 / price), floored at 100.
_DEFAULT_NOTIONAL = 300_000_000
MEMORY_KEEP_EACH_SIDE = 30        # remember 30 levels above + 30 below the mid


def large_threshold(ticker: str, price: float | None) -> int:
    if ticker in LARGE_ORDER_SHARES:
        return LARGE_ORDER_SHARES[ticker]
    if price and price > 0:
        return max(100, round(_DEFAULT_NOTIONAL / price))
    return 1000


def ensure_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orderbook_levels (
                ts        TIMESTAMPTZ NOT NULL,
                ticker    TEXT NOT NULL,
                side      TEXT NOT NULL,
                level     INT  NOT NULL,
                price     BIGINT NOT NULL,
                qty       BIGINT NOT NULL,
                is_large  BOOLEAN DEFAULT FALSE
            )""")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_ob_levels ON orderbook_levels (ticker, ts)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orderbook_memory (
                ticker     TEXT NOT NULL,
                price      BIGINT NOT NULL,
                side       TEXT NOT NULL,
                last_qty   BIGINT,
                max_qty    BIGINT,
                last_seen  TIMESTAMPTZ,
                seen_count INT DEFAULT 1,
                is_large   BOOLEAN DEFAULT FALSE,
                PRIMARY KEY (ticker, price)
            )""")
    conn.commit()


def record(conn, ticker: str, levels: list[dict], ts) -> dict[str, Any]:
    """Persist one snapshot's 10+10 levels and fold them into the rolling memory.
    Returns {large: n, levels: n} for logging."""
    if not levels:
        return {"large": 0, "levels": 0}
    bids = [l["price"] for l in levels if l.get("side") == "bid" and l.get("price")]
    asks = [l["price"] for l in levels if l.get("side") == "ask" and l.get("price")]
    best_bid = max(bids) if bids else None
    best_ask = min(asks) if asks else None
    mid = ((best_bid + best_ask) / 2) if (best_bid and best_ask) else (best_bid or best_ask)
    thr = large_threshold(ticker, mid)

    large = 0
    with conn.cursor() as cur:
        for l in levels:
            price, qty, side = l.get("price"), l.get("qty") or 0, l.get("side")
            if not price:
                continue
            is_large = qty >= thr
            large += 1 if is_large else 0
            cur.execute(
                "INSERT INTO orderbook_levels (ts, ticker, side, level, price, qty, is_large) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (ts, ticker, side, l.get("level"), price, qty, is_large))
            cur.execute(
                "INSERT INTO orderbook_memory (ticker, price, side, last_qty, max_qty, "
                "last_seen, seen_count, is_large) VALUES (%s,%s,%s,%s,%s,%s,1,%s) "
                "ON CONFLICT (ticker, price) DO UPDATE SET "
                "last_qty=EXCLUDED.last_qty, max_qty=GREATEST(orderbook_memory.max_qty,EXCLUDED.last_qty), "
                "last_seen=EXCLUDED.last_seen, seen_count=orderbook_memory.seen_count+1, "
                "side=EXCLUDED.side, is_large=EXCLUDED.is_large OR orderbook_memory.is_large",
                (ticker, price, side, qty, qty, ts, is_large))
        # prune memory to the 30 nearest levels each side of the mid (bounded growth)
        if mid:
            cur.execute(
                "DELETE FROM orderbook_memory WHERE ticker=%s AND price IN ("
                "  SELECT price FROM ("
                "    SELECT price, ROW_NUMBER() OVER ("
                "      PARTITION BY (price >= %s) ORDER BY abs(price - %s)) rn "
                "    FROM orderbook_memory WHERE ticker=%s) t WHERE rn > %s)",
                (ticker, mid, mid, ticker, MEMORY_KEEP_EACH_SIDE))
        # keep raw levels table from growing forever (last 2 days)
        cur.execute("DELETE FROM orderbook_levels WHERE ticker=%s AND ts < now() - interval '2 days'",
                    (ticker,))
    conn.commit()
    return {"large": large, "levels": len(levels), "threshold": thr}


def read_memory(db, ticker: str, depth: int = 30) -> dict[str, Any]:
    """SQLAlchemy read for the endpoint: the remembered deep book around the mid.
    Returns {asks:[...], bids:[...], threshold, mid} — asks ascending, bids descending,
    each level {price, last_qty, max_qty, is_large, age_sec, seen_count}."""
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT price, side, last_qty, max_qty, is_large, seen_count, "
        "EXTRACT(EPOCH FROM (now()-last_seen))::int AS age_sec "
        "FROM orderbook_memory WHERE ticker=:t ORDER BY price"), {"t": ticker}).fetchall()
    if not rows:
        return {"asks": [], "bids": [], "mid": None, "threshold": None}
    items = [dict(price=int(r[0]), side=r[1], last_qty=int(r[2] or 0), max_qty=int(r[3] or 0),
                  is_large=bool(r[4]), seen_count=int(r[5] or 0), age_sec=int(r[6] or 0))
             for r in rows]
    bids = [x for x in items if x["side"] == "bid"]
    asks = [x for x in items if x["side"] == "ask"]
    mid = None
    if bids and asks:
        mid = (max(b["price"] for b in bids) + min(a["price"] for a in asks)) / 2
    asks = sorted(asks, key=lambda x: x["price"])[:depth]                 # ascending
    bids = sorted(bids, key=lambda x: -x["price"])[:depth]                # descending
    thr = large_threshold(ticker, mid)
    return {"asks": asks, "bids": bids, "mid": mid, "threshold": thr}


def wall_bias(db, ticker: str, near_pct: float = 0.015) -> dict[str, Any]:
    """Large-order-wall decision factor for the Analysis method. A big BID wall just
    below price = support (buyers defending) → +1; a big ASK wall just above =
    resistance (sellers capping) → -1. Uses the remembered deep book.
    Returns {bias: -1/0/1, bid_wall, ask_wall} (walls = {price, max_qty} or None)."""
    mem = read_memory(db, ticker, depth=30)
    mid = mem.get("mid")
    if not mid:
        return {"bias": 0, "bid_wall": None, "ask_wall": None}
    lo, hi = mid * (1 - near_pct), mid * (1 + near_pct)
    bid_walls = [b for b in mem["bids"] if b["is_large"] and lo <= b["price"] <= mid]
    ask_walls = [a for a in mem["asks"] if a["is_large"] and mid <= a["price"] <= hi]
    bw = max(bid_walls, key=lambda x: x["max_qty"]) if bid_walls else None
    aw = max(ask_walls, key=lambda x: x["max_qty"]) if ask_walls else None
    bias = (1 if bw else 0) - (1 if aw else 0)
    return {"bias": bias,
            "bid_wall": {"price": bw["price"], "max_qty": bw["max_qty"]} if bw else None,
            "ask_wall": {"price": aw["price"], "max_qty": aw["max_qty"]} if aw else None}


def live_book(db, ticker: str, fresh_sec: int = 240) -> dict[str, Any]:
    """The most recent 10+10 snapshot the collector wrote (the LIVE visible book).
    fresh=True only when captured within fresh_sec (collector running = in-market)."""
    from sqlalchemy import text
    last_ts = db.execute(text("SELECT max(ts) FROM orderbook_levels WHERE ticker=:t"),
                         {"t": ticker}).scalar()
    if not last_ts:
        return {"levels": [], "as_of": None, "age_sec": None, "fresh": False}
    rows = db.execute(text(
        "SELECT side, level, price, qty, is_large FROM orderbook_levels "
        "WHERE ticker=:t AND ts=:ts ORDER BY side, level"), {"t": ticker, "ts": last_ts}).fetchall()
    age = db.execute(text("SELECT EXTRACT(EPOCH FROM (now()-:ts))::int"), {"ts": last_ts}).scalar()
    return {"levels": [dict(side=r[0], level=int(r[1]), price=int(r[2]),
                            qty=int(r[3] or 0), is_large=bool(r[4])) for r in rows],
            "as_of": str(last_ts), "age_sec": int(age or 0),
            "fresh": (age if age is not None else 99999) <= fresh_sec}  # age 0 IS fresh (don't treat 0 as falsy)


def orderbook_view(db, ticker: str, depth: int = 30) -> dict[str, Any]:
    """Full payload for the frontend depth panel: LIVE 10-deep book + the remembered
    ±depth deep book + large walls. Kiwoom (collector snapshot) while fresh/in-market,
    else NAVER current price (the book is static after close)."""
    live = live_book(db, ticker)
    mem = read_memory(db, ticker, depth)
    fresh = live.get("fresh")
    source = "키움 실시간" if fresh else "NAVER"
    price = None
    if not fresh:                       # after market → Naver current price
        try:
            from services.assistant_agent import _live_price_for_code
            from services.prediction_service import NAMES as _N
            q = _live_price_for_code(ticker, _N.get(ticker))
            price = q and q.get("price")
        except Exception:
            pass
    walls = sorted([x for x in (mem["asks"] + mem["bids"]) if x["is_large"]],
                   key=lambda x: -x["max_qty"])
    return {"ticker": ticker, "source": source, "live": live, "memory": mem,
            "walls": walls, "threshold": mem["threshold"], "mid": mem["mid"],
            "naver_price": price}
