"""⚡ ALGORITHM 2 — the boss's low-risk RIPPLE scalper (2026-07-14).

His spec, verbatim logic:
  - Don't hold 1 hour like Algorithm 1. Take SMALL wins repeatedly:
    buy when the price STARTS RISING, sell after a small gain (~+0.4% default,
    his example: buy 1,995,000 → sell 1,998,000). If it keeps rising after the
    sell → buy again, sell again. 2 buys + 2 sells inside 30 minutes is fine.
  - After a sell, if the price falls: WAIT. When it starts rising again → buy.
  - After a buy, if it falls: DO NOT panic-sell the dip. Only at −1% → sell,
    then wait for the next upturn.
  - Everything on the same shared paper desk, same stocks as Algorithm 1.

Two modes (UI): AUTO = this state machine runs it; MANUAL = the boss trades
with the Kiwoom order book + his own buy/sell, plus "auto-sell at my price"
(a paper-desk LIMIT SELL that our heartbeat fills server-side).

Driven by a 15-second scheduler heartbeat (see scheduler_service). Price
memory lives in-process (rebuilds ~1 min after a deploy — acceptable).
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

# ---- the boss's dials (defaults; changeable from the UI) ----------------- #
TAKE_PCT_DEFAULT = 0.4    # small win. NOTE: paper fees are 0.23% round-trip —
                          # a +0.2% gross win is a NET LOSS; 0.4% ≈ +0.17% net.
STOP_PCT_DEFAULT = 1.0    # his rule: hold dips, cut only at −1%
POS_PCT_DEFAULT = 10.0    # % of equity per ripple trade
DEFAULT_CODES = "000660,005930"

# upturn detection on the 15s price stream
BOUNCE_MIN_PCT = 0.10     # price must lift ≥0.10% off the recent low…
BOUNCE_MAX_PCT = 0.45     # …but not already ≥0.45% (too late, don't chase)
RISES_NEEDED = 2          # and the last N reads must be strictly rising
BUF_LEN = 40              # 40 × 15s = 10-minute rolling window
EOD_FLAT_HHMM = (15, 18)  # close everything at 15:18 — a scalper sleeps flat

# boss 2026-07-14 (2nd round): a buy needs THREE agreeing voices —
# ① the price stream (upturn), ② the Kiwoom order book (buyers not losing),
# ③ the partner stock (⑨ peer layer: SKH↔삼성전자 co-move, corr 0.83)
BOOK_IMB_MIN = -0.10      # (bids−asks)/(bids+asks) — sellers must not dominate
PEER = {"000660": "005930", "005930": "000660"}
PEER_DROP_PCT = -0.15     # partner falling faster than this over ~60s → no buy

_buf: dict[str, deque] = {}          # code -> deque[(ts, px)]


def _ensure(db) -> None:
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS scalp_state ("
        " id INT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT FALSE,"
        " take_pct DOUBLE PRECISION NOT NULL DEFAULT 0.4,"
        " stop_pct DOUBLE PRECISION NOT NULL DEFAULT 1.0,"
        " pos_pct DOUBLE PRECISION NOT NULL DEFAULT 10.0,"
        " codes TEXT NOT NULL DEFAULT '000660,005930',"
        " updated_at TIMESTAMPTZ DEFAULT now())"))
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS scalp_trades ("
        " id SERIAL PRIMARY KEY, ticker TEXT, name TEXT, qty INT,"
        " entry DOUBLE PRECISION, exit_price DOUBLE PRECISION,"
        " exit_reason TEXT, net_pct DOUBLE PRECISION,"
        " status TEXT NOT NULL DEFAULT 'OPEN',"
        " opened_at TIMESTAMPTZ DEFAULT now(), closed_at TIMESTAMPTZ)"))
    db.execute(text(
        "ALTER TABLE scalp_trades ADD COLUMN IF NOT EXISTS why TEXT"))
    r = db.execute(text("SELECT 1 FROM scalp_state WHERE id=1")).first()
    if not r:
        db.execute(text(
            "INSERT INTO scalp_state (id, enabled, take_pct, stop_pct, pos_pct, codes) "
            "VALUES (1, FALSE, :t, :s, :p, :c)"),
            {"t": TAKE_PCT_DEFAULT, "s": STOP_PCT_DEFAULT,
             "p": POS_PCT_DEFAULT, "c": DEFAULT_CODES})
    db.commit()


def _cfg(db) -> dict[str, Any]:
    _ensure(db)
    r = db.execute(text(
        "SELECT enabled, take_pct, stop_pct, pos_pct, codes FROM scalp_state WHERE id=1")).first()
    codes = [c.strip().zfill(6) for c in (r[4] or DEFAULT_CODES).split(",") if c.strip()]
    return {"enabled": bool(r[0]), "take_pct": float(r[1]), "stop_pct": float(r[2]),
            "pos_pct": float(r[3]), "codes": codes[:24]}


def set_enabled(db, on: bool) -> dict:
    _ensure(db)
    db.execute(text("UPDATE scalp_state SET enabled=:e, updated_at=now() WHERE id=1"),
               {"e": bool(on)})
    db.commit()
    return {"ok": True, "enabled": bool(on)}


def set_params(db, take_pct: Optional[float] = None, stop_pct: Optional[float] = None,
               pos_pct: Optional[float] = None, codes: Optional[str] = None) -> dict:
    _ensure(db)
    cur = _cfg(db)
    take = min(max(float(take_pct if take_pct is not None else cur["take_pct"]), 0.05), 2.0)
    stop = min(max(float(stop_pct if stop_pct is not None else cur["stop_pct"]), 0.5), 3.0)
    pos = min(max(float(pos_pct if pos_pct is not None else cur["pos_pct"]), 1.0), 25.0)
    cs = codes if codes is not None else ",".join(cur["codes"])
    lst: list[str] = []
    for c in cs.split(","):
        c = c.strip().zfill(6)
        if c.isdigit() and len(c) == 6 and c not in lst:
            lst.append(c)
    # boss: SK하이닉스 · 삼성전자 always on top, then the others (max 24 = full watchlist)
    mains = [c for c in ("000660", "005930") if c in lst]
    lst = (mains + [c for c in lst if c not in ("000660", "005930")])[:24]
    cs = ",".join(lst) or DEFAULT_CODES
    db.execute(text(
        "UPDATE scalp_state SET take_pct=:t, stop_pct=:s, pos_pct=:p, codes=:c, "
        "updated_at=now() WHERE id=1"), {"t": take, "s": stop, "p": pos, "c": cs})
    db.commit()
    return {"ok": True, "take_pct": take, "stop_pct": stop, "pos_pct": pos, "codes": cs}


def _market_open_now() -> bool:
    n = datetime.now(KST)
    return n.weekday() < 5 and (9 * 60) <= (n.hour * 60 + n.minute) <= (15 * 60 + 20)


def _px(code: str) -> Optional[float]:
    from services.paper_desk import _live_price
    p, _ = _live_price(code)
    return p


_name_cache: dict[str, str] = {}        # names don't change intraday — cache forever


def _name(code: str) -> str:
    hit = _name_cache.get(code)
    if hit:
        return hit
    from services.paper_desk import _name_for
    n = _name_for(code)
    if n and n != code:
        _name_cache[code] = n
    return n


def _close_row(db, out: dict, oid: int, name: str, entry: float, fill: float,
               reason: str) -> None:
    net = (fill / float(entry) - 1) * 100 - 0.23
    db.execute(text(
        "UPDATE scalp_trades SET status='CLOSED', exit_price=:x, exit_reason=:r, "
        "net_pct=:n, closed_at=now() WHERE id=:i"),
        {"x": fill, "r": reason, "n": round(net, 3), "i": oid})
    db.commit()
    out["closed"].append({"name": name, "reason": reason, "net_pct": round(net, 2)})


def _reconcile_external(db, out: dict) -> None:
    """Same shared-book lesson as the auto trader: if the shares behind an OPEN
    scalp row were sold outside (boss click / manual limit), close the row."""
    rows = db.execute(text(
        "SELECT id, ticker, name, entry, opened_at FROM scalp_trades "
        "WHERE status='OPEN'")).fetchall()
    for oid, tk, name, entry, opened_at in rows:
        held = int(db.execute(text(
            "SELECT qty FROM paper_desk_positions WHERE ticker=:t"),
            {"t": tk}).scalar() or 0)
        if held > 0:
            continue
        last_fill = db.execute(text(
            "SELECT fill_price FROM paper_desk_orders WHERE ticker=:t AND side='SELL' "
            "AND status='FILLED' AND fill_price IS NOT NULL AND created_at >= :o "
            "ORDER BY created_at DESC LIMIT 1"), {"t": tk, "o": opened_at}).scalar()
        fill = float(last_fill or _px(tk) or entry)
        _close_row(db, out, oid, name, float(entry), fill, "EXTERNAL")


def _book_ok(code: str) -> tuple[bool, str, Optional[float]]:
    """② Kiwoom order book must agree: sellers not dominating the queue.
    Fail-open when the book isn't available (after-hours, IP-blocked local)."""
    try:
        from services.kiwoom_rest import order_book
        ob = order_book(code, ttl=10)
        imb = (ob or {}).get("imbalance")
        if imb is None:
            return True, "book n/a", None
        return (float(imb) >= BOOK_IMB_MIN), f"book {float(imb):+.2f}", float(imb)
    except Exception:
        return True, "book n/a", None


