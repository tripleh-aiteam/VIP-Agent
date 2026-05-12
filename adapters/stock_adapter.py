"""Stock Adapter — translates orchestrator contract to stock agent format."""

from typing import Any
from adapters.base_adapter import BaseAdapter, AdapterResult


class StockAdapter(BaseAdapter):
    agent_type = "stock"

    def _build_payload(self, task_run_id, trace_id, task_type, input_payload):
        return {
            "task_run_id": task_run_id,
            "trace_id": trace_id,
            "task_type": task_type,
            "input_payload": {
                "symbols": input_payload.get("symbols", ["005930.KS"]),
                **{k: v for k, v in input_payload.items() if k != "symbols"},
            },
            "callback_url": "http://localhost:8000/callbacks/agent-result",
        }

    def _normalize_response(self, raw: dict) -> AdapterResult:
        output = raw.get("output_payload") or {}
        return AdapterResult(
            success=raw.get("status") == "completed",
            status=raw.get("status", "unknown"),
            agent_id=raw.get("agent_id", self.agent_name),
            summary=raw.get("summary"),
            output_payload={
                "symbols_analyzed": output.get("symbols_analyzed"),
                "market_sentiment": output.get("market_sentiment"),
                "risk_score": output.get("risk_score"),
                "stocks": output.get("stocks", []),
                "market_summary": output.get("market_summary"),
                "source": "stock-adapter",
            },
            error_message=raw.get("error_message"),
        )
