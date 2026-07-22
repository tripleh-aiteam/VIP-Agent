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
# RECENT-WINDOW agreement (boss 2026-07-22): the three algorithms run on very different
# clocks — the brain holds BUY for hours while Ripple/Candle only flash for seconds — so
# strict same-instant 3/3 almost never fires. Instead an algorithm "agrees" if it said BUY
# within the last AGREE_WINDOW_SEC. Keeps full consensus, but lets brief signals line up.
AGREE_WINDOW_SEC = 300           # 5 minutes
# fallback default watchlist (the shared 21 the fleet trades) if scalp_state isn't set yet
SCALP_21 = ("000660,005930,042660,035420,009150,373220,005380,000270,005490,035720,"
            "051910,006400,105560,055550,012450,329180,034020,010140,042700,066570,006840")

_peak: dict[str, float] = {}          # in-memory winner-peak (NO DB column — see scalp_trader)
_last_buy: dict[str, dict[str, float]] = {}   # code -> {algo1/ripple/candle: last BUY epoch}
_semi_signals: dict[str, dict] = {}   # semi mode: code -> live consensus BUY card
_sell_hint: dict[str, str] = {}       # semi mode: code -> pending SELL advice reason
_status_cache: Optional[tuple[float, dict]] = None   # 3s full-payload cache (page polls 4s)
# SNAPSHOT pattern (boss 2026-07-22, same lesson as auto_trader focus_status): the WEB
# path must NEVER compute signals — the 15s background tick() writes per-stock signals
# here and status() only READS them. Before the first tick: "warming up" placeholders.
_signal_snapshot: dict[str, dict] = {}   # code -> _signals_for() dict
_snapshot_ts: float = 0.0
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
    the raw booleans the entry/exit rules need AND a bilingual WHY per algorithm
    (boss 2026-07-22: every verdict must say its reason). allow_compute=True (tick)
    lets the Algo-1 brain compute on a cache miss; status() passes False."""
    from services import decision_brain, candle_trader
    ripple, rip_ko, rip_en = decision_brain._ripple_now(code)
    up, dn, cn = candle_trader._streaks_tf(code, tf)
    candle = "BUY" if up >= need else "SELL" if dn >= need else "WAIT"
    if not cn:
        can_ko, can_en = f"{tf}분봉 데이터 대기 중", f"waiting for {tf}-min candles"
    elif candle == "BUY":
        can_ko, can_en = (f"{tf}분봉 {up}연속 양봉 (기준 {need}) → 매수 신호",
                          f"{up} up {tf}-min candles in a row (needs {need}) → BUY")
    elif candle == "SELL":
        can_ko, can_en = (f"{tf}분봉 {dn}연속 음봉 (기준 {need}) → 매도 신호",
                          f"{dn} down {tf}-min candles in a row (needs {need}) → SELL")
    else:
        can_ko, can_en = (f"양봉 {up}·음봉 {dn}연속 — {need}연속 대기",
                          f"up {up}·down {dn} — waiting for {need} in a row")
    # Algorithm 1: scan act_now (conf gate) OR brain BUY; brain SELL = bearish
    item = scan_items.get(str(code).zfill(6))
    conf = (item.get("confidence") or 0) if item else 0
    scan_buy = bool(item and conf >= CONF_MIN)
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
    if scan_buy:
        a1_ko = f"엔진 스캔 지금 매수 자리 (확신 {conf}%)"
        a1_en = f"setup scan says ACT NOW (confidence {conf}%)"
    elif decision == "BUY":
        a1_ko, a1_en = "종합 브레인 판정: 매수", "combined brain verdict: BUY"
    elif decision == "SELL":
        a1_ko, a1_en = "종합 브레인 판정: 매도 (비관)", "combined brain verdict: SELL (bearish)"
    elif decision:
        a1_ko, a1_en = "브레인 중립 — 매수 신호 없음", "brain neutral — no buy signal"
    else:
        a1_ko, a1_en = "스캔·브레인 신호 대기 중", "waiting for scan/brain signal"
    prob = item.get("ai_1h_prob") if item else None
    return {
        "algo1": algo1, "ripple": ripple, "candle": candle,
        "algo1_buy": algo1_buy, "algo1_not_bearish": decision != "SELL",
        "algo1_sell": decision == "SELL", "algo1_prob": prob,
        "ripple_buy": ripple == "BUY", "ripple_sell": ripple == "SELL",
        "candle_buy": candle == "BUY", "candle_sell": candle == "SELL",
        "up": up, "dn": dn,
        "algo1_why_ko": a1_ko, "algo1_why_en": a1_en,
        "ripple_why_ko": rip_ko, "ripple_why_en": rip_en,
        "candle_why_ko": can_ko, "candle_why_en": can_en}


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


def _stamp_recent(snap: dict, now_ts: float) -> None:
    """Record the moment each algorithm last said BUY, per code (recent-window agree)."""
    for code, sig in snap.items():
        lb = _last_buy.setdefault(code, {})
        if sig.get("algo1_buy"):
            lb["algo1"] = now_ts
        if sig.get("ripple_buy"):
            lb["ripple"] = now_ts
        if sig.get("candle_buy"):
            lb["candle"] = now_ts


def _recent(code: str, algo: str, now_ts: float) -> bool:
    return (now_ts - _last_buy.get(code, {}).get(algo, 0.0)) <= AGREE_WINDOW_SEC


def _secs_since_buy(code: str, algo: str, now_ts: float) -> Optional[float]:
    ts = _last_buy.get(code, {}).get(algo)
    return (now_ts - ts) if ts else None


def _windowed_sig(code: str, sig: dict, now_ts: float) -> dict:
    """A copy of sig where each *_buy is TRUE if buying NOW or fired within the agreement
    window — this is what the entry rule and the agree highlight use (recent-window)."""
    w = dict(sig)
    w["algo1_buy"] = bool(sig.get("algo1_buy") or _recent(code, "algo1", now_ts))
    w["ripple_buy"] = bool(sig.get("ripple_buy") or _recent(code, "ripple", now_ts))
    w["candle_buy"] = bool(sig.get("candle_buy") or _recent(code, "candle", now_ts))
    return w


def _place(db, code: str, side: str, qty: int):
    from services.paper_desk import place_order
    return place_order(db, code, side, qty, "market", source="algo4")


def _cash(db) -> float:
    return float(db.execute(text("SELECT cash FROM paper_desk_account WHERE id=1")).scalar() or 0)


def tick(db, force: bool = False) -> dict[str, Any]:
    """One heartbeat: consensus BUY on agreement, safety-net + consensus exits.
    ALSO writes the per-stock signal SNAPSHOT that status() serves (web path
    never computes — boss 2026-07-22, the auto_trader focus_status lesson)."""
    global _signal_snapshot, _snapshot_ts
    import time as _t
    _ensure(db)
    cfg = _cfg(db)
    out: dict[str, Any] = {"enabled": cfg["enabled"], "opened": [], "closed": []}
    if not force and not _market_open_now():
        out["reason"] = "market closed"
        return out
    _t0 = _t.time()
    rule = cfg["rule"]
    need, tf = _candle_need_tf(db)
    scan_items = _scan_items(db)
    # ---- SNAPSHOT for the whole watchlist (runs even when the engine is OFF, so
    # the page shows live lights before the boss enables it). Cache-peek only
    # (allow_compute=False → no db use in threads); trade paths below refresh
    # their own codes with allow_compute=True. ---- #
    from concurrent.futures import ThreadPoolExecutor
    snap: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as _ex:
        for code, sig in _ex.map(
                lambda c: (c, _signals_for(None, c, need, tf, scan_items, allow_compute=False)),
                cfg["codes"]):
            snap[code] = sig
    _stamp_recent(snap, _t.time())     # record each algo's last BUY for the recent window
    if not cfg["enabled"]:
        _signal_snapshot, _snapshot_ts = snap, _t.time()
        logger.info("algo4 tick(snapshot-only) %.1fs (%d codes, engine off)", _t.time() - _t0, len(snap))
        out["reason"] = "algorithm 4 is OFF"
        return out
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
        snap[tk] = sig
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
        _signal_snapshot, _snapshot_ts = snap, _t.time()
        logger.info("algo4 tick %.1fs (%d codes, EOD)", _t.time() - _t0, len(snap))
        out["reason"] = "EOD flat — no new entries after 15:18"
        return out
    now_ts = _t.time()
    for code in cfg["codes"]:
        if code in open_codes:
            continue
        # cheap pre-check using the RECENT WINDOW (not just this instant) — only codes
        # where ripple AND candle bought recently get the full (possibly computing) read
        if not (_recent(code, "ripple", now_ts) and _recent(code, "candle", now_ts)):
            continue
        sig = _signals_for(db, code, need, tf, scan_items, allow_compute=True)
        snap[code] = sig
        _stamp_recent({code: sig}, now_ts)         # fresh compute may add a BUY just now
        wsig = _windowed_sig(code, sig, now_ts)    # buys-now OR fired-within-5-min
        prob = sig["algo1_prob"]
        if rule == "loose" and prob is None and wsig["ripple_buy"] and wsig["candle_buy"]:
            try:                                   # only ONE stock, only when close → cheap
                from services.hourly_model import prob_up_1h
                prob = prob_up_1h(db, code)
            except Exception:
                db.rollback()
        if not entry_fires(rule, wsig, _prob_ok(prob)):
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
    _signal_snapshot, _snapshot_ts = snap, _t.time()
    logger.info("algo4 tick %.1fs (%d codes)", _t.time() - _t0, len(snap))
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
    P&L, today's record, recent trades. Same shape family as Algorithm 3's status.

    The whole payload is cached 3s (the page polls every 4s; concurrent viewers
    dedupe) and the 21-stock signal fan-out is PARALLEL — a serial cold build hit
    ~15s of Kiwoom round-trips and piled up behind the 4s poll."""
    global _status_cache
    import time as _t
    if _status_cache and _t.time() - _status_cache[0] < 3.0:
        return _status_cache[1]
    cfg = _cfg(db)
    rule = cfg["rule"]
    need, tf = _candle_need_tf(db)
    open_map = {}
    for r in db.execute(text(
            "SELECT ticker, qty, entry, opened_at FROM cross_trades WHERE status='OPEN'")):
        open_map[r[0]] = {"qty": int(r[1]), "entry": float(r[2]), "opened_at": str(r[3])}
    # WEB PATH READS THE SNAPSHOT ONLY (written by the 15s tick) — never computes
    # signals here. Before the first tick: warming-up placeholders (~15s).
    warming = not _signal_snapshot or (_t.time() - _snapshot_ts) > 120
    _ph_ko, _ph_en = "신호 준비 중 (~15초)", "signals warming up (~15s)"
    _placeholder = {
        "algo1": "WAIT", "ripple": "WAIT", "candle": "WAIT",
        "algo1_buy": False, "algo1_not_bearish": True, "algo1_sell": False,
        "algo1_prob": None, "ripple_buy": False, "ripple_sell": False,
        "candle_buy": False, "candle_sell": False, "up": 0, "dn": 0,
        "algo1_why_ko": _ph_ko, "algo1_why_en": _ph_en,
        "ripple_why_ko": _ph_ko, "ripple_why_en": _ph_en,
        "candle_why_ko": _ph_ko, "candle_why_en": _ph_en}
    # prices only (per-ticker 2s micro-cache) — parallel, cheap
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as _ex:
        _pxs = dict(zip(cfg["codes"], _ex.map(_px, cfg["codes"])))
    now_ts = _t.time()
    win_min = round(AGREE_WINDOW_SEC / 60)
    stocks = []
    for code in cfg["codes"]:
        px = _pxs.get(code)
        sig = _signal_snapshot.get(code) or _placeholder
        o = open_map.get(code)
        # RECENT-WINDOW agreement: a signal counts if buying NOW or fired within the window
        wsig = _windowed_sig(code, sig, now_ts)
        agree_buy = entry_fires(rule, wsig, _prob_ok(sig["algo1_prob"]))
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
        # per-algo recency: minutes since its last BUY (None = never / outside window)
        def _ago_min(algo: str) -> Optional[int]:
            s = _secs_since_buy(code, algo, now_ts)
            return round(s / 60) if (s is not None and s <= AGREE_WINDOW_SEC) else None
        ago = {"algo1": _ago_min("algo1"), "ripple": _ago_min("ripple"), "candle": _ago_min("candle")}
        # agreement summary — now counts buys within the window (boss 2026-07-22)
        wbuys = {"algo1": wsig["algo1_buy"], "ripple": wsig["ripple_buy"], "candle": wsig["candle_buy"]}
        votes = {"algo1": sig["algo1"], "ripple": sig["ripple"], "candle": sig["candle"]}
        n_buy = sum(1 for v in wbuys.values() if v)
        n_sell = sum(1 for v in votes.values() if v == "SELL")
        _nm_ko = {"algo1": "알고1", "ripple": "잔물결", "candle": "캔들"}
        _nm_en = {"algo1": "Algo1", "ripple": "Ripple", "candle": "Candle"}
        not_buy = [k for k, v in wbuys.items() if not v]
        if agree_buy:
            agree_ko = f"3개 모두 매수 동의 (최근 {win_min}분 내) → 매수 진입"
            agree_en = f"all 3 agree to buy (within {win_min} min) → BUY entry"
        elif n_buy > 0:
            miss_ko = "·".join(_nm_ko[k] for k in not_buy)
            miss_en = ", ".join(_nm_en[k] for k in not_buy)
            agree_ko = f"{n_buy}/3 매수 (최근 {win_min}분) — {miss_ko} 대기 → 진입 안 함"
            agree_en = f"{n_buy}/3 buy (last {win_min}m) — waiting on {miss_en} → no entry"
        else:
            agree_ko, agree_en = "매수 동의 0/3 — 관망", "0/3 buy votes — watching"
        sell_need = 3 if rule == "strict" else 2
        sell_agree = (n_sell >= 3) if rule == "strict" else (sig["ripple_sell"] and sig["candle_sell"])
        stocks.append({
            "code": code, "name": _name(code), "price": px, "chg": chg,
            "state": "LONG" if o else "WAIT",
            "entry": o["entry"] if o else None, "qty": o["qty"] if o else None,
            "pnl_pct": round((px / o["entry"] - 1) * 100 - 0.23, 2) if (o and px) else None,
            "stop_at": stop_at, "advice": advice,
            "algo1": sig["algo1"], "ripple": sig["ripple"], "candle": sig["candle"],
            "algo1_prob": sig["algo1_prob"], "agree_buy": agree_buy,
            "algo1_why_ko": sig["algo1_why_ko"], "algo1_why_en": sig["algo1_why_en"],
            "ripple_why_ko": sig["ripple_why_ko"], "ripple_why_en": sig["ripple_why_en"],
            "candle_why_ko": sig["candle_why_ko"], "candle_why_en": sig["candle_why_en"],
            "n_buy": n_buy, "n_sell": n_sell, "sell_agree": sell_agree, "sell_need": sell_need,
            "agree_why_ko": agree_ko, "agree_why_en": agree_en,
            "algo1_ago": ago["algo1"], "ripple_ago": ago["ripple"], "candle_ago": ago["candle"],
            "window_min": win_min})
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
    out = {**cfg, "signals": signals, "stocks": stocks, "warming": warming,
           "today": {"trades": int(today[0] or 0), "wins": int(today[1] or 0),
                     "net_pct_sum": round(float(today[2] or 0), 2),
                     "realized_won": round(float(today[3] or 0))},
           "recent": recent, "market_open": _market_open_now(),
           "streak": need, "tf": tf,
           "rule_ko": f"{rule_ko} · −{cfg['stop_pct']}% 손절 · 트레일 청산 · 15:18 정리",
           "rule_en": f"{rule_en} · −{cfg['stop_pct']}% stop · trailing exit · flat 15:18"}
    _status_cache = (_t.time(), out)
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  🗣️  PLAIN-LANGUAGE EXPLANATION (boss 2026-07-22): explain each card like
#  telling a friend over coffee — zero trading jargon. Deterministic templates
#  (no LLM call), cache-only reads, identical every time.
# ═══════════════════════════════════════════════════════════════════════════
def _decide_full_peek(code: str) -> Optional[dict]:
    """The WHOLE cached decide() dict, read-only (never computes on the web path)."""
    try:
        import time as _t
        from services.decision_agent import _decide_cache
        hit = _decide_cache.get(str(code).zfill(6))
        if hit and _t.time() - hit[0] < 300:
            return hit[1] or None
    except Exception:
        pass
    return None