def _peer_ok(code: str) -> tuple[bool, str, Optional[float]]:
    """③ the partner stock (SKH↔삼성전자 co-move, corr 0.83) must not be
    falling hard — if the pair leader is dropping, the bounce usually dies."""
    peer = PEER.get(code)
    if not peer:
        return True, "no peer", None
    buf = _buf.get(peer)
    if not buf or len(buf) < 5:
        return True, "peer warming", None
    prices = [p for _, p in buf]
    r = (prices[-1] / prices[-5] - 1) * 100      # ~last 60 seconds
    return (r > PEER_DROP_PCT), f"peer {r:+.2f}%/60s", round(r, 2)


def _conviction(db, code: str, imb: Optional[float], peer_r: Optional[float]) -> tuple[float, str, str]:
    """WHO decides the share count (boss 2026-07-15): base = pos_pct% of cash,
    then the three voices scale it 0.7×~1.2×. Strong book + rising partner +
    positive 5-min forecast → bigger; weak evidence → smaller. Returns
    (multiplier, why_ko, why_en)."""
    mult = 0.7
    bits_ko: list[str] = []
    bits_en: list[str] = []
    if imb is not None and imb >= 0.10:
        mult += 0.15
        bits_ko.append("호가 매수우위")
        bits_en.append("book buy-heavy")
    if peer_r is not None and peer_r > 0.05:
        mult += 0.15
        bits_ko.append("짝꿍 상승 동행")
        bits_en.append("partner rising")
    pred_pct = up_rate = None
    try:
        from services.pattern_layer import next_bar_vote
        nb = next_bar_vote(db, code)
        if nb:
            pred_pct, up_rate = nb.get("pred_pct"), nb.get("up_rate")
    except Exception:
        db.rollback()
    if pred_pct is not None and pred_pct > 0:
        mult += 0.15
        bits_ko.append(f"5분예측 +{pred_pct}%")
        bits_en.append(f"5m pred +{pred_pct}%")
    if up_rate is not None and up_rate >= 55:
        mult += 0.05
        bits_ko.append(f"유사장면 {up_rate}% 상승")
        bits_en.append(f"analogs {up_rate}% up")
    mult = round(min(mult, 1.2), 2)
    tail_ko = " · ".join(bits_ko) if bits_ko else "근거 보통 — 작게"
    tail_en = " · ".join(bits_en) if bits_en else "modest evidence — sized down"
    return mult, f"확신도 ×{mult} ({tail_ko})", f"conviction ×{mult} ({tail_en})"


