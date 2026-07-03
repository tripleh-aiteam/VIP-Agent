"""
stock_data_tools — live-data read tools for the OASIS Stock Advisor agent.

These let the assistant fetch REAL-TIME data from the Stock Advisor backend
("Investment Engine API" on Render) on demand, instead of relying only on the
page-DOM snapshot. The LLM auto-discovers them via the global TOOL_REGISTRY
(see assistant_tools.py); each is description-gated so only the Stock agent's
conversations naturally trigger them.

Why this exists: the Stock app's real-time data (recommendations, intraday
signals, market flows, watchlist, portfolio, ownership, alerts, news) lives in
a separate FastAPI service the assistant could not see. These tools bridge that
gap — every result is timestamped so the assistant can say "as of HH:MM KST".

Config (env on the orchestrator / Render):
  - STOCK_BACKEND_URL        default https://stock-advisor-agent-9qwi.onrender.com
  - STOCK_ASSISTANT_API_KEY  optional shared secret; sent as X-Assistant-Key.
                             Backend enforces it only when its own
                             ASSISTANT_API_KEY env is set (opt-in).

Registration is done by assistant_tools.py calling register_stock_data_tools()
so there is no circular import.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

from services.logger import log

# Korea Standard Time — stock data is Korean-market-centric.
_KST = timezone(timedelta(hours=9))

_DEFAULT_BACKEND = "https://stock-advisor-agent-9qwi.onrender.com"


def _backend_url() -> str:
    return (os.environ.get("STOCK_BACKEND_URL") or _DEFAULT_BACKEND).rstrip("/")


def _now_kst_iso() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _get(path: str, params: dict | None = None, timeout: float = 20.0) -> dict[str, Any]:
    """GET <backend><path> with the optional shared-secret header.

    Always returns a dict with an `ok` key — never raises. On success the
    payload is wrapped with `fetched_at` (KST) so the LLM can disclose
    freshness. Render free tier can cold-start, hence the generous timeout.
    """
    import httpx

    url = f"{_backend_url()}{path}"
    headers = {"Accept": "application/json"}
    key = os.environ.get("STOCK_ASSISTANT_API_KEY")
    if key:
        headers["X-Assistant-Key"] = key

    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(url, params={k: v for k, v in (params or {}).items() if v is not None}, headers=headers)
    except Exception as e:  # network / timeout / DNS
        return {
            "ok": False,
            "error": f"Could not reach Stock backend: {str(e)[:160]}",
            "fetched_at": _now_kst_iso(),
        }

    if r.status_code == 401:
        return {"ok": False, "error": "Stock backend rejected the assistant key (401).", "fetched_at": _now_kst_iso()}
    if r.status_code >= 400:
        return {"ok": False, "error": f"Stock backend HTTP {r.status_code}", "fetched_at": _now_kst_iso()}

    try:
        data = r.json()
    except Exception:
        return {"ok": False, "error": "Stock backend returned non-JSON", "fetched_at": _now_kst_iso()}

    return {"ok": True, "fetched_at": _now_kst_iso(), "data": data}


# ============================================================================
#  Tool functions — each returns a dict; `db`/`agent_id` kwargs are injected by
#  execute_tool and simply ignored here (these tools are inherently stock-only).
# ============================================================================

def tool_stock_recommendations(market: str | None = None, status: str = "active", limit: int = 50, **_kw) -> dict[str, Any]:
    """Current AI stock recommendations (picks) with live returns."""
    return _get("/recommendations", {"market": market, "status": status, "limit": limit})


def tool_stock_portfolio(**_kw) -> dict[str, Any]:
    """The user's current portfolio positions / holdings."""
    return _get("/portfolio/positions")


