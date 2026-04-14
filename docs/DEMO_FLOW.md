# VIP Agent Platform — End-to-End Demo Flow

## One-Click Demo

Run the complete flow with a single API call:

```bash
curl -X POST http://localhost:8000/demo/full-flow | python -m json.tool
```

Or from the Swagger UI: `POST /demo/full-flow`

## What Happens (7 Steps)

```
Step 1: User requests stock analysis
        → Task created (pending)
        
Step 2: Orchestrator dispatches to stock agent via adapter
        → Stock agent returns market data
        → Auto-judged (requires_judgement=true)
        → Status: review_required or completed

Step 3: Stock agent sends risk_alert (A2A)
        → mock-stock-agent → mock-asset-agent
        → Flagged as HIGH RISK

Step 4: Asset agent requests portfolio exposure (A2A)
        → mock-asset-agent → mock-stock-agent
        → data_request / query

Step 5: Asset agent requests realty exposure (A2A)
        → mock-asset-agent → mock-realty-agent
        → report_request / delegate

Step 6: Daily report composed
        → Merges all task runs from last 48h
        → Asset + Stock + Realty + Risk sections
        → Executive summary generated

Step 7: Telegram /status simulated
        → Returns system health overview
```

## Where to Verify

After running the demo, check:

| What | Where |
|------|-------|
| Dashboard stats | http://localhost:3000 |
| Task runs | http://localhost:3000 → Runs in recent table |
| A2A messages | http://localhost:3000/a2a |
| Judgement cases | http://localhost:3000/judgement |
| Reports | http://localhost:3000/reports |
| Supabase | Table Editor → orch_task_runs, a2a_messages, audit_event_logs |

## Full Testing Checklist

### Core Flow
- [ ] `POST /tasks` creates a task in pending state
- [ ] `POST /tasks/{id}/dispatch` sends to correct agent
- [ ] Agent returns structured response
- [ ] Task status updates to completed/review_required
- [ ] Task run visible in `/runs` and dashboard

### Registry
- [ ] `POST /registry/agents` registers new agent
- [ ] `/registry/resolve` selects highest-priority agent
- [ ] `POST /registry/agents/{id}/heartbeat` updates reliability
- [ ] Higher-priority agent selected over lower

### Judgement
- [ ] Stock analysis triggers auto-judgement
- [ ] Risk score calculated correctly
- [ ] Pending cases appear in `/judgement/cases`
- [ ] Approve updates task to completed
- [ ] Reject updates task to failed

### A2A
- [ ] `POST /a2a/send` creates message with envelope
- [ ] risk_alert flagged as high_risk
- [ ] Messages linked by trace_id
- [ ] Demo flow creates 3 linked messages

### Reports
- [ ] `/reports/compose/daily` generates report from recent runs
- [ ] Report has executive summary + sections
- [ ] Markdown version available at `/reports/{id}/markdown`

### Schedules
- [ ] Schedules visible in `/schedules/`
- [ ] `run-now` triggers task creation
- [ ] Enable/disable reloads scheduler

### Telegram
- [ ] `/telegram/simulate?command=/status` returns health
- [ ] `/telegram/simulate?command=/agents` lists agents
- [ ] `/telegram/simulate?command=/report` shows latest report

### AI Glass
- [ ] `POST /ai-glass/capture` creates session
- [ ] Mock processing runs in background
- [ ] Status updates: pending → processing → completed
- [ ] Failed 3x → manual_review

### Dashboard
- [ ] All 9 pages load (Dashboard, Agents, Workflows, Reports, Judgement, A2A, Channels, AI Glass)
- [ ] Data refreshes automatically
- [ ] Buttons work (Run Now, Approve, Reject, Compose, Simulate)
