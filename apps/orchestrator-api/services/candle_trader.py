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

logger = logging.getLogger(__name__)

STOP_PCT_DEFAULT = 1.0
POS_PCT_DEFAULT = 10.0
DEFAULT_CODES = "000660,005930"
UP_NEEDED = 3            # 3 consecutive up 1-min candles → BUY
DOWN_NEEDED = 3          # 3 consecutive down 1-min candles → SELL
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
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS candle_trades ("
        " id SERIAL PRIMARY KEY, ticker TEXT, name TEXT, qty INT,"
        " entry DOUBLE PRECISION, exit_price DOUBLE PRECISION,"
        " exit_reason TEXT, net_pct DOUBLE PRECISION, why TEXT,"
        " status TEXT NOT NULL DEFAULT 'OPEN',"
        " opened_at TIMESTAMPTZ DEFAULT now(), closed_at TIMESTAMPTZ)"))
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
        "SELECT enabled, stop_pct, pos_pct, codes, mode, streak, tf, take_pct FROM candle_state WHERE id=1")).first()
    codes = [c.strip().zfill(6) for c in (r[3] or DEFAULT_CODES).split(",") if c.strip()]
    return {"enabled": bool(r[0]), "stop_pct": float(r[1]), "pos_pct": float(r[2]),
            "codes": codes[:24],
            "mode": (r[4] or "auto") if str(r[4] or "auto") in ("auto", "semi") else "auto",
            "streak": int(r[5] or 3) if int(r[5] or 3) in (2, 3) else 3,
            "tf": str(r[6] or "5") if str(r[6] or "5") in ("1", "3", "5") else "5",
            "take_pct": float(r[7]) if r[7] is not None else 0.5}


def set_enabled(db, on: bool) -> dict:
    _ensure(db)
    db.execute(text("UPDATE candle_state SET enabled=:e, updated_at=now() WHERE id=1"),
               {"e": bool(on)})
    db.commit()
    return {"ok": True, "enabled": bool(on)}


def set_params(db, stop_pct: Optional[float] = None, pos_pct: Optional[float] = None,
               codes: Optional[str] = None, mode: Optional[str] = None,
               streak: Optional[int] = None, tf: Optional[str] = None,
               take_pct: Optional[float] = None) -> dict:
    _ensure(db)
    if streak in (2, 3):
        db.execute(text("UPDATE candle_state SET streak=:s, updated_at=now() WHERE id=1"), {"s": int(streak)})
    if take_pct is not None:
        db.execute(text("UPDATE candle_state SET take_pct=:v, updated_at=now() WHERE id=1"),
                   {"v": max(0.1, min(3.0, float(take_pct)))})
    if tf in ("1", "3", "5"):
        db.execute(text("UPDATE candle_state SET tf=:v, updated_at=now() WHERE id=1"), {"v": str(tf)})
    if stop_pct is not None:
        db.execute(text("UPDATE candle_state SET stop_pct=:v, updated_at=now() WHERE id=1"),
                   {"v": max(0.5, min(3.0, float(stop_pct)))})
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


def _candles_tf(code: str, tf: str, n: int = 8) -> list[dict]:
    """Last n COMPLETED candles at the chosen timeframe (1/3/5-min). 20s cache."""
    import time as _t
    key = f"{code}:{tf}"
    hit = _tf_cache.get(key)
    if hit and _t.time() - hit[0] < 20:
        return hit[1][-n:]
    try:
        from services.kiwoom_rest import minute_bars
        raw = minute_bars(code, tic=str(tf), count=n + 2) or []
        cs = raw[:-1][-n:] if len(raw) >= 2 else []
    except Exception:
        return hit[1][-n:] if hit else []
    _tf_cache[key] = (_t.time(), cs)
    return cs


