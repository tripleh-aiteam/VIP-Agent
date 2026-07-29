"""🕯️ ALGORITHM 3 — the boss's CANDLE trader (2026-07-20).

His spec: watch the 1-minute candles.
  - 3 consecutive UP candles (양봉) → BUY.
  - 3 consecutive DOWN candles (음봉) → SELL.
  - −1% hard stop always; flat at 15:18 (EOD).
  - Meanwhile watch related stocks (peer co-move) and volume as confirmation.

A dedicated copy of Algorithm 2's shape (auto / semi / manual, same shared paper
desk, same 2 default stocks + dropdown, its own activity record) — only the BRAIN
differs: pure 3-up / 3-down candle rule. Records tagged source='algo3' and kept in
its own `candle_trades` table so the 3 algorithms compare cleanly.

Reuses scalp_trader's price/name/streak helpers so both engines read the same tape.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text

from services.scalp_trader import (KST, EOD_FLAT_HHMM, _px, _name, _candles_1m,
                                   _streaks_1m, _market_open_now)
from services.kiwoom_rest import order_book

logger = logging.getLogger(__name__)

STOP_PCT_DEFAULT = 1.0
POS_PCT_DEFAULT = 10.0
DEFAULT_CODES = "000660,005930"
UP_NEEDED = 3            # 3 consecutive up 1-min candles → BUY
DOWN_NEEDED = 3          # 3 consecutive down 1-min candles → SELL
# boss 2026-07-29: order-book (호가) confirmation layer. imbalance = (Σbuy_req − Σsell_req) /
# total, +1 = all buyers waiting, −1 = all sellers (a sell wall). The candle STAYS the boss —
# the book only (a) vetoes a 3-up buy when a clear sell wall sits above, and (b) sells a holding
# early when heavy selling hits — it NEVER trades on its own. Tunable.
FLOW_BUY_VETO = -0.30    # skip a 3-up BUY if imbalance < this (strong resting sell wall)
FLOW_SELL_FAST = -0.55   # sell a holding EARLY if imbalance <= this (heavy selling pressure)
_flow_vetoes: list[dict] = []   # recent 3-up buys the order book vetoed (for the UI proof panel)
# boss 2026-07-29: SHADOW A/B — both exit modes buy the SAME 3-up signals with the same fake
# notional; 'candle' book sells on 3-down, 'target' book sells on +take%. Same entries = a fair
# fight (only the exit differs). Pure simulation — does NOT touch the real paper account.
AB_NOTIONAL = 10_000_000   # ₩10M fake per virtual trade, identical for both books
NO_NEW_ENTRY_HHMM = (15, 8)   # boss 2026-07-29: stop opening NEW positions 10 min before the
                              # 15:18 flat — otherwise it buys a 3-up then EOD-flattens it a
                              # minute later (the confusing 4th/5th-candle end-of-day churn).
PEER = {"000660": "005930", "005930": "000660"}

_semi_signals: dict[str, dict] = {}   # semi mode: code -> live BUY recommendation
_sell_hint: dict[str, str] = {}       # semi mode: code -> pending SELL advice reason
_schema_ready = False


def _ensure(db) -> None:
    global _schema_ready
    if _schema_ready:
        return
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS candle_state ("
        " id INT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT FALSE,"
        " stop_pct DOUBLE PRECISION NOT NULL DEFAULT 1.0,"
        " pos_pct DOUBLE PRECISION NOT NULL DEFAULT 10.0,"
        " codes TEXT NOT NULL DEFAULT '000660,005930',"
        " mode TEXT NOT NULL DEFAULT 'auto',"
        " streak INT NOT NULL DEFAULT 3,"
        " tf TEXT NOT NULL DEFAULT '5',"
        " updated_at TIMESTAMPTZ DEFAULT now())"))
    db.execute(text("ALTER TABLE candle_state ADD COLUMN IF NOT EXISTS streak INT NOT NULL DEFAULT 3"))
    # boss 2026-07-20: candle timeframe — 1-min was pure noise (flips every minute, so
    # 3-in-a-row never forms). 3/5-min = the real trend you see on the chart. Default 5.
    db.execute(text("ALTER TABLE candle_state ADD COLUMN IF NOT EXISTS tf TEXT NOT NULL DEFAULT '5'"))
    # boss 2026-07-28: take-profit — sell on a small NET gain instead of waiting for a
    # 3-down reversal (which always exited a touch BELOW entry = a small loss every time).
    db.execute(text("ALTER TABLE candle_state ADD COLUMN IF NOT EXISTS "
                    "take_pct DOUBLE PRECISION NOT NULL DEFAULT 0.1"))
    # boss 2026-07-29: selectable SELL rule — 'target' = +take% take-profit (new),
    # 'candle' = 3 falling closes x1>x2>x3 (the old 3-down sell). -stop% + EOD always on.
    db.execute(text("ALTER TABLE candle_state ADD COLUMN IF NOT EXISTS "
                    "exit_mode TEXT NOT NULL DEFAULT 'target'"))
    # boss 2026-07-29: entry timing. 'confirmed' (default, safe) = act when the 3rd candle
    # CLOSES (fill at the very start of #4); 'early' = act on the still-forming 3rd candle
    # (fires ~1 candle sooner, near the 3rd, but the forming candle can flip before it closes).
    db.execute(text("ALTER TABLE candle_state ADD COLUMN IF NOT EXISTS "
                    "entry_timing TEXT NOT NULL DEFAULT 'confirmed'"))
    # boss 2026-07-29: order-book confirmation on/off (default OFF so it's opt-in / A/B testable)
    db.execute(text("ALTER TABLE candle_state ADD COLUMN IF NOT EXISTS "
                    "flow_confirm BOOLEAN NOT NULL DEFAULT FALSE"))
    # boss 2026-07-29: shadow A/B test on/off (default OFF). Runs both exit modes side by side.
    db.execute(text("ALTER TABLE candle_state ADD COLUMN IF NOT EXISTS "
                    "ab_test BOOLEAN NOT NULL DEFAULT FALSE"))
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS ab_trades ("
        " id SERIAL PRIMARY KEY, book TEXT, ticker TEXT, name TEXT, qty INT,"
        " entry DOUBLE PRECISION, exit_price DOUBLE PRECISION, exit_reason TEXT,"
        " net_pct DOUBLE PRECISION, status TEXT NOT NULL DEFAULT 'OPEN',"
        " opened_at TIMESTAMPTZ DEFAULT now(), closed_at TIMESTAMPTZ)"))
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS candle_trades ("
        " id SERIAL PRIMARY KEY, ticker TEXT, name TEXT, qty INT,"
        " entry DOUBLE PRECISION, exit_price DOUBLE PRECISION,"
        " exit_reason TEXT, net_pct DOUBLE PRECISION, why TEXT,"
        " status TEXT NOT NULL DEFAULT 'OPEN',"
        " opened_at TIMESTAMPTZ DEFAULT now(), closed_at TIMESTAMPTZ)"))
    db.execute(text("ALTER TABLE candle_trades ADD COLUMN IF NOT EXISTS "
                    "entry_timing TEXT"))   # 'confirmed'/'early' saved per trade for comparison
    db.execute(text("ALTER TABLE candle_trades ADD COLUMN IF NOT EXISTS "
                    "entry_flow DOUBLE PRECISION"))   # order-book imbalance at buy (audit proof)
    r = db.execute(text("SELECT 1 FROM candle_state WHERE id=1")).first()
    if not r:
        db.execute(text(
            "INSERT INTO candle_state (id, enabled, stop_pct, pos_pct, codes) "
            "VALUES (1, FALSE, :s, :p, :c)"),
            {"s": STOP_PCT_DEFAULT, "p": POS_PCT_DEFAULT, "c": DEFAULT_CODES})
    db.commit()
    _schema_ready = True


def _cfg(db) -> dict[str, Any]:
    _ensure(db)
    r = db.execute(text(
        "SELECT enabled, stop_pct, pos_pct, codes, mode, streak, tf, take_pct, exit_mode, entry_timing, flow_confirm, ab_test FROM candle_state WHERE id=1")).first()
    codes = [c.strip().zfill(6) for c in (r[3] or DEFAULT_CODES).split(",") if c.strip()]
    return {"enabled": bool(r[0]), "stop_pct": float(r[1]), "pos_pct": float(r[2]),
            "codes": codes[:24],
            "mode": (r[4] or "auto") if str(r[4] or "auto") in ("auto", "semi") else "auto",
            "streak": int(r[5] or 3) if int(r[5] or 3) in (2, 3) else 3,
            "tf": str(r[6] or "5") if str(r[6] or "5") in ("1", "3", "5") else "5",
            "take_pct": float(r[7]) if r[7] is not None else 0.5,
            "exit_mode": (r[8] or "target") if str(r[8] or "target") in ("target", "candle") else "target",
            "entry_timing": (r[9] or "confirmed") if str(r[9] or "confirmed") in ("confirmed", "early") else "confirmed",
            "flow_confirm": bool(r[10]), "ab_test": bool(r[11])}


def set_enabled(db, on: bool) -> dict:
    _ensure(db)
    db.execute(text("UPDATE candle_state SET enabled=:e, updated_at=now() WHERE id=1"),
               {"e": bool(on)})
    db.commit()
    return {"ok": True, "enabled": bool(on)}


def set_params(db, stop_pct: Optional[float] = None, pos_pct: Optional[float] = None,
               codes: Optional[str] = None, mode: Optional[str] = None,
               streak: Optional[int] = None, tf: Optional[str] = None,
               take_pct: Optional[float] = None, exit_mode: Optional[str] = None,
               entry_timing: Optional[str] = None, flow_confirm: Optional[bool] = None,
               ab_test: Optional[bool] = None) -> dict:
    _ensure(db)
    if entry_timing in ("confirmed", "early"):
        db.execute(text("UPDATE candle_state SET entry_timing=:v, updated_at=now() WHERE id=1"), {"v": entry_timing})
    if flow_confirm is not None:
        db.execute(text("UPDATE candle_state SET flow_confirm=:v, updated_at=now() WHERE id=1"), {"v": bool(flow_confirm)})
    if ab_test is not None:
        db.execute(text("UPDATE candle_state SET ab_test=:v, updated_at=now() WHERE id=1"), {"v": bool(ab_test)})
    if streak in (2, 3):
        db.execute(text("UPDATE candle_state SET streak=:s, updated_at=now() WHERE id=1"), {"s": int(streak)})
    if take_pct is not None:
        db.execute(text("UPDATE candle_state SET take_pct=:v, updated_at=now() WHERE id=1"),
                   {"v": max(0.1, min(3.0, float(take_pct)))})
    if exit_mode in ("target", "candle"):
        db.execute(text("UPDATE candle_state SET exit_mode=:v, updated_at=now() WHERE id=1"), {"v": exit_mode})
    if tf in ("1", "3", "5"):
        db.execute(text("UPDATE candle_state SET tf=:v, updated_at=now() WHERE id=1"), {"v": str(tf)})
    if stop_pct is not None:
        v = 0.0 if float(stop_pct) <= 0 else max(0.5, min(3.0, float(stop_pct)))   # 0 = stop OFF
        db.execute(text("UPDATE candle_state SET stop_pct=:v, updated_at=now() WHERE id=1"), {"v": v})
    if pos_pct is not None:
        db.execute(text("UPDATE candle_state SET pos_pct=:v, updated_at=now() WHERE id=1"),
                   {"v": max(1.0, min(25.0, float(pos_pct)))})
    if mode in ("auto", "semi"):
        db.execute(text("UPDATE candle_state SET mode=:m, updated_at=now() WHERE id=1"),
                   {"m": mode})
    if codes is not None:
        cl = [c.strip().zfill(6) for c in codes.split(",") if c.strip()]
        # pin the 2 mains first (boss's default view)
        for m in ("005930", "000660"):
            if m in cl:
                cl.remove(m)
        cl = ["000660", "005930"] + cl
        db.execute(text("UPDATE candle_state SET codes=:c, updated_at=now() WHERE id=1"),
                   {"c": ",".join(dict.fromkeys(cl))[:400]})
    db.commit()
    return {"ok": True, **_cfg(db)}


_tf_cache: dict[str, tuple[float, list]] = {}


def _candles_tf(code: str, tf: str, n: int = 8, include_forming: bool = False) -> list[dict]:
    """Last n candles at the chosen timeframe. include_forming=False (default) drops the
    still-forming candle so a streak is only CONFIRMED when a candle CLOSES; True keeps it
    (its live close) so 'early' mode can fire on the forming candle before it closes.
    boss 2026-07-29: cache 20s->5s so a just-closed candle is picked up within one 5s tick."""
    import time as _t
    key = f"{code}:{tf}:{int(include_forming)}"   # separate cache per mode (else confirmed/early collide)
    hit = _tf_cache.get(key)
    if hit and _t.time() - hit[0] < 5:
        return hit[1][-n:]
    try:
        from services.kiwoom_rest import minute_bars
        raw = minute_bars(code, tic=str(tf), count=n + 2) or []
        cs = (raw if include_forming else raw[:-1])[-n:] if len(raw) >= (1 if include_forming else 2) else []
        # boss 2026-07-29: keep ONLY today's candles. At the open, minute_bars returns
        # yesterday's tail too — a 3-up could then span the overnight GAP (buy after 2 real
        # candles, not 3). Filtering to today forces it to wait for 3 genuine session candles.
        _today = datetime.now(KST).strftime("%Y-%m-%d")
        cs = [b for b in cs if str(b.get("ts") or "").startswith(_today)]
    except Exception:
        return hit[1][-n:] if hit else []
    _tf_cache[key] = (_t.time(), cs)
    return cs


def _streaks_tf(code: str, tf: str, early: bool = False) -> tuple[int, int, int]:
    """(up-in-a-row, down-in-a-row, candles seen) at the chosen timeframe.
    boss 2026-07-28: 'up' now means the CLOSES actually step HIGHER (x1 < x2 < x3), not just
    3 candles that each close above their own open. A flat market oscillating in a tiny band
    (every candle green but all closing at the SAME price) is NO LONGER a 3-up — that was a
    false buy signal. up=k => the last k candles' closes are strictly rising; dn=k => strictly
    falling. len(cs) unchanged.
    early=True includes the still-forming candle (fires ~1 candle sooner, near the 3rd), at the
    risk that the forming candle flips before it closes."""
    cs = _candles_tf(code, tf, include_forming=early)
    closes = [b.get("close") for b in cs if b.get("close") is not None]
    n = len(closes)
    k = 0                                        # rising links at the tail: close[i] > close[i-1]
    while n - 1 - k >= 1 and closes[n - 1 - k] > closes[n - 2 - k]:
        k += 1
    up = k + 1 if k else 0                        # +1 for the candle the run starts from -> x1<x2<x3 == 3
    k = 0                                        # falling links at the tail: close[i] < close[i-1]
    while n - 1 - k >= 1 and closes[n - 1 - k] < closes[n - 2 - k]:
        k += 1
    dn = k + 1 if k else 0
    return up, dn, n


def _volume_rising(code: str) -> bool:
    """Confirmation: last candle's volume ≥ the average of the prior few (fail-open)."""
    try:
        cs = _candles_1m(code, n=6)
        vols = [b.get("volume") or 0 for b in cs]
        if len(vols) < 4:
            return True
        return vols[-1] >= (sum(vols[:-1]) / len(vols[:-1])) * 0.8
    except Exception:
        return True


