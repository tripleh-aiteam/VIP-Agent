"""
Yahoo Finance Stock Adapter — real-time KOSPI/global prices using yfinance (free).

Fetches live prices for the seeded portfolio holdings, computes P&L, sentiment,
and a simple risk score. Output keys match what the auto-daily report expects.

Caches results for 30 minutes to avoid rate-limiting Yahoo on every voice query.
"""

from __future__ import annotations

import time
import threading
from typing import Any

from adapters.base_adapter import BaseAdapter, AdapterResult


# --- Module-level cache (30 min TTL) ---
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 30 * 60
_CACHE_LOCK = threading.Lock()


def _cache_get(key: str) -> dict | None:
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        ts, data = entry
        if time.time() - ts > _CACHE_TTL_SECONDS:
            return None
        return data


def _cache_set(key: str, data: dict) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), data)


# --- Holdings the boss tracks (must match adapters/mock_data.py KOSPI_HOLDINGS) ---
HOLDINGS = [
    {"symbol": "005930.KS", "name": "Samsung Electronics", "shares": 12500, "avg_buy": 67500},
    {"symbol": "000660.KS", "name": "SK hynix",            "shares":  3200, "avg_buy": 145000},
    {"symbol": "035420.KS", "name": "NAVER",                "shares":  1800, "avg_buy": 215000},
    {"symbol": "035720.KS", "name": "Kakao",                "shares":  4500, "avg_buy":  62000},
    {"symbol": "207940.KS", "name": "Samsung Biologics",    "shares":   400, "avg_buy": 850000},
    {"symbol": "005380.KS", "name": "Hyundai Motor",        "shares":  2100, "avg_buy": 195000},
    {"symbol": "005490.KS", "name": "POSCO Holdings",       "shares":  1200, "avg_buy": 380000},
    {"symbol": "051910.KS", "name": "LG Chem",              "shares":   800, "avg_buy": 405000},
    {"symbol": "068270.KS", "name": "Celltrion",            "shares":  1500, "avg_buy": 175000},
    {"symbol": "012330.KS", "name": "Hyundai Mobis",        "shares":   900, "avg_buy": 235000},
]
KOSPI_INDEX = "^KS11"


