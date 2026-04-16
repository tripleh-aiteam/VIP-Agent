"""
VIP AI Platform — Orchestrator API Tests
Tests for task creation, dispatch, callback, and agent registration.
"""

import os
os.environ.setdefault("DATABASE_URL", "postgresql://vip:password@localhost:5432/vip_platform")

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"


def test_root():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "vip-orchestrator"


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

def test_list_agents():
    r = client.get("/agents")
    assert r.status_code == 200
    agents = r.json()
    assert len(agents) >= 3  # seeded mock agents
    names = [a["name"] for a in agents]
    assert "Asset Agent" in names


def test_register_agent():
    r = client.post("/agents/register", json={
        "name": "test-custom-agent",
        "type": "custom",
        "endpoint_url": "http://localhost:9999",
        "trace_id": "tr-test-register",
        "is_mock": True,
        "capabilities": {"actions": ["test"]},
    })
    assert r.status_code == 201
    data = r.json()
    assert data["registered"] is True
    assert data["name"] == "test-custom-agent"


# ---------------------------------------------------------------------------
# Task creation
# ---------------------------------------------------------------------------

def test_create_task():
    r = client.post("/tasks", json={
        "trace_id": "tr-test-001",
        "task_type": "asset_summary",
        "target_agent_type": "asset",
        "initiator_type": "user",
        "initiator_id": "test-user",
        "source_channel": "web",
        "input_payload": {"portfolio_id": "PF-TEST"},
    })
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "pending"
    assert data["trace_id"] == "tr-test-001"
    assert data["agent_name"] == "Asset Agent"
    return data["id"]


def test_create_task_unknown_type():
    r = client.post("/tasks", json={
        "trace_id": "tr-test-bad",
        "task_type": "nonexistent_type",
        "target_agent_type": "asset",
    })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Task dispatch
# ---------------------------------------------------------------------------

def test_dispatch_task():
    # Create first
    r = client.post("/tasks", json={
        "trace_id": "tr-test-dispatch",
        "task_type": "asset_summary",
        "target_agent_type": "asset",
        "input_payload": {"portfolio_id": "PF-DISPATCH"},
    })
    task_id = r.json()["id"]

    # Dispatch (mock agent offline -> auto-completes)
    r2 = client.post(f"/tasks/{task_id}/dispatch")
    assert r2.status_code == 200
    data = r2.json()
    assert data["status"] == "completed"  # mock agent auto-completes
    assert data["output_payload"]["mock"] is True


def test_dispatch_idempotent():
    """Dispatching an already-completed task should be a no-op."""
    r = client.post("/tasks", json={
        "trace_id": "tr-test-idempotent",
        "task_type": "asset_summary",
        "target_agent_type": "asset",
    })
    task_id = r.json()["id"]

    client.post(f"/tasks/{task_id}/dispatch")
    r2 = client.post(f"/tasks/{task_id}/dispatch")
    assert r2.status_code == 200  # no error, just skipped


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------

def test_callback_flow():
    """Create -> dispatch (mock skips to completed), verify full flow."""
    r = client.post("/tasks", json={
        "trace_id": "tr-test-callback",
        "task_type": "stock_analysis",
        "target_agent_type": "stock",
        "input_payload": {"symbols": ["AAPL"]},
    })
    task_id = r.json()["id"]

    # Dispatch mock agent -> auto completes
    r2 = client.post(f"/tasks/{task_id}/dispatch")
    data = r2.json()
    # stock_analysis requires_judgement=True, but mock path sets completed directly
    assert data["status"] in ("completed", "review_required")


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def test_list_runs():
    r = client.get("/runs")
    assert r.status_code == 200
    runs = r.json()
    assert isinstance(runs, list)


def test_list_runs_filter():
    r = client.get("/runs?status=completed")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def test_list_reports():
    r = client.get("/reports")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# Contracts still work
# ---------------------------------------------------------------------------

def test_contracts_list():
    r = client.get("/contracts/")
    assert r.status_code == 200
    contracts = r.json()
    assert len(contracts) == 9


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
