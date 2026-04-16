"""
Real Stock Agent Adapter
Connects VIP to the Stock Advisor Agent API.
No authentication required.
"""

from typing import Any
import httpx
from adapters.base_adapter import BaseAdapter, AdapterResult


class RealStockAdapter(BaseAdapter):

    def execute(self, task_run_id: str, trace_id: str, task_type: str, input_payload: dict[str, Any]) -> AdapterResult:
        try:
            news = self._fetch("/market/news")
            watchlist = self._fetch("/watchlist")
            volume = self._fetch("/market/volume-spikes")
            investor = self._fetch("/market/investor-flow")
            foreign_buy = self._fetch("/market/foreign-flow/top-buy")
            foreign_sell = self._fetch("/market/foreign-flow/top-sell")
            futures = self._fetch("/market/futures-positions")
            geopolitical = self._fetch("/market/geopolitical-impact")

            # Build output
            news_data = news or {}
            articles = news_data.get("articles", [])
            watchlist_data = (watchlist or {}).get("items", [])
            spikes = (volume or {}).get("spikes", [])
            investor_data = investor or {}
            foreign_buy_data = (foreign_buy or {}).get("data", [])
            foreign_sell_data = (foreign_sell or {}).get("data", [])
            futures_data = futures or {}
            geo_data = geopolitical or {}

            output = {
                "source": "real-stock-agent",
                "news": {"count": len(articles), "articles": [{"title": a.get("title", ""), "source": a.get("source", "")} for a in articles[:5]]},
                "watchlist": {"count": len(watchlist_data), "items": [{"ticker": w.get("ticker", ""), "name": w.get("ticker_name", "")} for w in watchlist_data[:10]]},
                "volume_spikes": {"count": len(spikes)},
                "investor_flow": investor_data,
                "foreign_buy_top": foreign_buy_data[:5] if isinstance(foreign_buy_data, list) else [],
                "foreign_sell_top": foreign_sell_data[:5] if isinstance(foreign_sell_data, list) else [],
                "futures": futures_data,
                "geopolitical": geo_data,
            }

            # Build report
            report_lines = [
                "━━━ Stock Market Report ━━━",
                "",
                f"Market News: {len(articles)} articles",
            ]
            for a in articles[:3]:
                report_lines.append(f"  • {a.get('title', '?')[:60]}")

            report_lines += [
                "",
                f"Watchlist: {len(watchlist_data)} stocks",
            ]
            for w in watchlist_data[:5]:
                report_lines.append(f"  • {w.get('ticker', '?')} — {w.get('ticker_name', '?')}")

            report_lines += [
                "",
                f"Volume Spikes: {len(spikes)}",
                "",
                "━━━ Foreign Investor Flow ━━━",
                "",
            ]
            if isinstance(foreign_buy_data, list) and foreign_buy_data:
                report_lines.append("Top Buy:")
                for f in foreign_buy_data[:3]:
                    if isinstance(f, dict):
                        report_lines.append(f"  • {f.get('ticker', '?')} — {f.get('name', '?')}")
            if isinstance(foreign_sell_data, list) and foreign_sell_data:
                report_lines.append("Top Sell:")
                for f in foreign_sell_data[:3]:
                    if isinstance(f, dict):
                        report_lines.append(f"  • {f.get('ticker', '?')} — {f.get('name', '?')}")

            if geo_data and not isinstance(geo_data, str):
                report_lines += ["", "━━━ Geopolitical Impact ━━━", ""]
                if isinstance(geo_data, dict):
                    for k, v in list(geo_data.items())[:3]:
                        report_lines.append(f"  • {k}: {str(v)[:80]}")

            summary = "\n".join(report_lines)
            output["report_text"] = summary

            return AdapterResult(
                success=True, status="completed", agent_id=self.agent_name,
                summary=summary, output_payload=output,
            )
        except Exception as e:
            return AdapterResult(
                success=False, status="failed", agent_id=self.agent_name,
                error_message=f"Stock adapter error: {e}",
            )

    def _fetch(self, path: str) -> dict | None:
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(f"{self.endpoint_url}{path}")
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass
        return None

    def health_check(self) -> dict:
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.endpoint_url}/health")
                if resp.status_code == 200:
                    return {"reachable": True, **resp.json()}
            return {"reachable": False}
        except Exception as e:
            return {"reachable": False, "error": str(e)}