def _peer_ok(code: str) -> bool:
    """Confirmation: the partner (SKH↔삼성) is not crashing right now (fail-open)."""
    p = PEER.get(code)
    if not p:
        return True
    try:
        up, dn, n = _streaks_1m(p)
        return not (n and dn >= 3)     # partner in a 3-down slide → hold off
    except Exception:
        return True


def _flow_imbalance(code: str) -> Optional[float]:
    """Order-book (호가) imbalance: +1 = all buyers waiting, −1 = all sellers (sell wall).
    ~2.5s cached inside order_book(). Returns None if unavailable → callers FAIL-OPEN
    (never veto / never force-sell on missing data)."""
    try:
        ob = order_book(code, ttl=2.5)
        return ob.get("imbalance") if ob else None
    except Exception:
        return None


def _run_ab_sim(db, cfg: dict, need: int, tf: str, eod: bool) -> None:
    """SHADOW A/B: both books buy the SAME 3-up signals with identical fake notional; the
    'candle' book sells on 3 DOWN closes, the 'target' book sells on +take% net. Pure
    simulation — never touches the real paper account. Runs every tick when ab_test is ON."""
    take = cfg["take_pct"]
    _flow = cfg["flow_confirm"]        # apply the SAME order-book layer to BOTH books when ON
    open_set = set()
    for oid, book, tk, entry in db.execute(text(
            "SELECT id, book, ticker, entry FROM ab_trades WHERE status='OPEN'")).fetchall():
        px = _px(tk)
        if px is None:
            open_set.add((book, tk)); continue
        entry = float(entry); net = (px / entry - 1) * 100 - 0.23
        reason = None
        if _flow and (_fi := _flow_imbalance(tk)) is not None and _fi <= FLOW_SELL_FAST:
            reason = "FLOW"                               # order book: heavy selling -> exit both books
        elif book == "target":
            reason = "TARGET" if net >= take else ("EOD" if eod else None)
        else:                                             # candle book
            _u, _dn, _cn = _streaks_tf(tk, tf)
            reason = "CANDLE3" if (_cn >= need and _dn >= need) else ("EOD" if eod else None)
        if reason:
            db.execute(text("UPDATE ab_trades SET status='CLOSED', exit_price=:x, exit_reason=:r, "
                            "net_pct=:n, closed_at=now() WHERE id=:i"),
                       {"x": px, "r": reason, "n": round(net, 3), "i": oid})
        else:
            open_set.add((book, tk))
    if not eod:                                           # open a virtual buy in BOTH books on a 3-up
        for code in cfg["codes"]:
            up, dn, cn = _streaks_tf(code, tf)
            if cn < need or up < need:
                continue
            if _flow and (_ib := _flow_imbalance(code)) is not None and _ib < FLOW_BUY_VETO:
                continue                                  # order book vetoes the buy for BOTH books
            px = _px(code)
            if px is None:
                continue
            qty = int(AB_NOTIONAL / px)
            if qty < 1:
                continue
            for book in ("candle", "target"):
                if (book, code) not in open_set:
                    db.execute(text("INSERT INTO ab_trades (book, ticker, name, qty, entry) "
                                    "VALUES (:b,:t,:n,:q,:e)"),
                               {"b": book, "t": code, "n": _name(code), "q": qty, "e": px})
                    open_set.add((book, code))
    db.commit()


