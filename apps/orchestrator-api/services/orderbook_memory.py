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

import time as _time
from typing import Any

# last-good direct-Kiwoom payloads (throttle-burst resilience, per ticker)
_last_good: dict[str, tuple] = {}
_last_trades: dict[str, tuple] = {}
_last_quote: dict[str, tuple] = {}
_watch_written: dict[str, float] = {}   # hot_watch UPSERT throttle (per ticker)


# ---------------------------------------------------------------------------
# PC HOT-RELAY (2026-07-06). Kiwoom's 지정단말기 allowlist rejects Render's
# outbound IP after some deploys (8050 — the IP is a per-instance lottery inside
# 74.220.52/60.0/24; only a few are registered). The PC's IP IS registered, so the
# PC collector relays true real-time Kiwoom data through Supabase: Render writes
# WHICH ticker is being watched (hot_watch), the PC bursts that ticker's book +
# fills + quote into kiwoom_hot every ~1s, Render serves it when direct fails.
# ---------------------------------------------------------------------------
def _hot_watch_write(db, ticker: str) -> None:
    """Tell the PC which tickers are being viewed (throttled, never raises).
    ROW PER TICKER (2026-07-07): the original single-row hot_watch thrashed when the
    VIP monitor, the AI Advisor monitor and any probe watched DIFFERENT stocks — the
    WS feed resubscribed in a loop and fills froze. Multiple viewers are normal now."""
    nowt = _time.time()
    if nowt - _watch_written.get(ticker, 0) < 5:
        return
    _watch_written[ticker] = nowt
    try:
        from sqlalchemy import text as _sql
        db.execute(_sql(
            "CREATE TABLE IF NOT EXISTS hot_watch_multi (ticker TEXT PRIMARY KEY, "
            "requested_at TIMESTAMPTZ DEFAULT now())"))
        db.execute(_sql(
            "INSERT INTO hot_watch_multi (ticker, requested_at) VALUES (:t, now()) "
            "ON CONFLICT (ticker) DO UPDATE SET requested_at=now()"), {"t": ticker})
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _hot_read(db, ticker: str) -> dict | None:
    """The PC-relayed live payload + its age. None if absent/unreadable."""
    try:
        import json as _json

        from sqlalchemy import text as _sql
        r = db.execute(_sql(
            "SELECT payload, EXTRACT(EPOCH FROM (now()-updated_at)) "
            "FROM kiwoom_hot WHERE ticker=:t"), {"t": ticker}).first()
        if not r or r[0] is None:
            return None
        p = r[0] if isinstance(r[0], dict) else _json.loads(r[0])
        return {"payload": p, "age_sec": float(r[1] or 999)}
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None

