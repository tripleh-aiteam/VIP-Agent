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


@router.get("/trading-brief")
def trading_brief(horizon: int = Query(5), db: Session = Depends(get_db)):
    """The full short-term (단타) brief the Daily Trading UI renders:
    market regime + BUY/SELL picks with 박스권 trade levels + 수급 (who's buying) +
    impact-scored effective news + DART disclosures. All from data we already have."""
    from services.trading_brief import brief
    return brief(db, horizon=horizon)


@router.post("/collect-news")
def collect_news(limit: int = Query(5, description="how many stocks to collect (test small)"),
                 db: Session = Depends(get_db)):
    """Manually run the news+sentiment collector -> raw_news (normally a daily cron)."""
    from services.news_sentiment_collector import collect_all
    return collect_all(db, limit=limit)


@router.post("/collect-dart")
def collect_dart(days: int = Query(1), db: Session = Depends(get_db)):
    """Pull recent DART disclosures -> raw_disclosures (needs DART_API_KEY)."""
    from services.dart_collector import collect
    return collect(db, days=days)


@router.get("/analysis-batch")
def analysis_batch(tickers: str = Query(..., description="comma-separated tickers"),
                   horizon: int = Query(5), db: Session = Depends(get_db)):
    """METHOD 2 (Analysis) for many stocks in ONE server-side call — rule-based
    매수/관망/매도 from 호가+수급+박스권. Sequential + cached so the mock API isn't
    rate-limited by parallel client fetches (fixes the 'Awaiting live flows' gaps)."""
    from services.trading_brief import analysis_batch as _ab
    tk = [t.strip() for t in tickers.split(",") if t.strip()][:20]
    return {"results": _ab(db, tk, horizon)}


@router.get("/realtime/{ticker}")
def realtime_signals(ticker: str):
    """LIVE Kiwoom signals for one stock (order-book imbalance + intraday 수급 +
    program net). Fetched lazily by the Daily Trading UI per pick card so the main
    brief stays fast. Returns {live: false} when Kiwoom keys aren't set."""
    from services.trading_brief import realtime_for
    rt = realtime_for(ticker)
    return rt or {"live": False, "ticker": ticker}


@router.get("/{ticker}")
def prediction_ticker(ticker: str, horizon: int = Query(5), db: Session = Depends(get_db)):
    p = ps.get_ticker(db, ticker, horizon)
    return p or {"error": "no prediction for this ticker", "ticker": ticker}
