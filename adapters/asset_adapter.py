"""Asset Adapter — translates orchestrator contract to asset agent format."""

from typing import Any
from adapters.base_adapter import BaseAdapter, AdapterResult


class AssetAdapter(BaseAdapter):
    agent_type = "asset"

    def _build_payload(self, task_run_id, trace_id, task_type, input_payload):
        return {
            "task_run_id": task_run_id,
            "trace_id": trace_id,
            "task_type": task_type,
            "input_payload": {
                "portfolio_id": input_payload.get("portfolio_id", "PF-DEFAULT"),
                **{k: v for k, v in input_payload.items() if k != "portfolio_id"},
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
                "portfolio_id": output.get("portfolio_id"),
                "total_value": output.get("total_value"),
                "currency": output.get("currency"),
                "change_pct": output.get("change_pct"),
                "asset_count": output.get("asset_count"),
                "top_holdings": output.get("top_holdings", []),
                "risk_level": output.get("risk_level"),
                "source": "asset-adapter",
            },
            error_message=raw.get("error_message"),
        )
