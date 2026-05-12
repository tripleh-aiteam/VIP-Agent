"""Realty Adapter — translates orchestrator contract to realty agent format."""

from typing import Any
from adapters.base_adapter import BaseAdapter, AdapterResult


class RealtyAdapter(BaseAdapter):
    agent_type = "realty"

    def _build_payload(self, task_run_id, trace_id, task_type, input_payload):
        return {
            "task_run_id": task_run_id,
            "trace_id": trace_id,
            "task_type": task_type,
            "input_payload": {
                "region": input_payload.get("region", "Seoul-Gangnam"),
                **{k: v for k, v in input_payload.items() if k != "region"},
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
                "region": output.get("region"),
                "total_listings": output.get("total_listings"),
                "avg_vacancy_pct": output.get("avg_vacancy_pct"),
                "avg_yield_pct": output.get("avg_yield_pct"),
                "properties": output.get("properties", []),
                "market_trend": output.get("market_trend"),
                "source": "realty-adapter",
            },
            error_message=raw.get("error_message"),
        )