def ab_scorecard(db) -> dict[str, Any]:
    """Per-book A/B stats CUMULATIVE across ALL days since the last reset (multi-day test):
    trades, wins, win%, cumulative net%, fake ₩ won. Plus the span (days / since date)."""
    out: dict[str, Any] = {}
    for book in ("candle", "target"):
        rows = db.execute(text(
            "SELECT net_pct, qty, entry FROM ab_trades WHERE book=:b AND status='CLOSED'"),
            {"b": book}).fetchall()
        n = len(rows)
        wins = sum(1 for r in rows if (r[0] or 0) > 0)
        won = sum(int(r[1]) * float(r[2]) * float(r[0] or 0) / 100 for r in rows)
        openn = int(db.execute(text(
            "SELECT count(*) FROM ab_trades WHERE book=:b AND status='OPEN'"), {"b": book}).scalar() or 0)
        out[book] = {"trades": n, "wins": wins, "win_pct": round(wins / n * 100) if n else 0,
                     "net_sum": round(sum(float(r[0] or 0) for r in rows), 2),
                     "won": round(won), "open": openn}
    span = db.execute(text(
        "SELECT to_char(min(opened_at) AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI'), "
        "count(DISTINCT (opened_at AT TIME ZONE 'Asia/Seoul')::date) FROM ab_trades")).first()
    out["since"] = span[0]
    out["days"] = int(span[1] or 0)
    return out


