# VIP Agent Platform — Mock-to-Real Agent Replacement Guide

## Current Mock Agents

| Mock Agent | Port | Type | Replaces With |
|-----------|------|------|---------------|
| mock-asset-agent | 9010 | asset | Real portfolio management API |
| mock-stock-agent | 9011 | stock | Real market data provider |
| mock-realty-agent | 9015 | realty | Real property listing service |

## How to Replace a Mock Agent

### Step 1: Build your real agent

Follow `TEAM_INTEGRATION.md`. Your agent must expose:
- `GET /health`
- `POST /execute`

### Step 2: Register with higher priority

```bash
curl -X POST http://localhost:8000/registry/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "real-stock-agent",
    "type": "stock",
    "endpoint_url": "https://your-real-api.com",
    "version": "1.0.0",
    "is_mock": false,
    "supported_task_types": ["stock_analysis"],
    "priority_score": 200,
    "description": "Production stock agent"
  }'
```

**Key: set `priority_score` higher than the mock (100).** The router picks the highest-priority active agent.

### Step 3: Verify routing

```bash
curl "http://localhost:8000/registry/resolve?agent_type=stock&task_type=stock_analysis"
# Should return your real agent, not the mock
```

### Step 4: Disable the mock (optional)

```bash
# Find mock agent ID
curl http://localhost:8000/registry/agents?agent_type=stock&is_mock=true

# Disable it
curl -X PATCH http://localhost:8000/registry/agents/{mock-id} \
  -H "Content-Type: application/json" \
  -d '{"status": "inactive"}'
```

### Step 5: Update adapter if needed

If your real agent has a different API format, create a custom adapter:

```python
# adapters/real_stock_adapter.py
from adapters.base_adapter import BaseAdapter, AdapterResult

class RealStockAdapter(BaseAdapter):
    def _build_payload(self, task_run_id, trace_id, task_type, input_payload):
        return {"symbols": input_payload.get("symbols"), "api_key": "..."}

    def _normalize_response(self, raw):
        return AdapterResult(
            success=raw.get("ok"),
            status="completed" if raw.get("ok") else "failed",
            agent_id=self.agent_name,
            output_payload=raw.get("data"),
        )
```

Register: `ADAPTER_MAP["stock"] = RealStockAdapter` in `adapters/__init__.py`

## Rollback

To switch back to mock:
1. Set mock agent priority higher: `PATCH /registry/agents/{mock-id}` with `{"priority_score": 300}`
2. Or disable the real agent: `PATCH /registry/agents/{real-id}` with `{"status": "inactive"}`

No code changes needed — purely database-driven.

## Checklist

- [ ] Real agent exposes `/health` and `/execute`
- [ ] Registered with `is_mock: false` and higher priority
- [ ] `/registry/resolve` returns the real agent
- [ ] Test task dispatch succeeds
- [ ] Heartbeats working
- [ ] Adapter handles the response format
- [ ] Mock agent disabled or lower priority