def tool_stock_intraday_signals(ticker: str | None = None, status: str | None = None, limit: int = 50, **_kw) -> dict[str, Any]:
    """Real-time intraday trade signals (bullish/bearish/risk) for the watchlist.

    The LLM sometimes invents args (a Korean phrase as `ticker`, a made-up `status`) and
    the backend 422s — sanitize first, and if a filtered call still 4xxes, retry BARE so
    the user gets data instead of an apology."""
    tk = str(ticker).strip() if ticker else None
    if tk and not (tk.isdigit() and len(tk) == 6):
        try:
            from services.stock_resolver import resolve_one
            tk = (resolve_one(tk) or (None,))[0]
        except Exception:
            tk = None
    st = status if status in ("active", "closed", "all") else None
    try:
        lim = max(1, min(int(limit or 50), 100))
    except Exception:
        lim = 50
    r = _get("/intraday/signals", {"ticker": tk, "status": st, "limit": lim})
    if not r.get("ok") and "HTTP 4" in str(r.get("error", "")):
        r = _get("/intraday/signals", None)
    return r


def tool_stock_intraday_status(**_kw) -> dict[str, Any]:
    """Whether intraday monitoring is live + its config (mode, watchlist size)."""
    return _get("/intraday/status")


def tool_stock_market_summary(**_kw) -> dict[str, Any]:
    """Overall market snapshot — index levels / asset summary."""
    return _get("/market/assets/summary")


def tool_stock_foreign_flow(direction: str | None = None, **_kw) -> dict[str, Any]:
    """Foreign-investor net buy/sell flow. Pass direction='buy' or 'sell' for top movers."""
    if direction in ("buy", "sell"):
        return _get(f"/market/foreign-flow/top-{direction}")
    return _get("/market/foreign-flow")


def tool_stock_investor_flow(**_kw) -> dict[str, Any]:
    """Investor-flow dashboard — 외국인 / 기관 / 개인 net buy/sell by symbol."""
    return _get("/dashboard/investor-flow")


def tool_stock_volume_spikes(**_kw) -> dict[str, Any]:
    """Stocks with unusual trading-volume spikes right now."""
    return _get("/market/volume-spikes")


def tool_stock_price_history(limit: int = 60, hours: int | None = None, **_kw) -> dict[str, Any]:
    """Recorded 5-minute price snapshots (time series) the agent stores silently.
    Use to analyze trends over time, compare to a few minutes/hours ago."""
    params: dict[str, Any] = {"limit": limit}
    if hours is not None:
        params["hours"] = hours
    return _get("/market/snapshots", params)


def tool_stock_watchlist(**_kw) -> dict[str, Any]:
    """The symbols the user is actively tracking."""
    return _get("/watchlist")


def tool_stock_alerts(**_kw) -> dict[str, Any]:
    """Triggered price/condition alerts."""
    return _get("/alerts")


def tool_stock_ownership_changes(**_kw) -> dict[str, Any]:
    """Recent major-holder ownership change events (5% rule / insider)."""
    return _get("/ownership/change-events")


def tool_stock_news(**_kw) -> dict[str, Any]:
    """Latest Korean market news feed."""
    return _get("/market/news")


# ============================================================================
#  Current quote by name/ticker (resolves a company name → ticker → live price)
# ============================================================================