def ab_reset(db) -> dict:
    """Clear the A/B sim to start a fresh comparison."""
    _ensure(db)
    db.execute(text("DELETE FROM ab_trades"))
    db.commit()
    return {"ok": True}


def _place(db, code: str, side: str, qty: int):
    from services.paper_desk import place_order
    return place_order(db, code, side, qty, "market", source="algo3")


_tick_lock = threading.Lock()     # serialize tick() so the 5s exit pulse never races the 60s full tick


def exit_pulse(db) -> dict[str, Any]:
    """FAST stop/take-profit protection — runs every 5s. Checks open positions and sells on
    STOP/TARGET/EOD only (no entry scan), so a fast drop is caught in <=5s instead of <=60s.
    This tightens the -stop% floor: less slippage past -1% between checks (boss 2026-07-28)."""
    return tick(db, exits_only=True)


def tick(db, force: bool = False, exits_only: bool = False) -> dict[str, Any]:
    """One heartbeat: 3-up → BUY, +take% take-profit → SELL, −stop% stop, EOD flat.
    exits_only=True runs just the exit leg (used by the 5s exit_pulse)."""
    if not _tick_lock.acquire(blocking=False):
        return {"enabled": True, "opened": [], "closed": [], "reason": "tick busy — skipped"}
    try:
        return _tick_impl(db, force, exits_only)
    finally:
        _tick_lock.release()


