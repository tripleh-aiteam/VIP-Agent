# VIP Agent Platform — Team Integration Guide

## For External Agent Teams

This guide explains how to build and register a new agent that plugs into VIP.

## Architecture Overview

```
Your Agent ←→ Adapter ←→ Orchestrator ←→ Database
                                ↕
                          Judgement Service
```

- Your agent receives tasks via HTTP POST `/execute`
- Your agent must expose `/health`
- Orchestrator dispatches through adapters — you don't call the DB directly
- If your task type has `requires_judgement=true`, output goes through risk evaluation

## Step 1: Build Your Agent

Your agent must expose two endpoints:

### GET /health
```json
{
  "agent": "your-agent-name",
  "type": "your-type",
  "status": "healthy",
  "version": "1.0.0"
}
```

### POST /execute
**Request:**
```json
{
  "task_run_id": "uuid",
  "trace_id": "tr-xxx",
  "task_type": "your_task_type",
  "input_payload": { ... },
  "callback_url": "http://orchestrator:8000/callbacks/agent-result"
}
```

**Response:**
```json
{
  "task_run_id": "uuid",
  "trace_id": "tr-xxx",
  "agent_id": "your-agent-name",
  "status": "completed",
  "summary": "Description of result",
  "output_payload": { ... },
  "generated_at": "2026-04-14T12:00:00"
}
```

Status must be `completed` or `failed`. If `failed`, include `error_message`.

## Step 2: Register Your Agent

```bash
curl -X POST http://localhost:8000/registry/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "your-agent-name",
    "type": "your-type",
    "endpoint_url": "http://your-host:port",
    "trace_id": "tr-register",
    "version": "1.0.0",
    "is_mock": false,
    "supported_task_types": ["your_task_type"],
    "supported_channels": ["web", "telegram"],
    "priority_score": 150,
    "description": "What your agent does"
  }'
```

## Step 3: Create a Task Definition

Ask the platform admin to add your task type to `orch_task_definitions`:

```sql
INSERT INTO orch_task_definitions (task_type, target_agent_type, timeout_seconds, requires_judgement)
VALUES ('your_task_type', 'your-type', 300, false);
```

## Step 4: Test

```bash
# Create a task
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{"trace_id":"tr-test","task_type":"your_task_type","target_agent_type":"your-type","initiator_type":"user","initiator_id":"tester","input_payload":{}}'

# Dispatch
curl -X POST http://localhost:8000/tasks/{id}/dispatch

# Verify
curl http://localhost:8000/runs?limit=1
```

## Step 5: Send Heartbeats

```bash
curl -X POST http://localhost:8000/registry/agents/{your-agent-id}/heartbeat \
  -H "Content-Type: application/json" \
  -d '{"status": "healthy", "latency_ms": 50}'
```

## Step 6: Write an Adapter (Optional)

If your agent has a non-standard API, create an adapter in `adapters/`:

```python
from adapters.base_adapter import BaseAdapter, AdapterResult

class YourAdapter(BaseAdapter):
    def _build_payload(self, task_run_id, trace_id, task_type, input_payload):
        # Transform to your agent's format
        return { ... }

    def _normalize_response(self, raw: dict) -> AdapterResult:
        # Transform from your agent's format
        return AdapterResult(success=True, status="completed", ...)
```

Register in `adapters/__init__.py`:
```python
ADAPTER_MAP["your-type"] = YourAdapter
```

## Contract Validation

Test your payloads before going live:

```bash
curl -X POST http://localhost:8000/contracts/validate/task-response \
  -H "Content-Type: application/json" \
  -d '{"task_id":"...","trace_id":"tr-1","agent_id":"your-agent","status":"completed","output_payload":{}}'
```

## Rules

1. **Never write to the database directly** — all writes go through the Orchestrator API
2. **Always include `trace_id`** — every operation must be traceable
3. **Respond within timeout** — default 300 seconds, configurable per task definition
4. **Handle failures gracefully** — return `status: "failed"` with `error_message`
5. **Send heartbeats** — at least every 60 seconds when active
