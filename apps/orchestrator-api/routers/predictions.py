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


@router.get("/readiness")
def readiness(db: Session = Depends(get_db)):
    """Phase C gate: is the chatbot's measured record good enough for real money?
    (100+ decisive scalp calls at 58%+ win rate after costs.) GO / NO_GO / COLLECTING."""
    from services.readiness import report
    return report(db)


@router.post("/self-tune")
def self_tune_ep(db: Session = Depends(get_db)):
    """Autopilot B: run one bounded tuning pass now (normally nightly 16:40 KST)."""
    from services.self_tune import run
    return run(db)


@router.post("/paper/tick")
def paper_tick_ep(force: bool = Query(False), db: Session = Depends(get_db)):
    """One paper-trader pass (close hits/timeouts, open from bot signals). External-cron
    safe; force=true runs the open/close logic outside market hours (testing)."""
    from services.paper_trader import tick
    return tick(db, force=force)


@router.get("/paper/scorecard")
def paper_scorecard_ep(days: int = Query(1), db: Session = Depends(get_db)):
    """Virtual P&L — 'if you had followed the bot': recent window + cumulative + per strategy."""
    from services.paper_trader import scorecard
    return scorecard(db, days=days)


@router.post("/paper/report")
def paper_report_ep(db: Session = Depends(get_db)):
    """Send the morning scorecard email now (normally 08:20 KST cron)."""
    from services.paper_trader import morning_report
    return morning_report(db)


@router.post("/dip-alert")
def dip_alert_ep(force: bool = Query(False), db: Session = Depends(get_db)):
    """One proactive dip-bounce alert pass (scan → dedup → email new candidates).
    Fire every ~10 min during market via external cron; force=true tests after hours."""
    from services.dip_alert import run
    return run(db, force=force)


@router.get("/dip-bounce")
def dip_bounce_ep(min_dip: float = Query(1.5), db: Session = Depends(get_db)):
    """Dip-bounce hunter: stocks down ≥min_dip% vs ~1h ago + tape confirmation, with
    entry/target/stop. Measured basis: 81.7% next-hour-up after ≥1.5%/1h dips (n=71).
    Candidates are NOT logged for grading here (the chat intent logs) — pass-through view."""
    from services.dip_bounce import scan
    return scan(db, min_dip=min_dip, log_calls=False)


@router.get("/route-debug")
def route_debug(q: str = Query(...), agent: str = Query("stock")):
    """Routing predicate breakdown for a question — shows exactly why a query routes
    where it does ON THIS DEPLOY (prod-vs-local routing mysteries die here)."""
    from services import assistant_agent as aa
    from services.stock_resolver import resolve_one
    t = (q or "").lower()
    out = {
        "wants_recommendation": aa._wants_recommendation(q),
        "is_decision_q": aa._is_decision_q(q),
        "is_past_price": aa._is_past_price(q),
        "has_explicit_date": aa._has_explicit_date(q),
        "past_kw_hits": [k for k in aa._PAST_DATE_KW if k in t],
        "pricey_hits": [k for k in aa._PRICEY_KW if k in t],
        "stock_in_query": aa._stock_in_query(q),
        "resolve_one": resolve_one(q),
        "is_watchlist": aa._is_watchlist_question(q),
        "is_stock_advice": aa._is_stock_advice(q, agent),
        "is_future_outlook": aa._is_future_outlook(q),
    }
    return out


@router.get("/strategy-params")
def strategy_params_ep(db: Session = Depends(get_db)):
    """Current Autopilot-tuned strategy parameters + last tuning note. NOTE: must be
    registered BEFORE the /{ticker} catch-all or that route swallows the path."""
    from services.self_tune import latest_note, params_get
    return {"dip_bounce": params_get(db, "dip_bounce"), "last_tune": latest_note(db)}


@router.get("/method-weights")
def method_weights_ep(db: Session = Depends(get_db)):
    """M5.7 transparency: the live track-record-derived weights + intraday tier stats
    (rolling windows, straight from the graded tables)."""
    from services.method_weights import daily_stats, fusion_weights, intraday_stats
    return {"fusion": fusion_weights(db), "intraday": intraday_stats(db),
            "daily": daily_stats(db)}


@router.get("/checklist/{ticker}")
def checklist_ep(ticker: str, db: Session = Depends(get_db)):
    """The boss's 100-item pre-trade checklist, agent-run for ONE stock: market pre-flight
    + per-stock scorecard + deal-breakers + honest '확인 불가' items. Powers the chatbot
    '체크리스트' intent and (future) a dashboard card."""
    from services.checklist_engine import stock_scorecard
    return stock_scorecard(db, str(ticker).zfill(6))


