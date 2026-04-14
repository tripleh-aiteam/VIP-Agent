# VIP AI Platform — Contract Integration Guide

## Overview

The VIP AI Platform uses a **contract-first** architecture. All communication between services, agents, and external integrations follows strict JSON schemas validated by Pydantic models.

**Rule:** Every input and output must conform to a contract. No raw/unvalidated data passes between services.

## Available Contracts

| # | Contract | Purpose | Strict Validation |
|---|----------|---------|-------------------|
| 1 | `TaskRequest` | Orchestrator dispatches a task to an agent | Yes |
| 2 | `TaskResponse` | Agent returns results to the Orchestrator | Yes |
| 3 | `A2AMessageEnvelope` | Agent-to-agent communication | Yes |
| 4 | `JudgementRequest` | Orchestrator asks Judgement Service to evaluate | Yes |
| 5 | `JudgementResult` | Judgement Service returns its decision | Yes |
| 6 | `ReportDraft` | Intermediate report before finalization | Yes |
| 7 | `FinalReport` | Completed report for delivery | Yes |
| 8 | `TelegramActionPayload` | Telegram bot commands and notifications | Yes |
| 9 | `AIGlassCaptureEvent` | Spatial capture from AI Glasses devices | Yes |

## How to Integrate (External Teams)

### 1. Get the JSON Schema

```bash
# List all contracts
curl http://localhost:8000/contracts/

# Get schema for a specific contract
curl http://localhost:8000/contracts/schema/task-request
curl http://localhost:8000/contracts/schema/task-response
curl http://localhost:8000/contracts/schema/a2a-message-envelope
```

### 2. Validate Your Payload

Before sending real requests, test your payload against the validation endpoint:

```bash
# Validate a TaskRequest
curl -X POST http://localhost:8000/contracts/validate/task-request \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "tr-test-001",
    "initiator_type": "user",
    "initiator_id": "user-001",
    "target_agent_type": "asset",
    "task_type": "asset_summary",
    "input_payload": {"portfolio_id": "PF-1234"}
  }'
```

**Response (valid):**
```json
{
  "valid": true,
  "contract": "task-request",
  "parsed": { ... }
}
```

**Response (invalid):**
```json
{
  "valid": false,
  "contract": "task-request",
  "errors": [{"loc": ["initiator_type"], "msg": "field required", "type": "missing"}],
  "error_count": 1
}
```

### 3. Use Sample Files

Sample payloads for each contract are in `/contracts/samples/`:

```
contracts/samples/
├── task-request.sample.json
├── task-response.sample.json
├── a2a-message-envelope.sample.json
├── judgement-request.sample.json
├── judgement-result.sample.json
├── report-draft.sample.json
├── final-report.sample.json
├── telegram-action-payload.sample.json
└── ai-glass-capture-event.sample.json
```

### 4. OpenAPI Documentation

Full interactive documentation with all contracts:

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

All 9 contracts appear under the "contracts" tag with request/response schemas.

## Required Fields by Contract

### TaskRequest (required)
- `trace_id` — Distributed tracing ID
- `initiator_type` — `user` | `schedule` | `agent` | `system`
- `initiator_id` — Who initiated
- `target_agent_type` — Agent type to route to
- `task_type` — Must match `orch_task_definitions.task_type`

### TaskResponse (required)
- `task_id` — Must match the request
- `trace_id` — Must match the request
- `agent_id` — Agent that executed
- `status` — `pending` | `running` | `completed` | `failed` | `partial` | `cancelled`
- `error_message` — **Required** when status is `failed`

### A2AMessageEnvelope (required)
- `trace_id`
- `sender_agent_id`
- `target_agent_id`
- `message_type` — `request` | `response` | `event` | `broadcast`
- `purpose` — `delegate` | `inform` | `query` | `escalate` | `ack`

### JudgementRequest (required)
- `trace_id`
- `task_run_id`
- `task_type`
- `agent_id`
- `agent_output`

### JudgementResult (required)
- `judgement_id`, `trace_id`, `task_run_id`
- `risk_score` — Float 0.0 to 1.0
- `risk_level` — `low` | `medium` | `high` | `critical`
- `decision` — `approve` | `reject` | `escalate` | `hold`

### TelegramActionPayload (required)
- `telegram_user_id`
- `chat_id`
- `action_type` — `command` | `approval` | `notification` | `query` | `alert`

### AIGlassCaptureEvent (required)
- `trace_id`
- `agent_id`
- `device_id`
- `capture_type` — `video` | `photo` | `spatial_3d` | `audio` | `mixed`

## Versioning

All contracts include a `version` field (default: `"1.0"`). When breaking changes are introduced, the version will increment. Agents should check the version field and reject unknown versions.

## Architecture Rules

1. **All writes go through the Orchestrator API** — gateway/OpenClaw must never write directly
2. **All payloads must validate** — invalid payloads are rejected with detailed errors
3. **trace_id is mandatory** — every operation must be traceable across services
4. **Contracts are the source of truth** — not database columns, not API docs