def _ripple_plain(code: str) -> str:
    """Plain state of the 'ripple watcher' (cache-backed 1-min read, no jargon).
    -> climbing_now | already_ran | not_yet | no_data."""
    try:
        from services.scalp_trader import _candles_1m
        cs = _candles_1m(code, n=12)
        closes = [b.get("close") for b in cs if b.get("close")]
        if len(closes) < 4:
            return "no_data"
        lo = min(closes)
        if lo <= 0:
            return "no_data"
        bounce = (closes[-1] / lo - 1) * 100
        rising = closes[-1] > closes[-2] > closes[-3]
        if rising and 0.10 <= bounce <= 0.45:
            return "climbing_now"
        if bounce > 0.45:
            return "already_ran"
        return "not_yet"
    except Exception:
        return "no_data"


def _build_explain(name: str, sig: dict, d: Optional[dict], rip_state: str,
                   need: int, tf: str, held: bool, en: bool) -> dict:
    """Pure text builder — friendly, everyday language. Testable in isolation."""
    up, dn = int(sig.get("up") or 0), int(sig.get("dn") or 0)
    L: dict[str, str] = {}

    # 🤖 the smart brain — news mood, big buyers, chart health
    if not d:
        L["algo1"] = ("🤖 The smart brain is still studying this stock — check back in a minute."
                      if en else "🤖 똑똑한 브레인이 아직 이 종목을 분석 중이에요 — 잠시 후 다시 눌러보세요.")
    else:
        dec = (d.get("decision") or "HOLD").upper()
        news = (d.get("news") or {}).get("score") or 0
        flows = (d.get("flows") or {}).get("score") or 0
        tech = (d.get("technicals") or {}).get("score") or 0
        bko, ben = [], []
        if flows > 0:
            bko.append("큰손들이 오늘 이 주식을 사들이고 있어요"); ben.append("big investors have been buying this stock today")
        elif flows < 0:
            bko.append("큰손들이 팔고 있어요"); ben.append("big investors have been selling it")
        if news > 0:
            bko.append("뉴스 분위기도 좋아요"); ben.append("the news mood is friendly")
        elif news < 0:
            bko.append("뉴스 분위기가 안 좋아요"); ben.append("the news has been gloomy")
        if tech > 0:
            bko.append("차트 모양도 건강해 보여요"); ben.append("the overall picture looks healthy")
        elif tech < 0:
            bko.append("차트는 아직 힘이 없어요"); ben.append("the chart still looks weak")
        if dec == "BUY":
            vk, ve = "사도 좋겠다고 해요", "says BUY"
        elif dec == "SELL":
            vk, ve = "지금은 조심하라고 해요", "says be careful"
        else:
            vk, ve = "조금 더 기다리라고 해요", "says WAIT"
        rk = ", ".join(bko[:2]) if bko else "아직 뚜렷한 이유는 안 보여요"
        re_ = ", ".join(ben[:2]) if ben else "there's no strong reason either way yet"
        L["algo1"] = (f"🤖 The smart brain {ve} — {re_}." if en
                      else f"🤖 똑똑한 브레인은 {vk} — {rk}.")

    # ⚡ the ripple watcher — waits for the price to start climbing
    L["ripple"] = {
        "climbing_now": ("⚡ The ripple watcher says BUY — the price has just started climbing, and that's exactly the moment it waits for."
                         if en else "⚡ 잔물결 감시자는 사자고 해요 — 가격이 막 오르기 시작했어요. 딱 이 순간을 기다렸어요."),
        "already_ran": ("⚡ The ripple watcher says WAIT — the price already jumped up, so it won't chase; it waits for a small dip first."
                        if en else "⚡ 잔물결 감시자는 기다려요 — 가격이 이미 훌쩍 올랐어요. 뒤늦게 쫓아가지 않고 살짝 내릴 때를 기다려요."),
        "not_yet": ("⚡ The ripple watcher says WAIT — the price hasn't started climbing yet. It only buys the moment the price begins to rise."
                    if en else "⚡ 잔물결 감시자는 기다려요 — 아직 가격이 오르기 시작하지 않았어요. 오르기 시작하는 바로 그 순간에만 사요."),
        "no_data": ("⚡ The ripple watcher needs a little more price info first (the market just opened or it's quiet right now)."
                    if en else "⚡ 잔물결 감시자는 가격 정보를 조금 더 기다리고 있어요 (장 초반이거나 조용할 때예요)."),
    }.get(rip_state, "")

    # 🕯️ the candle watcher — counts green/red candles
    if not (up or dn):
        L["candle"] = (f"🕯️ The candle watcher is still counting the {tf}-minute candles."
                       if en else f"🕯️ 캔들 감시자는 아직 {tf}분 캔들을 세고 있어요.")
    elif up >= need:
        L["candle"] = (f"🕯️ The candle watcher says BUY — it wanted {need} green candles in a row, and now there are {up}."
                       if en else f"🕯️ 캔들 감시자는 사자고 해요 — 초록 캔들 {need}개가 연달아 나오길 기다렸는데 지금 {up}개가 됐어요.")
    elif dn >= need:
        L["candle"] = (f"🕯️ The candle watcher says SELL — it counted {dn} red candles in a row, so the price is sliding down."
                       if en else f"🕯️ 캔들 감시자는 팔라고 해요 — 빨간 캔들이 {dn}개 연달아 나왔어요. 가격이 흘러내리고 있어요.")
    elif up > 0:
        L["candle"] = (f"🕯️ The candle watcher says WAIT — it wants {need} green candles in a row and has {up} so far, so it's still counting."
                       if en else f"🕯️ 캔들 감시자는 기다려요 — 초록 캔들 {need}개가 연달아 필요한데 지금 {up}개예요. 아직 세는 중이에요.")
    else:
        L["candle"] = ("🕯️ The candle watcher says WAIT — no run of green candles yet."
                       if en else "🕯️ 캔들 감시자는 기다려요 — 아직 초록 캔들이 연달아 나오지 않았어요.")

    # 🔀 the plain conclusion
    votes = [sig.get("algo1"), sig.get("ripple"), sig.get("candle")]
    n_buy = votes.count("BUY")
    nm_ko = {"algo1": "브레인", "ripple": "잔물결 감시자", "candle": "캔들 감시자"}
    nm_en = {"algo1": "the brain", "ripple": "the ripple watcher", "candle": "the candle watcher"}
    not_buy = [k for k, v in (("algo1", votes[0]), ("ripple", votes[1]), ("candle", votes[2])) if v != "BUY"]
    if held:
        L["cross"] = ("🔀 Together: we're already holding this one — now it's just watching for the right time to sell."
                      if en else "🔀 종합: 이 종목은 이미 사둔 상태예요 — 이제 언제 팔지 지켜보는 중이에요.")
    elif n_buy >= 3:
        L["cross"] = ("🔀 Together: all three agree, so Cross-Check is buying this one now."
                      if en else "🔀 종합: 셋 다 사자고 해서, 교차검증이 지금 이 종목을 삽니다.")
    elif n_buy > 0:
        miss_ko = "와 ".join(nm_ko[k] for k in not_buy)
        miss_en = " and ".join(nm_en[k] for k in not_buy)
        L["cross"] = (f"🔀 Together: only {n_buy} of 3 agrees, so Cross-Check is NOT buying. When {miss_en} come around too, all three will line up."
                      if en else f"🔀 종합: 셋 중 {n_buy}명만 사자고 해서, 교차검증은 아직 안 삽니다. {miss_ko}까지 마음이 맞으면 그때 셋이 딱 맞아떨어져요.")
    else:
        L["cross"] = ("🔀 Together: none of the three wants to buy right now, so Cross-Check is just watching."
                      if en else "🔀 종합: 지금은 셋 다 사고 싶어 하지 않아서, 교차검증은 그냥 지켜보고 있어요.")

    return {"lines": L, "text": "\n".join([L["algo1"], L["ripple"], L["candle"], L["cross"]])}


def explain(db, code: str, lang: str = "ko") -> dict:
    """Friendly, jargon-free explanation of a stock's Cross-Check reasoning.
    Cache-only (fast, deterministic). Same verdicts as the card's lights."""
    code = str(code).strip().zfill(6)
    en = str(lang or "").lower().startswith("en")
    need, tf = _candle_need_tf(db)
    scan_items = _scan_items(db)
    sig = _signals_for(None, code, need, tf, scan_items, allow_compute=False)
    d = _decide_full_peek(code)
    rip_state = _ripple_plain(code)
    try:
        held = bool(db.execute(text(
            "SELECT 1 FROM cross_trades WHERE ticker=:t AND status='OPEN' LIMIT 1"),
            {"t": code}).first())
    except Exception:
        db.rollback()
        held = False
    built = _build_explain(_name(code), sig, d, rip_state, need, tf, held, en)
    return {"code": code, "name": _name(code),
            "algo1": sig.get("algo1"), "ripple": sig.get("ripple"), "candle": sig.get("candle"),
            **built}