def _fetch_live_prices() -> dict | None:
    """Fetch live prices from Yahoo Finance. Returns None on failure."""
    try:
        import yfinance as yf
    except Exception:
        return None

    cached = _cache_get("portfolio")
    if cached:
        return cached

    try:
        symbols = [h["symbol"] for h in HOLDINGS] + [KOSPI_INDEX]
        # yfinance accepts space-separated symbols
        tickers = yf.Tickers(" ".join(symbols))

        holdings_out = []
        total_value = 0
        total_cost = 0

        for h in HOLDINGS:
            try:
                t = tickers.tickers.get(h["symbol"])
                if t is None:
                    continue
                hist = t.history(period="2d", auto_adjust=True)
                if len(hist) == 0:
                    continue
                price_now = float(hist["Close"].iloc[-1])
                price_prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price_now
                change_pct = round((price_now - price_prev) / price_prev * 100, 2) if price_prev else 0.0
                value = int(price_now * h["shares"])
                cost = h["avg_buy"] * h["shares"]
                total_value += value
                total_cost += cost
                holdings_out.append({
                    "symbol": h["symbol"],
                    "name": h["name"],
                    "shares": h["shares"],
                    "avg_buy_krw": h["avg_buy"],
                    "price_krw": int(price_now),
                    "change_pct": change_pct,
                    "value_krw": value,
                    "recommendation": "hold" if abs(change_pct) < 2.0 else ("buy" if change_pct < -2 else "sell"),
                })
            except Exception:
                continue

        # KOSPI index
        try:
            kospi_t = tickers.tickers.get(KOSPI_INDEX)
            kospi_hist = kospi_t.history(period="2d", auto_adjust=True) if kospi_t else None
            if kospi_hist is not None and len(kospi_hist) > 0:
                kospi_now = round(float(kospi_hist["Close"].iloc[-1]), 2)
                kospi_prev = round(float(kospi_hist["Close"].iloc[-2]), 2) if len(kospi_hist) >= 2 else kospi_now
                kospi_change_pct = round((kospi_now - kospi_prev) / kospi_prev * 100, 2) if kospi_prev else 0.0
            else:
                kospi_now, kospi_change_pct = 0.0, 0.0
        except Exception:
            kospi_now, kospi_change_pct = 0.0, 0.0

        if not holdings_out:
            return None

        pnl = total_value - total_cost
        pnl_pct = round(pnl / total_cost * 100, 2) if total_cost else 0.0

        # Simple sentiment: avg change of holdings
        avg_change = sum(h["change_pct"] for h in holdings_out) / len(holdings_out)
        sentiment = "bullish" if avg_change > 1.0 else "bearish" if avg_change < -1.0 else "neutral"
        risk_score = round(min(0.9, max(0.1, 0.5 + (avg_change * -0.05))), 2)

        # High-risk holdings (moves > 3%)
        high_risk = [h for h in holdings_out if abs(h["change_pct"]) > 3.0]

        result = {
            "source": "yahoo-finance-live",
            "live_data": True,
            # Keys for auto-daily report
            "symbols_analyzed": len(holdings_out),
            "market_sentiment": sentiment,
            "risk_score": risk_score,
            # Detail
            "stocks": holdings_out,
            "portfolio": {
                "total_value_krw": total_value,
                "total_cost_krw": total_cost,
                "unrealized_pnl_krw": pnl,
                "unrealized_pnl_pct": pnl_pct,
                "holdings_count": len(holdings_out),
            },
            "market_summary": {
                "index": "KOSPI",
                "value": kospi_now,
                "change_pct": kospi_change_pct,
            },
            "high_risk_holdings": [
                {"symbol": h["symbol"], "name": h["name"], "change_pct": h["change_pct"]}
                for h in high_risk
            ],
            "summary": (
                f"KOSPI {kospi_now:.0f} ({kospi_change_pct:+.2f}%) · "
                f"Portfolio {total_value/1e9:.1f}B KRW ({pnl_pct:+.2f}%) · "
                f"{len(high_risk)} holdings >3% move"
            ),
            "fetched_at": time.time(),
        }
        _cache_set("portfolio", result)
        return result
    except Exception:
        return None


class YahooStockAdapter(BaseAdapter):
    """Pulls real prices from Yahoo Finance. Falls back to mock if Yahoo is unreachable."""

    agent_type = "stock"

    def execute(self, task_run_id: str, trace_id: str, task_type: str, input_payload: dict[str, Any]) -> AdapterResult:
        live = _fetch_live_prices()
        if live:
            return AdapterResult(
                success=True, status="completed", agent_id=self.agent_name,
                summary=live["summary"],
                output_payload=live,
            )
        # Fall back to inline mock if Yahoo is offline / rate-limited
        from adapters.mock_data import get_mock_summary
        mock = get_mock_summary("stock")
        return AdapterResult(
            success=True, status="completed", agent_id=self.agent_name,
            summary=mock.get("summary", "Stock report (Yahoo offline → mock fallback)"),
            output_payload={**mock, "source": "yahoo-stock-adapter (mock fallback)", "fallback": True},
        )

    def fetch_summary(self) -> dict:
        """Quick-read summary used by voice + reports."""
        live = _fetch_live_prices()
        if live:
            return live
        from adapters.mock_data import get_mock_summary
        return {**get_mock_summary("stock"), "_source": "yahoo_fallback_to_mock"}

    def health_check(self) -> dict:
        try:
            import yfinance as yf
            t = yf.Ticker("005930.KS")
            info = t.fast_info
            return {
                "reachable": True,
                "library": f"yfinance {yf.__version__}",
                "test_symbol": "005930.KS",
                "test_price": float(info.last_price) if hasattr(info, "last_price") else None,
            }
        except Exception as e:
            return {"reachable": False, "error": str(e)}
