# VIP Agent Platform — Local Setup Guide

## Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL 16 (local or Supabase)
- Git

## 1. Clone & Install

```bash
git clone <repo-url>
cd vip-ai-platform

# Python dependencies
pip install -r apps/orchestrator-api/requirements.txt
pip install apscheduler==3.10.4 python-telegram-bot==21.3 fakeredis

# Frontend dependencies
cd apps/admin-dashboard && npm install && cd ../..
```

## 2. Environment

```bash
cp .env.example .env
```

Edit `.env` — set your `DATABASE_URL`:

```env
# Local PostgreSQL
DATABASE_URL=postgresql://vip:YOUR_PASSWORD@localhost:5432/vip_platform

# OR Supabase (session pooler)
DATABASE_URL=postgresql://postgres.YOUR_PROJECT:PASSWORD@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres
```

## 3. Database Setup

```bash
cd apps/orchestrator-api

# Run migrations
python -m alembic upgrade head

# Seed data (agents, channels, schedules, task definitions)
python -m db.seed
```

## 4. Start All Services

Open 5 terminals:

```bash
# Terminal 1: Orchestrator API (port 8000)
cd apps/orchestrator-api
PYTHONPATH="../../" DATABASE_URL="your-db-url" python -m uvicorn main:app --port 8000

# Terminal 2: Mock Asset Agent (port 9010)
cd agents/mock-asset-agent
python -m uvicorn main:app --port 9010

# Terminal 3: Mock Stock Agent (port 9011)
cd agents/mock-stock-agent
python -m uvicorn main:app --port 9011

# Terminal 4: Mock Realty Agent (port 9015)
cd agents/mock-realty-agent
python -m uvicorn main:app --port 9015

# Terminal 5: Dashboard (port 3000)
cd apps/admin-dashboard
npx next dev --port 3000
```

## 5. Verify

| Service | URL | Expected |
|---------|-----|----------|
| Dashboard | http://localhost:3000 | Command Center page |
| API Health | http://localhost:8000/health | `{"status":"ok"}` |
| Swagger | http://localhost:8000/docs | Interactive API docs |
| Asset Agent | http://localhost:9010/health | `{"status":"healthy"}` |
| Stock Agent | http://localhost:9011/health | `{"status":"healthy"}` |
| Realty Agent | http://localhost:9015/health | `{"status":"healthy"}` |

## 6. Quick Test

```bash
# Create and dispatch a task
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{"trace_id":"tr-test","task_type":"asset_summary","target_agent_type":"asset","initiator_type":"user","initiator_id":"tester","input_payload":{"portfolio_id":"PF-1"}}'

# Dispatch it (use the id from above)
curl -X POST http://localhost:8000/tasks/{id}/dispatch

# Check runs
curl http://localhost:8000/runs
```

## Port Map

| Port | Service |
|------|---------|
| 3000 | Dashboard |
| 8000 | Orchestrator API |
| 9010 | Mock Asset Agent |
| 9011 | Mock Stock Agent |
| 9015 | Mock Realty Agent |
| 5432 | PostgreSQL |
