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