def _upturn(code: str, px: float) -> tuple[bool, float]:
    """Boss's entry: the price STARTED RISING off a local low.
    Returns (fire?, bounce_pct_from_recent_low)."""
    buf = _buf.get(code)
    if not buf or len(buf) < RISES_NEEDED + 2:
        return False, 0.0
    prices = [p for _, p in buf]
    lo = min(prices)
    if lo <= 0:
        return False, 0.0
    bounce = (px / lo - 1) * 100
    rising = all(prices[i] < prices[i + 1]
                 for i in range(len(prices) - RISES_NEEDED - 1, len(prices) - 1))
    return (rising and BOUNCE_MIN_PCT <= bounce <= BOUNCE_MAX_PCT), round(bounce, 3)


def tick(db, force: bool = False) -> dict[str, Any]:
    """One 15s pass of Algorithm 2. Exits → entries, one shared desk book.
    Also fills the boss's manual auto-sell LIMIT orders (server-side)."""
    cfg = _cfg(db)
    out: dict[str, Any] = {"enabled": cfg["enabled"], "closed": [], "opened": [],
                           "reason": None}
    # manual-mode auto-sell orders must fill even when the machine is OFF
    try:
        from services.paper_desk import check_limit_orders
        n = check_limit_orders(db)
        if n:
            out["limit_fills"] = n
    except Exception:
        db.rollback()
    try:
        _reconcile_external(db, out)
    except Exception:
        db.rollback()
    if not force and not _market_open_now():
        out["reason"] = "market closed"
        return out

    # feed the 15s price memory for every configured stock (even when OFF —
    # the moment he flips ON, the upturn detector already has its window).
    # PARALLEL fetch: at 24 stocks a serial loop would eat the whole 15s beat.
    live: dict[str, float] = {}
    now_ts = datetime.now(KST).timestamp()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as _ex:
        for code, p in zip(cfg["codes"], _ex.map(_px, cfg["codes"])):
            if p is None:
                continue
            live[code] = float(p)
            buf = _buf.setdefault(code, deque(maxlen=BUF_LEN))
            buf.append((now_ts, float(p)))

    if not cfg["enabled"]:
        out["reason"] = "algorithm 2 is OFF"
        return out

    from services.paper_desk import place_order
    n = datetime.now(KST)
    eod = (n.hour * 60 + n.minute) >= (EOD_FLAT_HHMM[0] * 60 + EOD_FLAT_HHMM[1])

    # ---- 1) EXITS (protect first) ---- #
    open_rows = db.execute(text(
        "SELECT id, ticker, name, qty, entry FROM scalp_trades WHERE status='OPEN'")).fetchall()
    open_codes = set()
    for oid, tk, name, qty, entry in open_rows:
        px = live.get(tk) or _px(tk)
        if px is None:
            open_codes.add(tk)
            continue
        entry = float(entry)
        reason = None
        if px >= entry * (1 + cfg["take_pct"] / 100):
            reason = "TAKE"      # small win — the whole point
        elif px <= entry * (1 - cfg["stop_pct"] / 100):
            reason = "STOP"      # his rule: hold dips, cut at −1%
        elif eod:
            reason = "EOD"       # a scalper sleeps flat
        if not reason:
            open_codes.add(tk)
            continue
        held = int(db.execute(text(
            "SELECT qty FROM paper_desk_positions WHERE ticker=:t"),
            {"t": tk}).scalar() or 0)
        sell_qty = min(int(qty), held)
        if sell_qty <= 0:
            continue             # reconcile closes it next pass
        r = place_order(db, tk, "SELL", sell_qty, "market", source="algo2")
        if r.get("ok"):
            _close_row(db, out, oid, name, entry, float(r.get("fill_price") or px), reason)
            _buf.pop(tk, None)   # fresh window: re-buy only on a NEW rise
        else:
            logger.warning("scalp: SELL failed %s: %s", tk, r.get("error"))
            open_codes.add(tk)

    # ---- 2) ENTRIES (one per stock, never while holding it) ---- #
    if eod:
        out["reason"] = "EOD flat — no new entries after 15:18"
        return out
    for code in cfg["codes"]:
        if code in open_codes or code not in live:
            continue
        fire, bounce = _upturn(code, live[code])
        if not fire:
            continue
        # three agreeing voices before money moves (boss 2026-07-14 round 2)
        ok_book, why_book, imb = _book_ok(code)
        ok_peer, why_peer, peer_r = _peer_ok(code)
        if not (ok_book and ok_peer):
            logger.info("scalp: %s upturn but vetoed — %s · %s", code, why_book, why_peer)
            continue
        cash = float(db.execute(text(
            "SELECT cash FROM paper_desk_account WHERE id=1")).scalar() or 0)
        # WHO decides the size: pos_pct% of cash × conviction (0.7~1.2 from the voices)
        mult, why_ko, _why_en = _conviction(db, code, imb, peer_r)
        qty = int(cash * cfg["pos_pct"] / 100 * mult / live[code])
        if qty < 1:
            continue
        r = place_order(db, code, "BUY", qty, "market", source="algo2")
        if r.get("ok"):
            fill = float(r.get("fill_price") or live[code])
            why = f"반등 +{bounce}% · {why_book} · {why_peer} · {why_ko}"
            db.execute(text(
                "INSERT INTO scalp_trades (ticker, name, qty, entry, why) "
                "VALUES (:t,:n,:q,:e,:w)"),
                {"t": code, "n": _name(code), "q": qty, "e": fill, "w": why[:300]})
            db.commit()
            out["opened"].append({"code": code, "qty": qty, "entry": fill,
                                  "bounce_pct": bounce, "why": why})
            logger.info("scalp: BUY %s x%d @ %s (%s)", code, qty, fill, why)
    return out