# Per-stock large-order thresholds in SHARES (from the user's day-trading rules).
LARGE_ORDER_SHARES: dict[str, int] = {
    "005930": 1000,    # 삼성전자  (user rule: show ≥1,000 shares)
    "000660": 100,     # SK하이닉스 (user rule: show ≥100 — pricey stock, fewer shares)
}
# default = a ~300M KRW notional wall (shares = 3e8 / price), floored at 100.
_DEFAULT_NOTIONAL = 300_000_000
MEMORY_KEEP_EACH_SIDE = 200       # keep a WIDE band so levels accumulate over days/weeks
                                  # (never forget within ~200 ticks of price) → calm stocks
                                  # build up to 30/side over time and stay there.


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
    each level {price, last_qty, max_qty, is_large, age_sec}.

    Source = the RAW orderbook_levels history (last ~2 days), NOT the rolling
    orderbook_memory table. The rolling table prunes to the N levels nearest the
    *current* mid, so on a stock that ranged far intraday (e.g. opened 359k, now 331k)
    it deletes the higher asks and one side collapses. The raw history kept every
    price the market ever showed, so taking the LAST-seen qty per price recovers the
    full depth → 30 real levels on BOTH sides. Classify by the current best bid/ask."""
    from sqlalchemy import text
    # source: the rolling orderbook_memory (one row per price, accumulates last-seen over
    # time, pruned only to ±MEMORY_KEEP_EACH_SIDE of the mid). Cheap (hundreds of rows).
    rows = db.execute(text(
        "SELECT price, last_qty, max_qty, is_large, "
        "EXTRACT(EPOCH FROM (now()-last_seen))::int AS age_sec "
        "FROM orderbook_memory WHERE ticker=:t"), {"t": ticker}).fetchall()
    if not rows:
        return {"asks": [], "bids": [], "mid": None, "threshold": None}
    items = [dict(price=int(r[0]), last_qty=int(r[1] or 0), max_qty=int(r[2] or 0),
                  is_large=bool(r[3]), age_sec=int(r[4] or 0)) for r in rows]
    # current live best bid/ask (classify by price vs these, not a stale stored side)
    snap = db.execute(text(
        "SELECT side, price FROM orderbook_levels WHERE ticker=:t AND "
        "ts=(SELECT max(ts) FROM orderbook_levels WHERE ticker=:t)"), {"t": ticker}).fetchall()
    cur_asks = [int(p) for s, p in snap if s == "ask"]
    cur_bids = [int(p) for s, p in snap if s == "bid"]
    best_ask = min(cur_asks) if cur_asks else None
    best_bid = max(cur_bids) if cur_bids else None
    if best_ask and best_bid:
        mid = (best_ask + best_bid) / 2
    else:                                       # no fresh snapshot (after close) → use median
        prices = sorted(x["price"] for x in items)
        mid = prices[len(prices) // 2] if prices else None
        best_ask = best_bid = mid
    asks = sorted([x for x in items if x["price"] >= best_ask], key=lambda x: x["price"])[:depth]
    bids = sorted([x for x in items if x["price"] <= best_bid], key=lambda x: -x["price"])[:depth]
    thr = large_threshold(ticker, mid)
    return {"asks": asks, "bids": bids, "mid": mid, "threshold": thr}


def backfill_from_raw(conn) -> dict[str, Any]:
    """One-time (psycopg2): seed orderbook_memory from ALL raw orderbook_levels history,
    so the deep book has its full accumulated depth immediately (then record() keeps
    adding). Safe to re-run — upsert keeps the freshest/biggest per price."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO orderbook_memory "
            "(ticker, price, side, last_qty, max_qty, last_seen, seen_count, is_large) "
            "SELECT ticker, price, "
            "       (array_agg(side ORDER BY ts DESC))[1], "
            "       (array_agg(qty  ORDER BY ts DESC))[1], "
            "       max(qty), max(ts), count(*), bool_or(is_large) "
            "FROM orderbook_levels GROUP BY ticker, price "
            "ON CONFLICT (ticker, price) DO UPDATE SET "
            "  last_qty=EXCLUDED.last_qty, "
            "  max_qty=GREATEST(orderbook_memory.max_qty, EXCLUDED.max_qty), "
            "  last_seen=GREATEST(orderbook_memory.last_seen, EXCLUDED.last_seen), "
            "  seen_count=GREATEST(orderbook_memory.seen_count, EXCLUDED.seen_count), "
            "  is_large=orderbook_memory.is_large OR EXCLUDED.is_large")
        n = cur.rowcount
    conn.commit()
    return {"upserted": n}


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
    ±depth deep book + large walls + tick EXECUTIONS (체결).

    REAL-TIME path (2026-07-06): the live book + executions come STRAIGHT from Kiwoom
    REST (~1s micro-cache) — the old path read the PC collector's DB snapshot (~30s
    stale), which made the monitor crawl. Render's IPs are Kiwoom-registered, so this
    works with no PC. Falls back to the collector snapshot, then NAVER price after close."""
    mem = read_memory(db, ticker, depth)

    # 1) LIVE book — direct Kiwoom first (true real-time), snapshot fallback.
    # THROTTLE RESILIENCE: the PC collector bursts ~100 calls/30s on the same key, so a
    # direct call can fail for a few seconds — serve the LAST GOOD direct payload
    # (≤15s book / ≤30s trades / ≤10s quote) instead of flip-flopping to the slow path.
    live: dict[str, Any] = {}
    trades: list = []
    trades_age = 0
    quote: dict[str, Any] | None = None
    source = None
    now = _time.time()
    try:
        from services import kiwoom_rest as kr
        kb = kr.order_book(ticker, ttl=1.0)
        if kb and kb.get("levels"):
            _last_good[ticker] = (now, kb)
        elif ticker in _last_good and now - _last_good[ticker][0] <= 15:
            kb = _last_good[ticker][1]
        if kb and kb.get("levels"):
            live = {"levels": kb["levels"], "as_of": None, "age_sec": 0, "fresh": True,
                    "imbalance": kb.get("imbalance"),
                    "tot_bid": kb.get("tot_bid"), "tot_ask": kb.get("tot_ask")}
            source = "키움 실시간(직결)"
        tr = kr.executions(ticker, ttl=1.0)
        if tr:
            _last_trades[ticker] = (now, tr[:30])
        q = kr.current_price(ticker)
        if q and q.get("price"):
            _last_quote[ticker] = (now, q)
    except Exception:
        pass

    # PC hot-relay: when direct Kiwoom is IP-blocked on this instance (8050), the PC
    # (registered IP) bursts the watched ticker into kiwoom_hot — same data, ~1-3s old.
    _hot_watch_write(db, ticker)
    if not live.get("levels"):
        hot = _hot_read(db, ticker)
        if hot:
            hage = hot["age_sec"]
            p = hot["payload"]
            if hage <= 12 and p.get("levels"):
                live = {"levels": p["levels"], "as_of": None, "age_sec": int(hage),
                        "fresh": True, "imbalance": p.get("imbalance"),
                        "tot_bid": p.get("tot_bid"), "tot_ask": p.get("tot_ask")}
                source = "키움 실시간(PC중계)"
            hot_ts = now - min(hage, 9e5)
            if p.get("trades") and (ticker not in _last_trades
                                    or hot_ts > _last_trades[ticker][0]):
                _last_trades[ticker] = (hot_ts, p["trades"][:30])
            hq = p.get("quote") or {}
            if hq.get("price") and (ticker not in _last_quote
                                    or hot_ts > _last_quote[ticker][0]):
                _last_quote[ticker] = (hot_ts, hq)

    # LAST-KNOWN trades are served indefinitely (frontend labels them by age) —
    # after 15:30 the day's final ticks must stay visible, not "appear during
    # market hours" (boss, 2026-07-06).
    if ticker in _last_trades:
        trades = _last_trades[ticker][1]
        trades_age = int(now - _last_trades[ticker][0])
    # quote: fresh Kiwoom (≤10s) → last-known Kiwoom (≤120s) → NAVER realtime (always
    # works, incl. after close and while the Kiwoom token is contested) — the header
    # strip (시가/현재가±%/고가/저가) must NEVER be empty.
    if ticker in _last_quote and now - _last_quote[ticker][0] <= 120:
        quote = dict(_last_quote[ticker][1])
        quote["src"] = "kiwoom"
        quote["age_sec"] = int(now - _last_quote[ticker][0])
    else:
        try:
            from services.naver_stock import daily_history, realtime_quote
            nq = realtime_quote(ticker)
            if nq and nq.get("price"):
                quote = {"price": nq["price"], "change_pct": nq.get("change_pct"),
                         "open": nq.get("open"), "high": nq.get("high"),
                         "low": nq.get("low"), "volume": nq.get("volume"),
                         "prev_close": None, "name": None,
                         "src": "naver", "age_sec": 0}
                if quote["open"] is None:   # basic endpoint drops OHLC after close
                    d = (daily_history(ticker, days=1) or [{}])[0]
                    for k in ("open", "high", "low", "volume"):
                        quote[k] = quote[k] if quote[k] is not None else d.get(k)
        except Exception:
            pass
    # 평균가 (VWAP) = accumulated amount / accumulated volume from the tick feed
    if quote:
        try:
            t0 = trades[0] if trades else None
            if t0 and t0.get("acc_amount") and t0.get("acc_volume"):
                quote["vwap"] = round(t0["acc_amount"] / t0["acc_volume"])
        except Exception:
            pass
    if not live.get("levels"):
        live = live_book(db, ticker)
        source = "키움 실시간" if live.get("fresh") else "NAVER"

    # live best bid/ask beats the memory-derived mid (fresher)
    mid = mem["mid"]
    try:
        la = [x["price"] for x in live.get("levels", []) if x["side"] == "ask"]
        lb = [x["price"] for x in live.get("levels", []) if x["side"] == "bid"]
        if la and lb:
            mid = (min(la) + max(lb)) / 2
    except Exception:
        pass

    price = None
    if not live.get("fresh"):           # after market → Naver current price
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
            "trades": trades, "trades_age_sec": trades_age, "quote": quote,
            "walls": walls, "threshold": mem["threshold"], "mid": mid,
            "naver_price": price}