def _streaks_tf(code: str, tf: str) -> tuple[int, int, int]:
    """(up-in-a-row, down-in-a-row, candles seen) at the chosen timeframe.
    boss 2026-07-28: 'up' now means the CLOSES actually step HIGHER (x1 < x2 < x3), not just
    3 candles that each close above their own open. A flat market oscillating in a tiny band
    (every candle green but all closing at the SAME price) is NO LONGER a 3-up — that was a
    false buy signal. up=k => the last k candles' closes are strictly rising; dn=k => strictly
    falling. len(cs) unchanged."""
    cs = _candles_tf(code, tf)
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
        if (px / entry - 1) * 100 - 0.23 <= -cfg["stop_pct"]:
            reason = "STOP"                              # NET loss (after 0.23% tax) hits -stop% -> what you SEE = -stop%
        elif (px / entry - 1) * 100 - 0.23 >= cfg["take_pct"]:
            reason = "TARGET"                            # +take% NET gain (after tax) -> lock the small win
        elif eod:
            reason = "EOD"                               # 15:18 flat
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
    if eod:
        out["reason"] = "EOD flat — no new entries after 15:18"
        return out
    for code in cfg["codes"]:
        if code in open_codes:
            continue
        up, dn, cn = _streaks_tf(code, tf)
        if cn < need or up < need:
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
                "INSERT INTO candle_trades (ticker, name, qty, entry, why) "
                "VALUES (:t,:n,:q,:e,:w)"),
                {"t": code, "n": _name(code), "q": qty, "e": fill, "w": why[:300]})
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
        "INSERT INTO candle_trades (ticker, name, qty, entry, why) VALUES (:t,:n,:q,:e,:w)"),
        {"t": code, "n": _name(code), "q": qty, "e": fill, "w": why[:300]})
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
    open_map = {}
    for r in db.execute(text(
            "SELECT ticker, qty, entry, opened_at FROM candle_trades WHERE status='OPEN'")):
        open_map[r[0]] = {"qty": int(r[1]), "entry": float(r[2]), "opened_at": str(r[3])}
    stocks = []
    for code in cfg["codes"]:
        px = _px(code)
        o = open_map.get(code)
        up, dn, cn = _streaks_tf(code, tf)
        chg = None
        try:
            from services.paper_desk import _chg_cache
            chg = _chg_cache.get(code)
        except Exception:
            pass
        advice = None
        if o and cfg["mode"] == "semi":
            advice = {"TARGET": "SELL", "STOP": "STOP", "EOD": "STOP"}.get(_sell_hint.get(code) or "")
        stop_at = round(o["entry"] * (1 - cfg["stop_pct"] / 100)) if o else None
        stocks.append({
            "code": code, "name": _name(code), "price": px, "chg": chg,
            "state": "LONG" if o else "WAIT",
            "entry": o["entry"] if o else None, "qty": o["qty"] if o else None,
            "pnl_pct": round((px / o["entry"] - 1) * 100 - 0.23, 2) if (o and px) else None,
            "stop_at": stop_at, "advice": advice,
            "up": up, "dn": dn, "n": cn,
            "candle_signal": "BUY" if up >= need else "SELL" if dn >= need else "WAIT"})
    today = db.execute(text(
        "SELECT count(*), coalesce(sum(CASE WHEN net_pct>0 THEN 1 ELSE 0 END),0), "
        "coalesce(sum(net_pct),0), coalesce(sum(qty*entry*net_pct/100.0),0) "
        "FROM candle_trades WHERE status='CLOSED' "
        "AND closed_at::date=(now() AT TIME ZONE 'Asia/Seoul')::date")).first()
    recent = [{"name": r[0], "qty": int(r[1]), "entry": float(r[2]),
               "exit_price": (float(r[3]) if r[3] is not None else None),
               "exit_reason": r[4],
               "net_pct": (float(r[5]) if r[5] is not None else None),
               "won": (round(int(r[1]) * float(r[2]) * float(r[5]) / 100) if r[5] is not None else None),
               "closed_at": str(r[6]), "opened_at": str(r[7]), "why": r[8]}
              for r in db.execute(text(
                  "SELECT name, qty, entry, exit_price, exit_reason, net_pct, closed_at, "
                  "opened_at, why FROM candle_trades WHERE status='CLOSED' "
                  "ORDER BY closed_at DESC LIMIT 150"))]
    now_ts = datetime.now(KST).timestamp()
    signals = [s for c, s in _semi_signals.items()
               if now_ts - s.get("ts", 0) < 120 and c not in open_map] if cfg["mode"] == "semi" else []
    return {**cfg, "signals": signals, "stocks": stocks,
            "today": {"trades": int(today[0] or 0), "wins": int(today[1] or 0),
                      "net_pct_sum": round(float(today[2] or 0), 2),
                      "realized_won": round(float(today[3] or 0))},
            "recent": recent, "market_open": _market_open_now(),
            "rule_ko": f"{tf}분봉 종가 {need}연속 상승(x1<x2<x3) → 매수 · +{cfg['take_pct']}% 익절 · −{cfg['stop_pct']}% 손절 · 15:18 정리",
            "rule_en": f"{need} rising closes (x1<x2<x3) on {tf}-min → BUY · +{cfg['take_pct']}% take-profit · −{cfg['stop_pct']}% stop · flat 15:18"}