def _tick_impl(db, force: bool, exits_only: bool) -> dict[str, Any]:
    _ensure(db)
    cfg = _cfg(db)
    out: dict[str, Any] = {"enabled": cfg["enabled"], "opened": [], "closed": []}
    if not force and not _market_open_now():
        out["reason"] = "market closed"
        return out
    if not cfg["enabled"]:
        out["reason"] = "algorithm 3 is OFF"
        return out
    need = int(cfg.get('streak') or 3)
    tf = str(cfg.get('tf') or '5')
    _early = cfg.get('entry_timing') == "early"   # include the forming candle -> fire ~1 sooner
    n = datetime.now(KST)
    eod = (n.hour * 60 + n.minute) >= (EOD_FLAT_HHMM[0] * 60 + EOD_FLAT_HHMM[1])

    # ---- EXITS ---- #
    open_rows = db.execute(text(
        "SELECT id, ticker, name, qty, entry FROM candle_trades WHERE status='OPEN'")).fetchall()
    open_codes = set()
    for oid, tk, name, qty, entry in open_rows:
        px = _px(tk)
        if px is None:
            open_codes.add(tk)
            continue
        entry = float(entry)
        reason = None
        _fi = _flow_imbalance(tk) if cfg["flow_confirm"] else None
        if cfg["stop_pct"] > 0 and (px / entry - 1) * 100 - 0.23 <= -cfg["stop_pct"]:
            reason = "STOP"                              # -stop% NET floor (stop_pct=0 turns it OFF -> pure candle test)
        elif _fi is not None and _fi <= FLOW_SELL_FAST:  # order book: heavy SELLING -> exit early
            reason = "FLOW"                              # sell before the 3-down candle even forms
        elif cfg["exit_mode"] == "candle":               # OLD mode: sell on 3 FALLING closes
            _u, _dn, _cn = _streaks_tf(tk, tf, early=_early)
            if _cn >= need and _dn >= need:
                reason = "CANDLE3"                       # x1>x2>x3 (3 down) -> sell
            elif eod:
                reason = "EOD"
        else:                                            # NEW mode: take-profit
            if (px / entry - 1) * 100 - 0.23 >= cfg["take_pct"]:
                reason = "TARGET"                        # +take% NET gain (after tax) -> lock the small win
            elif eod:
                reason = "EOD"                           # 15:18 flat
        if not reason:
            _sell_hint.pop(tk, None)
            open_codes.add(tk)
            continue
        if cfg["mode"] == "semi":
            _sell_hint[tk] = reason      # machine never sells in semi — advises
            open_codes.add(tk)
            continue
        held = int(db.execute(text(
            "SELECT qty FROM paper_desk_positions WHERE ticker=:t"), {"t": tk}).scalar() or 0)
        sell_qty = min(int(qty), held)
        if sell_qty <= 0:
            continue
        r = _place(db, tk, "SELL", sell_qty)
        if r.get("ok"):
            fill = float(r.get("fill_price") or px)
            net = (fill / entry - 1) * 100 - 0.23
            db.execute(text(
                "UPDATE candle_trades SET status='CLOSED', exit_price=:x, exit_reason=:r, "
                "net_pct=:n, closed_at=now() WHERE id=:i"),
                {"x": fill, "r": reason, "n": round(net, 3), "i": oid})
            db.commit()
            out["closed"].append({"name": name, "reason": reason, "net_pct": round(net, 2)})
        else:
            open_codes.add(tk)

    # ---- ENTRIES (one open per stock) ---- #
    if exits_only:                    # 5s fast pulse: protect open positions, skip entry scan
        return out
    if cfg["ab_test"]:                # SHADOW A/B: run both exit modes side by side (sim only)
        try:
            _run_ab_sim(db, cfg, need, tf, eod)
        except Exception:
            db.rollback()
    if eod:
        out["reason"] = "EOD flat — no new entries after 15:18"
        return out
    if (n.hour * 60 + n.minute) >= (NO_NEW_ENTRY_HHMM[0] * 60 + NO_NEW_ENTRY_HHMM[1]):
        out["reason"] = "no new entries after 15:08 — avoid buy-then-flatten churn near close"
        return out
    for code in cfg["codes"]:
        if code in open_codes:
            continue
        up, dn, cn = _streaks_tf(code, tf, early=_early)
        if cn < need or up < need:
            continue
        # ORDER-BOOK CONFIRMATION (only when ON): veto a 3-up buy if a clear SELL WALL sits above.
        entry_flow = None
        if cfg["flow_confirm"]:
            entry_flow = _flow_imbalance(code)
            if entry_flow is not None and entry_flow < FLOW_BUY_VETO:
                _flow_vetoes.insert(0, {"name": _name(code), "up": up, "imb": entry_flow,
                                        "ts": n.strftime("%H:%M:%S")})   # UI proof panel
                del _flow_vetoes[20:]
                continue
        # peer + volume confirmation (fail-open)
        vol_ok, peer_ok = _volume_rising(code), _peer_ok(code)
        px = _px(code)
        if px is None:
            continue
        cash = float(db.execute(text(
            "SELECT cash FROM paper_desk_account WHERE id=1")).scalar() or 0)
        qty = int(cash * cfg["pos_pct"] / 100 / px)
        if qty < 1:
            continue
        vtag = "거래량↑" if vol_ok else "거래량 약함"
        ptag = "짝꿍 안정" if peer_ok else "짝꿍 하락"
        why = f"알고3 캔들: 1분봉 종가 {up}연속 상승(x1<x2<x3) → 매수 ({vtag}·{ptag}, +익절/-1% 손절)"
        if cfg["mode"] == "semi":
            _semi_signals[code] = {"code": code, "name": _name(code), "price": px,
                                   "qty": qty, "why": why, "ts": n.timestamp()}
            out["opened"].append({"code": code, "semi_signal": True})
            continue
        r = _place(db, code, "BUY", qty)
        if r.get("ok"):
            fill = float(r.get("fill_price") or px)
            db.execute(text(
                "INSERT INTO candle_trades (ticker, name, qty, entry, why, entry_timing, entry_flow) "
                "VALUES (:t,:n,:q,:e,:w,:et,:ef)"),
                {"t": code, "n": _name(code), "q": qty, "e": fill, "w": why[:300],
                 "et": cfg["entry_timing"], "ef": entry_flow})
            db.commit()
            out["opened"].append({"code": code, "qty": qty, "entry": fill})
            logger.info("algo3 candle BUY %s x%d @ %s", code, qty, fill)
    return out


