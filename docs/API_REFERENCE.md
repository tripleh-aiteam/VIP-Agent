# VIP Agent Platform — API Reference

**Base URL:** `http://localhost:8000`
**Interactive Docs:** `http://localhost:8000/docs`

## Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Service info |
| GET | `/health` | DB connectivity check |
| GET | `/health/db` | Detailed DB health with table count |

## Tasks

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/tasks` | Create a task run |
| GET | `/tasks/{id}` | Get task run by ID |
| POST | `/tasks/{id}/dispatch` | Dispatch to assigned agent |
| POST | `/callbacks/agent-result` | Agent completion callback |
| GET | `/runs` | List task runs (`?status=completed&limit=20`) |

**POST /tasks body:**
```json
{
  "trace_id": "tr-001",
  "task_type": "asset_summary",
  "target_agent_type": "asset",
  "initiator_type": "user",
  "initiator_id": "user-001",
  "source_channel": "web",
  "input_payload": {"portfolio_id": "PF-1234"}
}
```

## Agent Registry

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/agents` | List agents (basic) |
| GET | `/registry/agents` | List agents (full detail, filterable) |
| GET | `/registry/agents/{id}` | Get single agent |
| POST | `/registry/agents` | Register new agent |
| PATCH | `/registry/agents/{id}` | Update agent |
| POST | `/registry/agents/{id}/heartbeat` | Record heartbeat |
| GET | `/registry/resolve` | Find best agent (`?agent_type=stock&task_type=stock_analysis`) |

## Schedules

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/schedules/` | List all schedule rules |
| PATCH | `/schedules/{id}` | Enable/disable, change cron |
| POST | `/schedules/{id}/run-now` | Trigger immediately |

## Reports

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/reports/compose/daily` | Compose daily summary |
| POST | `/reports/compose/weekly` | Compose weekly summary |
| POST | `/reports/compose/alert` | Compose urgent alert |
| GET | `/reports/` | List reports |
| GET | `/reports/{id}` | Get report JSON |
| GET | `/reports/{id}/markdown` | Get report as Markdown |

## Judgement

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/judgement/evaluate` | Run judgement on output |
| GET | `/judgement/cases` | List cases (`?decision=human_review_required`) |
| GET | `/judgement/cases/{id}` | Get case with evidence |
| POST | `/judgement/cases/{id}/approve` | Approve case |
| POST | `/judgement/cases/{id}/reject` | Reject case |

## A2A (Agent-to-Agent)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/a2a/status` | Event bus status |
| POST | `/a2a/send` | Send A2A message |
| GET | `/a2a/messages` | List messages (`?message_type=risk_alert&trace_id=tr-001`) |
| GET | `/a2a/messages/{id}` | Get message with envelope |
| POST | `/a2a/demo/risk-flow` | Run 3-step demo flow |

## Telegram

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/telegram/webhook` | Receive bot updates |
| GET | `/telegram/status` | Bot config status |
| POST | `/telegram/set-webhook` | Register webhook URL |
| POST | `/telegram/simulate` | Test commands locally (`?command=/status`) |
| POST | `/telegram/link-user` | Link Telegram user |
| GET | `/telegram/users` | List linked users |

## AI Glass

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/ai-glass/capture` | Create capture session |
| GET | `/ai-glass/sessions` | List sessions (`?status=completed`) |
| GET | `/ai-glass/sessions/{id}` | Get session detail |
| PATCH | `/ai-glass/sessions/{id}/status` | Update status |
| GET | `/ai-glass/stats` | Processing statistics |

## Contracts

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/contracts/` | List all 9 contracts |
| GET | `/contracts/schema/{name}` | Get JSON Schema |
| POST | `/contracts/validate/{name}` | Validate payload |

## Channels

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/channels` | List all channels |
