"""🔀 ALGORITHM 4 — the boss's CROSS-CHECK trader (2026-07-21).

The consensus engine. It has NO opinion of its own — it only trades when the
three existing algorithms AGREE:

  - Algorithm 1 (🤖 auto/setup + decision brain)  — buy signal
  - Algorithm 2 (⚡ ripple bounce)                 — buy signal
  - Algorithm 3 (🕯️ candle streak)                — buy signal

  ENTRY  · strict (3/3): algo1 BUY AND ripple BUY AND candle BUY.
         · loose  (2/3+brain): ripple BUY AND candle BUY AND algo1 not-bearish
           AND the 1-hour up-probability is ok.
  EXIT   · safety net ALWAYS on: −stop% hard stop, the ripple trailing exit
           (let winners run), EOD flat 15:18.  PLUS a consensus sell.

A dedicated copy of Algorithm 3's shape (auto / semi / manual pages, same shared
paper desk, its own cross_trades table, records tagged source='algo4') so the 4
competitors compare cleanly on the verdict board. It reads the OTHER algorithms'
LIVE signals — it never changes how they trade (they stay the control group).

Every path is CHEAP: signals come from the 20s candle cache, the warm scan cache,
and the warm decision cache — status() NEVER computes scan() or decide().
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text

from services.scalp_trader import (KST, EOD_FLAT_HHMM, _px, _name,
                                   _market_open_now, _ripple_exit, TRAIL_PCT, FEE_PCT)

logger = logging.getLogger(__name__)

STOP_PCT_DEFAULT = 1.0
POS_PCT_DEFAULT = 10.0
# the arming floor for the trailing exit: once a position is up ≥ this, we ride it
# and sell only on a give-back — never below break-even (+fee). Same as ripple's take.
TAKE_FLOOR = 0.4
# Algo-1 gates (boss's live-test numbers, same as auto_trader.MIN_CONF / the loose prob gate)
CONF_MIN = 55
PROB_MIN = 50.0
# fallback default watchlist (the shared 21 the fleet trades) if scalp_state isn't set yet
SCALP_21 = ("000660,005930,042660,035420,009150,373220,005380,000270,005490,035720,"
            "051910,006400,105560,055550,012450,329180,034020,010140,042700,066570,006840")

_peak: dict[str, float] = {}          # in-memory winner-peak (NO DB column — see scalp_trader)
_semi_signals: dict[str, dict] = {}   # semi mode: code -> live consensus BUY card
_sell_hint: dict[str, str] = {}       # semi mode: code -> pending SELL advice reason
_schema_ready = False


def _default_codes(db) -> str:
    """Cross-Check watches the SAME stocks as Algorithm 2 (boss's fleet)."""
    try:
        r = db.execute(text("SELECT codes FROM scalp_state WHERE id=1")).scalar()
        if r and str(r).strip():
            return str(r)
    except Exception:
        db.rollback()
    return SCALP_21


def _ensure(db) -> None:
    # DDL once per process — same lesson as scalp/candle: a per-tick ALTER TABLE
    # takes an AccessExclusiveLock and deadlocked under market-open load.
    global _schema_ready
    if _schema_ready:
        return
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS cross_state ("
        " id INT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT FALSE,"
        " mode TEXT NOT NULL DEFAULT 'auto',"
        " rule TEXT NOT NULL DEFAULT 'strict',"
        " stop_pct DOUBLE PRECISION NOT NULL DEFAULT 1.0,"
        " pos_pct DOUBLE PRECISION NOT NULL DEFAULT 10.0,"
        " codes TEXT NOT NULL DEFAULT '000660,005930',"
        " updated_at TIMESTAMPTZ DEFAULT now())"))
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS cross_trades ("
        " id SERIAL PRIMARY KEY, ticker TEXT, name TEXT, qty INT,"
        " entry DOUBLE PRECISION, exit_price DOUBLE PRECISION,"
        " exit_reason TEXT, net_pct DOUBLE PRECISION, why TEXT,"
        " status TEXT NOT NULL DEFAULT 'OPEN',"
        " opened_at TIMESTAMPTZ DEFAULT now(), closed_at TIMESTAMPTZ)"))
    r = db.execute(text("SELECT 1 FROM cross_state WHERE id=1")).first()
    if not r:
        db.execute(text(
            "INSERT INTO cross_state (id, enabled, stop_pct, pos_pct, codes) "
            "VALUES (1, FALSE, :s, :p, :c)"),
            {"s": STOP_PCT_DEFAULT, "p": POS_PCT_DEFAULT, "c": _default_codes(db)})
    db.commit()
    _schema_ready = True


