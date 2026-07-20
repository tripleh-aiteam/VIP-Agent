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
    src = source if source in ("manual", "algo1", "algo2", "guard") else "algo1"
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
    for src, tbl in (("algo2", "scalp_trades"), ("algo3", "candle_trades")):
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
    return place_order(db, code, body.side, body.qty,
                       order_type=body.order_type, limit_price=body.limit_price)


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
    """Intraday candles for the inline focus chart (boss 2026-07-10: click the stock →
    live chart between Place Order and Trade History). tf=5m: our collected 5-min bars
    (today + 1yr history); tf=1h: the same bars aggregated. Daily/weekly/monthly come
    from /predictions/stock-detail."""
    from services.cycle_scalp import _bars
    code = str(code).zfill(6)
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
    try:
        from services.auto_trader import tick as _a1_tick   # Algo 1 entries (was never ticked)
        _a1_tick(db)
    except Exception:
        db.rollback()
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
                   db: Session = Depends(get_db)):
    """Algorithm 3 dials: stop %, size %, stock list, mode, streak (2/3), tf (1/3/5-min)."""
    from services.candle_trader import set_params
    return set_params(db, stop_pct=stop_pct, pos_pct=pos_pct, codes=codes, mode=mode, streak=streak, tf=tf)


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
    """⚡ FAST PRICE LANE (boss 2026-07-14: '현재가 changes very slow vs Kiwoom').
    The focus board recomputes every 60s (heavy engine work) — the page overlays
    prices from THIS every ~3s instead. Kiwoom-first via _live_price (2s
    micro-cache), parallel fan-out, no DB."""
    from concurrent.futures import ThreadPoolExecutor
    from services.paper_desk import _live_price, _chg_cache
    cs: list[str] = []
    for c in (codes or "").split(","):
        c = c.strip().zfill(6)
        if c.isdigit() and len(c) == 6 and c not in cs:
            cs.append(c)
    cs = cs[:20]
    out: dict[str, dict] = {}
    if cs:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for c, (px, _name) in zip(cs, ex.map(_live_price, cs)):
                if px is not None:
                    out[c] = {"price": px, "chg": _chg_cache.get(c)}
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
