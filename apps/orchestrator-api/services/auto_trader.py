"""auto_trader.py — Phase 4: the AUTO-AGENT. The decision engine's hands.

Runs the boss's buy→sell→buy→sell loop AUTOMATICALLY on the paper desk (fake money):
  1. every tick (external cron ~5min + the desk page's poll) it asks the SAME decision
     engine the chatbot uses (intraday_setup.scan) for ACT_NOW setups;
  2. buys the best one on the paper desk (paper_desk.place_order — live prices, real
     fees), sized by AUTO_POS_PCT of desk equity;
  3. manages each open auto-position: SELL at the target band (first touch), SELL at
     the stop, SELL after the time-stop — the exact zone rules of the setup;
  4. every trade lands in the desk's history + its own auto_trades log, so the
     scorecard answers the ONLY question that matters: "does following the engine
     make money?" — with zero real won at risk.

Safety rails: OFF by default (auto_state.enabled, toggled from the Testing page);
max concurrent auto-positions; max trades/day; market-hours only; never touches
real money — the ONLY order path is the paper desk.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger("vip.auto_trader")
KST = timezone(timedelta(hours=9))

AUTO_POS_PCT = 10.0          # % of desk equity per trade
MAX_OPEN = 6                 # concurrent auto-positions (boss 2026-07-09 PM: test phase —
                             # "increase the number of trading and remove the limit")
MAX_TRADES_DAY = 30          # test-phase caps: effectively unlimited for a 6.5h session
MAX_TRADES_DAY_CHEAP = 40    # (~1 trade every 10 min if the scanner kept firing)
CHEAP_PX = 100_000           # <₩100k/share = the cheap tier (boss exit tiers + extension)
CIRCUIT_DAY_NET = -15.0      # disaster backstop only (boss removed the tight -5% for the
                             # test): ≈ −1.5% of the account in one day — beyond any
                             # normal bad day; exits always keep being managed
MIN_CONF = 65                # raised 60→65 (boss 2026-07-13 loss audit): signals need
                             # stronger proof until the measured win-rate recovers
DAY_LOSS_BRAKE_PCT = 1.0     # today's REALIZED losses (all trades, his + machine's)
                             # reach 1% of equity → every buy signal halts until tomorrow


def _day_brake(db) -> Optional[str]:
    """Returns a reason string when today's realized loss has hit the brake."""
    try:
        eq_cash = db.execute(text("SELECT cash FROM paper_desk_account WHERE id=1")).scalar()
        posv = db.execute(text(
            "SELECT COALESCE(SUM(qty*avg_price),0) FROM paper_desk_positions")).scalar()
        eq = float(eq_cash or 0) + float(posv or 0)
        realized = db.execute(text(
            "SELECT COALESCE(SUM(realized_pnl),0) FROM paper_desk_orders "
            "WHERE status='FILLED' AND side='SELL' "
            "AND COALESCE(filled_at, created_at)::date = (now() AT TIME ZONE 'Asia/Seoul')::date"
        )).scalar()
        if eq > 0 and float(realized or 0) <= -eq * DAY_LOSS_BRAKE_PCT / 100.0:
            return (f"오늘 실현 손실 {float(realized):,.0f}원 — 계좌의 -{DAY_LOSS_BRAKE_PCT:g}% 한도 도달, "
                    f"오늘은 모든 매수 신호를 중단합니다 (내일 초기화)")
    except Exception:
        db.rollback()
    return None

_DDL = (
    "CREATE TABLE IF NOT EXISTS auto_state ("
    " id INT PRIMARY KEY DEFAULT 1, enabled BOOLEAN DEFAULT FALSE,"
    " updated_at TIMESTAMPTZ DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS auto_trades ("
    " id SERIAL PRIMARY KEY, ticker TEXT, name TEXT, setup_type TEXT,"
    " qty BIGINT, entry DOUBLE PRECISION, target_lo DOUBLE PRECISION,"
    " target_hi DOUBLE PRECISION, stop DOUBLE PRECISION, time_min INT,"
    " confidence INT, status TEXT DEFAULT 'OPEN',"
    " exit_price DOUBLE PRECISION, exit_reason TEXT, net_pct DOUBLE PRECISION,"
    " opened_at TIMESTAMPTZ DEFAULT now(), closed_at TIMESTAMPTZ)",
    # veto receipts (boss: trust needs receipts — show WHEN the engine said no)
    "CREATE TABLE IF NOT EXISTS auto_vetoes ("
    " id SERIAL PRIMARY KEY, ticker TEXT, name TEXT, reason TEXT,"
    " ts TIMESTAMPTZ DEFAULT now())",
    # trail state for the boss's ride-the-rise exits (2026-07-13)
    "ALTER TABLE auto_trades ADD COLUMN IF NOT EXISTS peak DOUBLE PRECISION",
)


