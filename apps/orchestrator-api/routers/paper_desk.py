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


@router.get("/live/rules")
def live_rules(tick: int = Query(5), period: int = Query(0), day: str = Query(""),
               frm: str = Query(""), to: str = Query(""), gate: int = Query(1),
               auto: int = Query(1)):
    """The same rules the Strategy Lab runs, over the REAL Kiwoom tape. No ML here — the
    boss asked for the plain rules on real data first, which is the right order."""
    from services.kiwoom_rules import rank
    # gate=0 shows what the rules WOULD have done on a day the gate closed - a viewing
    # switch only; the desk itself always trades gated (boss 2026-08-10)
    # auto=0: the user explicitly chose TODAY, so an empty board is the honest answer -
    # never yesterday's trades under today's label (boss 2026-08-11, three times)
    return rank(tick=tick, period=max(0, min(int(period or 0), 600)),
                day=day, frm=frm, to=to, use_gate=bool(gate),
                allow_fallback=bool(auto))


@router.get("/live/rules/trades")
def live_rule_trades(variant: str = Query(...), tick: int = Query(5),
                     period: int = Query(0), code: str = Query(""),
                     bars: int = Query(2500), limit: int = Query(300),
                     around: int = Query(-1), budget: int = Query(0),
                     day: str = Query(""), frm: str = Query(""), to: str = Query(""),
                     gate: int = Query(1), auto: int = Query(1)):
    """One rule's real trades: what it bought, at what, when, and why.

    `budget` is won per trade — 0 means the historical one share. It scales the money and
    never the win rate, which is the point of being able to set it (boss 2026-08-04)."""
    from services.kiwoom_rules import trades
    return trades(variant, tick=tick, period=max(0, min(int(period or 0), 600)),
                  code=code, bars=bars, limit=limit, around=around,
                  budget=max(0, min(int(budget or 0), 1_000_000_000)),
                  day=day, frm=frm, to=to, use_gate=bool(gate),
                  allow_fallback=bool(auto))


_FAM_TTL: dict = {}


@router.get("/live/rules/family-trades")
def live_family_trades(family: str = Query("new"), tick: int = Query(5),
                       period: int = Query(0), day: str = Query(""),
                       frm: str = Query(""), to: str = Query(""),
                       gate: int = Query(1), auto: int = Query(1)):
    """EVERY trade of one family in one table (boss 2026-08-11: rule, stock, buy, sell,
    result, money - across the whole family, not one rule at a time). Rows carry the
    rule id and the trade's index inside that rule's own list, so the page can open the
    exact trade on the chart as proof with the machinery it already has."""
    from services.kiwoom_rules import DESK, trades
    # 4-second answer cache: the page polls this every 20s while the same computation
    # also feeds the 3s rules poll, and the boss felt the wait (2026-08-11) - the
    # holdings used to be a SECOND full pass per rule, which doubled the work for
    # nothing since trades() already returns them
    import time as _t
    _fk = (family, tick, period, day, frm, to, gate, auto)
    _hit = _FAM_TTL.get(_fk)
    if _hit and _t.time() - _hit[0] < 20.0:
        return _hit[1]
    rows = []
    holding = []
    for v in DESK:
        if family != "all" and v.get("family", "old") != family:
            continue
        d = trades(v["id"], tick=tick, period=max(0, min(int(period or 0), 600)),
                   bars=10, limit=500, day=day, frm=frm, to=to,
                   use_gate=bool(gate), allow_fallback=bool(auto))
        if not d.get("ok"):
            continue
        for h in (d.get("holding") or []):
            holding.append(dict(h, rule=v["id"]))
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
                         "parts": tr.get("parts")})
    # newest first (boss 2026-08-11) - the top of the table is what just happened
    rows.sort(key=lambda r: (r.get("d8") or "", r.get("buy_t") or ""), reverse=True)
    w = sum(1 for r in rows if r["result"] == "win")
    l = sum(1 for r in rows if r["result"] == "loss")
    _res = {"ok": True, "family": family, "rows": rows,
            "trips": len(rows), "wins": w, "losses": l,
            "win_pct": round(w / (w + l) * 100) if (w + l) else 0,
            "holding": holding,
            "net_won": sum(r["won"] for r in rows)}
    _FAM_TTL[_fk] = (_t.time(), _res)
    return _res


@router.get("/live/dip-status")
def live_dip_status(tick: int = Query(5), period: int = Query(0)):
    """Per stock, where the new rule's hunt stands right now - so a quiet board reads
    as "condition not met yet at 삼성전자: waiting for a sharp drop" instead of broken."""
    from services.kiwoom_rules import dip_status
    return dip_status(tick=tick, period=max(0, min(int(period or 0), 600)))


@router.post("/desk-mode")
def desk_mode_set(mode: str = Query(...), force: int = Query(0)):
    """Switch the whole desk between the boss's six and the checklist's top five.
    One is always OFF: the collector follows whichever is set. During market hours the
    swap needs force=1, because re-pointing mid-session abandons the tape already
    collected for the stocks that leave."""
    from services.daily_pick import desk_mode, save_picks, set_desk_mode
    from services.kiwoom_tape import WATCH, _day, market_open, refresh_watch
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
                r = live_family_trades(family=family, tick=tick, period=period,
                                       day=d, frm="", to="", gate=1, auto=0)
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


@router.get("/daily-pick")
def daily_pick_today(day: str = Query(""), refresh: int = Query(0),
                     force: int = Query(0)):
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