# Major KR names (KO + EN aliases) → 6-digit ticker. The backend symbol-search
# only matches by ticker code, so we map common names here. Extend as needed.
_NAME_TO_TICKER: dict[str, str] = {
    "삼성전자": "005930", "samsung electronics": "005930", "samsung": "005930",
    "sk하이닉스": "000660", "하이닉스": "000660", "sk hynix": "000660", "hynix": "000660",
    "삼성전기": "009150", "samsung electro-mechanics": "009150", "samsung electro mechanics": "009150", "samsung electromechanics": "009150",
    "sk스퀘어": "402340", "sk square": "402340", "sksquare": "402340",
    "lg에너지솔루션": "373220", "lg energy": "373220", "lges": "373220",
    "삼성바이오로직스": "207940", "samsung biologics": "207940",
    "현대차": "005380", "현대자동차": "005380", "hyundai motor": "005380", "hyundai": "005380",
    "기아": "000270", "kia": "000270",
    "셀트리온": "068270", "celltrion": "068270",
    "naver": "035420", "네이버": "035420",
    "카카오": "035720", "kakao": "035720",
    "posco홀딩스": "005490", "posco": "005490", "포스코": "005490",
    "lg화학": "051910", "lg chem": "051910",
    "삼성sdi": "006400", "samsung sdi": "006400",
    "현대모비스": "012330", "hyundai mobis": "012330",
    "kb금융": "105560", "kb financial": "105560",
    "신한지주": "055550", "shinhan": "055550",
    "삼성물산": "028260",
    "카카오뱅크": "323410", "kakaobank": "323410",
    "크래프톤": "259960", "krafton": "259960",
    "한화에어로스페이스": "012450", "hanwha aerospace": "012450",
    "hmm": "011200",
    "두산에너빌리티": "034020", "doosan enerbility": "034020",
    "lg전자": "066570", "lg electronics": "066570",
    "sk이노베이션": "096770", "sk innovation": "096770",
    "한국전력": "015760", "kepco": "015760",
    "현대로템": "064350", "hyundai rotem": "064350",
    "삼성생명": "032830", "lg유플러스": "032640", "kt": "030200",
}


def _resolve_ticker(query: str) -> tuple[str | None, str | None]:
    """Return (ticker, matched_name) for a name or 6-digit code."""
    q = (query or "").strip()
    if q.isdigit() and len(q) == 6:
        return q, None
    low = q.lower()
    if low in _NAME_TO_TICKER:
        return _NAME_TO_TICKER[low], q
    # comprehensive resolver (slang 하닉/삼전 + all-51 + fuzzy typo 삼썽전자→삼성전자)
    try:
        from services.stock_resolver import resolve_one
        _c, _n = resolve_one(q)
        if _c:
            return _c, (_n or q)
    except Exception:
        pass
    # loose contains-match (longest alias first). Require ≥2 chars so an empty or
    # 1-char query can't false-match (an empty string is a substring of every name).
    if len(low) >= 2:
        for name in sorted(_NAME_TO_TICKER, key=len, reverse=True):
            if name in low or low in name:
                return _NAME_TO_TICKER[name], name
    return None, None


def tool_stock_quote(query: str = "", ticker: str = "", user_transcript: str = "", **_kw) -> dict[str, Any]:
    """Current price of ONE named stock. Resolves a company name (SK Hynix /
    SK하이닉스 / 삼성전자 / Samsung) or a 6-digit ticker, then quotes through the
    SAME Kiwoom price layer the reports use (KST-aware settled/live close + US
    fallback) — so every chatbot returns the SAME correct price, even off-hours."""
    q = (ticker or query or "").strip()
    code, matched = _resolve_ticker(q)
    if not code and user_transcript:
        # The LLM sometimes passes a garbage arg — recover from the real message.
        code, matched = _resolve_ticker(user_transcript)
    if not code:
        return {"ok": False, "fetched_at": _now_kst_iso(),
                "error": f"Couldn't resolve '{q}' to a ticker. Give the 6-digit "
                         "code (e.g. 000660) or a major KR company name."}
    try:
        from services import kiwoom_report
        quote = kiwoom_report.get_quote(code, ko=(matched or ""))
    except Exception as e:
        quote = None
        log.warning(f"stock_quote get_quote failed for {code}: {e}")
    if not quote or quote.get("price") is None:
        return {"ok": False, "fetched_at": _now_kst_iso(),
                "error": f"No price available for {matched or code} ({code}) right now."}
    price = quote["price"]
    is_kr = quote["market"] == "KR"
    kind = {"live": "현재가(장중)", "close": "종가", "prev_close": "전일 종가",
            "google": "최근 종가"}.get(quote.get("price_kind"), "")
    chg = quote.get("change_pct")
    chg_r = round(chg, 2) if isinstance(chg, (int, float)) else None
    return {
        "ok": True,
        "fetched_at": _now_kst_iso(),
        "ticker": code,
        "name": matched or quote.get("name_ko") or code,
        "market": quote["market"],
        "current_price": price,
        "current_price_won": (f"{price:,.0f}원" if is_kr else f"${price:,.2f}"),
        "change_pct": chg_r,
        "change": (f"{chg_r:+.2f}%" if chg_r is not None else None),  # pre-formatted for the reply
        "basis": kind, "as_of": quote.get("as_of"),
        "source": "Kiwoom / Stock-Advisor (settled/live close)",
    }