def semi_buy(db, code: str) -> dict[str, Any]:
    code = str(code).zfill(6)
    sig = _semi_signals.get(code)
    px = _px(code)
    if px is None:
        return {"ok": False, "error": "no price"}
    cfg = _cfg(db)
    cash = float(db.execute(text("SELECT cash FROM paper_desk_account WHERE id=1")).scalar() or 0)
    qty = (sig or {}).get("qty") or int(cash * cfg["pos_pct"] / 100 / px)
    if qty < 1:
        return {"ok": False, "error": "size too small"}
    r = _place(db, code, "BUY", qty)
    if not r.get("ok"):
        return r
    fill = float(r.get("fill_price") or px)
    why = (sig or {}).get("why") or "알고3 캔들: 3연속 양봉 매수"
    db.execute(text(
        "INSERT INTO candle_trades (ticker, name, qty, entry, why, entry_timing) "
        "VALUES (:t,:n,:q,:e,:w,:et)"),
        {"t": code, "n": _name(code), "q": qty, "e": fill, "w": why[:300], "et": cfg["entry_timing"]})
    db.commit()
    _semi_signals.pop(code, None)
    return {"ok": True, "fill_price": fill, "qty": qty,
            "stop_at": round(fill * (1 - cfg["stop_pct"] / 100))}


