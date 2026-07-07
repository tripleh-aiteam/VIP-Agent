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
    """Live price + resolved name for the order box (any code or name)."""
    from services.paper_desk import _live_price, _name_for
    code = _resolve(q, db)
    if not code.isdigit():
        return {"ok": False, "error": f"'{q}' 종목을 찾지 못했어요"}
    px, kw_name = _live_price(code)
    if px is None:
        return {"ok": False, "error": f"{code} 시세를 가져오지 못했어요"}
    return {"ok": True, "ticker": code, "name": _name_for(code, kw_name), "price": px}


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