@router.get("/checklist-market")
def checklist_market_ep(db: Session = Depends(get_db)):
    """Market-only pre-flight (오늘 매매하기 좋은 날인가?) — cached ~5 min."""
    from services.checklist_engine import market_preflight
    return market_preflight(db)


@router.post("/collector/pass")
def collector_pass(force: bool = Query(False)):
    """Cloud collection pass — the server polls Kiwoom itself (Render IPs are
    registered), so live data flows with NO PC involved. Fire every ~2 min during
    market via external cron. Skips when a fresh snapshot exists (PC collector
    active) — automatic failover, never double work."""
    from services.cloud_collector import run_pass
    return run_pass(force=force)


@router.post("/intraday/bank")
def intraday_bank(db: Session = Depends(get_db)):
    """B2 prep: append fresh order-flow snapshots to intraday_snapshot_history — the
    training series for the next-30-min model. Fire every 5 min during market via
    external cron (idempotent; the in-process scheduler also runs it)."""
    from services.snapshot_bank import bank
    return bank(db)


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


@router.get("/wave/{ticker}")
def wave_method(ticker: str, db: Session = Depends(get_db)):
    """METHOD 3 ("Wave"): rule-based Elliott/Fibonacci deep-pullback verdict — strong
    rally (6-D wave score) + current price in the deep-pullback Fib zone → BUY/WATCH/
    AVOID with entry/stop/target/R:R. Read from wave_features_daily (banked off-Render)."""
    from services.wave_method import wave_for
    return wave_for(db, str(ticker).zfill(6))


# Extra names covered by Method 3 (Wave) ONLY — not in Methods 1/2 (STARTER_KR/NAMES).
# Method 3 is a wider scanner: it surfaces strong-rally pullbacks the other two don't track.
WAVE_EXTRA_NAMES = {
    "066570": "LG전자", "036570": "엔씨소프트", "352820": "하이브", "259960": "크래프톤",
    "323410": "카카오뱅크", "003670": "포스코퓨처엠", "051900": "LG생활건강", "090430": "아모레퍼시픽",
    "097950": "CJ제일제당", "017670": "SK텔레콤", "030200": "KT", "008770": "호텔신라",
}


@router.get("/wave-batch")
def wave_batch(tickers: str = Query(""), db: Session = Depends(get_db)):
    """METHOD 3 ("Wave") for the FULL wave universe (or a comma list) — for the Daily
    Trading 'Wave' view. Scans every stock with wave data (incl. names not in Methods
    1/2). Sorted by wave_score desc; drops the index proxy + names with no rally yet."""
    from sqlalchemy import text
    from services.wave_method import wave_for
    from services.prediction_service import NAMES
    if tickers.strip():
        tk = [t.strip().zfill(6) for t in tickers.split(",") if t.strip()]
    else:
        tk = [r[0] for r in db.execute(text("SELECT DISTINCT ticker FROM wave_features_daily")).fetchall()]
    out = []
    for t in tk[:80]:
        if t == "069500":           # KODEX 200 = index ETF, not a tradable pick
            continue
        v = wave_for(db, t)
        if v.get("verdict") not in (None, "N/A"):
            v["name"] = NAMES.get(t) or WAVE_EXTRA_NAMES.get(t, t)
            out.append(v)
    out.sort(key=lambda o: -(o.get("wave_score") or 0))
    return {"results": out}


@router.get("/news-universe")
def news_universe():
    """Stocks for the Report → News dropdown: [{code, name}] (tracked + Wave-extra)."""
    from services.stock_news import universe
    return {"stocks": universe()}


@router.get("/stock-news/{ticker}")
def stock_news_ep(ticker: str, days: int = Query(7), limit: int = Query(15), db: Session = Depends(get_db)):
    """A LIST of recent Korean news for one stock (real headlines, clickable). Report panel."""
    from services.stock_news import stock_news
    return stock_news(db, str(ticker).zfill(6), days=days, limit=limit)


@router.get("/stock-news/{ticker}/summary")
def stock_news_summary_ep(ticker: str, days: int = Query(7), limit: int = Query(15), db: Session = Depends(get_db)):
    """On-demand AI summary of the stock's recent news (the '요약 보기' button)."""
    from services.stock_news import news_summary
    return news_summary(db, str(ticker).zfill(6), days=days, limit=limit)


@router.get("/scalp/{ticker}")
def scalp(ticker: str, target: float = Query(1.0), db: Session = Depends(get_db)):
    """M3 live scalp signal: 진입/대기 + 매수가/목표(+target%)/손절/예상시간, gated by
    Method 1 & 3 bias. Naver/daily fallback when the minute collector is off."""
    from services.day_trade import scalp_signal
    return scalp_signal(db, str(ticker).zfill(6), float(target))