def adopt(db, code: str) -> dict[str, Any]:
    """⚡ 맡기기 (boss 2026-07-15: manual buy sat unsold at +2.2%): hand a
    hand-bought position to Algorithm 2. Creates an OPEN scalp row at the
    position's avg price — the next 15s beat manages the exit by the ripple
    rules (take / −1% stop / EOD flat)."""
    _ensure(db)
    code = str(code).strip().zfill(6)
    row = db.execute(text(
        "SELECT qty, avg_price FROM paper_desk_positions WHERE ticker=:t AND qty>0"),
        {"t": code}).first()
    if not row:
        return {"ok": False, "error": "no shares held"}
    existing = db.execute(text(
        "SELECT 1 FROM scalp_trades WHERE ticker=:t AND status='OPEN'"),
        {"t": code}).first()
    if existing:
        return {"ok": False, "error": "already managed by Algorithm 2"}
    qty, avg = int(row[0]), float(row[1])
    cfg = _cfg(db)
    db.execute(text(
        "INSERT INTO scalp_trades (ticker, name, qty, entry, why) VALUES (:t,:n,:q,:e,:w)"),
        {"t": code, "n": _name(code), "q": qty, "e": avg,
         "w": "👤 수동 매수 → ⚡ 알고리즘 2 위임 (보스 클릭)"})
    db.commit()
    if code not in cfg["codes"] and len(cfg["codes"]) < 24:
        set_params(db, codes=",".join(cfg["codes"] + [code]))   # card + buffers track it
    return {"ok": True, "code": code, "qty": qty, "entry": avg,
            "take_at": round(avg * (1 + cfg["take_pct"] / 100)),
            "stop_at": round(avg * (1 - cfg["stop_pct"] / 100))}