def sell_all(db, code: str) -> dict[str, Any]:
    code = str(code).zfill(6)
    row = db.execute(text(
        "SELECT id, qty, entry FROM candle_trades WHERE ticker=:t AND status='OPEN' "
        "ORDER BY opened_at DESC LIMIT 1"), {"t": code}).first()
    if not row:
        return {"ok": False, "error": "no open position"}
    oid, qty, entry = row
    held = int(db.execute(text(
        "SELECT qty FROM paper_desk_positions WHERE ticker=:t"), {"t": code}).scalar() or 0)
    sell_qty = min(int(qty), held)
    if sell_qty <= 0:
        return {"ok": False, "error": "no shares"}
    r = _place(db, code, "SELL", sell_qty)
    if not r.get("ok"):
        return r
    fill = float(r.get("fill_price") or _px(code) or entry)
    net = (fill / float(entry) - 1) * 100 - 0.23
    db.execute(text(
        "UPDATE candle_trades SET status='CLOSED', exit_price=:x, exit_reason='MANUAL', "
        "net_pct=:n, closed_at=now() WHERE id=:i"),
        {"x": fill, "n": round(net, 3), "i": oid})
    db.commit()
    _sell_hint.pop(code, None)
    return {"ok": True, "realized_pnl": round(sell_qty * float(entry) * net / 100),
            "realized_pnl_pct": round(net, 2)}


