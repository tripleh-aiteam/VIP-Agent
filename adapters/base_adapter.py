"""
VIP AI Platform — Base Adapter
Common logic for all agent adapters: HTTP dispatch, timeout, auth, error normalization.
"""

from datetime import datetime
from typing import Any

import httpx


class AdapterResult:
    def __init__(
        self,
        success: bool,
        status: str,
        agent_id: str,
        summary: str | None = None,
        output_payload: dict[str, Any] | None = None,
        error_message: str | None = None,
    ):
        self.success = success
        self.status = status
        self.agent_id = agent_id
        self.summary = summary
        self.output_payload = output_payload
        self.error_message = error_message

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "status": self.status,
            "agent_id": self.agent_id,
            "summary": self.summary,
            "output_payload": self.output_payload,
            "error_message": self.error_message,
        }


class BaseAdapter:
    """Base adapter — all agent adapters inherit from this."""

    def __init__(
        self,
        agent_name: str,
        endpoint_url: str,
        timeout_seconds: int = 30,
        auth_type: str = "none",
        auth_token: str | None = None,
    ):
        self.agent_name = agent_name
        self.endpoint_url = endpoint_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.auth_type = auth_type
        self.auth_token = auth_token

    def _build_headers(self) -> dict[str, str]:
        """Build auth headers placeholder."""
        headers = {"Content-Type": "application/json"}
        if self.auth_type == "api_key" and self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        elif self.auth_type == "oauth" and self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _build_payload(
        self,
        task_run_id: str,
        trace_id: str,
        task_type: str,
        input_payload: dict[str, Any],
    ) -> dict:
        """Override in subclasses to transform input for specific agents."""
        return {
            "task_run_id": task_run_id,
            "trace_id": trace_id,
            "task_type": task_type,
            "input_payload": input_payload,
            "callback_url": "http://localhost:8000/callbacks/agent-result",
        }

    def _normalize_response(self, raw: dict) -> AdapterResult:
        """Override in subclasses to normalize agent-specific responses."""
        return AdapterResult(
            success=raw.get("status") == "completed",
            status=raw.get("status", "unknown"),
            agent_id=raw.get("agent_id", self.agent_name),
            summary=raw.get("summary"),
            output_payload=raw.get("output_payload"),
            error_message=raw.get("error_message"),
        )

    def execute(
        self,
        task_run_id: str,
        trace_id: str,
        task_type: str,
        input_payload: dict[str, Any],
    ) -> AdapterResult:
        """Dispatch task to agent via HTTP with timeout and error handling."""
        payload = self._build_payload(task_run_id, trace_id, task_type, input_payload)
        headers = self._build_headers()

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(
                    f"{self.endpoint_url}/execute",
                    json=payload,
                    headers=headers,
                )

            if resp.status_code != 200:
                return AdapterResult(
                    success=False,
                    status="failed",
                    agent_id=self.agent_name,
                    error_message=f"Agent returned HTTP {resp.status_code}: {resp.text[:200]}",
                )

            return self._normalize_response(resp.json())

        except httpx.TimeoutException:
            return AdapterResult(
                success=False,
                status="failed",
                agent_id=self.agent_name,
                error_message=f"Timeout after {self.timeout_seconds}s calling {self.agent_name}",
            )
        except httpx.ConnectError:
            return AdapterResult(
                success=False,
                status="failed",
                agent_id=self.agent_name,
                error_message=f"Connection refused: {self.agent_name} at {self.endpoint_url} is offline",
            )
        except Exception as e:
            return AdapterResult(
                success=False,
                status="failed",
                agent_id=self.agent_name,
                error_message=f"Adapter error: {type(e).__name__}: {e}",
            )

    def health_check(self) -> dict:
        """Check agent health endpoint."""
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.endpoint_url}/health")
            if resp.status_code == 200:
                return {"reachable": True, **resp.json()}
            return {"reachable": False, "http_status": resp.status_code}
        except Exception as e:
            return {"reachable": False, "error": str(e)}