def _cfg(db) -> dict[str, Any]:
    _ensure(db)
    r = db.execute(text(
        "SELECT enabled, mode, rule, stop_pct, pos_pct, codes FROM cross_state WHERE id=1")).first()
    codes = [c.strip().zfill(6) for c in (r[5] or SCALP_21).split(",") if c.strip()]
    return {"enabled": bool(r[0]),
            "mode": (r[1] or "auto") if str(r[1] or "auto") in ("auto", "semi") else "auto",
            "rule": (r[2] or "strict") if str(r[2] or "strict") in ("strict", "loose") else "strict",
            "stop_pct": float(r[3]), "pos_pct": float(r[4]), "codes": codes[:24]}


def set_enabled(db, on: bool) -> dict:
    _ensure(db)
    db.execute(text("UPDATE cross_state SET enabled=:e, updated_at=now() WHERE id=1"),
               {"e": bool(on)})
    db.commit()
    return {"ok": True, "enabled": bool(on)}


def set_params(db, rule: Optional[str] = None, stop_pct: Optional[float] = None,
               pos_pct: Optional[float] = None, mode: Optional[str] = None,
               codes: Optional[str] = None) -> dict:
    _ensure(db)
    if rule in ("strict", "loose"):
        db.execute(text("UPDATE cross_state SET rule=:v, updated_at=now() WHERE id=1"), {"v": rule})
    if mode in ("auto", "semi"):
        db.execute(text("UPDATE cross_state SET mode=:v, updated_at=now() WHERE id=1"), {"v": mode})
    if stop_pct is not None:
        db.execute(text("UPDATE cross_state SET stop_pct=:v, updated_at=now() WHERE id=1"),
                   {"v": max(0.5, min(3.0, float(stop_pct)))})
    if pos_pct is not None:
        db.execute(text("UPDATE cross_state SET pos_pct=:v, updated_at=now() WHERE id=1"),
                   {"v": max(1.0, min(25.0, float(pos_pct)))})
    if codes is not None:
        cl = [c.strip().zfill(6) for c in codes.split(",") if c.strip()]
        for m in ("005930", "000660"):    # pin the 2 mains first (boss's default view)
            if m in cl:
                cl.remove(m)
        cl = ["000660", "005930"] + cl
        db.execute(text("UPDATE cross_state SET codes=:c, updated_at=now() WHERE id=1"),
                   {"c": ",".join(dict.fromkeys(cl))[:400]})
    db.commit()
    return {"ok": True, **_cfg(db)}


# ---- reading the OTHER three algorithms' live signals (caches only) ---------- #
def _scan_items(db) -> dict[str, dict]:
    """Algorithm 1's warm setup scan (act_now list) — READ the cache directly,
    NEVER compute scan() on a request path (a cold scan took 42s and froze the UI)."""
    try:
        from services import intraday_setup as _iset
        cached = _iset._scan_cache.get("v")
        return {str(s.get("code")).zfill(6): s
                for s in ((cached or {}).get("act_now") or []) if s.get("code")}
    except Exception:
        db.rollback()
        return {}


def _decide_peek(code: str) -> Optional[str]:
    """Algorithm 1's brain verdict from the WARM decision cache only (no compute)."""
    try:
        import time as _t
        from services.decision_agent import _decide_cache
        hit = _decide_cache.get(str(code).zfill(6))
        if hit and _t.time() - hit[0] < 300:
            return (hit[1] or {}).get("decision")
    except Exception:
        pass
    return None


def _candle_need_tf(db) -> tuple[int, str]:
    """Algorithm 3's live streak + timeframe settings (so 'candle BUY' == Algo 3's)."""
    try:
        from services import candle_trader
        c = candle_trader._cfg(db)
        return int(c.get("streak") or 3), str(c.get("tf") or "5")
    except Exception:
        db.rollback()
        return 3, "5"