_pred_cache: dict[str, tuple[float, Any]] = {}   # 5-min bars only change every 5 min
_status_cache: Optional[tuple[float, dict]] = None


def status(db) -> dict[str, Any]:
    """Everything the Algorithm 2 page needs, one call. With 21 stocks a cold
    build costs ~60 cross-region DB round trips — so the whole payload is cached
    for 3s (the page polls every 4s; concurrent viewers dedupe) and each stock's
    5-min forecast for 45s."""
    global _status_cache
    import time as _t
    if _status_cache and _t.time() - _status_cache[0] < 3.0:
        return _status_cache[1]
    cfg = _cfg(db)
    stocks: list[dict[str, Any]] = []
    open_map: dict[str, Any] = {}
    for r in db.execute(text(
            "SELECT ticker, qty, entry, opened_at FROM scalp_trades WHERE status='OPEN'")):
        open_map[r[0]] = {"qty": int(r[1]), "entry": float(r[2]), "opened_at": str(r[3])}
    # parallel price fan-out — 21 stocks serial would take ~4s per status call
    # while the page polls every 4s (pile-up); parallel keeps it under ~1s
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as _ex:
        _pxs = dict(zip(cfg["codes"], _ex.map(_px, cfg["codes"])))
    for code in cfg["codes"]:
        px = _pxs.get(code)
        o = open_map.get(code)
        buf = _buf.get(code)
        lo = min((p for _, p in buf), default=None) if buf else None
        bounce = round((px / lo - 1) * 100, 2) if (px and lo) else None
        # today's change% (cached by _live_price) — the boss's real-time display
        chg = None
        try:
            from services.paper_desk import _chg_cache
            chg = _chg_cache.get(code)
        except Exception:
            pass
        # ⏱️ next-5-minutes analog forecast (boss 2026-07-15): predicted price + ±%
        pred = None
        try:
            _hit = _pred_cache.get(code)
            if _hit and _t.time() - _hit[0] < 45.0:
                nb = _hit[1]
            else:
                from services.pattern_layer import next_bar_vote
                nb = next_bar_vote(db, code)
                _pred_cache[code] = (_t.time(), nb)
            if nb and px:
                pred = {**nb, "pred_price": round(px * (1 + nb["pred_pct"] / 100))}
        except Exception:
            db.rollback()
        # newly-added stocks have no deep 5-min history yet → fall back to the
        # live 15s stream's drift (honest label: 단기 추세 기반)
        if pred is None and px and buf and len(buf) >= 5:
            prices = [p for _, p in buf]
            drift = (prices[-1] / prices[-5] - 1) * 5   # last ~60s extrapolated to 5min
            drift = max(-0.006, min(0.006, drift))       # clamp ±0.6%
            pred = {"pred_pct": round(drift * 100, 3), "up_rate": None, "n": 0,
                    "pred_price": round(px * (1 + drift)), "fallback": True}
        stocks.append({
            "code": code, "name": _name(code), "price": px, "chg": chg,
            "state": "LONG" if o else "WAIT",
            "entry": o["entry"] if o else None, "qty": o["qty"] if o else None,
            "pnl_pct": round((px / o["entry"] - 1) * 100 - 0.23, 2) if (o and px) else None,
            "take_at": round(o["entry"] * (1 + cfg["take_pct"] / 100)) if o else None,
            "stop_at": round(o["entry"] * (1 - cfg["stop_pct"] / 100)) if o else None,
            "bounce_pct": bounce, "buf_n": len(buf) if buf else 0,
            "pred": pred})
    today = db.execute(text(
        "SELECT count(*), coalesce(sum(CASE WHEN net_pct>0 THEN 1 ELSE 0 END),0), "
        "coalesce(sum(net_pct),0), "
        "coalesce(sum(qty * entry * net_pct / 100.0),0) "
        "FROM scalp_trades WHERE status='CLOSED' "
        "AND closed_at::date=(now() AT TIME ZONE 'Asia/Seoul')::date")).first()
    recent = [{"name": r[0], "qty": int(r[1]), "entry": float(r[2]),
               "exit_price": (float(r[3]) if r[3] is not None else None),
               "exit_reason": r[4],
               "net_pct": (float(r[5]) if r[5] is not None else None),
               "won": (round(int(r[1]) * float(r[2]) * float(r[5]) / 100)
                       if r[5] is not None else None),
               "closed_at": str(r[6]), "opened_at": str(r[7]), "why": r[8]}
              for r in db.execute(text(
                  "SELECT name, qty, entry, exit_price, exit_reason, net_pct, closed_at, "
                  "opened_at, why FROM scalp_trades WHERE status='CLOSED' "
                  "ORDER BY closed_at DESC LIMIT 25"))]
    out = {**cfg,
            "stocks": stocks,
            "today": {"trades": int(today[0] or 0), "wins": int(today[1] or 0),
                      "net_pct_sum": round(float(today[2] or 0), 2),
                      "realized_won": round(float(today[3] or 0))},
            "recent": recent,
            "market_open": _market_open_now(),
            "fee_note_ko": "왕복 수수료+세금 0.23% — 목표 0.4%면 실수익 약 +0.17%",
            "fee_note_en": "round-trip fees+tax 0.23% — a 0.4% take nets about +0.17%"}
    _status_cache = (_t.time(), out)
    return out
