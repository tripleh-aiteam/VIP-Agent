"""/predictions — serve the ML model's daily BUY/SELL/HOLD calls to the dashboard,
chatbot, and reports. Read-only over model_predictions (written off-Render)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from db.base import get_db
from services import prediction_service as ps

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/summary")
def predictions_summary(horizon: int = Query(5), db: Session = Depends(get_db)):
    """Top BUY/SELL picks + counts — what the VIP Agent shows on open."""
    return ps.summary(db, horizon=horizon)


@router.get("/top")
def predictions_top(advice: str = Query("BUY"), n: int = Query(5),
                    horizon: int = Query(5), db: Session = Depends(get_db)):
    return {"advice": advice.upper(), "picks": ps.top_picks(db, advice, n, horizon)}


@router.get("")
def predictions_list(horizon: int = Query(5), advice: Optional[str] = Query(None),
                     db: Session = Depends(get_db)):
    return {"predictions": ps.list_predictions(db, horizon=horizon, advice=advice)}


@router.get("/{ticker}")
def prediction_ticker(ticker: str, horizon: int = Query(5), db: Session = Depends(get_db)):
    p = ps.get_ticker(db, ticker, horizon)
    return p or {"error": "no prediction for this ticker", "ticker": ticker}