@router.post("/chatbot-grade")
def chatbot_grade(void_graded_before: str | None = Query(None), db: Session = Depends(get_db)):
    """Grade chatbot advice calls whose horizon elapsed (M1.2). Fire every ~20-30 min
    during market via external cron so the hit-rate matures.

    void_graded_before (ISO date, one-time maintenance): mark rows graded BEFORE that
    date as outcome='void' so calls graded under old, buggy logic stop polluting the
    readiness gate — the track record restarts clean from the fixed grader."""
    from sqlalchemy import text as _t
    from services.call_grader import grade_open
    voided = 0
    if void_graded_before:
        r = db.execute(_t(
            "UPDATE chatbot_calls SET outcome='void' WHERE status='graded' "
            "AND outcome != 'void' AND graded_ts < :d"), {"d": void_graded_before})
        db.commit()
        voided = r.rowcount or 0
    out = grade_open(db)
    if void_graded_before:
        out["voided"] = voided
    return out


@router.get("/chatbot-scoreboard")
def chatbot_scoreboard(days: int = Query(30), db: Session = Depends(get_db)):
    """Chatbot advice hit-rate (overall + per intent) — the honest 'can I trust it' panel."""
    from services.call_grader import scoreboard
    return scoreboard(db, days=days)


@router.post("/recommendation-report/run")
def recommendation_report_run(email: bool = Query(True), db: Session = Depends(get_db)):
    """Build the daily 3-method Top-5 Recommendation Report (ML + Analysis + Wave +
    Kiwoom/Newspaper/YouTube backdrop). If email=true, sends the .docx to the pilot
    recipient(s). Exposed so a FREE external cron can fire it at 07:30 KST (Render's
    free tier sleeps). Pilot: owner-only until sign-off."""
    from services.recommendation_report import send, build
    if email:
        return send(db)
    return build(db)


@router.get("/ws-debug")
def ws_orderbook_debug():
    """Live state of the WebSocket order-book collector — connected, logged in,
    receiving, writing — plus a real 0D frame to verify FIDs. Gated behind
    DEBUG_KIWOOM (operational diagnostics are not public). Login data is sanitized
    to non-sensitive status fields; no tokens/secrets are returned."""
    import os
    from fastapi import HTTPException
    if os.getenv("DEBUG_KIWOOM", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="Not found")
    from services.ws_orderbook_collector import status
    return status()


@router.post("/story/seed-mega")
def story_seed_mega(db: Session = Depends(get_db)):
    """Seed the priority story: the 3 Mega Investment Projects (정부·삼성·SK하이닉스)."""
    from services.story_tracker import seed_story, MEGA_PROJECTS as M
    return seed_story(db, M["key"], M["topic"], M["queries"], M["tickers"])


@router.post("/story/monitor")
def story_monitor(email: bool = Query(False), db: Session = Depends(get_db)):
    """Update every tracked story (raw_news + web search) and, if email=true, send an
    executive brief of NEW developments to management. Fire daily via cron/scheduler."""
    from services.story_tracker import daily_monitor
    return daily_monitor(db, email=email)


@router.get("/story/brief/{key}")
def story_brief(key: str, lang: str = Query("ko"), db: Session = Depends(get_db)):
    """The current executive brief for a tracked story (LLM summary of accumulated news)."""
    from services.story_tracker import update_story, executive_brief
    update_story(db, key)
    return executive_brief(db, key, lang=lang)


@router.get("/investor-flows")
def investor_flows():
    """Market-wide 투자자별 매매 — net buying by investor type (개인/외국인/기관계 + 금융투자/
    보험/투신/연기금/사모펀드…) for KOSPI + KOSDAQ. Source: Naver (works in/after market)."""
    from services.market_investor_flows import market_flows
    return market_flows()


@router.get("/minute-bars/{ticker}")
def minute_bars_view(ticker: str, db: Session = Depends(get_db)):
    """Today's per-minute candles + the day's VOLATILITY BASELINE (realized range,
    1σ expected day move from minute vol, opening range, position-in-range). Feeds the
    intraday chart + the day-trade feasibility answer. Populated by the PC collector."""
    from services.minute_bars import read_bars, intraday_vol
    tk = str(ticker).zfill(6)
    return {"ticker": tk, "bars": read_bars(db, tk), "vol": intraday_vol(db, tk)}


@router.get("/day-trade/{ticker}")
def day_trade_feasibility(ticker: str, target: float = Query(1.0), db: Session = Depends(get_db)):
    """Answers 'can I buy {stock} near its intraday bottom and sell +{target}% today?' with
    a buy zone (near low / support wall), sell target, stop-loss, R:R and a feasible verdict —
    all from today's measured intraday volatility + the remembered order-book support."""
    from services.day_trade import feasibility
    return feasibility(db, str(ticker).zfill(6), float(target))


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