def _signals_for(db, code: str, need: int, tf: str, scan_items: dict,
                 allow_compute: bool) -> dict[str, Any]:
    """The three algorithms' live verdicts for one stock (BUY/WAIT/SELL each) plus
    the raw booleans the entry/exit rules need. allow_compute=True (tick) lets the
    Algo-1 brain compute on a cache miss; status() passes False (cache peek only)."""
    from services import decision_brain, candle_trader
    ripple = decision_brain._ripple_now(code)[0]
    up, dn, cn = candle_trader._streaks_tf(code, tf)
    candle = "BUY" if up >= need else "SELL" if dn >= need else "WAIT"
    # Algorithm 1: scan act_now (conf gate) OR brain BUY; brain SELL = bearish
    item = scan_items.get(str(code).zfill(6))
    scan_buy = bool(item and (item.get("confidence") or 0) >= CONF_MIN)
    decision = None
    if allow_compute:
        try:
            from services.decision_agent import decide_cached
            decision = (decide_cached(db, code, ttl=180) or {}).get("decision")
        except Exception:
            db.rollback()
    if decision is None:
        decision = _decide_peek(code)
    algo1_buy = scan_buy or (decision == "BUY")
    algo1 = "BUY" if algo1_buy else ("SELL" if decision == "SELL" else "WAIT")
    prob = item.get("ai_1h_prob") if item else None
    return {
        "algo1": algo1, "ripple": ripple, "candle": candle,
        "algo1_buy": algo1_buy, "algo1_not_bearish": decision != "SELL",
        "algo1_sell": decision == "SELL", "algo1_prob": prob,
        "ripple_buy": ripple == "BUY", "ripple_sell": ripple == "SELL",
        "candle_buy": candle == "BUY", "candle_sell": candle == "SELL",
        "up": up, "dn": dn}


# ---- pure decision rules (unit-tested in isolation) -------------------------- #
def _prob_ok(prob: Optional[float]) -> bool:
    """1-hour up-probability gate — fail-open when unknown (boss's loose rule)."""
    return prob is None or float(prob) >= PROB_MIN


def entry_fires(rule: str, sig: dict, prob_ok: bool) -> bool:
    """Do the algorithms AGREE to BUY? strict = 3/3 · loose = ripple+candle+brain-ok."""
    if rule == "loose":
        return bool(sig["ripple_buy"] and sig["candle_buy"]
                    and sig["algo1_not_bearish"] and prob_ok)
    return bool(sig["algo1_buy"] and sig["ripple_buy"] and sig["candle_buy"])


def exit_reason(rule: str, entry: float, px: float, peak: float,
                stop_pct: float, eod: bool, sig: dict) -> Optional[str]:
    """Exit decision. The safety net (STOP / trailing TRAIL / EOD) is ALWAYS on;
    a consensus sell is an ADDITIONAL exit. None = keep holding."""
    r = _ripple_exit(entry, px, peak, TAKE_FLOOR, stop_pct, eod)
    if r:
        return r
    if rule == "loose":
        if sig["ripple_sell"] and sig["candle_sell"]:
            return "CONSENSUS"
    else:
        if sig["algo1_sell"] and sig["ripple_sell"] and sig["candle_sell"]:
            return "CONSENSUS"
    return None


def _place(db, code: str, side: str, qty: int):
    from services.paper_desk import place_order
    return place_order(db, code, side, qty, "market", source="algo4")


def _cash(db) -> float:
    return float(db.execute(text("SELECT cash FROM paper_desk_account WHERE id=1")).scalar() or 0)


