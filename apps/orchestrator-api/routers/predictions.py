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


@router.get("/scoreboard")
def scoreboard(db: Session = Depends(get_db)):
    """Track record per method — win rate, profit rate, avg return + recent graded calls.
    This is the honest 'does it actually work' panel; accumulates as days pass."""
    from services.scorekeeper_service import scoreboard as _sb
    return _sb(db)


@router.post("/scorekeeper/run")
def scorekeeper_run(db: Session = Depends(get_db)):
    """Manually log today's signals + grade matured ones (normally a daily cron)."""
    from services.scorekeeper_service import log_today, grade_matured
    return {"log": log_today(db), "grade": grade_matured(db)}


@router.get("/realtime/{ticker}")
def realtime_signals(ticker: str, db: Session = Depends(get_db)):
    """LIVE signals for one stock (order-book imbalance + intraday 수급 + program net),
    read from the realtime_snapshot table the PC collector writes. Returns {live: false}
    when no fresh snapshot exists (collector off / after market)."""
    from services.trading_brief import realtime_for
    rt = realtime_for(ticker, db=db)
    return rt or {"live": False, "ticker": ticker}


@router.post("/scorekeeper/run")
def scorekeeper_run(db: Session = Depends(get_db)):
    """Log today's signals + grade matured ones (incl. beta-adjusted skill vs market).
    Exposed so a FREE external cron (cron-job.org / GitHub Actions) can fire it daily —
    Render's free tier sleeps, so the in-process 16:45 KST cron misses days. Idempotent."""
    from services.scorekeeper_service import log_today, grade_matured
    return {"logged": log_today(db), "graded": grade_matured(db)}


@router.post("/intraday/tick")
def intraday_tick(db: Session = Depends(get_db)):
    """One market-hour pass of the 2-method hourly forward test: grade matured forecasts,
    then predict the next ~1h with BOTH methods. Hit hourly during market (09–15 KST) by a
    FREE external cron (cron-job.org) so it survives Render free-tier sleep. Idempotent."""
    from services.intraday_forecast import tick
    return tick(db)


@router.post("/intraday/morning-report")
def intraday_morning_report(email: str | None = Query(None), db: Session = Depends(get_db)):
    """Email yesterday's hourly accuracy scorecard (.docx) to davronbekmalikov96@gmail.com
    before market open. Fire daily ~08:00 KST via external cron (or the in-process job)."""
    from services.intraday_forecast import morning_report
    return morning_report(db, email)


@router.get("/intraday/scorecard")
def intraday_scorecard(db: Session = Depends(get_db)):
    """Latest graded day's hourly accuracy (both methods) as JSON — for a dashboard panel."""
    from services.intraday_forecast import _scorecard, _ensure_table
    from sqlalchemy import text as _t
    _ensure_table(db)
    last = db.execute(_t("SELECT max(made_date) FROM intraday_forecasts WHERE status='graded'")).scalar()
    return _scorecard(db, last) if last else {"total": 0, "methods": {}}


@router.get("/orderbook/{ticker}")
def orderbook_depth(ticker: str, depth: int = Query(30), db: Session = Depends(get_db)):
    """Deep order-book view: LIVE 10 bid/ask levels + the ±depth-level MEMORY of
    levels that scrolled out over time (the 'disappearing levels' the agent remembers)
    + large-order walls. 키움 실시간 during market, NAVER after. depth up to 30/side."""
    from services.orderbook_memory import orderbook_view
    return orderbook_view(db, str(ticker).zfill(6), min(max(depth, 5), 30))


@router.get("/minute-bars/{ticker}")
def minute_bars_view(ticker: str, db: Session = Depends(get_db)):
    """Today's per-minute candles + the day's VOLATILITY BASELINE (realized range,
    1σ expected day move from minute vol, opening range, position-in-range). Feeds the
    intraday chart + the day-trade feasibility answer. Populated by the PC collector."""
    from services.minute_bars import read_bars, intraday_vol
    tk = str(ticker).zfill(6)
    return {"ticker": tk, "bars": read_bars(db, tk), "vol": intraday_vol(db, tk)}


@router.get("/stock-detail/{ticker}")
def stock_detail(ticker: str, db: Session = Depends(get_db)):
    """Rich single-stock detail for the click-through detail view — OHLC, 등락%, 거래량,
    기간 고저, NXT 시간외, 수급, LIVE 호가/수급 (Kiwoom 실전 via snapshot), and 개별주식
    선물·옵션 when available. Kiwoom 실전 during market, Naver after."""
    from services.trading_brief import stock_detail as _detail
    return _detail(db, ticker)


@router.get("/{ticker}")
def prediction_ticker(ticker: str, horizon: int = Query(5), db: Session = Depends(get_db)):
    p = ps.get_ticker(db, ticker, horizon)
    return p or {"error": "no prediction for this ticker", "ticker": ticker}
