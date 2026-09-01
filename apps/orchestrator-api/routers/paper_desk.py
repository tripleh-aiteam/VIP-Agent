"""/paper-desk — the boss's manual fake-money Testing dashboard (VIP menu).

Human-in-the-loop test of the chatbot + decision engine: virtual cash, live Kiwoom
prices, market + limit orders on ANY code, positions, realized/unrealized P&L.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.base import get_db

router = APIRouter(prefix="/paper-desk", tags=["paper-desk"])


class OrderBody(BaseModel):
    ticker: str = Field(..., description="6-digit code OR a stock name (resolved)")
    side: str = Field(..., description="BUY or SELL")
    qty: int = Field(..., gt=0)
    order_type: str = Field("market", description="market | limit")
    limit_price: Optional[float] = Field(None, description="trigger price for limit orders")
    source: Optional[str] = Field(None, description="which page placed it: algo1 / manual …")
    ref_price: Optional[float] = Field(None, description="the on-screen live price at click "
                                       "time — a MARKET order fills at THIS when within ±3% of "
                                       "the server price (WYSIWYG: realized % matches the card)")


def _resolve(ticker: str, db: Optional[Session] = None) -> str:
    t = (ticker or "").strip()
    if t.isdigit():
        return t.zfill(6)
    # dropdown format "이름 (005930)" → take the code
    import re
    m = re.search(r"\((\d{6})\)\s*$", t)
    if m:
        return m.group(1)
    # exact KRX name match (covers all 2,873 listed stocks)
    if db is not None:
        try:
            from sqlalchemy import text
            r = db.execute(text(
                "SELECT code FROM krx_stocks WHERE name=:n LIMIT 1"), {"n": t}).first()
            if r:
                return str(r[0])
        except Exception:
            db.rollback()
    try:
        from services.stock_resolver import resolve_one
        code, _name = resolve_one(t)
        if code:
            return code
    except Exception:
        pass
    return t


_stocks_cache: dict = {"t": 0.0, "v": None}


@router.get("/stocks")
def desk_stocks(db: Session = Depends(get_db)):
    """ALL KRX stocks (code+name+market) for the order-box dropdown — loaded from the
    krx_stocks table (PC refreshes it via FinanceDataReader). Cached 1h in-process."""
    import time as _t
    if _stocks_cache["v"] is not None and _t.time() - _stocks_cache["t"] < 3600:
        return _stocks_cache["v"]
    from sqlalchemy import text
    try:
        rows = db.execute(text(
            "SELECT code, name, market FROM krx_stocks ORDER BY name")).fetchall()
        out = {"stocks": [{"code": r[0], "name": r[1], "market": r[2]} for r in rows]}
    except Exception:
        db.rollback()
        out = {"stocks": []}
    if out["stocks"]:
        _stocks_cache["t"], _stocks_cache["v"] = _t.time(), out
    return out


@router.get("/state")
def desk_state(db: Session = Depends(get_db)):
    """Cash, equity, positions (live-marked), open orders, history, win record.
    Polling this ALSO fills any triggered limit orders."""
    from services.paper_desk import state
    return state(db)


@router.get("/roundtrips")
def desk_roundtrips(source: str = Query("algo1"), limit: int = Query(150),
                    db: Session = Depends(get_db)):
    """🤖 Round trips (bought→sold pairs) for one actor — the boss's Algorithm 1
    activity table (2026-07-16: 'make like this table in Algorithm 1'). Each
    FILLED SELL with realized P&L is paired with the latest preceding FILLED BUY
    of the same ticker (entry price/time shown = that buy; P&L stays the desk's
    avg-cost number, net of 0.23% fees)."""
    from sqlalchemy import text
    src = source if source in ("manual", "algo1", "algo2", "algo3", "algo4", "guard") else "algo1"
    rows = db.execute(text(
        "SELECT s.name, s.qty, COALESCE(b.fill_price, s.fill_price) AS entry, "
        "       s.fill_price AS exit_price, s.realized_pnl, s.realized_pnl_pct, "
        "       s.filled_at AS closed_at, b.filled_at AS opened_at, s.note, s.ticker "
        "FROM paper_desk_orders s "
        "LEFT JOIN LATERAL ("
        "  SELECT fill_price, filled_at FROM paper_desk_orders b "
        "  WHERE b.ticker = s.ticker AND b.side = 'BUY' AND b.status = 'FILLED' "
        "    AND b.filled_at <= s.filled_at "
        "  ORDER BY b.filled_at DESC LIMIT 1) b ON true "
        "WHERE s.side = 'SELL' AND s.status = 'FILLED' AND s.realized_pnl IS NOT NULL "
        "  AND COALESCE(s.source, 'manual') = :src "
        "ORDER BY s.filled_at DESC LIMIT :lim"),
        {"src": src, "lim": max(1, min(int(limit), 500))}).fetchall()
    return {"ok": True, "source": src, "trips": [
        {"name": r[0], "qty": int(r[1] or 0), "entry": (float(r[2]) if r[2] is not None else None),
         "exit_price": (float(r[3]) if r[3] is not None else None),
         "won": (float(r[4]) if r[4] is not None else None),
         "net_pct": (float(r[5]) if r[5] is not None else None),
         "closed_at": (str(r[6]) if r[6] else None), "opened_at": (str(r[7]) if r[7] else None),
         "why": r[8], "ticker": r[9]} for r in rows]}


@router.get("/algo-compare")
def desk_algo_compare(db: Session = Depends(get_db)):
    """📊 Today's Algorithm 1 vs Algorithm 2 scoreboard (boss 2026-07-16:
    'in the both side we can compare') — trips, wins, win %, net ₩ per actor,
    from the same fee-net realized numbers the trade history shows."""
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT COALESCE(source,'manual') AS src, count(*), "
        "       sum(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), "
        "       COALESCE(sum(realized_pnl), 0) "
        "FROM paper_desk_orders "
        "WHERE side='SELL' AND status='FILLED' AND realized_pnl IS NOT NULL "
        "  AND filled_at::date = (now() AT TIME ZONE 'Asia/Seoul')::date "
        "GROUP BY 1")).fetchall()
    out = {}
    for src, n, w, net in rows:
        out[src] = {"trips": int(n or 0), "wins": int(w or 0),
                    "win_rate": (round(int(w or 0) / int(n) * 100) if n else None),
                    "net_won": round(float(net or 0)), "holding": 0}
    # open positions per algo (so a HOLDING engine doesn't look idle) — algo2/algo3
    # keep their own tables; algo1 opens on the shared desk with source='algo1'.
    for src, tbl in (("algo2", "scalp_trades"), ("algo3", "candle_trades"),
                     ("algo4", "cross_trades")):
        try:
            h = db.execute(text(f"SELECT count(*) FROM {tbl} WHERE status='OPEN'")).scalar()
            out.setdefault(src, {"trips": 0, "wins": 0, "win_rate": None, "net_won": 0})["holding"] = int(h or 0)
        except Exception:
            db.rollback()
    try:
        h1 = db.execute(text(
            "SELECT count(DISTINCT ticker) FROM paper_desk_orders WHERE source='algo1' "
            "AND side='BUY' AND status='FILLED' AND ticker IN (SELECT ticker FROM paper_desk_positions WHERE qty>0)")).scalar()
        out.setdefault("algo1", {"trips": 0, "wins": 0, "win_rate": None, "net_won": 0})["holding"] = int(h1 or 0)
    except Exception:
        db.rollback()
    return {"ok": True, "today": out}


# ── Multi-day Algorithm Scoreboard (boss 2026-07-20: "which one is better before
#    I use real money?"). Aggregates fee-net realized_pnl per algorithm across the
#    last N trading days and returns the metrics that actually matter for real
#    money — cumulative net ₩, net per trade, win %, worst single day (drawdown),
#    days traded, sample size — plus a go/no-go verdict on the CAREFUL gate:
#    an algo is 'READY' only if net ₩ > 0 AND days ≥ 5 AND completed trips ≥ 30.
_GATE_DAYS = 5
_GATE_TRIPS = 30
_ALGO_LABEL = {"algo1": "Algorithm 1", "algo2": "Algorithm 2 · Ripple",
               "algo3": "Algorithm 3 · Candle", "algo4": "Cross-Check · 3-agree"}


def _scoreboard(db, days: int = 15) -> dict:
    from sqlalchemy import text
    # one row per (algo, KST trade-day): trips, wins, net ₩ — fee-net realized_pnl
    rows = db.execute(text(
        "SELECT COALESCE(source,'manual') AS src, "
        "       (filled_at AT TIME ZONE 'Asia/Seoul')::date AS d, "
        "       count(*), sum(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), "
        "       COALESCE(sum(realized_pnl),0), COALESCE(sum(realized_pnl_pct),0) "
        "FROM paper_desk_orders "
        "WHERE side='SELL' AND status='FILLED' AND realized_pnl IS NOT NULL "
        "  AND COALESCE(source,'manual') IN ('algo1','algo2','algo3','algo4') "
        "  AND filled_at >= (now() AT TIME ZONE 'Asia/Seoul')::date - :d "
        "GROUP BY 1,2 ORDER BY 1,2"),
        {"d": int(days)}).fetchall()
    agg: dict = {}
    for src, d, n, w, net, netpct in rows:
        a = agg.setdefault(src, {"trips": 0, "wins": 0, "net_won": 0.0,
                                 "sum_pct": 0.0, "days": {}, "worst_day": None,
                                 "best_day": None})
        n, w, net, netpct = int(n or 0), int(w or 0), float(net or 0), float(netpct or 0)
        a["trips"] += n; a["wins"] += w; a["net_won"] += net; a["sum_pct"] += netpct
        a["days"][str(d)] = round(net)
        if a["worst_day"] is None or net < a["worst_day"][1]:
            a["worst_day"] = [str(d), round(net)]
        if a["best_day"] is None or net > a["best_day"][1]:
            a["best_day"] = [str(d), round(net)]
    out = {}
    for src in ("algo1", "algo2", "algo3", "algo4"):
        a = agg.get(src)
        if not a:
            out[src] = {"label": _ALGO_LABEL[src], "trips": 0, "days": 0,
                        "net_won": 0, "verdict": "NO DATA",
                        "reason": "no completed trades yet"}
            continue
        trips, days_n, net = a["trips"], len(a["days"]), a["net_won"]
        win_rate = round(a["wins"] / trips * 100) if trips else None
        per_trade = round(net / trips) if trips else None
        avg_pct = round(a["sum_pct"] / trips, 3) if trips else None  # net %/trade after fees
        # CAREFUL gate verdict
        if net > 0 and days_n >= _GATE_DAYS and trips >= _GATE_TRIPS:
            verdict, reason = "✅ READY", f"profitable over {days_n} days / {trips} trades"
        elif net <= 0 and days_n >= _GATE_DAYS and trips >= _GATE_TRIPS:
            verdict, reason = "❌ REJECT", f"enough data but NOT profitable (net {round(net):,}₩)"
        else:
            need = []
            if days_n < _GATE_DAYS:
                need.append(f"{_GATE_DAYS - days_n} more day(s)")
            if trips < _GATE_TRIPS:
                need.append(f"{_GATE_TRIPS - trips} more trade(s)")
            lean = "leaning profit" if net > 0 else "currently down"
            verdict = "⏳ NOT ENOUGH DATA"
            reason = f"{lean} (net {round(net):,}₩) — need {', '.join(need) or 'more data'}"
        out[src] = {"label": _ALGO_LABEL[src], "trips": trips, "days": days_n,
                    "win_rate": win_rate, "net_won": round(net),
                    "net_per_trade": per_trade, "net_pct_per_trade": avg_pct,
                    "worst_day": a["worst_day"], "best_day": a["best_day"],
                    "verdict": verdict, "reason": reason}
    # overall recommendation: only among READY algos, the highest net ₩ wins
    ready = {s: v for s, v in out.items() if v["verdict"] == "✅ READY"}
    if ready:
        best = max(ready, key=lambda s: ready[s]["net_won"])
        rec = {"algo": best, "label": out[best]["label"], "status": "GO",
               "text": f"{out[best]['label']} is the only proven winner"
                       if len(ready) == 1 else
                       f"{out[best]['label']} leads the proven set (net {out[best]['net_won']:,}₩)"}
    else:
        rec = {"algo": None, "status": "WAIT",
               "text": "No algorithm has passed the safety gate yet — keep paper-testing, "
                       "do NOT use real money."}
    return {"gate": {"days": _GATE_DAYS, "trips": _GATE_TRIPS, "window_days": days},
            "algos": out, "recommendation": rec}


@router.get("/scoreboard")
def desk_scoreboard(days: int = Query(15, ge=1, le=60),
                    db: Session = Depends(get_db)):
    """📊🏁 Multi-day, fee-honest verdict board for the 3 algorithms — the real-
    money decision. Careful gate: net ₩>0 AND ≥5 days AND ≥30 trips → READY."""
    try:
        return {"ok": True, **_scoreboard(db, days)}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)[:200]}


@router.get("/quote")
def desk_quote(q: str = Query(...), db: Session = Depends(get_db)):
    """Full quote for the order box: 시가/현재가±%/고가/저가 (any code or name).
    Kiwoom first; Naver realtime + daily candle fill the gaps after hours."""
    from services.paper_desk import _live_price, _name_for
    code = _resolve(q, db)
    if not code.isdigit():
        return {"ok": False, "error": f"'{q}' 종목을 찾지 못했어요"}
    out: dict = {"ok": True, "ticker": code}
    kw_name = None
    try:
        from services import kiwoom_rest as kr
        kq = kr.current_price(code)
        if kq and kq.get("price"):
            kw_name = kq.get("name")
            out.update({k: kq.get(k) for k in ("price", "open", "high", "low", "change_pct")})
    except Exception:
        pass
    if not out.get("price"):
        px, _n = _live_price(code)
        if px is None:
            return {"ok": False, "error": f"{code} 시세를 가져오지 못했어요"}
        out["price"] = px
    if out.get("open") is None or out.get("change_pct") is None:
        try:
            from services.naver_stock import daily_history, realtime_quote
            nq = realtime_quote(code) or {}
            for k in ("open", "high", "low", "change_pct"):
                if out.get(k) is None:
                    out[k] = nq.get(k)
            if out.get("open") is None:
                d = (daily_history(code, days=1) or [{}])[0]
                for k in ("open", "high", "low"):
                    if out.get(k) is None:
                        out[k] = d.get(k)
        except Exception:
            pass
    out["name"] = _krx_name(db, code) or _name_for(code, kw_name)
    return out


def _krx_name(db: Session, code: str) -> Optional[str]:
    """Authoritative name for ANY listed stock from the krx_stocks table (2,873 rows) —
    watchlist NAMES only covers ~51 and Kiwoom may be IP-blocked on this instance."""
    try:
        from sqlalchemy import text
        r = db.execute(text("SELECT name FROM krx_stocks WHERE code=:c"), {"c": code}).first()
        return str(r[0]) if r else None
    except Exception:
        db.rollback()
        return None


@router.post("/order")
def desk_order(body: OrderBody, db: Session = Depends(get_db)):
    from services.paper_desk import place_order
    code = _resolve(body.ticker, db)
    if not code.isdigit():
        return {"ok": False, "error": f"'{body.ticker}' 종목을 찾지 못했어요"}
    src = body.source if body.source in ("algo1", "algo2", "algo3", "algo4", "guard", "manual") else "manual"
    return place_order(db, code, body.side, body.qty,
                       order_type=body.order_type, limit_price=body.limit_price, source=src,
                       ref_price=body.ref_price)


@router.post("/cancel/{order_id}")
def desk_cancel(order_id: int, db: Session = Depends(get_db)):
    from services.paper_desk import cancel_order
    return cancel_order(db, order_id)


@router.post("/reset")
def desk_reset(cash: float = Query(100_000_000), db: Session = Depends(get_db)):
    from services.paper_desk import reset
    return reset(db, cash=cash)


@router.post("/deposit")
def desk_deposit(amount: float = Query(100_000_000), db: Session = Depends(get_db)):
    """Add fake money to the desk (boss's 'fill money' button) — start_cash rises too
    so the P&L% stays honest."""
    from services.paper_desk import deposit
    return deposit(db, amount)


@router.get("/day-report")
def desk_day_report(db: Session = Depends(get_db)):
    """📊 Today's per-stock trading summary (KST) for the results section."""
    from services.paper_desk import day_report
    return day_report(db)


@router.get("/chart")
def desk_chart(code: str = Query(...), tf: str = Query("5m"), db: Session = Depends(get_db)):
    """Intraday candles for inline stock charts.

    The 1m timeframe returns the latest captured trading session from the true
    one-minute collector. The 5m timeframe returns the combined live and historical
    intraday series, and 1h aggregates that series by hour.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from services.cycle_scalp import _bars
    code = str(code).zfill(6)

    if tf == "1m":
        from services.minute_bars import read_bars

        raw_bars = read_bars(db, code, limit=500)
        out = []
        for bar in raw_bars:
            try:
                timestamp = datetime.fromisoformat(str(bar["ts"]))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=ZoneInfo("Asia/Seoul"))
                epoch = int(timestamp.timestamp())
                open_price = float(bar["open"])
                high_price = float(bar["high"])
                low_price = float(bar["low"])
                close_price = float(bar["close"])
            except (TypeError, ValueError):
                continue
            out.append({
                "time": epoch,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": float(bar.get("volume") or 0),
            })
        return {"code": code, "tf": tf, "bars": out}

    bars = _bars(db, code, limit=2500)

    def _ts(b) -> int:
        try:
            return int(b["ts"].timestamp())
        except Exception:
            return 0
    out: list[dict] = []
    if tf == "1h":
        agg: dict = {}
        for b in bars:
            try:
                k = b["ts"].strftime("%Y-%m-%d %H")
            except Exception:
                continue
            a = agg.get(k)
            if not a:
                agg[k] = {"time": _ts(b), "open": b["open"], "high": b["high"],
                          "low": b["low"], "close": b["close"], "volume": b["volume"]}
            else:
                a["high"] = max(a["high"], b["high"])
                a["low"] = min(a["low"], b["low"])
                a["close"] = b["close"]
                a["volume"] += b["volume"]
        out = list(agg.values())
    else:
        out = [{"time": _ts(b), "open": b["open"], "high": b["high"], "low": b["low"],
                "close": b["close"], "volume": b["volume"]} for b in bars[-500:]]
    out = [b for b in out if b["time"] > 0]
    return {"code": code, "tf": tf, "bars": out}


# ---- Phase 4: the AUTO-AGENT (auto-trades the scanner's setups on this desk) ----
@router.get("/auto/status")
def auto_status(db: Session = Depends(get_db)):
    """Auto-agent scorecard + open auto-positions + limits (Testing page panel)."""
    from services.auto_trader import status
    return status(db)


@router.post("/auto/toggle")
def auto_toggle(on: bool = Query(...), db: Session = Depends(get_db)):
    """Turn the auto-agent ON/OFF (paper money only)."""
    from services.auto_trader import set_enabled
    return set_enabled(db, on)


@router.post("/auto/tick")
def auto_tick(force: bool = Query(False), db: Session = Depends(get_db)):
    """One auto-agent pass: manage exits, then maybe open one new setup. Fired by the
    external 5-min cron during market; also safe to call ad hoc. force=testing only."""
    from services.auto_trader import tick
    return tick(db, force=force)


@router.get("/auto/candidates")
def auto_candidates(db: Session = Depends(get_db)):
    """The setups the auto-agent WOULD buy right now (same gates, no order) — powers
    the ⚡ popup alarm so a popup is always a machine-grade buy candidate."""
    from services.auto_trader import buy_candidates
    return buy_candidates(db)


@router.get("/auto/focus")
def auto_focus(extra: str = Query(""), db: Session = Depends(get_db)):
    """Semi-auto FOCUS board — the live 1-hour state of the boss's test stocks.
    `extra` = comma-separated codes the boss added via the board's search box
    (any KRX stock, computed on top of the cached 20)."""
    from services.auto_trader import focus_status
    out = focus_status(db)
    ex = [c.strip().zfill(6) for c in (extra or "").split(",") if c.strip()]
    have = {s["code"] for s in out.get("stocks", [])}
    ex = [c for c in ex if c not in have][:8]
    if ex:
        more = focus_status(db, codes=ex)
        out = {**out, "stocks": list(out.get("stocks", [])) + list(more.get("stocks", []))}
    return out


# --------------------------------------------------------------------------- #
# ⚡ ALGORITHM 2 — the boss's ripple scalper (buy the upturn, sell the small win,
# repeat; hold dips, cut at −1%). services/scalp_trader.py; 15s scheduler tick.
# --------------------------------------------------------------------------- #

@router.get("/scalp/status")
def scalp_status(db: Session = Depends(get_db)):
    """Everything the Algorithm 2 page needs: switch, dials, per-stock state
    (WAIT/LONG + entry/take/stop lines), today's record, recent round trips."""
    from services.scalp_trader import status
    return status(db)


@router.get("/scalp/candles")
def scalp_candles(code: str = Query(...)):
    """🕯️ Candle 3-2 signal for ONE stock — the manual page's guidance strip.
    Last completed 1-min candles + current up/down streak + what the boss's
    rule says to do right now (he clicks the buy/sell buttons himself)."""
    from services.scalp_trader import _candles_1m, _streaks_1m
    code = (code or "").strip().zfill(6)
    cs = _candles_1m(code, n=6)
    up, dn, n = _streaks_1m(code)
    signal = "BUY" if up >= 3 else "SELL" if dn >= 2 else "HOLD"
    return {"ok": True, "code": code, "up": up, "dn": dn, "n": n, "signal": signal,
            "candles": [{"ts": b.get("ts"), "open": b.get("open"), "close": b.get("close"),
                         "dir": ("up" if (b.get("close") or 0) > (b.get("open") or 0)
                                 else "down" if (b.get("close") or 0) < (b.get("open") or 0) else "flat")}
                        for b in cs]}


@router.post("/scalp/toggle")
def scalp_toggle(on: bool = Query(...), db: Session = Depends(get_db)):
    from services.scalp_trader import set_enabled
    return set_enabled(db, on)


@router.post("/scalp/params")
def scalp_params(take_pct: Optional[float] = Query(None), stop_pct: Optional[float] = Query(None),
                 pos_pct: Optional[float] = Query(None), codes: Optional[str] = Query(None),
                 mode: Optional[str] = Query(None), strategy: Optional[str] = Query(None),
                 db: Session = Depends(get_db)):
    """The boss's dials: small-win target %, stop %, size % of cash, stock list,
    mode ('auto'/'semi'), strategy ('ripple' = bounce+take · 'candle' = 1-min
    3-up buy / 2-down sell / −1% stop)."""
    from services.scalp_trader import set_params
    return set_params(db, take_pct=take_pct, stop_pct=stop_pct, pos_pct=pos_pct,
                      codes=codes, mode=mode, strategy=strategy)


@router.post("/scalp/buy")
def scalp_buy(code: str = Query(...), db: Session = Depends(get_db)):
    """SEMI mode: execute a machine recommendation — the BOSS's click buys it."""
    from services.scalp_trader import semi_buy
    return semi_buy(db, code)


@router.post("/scalp/sell")
def scalp_sell(code: str = Query(...), db: Session = Depends(get_db)):
    """Sell the whole position of one stock (boss's click) and close its scalp rows."""
    from services.scalp_trader import sell_all
    return sell_all(db, code)


@router.post("/scalp/adopt")
def scalp_adopt(code: str = Query(...), db: Session = Depends(get_db)):
    """⚡ 맡기기: hand a manual position to Algorithm 2 — it manages the exit
    (take / stop / EOD) from the next 15s beat."""
    from services.scalp_trader import adopt
    return adopt(db, code)


@router.post("/scalp/tick")
def scalp_tick(force: bool = Query(False), db: Session = Depends(get_db)):
    """Manual heartbeat (testing) — the scheduler fires this every 15s anyway.
    Also drives the 🏁 3-strategy tournament AND Algorithm 3 (candle) on the SAME
    reliable cron-job.org beat (APScheduler stalls when Render's free tier sleeps)."""
    from services.scalp_trader import tick
    r = tick(db, force=force)
    try:
        from services.strategy_tournament import tick as _tt
        _tt(db)
    except Exception:
        db.rollback()
    try:
        from services.candle_trader import tick as _c3
        _c3(db)
    except Exception:
        db.rollback()
    # NOTE: auto_trader.tick (Algo 1 entries) is NOT run here — it does a heavy
    # full-universe scan and the in-process ticker already runs it every ~60s.
    # Running it on every cron ping pegged the CPU (Render health-check timeout).
    return r


# --------------------------------------------------------------------------- #
# 🕯️ ALGORITHM 3 — the boss's candle trader (3 up 1-min candles → buy, 3 down →
# sell). A dedicated copy of Algorithm 2's shape; services/candle_trader.py.
# --------------------------------------------------------------------------- #

@router.get("/candle3/status")
def candle3_status(db: Session = Depends(get_db)):
    from services.candle_trader import status
    return status(db)


@router.post("/candle3/toggle")
def candle3_toggle(on: bool = Query(...), db: Session = Depends(get_db)):
    from services.candle_trader import set_enabled
    return set_enabled(db, on)


@router.post("/candle3/params")
def candle3_params(stop_pct: Optional[float] = Query(None), pos_pct: Optional[float] = Query(None),
                   codes: Optional[str] = Query(None), mode: Optional[str] = Query(None),
                   streak: Optional[int] = Query(None), tf: Optional[str] = Query(None),
                   take_pct: Optional[float] = Query(None), exit_mode: Optional[str] = Query(None),
                   entry_timing: Optional[str] = Query(None), flow_confirm: Optional[bool] = Query(None),
                   ab_test: Optional[bool] = Query(None),
                   exit_manual: Optional[bool] = Query(None),
                   db: Session = Depends(get_db)):
    """Algorithm 3 dials: stop %, size %, stock list, mode, streak (2/3), tf (1/3/5-min),
    take_pct (NET take-profit %), exit_mode ('target'=take-profit / 'candle'=3-down sell),
    entry_timing ('confirmed'=act on 3rd candle CLOSE / 'early'=act on the forming candle),
    flow_confirm (order-book 호가 layer), ab_test (shadow A/B: run both exit modes side by side),
    exit_manual (hybrid: auto entries but you sell by hand — machine buys, you take the profit)."""
    from services.candle_trader import set_params
    return set_params(db, stop_pct=stop_pct, pos_pct=pos_pct, codes=codes, mode=mode,
                      streak=streak, tf=tf, take_pct=take_pct, exit_mode=exit_mode,
                      entry_timing=entry_timing, flow_confirm=flow_confirm, ab_test=ab_test,
                      exit_manual=exit_manual)


@router.post("/candle3/ab_reset")
def candle3_ab_reset(db: Session = Depends(get_db)):
    """Clear the shadow A/B comparison to start fresh."""
    from services.candle_trader import ab_reset
    return ab_reset(db)


# ── 📡 LIVE KIWOOM DESK ────────────────────────────────────────────────────────────
# The same charts and tables as the artificial labs, on REAL executions. Separate from
# /proof/* on purpose: the artificial market must keep trading untouched (boss
# 2026-08-04), and these two must never share a code path that could couple them.
def _tape_ready():
    """Start the collector on first use. Kiwoom keeps only ~40 SECONDS of tick history,
    so the tape has to be accumulated continuously or it does not exist."""
    from services import kiwoom_tape
    kiwoom_tape.start()
    return kiwoom_tape


@router.get("/live/status")
def live_status():
    """Is the collector running, and how much tape has it gathered per stock."""
    return _tape_ready().status()


@router.get("/live/tape")
def live_tape(code: str = Query("005930"), period: int = Query(0),
              tick: int = Query(5), bars: int = Query(400)):
    """Real executions aggregated into bars — N seconds (period) or N executions (tick),
    the same two clocks the artificial labs use, so the charts are comparable."""
    kt = _tape_ready()
    ticks = kt.load(code)
    if not ticks:
        return {"ok": True, "code": code, "bars": [], "ticks": 0,
                "note": "no tape yet - the collector needs the market open"}
    cs = kt.bars_time(ticks, period) if period else kt.bars_ticks(ticks, max(1, tick))
    name = next((n for c, n in kt.WATCH if c == code), code)
    win = cs[-max(1, bars):]
    return {"ok": True, "code": code, "name": name,
            "clock": f"{period}초" if period else f"{tick}틱",
            "ticks": len(ticks), "first": ticks[0]["t"], "last": ticks[-1]["t"],
            # `off` = the absolute position of the first returned bar in the day's tape.
            # The chart addresses bars by NUMBER; without this, each poll's sliding window
            # renumbered every bar (bar #0 became a different bar every 3 seconds) and the
            # axis label cache could briefly show the previous window's times - which is
            # exactly the "15:11 between 09:11 and 09:12" the boss kept seeing. With an
            # absolute number, a bar keeps its number for ever and nothing can mix.
            "off": len(cs) - len(win), "total_bars": len(cs),
            "bars": win}


@router.get("/live/tape-hist")
def live_tape_hist(code: str = Query(...), tick: int = Query(5),
                   period: int = Query(0)):
    """ALL stored days' bars, oldest→newest, for the ONE continuous
    Kiwoom-style chart (boss 2026-08-28: "one type of chart like normal
    Kiwoom, so I can scroll and move to the past"). Today is NOT included -
    the live poll supplies it and the frontend appends; finished days come
    from the per-day bars cache, so after the first build this is cheap."""
    from services.kiwoom_rules import _bars_for, stored_days
    from services.kiwoom_tape import _day as _kd
    td = _kd()
    out: list = []
    days: list = []
    for d in stored_days(code):
        if d >= td:
            continue
        cs = _bars_for(code, tick, max(0, min(int(period or 0), 600)), d)
        if cs:
            days.append({"d8": d, "i": len(out), "n": len(cs)})
            out.extend(cs)
    return {"ok": True, "code": code, "days": days, "bars": out}


@router.get("/live/rules")
def live_rules(tick: int = Query(5), period: int = Query(0), day: str = Query(""),
               frm: str = Query(""), to: str = Query(""), gate: int = Query(1),
               auto: int = Query(1), codes: str = Query("")):
    """The same rules the Strategy Lab runs, over the REAL Kiwoom tape. No ML here — the
    boss asked for the plain rules on real data first, which is the right order."""
    from services.kiwoom_rules import rank
    # gate=0 shows what the rules WOULD have done on a day the gate closed - a viewing
    # switch only; the desk itself always trades gated (boss 2026-08-10)
    # auto=0: the user explicitly chose TODAY, so an empty board is the honest answer -
    # never yesterday's trades under today's label (boss 2026-08-11, three times)
    _p = max(0, min(int(period or 0), 600))
    # the cache key names the ACTUAL day: a live (day="") table computed
    # yesterday must be unreachable today (boss 2026-08-20 09:0x: "Algo 1
    # still shows yesterday's result")
    from services.kiwoom_tape import _day as _kd9
    # the key normalizes the codes list (sorted) so the warm can prefill it
    # server-side without knowing the page's join order, and VITAL so the
    # page's backbone can't be starved behind family refreshers (2026-08-25:
    # the reco menu's whole section hid behind this key computing)
    _nc = ",".join(sorted(c for c in (codes or "").split(",") if c))
    return _swr(("rank", tick, _p, day or _kd9(), frm, to, bool(gate),
                 bool(auto), _nc), 3.0,
                lambda: rank(tick=tick, period=_p, day=day, frm=frm, to=to,
                             use_gate=bool(gate), allow_fallback=bool(auto), codes=codes),
                placeholder={"ok": False, "computing": True}, vital=True)


@router.get("/live/rules/trades")
def live_rule_trades(variant: str = Query(...), tick: int = Query(5),
                     period: int = Query(0), code: str = Query(""),
                     bars: int = Query(2500), limit: int = Query(300),
                     around: int = Query(-1), budget: int = Query(0),
                     day: str = Query(""), frm: str = Query(""), to: str = Query(""),
                     gate: int = Query(1), auto: int = Query(1), codes: str = Query("")):
    """One rule's real trades: what it bought, at what, when, and why.

    `budget` is won per trade — 0 means the historical one share. It scales the money and
    never the win rate, which is the point of being able to set it (boss 2026-08-04)."""
    from services.kiwoom_rules import trades
    _p = max(0, min(int(period or 0), 600))
    _b = max(0, min(int(budget or 0), 1_000_000_000))
    from services.kiwoom_tape import _day as _kd9
    return _swr(("trades", variant, tick, _p, code, bars, limit, around, _b,
                 day or _kd9(), frm, to, bool(gate), bool(auto), codes), 10.0,
                lambda: trades(variant, tick=tick, period=_p, code=code,
                               bars=bars, limit=limit, around=around, budget=_b,
                               day=day, frm=frm, to=to, use_gate=bool(gate),
                               allow_fallback=bool(auto), codes=codes),
                placeholder={"ok": False, "computing": True}, vital=True)


_FAM_TTL: dict = {}

# SERVE STALE, REFRESH BEHIND (boss 2026-08-19: "when I reload it is opening
# trading history and other things inside algorithms very slow"). A reload used
# to wait for a full-day replay of six tapes before showing anything. Now every
# heavy endpoint answers with the last computed table INSTANTLY and recomputes
# in a background thread; the page's own 3-20s polls pick up the fresh answer
# one beat later. Only the very first request after a restart still computes
# inline - and /live/warm covers that.
_SWR: dict = {}
# key -> epoch the refresh started. A refresh thread that stalls (observed
# 2026-08-19 ~12:1x-13:5x: the 알고리즘2 board served a 12:1x snapshot for
# ~100 minutes while its refresher hung and the busy flag blocked every new
# attempt) may hold a key for at most 300s - after that a fresh thread may
# take over, so a stall heals itself instead of freezing the board.
_SWR_BUSY: dict = {}


def _swr(key, fresh_sec: float, compute, placeholder=None, vital: bool = False):
    """placeholder (boss 2026-08-19: 'it is not showing trading history... I do
    not want more like this case'): a COLD key - an hour window, a stored day,
    the first poll after a restart - used to compute a full-day replay inline
    while the page's fetch timed out and the board read as gone. With a
    placeholder, a cold key answers {computing: true} in milliseconds, the
    replay runs in a background thread, and the page retries until the real
    table lands. A failed background compute keeps the last good answer."""
    import threading
    import time as _t
    hit = _SWR.get(key)
    if hit and _t.time() - hit[0] < fresh_sec:
        return hit[1]

    def _spawn():
        st = _SWR_BUSY.get(key)
        if st is not None and _t.time() - st < 300.0:
            return                       # a live refresh is already on it
        # at most 3 replays in flight (2026-08-19 evening: a fresh boot, the
        # page's cold-key polls and /live/warm together spawned 6-8 full-day
        # replays at once and the process died without a word - the OS kills
        # an out-of-memory python silently). A skipped spawn simply retries
        # on the page's next poll; stale answers keep serving meanwhile.
        # VITAL keys (a user's click waiting for a chart) skip the cap: the
        # board's endless rank/history refreshers were hogging all 3 slots and
        # a cross-company click starved forever (boss 2026-08-20: "if I am in
        # SK I cannot move to Samsung")
        # 2026-08-25: the desk grew to 9 stocks (desk_mode extras) and the
        # process died three times before 10:00 - each replay is ~50% heavier
        # now. Background cap drops to 2, and the vital lane (user clicks) is
        # BOUNDED at 5 instead of unlimited: a click still jumps the queue,
        # but clicks can no longer stampede memory alongside the refreshers.
        if not vital and len(_SWR_BUSY) >= 2:
            return
        if vital and len(_SWR_BUSY) >= 5:
            return
        _SWR_BUSY[key] = _t.time()

        def _go():
            try:
                _SWR[key] = (_t.time(), compute())
            except Exception:
                pass
            finally:
                _SWR_BUSY.pop(key, None)
        threading.Thread(target=_go, daemon=True).start()
    if hit:
        _spawn()
        return hit[1]
    if placeholder is not None:
        _spawn()
        return placeholder
    v = compute()
    _SWR[key] = (_t.time(), v)
    return v


@router.get("/live/rules/family-trades")
def live_family_trades(family: str = Query("new"), tick: int = Query(5),
                       period: int = Query(0), day: str = Query(""),
                       frm: str = Query(""), to: str = Query(""),
                       gate: int = Query(1), auto: int = Query(1),
                       codes: str = Query("")):
    """EVERY trade of one family in one table (boss 2026-08-11: rule, stock, buy, sell,
    result, money - across the whole family, not one rule at a time). Rows carry the
    rule id and the trade's index inside that rule's own list, so the page can open the
    exact trade on the chart as proof with the machinery it already has."""
    # stale-serve: the last table answers instantly, a background thread recomputes
    # (the 20s hard cache alone still made every reload wait out a full-day replay)
    from services.kiwoom_tape import _day as _kd9
    # NO MIXED DESKS (boss 2026-08-25: "menu 1 trades ONLY the six, menu 2
    # ONLY the recommended - no mixed"): the history is desk-scoped by codes,
    # normalized (sorted) in the key so the warm can prefill both desks.
    _nc = ",".join(sorted(c for c in (codes or "").split(",") if c))
    if not _nc:
        # an empty codes list (a page mid-load) must NEVER serve the mixed
        # pot - it defaults to the boss's six (menu 1 semantics)
        codes = "000660,005930,017670,034020,035420,042660"
        _nc = codes
    res = _swr(("fam", family, tick, period, day or _kd9(), frm, to, gate,
                auto, _nc), 20.0,
                lambda: _fam_compute(family, tick, period, day, frm, to, gate,
                                     auto, codes),
                placeholder={"ok": False, "computing": True})
    # 💬 chatbot trades live IN the same history as the algos' (boss 2026-08-26:
    # "show trading history together with auto and with chatbot buyers/sellers") —
    # completed chat trips join the rows, open chat positions join the holding
    # block, all in the exact same row shape the table already renders.
    try:
        if res.get("ok"):
            # DAY-SCOPED like the algo replay (boss 2026-08-26: "even I choose today
            # it is showing old days result") — only the selected day's chat orders
            _crows, _chold = _chat_fam_entries(set(codes.split(",")), day or _kd9())
            if _crows or _chold:
                res = {**res, "rows": _crows + list(res.get("rows") or []),
                       "holding": list(res.get("holding") or []) + _chold}
    except Exception as _ce:
        from services.logger import log as _lg
        _lg.warning(f"chat fam merge failed: {str(_ce)[:120]}")
    # 🗑 chat-erased trips vanish from the board (boss 2026-09-01: "remove SK하이닉스
    # which bought at 10:17") — display filter only, records stay; "복원해줘" undoes
    try:
        if res.get("ok") and res.get("rows"):
            from services.trip_eraser import filter_rows as _tef
            res = {**res, "rows": _tef(list(res["rows"]), day or _kd9())}
    except Exception:
        pass
    return res


def _chat_fam_entries(code_set: set, day8: str = ""):
    """💬 chatbot orders reshaped into the SAME rows/holding forms the algo history
    table renders (boss 2026-08-26: "show trading history together with auto and
    with chatbot buyers/sellers") — FIFO trips per stock: sells close buys into
    completed rows, a net-long remainder becomes a holding entry with the live
    unrealized %. Menu 1 = the six's chat orders, menu 2 = every other stock's."""
    from datetime import timedelta, timezone
    from sqlalchemy import text as _sqt
    from db.base import SessionLocal
    KST = timezone(timedelta(hours=9))
    rows_out, hold_out = [], []
    db = SessionLocal()
    try:
        from services.paper_desk import _ensure
        _ensure(db)
        recs = db.execute(_sqt(
            "SELECT ticker, name, side, qty, fill_price, realized_pnl_pct, created_at "
            "FROM paper_desk_orders WHERE (COALESCE(source,'') IN ('chat','chatbot') OR COALESCE(source,'') LIKE '%-chat') "
            "AND status='FILLED' ORDER BY id")).fetchall()
        # KOSPI ONLY on the boards (boss 2026-08-26: "please remove 에코프로비엠
        # from both menus — yesterday I requested do not use Kosdaq"): KOSDAQ/ETF
        # chat trips no longer render in the trading history. The order RECORDS
        # stay untouched in the database — display filter only, never deletion.
        _tks9 = list({r[0] for r in recs})
        _mkt9: dict = {}
        if _tks9:
            try:
                for _c9, _m9 in db.execute(_sqt(
                        "SELECT code, market FROM krx_stocks WHERE code = ANY(:t)"),
                        {"t": _tks9}).fetchall():
                    _mkt9[_c9] = str(_m9 or "").upper()
            except Exception:
                _mkt9 = {}
        # MENU 1 IS THE SIX, PURE (boss 2026-08-27: "in menu 1 we have only 6
        # stock companies - keep them and remove others; menu 2 is different,
        # every day it can change, so it is OK"). Supersedes the 08-26 "on both"
        # ruling: when the desk asking is exactly the six, chat trades of any
        # OTHER company stay off that board. Menu 2 keeps showing them all.
        _SIX9 = {"000660", "005930", "017670", "034020", "035420", "042660"}
        _menu1_9 = set(code_set) == _SIX9
        by_code: dict = {}
        for r in recs:
            c = r[0]
            if _mkt9 and _mkt9.get(c, "") != "KOSPI":
                continue
            if _menu1_9 and c not in _SIX9:
                continue
            # only the SELECTED day's orders (boss 2026-08-26: old days leaked into
            # today's view and confused the room)
            if day8:
                try:
                    if r[6] is None or r[6].astimezone(KST).strftime("%Y%m%d") != day8:
                        continue
                except Exception:
                    continue
            by_code.setdefault(c, []).append(r)
        for c, rs in by_code.items():
            name = rs[-1][1] or c
            trips, cur, net = [], {"buys": [], "sells": []}, 0
            for r in rs:
                try:
                    t = r[6].astimezone(KST).strftime("%H:%M:%S") if r[6] is not None else ""
                except Exception:
                    t = ""
                if r[2] == "BUY":
                    cur["buys"].append([float(r[4] or 0), int(r[3] or 0), t])
                    net += int(r[3] or 0)
                else:
                    cur["sells"].append([float(r[4] or 0), int(r[3] or 0), t])
                    net -= int(r[3] or 0)
                if net <= 0 and cur["buys"]:
                    trips.append(cur)
                    cur, net = {"buys": [], "sells": []}, 0
            for i, tr in enumerate(trips):
                qty = sum(q for _p, q, _t in tr["buys"]) or 1
                entry = sum(p * q for p, q, _t in tr["buys"]) / qty
                sq = sum(q for _p, q, _t in tr["sells"]) or 1
                exitp = sum(p * q for p, q, _t in tr["sells"]) / sq
                net_pct = round((exitp / entry - 1) * 100 - 0.23, 2) if entry else 0.0
                left = qty
                sells7 = []
                for p, q, t in tr["sells"]:
                    left = max(0, left - q)
                    sells7.append([p, q, t, 0, left, p > entry, entry])
                rows_out.append({
                    "rule": "chatbot", "rule_ko": "💬 챗봇", "idx": i, "code": c,
                    "name": f"💬 {name}", "buy_t": tr["buys"][0][2],
                    "sell_t": tr["sells"][-1][2] if tr["sells"] else "",
                    "entry": entry, "exit": exitp, "qty": qty,
                    "result": "win" if net_pct > 0 else "loss" if net_pct < 0 else "flat",
                    "net_pct": net_pct,
                    "parts": {"buys": [[p, q, t] for p, q, t in tr["buys"]],
                              "sells": sells7}})
            if cur["buys"] and net > 0:
                qty = sum(q for _p, q, _t in cur["buys"]) or 1
                entry = sum(p * q for p, q, _t in cur["buys"]) / qty
                # the SHARED position may have been flattened by the ENGINE (15:19
                # close-out / algo sells) — a chat trip must not keep showing
                # 'holding' when the desk truly holds nothing (boss 2026-08-26:
                # 'Please sell 💬 LG전자' vs '보유 수량이 없습니다' contradiction)
                try:
                    _rq = db.execute(_sqt(
                        "SELECT qty FROM paper_desk_positions WHERE ticker=:t"),
                        {"t": c}).scalar()
                except Exception:
                    _rq = None
                if not _rq or int(_rq) <= 0:
                    _es = db.execute(_sqt(
                        "SELECT fill_price, filled_at FROM paper_desk_orders "
                        "WHERE ticker=:t AND side='SELL' AND status='FILLED' "
                        "ORDER BY id DESC LIMIT 1"), {"t": c}).fetchone()
                    if _es and _es[0]:
                        try:
                            _et = _es[1].astimezone(KST).strftime("%H:%M:%S") if _es[1] else ""
                        except Exception:
                            _et = ""
                        exitp = float(_es[0])
                        net_pct = round((exitp / entry - 1) * 100 - 0.23, 2) if entry else 0.0
                        rows_out.append({
                            "rule": "chatbot", "rule_ko": "💬 챗봇", "idx": 90 + len(rows_out),
                            "code": c, "name": f"💬 {name}", "buy_t": cur["buys"][0][2],
                            "sell_t": _et, "entry": entry, "exit": exitp, "qty": net,
                            "result": "win" if net_pct > 0 else "loss" if net_pct < 0 else "flat",
                            "net_pct": net_pct,
                            "parts": {"buys": [[p, q, t] for p, q, t in cur["buys"]],
                                      "sells": [[exitp, net, _et, 0, 0, exitp > entry, entry]]}})
                        continue
                last = None
                try:
                    from services.paper_desk import _live_price
                    last, _n2 = _live_price(c)
                except Exception:
                    pass
                up = round((float(last) / entry - 1) * 100, 2) if (last and entry) else None
                h = {"rule": "chatbot", "code": c, "name": f"💬 {name}",
                     "buy_t": cur["buys"][0][2], "entry": entry,
                     "last": float(last) if last else entry, "unreal_pct": up,
                     "parts": {"buys": [[p, q, t] for p, q, t in cur["buys"]]}}
                if cur["sells"]:
                    left = net
                    s7 = []
                    for p, q, t in cur["sells"]:
                        s7.append([p, q, t, 0, left, p > entry, entry])
                    h["parts"]["sells"] = s7
                    h["qty_left"] = net
                hold_out.append(h)
        # 🕐 OPEN limit orders still queued in the book (boss 2026-08-26: "I have
        # bought naver but it is not come to Holding part... even though we did not
        # buy yet please add like waiting icon") — shown as waiting entries
        opens = db.execute(_sqt(
            "SELECT ticker, name, side, qty, limit_price, created_at FROM paper_desk_orders "
            "WHERE (COALESCE(source,'') IN ('chat','chatbot') OR COALESCE(source,'') LIKE '%-chat') AND status='OPEN' "
            "AND order_type='limit' ORDER BY id")).fetchall()
        for r in opens:
            c = r[0]
            if _menu1_9 and c not in _SIX9:
                continue              # menu 1 is the six, pure (boss 08-27)
            try:
                _m8 = db.execute(_sqt(
                    "SELECT market FROM krx_stocks WHERE code=:c"), {"c": c}).scalar()
                if str(_m8 or "").upper() != "KOSPI":
                    continue          # KOSDAQ/ETF never on the boards (boss 08-26)
            except Exception:
                pass
            if day8:
                try:
                    if r[5] is None or r[5].astimezone(KST).strftime("%Y%m%d") != day8:
                        continue
                except Exception:
                    continue
            try:
                t = r[5].astimezone(KST).strftime("%H:%M:%S") if r[5] is not None else ""
            except Exception:
                t = ""
            lp = float(r[4] or 0)
            hold_out.append({"rule": "chatbot", "code": c, "name": f"💬 {r[1] or c}",
                             "buy_t": t, "entry": lp, "last": lp, "unreal_pct": None,
                             "waiting": True, "side": r[2],
                             "parts": {"buys": [[lp, int(r[3] or 0), t]]}})
    finally:
        db.close()
    return rows_out, hold_out


@router.get("/live/daily-chart")
def live_daily_chart(code: str = Query(...)):
    """THE DAILY CHART on the live desk (boss 2026-08-24: "in this part can
    you put daily chart also" - next to the 5틱/1분 selector). A year of
    daily candles from minute1_hist + today's live tape as the newest one,
    with the layer-zone price lines (85% no-buy / 60% caution / 20% bottom)
    so the year map the judges read is visible to the eye too."""
    import json as _json
    from pathlib import Path as _P
    from services import kiwoom_rules as kr
    c6 = _resolve(code)
    days: dict = {}
    try:
        hist = (_P(__file__).resolve().parent.parent / "data"
                / "minute1_hist" / f"{c6}.json")
        for r in _json.loads(hist.read_text()):
            ts = str(r[0])
            if not ("090000" <= ts[8:14] <= "153000"):
                continue
            d = days.setdefault(ts[:8], {"o": float(r[1]), "h": float(r[2]),
                                         "l": float(r[3]), "c": float(r[4]),
                                         "v": 0.0})
            d["h"] = max(d["h"], float(r[2]))
            d["l"] = min(d["l"], float(r[3]))
            d["c"] = float(r[4])
            d["v"] += float(r[5] or 0)
    except Exception:
        return {"ok": False}
    try:
        cs = kr._bars_for(c6, 5, 60)
        if cs:
            from services.kiwoom_tape import _day as _kd
            days[_kd()] = {"o": cs[0]["close"],
                           "h": max(x["high"] for x in cs),
                           "l": min(x["low"] for x in cs),
                           "c": cs[-1]["close"],
                           "v": sum(float(x.get("vol") or 0) for x in cs)}
    except Exception:
        pass
    if not days:
        return {"ok": False}
    seq = sorted(days.items())[-260:]
    px = seq[-1][1]["c"]
    # the VIEW must read the judge's own map (the engine's _daily_pos range),
    # not a private recount - a 73%-vs-39% split between eye and law would
    # mislead the boss about the very zones he ordered
    kr._daily_pos(c6, px)
    lo, hi = kr._YR_CACHE.get(c6) or (None, None)
    if not lo or not hi or hi <= lo:
        closes = [d["c"] for _, d in seq[:-1]] or [px]
        lo, hi = min(closes), max(closes)
    return {"ok": True, "code": c6,
            "candles": [{"d8": d8, "open": d["o"], "high": d["h"],
                         "low": d["l"], "close": d["c"], "vol": d["v"]}
                        for d8, d in seq],
            "year_hi": hi, "year_lo": lo,
            "pos": (px - lo) / (hi - lo) if hi > lo else None,
            "lines": {"no_buy_85": lo + (hi - lo) * 0.85,
                      "caution_60": lo + (hi - lo) * 0.60,
                      "bottom_20": lo + (hi - lo) * 0.20}}


@router.get("/live/reco-rank-log")
def live_reco_rank_log(n: int = Query(30), day: str = Query("")):
    """THE VISIBLE HEARTBEAT (boss 2026-08-25: "show the process that every
    20 sec is rechecking, like a real-time interactive check"): the rank
    logger's recent snapshots, newest last, for the reco desk's live-check
    panel - each one is a completed 40-stock re-examination."""
    from services.kiwoom_tape import WATCH
    from services.reco_rank_log import live_pulse, snapshots, top_n
    sn = snapshots(day or None)
    return {"ok": True, "top_n": top_n(), "universe": len(WATCH),
            "count": len(sn), "live": live_pulse(),
            "snaps": sn[-max(1, min(int(n or 30), 200)):]}


@router.get("/live/reco-rank-at")
def live_reco_rank_at(code: str = Query(...), t: str = Query(...),
                      day: str = Query("")):
    """WHY THIS STOCK, WHY THIS TIME (boss 2026-08-25, menu 2): the recorded
    checklist rank and score of one stock at one moment - the record every
    reco-desk buy is judged and explained by."""
    from services.reco_rank_log import rank_at
    r = rank_at(_resolve(code), (t or "")[:8], day or None)
    return {"ok": bool(r), **(r or {})}


@router.get("/chat-orders")
def chat_orders(limit: int = Query(30), db: Session = Depends(get_db)):
    """💬 The CHATBOT's own trades (boss 2026-08-26: an order made by chat must be
    recognizable as a chatbot trade on the desk) — newest first."""
    from sqlalchemy import text
    from services.paper_desk import _ensure
    _ensure(db)
    rows = db.execute(text(
        "SELECT id, ticker, name, side, qty, status, fill_price, realized_pnl, "
        "  realized_pnl_pct, created_at FROM paper_desk_orders "
        "WHERE (COALESCE(source,'') IN ('chat','chatbot') OR COALESCE(source,'') LIKE '%-chat') ORDER BY id DESC LIMIT :n"),
        {"n": int(limit)}).fetchall()
    from datetime import timedelta, timezone
    _KST9 = timezone(timedelta(hours=9))

    def _kat(v):
        try:
            return v.astimezone(_KST9).strftime("%Y-%m-%d %H:%M") if v is not None else None
        except Exception:
            return str(v)[:16] if v is not None else None
    return {"ok": True, "orders": [
        {"id": r[0], "ticker": r[1], "name": r[2], "side": r[3], "qty": int(r[4] or 0),
         "status": r[5], "fill_price": (float(r[6]) if r[6] is not None else None),
         "realized_pnl": (float(r[7]) if r[7] is not None else None),
         "realized_pnl_pct": (float(r[8]) if r[8] is not None else None),
         "at": _kat(r[9])} for r in rows]}


@router.get("/live/news-stamps")
def live_news_stamps(code: str = Query(...), stamp: str = Query("")):
    """THE EVIDENCE BEHIND THE RULING (boss 2026-08-24: "I do not see which
    news, why - you have to put hyperlink so I can click and read the full
    news"): today's intern stamps for one stock, headline + link + Qwen's
    one-line reason, newest first. Filter with stamp=위험 for the danger
    list a halving was based on."""
    import json as _json
    from pathlib import Path as _P
    c6 = _resolve(code)
    out = []
    try:
        nd = _P(__file__).resolve().parent.parent / "data" / "news_intern"
        files = sorted(nd.glob("2*.jsonl"))
        if files:
            for ln in files[-1].read_text(encoding="utf-8").splitlines():
                try:
                    r = _json.loads(ln)
                except Exception:
                    continue
                if r.get("code") == c6 and (not stamp
                                            or r.get("stamp") == stamp):
                    out.append({"ts": r.get("ts", ""), "stamp": r.get("stamp"),
                                "title": r.get("title", ""),
                                "link": r.get("link", ""),
                                "why": r.get("why", "")})
    except Exception:
        pass
    return {"ok": True, "code": c6, "stamps": out[-30:][::-1]}


@router.get("/live/rules/layers")
def live_layers(code: str = Query(...)):
    """THE JUDGES' STAMPS (boss 2026-08-21 night: "if we click it should show
    exact steps - daily chart buying zone, volume, news analyzed by Qwen").
    One stock's full layer story for the trade-detail panel: yearly position
    + zone verdict, volume fuel, and the news intern's freshest stamps."""
    import datetime as _dt
    import json as _json
    from collections import Counter as _C
    from pathlib import Path as _P
    from services import kiwoom_rules as kr
    c6 = _resolve(code)
    px = dp = fu = None
    try:
        bars = kr._bars_for(c6, 5, 60)
        px = bars[-1]["close"] if bars else None
        dp = kr._daily_pos(c6, px) if px else None
        fu = kr._fuel(c6, bars) if bars else None
    except Exception:
        pass
    if px is None:
        # market closed / pre-open: no live tape, but the year map still
        # exists - fall back to the last stored close so the daily-chart
        # step never goes dark (Sunday rehearsal catch, 2026-08-23)
        try:
            _hist9 = (_P(__file__).resolve().parent.parent / "data"
                      / "minute1_hist" / f"{c6}.json")
            _rows9 = _json.loads(_hist9.read_text())
            if _rows9:
                px = float(_rows9[-1][4])
                dp = kr._daily_pos(c6, px)
        except Exception:
            pass
    # every line carries both tongues; the page's language toggle picks
    # (boss 2026-08-21: "if English the explanation should be in English,
    # if Korean it should be in Korean")
    steps = []
    if dp is not None:
        pct = round(dp * 100)
        if dp >= 0.85:
            zv = "최고가 근처 - 매수 금지 구역 (자기 최고가를 다시 넘기 어렵다)"
            zve = "near its own record - NO-BUY zone (hard to beat its own highest)"
        elif dp >= 0.6:
            zv = "연중 상단 - 조심 구역, 절반 매수"
            zve = "upper year range - caution zone, half-size buys"
        elif dp <= 0.20:
            zv = ("바닥 근처 - 매수 존. 상승을 음봉으로 팔지 않는다 "
                  "(+2% 도달 후 첫 하락에 매도 밸브)")
            zve = ("near the year's floor - BUYING zone. A rise is not sold "
                   "on falling candles (valve: after +2%, first dip sells)")
        else:
            zv = "중간 지대 - 정상 사이즈"
            zve = "middle band - normal size"
        steps.append({"icon": "📅", "name": "일봉 (연중 위치)",
                      "name_en": "Daily chart (year position)",
                      "value": f"1년 범위의 {pct}% 지점",
                      "value_en": f"at {pct}% of the 1-year range",
                      "verdict": zv, "verdict_en": zve})
    if fu is not None:
        steps.append({"icon": "📊", "name": "거래량 (연료)",
                      "name_en": "Volume (fuel)",
                      "value": f"최근 10분 = 평소의 {fu:.1f}배",
                      "value_en": f"last 10 min = {fu:.1f}x its usual",
                      "verdict": ("연료 부족 - 절반 매수" if fu <= 0.7
                                  else "연료 정상 - 정상 사이즈"),
                      "verdict_en": ("low fuel - half-size buys" if fu <= 0.7
                                     else "fuel normal - full size")})
    # the intern's stamps: today's log, or the freshest log there is
    stamps = []
    try:
        nd = _P(__file__).resolve().parent.parent / "data" / "news_intern"
        files = sorted(nd.glob("2*.jsonl"))
        if files:
            for ln in files[-1].read_text(encoding="utf-8").splitlines():
                try:
                    r = _json.loads(ln)
                except Exception:
                    continue
                if r.get("code") == c6:
                    stamps.append(r)
    except Exception:
        pass
    cc = _C(r.get("stamp") for r in stamps)
    steps.append({"icon": "📰", "name": "뉴스 (Qwen3 로컬 분석)",
                  "name_en": "News (local Qwen3 analysis)",
                  "value": (f"오늘 {len(stamps)}건: 호재 {cc.get('호재', 0)} · "
                            f"중립 {cc.get('중립', 0)} · 위험 {cc.get('위험', 0)}"
                            if stamps else "오늘 분석된 기사 없음"),
                  "value_en": (f"today {len(stamps)} items: good "
                               f"{cc.get('호재', 0)} · neutral "
                               f"{cc.get('중립', 0)} · danger "
                               f"{cc.get('위험', 0)}"
                               if stamps else "no articles analyzed today"),
                  "verdict": "안전 모드 - 최근 1시간 위험 2건 이상이면 신규 매수 "
                             "절반 (금지·강제매도 없음, 채점은 매일 저녁 계속)",
                  "verdict_en": "SAFE MODE - 2+ danger stamps in the last hour "
                                "halve NEW buys (no bans, no forced sells; "
                                "grading continues nightly)"})
    steps.append({"icon": "⏱", "name": "분봉 (트리거)",
                  "name_en": "Minute chart (trigger)",
                  "value": "다섯 개의 문 + 수확/정지 법",
                  "value_en": "the five doors + harvest/stop laws",
                  "verdict": "실제 매수/매도 순간은 분봉 엔진이 결정 - "
                             "각 거래의 '이유' 칸이 그 문이다",
                  "verdict_en": "the exact buy/sell moment is decided by the "
                                "minute engine - each trade's 'why' names "
                                "its door"})
    return {"ok": True, "code": c6, "price": px, "daily_pos": dp, "fuel": fu,
            "steps": steps,
            "news": [{"ts": r.get("ts", ""), "stamp": r.get("stamp"),
                      "title": r.get("title", ""), "why": r.get("why", "")}
                     for r in stamps[-3:]]}


def _fam_compute(family: str, tick: int, period: int, day: str,
                 frm: str, to: str, gate: int, auto: int, codes: str = ""):
    from services.kiwoom_rules import DESK, trades
    import time as _t
    _fk = (family, tick, period, day, frm, to, gate, auto)
    rows = []
    holding = []
    waiting = []
    for v in DESK:
        if family != "all" and v.get("family", "old") != family:
            continue
        _sixset9 = {"000660", "005930", "017670", "034020", "035420", "042660"}
        _codeset9 = {c for c in (codes or "").split(",") if c}
        _rg9 = bool(_codeset9) and _codeset9 != _sixset9
        d = trades(v["id"], tick=tick, period=max(0, min(int(period or 0), 600)),
                   bars=10, limit=500, day=day, frm=frm, to=to,
                   use_gate=bool(gate), allow_fallback=bool(auto), codes=codes,
                   rank_gate=_rg9)
        if not d.get("ok"):
            continue
        for w9 in (d.get("waiting") or []):
            waiting.append(dict(w9, rule=v["id"]))
        for h in (d.get("holding") or []):
            holding.append(dict(h, rule=v["id"]))
            # ONE ROW PER OPEN EPISODE (boss 2026-08-21: "one set of buys, and
            # in the selling column show all sells with time and price" - the
            # per-slice rows repeated the same buying list and confused the
            # room). Every sold slice keeps its full record inside the row's
            # sells; the tally below still counts each slice on its own.
            _hb = (h.get("parts") or {}).get("buys")
            _sl = h.get("slices") or []
            if _sl:
                _sells7 = []
                _won_sum = 0.0
                _cost_sum = 0.0
                for p_, q_, w_, t_, i_, *r_ in _sl:
                    _left = r_[0] if r_ else None
                    _base = ((r_[1] if len(r_) > 1 else None)
                             or h.get("base") or h.get("entry") or p_)
                    _sells7.append([p_, q_, t_, i_, _left, w_, _base])
                    _won_sum += (p_ - _base) * q_
                    _cost_sum += _base * q_
                _gt = (_won_sum / _cost_sum * 100) if _cost_sum else 0.0
                _lastq = _sl[-1]
                _left0 = (_lastq[5] if len(_lastq) > 5 and
                          isinstance(_lastq[5], (int, float)) else None)
                _left9 = h.get("qty_left")
                rows.append({"rule": v["id"], "rule_ko": d.get("ko"),
                             "rule_en": d.get("en"), "idx": -1,
                             "code": h.get("code"), "name": h.get("name"),
                             "d8": None, "buy_t": h.get("buy_t"),
                             "entry": h.get("base") or h.get("entry"),
                             "sell_t": _sl[-1][3], "exit": _sl[-1][0],
                             "net_pct": round(_gt - 0.23, 3),
                             "exit_why": (f"계단 매도 {len(_sl)}조각"
                                          + (f" · 잔여 {_left9:,}주 보유 중"
                                             if _left9 is not None
                                             else " · 보유 중 에피소드")),
                             "left": _left9,
                             "qty": sum(x[1] for x in _sells7),
                             "won": round(_won_sum),
                             "result": ("win" if _won_sum > 0
                                        else "loss" if _won_sum < 0 else "flat"),
                             "partial": True, "sig": None, "wall": None,
                             "judge": h.get("judge"),
                             "parts": {"buys": _hb, "sells": _sells7}})
        for i, tr in enumerate(d.get("trades") or []):
            won = round((tr.get("entry") or 0) * (tr.get("qty") or 1)
                        * (tr.get("net_pct") or 0) / 100)
            rows.append({"rule": v["id"], "rule_ko": d.get("ko"), "rule_en": d.get("en"),
                         "idx": i, "code": tr.get("code"), "name": tr.get("name"),
                         "d8": tr.get("d8"), "buy_t": tr.get("buy_t"),
                         "entry": tr.get("entry"), "sell_t": tr.get("sell_t"),
                         "exit": tr.get("exit"), "net_pct": tr.get("net_pct"),
                         "exit_why": tr.get("exit_why"), "qty": tr.get("qty"),
                         "won": won, "result": tr.get("result"),
                         "sig": tr.get("sig"), "wall": tr.get("wall"),
                         "judge": tr.get("judge"),
                         "parts": tr.get("parts")})
    # newest first (boss 2026-08-11) - the top of the table is what just happened
    rows.sort(key=lambda r: (r.get("d8") or "", r.get("buy_t") or ""), reverse=True)
    # THE GUARD (boss 2026-08-28 night: "every day we have a mistake... every
    # trading result will check in any cases - calculation, time, price, all
    # rules matching - if any mistake write with red small ? sign and inform
    # me"): every row is re-audited against the laws it claims to follow; any
    # discrepancy rides on the row as guard[] and the board wears a red ?.
    for r in rows:
        _gd = []
        p9 = r.get("parts") or {}
        b9 = p9.get("buys") or []
        s9 = p9.get("sells") or []
        if not r.get("partial") and b9 and s9:
            _sp = sum((x[0] or 0) * (x[1] or 0) for x in b9)
            _so = sum((x[0] or 0) * (x[1] or 0) for x in s9)
            if _sp > 0:
                _tn = (_so / _sp - 1) * 100 - 0.23
                if abs(_tn - (r.get("net_pct") or 0)) > 0.05:
                    _gd.append(f"계산 불일치: 체결 재계산 {_tn:.2f}% ≠ 표시 "
                               f"{r.get('net_pct')}%")
            _qb = sum(x[1] or 0 for x in b9)
            _qs = sum(x[1] or 0 for x in s9)
            if _qb != _qs:
                _gd.append(f"수량 불일치: 매수 {_qb}주 ≠ 매도 {_qs}주")
        for x in s9:
            if len(x) > 6 and x[6]:
                _g9 = (x[0] / x[6] - 1) * 100
                _w9 = str(x[5] if len(x) > 5 else "")
                if _g9 < -2.7:   # vol-stop ceiling 2.0% + slippage room
                    _gd.append(f"손절선 크게 이탈: {str(x[2])[:5]} {_g9:.2f}% ({_w9})")
                if 0 < _g9 < 0.23 and "마감" not in _w9:
                    _gd.append(f"수수료선 안 매도: {str(x[2])[:5]} +{_g9:.2f}% ({_w9})")
        _dp = (r.get("judge") or {}).get("dp")
        if _dp is not None and _dp >= 0.85:
            _gd.append(f"최고가권 매수 (연중 위치 {_dp:.2f})")
        if _dp is not None and _dp <= 0.20:
            for x in s9:
                _w9 = str((x[5] if len(x) > 5 else "") or "")
                if ("음봉" in _w9 or "고점" in _w9) and "마감" not in _w9:
                    _gd.append(f"매수존 매도: {str(x[2])[:5]} ({_w9})")
                    break
        if _gd:
            r["guard"] = _gd
    # PIECE-COUNT WIN % (boss 2026-08-28 17:1x, his explicit word after hearing
    # the trip-ruler case: "each one +% must count as one winning - all positive
    # winning cases divided by overall trading cases"): every ▼ sell line is one
    # count, judged against its own at-the-moment base. ON RECORD: this ruler
    # can read green on a red-money day (deployed reading 63% beside -1.01M on
    # d2/m1) - the trip ruler stays in ep_wins/ep_losses and the money column
    # tells the rest. Rows without piece records count their whole trip once.
    w = l = 0
    for r in rows:
        sells = (r.get("parts") or {}).get("sells") or []
        counted = False
        if sells and len(sells[0]) >= 7:
            for sr in sells:
                _b0 = (sr[6] if len(sr) > 6 and sr[6] else r.get("entry"))
                if _b0:
                    counted = True
                    if sr[0] > _b0:
                        w += 1
                    elif sr[0] < _b0:
                        l += 1
        if not counted and not r.get("partial"):
            _n9 = r.get("net_pct") or 0
            if _n9 > 0:
                w += 1
            elif _n9 < 0:
                l += 1
    ew = sum(1 for r in rows
             if not r.get("partial") and (r.get("net_pct") or 0) > 0)
    el = sum(1 for r in rows
             if not r.get("partial") and (r.get("net_pct") or 0) < 0)
    # TOTAL INVESTED (boss 2026-08-31 evening: "add one statistics on top -
    # how much we have invested and how much we gain with price and %"):
    # every won that went into buys, completed episodes and open holdings
    # alike, summed from the raw fills.
    _inv9 = 0.0
    for r in rows:
        if r.get("partial"):
            continue
        for b9 in ((r.get("parts") or {}).get("buys") or []):
            _inv9 += (b9[0] or 0) * (b9[1] or 0)
    for h9 in holding:
        for b9 in ((h9.get("parts") or {}).get("buys") or []):
            _inv9 += (b9[0] or 0) * (b9[1] or 0)
    _res = {"ok": True, "family": family, "rows": rows,
            "trips": len(rows), "wins": w, "losses": l,
            "ep_wins": ew, "ep_losses": el,
            "win_pct_ep": round(ew / (ew + el) * 100) if (ew + el) else 0,
            "win_pct": round(w / (w + l) * 100) if (w + l) else 0,
            "holding": holding, "waiting": waiting,
            "invested": round(_inv9),
            "net_won": sum(r["won"] for r in rows)}
    _FAM_TTL[_fk] = (_t.time(), _res)   # kept: family_daily still reads it
    return _res


@router.get("/live/dip-status")
def live_dip_status(tick: int = Query(5), period: int = Query(0)):
    """Per stock, where the new rule's hunt stands right now - so a quiet board reads
    as "condition not met yet at 삼성전자: waiting for a sharp drop" instead of broken."""
    from services.kiwoom_rules import dip_status
    return dip_status(tick=tick, period=max(0, min(int(period or 0), 600)))


@router.post("/desk-mode")
def desk_mode_set(mode: str = Query(...), force: int = Query(0),
                  confirm_six_off: int = Query(0)):
    """Set which desks trade: "both" (default since 2026-08-24 — the boss's six AND the
    checklist's top five together, deduped), "fixed" (six only) or "score" (five only).
    The collector follows the combined list. During market hours a switch that REMOVES
    stocks needs force=1, because re-pointing mid-session abandons the tape already
    collected for the stocks that leave (adding stocks never abandons anything).

    SIX-OFF GUARD (boss 2026-08-24 ~14:10: his six silently stopped — a STALE board page,
    built before the toggle UI, still sent mode=score as a radio click): turning the six
    OFF (mode="score") now also needs confirm_six_off=1, which only the new toggle UI
    sends after its confirmation dialog. Old cached pages can no longer stop the six."""
    from services.daily_pick import desk_mode, save_picks, set_desk_mode
    from services.kiwoom_tape import WATCH, _day, market_open, refresh_watch
    if mode == "score" and not confirm_six_off:
        return {"ok": False, "mode": desk_mode(), "applied": False,
                "note": ("mode=score turns the boss's six OFF - refused without "
                         "confirm_six_off=1 (sent only by the current board UI after "
                         "its confirmation dialog). Reload the board page if you meant it."),
                "trading_now": [{"code": c, "name": n} for c, n in WATCH]}
    m = set_desk_mode(mode)
    _DP_TTL.clear()
    d = _day()
    if market_open() and not force:
        return {"ok": True, "mode": m, "applied": False,
                "note": "market is open - pass force=1 to switch the collector now",
                "trading_now": [{"code": c, "name": n} for c, n in WATCH]}
    save_picks(d)
    refresh_watch(force=True)
    return {"ok": True, "mode": m, "applied": True, "day": d,
            "trading_now": [{"code": c, "name": n} for c, n in WATCH]}


@router.post("/reco-n")
def reco_n_set(n: int = Query(...), force: int = Query(0)):
    """How many top-scored stocks the reco desk trades (boss 2026-08-24: a menu to
    choose the number). Persists, re-saves today's picks, re-points the collector.
    Mid-session it needs force=1 (shrinking N abandons the leavers' tape)."""
    from services.daily_pick import reco_n, set_reco_n
    from services.kiwoom_tape import WATCH, _day, market_open, refresh_watch
    from services.daily_pick import save_picks
    nn = set_reco_n(n)
    _DP_TTL.clear()
    if market_open() and not force:
        return {"ok": True, "n": nn, "applied": False,
                "note": "market is open - pass force=1 to re-point the collector now"}
    save_picks(_day())
    refresh_watch(force=True)
    return {"ok": True, "n": nn, "applied": True,
            "trading_now": [{"code": c, "name": x} for c, x in WATCH]}


@router.get("/reco-trade-mode")
def reco_trade_mode_get():
    """auto = the reco picks trade themselves · semi = algo BUYs on reco picks become
    suggestions awaiting the human click (SELLs always execute; the six always auto)."""
    from services.trade_suggestions import pending, trade_mode
    return {"ok": True, "mode": trade_mode(), "pending": pending()}


@router.post("/reco-trade-mode")
def reco_trade_mode_set(mode: str = Query(...)):
    from services.trade_suggestions import set_trade_mode
    return {"ok": True, "mode": set_trade_mode(mode)}


@router.get("/suggestions")
def suggestions_list():
    from services.trade_suggestions import pending
    return {"ok": True, "pending": pending()}


@router.post("/suggestions/{sug_id}")
def suggestion_decide(sug_id: str, approve: int = Query(...), db: Session = Depends(get_db)):
    """The human's final click: approve=1 executes the suggested order, approve=0 rejects."""
    from services.trade_suggestions import decide
    return decide(db, sug_id, bool(approve))


@router.get("/desk-mode")
def desk_mode_get():
    from services.daily_pick import DESK, desk_mode
    from services.kiwoom_tape import WATCH
    return {"ok": True, "mode": desk_mode(), "fixed_six": DESK,
            "trading_now": [{"code": c, "name": n} for c, n in WATCH]}


@router.get("/overnight")
def overnight(force: int = Query(0)):
    """What the US did while Korea slept - S&P, NASDAQ, SOXX, USD/KRW with the day's
    plain-words read. Cached per calendar day; force=1 refetches."""
    from services.overnight import fetch
    return fetch(force=bool(force))


@router.get("/drill")
def history_drill(code: str = Query(...), day: str = Query(...),
                  level: str = Query("hours"), hour: str = Query(""),
                  minute: str = Query("")):
    """The boss's drill-down (2026-08-11): a day opens into hours, an hour into
    minutes, a minute into seconds - price, volume, and direction at every level.
    Second-level truth exists only for days our collector recorded; Kiwoom keeps ~40s
    of the past, so what we did not record cannot be conjured."""
    from services.kiwoom_tape import load
    ticks = load(code, day)
    if not ticks:
        return {"ok": False, "error": "no tape recorded for this day"}

    def bucket(key_len):
        out = []
        cur = None
        for x in ticks:
            t = x["ts"][8:14]              # HHMMSS
            if level == "minutes" and t[:2] != hour:
                continue
            if level == "seconds" and (t[:2] != hour or t[2:4] != minute):
                continue
            k = t[:key_len]
            if cur is None or cur["k"] != k:
                if cur is not None:
                    out.append(cur)
                cur = {"k": k, "open": x["px"], "high": x["px"], "low": x["px"],
                       "close": x["px"], "vol": 0, "n": 0}
            cur["close"] = x["px"]
            cur["high"] = max(cur["high"], x["px"])
            cur["low"] = min(cur["low"], x["px"])
            cur["vol"] += x.get("qty") or 0
            cur["n"] += 1
        if cur is not None:
            out.append(cur)
        prev = None
        for r in out:
            r["chg"] = (round((r["close"] / prev - 1) * 100, 3) if prev else None)
            r["dir"] = 0 if prev is None or r["close"] == prev else (1 if r["close"] > prev else -1)
            prev = r["close"]
        return out

    key_len = {"hours": 2, "minutes": 4, "seconds": 6}.get(level, 2)
    rows = bucket(key_len)
    fmt = {2: lambda k: f"{k}:00", 4: lambda k: f"{k[:2]}:{k[2:]}",
           6: lambda k: f"{k[:2]}:{k[2:4]}:{k[4:]}"}[key_len]
    return {"ok": True, "code": code, "day": day, "level": level,
            "rows": [{"t": fmt(r["k"]), "key": r["k"], "open": r["open"],
                      "high": r["high"], "low": r["low"], "close": r["close"],
                      "vol": r["vol"], "n": r["n"], "chg": r["chg"], "dir": r["dir"]}
                     for r in rows]}


_FAM_DAILY_CACHE: dict = {}


@router.get("/live/rules/family-daily")
def family_daily(family: str = Query("d1"), tick: int = Query(5),
                 period: int = Query(60)):
    """One line per stored day for one algorithm - trades, win %, money - so the
    day dropdown can show what each day WAS at a glance (boss 2026-08-12 night:
    "I can see what was winning % on 08-12 or other days"). Finished days are
    frozen and cached for ever; today is recomputed on each call."""
    from services.kiwoom_rules import stored_days
    from services.kiwoom_tape import _day as _kd
    out = []
    for d in stored_days("000660"):
        key = (family, d, tick, period)
        hit = _FAM_DAILY_CACHE.get(key)
        if hit is None:
            try:
                # read the live cache or compute directly - never the endpoint,
                # whose {computing} placeholder would record a 0-trade day here
                _sk = ("fam", family, tick, period, d, "", "", 1, 0)
                _sh = _SWR.get(_sk)
                r = _sh[1] if _sh else _fam_compute(family, tick, period,
                                                    d, "", "", 1, 0)
                hit = {"d8": d, "trips": r.get("trips", 0),
                       "win_pct": r.get("win_pct", 0), "wins": r.get("wins", 0),
                       "losses": r.get("losses", 0),
                       "net_won": r.get("net_won", 0)}
            except Exception:
                hit = {"d8": d, "trips": 0, "win_pct": 0, "wins": 0,
                       "losses": 0, "net_won": 0}
            if d < _kd():
                _FAM_DAILY_CACHE[key] = hit
        out.append(hit)
    return {"ok": True, "family": family, "days": out}


@router.get("/drill-days")
def history_drill_days(code: str = Query(...)):
    """Which days have recorded tape (drillable to the second) for this stock."""
    from services.kiwoom_rules import stored_days
    return {"ok": True, "days": stored_days(code)}


@router.get("/raw-daily")
def raw_daily(code: str = Query(...), days: int = Query(20), to: str = Query("")):
    """The raw rows the picker reads - open/high/low/close/volume straight out of
    raw_daily_prices, plus the flow rows, so the boss can check the source data without
    opening Supabase. Nothing is computed here beyond the day's % change."""
    from ml._db import get_conn
    n = max(1, min(int(days or 20), 250))
    conn = get_conn(); cur = conn.cursor()
    try:
        if to:
            cur.execute("""SELECT date,open,high,low,close,volume FROM raw_daily_prices
                           WHERE ticker=%s AND date<=%s ORDER BY date DESC LIMIT %s""",
                        (code, f"{to[:4]}-{to[4:6]}-{to[6:]}", n))
        else:
            cur.execute("""SELECT date,open,high,low,close,volume FROM raw_daily_prices
                           WHERE ticker=%s ORDER BY date DESC LIMIT %s""", (code, n))
        rows = [{"date": d.strftime("%Y-%m-%d"), "open": float(o or 0), "high": float(h or 0),
                 "low": float(lo or 0), "close": float(c or 0), "volume": float(v or 0)}
                for d, o, h, lo, c, v in cur.fetchall()][::-1]
        for i in range(1, len(rows)):
            p = rows[i - 1]["close"]
            rows[i]["chg"] = round((rows[i]["close"] / p - 1) * 100, 2) if p else 0.0
        if rows:
            rows[0]["chg"] = None
        flows = []
        try:
            cur.execute("""SELECT date,foreign_net_value,inst_net_value,individual_net_value
                           FROM korean_investor_flows WHERE ticker=%s ORDER BY date DESC LIMIT %s""",
                        (code, n))
            flows = [{"date": d.strftime("%Y-%m-%d"), "foreign": float(f or 0),
                      "inst": float(i or 0), "retail": float(r or 0)}
                     for d, f, i, r in cur.fetchall()][::-1]
        except Exception:
            conn.rollback()
        cur.execute("SELECT name FROM krx_stocks WHERE code=%s", (code,))
        nm = cur.fetchone()
        return {"ok": True, "code": code, "name": (nm[0] if nm else code),
                "table": "raw_daily_prices", "rows": rows, "flows": flows,
                "flow_latest": (flows[-1]["date"] if flows else None)}
    finally:
        conn.close()


_DP_TTL: dict = {}


def _enrich_pick(res: dict, db) -> None:
    """CONSISTENCY WITH THE CHATBOT (boss 2026-08-24: board 순위표 and chat 추천 showed
    different lists): attach the same live layer the chat recommendation uses — base
    (morning) + now (intraday change ±3, order-book pressure ±2) → live_total — plus
    the checklist-CATEGORY columns (시장/이슈·수급/종목선정/실행·관리) for the new table."""
    try:
        from services.checklist_reco import _live_state
        rows_sorted = sorted(res.get("rows", []), key=lambda r: -(r.get("score") or 0))
        for r in rows_sorted[:10]:
            lv = _live_state(db, r["code"])
            r["live_adj"] = lv["adj"]
            r["live_total"] = round((r.get("score") or 0) + lv["adj"], 1)
            if lv.get("chg") is not None:
                r["live_chg"] = round(lv["chg"], 1)
            z = lv.get("zone")
            if z:
                r["zone"] = z["zone"]
                r["zone_pos"] = z["pos"]
    except Exception:
        pass
    try:
        from services.checklist_engine import market_preflight
        _mp9 = market_preflight(db) or {}
        res["market_pct"] = _mp9.get("pct")
        # the market column's own calculation, item by item (boss 2026-08-25:
        # "each column must show its calculation") - market-wide, so once per
        # response, not per stock
        res["market_items"] = [
            {"no": it.get("no"), "q": it.get("q"), "q_en": it.get("q_en"),
             "ok": it.get("ok"), "d": str(it.get("detail") or "")[:70],
             "w": it.get("weight")}
            for it in sorted(_mp9.get("items") or [],
                             key=lambda x: x.get("no") or 0)]
    except Exception:
        res["market_pct"] = None
        res["market_items"] = []
    _W = {"trend": 25, "liquidity": 20, "flexibility": 20, "levels": 15, "momentum": 10}
    # THE FOUR-CATEGORY AVERAGE (boss 2026-08-25: "use market / issue-supply /
    # stock-selection / execution-management, calculate each column and divide
    # by the number of columns - the average score - and calculate execution
    # management too, it shows nothing"). exec = the automatable 실행 items
    # (#76 levels, #79 risk:reward, #82 method) run per stock, plus #83
    # (mechanical stop) which our engine guarantees by law. Computed for the
    # ranking's top rows + the desk (the ctx fetch costs ~db-read per stock).
    _rows_by = sorted(res.get("rows", []), key=lambda r: -(r.get("score") or 0))
    _exec_on = {r["code"] for r in _rows_by[:10]} | {r["code"] for r in res.get("rows", []) if r.get("pinned") or r.get("by_score")}
    for r in res.get("rows", []):
        g = r.get("groups") or {}
        try:
            _wsum = sum(w for k, w in _W.items() if k in g)
            ssel = round(sum(g[k] * w for k, w in _W.items() if k in g) / _wsum) if _wsum else None
        except Exception:
            ssel = None
        _exec9 = None
        _exec_items9 = []
        if r["code"] in _exec_on:
            try:
                from services.checklist_engine import (_c_entry_levels, _c_rr,
                                                       _c_method_agreement,
                                                       _stock_ctx)
                _cx9 = _stock_ctx(db, r["code"])
                _got9 = []
                for _no9, _q9, _fn9 in ((76, "진입/손절/목표 레벨", _c_entry_levels),
                                        (79, "손익비 ≥1.2", _c_rr),
                                        (82, "방법(ML/파동) 지지", _c_method_agreement)):
                    try:
                        _ok9, _dt9 = _fn9(_cx9)
                    except Exception:
                        _ok9, _dt9 = None, "계산 불가"
                    if _ok9 is not None:
                        _got9.append(bool(_ok9))
                    _exec_items9.append({"no": _no9, "q": _q9,
                                         "ok": _ok9, "d": str(_dt9)[:80]})
                _got9.append(True)   # #83 기계식 손절 - the engine's own law
                _exec_items9.append({"no": 83, "q": "기계식 손절 (엔진 자동)",
                                     "ok": True, "d": "-1% 보호선을 엔진이 즉시 실행"})
                _exec9 = round(sum(_got9) / len(_got9) * 100) if _got9 else None
            except Exception:
                _exec9 = None
        _cats9 = {"market": res.get("market_pct"), "issue": g.get("flows"),
                  "stock_sel": ssel, "exec": _exec9}
        # THE SUM LAW (boss 2026-08-26: "no average - the sum of all item
        # scores is the final score", per-item weights from the proof document
        # measured on 2x250 days + literature). Base max 92; the remaining 8
        # news-layer points act through the live 4-second adjustment.
        # ⚙️ engine-law items (28 pts) are granted - the engine enforces them
        # on every trade, identically for every stock.
        try:
            _pts9 = 28.0                              # engine-enforced items
            _MW9 = {11: 1, 12: 2, 13: 0.5, 14: 1, 15: 0.5, 16: 1, 17: 0.5,
                    18: 0.5, 19: 0.5, 20: 0.5, 21: 4, 22: 2, 24: 0.5,
                    25: 0.5, 28: 0.5, 30: 0.5, 33: 0.5, 36: 0.5, 37: 0.5,
                    39: 0.5, 95: 2, 100: 1}
            for _mi9 in (res.get("market_items") or []):
                if _mi9.get("ok"):
                    _pts9 += _MW9.get(_mi9.get("no"), 0)
            _dt9x = r.get("detail") or {}
            def _sub9(grp, idx):
                try:
                    return float((_dt9x.get(grp) or [])[idx].get("s") or 0)
                except Exception:
                    return 0.0
            # 31/32/34/43 (flows), 46/47+69 (volume family), 48, 51/52/50/58
            _pts9 += 6 * _sub9("flows", 0) / 100 + 3 * _sub9("flows", 1) / 100 \
                + 2 * _sub9("flows", 2) / 100 + 1 * _sub9("flows", 3) / 100
            _pts9 += 5 * _sub9("liquidity", 0) / 100 + 6 * _sub9("liquidity", 1) / 100
            _pts9 += 2 * _sub9("flexibility", 0) / 100
            _pts9 += 5 * _sub9("trend", 0) / 100 + 2 * _sub9("trend", 1) / 100 \
                + 1 * _sub9("trend", 2) / 100 + 2 * _sub9("trend", 3) / 100
            _EW9 = {76: 2, 79: 2, 82: 2, 83: 2}
            if _exec_items9:
                for _ei9 in _exec_items9:
                    if _ei9.get("ok"):
                        _pts9 += _EW9.get(_ei9.get("no"), 0)
            else:
                _pts9 += 2      # #83 mechanical stop - engine-guaranteed for
                                # every stock even when the per-stock exec trio
                                # wasn't computed (only the top-10 get that
                                # db-read; the top-5 seat race stays fair since
                                # all contenders are inside the computed set)
            _cats9["avg"] = round(_pts9, 1)
        except Exception:
            _have9 = [v for v in _cats9.values() if v is not None]
            _cats9["avg"] = round(sum(_have9) / len(_have9), 1) if _have9 else None
        r["cats"] = _cats9
        if _exec_items9:
            r["exec_items"] = _exec_items9


@router.get("/daily-pick")
def daily_pick_today(day: str = Query(""), refresh: int = Query(0),
                     force: int = Query(0), db: Session = Depends(get_db)):
    """TODAY's five, chosen by the checklist: long-run character x current condition,
    everything from data before today. refresh=1 recomputes and re-points the collector
    (only honoured outside market hours - swapping stocks mid-session would abandon
    half a day of tape)."""
    from services.daily_pick import pick, save_picks
    from services.kiwoom_tape import WATCH, _day, market_open, refresh_watch
    d = day or _day()
    # the picker reads Supabase and costs ~1.7s per call for an answer that changes once
    # a day - a 60s cache makes every page load after the first ~3ms. refresh and the
    # desk-mode switch clear it.
    import time as _t
    from services.daily_pick import desk_mode as _dm
    if not refresh:
        _hit = _DP_TTL.get((d, _dm()))
        if _hit and _t.time() - _hit[0] < 60:
            return _hit[1]
    # refresh=1 re-points the collector at today's list. Normally only outside market
    # hours (swapping mid-session abandons half a day of a stock's tape) - force=1 does
    # it anyway, which the boss asked for when a pinned stock was missing from the desk.
    if refresh:
        if market_open() and not force:
            res = pick(d)
            res["note"] = "market is open - pass force=1 to switch now"
        else:
            save_picks(d)
            refresh_watch(force=True)
    res = pick(d)
    _enrich_pick(res, db)
    try:
        from services.daily_pick import reco_n as _rn
        res["n_picks"] = _rn()
    except Exception:
        pass
    res["trading_now"] = [{"code": c, "name": n} for c, n in WATCH]
    # SETS, not lists: the collector holds the picks in score order while a fixed desk
    # lists them in the boss's order, and comparing lists made the board warn "still
    # collecting the previous five" when it was collecting exactly the right ones.
    res["applied"] = {c for c, _n in WATCH} == set(res.get("picks", []))
    res["market_open"] = market_open()
    if not refresh:
        from services.daily_pick import desk_mode as _dm2
        _DP_TTL[(d, _dm2())] = (_t.time(), res)
    else:
        _DP_TTL.clear()
    return res


@router.get("/gate")
def daily_gate_today(day: str = Query("")):
    """GO / NO-GO per stock for today, with the reason (advisor's point 2). Computed
    from daily bars STRICTLY BEFORE the day being judged, so the morning verdict uses
    nothing from the session it gates."""
    from services.daily_gate import ACTIVE, gate_all
    from services.kiwoom_tape import _day
    rows = gate_all(day)
    return {"ok": True, "day": day or _day(), "checks_active": ACTIVE,
            "go": sum(1 for r in rows if r["go"]), "total": len(rows), "rows": rows}


@router.get("/screener")
def screener():
    """The stock screener's ranking (advisor's point 1, boss 2026-08-10). Every stock
    with a year of data scored on cost / liquidity / movement / continuation behaviour /
    flows / how our own rules did - scored ONLY on months before April, then the chosen
    five were tested on the four months it never saw. Served from the stored result so
    the page never waits on a 7-minute recompute; rerun the screener to refresh it."""
    import json as _j
    from pathlib import Path as _P
    f = _P(__file__).resolve().parent.parent / "data" / "screener.json"
    if not f.exists():
        return {"ok": False, "error": "screener has not been run yet"}
    d = _j.loads(f.read_text(encoding="utf-8"))
    rows = sorted(d.get("scores", {}).items(), key=lambda kv: -kv[1])
    src = d.get("source", "")
    out = []
    for i, (code, sc) in enumerate(rows, 1):
        c = (d.get("checks") or {}).get(code, {})
        g = (d.get("groups") or {}).get(code, {})
        out.append({"rank": i, "code": code,
                    "name": (d.get("names") or {}).get(code, code),
                    "score": round(sc, 1),
                    # the six checkpoint groups behind the score (0-100 each)
                    "g_cost": g.get("g_flex"), "g_liquidity": g.get("g_liquidity"),
                    "g_movement": g.get("g_levels"), "g_behavior": g.get("g_trend"),
                    "g_flow": g.get("g_flow"), "g_fit": g.get("g_momentum"),
                    "tick_pct": round(c.get("tick_pct", 0), 3),
                    "move_vs_cost": round(c.get("mv_vs_cost", 0), 2),
                    "continue_pct": round(c.get("cont", 0), 1),
                    "fit_win": round(c.get("fit_win", 0), 1),
                    "live": code in (d.get("live") or [])})
    return {"ok": True, "scored_on": d.get("scored_on"), "tested_on": d.get("tested_on"),
            "test_result": d.get("test_result"), "weights": d.get("weights"),
            "source": src, "missing": d.get("missing"), "rows": out}


@router.get("/live/warm")
def live_warm():
    """Pre-open warm-up: train/cache TODAY's ML bundles before the bell, so the first
    poll after 09:00 answers instantly instead of training 30 models mid-open. Safe to
    call any time - training is walk-forward (prior stored days only) and cached."""
    from services.kiwoom_rules import ML_RULES, kiwoom_ml_for
    from services.kiwoom_tape import WATCH, _day
    ref = _day()
    out = []
    # all four clocks the dropdown offers - a cold clock trained minutes mid-session
    # when first clicked (boss 2026-08-07: "if i switch to 1 minute it is not showing")
    for tick, period in ((5, 0), (10, 0), (5, 30), (5, 60)):
        for v in ML_RULES:
            for code, _n in WATCH:
                kiwoom_ml_for(code, tick, period, v, ref)
    for v in ML_RULES:
        for code, name in WATCH:
            b = kiwoom_ml_for(code, 5, 0, v, ref)
            out.append({"rule": v["id"], "stock": name,
                        "model": (b.get("algo") if b else None),
                        "auc": (b.get("auc") if b else None)})
    # THE HISTORIES TOO (boss 2026-08-13 15:2x: a cold 알고리즘1 history took
    # 218 seconds after a restart and read as "not showing"). Warm computes all
    # three families' tables so the first click after any restart is instant.
    # period=0/tick=5 IS the key the page actually requests (found 2026-08-19:
    # warm precomputed period=60 - a key the page never asks for, so the warm
    # never helped a real reload). The rank board is prefilled the same way.
    # computed DIRECTLY and stored under the endpoints' exact cache keys - going
    # through the endpoints would now just receive {computing} placeholders
    import time as _t2
    from services.kiwoom_tape import _day as _kd9
    # SLIM WARM (2026-08-25 midday: the fat warm - 8 fam computes + 4 ranks
    # in ONE request - outlived its client and took the process with it).
    # Only what the page's default view actually asks for: the three live
    # families + old, 1분 clock. Everything else earns its cache on first
    # click through the vital lane.
    _sixw = ["000660", "005930", "017670", "034020", "035420", "042660"]
    try:
        from services.daily_pick import score_five as _sfw
        _recow = sorted(c for c, _n in (_sfw() or []))
    except Exception:
        _recow = []
    for _cw in ([_sixw, _recow] if _recow else [_sixw]):
        _ncw = ",".join(sorted(_cw))
        for fam in ("d1", "d2", "d3", "d4"):
            try:
                _SWR[("fam", fam, 5, 60, _kd9(), "", "", 1, 1, _ncw)] = (
                    _t2.time(), _fam_compute(fam, 5, 60, "", "", "", 1, 1,
                                             ",".join(_cw)))
            except Exception:
                pass
    try:
        from services.kiwoom_rules import rank as _rank2
        # both desks' rank keys, in the endpoint's normalized (sorted-codes)
        # shape - the old codes-less prefill matched nothing the page asks
        # for since the two-menu split (2026-08-25: the reco menu hid its
        # whole section behind this cold key)
        _six9 = ["000660", "005930", "017670", "034020", "035420", "042660"]
        try:
            from services.daily_pick import score_five as _sf9
            _reco9 = sorted(c for c, _n in (_sf9() or []))
        except Exception:
            _reco9 = []
        for _codes9 in ([_six9, _reco9] if _reco9 else [_six9]):
            _nc9 = ",".join(sorted(_codes9))
            _SWR[("rank", 5, 60, _kd9(), "", "", True, True, _nc9)] = (
                _t2.time(), _rank2(tick=5, period=60, day="", frm="",
                                   to="", use_gate=True,
                                   allow_fallback=True,
                                   codes=",".join(_codes9)))
    except Exception:
        pass
    return {"ok": True, "day": ref, "models": out,
            "trained": sum(1 for x in out if x["model"])}


@router.get("/live/datafile")
def live_datafile(code: str = Query("005930"), mins: int = Query(12),
                  frm: str = Query(""), to: str = Query(""), hhmm: str = Query("")):
    """The minute-by-minute record of what REALLY traded, so a fill can be reconciled
    against it — the same surface the artificial Strategy Lab has (boss 2026-08-04:
    "Data file also all row data like we did in the Strategy lab which is missing in the
    Kiwoom side"). hhmm="10:32" returns every execution in that minute."""
    from services.kiwoom_tape import data_file
    return data_file(code, mins=max(1, min(int(mins or 12), 400)),
                     frm=frm, to=to, hhmm=hhmm)


@router.get("/live/book")
def live_book(code: str = Query("005930")):
    """The real 10-level order book — who is waiting to buy and to sell, right now."""
    from services.kiwoom_rest import order_book, current_price
    ob = order_book(code, ttl=0.8) or {}
    lv = ob.get("levels") or []
    asks = sorted([[l["price"], l["qty"]] for l in lv if l["side"] == "ask"])[:10]
    bids = sorted([[l["price"], l["qty"]] for l in lv if l["side"] == "bid"], reverse=True)[:10]
    cp = current_price(code) or {}
    return {"ok": bool(asks or bids), "code": code, "asks": asks, "bids": bids,
            "best_ask": ob.get("best_ask"), "best_bid": ob.get("best_bid"),
            "last": cp.get("price"), "prev_close": cp.get("prev_close"),
            "change_pct": cp.get("change_pct"), "name": cp.get("name")}


@router.get("/live/execs")
def live_execs(code: str = Query("005930"), n: int = Query(120)):
    """The real execution tape, newest first, from what the collector has stored — so the
    rows here are exactly the ticks the bars above are built from."""
    kt = _tape_ready()
    ticks = kt.load(code)
    from services.kiwoom_rest import current_price
    cp = current_price(code) or {}
    rows = ticks[-max(1, n):][::-1]
    return {"ok": True, "code": code, "prev_close": cp.get("prev_close"),
            "rows": rows, "total": len(ticks)}


@router.get("/proof/run")
def proof_run(source: str = Query("synthetic"), seed: int = Query(7),
              code: str = Query("005930"), period: int = Query(60),
              mode: str = Query("min1"), around: str = Query(""),
              start: int = Query(0), tick: int = Query(0),
              exit_mode: str = Query("candle"), take_pct: float = Query(0.5),
              stop_pct: float = Query(1.0), need: int = Query(3), need_dn: int = Query(0),
              dec_tick: int = Query(0),
              db: Session = Depends(get_db)):
    """🧪 Proof Lab (boss 2026-07-29): prove Algo 3 buys EXACTLY on the 3rd rising candle
    and sells EXACTLY on the 3rd falling candle, with an independent verifier.
    source='synthetic' = planted artificial day (order-book fill proof included);
    source='kiwoom'    = TODAY's real Kiwoom 1-min bars replayed through the same engine
    function. code='ALL' = every company on the desk watchlist, aggregated.
    start=0 → the complete artificial day; start=<epoch> → a tape that BEGINS at that
    moment and grows one candle per real minute (the boss's live watch)."""
    from services.proof_sim import run_synthetic, run_kiwoom
    if source == "kiwoom":
        if code == "ALL":
            from services.candle_trader import _cfg
            return run_kiwoom(codes=_cfg(db)["codes"])
        return run_kiwoom(code=code)
    res = run_synthetic(seed=seed, period=period, mode=mode, around=around, start=start, tick=tick,
                        exit_mode=exit_mode, take_pct=take_pct, stop_pct=stop_pct,
                        need=max(2, min(int(need), 6)), need_dn=max(0, min(int(need_dn), 6)),
                        # dec_tick PINS the rule's clock: the trades stay identical whatever
                        # timeframe the chart is drawing (boss 2026-08-03)
                        dec_tick=max(0, min(int(dec_tick or 0), 500)))
    # Trim the wire payload to the fields the page actually draws. off0/n/half are internal
    # bookkeeping (which seconds a bar covers) that the verifier needs but the chart never
    # reads — and on the 1초 chart there are thousands of bars, so they are pure weight.
    # self_check() calls run_synthetic directly and still sees the complete candles.
    keep = ("time", "hhmm", "open", "high", "low", "close", "dir", "t0", "vol")
    for sym in res.get("symbols", []):
        sym["candles"] = [{k: c[k] for k in keep if k in c} for c in sym["candles"]]
    return res


@router.get("/proof/combos")
def proof_combos(seed: int = Query(7), start: int = Query(0), tick: int = Query(5)):
    """🔀 Every rule combination's 승률 in one call — the numbers that sit on the buttons
    BEFORE the boss clicks one (2026-07-31: "before clicking also it should show winning %").

    Deliberately returns counts only, no candles: the page needs nine numbers, and nine
    full chart payloads is ~32k bars × 3 stocks, which is not something to run on page load.
    Same market, same clock, one rule apart — so the nine are directly comparable."""
    from services.proof_sim import combo_scores
    return combo_scores(seed=seed, start=start, tick=tick)


@router.get("/proof/lab")
def proof_lab(seed: int = Query(7), start: int = Query(0), tick: int = Query(5),
              code: str = Query(""), bars: int = Query(500), hist: int = Query(40),
              period: int = Query(0)):
    """🔬 Strategy Lab: every rule variant trading the SAME artificial market, side by
    side, so Monday's comparison differs only by the rule (boss 2026-07-31). Nothing is
    stored — the market is deterministic, so this recomputes the full history from the
    session start on every call and a restart cannot lose a trade."""
    from services.proof_lab import compare
    return compare(seed=seed, start=start, tick=tick, code=code, bars=bars, hist=hist,
                   period=max(0, min(int(period or 0), 60)))


@router.get("/proof/lab/trades")
def proof_lab_trades(variant: str = Query(...), seed: int = Query(7), start: int = Query(0),
                     tick: int = Query(5), code: str = Query(""), bars: int = Query(400),
                     limit: int = Query(400), around: int = Query(-1),
                     period: int = Query(0), at: str = Query("")):
    """🔎 Drill-down behind a ranking row: every trade ONE rule made, on every stock, with
    the 5틱 candles so the rule can be checked against the bars it counted."""
    from services.proof_lab import variant_trades
    return variant_trades(variant, seed=seed, start=start, tick=tick, code=code,
                          bars=bars, limit=limit, around=around,
                          period=max(0, min(int(period or 0), 60)), at=at)


@router.get("/proof/lab/sessions")
def proof_lab_sessions():
    """The 07:21 opens of today and the days before it, so the lab can load more than one
    day. The artificial market is deterministic — an earlier open regenerates those days
    exactly, which is why nothing had to be stored to get yesterday back."""
    from services.proof_lab import sessions
    return sessions()


@router.get("/proof/lab/datafile")
def proof_lab_datafile(seed: int = Query(7), start: int = Query(0), code: str = Query(""),
                       mins: int = Query(10), frm: str = Query(""), to: str = Query(""),
                       hhmm: str = Query("")):
    """🕰️ Data File for the Strategy Lab — the minute-by-minute record the rules trade on.
    hhmm=10:32 opens that minute and returns EVERY deal in it, grouped by second, so a
    fill can be reconciled against the tape it came from (boss 2026-08-03)."""
    from services.proof_lab import data_file
    return data_file(seed=seed, start=start, code=code, mins=mins, frm=frm, to=to, hhmm=hhmm)


@router.get("/proof/lab/gate")
def proof_lab_gate(seed: int = Query(7), start: int = Query(0), tick: int = Query(5)):
    """The consistency gate: proves the lab is reading the same market the charts draw —
    same prices, same times, same ups and downs. Run hourly by the watchdog."""
    from services.proof_lab import consistency_gate
    return consistency_gate(seed=seed, start=start, tick=tick)


@router.get("/proof/selfcheck")
def proof_selfcheck(seed: int = Query(7)):
    """🔬 Proof Lab self-check: runs the whole consistency matrix (all timeframes × both
    decision modes) and returns pass/fail counts so the boss can verify it himself."""
    from services.proof_sim import self_check
    return self_check(seed=seed)


@router.get("/proof/book")
def proof_book(source: str = Query("synthetic"), code: str = Query("PRF1"),
               seed: int = Query(7), period: int = Query(60), start: int = Query(0)):
    """⚡ Kiwoom-speed 10-level ladder for the Proof Lab price-table view (polled ~1/sec)."""
    from services.proof_sim import live_book_fast
    return live_book_fast(source, code, seed, period, start)


@router.get("/proof/minute_tape")
def proof_minute_tape(source: str = Query("synthetic"), code: str = Query("PRF1"),
                      seed: int = Query(7), hhmm: str = Query(...), period: int = Query(60),
                      start: int = Query(0)):
    """🕰️ Proof Lab drill-down: one candle's per-second tape (synthetic only)."""
    from services.proof_sim import minute_tape
    return minute_tape(source, code, seed, hhmm, period, start)


@router.post("/candle3/buy")
def candle3_buy(code: str = Query(...), db: Session = Depends(get_db)):
    """Semi mode: the boss accepts a 🔔 candle BUY recommendation."""
    from services.candle_trader import semi_buy
    return semi_buy(db, code)


@router.post("/candle3/sell")
def candle3_sell(code: str = Query(...), db: Session = Depends(get_db)):
    from services.candle_trader import sell_all
    return sell_all(db, code)


@router.post("/candle3/tick")
def candle3_tick(force: bool = Query(False), db: Session = Depends(get_db)):
    from services.candle_trader import tick
    return tick(db, force=force)


# ---- 🔀 Algorithm 4 · Cross-Check (trades only when the 3 algos agree) -------- #
@router.get("/crosscheck/status")
def crosscheck_status(db: Session = Depends(get_db)):
    from services.cross_trader import status
    return status(db)


@router.post("/crosscheck/toggle")
def crosscheck_toggle(on: bool = Query(...), db: Session = Depends(get_db)):
    from services.cross_trader import set_enabled
    return set_enabled(db, on)


@router.post("/crosscheck/params")
def crosscheck_params(rule: Optional[str] = Query(None), stop_pct: Optional[float] = Query(None),
                      pos_pct: Optional[float] = Query(None), mode: Optional[str] = Query(None),
                      codes: Optional[str] = Query(None), take_pct: Optional[float] = Query(None),
                      brain_light: Optional[bool] = Query(None),
                      exit_mode: Optional[str] = Query(None),
                      db: Session = Depends(get_db)):
    """Cross-Check dials: rule, stop %, size %, mode, stock list, take_pct, brain_light, and
    exit_mode ('trail' = winner-target trailing; 'candle' = ride until a 3-down-candle
    reversal, keeping the -stop% floor + EOD)."""
    from services.cross_trader import set_params
    return set_params(db, rule=rule, stop_pct=stop_pct, pos_pct=pos_pct, mode=mode,
                      codes=codes, take_pct=take_pct, brain_light=brain_light, exit_mode=exit_mode)


@router.post("/crosscheck/buy")
def crosscheck_buy(code: str = Query(...), price: Optional[float] = Query(None),
                   db: Session = Depends(get_db)):
    """Semi mode: the boss accepts a 🔔 3-agree BUY recommendation. `price` = the on-screen
    live price → the paper fill uses it (WYSIWYG) when within ±3% of the server price."""
    from services.cross_trader import semi_buy
    return semi_buy(db, code, ref_price=price)


@router.post("/crosscheck/sell")
def crosscheck_sell(code: str = Query(...), price: Optional[float] = Query(None),
                    db: Session = Depends(get_db)):
    from services.cross_trader import sell_all
    return sell_all(db, code, ref_price=price)


@router.post("/crosscheck/tick")
def crosscheck_tick(force: bool = Query(False), db: Session = Depends(get_db)):
    from services.cross_trader import tick
    return tick(db, force=force)


@router.get("/crosscheck/explain")
def crosscheck_explain(code: str = Query(...), lang: str = Query("ko"),
                       db: Session = Depends(get_db)):
    """Plain-language, jargon-free explanation of one stock's Cross-Check reasoning
    (deterministic templates, cache-only reads — fast)."""
    from services.cross_trader import explain
    return explain(db, code, lang)


@router.get("/executions")
def desk_executions(code: str = Query(...)):
    """체결 feed for the Algorithm-2 manual desk (boss 2026-07-14): the DEALS actually
    happening (Kiwoom ka10003, newest first, ~30 rows). dir +1 = buyer-initiated
    매수체결 (red), −1 = seller-initiated 매도체결 (blue)."""
    code = str(code).zfill(6)
    try:
        from services.kiwoom_rest import executions
        rows = executions(code, ttl=2.0) or []
    except Exception:
        rows = []
    src = "kiwoom"
    if not rows:
        # Kiwoom's tick API doesn't serve on Render — use the volume-delta feed
        # built by the fast price lane (also make sure it's sampling this code)
        from services.paper_desk import _deal_hist, _live_price
        _live_price(code)
        st = _deal_hist.get(code) or {}
        rows = list(st.get("rows") or [])
        src = "volume-delta"
        # diagnostic: does the price source even carry volume on this host?
        return {"code": code, "rows": rows[:30], "src": src,
                "last_vol": st.get("last_vol"), "last_px": st.get("last_px")}
    return {"code": code, "rows": rows[:30], "src": src}


@router.get("/prices")
def live_prices(codes: str = Query(..., description="comma-separated 6-digit codes, max 20")):
    """⚡ FAST PRICE LANE (boss 2026-07-14 / sped up 2026-07-22): the page's 1-second
    tick. Serves the warm cache INSTANTLY (never blocks on Kiwoom's ~1.5s REST) and
    kicks background refreshes so prices stay ~1s fresh during market hours. No DB, no
    signals. Returns {code: {price, chg, change_pct, ts, source}}."""
    from concurrent.futures import ThreadPoolExecutor
    from services.paper_desk import fast_price
    cs: list[str] = []
    for c in (codes or "").split(","):
        c = c.strip().zfill(6)
        if c.isdigit() and len(c) == 6 and c not in cs:
            cs.append(c)
    cs = cs[:20]
    out: dict[str, dict] = {}
    if cs:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for c, (px, chg, ts, src) in zip(cs, ex.map(fast_price, cs)):
                if px is not None:
                    out[c] = {"price": px, "chg": chg, "change_pct": chg,
                              "ts": round(ts, 3), "source": src}
    return {"prices": out}


@router.get("/auto/focus/detail")
def auto_focus_detail(code: str = Query(...), lang: str = Query("ko"),
                      db: Session = Depends(get_db)):
    """HOW THE ENGINE THINKS, in full (boss 2026-07-10): the same detailed report the
    chatbot gives — hybrid ML + market situation (KOSPI/KOSDAQ/oil/NASDAQ) + chart
    analysis + news + methods + ⚡ 1-hour view — for a focus panel's 상세 설명 button.
    decide_cached keeps it instant after the board's own poll warmed the cache."""
    from services.decision_agent import decide_cached
    d = decide_cached(db, str(code).zfill(6), ttl=180) or {}
    return {"code": str(code).zfill(6),
            "reply": d.get("reasoning_en" if str(lang).lower().startswith("en")
                           else "reasoning_ko") or ""}


# THE RECO RANK LOGGER rides the server's own lifetime (boss 2026-08-25,
# menu 2's living top-3): it polls our own daily-pick endpoint - zero new
# Kiwoom load - and records the ranking timeline the reco engine trades by.
try:
    from services.reco_rank_log import start_logger as _srl9
    _srl9()
except Exception:
    pass