def tick(db, force: bool = False) -> dict[str, Any]:
    """One heartbeat: consensus BUY on agreement, safety-net + consensus exits."""
    _ensure(db)
    cfg = _cfg(db)
    out: dict[str, Any] = {"enabled": cfg["enabled"], "opened": [], "closed": []}
    if not force and not _market_open_now():
        out["reason"] = "market closed"
        return out
    if not cfg["enabled"]:
        out["reason"] = "algorithm 4 is OFF"
        return out
    rule = cfg["rule"]
    need, tf = _candle_need_tf(db)
    scan_items = _scan_items(db)
    n = datetime.now(KST)
    eod = (n.hour * 60 + n.minute) >= (EOD_FLAT_HHMM[0] * 60 + EOD_FLAT_HHMM[1])

    # ---- EXITS first (protect open positions) ---- #
    open_rows = db.execute(text(
        "SELECT id, ticker, name, qty, entry FROM cross_trades WHERE status='OPEN'")).fetchall()
    open_codes = set()
    for oid, tk, name, qty, entry in open_rows:
        px = _px(tk)
        if px is None:
            open_codes.add(tk)
            continue
        entry = float(entry)
        peak = max(_peak.get(tk, entry), px)
        _peak[tk] = peak
        sig = _signals_for(db, tk, need, tf, scan_items, allow_compute=True)
        reason = exit_reason(rule, entry, px, peak, cfg["stop_pct"], eod, sig)
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
                "UPDATE cross_trades SET status='CLOSED', exit_price=:x, exit_reason=:r, "
                "net_pct=:n, closed_at=now() WHERE id=:i"),
                {"x": fill, "r": reason, "n": round(net, 3), "i": oid})
            db.commit()
            _peak.pop(tk, None)
            out["closed"].append({"name": name, "reason": reason, "net_pct": round(net, 2)})
        else:
            open_codes.add(tk)

    # ---- ENTRIES (one open per stock; only on agreement) ---- #
    if eod:
        out["reason"] = "EOD flat — no new entries after 15:18"
        return out
    for code in cfg["codes"]:
        if code in open_codes:
            continue
        sig = _signals_for(db, code, need, tf, scan_items, allow_compute=True)
        prob = sig["algo1_prob"]
        if rule == "loose" and prob is None and sig["ripple_buy"] and sig["candle_buy"]:
            try:                                   # only ONE stock, only when close → cheap
                from services.hourly_model import prob_up_1h
                prob = prob_up_1h(db, code)
            except Exception:
                db.rollback()
        if not entry_fires(rule, sig, _prob_ok(prob)):
            continue
        px = _px(code)
        if px is None:
            continue
        qty = int(_cash(db) * cfg["pos_pct"] / 100 / px)
        if qty < 1:
            continue
        agree_ko = "3/3 동의" if rule == "strict" else "2/3+브레인"
        why = (f"교차검증 {agree_ko}: 🤖알고1 {sig['algo1']} · ⚡리플 {sig['ripple']} · "
               f"🕯️캔들 {sig['candle']} 동시 매수 → 진입 (−{cfg['stop_pct']}% 손절·트레일 청산)")
        if cfg["mode"] == "semi":
            _semi_signals[code] = {"code": code, "name": _name(code), "price": px,
                                   "qty": qty, "why": why, "ts": n.timestamp()}
            out["opened"].append({"code": code, "semi_signal": True})
            continue
        r = _place(db, code, "BUY", qty)
        if r.get("ok"):
            fill = float(r.get("fill_price") or px)
            _peak[code] = fill
            db.execute(text(
                "INSERT INTO cross_trades (ticker, name, qty, entry, why) VALUES (:t,:n,:q,:e,:w)"),
                {"t": code, "n": _name(code), "q": qty, "e": fill, "w": why[:300]})
            db.commit()
            out["opened"].append({"code": code, "qty": qty, "entry": fill})
            logger.info("algo4 cross BUY %s x%d @ %s (%s)", code, qty, fill, rule)
    return out


def semi_buy(db, code: str) -> dict[str, Any]:
    _ensure(db)
    code = str(code).strip().zfill(6)
    sig = _semi_signals.get(code)
    px = _px(code)
    if px is None:
        return {"ok": False, "error": "no live price"}
    cfg = _cfg(db)
    qty = (sig or {}).get("qty") or int(_cash(db) * cfg["pos_pct"] / 100 / px)
    if qty < 1:
        return {"ok": False, "error": "size too small"}
    r = _place(db, code, "BUY", qty)
    if not r.get("ok"):
        return r
    fill = float(r.get("fill_price") or px)
    why = (sig or {}).get("why") or "교차검증: 세 알고리즘 동의 매수"
    db.execute(text(
        "INSERT INTO cross_trades (ticker, name, qty, entry, why) VALUES (:t,:n,:q,:e,:w)"),
        {"t": code, "n": _name(code), "q": qty, "e": fill, "w": why[:300]})
    db.commit()
    _peak[code] = fill
    _semi_signals.pop(code, None)
    return {"ok": True, "fill_price": fill, "qty": qty,
            "stop_at": round(fill * (1 - cfg["stop_pct"] / 100))}


def sell_all(db, code: str) -> dict[str, Any]:
    _ensure(db)
    code = str(code).strip().zfill(6)
    row = db.execute(text(
        "SELECT id, qty, entry FROM cross_trades WHERE ticker=:t AND status='OPEN' "
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
        "UPDATE cross_trades SET status='CLOSED', exit_price=:x, exit_reason='MANUAL', "
        "net_pct=:n, closed_at=now() WHERE id=:i"),
        {"x": fill, "n": round(net, 3), "i": oid})
    db.commit()
    _sell_hint.pop(code, None)
    _peak.pop(code, None)
    return {"ok": True, "realized_pnl": round(sell_qty * float(entry) * net / 100),
            "realized_pnl_pct": round(net, 2)}