def tool_stock_price_history(query: str = "", ticker: str = "", days: int = 14,
                             user_transcript: str = "", db=None, **_kw) -> dict[str, Any]:
    """Historical daily closing prices for ONE stock from our STORED daily reports
    + hourly snapshots — so the agent REMEMBERS past days ('지난주 삼성전자 주가',
    'last week's SK Hynix price'). Returns a date→close series."""
    q = (ticker or query or "").strip()
    code, matched = _resolve_ticker(q)
    if not code and user_transcript:
        code, matched = _resolve_ticker(user_transcript)
    if not code:
        return {"ok": False, "error": f"Couldn't resolve '{q}' to a ticker."}
    if db is None:
        return {"ok": False, "error": "history unavailable"}
    try:
        from db.models import OrchReport
        from datetime import datetime, timedelta
        try:
            days = max(1, min(int(days or 14), 90))
        except Exception:
            days = 14
        since = datetime.utcnow() - timedelta(days=days + 3)
        recs = (db.query(OrchReport)
                .filter(OrchReport.report_type.in_(["kiwoom_report", "kiwoom_snapshot"]))
                .filter(OrchReport.created_at >= since)
                .order_by(OrchReport.created_at.asc()).limit(300).all())
        hist: dict[str, float] = {}
        for rec in recs:
            c = rec.content_json or {}
            rows = (c.get("report") or {}).get("rows") or c.get("items") or []
            for r in rows:
                if r.get("t") == code and r.get("close") is not None:
                    d = r.get("data_date") or (rec.created_at.strftime("%Y-%m-%d") if rec.created_at else "")
                    if d:
                        hist[d] = r.get("close")
        series = [{"date": d, "close": hist[d]} for d in sorted(hist)][-days:]
        if not series:
            return {"ok": False, "error": f"No stored price history yet for {matched or code} ({code})."}
        return {"ok": True, "fetched_at": _now_kst_iso(), "ticker": code,
                "name": matched or code, "days": len(series), "history": series,
                "source": "Stored daily Kiwoom reports + hourly snapshots"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_stock_daily_history(query: str = "", ticker: str = "", days: int = 14,
                             user_transcript: str = "", **_kw) -> dict[str, Any]:
    """Daily OHLCV history (open/high/low/close/volume per day) for ONE Korean
    stock, fetched LIVE from Naver — works for ANY KR ticker (not just the
    watchlist) and goes back weeks/months. The authority for past-date price
    questions ('어제 종가', '10일 전 주가', '지난주') and for computing technicals."""
    q = (ticker or query or "").strip()
    code, matched = _resolve_ticker(q)
    if not code and user_transcript:
        code, matched = _resolve_ticker(user_transcript)
    if not code:
        return {"ok": False, "error": f"Couldn't resolve '{q}' to a ticker."}
    # Naver daily history is for Korean (6-digit) tickers only.
    if not code.isdigit():
        return {"ok": False,
                "error": f"Daily history is available for Korean stocks only "
                         f"(got {matched or code}). For US names I only have the live price."}
    try:
        days = max(1, min(int(days or 14), 120))
    except Exception:
        days = 14
    try:
        from services import naver_stock
        rows = naver_stock.daily_history(code, days=days)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    if not rows:
        return {"ok": False, "error": f"No daily history available for {matched or code} ({code})."}
    return {"ok": True, "fetched_at": _now_kst_iso(), "ticker": code,
            "name": matched or code, "days": len(rows), "history": rows,
            "source": "Naver Finance daily OHLCV (live)"}


# ============================================================================
#  Registration
# ============================================================================

# (name, fn, description, parameters) — parameters use JSON-schema style.
_TOOL_DEFS: list[tuple[str, Callable, str, dict]] = [
    (
        "stock_quote", tool_stock_quote,
        "OASIS Stock Advisor — the CURRENT PRICE / 현재가 / 시세 of ONE specific "
        "stock by name or ticker. Use this ONLY when the user explicitly asks for the "
        "PRICE and nothing else: 'what is the price of X', 'X 주가', 'how much is X', "
        "'X 얼마', 'X 시세' where X is a company (SK Hynix / SK하이닉스 / 삼성전자 / "
        "Samsung / 카카오 …) or a 6-digit code (000660). Returns live current price "
        "(best bid/ask) in KRW with the ticker name. "
        "⚠ Do NOT use this alone for ADVICE questions ('should I buy X', 'X 어때', "
        "'is X a good buy', 'X 살까/전망/분석') — those need analysis: combine this with "
        "stock_get_investor_flow + stock_get_intraday_signals + stock_get_recommendations "
        "+ stock_get_news and give a reasoned BUY/HOLD/SELL view. "
        "Do NOT use stock_get_price_history or web search for a single stock's "
        "current price — use this.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "Company name or 6-digit ticker (SK Hynix, 삼성전자, 000660)"},
        }, "required": ["query"]},
    ),
    (
        "stock_get_daily_history", tool_stock_daily_history,
        "LIVE daily OHLCV history (open/high/low/close/volume per day) for ONE "
        "KOREAN stock from Naver — works for ANY KR ticker (not just the watchlist) "
        "and goes back weeks/months. ⚠ USE THIS for EVERY past-date price question — "
        "'yesterday's close', 'X 어제 종가', '10 days ago', 'X 10일 전 주가', "
        "'last week', 'X 지난주 주가', 'how did X move over the past N days'. NEVER "
        "answer a past-date price from memory — ALWAYS call this. Pass days=N for how "
        "far back (default 14, max 120). Korean stocks only.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "Company name or 6-digit ticker (삼성전자, 005930)"},
            "days": {"type": "integer", "description": "How many trading days back (default 14, max 120)"},
        }, "required": ["query"]},
    ),
    (
        "stock_price_history", tool_stock_price_history,
        "Historical daily CLOSING prices of ONE stock from our stored daily reports "
        "(watchlist only, limited depth). Prefer stock_get_daily_history for past-date "
        "price questions; use this only as a fallback for stored-report closes.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "Company name or 6-digit ticker"},
            "days": {"type": "integer", "description": "How many recent days (default 14)"},
        }, "required": ["query"]},
    ),
    (
        "stock_get_recommendations", tool_stock_recommendations,
        "OASIS Stock Advisor — fetch the LIVE current stock recommendations (buy/sell picks) with up-to-the-moment returns. "
        "Use whenever the user asks what to buy, today's picks, recommendation performance, or which positions are open. "
        "Returns are real-time; cite the fetched_at timestamp.",
        {"type": "object", "properties": {
            "market": {"type": "string", "description": "KR or US (omit for both)"},
            "status": {"type": "string", "description": "active (open), closed, or all. Default active."},
            "limit": {"type": "integer", "description": "max rows (1-200)"},
        }, "required": []},
    ),
    (
        "stock_get_portfolio", tool_stock_portfolio,
        "OASIS Stock Advisor — the user's current portfolio positions / holdings (quantity, entry, live value). "
        "Use for 'my portfolio', 'what do I hold', 'my positions', P&L questions.",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "stock_get_intraday_signals", tool_stock_intraday_signals,
        "OASIS Stock Advisor — LIVE intraday trade signals (bullish / bearish / risk score) generated during market hours. "
        "Use for 'any signals right now', 'intraday alerts', 'is X showing a signal'. Optionally filter by ticker.",
        {"type": "object", "properties": {
            "ticker": {"type": "string", "description": "filter to one ticker (optional)"},
            "status": {"type": "string", "description": "filter by signal status (optional)"},
            "limit": {"type": "integer", "description": "max signals (1-200)"},
        }, "required": []},
    ),
    (
        "stock_get_intraday_status", tool_stock_intraday_status,
        "OASIS Stock Advisor — whether real-time intraday monitoring is currently enabled and its configuration "
        "(mode, watchlist size). Use for 'is monitoring on?', 'are you watching the market?'.",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "stock_get_market_summary", tool_stock_market_summary,
        "OASIS Stock Advisor — overall live market snapshot (index levels / asset summary). "
        "Use for 'how's the market', 'KOSPI level', 'market overview'. Real-time during 09:00-15:30 KST.",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "stock_get_foreign_flow", tool_stock_foreign_flow,
        "OASIS Stock Advisor — foreign-investor net buy/sell flow. Pass direction='buy' or 'sell' to get the top "
        "net-bought / net-sold names. Use for 'what are foreigners buying', '외국인 순매수'.",
        {"type": "object", "properties": {
            "direction": {"type": "string", "description": "'buy' or 'sell' for top movers; omit for overall flow"},
        }, "required": []},
    ),
    (
        "stock_get_investor_flow", tool_stock_investor_flow,
        "OASIS Stock Advisor — investor-flow dashboard: foreign / institution / retail (외국인·기관·개인) net buy/sell "
        "by symbol. Use for 수급 questions, who is accumulating a stock.",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "stock_get_volume_spikes", tool_stock_volume_spikes,
        "OASIS Stock Advisor — stocks showing unusual trading-VOLUME spikes right now. "
        "Use for 'unusual volume', 'what's moving', 'volume leaders'.",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "stock_get_price_history", tool_stock_price_history,
        "OASIS Stock Advisor — recorded 5-minute PRICE SNAPSHOTS over time (a time "
        "series of KOSPI/KOSDAQ, 삼성, SK하이닉스, NVDA, AAPL, TSLA, BTC, USD/KRW, etc.). "
        "Use to analyze trends, momentum, or compare now vs earlier ('how has X moved "
        "today', 'trend over the last hour', '추세'). Each row has captured_at + prices.",
        {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "how many snapshots (newest first, 1-1000)"},
            "hours": {"type": "integer", "description": "only snapshots from the last N hours (optional)"},
        }, "required": []},
    ),
    (
        "stock_get_watchlist", tool_stock_watchlist,
        "OASIS Stock Advisor — the symbols the user is actively tracking (their watchlist).",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "stock_get_alerts", tool_stock_alerts,
        "OASIS Stock Advisor — currently triggered price/condition alerts.",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "stock_get_ownership_changes", tool_stock_ownership_changes,
        "OASIS Stock Advisor — recent major-holder ownership change events (5%-rule / insider filings). "
        "Use for 'who bought a big stake', '대량보유 변동'.",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "stock_get_news", tool_stock_news,
        "OASIS Stock Advisor — latest Korean market news feed. Use for 'any news', 'market headlines'.",
        {"type": "object", "properties": {}, "required": []},
    ),
]


def register_stock_data_tools(registry: dict, ToolCls: type) -> int:
    """Register all Stock live-data read tools into the given TOOL_REGISTRY.

    Called from assistant_tools.py (passing its TOOL_REGISTRY + Tool class) to
    avoid a circular import. Returns the number of tools registered.
    """
    n = 0
    for name, fn, description, parameters in _TOOL_DEFS:
        registry[name] = ToolCls(
            name=name, kind="read", description=description,
            parameters=parameters, fn=fn,
        )
        n += 1
    log.info(f"stock_data_tools: registered {n} live-data read tools (backend={_backend_url()})")
    return n