def status(db) -> dict[str, Any]:
    """Everything the Algorithm 3 page needs — same shape as Algorithm 2's status."""
    cfg = _cfg(db)
    need = int(cfg.get('streak') or 3)
    tf = str(cfg.get('tf') or '5')
    _early = cfg.get('entry_timing') == "early"   # include the forming candle -> fire ~1 sooner
    open_map = {}
    for r in db.execute(text(
            "SELECT ticker, qty, entry, opened_at FROM candle_trades WHERE status='OPEN'")):
        open_map[r[0]] = {"qty": int(r[1]), "entry": float(r[2]), "opened_at": str(r[3])}
    stocks = []
    for code in cfg["codes"]:
        px = _px(code)
        o = open_map.get(code)
        up, dn, cn = _streaks_tf(code, tf, early=_early)
        chg = None
        try:
            from services.paper_desk import _chg_cache
            chg = _chg_cache.get(code)
        except Exception:
            pass
        advice = None
        if o and cfg["mode"] == "semi":
            advice = {"TARGET": "SELL", "CANDLE3": "SELL", "FLOW": "SELL", "STOP": "STOP", "EOD": "STOP"}.get(_sell_hint.get(code) or "")
        stop_at = round(o["entry"] * (1 - cfg["stop_pct"] / 100)) if o else None
        _sig = "BUY" if up >= need else "SELL" if dn >= need else "WAIT"
        # live order-book pressure — only when the layer is ON and the stock is relevant
        # (held or signalling), to keep the호가 calls light
        flow = _flow_imbalance(code) if (cfg["flow_confirm"] and (o or _sig != "WAIT")) else None
        stocks.append({
            "code": code, "name": _name(code), "price": px, "chg": chg,
            "state": "LONG" if o else "WAIT",
            "entry": o["entry"] if o else None, "qty": o["qty"] if o else None,
            "pnl_pct": round((px / o["entry"] - 1) * 100 - 0.23, 2) if (o and px) else None,
            "stop_at": stop_at, "advice": advice,
            "up": up, "dn": dn, "n": cn, "flow": flow,
            "candle_signal": _sig})
    today = db.execute(text(
        "SELECT count(*), coalesce(sum(CASE WHEN net_pct>0 THEN 1 ELSE 0 END),0), "
        "coalesce(sum(net_pct),0), coalesce(sum(qty*entry*net_pct/100.0),0) "
        "FROM candle_trades WHERE status='CLOSED' "
        "AND closed_at::date=(now() AT TIME ZONE 'Asia/Seoul')::date")).first()
    recent = [{"ticker": r[0], "name": r[1], "qty": int(r[2]), "entry": float(r[3]),
               "exit_price": (float(r[4]) if r[4] is not None else None),
               "exit_reason": r[5],
               "net_pct": (float(r[6]) if r[6] is not None else None),
               "won": (round(int(r[2]) * float(r[3]) * float(r[6]) / 100) if r[6] is not None else None),
               "closed_at": str(r[7]), "opened_at": str(r[8]), "why": r[9]}
              for r in db.execute(text(
                  "SELECT ticker, name, qty, entry, exit_price, exit_reason, net_pct, closed_at, "
                  "opened_at, why FROM candle_trades WHERE status='CLOSED' "
                  "ORDER BY closed_at DESC LIMIT 150"))]
    now_ts = datetime.now(KST).timestamp()
    signals = [s for c, s in _semi_signals.items()
               if now_ts - s.get("ts", 0) < 120 and c not in open_map] if cfg["mode"] == "semi" else []
    _candle_sell = cfg["exit_mode"] == "candle"
    sell_ko = f"{need}연속 종가 하락(x1>x2>x3) 매도" if _candle_sell else f"+{cfg['take_pct']}% 익절"
    sell_en = f"{need} falling closes (x1>x2>x3) SELL" if _candle_sell else f"+{cfg['take_pct']}% take-profit"
    stop_ko = f"−{cfg['stop_pct']}% 손절" if cfg["stop_pct"] > 0 else "손절 없음(순수 테스트)"
    stop_en = f"−{cfg['stop_pct']}% stop" if cfg["stop_pct"] > 0 else "no stop (pure test)"
    _early_mode = cfg["entry_timing"] == "early"
    time_ko = "진입:빠름(형성중 캔들)" if _early_mode else "진입:확정(3번째 종가)"
    time_en = "timing: EARLY (forming candle)" if _early_mode else "timing: confirmed (3rd close)"
    flow_ko = " · 🔵호가확인 ON" if cfg["flow_confirm"] else ""
    flow_en = " · 🔵order-book confirm ON" if cfg["flow_confirm"] else ""
    return {**cfg, "signals": signals, "stocks": stocks,
            "today": {"trades": int(today[0] or 0), "wins": int(today[1] or 0),
                      "net_pct_sum": round(float(today[2] or 0), 2),
                      "realized_won": round(float(today[3] or 0))},
            "recent": recent, "market_open": _market_open_now(),
            "flow_vetoes": _flow_vetoes[:10],
            "ab": ab_scorecard(db) if cfg["ab_test"] else None,
            "rule_ko": f"{tf}분봉 종가 {need}연속 상승(x1<x2<x3) → 매수 · {sell_ko} · {stop_ko} · {time_ko}{flow_ko} · 15:18 정리",
            "rule_en": f"{need} rising closes (x1<x2<x3) on {tf}-min → BUY · {sell_en} · {stop_en} · {time_en}{flow_en} · flat 15:18"}