def status(db) -> dict[str, Any]:
    """Everything the Cross-Check page needs — CHEAP: warm caches + memory only,
    no scan()/decide() compute. Per stock: the 3 signal lights, agreement, open
    P&L, today's record, recent trades. Same shape family as Algorithm 3's status."""
    cfg = _cfg(db)
    rule = cfg["rule"]
    need, tf = _candle_need_tf(db)
    scan_items = _scan_items(db)
    open_map = {}
    for r in db.execute(text(
            "SELECT ticker, qty, entry, opened_at FROM cross_trades WHERE status='OPEN'")):
        open_map[r[0]] = {"qty": int(r[1]), "entry": float(r[2]), "opened_at": str(r[3])}
    stocks = []
    for code in cfg["codes"]:
        px = _px(code)
        o = open_map.get(code)
        sig = _signals_for(db, code, need, tf, scan_items, allow_compute=False)
        agree_buy = entry_fires(rule, sig, _prob_ok(sig["algo1_prob"]))
        chg = None
        try:
            from services.paper_desk import _chg_cache
            chg = _chg_cache.get(code)
        except Exception:
            pass
        advice = None
        if o and cfg["mode"] == "semi":
            advice = _sell_hint.get(code)
        stop_at = round(o["entry"] * (1 - cfg["stop_pct"] / 100)) if o else None
        stocks.append({
            "code": code, "name": _name(code), "price": px, "chg": chg,
            "state": "LONG" if o else "WAIT",
            "entry": o["entry"] if o else None, "qty": o["qty"] if o else None,
            "pnl_pct": round((px / o["entry"] - 1) * 100 - 0.23, 2) if (o and px) else None,
            "stop_at": stop_at, "advice": advice,
            "algo1": sig["algo1"], "ripple": sig["ripple"], "candle": sig["candle"],
            "algo1_prob": sig["algo1_prob"], "agree_buy": agree_buy})
    today = db.execute(text(
        "SELECT count(*), coalesce(sum(CASE WHEN net_pct>0 THEN 1 ELSE 0 END),0), "
        "coalesce(sum(net_pct),0), coalesce(sum(qty*entry*net_pct/100.0),0) "
        "FROM cross_trades WHERE status='CLOSED' "
        "AND closed_at::date=(now() AT TIME ZONE 'Asia/Seoul')::date")).first()
    recent = [{"name": r[0], "qty": int(r[1]), "entry": float(r[2]),
               "exit_price": (float(r[3]) if r[3] is not None else None),
               "exit_reason": r[4],
               "net_pct": (float(r[5]) if r[5] is not None else None),
               "won": (round(int(r[1]) * float(r[2]) * float(r[5]) / 100) if r[5] is not None else None),
               "closed_at": str(r[6]), "opened_at": str(r[7]), "why": r[8]}
              for r in db.execute(text(
                  "SELECT name, qty, entry, exit_price, exit_reason, net_pct, closed_at, "
                  "opened_at, why FROM cross_trades WHERE status='CLOSED' "
                  "ORDER BY closed_at DESC LIMIT 150"))]
    now_ts = datetime.now(KST).timestamp()
    signals = [s for c, s in _semi_signals.items()
               if now_ts - s.get("ts", 0) < 120 and c not in open_map] if cfg["mode"] == "semi" else []
    rule_ko = ("3개 모두 매수 동의 → 진입 (엄격)" if rule == "strict"
               else "리플+캔들 매수 & 알고1 비관 아님+확률 OK → 진입 (느슨)")
    rule_en = ("all 3 agree BUY → enter (strict)" if rule == "strict"
               else "ripple+candle BUY & algo1 not-bearish+prob ok → enter (loose)")
    return {**cfg, "signals": signals, "stocks": stocks,
            "today": {"trades": int(today[0] or 0), "wins": int(today[1] or 0),
                      "net_pct_sum": round(float(today[2] or 0), 2),
                      "realized_won": round(float(today[3] or 0))},
            "recent": recent, "market_open": _market_open_now(),
            "streak": need, "tf": tf,
            "rule_ko": f"{rule_ko} · −{cfg['stop_pct']}% 손절 · 트레일 청산 · 15:18 정리",
            "rule_en": f"{rule_en} · −{cfg['stop_pct']}% stop · trailing exit · flat 15:18"}