def _ensure(db) -> None:
    for ddl in _DDL:
        db.execute(text(ddl))
    r = db.execute(text("SELECT 1 FROM auto_state WHERE id=1")).first()
    if not r:
        db.execute(text("INSERT INTO auto_state (id, enabled) VALUES (1, FALSE)"))
    db.commit()


def is_enabled(db) -> bool:
    _ensure(db)
    return bool(db.execute(text("SELECT enabled FROM auto_state WHERE id=1")).scalar())


def set_enabled(db, on: bool) -> dict:
    _ensure(db)
    db.execute(text("UPDATE auto_state SET enabled=:e, updated_at=now() WHERE id=1"),
               {"e": bool(on)})
    db.commit()
    return {"ok": True, "enabled": bool(on)}


def _market_open_now() -> bool:
    n = datetime.now(KST)
    return n.weekday() < 5 and (9 * 60) <= (n.hour * 60 + n.minute) <= (15 * 60 + 20)


def _live_px(code: str) -> Optional[float]:
    from services.paper_desk import _live_price
    px, _ = _live_price(code)
    return px


def tick(db, force: bool = False) -> dict[str, Any]:
    """One auto-agent pass: manage exits first (protect), then consider a new entry.
    Idempotent; safe to fire every few minutes. force=True ignores market hours
    (testing only — exits still honest, entries use live/last prices)."""
    _ensure(db)
    out: dict[str, Any] = {"enabled": is_enabled(db), "closed": [], "opened": None,
                           "reason": None}
    if not out["enabled"]:
        out["reason"] = "auto-trading is OFF"
        return out
    if not force and not _market_open_now():
        out["reason"] = "market closed"
        return out

    # ---- 0) POSITION GUARD backstop (boss's -1%/-peak-1% auto-protection on his own
    # focus holdings; the desk page's 4s poll is primary, this cron covers page-closed)
    try:
        from services.position_guard import run as _guard_run
        _g = _guard_run(db)
        if _g:
            out["guard"] = _g
    except Exception:
        db.rollback()

    # ---- 1) MANAGE OPEN AUTO-POSITIONS (exits first — protection before opportunity) --
    from services.paper_desk import place_order
    open_rows = db.execute(text(
        "SELECT id, ticker, name, qty, entry, target_lo, stop, time_min, "
        "EXTRACT(EPOCH FROM (now()-opened_at))/60 AS age_min, peak "
        "FROM auto_trades WHERE status='OPEN'")).fetchall()
    for oid, tk, name, qty, entry, tlo, stop, tmin, age, peak in open_rows:
        # RECONCILE (2026-07-14): the desk is ONE shared book — the boss, the guard,
        # or an overlapping trade may have already sold these shares. If the desk no
        # longer holds them, close this row as EXTERNAL at the actual last sell fill
        # instead of retrying a doomed SELL every tick (165 REJECTED orders/day bug).
        held = int(db.execute(text(
            "SELECT qty FROM paper_desk_positions WHERE ticker=:t"),
            {"t": tk}).scalar() or 0)
        if held <= 0:
            last_fill = db.execute(text(
                "SELECT fill_price FROM paper_desk_orders WHERE ticker=:t AND side='SELL' "
                "AND status='FILLED' AND fill_price IS NOT NULL "
                "AND created_at >= (SELECT opened_at FROM auto_trades WHERE id=:i) "
                "ORDER BY created_at DESC LIMIT 1"), {"t": tk, "i": oid}).scalar()
            fill = float(last_fill or _live_px(tk) or entry)
            net = (fill / float(entry) - 1) * 100 - 0.23
            db.execute(text(
                "UPDATE auto_trades SET status='CLOSED', exit_price=:x, exit_reason='EXTERNAL', "
                "net_pct=:n, closed_at=now() WHERE id=:i"),
                {"x": fill, "n": round(net, 3), "i": oid})
            db.commit()
            out["closed"].append({"name": name, "reason": "EXTERNAL", "net_pct": round(net, 2)})
            continue
        qty = min(int(qty), held)   # partial outside-sell → manage what's actually left
        px = _live_px(tk)
        if px is None:
            continue
        # BOSS EXITS (2026-07-13): reaching the target no longer sells — it ARMS the
        # trail. Ride while rising; sell 1% below the peak. Stop and time doors stay.
        peak = max(float(peak or entry), float(px))
        try:
            db.execute(text("UPDATE auto_trades SET peak=:p WHERE id=:i"),
                       {"p": peak, "i": oid})
            db.commit()
        except Exception:
            db.rollback()
        armed = peak >= float(tlo)
        reason = None
        if armed and px <= peak * 0.99:
            reason = "TRAIL"
        elif px <= float(stop):
            reason = "STOP"
        elif age is not None and float(age) >= float(tmin):
            reason = "TIME"
        if not reason:
            continue
        r = place_order(db, tk, "SELL", int(qty), "market")
        if r.get("ok"):
            fill = float(r.get("fill_price") or px)
            net = (fill / float(entry) - 1) * 100 - 0.23
            db.execute(text(
                "UPDATE auto_trades SET status='CLOSED', exit_price=:x, exit_reason=:rr, "
                "net_pct=:n, closed_at=now() WHERE id=:i"),
                {"x": fill, "rr": reason, "n": round(net, 3), "i": oid})
            db.commit()
            out["closed"].append({"name": name, "reason": reason, "net_pct": round(net, 2)})
        else:
            logger.warning("auto_trader: SELL failed for %s: %s", tk, r.get("error"))

    # ---- 2) NEW ENTRY (one per tick, capped) ----
    n_open = db.execute(text(
        "SELECT count(*) FROM auto_trades WHERE status='OPEN'")).scalar() or 0
    if int(n_open) >= MAX_OPEN:
        out["reason"] = f"max open positions ({MAX_OPEN})"
        return out
    n_today = db.execute(text(
        "SELECT count(*) FROM auto_trades WHERE opened_at::date = (now() AT TIME ZONE 'Asia/Seoul')::date")).scalar() or 0
    if int(n_today) >= MAX_TRADES_DAY_CHEAP:
        out["reason"] = f"daily trade cap ({MAX_TRADES_DAY_CHEAP})"
        return out
    # DAILY CIRCUIT-BREAKER — a hostile day must not be traded to the last bullet
    day_net = float(db.execute(text(
        "SELECT coalesce(sum(net_pct),0) FROM auto_trades WHERE status='CLOSED' "
        "AND closed_at::date=(now() AT TIME ZONE 'Asia/Seoul')::date")).scalar() or 0)
    if day_net <= CIRCUIT_DAY_NET:
        out["reason"] = (f"daily circuit-breaker: today's closed trades sum "
                         f"{day_net:+.1f}% ≤ {CIRCUIT_DAY_NET}% — no new entries today "
                         f"(exits still managed)")
        return out
    # trades 11..20 are reserved for CHEAP stocks (<₩100k/share) — boss's test extension
    cheap_only = int(n_today) >= MAX_TRADES_DAY

    from services.intraday_setup import scan
    setups = [s for s in (scan(db, use_cache=False).get("act_now") or [])
              if s.get("confidence", 0) >= MIN_CONF and s.get("price")]
    # skip tickers we already hold as auto-positions (no doubling)
    held = {r[0] for r in db.execute(text(
        "SELECT ticker FROM auto_trades WHERE status='OPEN'")).fetchall()}
    setups = [s for s in setups if s["code"] not in held]
    if cheap_only:
        setups = [s for s in setups if float(s["price"]) < CHEAP_PX]
        if not setups:
            out["reason"] = (f"base cap {MAX_TRADES_DAY} reached — only cheap "
                             f"(<₩{CHEAP_PX:,}) setups may trade now (none qualify)")
            return out
    if not setups:
        out["reason"] = out["reason"] or "no qualifying setup"
        return out
    # DECISION-ENGINE VETO (boss 2026-07-09: "auto-trading must listen to the decision
    # engine"): before buying, ask the full 9-method fused verdict. SELL → forbidden
    # (skip to the next candidate). WATCH/HOLD = no objection — it's a 60-minute trade,
    # not an investment. Engine unavailable → fail-open: the scanner's own ML/news/regime
    # gates already passed, and a dead engine must not silently halt the whole agent.
    picked = None
    out["vetoed"] = []
    for cand in setups[:3]:
        if cand.get("setup_type") == "inverse_down":
            picked = cand            # the 9-method stock verdict doesn't apply to an
            break                    # inverse ETF — its gates live in _scan_down
        try:
            from services.decision_agent import decide_cached
            _d = decide_cached(db, cand["code"]) or {}
            if _d.get("decision") == "SELL":
                out["vetoed"].append({"name": cand["name"], "code": cand["code"],
                                      "reason": "decision engine says SELL"})
                logger.info("auto_trader veto: %s — decision engine SELL", cand["name"])
                try:                       # veto receipt for the panel
                    db.execute(text(
                        "INSERT INTO auto_vetoes (ticker, name, reason) VALUES (:t,:n,:r)"),
                        {"t": cand["code"], "n": cand["name"],
                         "r": "decision engine SELL"})
                    db.commit()
                except Exception:
                    db.rollback()
                continue
        except Exception as e:
            logger.warning("auto_trader: decide() failed for %s (%s) — proceeding on scanner gates",
                           cand["code"], str(e)[:80])
            db.rollback()
        picked = cand
        break
    if not picked:
        out["reason"] = "all candidates vetoed by the decision engine (SELL)"
        return out
    s = picked                                     # best non-vetoed (scan sorts by AI prob + conf)
    # SMART SIZING (boss 2026-07-13): engine-predicted size — risk core + conviction
    # (confidence · 🤖 AI · 📊 pattern) + caps. Falls back to the flat 10% rule.
    qty = 0
    try:
        from services.smart_size import suggest as _sz
        _pat = (s.get("pattern") or {})
        _sug = _sz(db, s["price"], s.get("stop_pct"), conf=s.get("confidence"),
                   ai_prob=s.get("ai_1h_prob"), pattern_up=_pat.get("up_rate"))
        if _sug:
            qty = int(_sug["qty"])
            out["sizing"] = {"mult": _sug["mult"], "capped_by": _sug["capped_by"]}
    except Exception:
        db.rollback()
    if qty < 1:
        from services.paper_desk import state as desk_state
        eq = float((desk_state(db) or {}).get("equity") or 0)
        qty = int(eq * AUTO_POS_PCT / 100.0 // float(s["price"]))
    if qty < 1:
        out["reason"] = "equity too small for 1 share of the setup"
        return out
    r = place_order(db, s["code"], "BUY", qty, "market")
    if not r.get("ok"):
        out["reason"] = f"BUY failed: {r.get('error') or r.get('reason')}"
        return out
    fill = float(r.get("fill_price") or s["price"])
    db.execute(text(
        "INSERT INTO auto_trades (ticker, name, setup_type, qty, entry, target_lo, "
        "target_hi, stop, time_min, confidence) VALUES (:t,:n,:st,:q,:e,:tl,:th,:s,:tm,:c)"),
        {"t": s["code"], "n": s["name"], "st": s.get("setup_type") or "dip", "q": qty,
         "e": fill, "tl": s["target_band"][0], "th": s["target_band"][1],
         "s": s["stop"], "tm": s["time_min"], "c": s["confidence"]})
    db.commit()
    out["opened"] = {"name": s["name"], "qty": qty, "entry": fill,
                     "target": s["target_band"], "stop": s["stop"],
                     "confidence": s["confidence"], "type": s.get("setup_type")}
    return out


def buy_candidates(db, max_n: int = 3) -> dict[str, Any]:
    """The setups the auto-trader WOULD buy right now — the exact same quality gates
    as tick() (market open, conf ≥ MIN_CONF, not already held, decision-engine veto),
    WITHOUT placing an order. Powers the ⚡ popup (boss 2026-07-09: 'if it pops, it
    must be a buy — filter the rest before it reaches me'). When only auto's budget
    blocks (caps/circuit-breaker), candidates still show for MANUAL trading, with the
    block stated in auto_note."""
    _ensure(db)
    out: dict[str, Any] = {"candidates": [], "auto_note": None}
    if not _market_open_now():
        out["auto_note"] = "market closed"
        return out
    _brake = _day_brake(db)
    if _brake:
        out["auto_note"] = _brake
        return out
    try:
        from services.intraday_setup import scan
        setups = [s for s in (scan(db).get("act_now") or [])
                  if s.get("confidence", 0) >= MIN_CONF and s.get("price")]
        held = {r[0] for r in db.execute(text(
            "SELECT ticker FROM auto_trades WHERE status='OPEN'")).fetchall()}
        setups = [s for s in setups if s["code"] not in held]
        for s in setups[:max_n]:
            # inverse_down: stock verdicts don't judge ETFs. quick_bounce: a 20-min
            # counter-trend scalp isn't judged by the daily SELL verdict (it fires
            # DURING falls by design; it has its own ML/news/panic/volume vetoes).
            if s.get("setup_type") not in ("inverse_down", "quick_bounce"):
                try:
                    from services.decision_agent import decide_cached
                    if (decide_cached(db, s["code"]) or {}).get("decision") == "SELL":
                        continue                  # engine veto — never show it
                except Exception:
                    db.rollback()
            out["candidates"].append(s)
    except Exception:
        db.rollback()
    # measured 1-hour accuracy (same number the position forecasts show) — the
    # semi-automatic decision card displays it next to the predicted interval
    try:
        from services.method_weights import intraday_stats
        _ml = (intraday_stats(db) or {}).get("ml") or {}
        if _ml.get("n"):
            out["hour_acc"] = {"acc": _ml["acc"], "n": _ml["n"]}
    except Exception:
        db.rollback()
    # why auto might still sit out (candidates stay visible for manual trading)
    try:
        n_open = int(db.execute(text(
            "SELECT count(*) FROM auto_trades WHERE status='OPEN'")).scalar() or 0)
        n_today = int(db.execute(text(
            "SELECT count(*) FROM auto_trades WHERE opened_at::date = "
            "(now() AT TIME ZONE 'Asia/Seoul')::date")).scalar() or 0)
        day_net = float(db.execute(text(
            "SELECT coalesce(sum(net_pct),0) FROM auto_trades WHERE status='CLOSED' "
            "AND closed_at::date=(now() AT TIME ZONE 'Asia/Seoul')::date")).scalar() or 0)
        if not is_enabled(db):
            out["auto_note"] = "auto-trading is OFF"
        elif day_net <= CIRCUIT_DAY_NET:
            out["auto_note"] = f"circuit-breaker ({day_net:+.1f}%)"
        elif n_open >= MAX_OPEN:
            out["auto_note"] = f"max open ({MAX_OPEN})"
        elif n_today >= MAX_TRADES_DAY_CHEAP:
            out["auto_note"] = f"daily cap ({MAX_TRADES_DAY_CHEAP})"
    except Exception:
        db.rollback()
    return out


from services.position_guard import GUARD_CODES as FOCUS_CODES   # the boss's 20 companies

_focus_cache: dict[str, Any] = {"t": 0.0, "v": None}


def focus_status(db, codes: Optional[list[str]] = None) -> dict[str, Any]:
    """The SEMI-AUTO focus board (boss 2026-07-09): the live 1-hour state of his two
    test stocks — ALWAYS answers, signal or not: ACT_NOW plan (veto-checked), FORMING
    with its trigger, or NOTHING with the honest reason. The auto-trader keeps its own
    full-market universe in the background (self-improvement data)."""
    import time as _time
    if codes is None and _focus_cache["v"] is not None and _time.time() - _focus_cache["t"] < 50:
        return _focus_cache["v"]
    from services import prediction_service as ps
    out: dict[str, Any] = {"stocks": [], "hour_acc": None}
    for code in (codes or FOCUS_CODES):
        code = str(code).zfill(6)
        name = ps.NAMES.get(code, code)
        if name == code:                # outside the collected ~40 → full KRX list
            try:
                _r = db.execute(text(
                    "SELECT name FROM krx_stocks WHERE code=:c"), {"c": code}).first()
                if _r:
                    name = _r[0]
            except Exception:
                db.rollback()
        s: dict[str, Any] = {}
        try:
            from services.intraday_setup import setup_for
            s = setup_for(db, code, name) or {}
        except Exception:
            db.rollback()
            s = {"state": "ERROR", "reason_ko": "데이터 오류", "reason_en": "data error"}
        qualified = bool(s.get("state") == "ACT_NOW" and (s.get("confidence") or 0) >= MIN_CONF)
        # boss 2026-07-10: NO new recommendations after 15:20 — the market closes 15:30
        # and a fresh 60-minute idea can't finish its life. (Entries were already
        # gated in tick()/buy_candidates; this covers the panels too.)
        if qualified and not _market_open_now():
            qualified = False
            s["reason_ko"] = "15:20 이후 신규 추천 중단 — 장 마감(15:30) 임박, 새 1시간 아이디어가 끝까지 살 수 없습니다"
            s["reason_en"] = "no new recommendations after 15:20 — the market closes at 15:30; a fresh 1-hour idea can't finish its life"
        if qualified:
            _brk = _day_brake(db)
            if _brk:
                qualified = False
                s["reason_ko"] = "🛑 " + _brk
                s["reason_en"] = ("🛑 today's realized loss hit the daily brake "
                                  f"(-{DAY_LOSS_BRAKE_PCT:g}% of equity) — all buy signals halted until tomorrow")
        vetoed = False
        # holding? fetched first — a held stock always earns the full opinion.
        # The guard now protects EVERY held stock (boss 2026-07-13), so any panel
        # with a holding shows its real protection lines.
        guard0: Optional[dict[str, Any]] = None
        try:
            from services.position_guard import info as _ginfo0
            guard0 = _ginfo0(db, code)
        except Exception:
            db.rollback()
        # FULL ENGINE OPINION — for ACTIVE panels (signal / forming / held) AND the two
        # mains, ALWAYS (boss 2026-07-13: "why is it not increasing?" needs the real
        # answer even when quiet). Other quiet panels use the on-demand 📖 button.
        need_opinion = (s.get("state") in ("ACT_NOW", "FORMING") or guard0 is not None
                        or code in ("000660", "005930"))
        opinion: Optional[dict[str, Any]] = None
        try:
            from services.decision_agent import decide_cached
            _d = (decide_cached(db, code, ttl=180) or {}) if need_opinion else {}
            if (qualified and _d.get("decision") == "SELL"
                    and s.get("setup_type") not in ("inverse_down", "quick_bounce")):
                vetoed = True   # ⚡ 20-min scalps aren't judged by the daily verdict
            _m1 = _d.get("method1_ml") or {}
            _m2 = _d.get("method2_analysis") or {}
            _m3 = _d.get("method3_wave") or {}
            _news = _d.get("news") or {}
            _micro = _d.get("micro_trend") or {}
            if need_opinion:
                opinion = {
                    "decision": _d.get("decision"), "score": _d.get("score"),
                    "confidence": _d.get("confidence"),
                    "ml": _m1.get("call") or None, "ml_acc": _m1.get("accuracy_pct"),
                    "analysis": _m2.get("signal") or None,
                    "wave": _m3.get("verdict") or None,
                    "news_score": _news.get("score"),
                    "micro_ko": (_micro or {}).get("line_ko"),
                    "micro_en": (_micro or {}).get("line_en"),
                }
        except Exception:
            db.rollback()
        # 🛡️ his own holding on this stock: guard lines + the "don't sell" advice
        guard: Optional[dict[str, Any]] = None
        try:
            from services.position_guard import TRAIL_PCT
            guard = guard0
            if guard and s.get("price") and guard.get("avg"):
                _pnl = (float(s["price"]) / guard["avg"] - 1) * 100
                guard["pnl_pct"] = round(_pnl, 2)
                if _pnl >= 1.0:
                    # engine still bullish? → say "don't sell" with the reasons
                    _bull: list[str] = []
                    _bull_en: list[str] = []
                    if (s.get("ai_1h_prob") or 0) >= 55:
                        _bull.append(f"AI 상승확률 {s['ai_1h_prob']}%")
                        _bull_en.append(f"AI up-prob {s['ai_1h_prob']}%")
                    _mk = (opinion or {}).get("micro_ko") or ""
                    if "UP" in ((opinion or {}).get("micro_en") or "") or "상승" in _mk:
                        _bull.append("5분 흐름 상승 중")
                        _bull_en.append("5-min flow rising")
                    if (opinion or {}).get("decision") == "BUY":
                        _bull.append("종합 엔진 매수 의견")
                        _bull_en.append("fused engine says BUY")
                    if s.get("state") == "ACT_NOW":
                        _bull.append("1시간 셋업 유효")
                        _bull_en.append("1-hour setup still live")
                    if _bull:
                        guard["advice_ko"] = ("💬 지금 팔지 마세요 — 더 오를 가능성이 남아 있습니다 ("
                                              + " · ".join(_bull) + "). 내려가기 시작하면 제가 고점 대비 "
                                              f"-{TRAIL_PCT:.0f}%에서 자동으로 팔아 이익을 지킵니다.")
                        guard["advice_en"] = ("💬 Don't sell yet — more upside looks likely ("
                                              + " · ".join(_bull_en) + "). If it turns down, I auto-sell "
                                              f"at {TRAIL_PCT:.0f}% below the peak to protect the profit.")
                    else:
                        guard["advice_ko"] = ("💬 상승 근거가 약해졌습니다 — 직접 파셔도 되고, 두면 고점 대비 "
                                              f"-{TRAIL_PCT:.0f}%에서 자동 매도로 이익을 지킵니다.")
                        guard["advice_en"] = ("💬 The rise is losing backing — sell if you like, or the "
                                              f"guard auto-sells {TRAIL_PCT:.0f}% off the peak to keep the profit.")
        except Exception:
            db.rollback()
        # engine-predicted size for a live signal (smart_size: risk core + conviction)
        size_sug = None
        if qualified and not vetoed and s.get("price"):
            try:
                from services.smart_size import suggest as _sz
                size_sug = _sz(db, s["price"], s.get("stop_pct"), conf=s.get("confidence"),
                               ai_prob=s.get("ai_1h_prob"),
                               pattern_up=(s.get("pattern") or {}).get("up_rate"))
            except Exception:
                db.rollback()
        out["stocks"].append({
            "code": code, "name": name,
            **{k: s.get(k) for k in (
                "state", "price", "confidence", "ai_1h_prob", "entry_zone", "target_band",
                "target_pct", "stop", "stop_pct", "time_min", "why_ko", "why_en",
                "reason_ko", "reason_en", "trigger_ko", "trigger_en",
                "path_ko", "path_en", "pattern", "setup_type")},
            "qualified": qualified and not vetoed, "vetoed": vetoed,
            "opinion": opinion, "guard": guard, "size": size_sug})

    # 🔥 DYNAMIC SIGNALS FROM THE WHOLE KOREAN MARKET (boss 2026-07-10): any other
    # stock (네이버, LG, 한화오션, movers…) with a machine-grade buy signal appears
    # UNDER the two fixed panels — same full treatment. buy_candidates already ran
    # every gate incl. the decision-engine veto (decide_cached is warm from it).
    try:
        fixed = set(codes or FOCUS_CODES)
        cand = buy_candidates(db, max_n=4)
        for s in (cand.get("candidates") or []):
            if s.get("code") in fixed:
                continue
            opinion2: Optional[dict[str, Any]] = None
            try:
                from services.decision_agent import decide_cached
                _d = decide_cached(db, s["code"], ttl=180) or {}
                _m1 = _d.get("method1_ml") or {}
                _m2 = _d.get("method2_analysis") or {}
                _m3 = _d.get("method3_wave") or {}
                opinion2 = {
                    "decision": _d.get("decision"), "score": _d.get("score"),
                    "confidence": _d.get("confidence"),
                    "ml": _m1.get("call") or None, "ml_acc": _m1.get("accuracy_pct"),
                    "analysis": _m2.get("signal") or None,
                    "wave": _m3.get("verdict") or None,
                    "news_score": (_d.get("news") or {}).get("score"),
                    "micro_ko": (_d.get("micro_trend") or {}).get("line_ko"),
                    "micro_en": (_d.get("micro_trend") or {}).get("line_en"),
                }
            except Exception:
                db.rollback()
            size2 = None
            try:
                from services.smart_size import suggest as _sz2
                size2 = _sz2(db, s.get("price"), s.get("stop_pct"), conf=s.get("confidence"),
                             ai_prob=s.get("ai_1h_prob"),
                             pattern_up=(s.get("pattern") or {}).get("up_rate"))
            except Exception:
                db.rollback()
            out["stocks"].append({
                "code": s.get("code"), "name": s.get("name"), "dynamic": True,
                **{k: s.get(k) for k in (
                    "state", "price", "confidence", "ai_1h_prob", "entry_zone",
                    "target_band", "target_pct", "stop", "stop_pct", "time_min",
                    "why_ko", "why_en", "setup_type", "pattern")},
                "qualified": True, "vetoed": False,
                "opinion": opinion2, "guard": None, "size": size2})
    except Exception:
        db.rollback()

    try:
        from services.method_weights import intraday_stats
        _ml = (intraday_stats(db) or {}).get("ml") or {}
        if _ml.get("n"):
            out["hour_acc"] = {"acc": _ml["acc"], "n": _ml["n"]}
    except Exception:
        db.rollback()
    if codes is None:
        _focus_cache["t"], _focus_cache["v"] = _time.time(), out
    return out


def status(db) -> dict[str, Any]:
    """The auto-agent's own scorecard + open positions (for the Testing page)."""
    _ensure(db)
    open_rows = [dict(r._mapping) for r in db.execute(text(
        "SELECT ticker, name, qty, entry, target_lo, stop, time_min, confidence, opened_at "
        "FROM auto_trades WHERE status='OPEN' ORDER BY opened_at DESC"))]
    closed = db.execute(text(
        "SELECT count(*), count(*) FILTER (WHERE net_pct > 0), "
        "round(sum(net_pct)::numeric, 2), round(avg(net_pct)::numeric, 3) "
        "FROM auto_trades WHERE status='CLOSED'")).first()
    n, wins, tot, avg = (closed or (0, 0, None, None))
    recent = [dict(r._mapping) for r in db.execute(text(
        "SELECT name, exit_reason, net_pct, closed_at FROM auto_trades "
        "WHERE status='CLOSED' ORDER BY closed_at DESC LIMIT 10"))]
    vetoes_today = 0
    veto_recent: list = []
    try:
        vetoes_today = int(db.execute(text(
            "SELECT count(*) FROM auto_vetoes "
            "WHERE ts::date=(now() AT TIME ZONE 'Asia/Seoul')::date")).scalar() or 0)
        veto_recent = [dict(r._mapping) for r in db.execute(text(
            "SELECT name, reason, ts FROM auto_vetoes ORDER BY ts DESC LIMIT 5"))]
    except Exception:
        db.rollback()
    return {"enabled": is_enabled(db), "open": open_rows,
            "vetoes": {"today": vetoes_today, "recent": veto_recent},
            "record": {"trades": int(n or 0), "wins": int(wins or 0),
                       "win_rate": round(int(wins or 0) / int(n) * 100, 1) if n else None,
                       "total_net_pct": float(tot) if tot is not None else 0.0,
                       "avg_net_pct": float(avg) if avg is not None else None},
            "recent": recent,
            "limits": {"pos_pct": AUTO_POS_PCT, "max_open": MAX_OPEN,
                       "max_trades_day": MAX_TRADES_DAY, "min_conf": MIN_CONF,
                       "max_trades_day_cheap": MAX_TRADES_DAY_CHEAP, "cheap_px": CHEAP_PX}}
