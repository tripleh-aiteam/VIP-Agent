# VIP AI Agent Platform

Enterprise-style multi-agent orchestration platform for VIP asset management, reporting, and decision-making.

## Architecture

- **VIP Orchestrator** is the core supervisor (the brain)
- **OpenClaw** is the gateway/front door only — it routes requests, not decisions
- Sub-agents are pluggable and communicate via standardized contracts
- Telegram is supported as a control/notification channel

## Folder Structure

```
vip-ai-platform/
├── apps/                        # Core application services
│   ├── orchestrator-api/        # Main supervisor — dispatches tasks to agents
│   ├── admin-dashboard/         # Next.js + Tailwind control panel UI
│   ├── judgement-service/       # Decision engine — evaluates agent outputs
│   ├── report-composer/         # Generates structured reports from results
│   └── gateway-adapter/         # OpenClaw gateway interface (front door)
│
├── agents/                      # External sub-agents (mock for now)
│   ├── mock-asset-agent/        # Simulated asset management agent
│   ├── mock-stock-agent/        # Simulated stock market agent
│   └── mock-realty-agent/       # Simulated real estate agent
│
├── adapters/                    # Translation layer between orchestrator and agents
│   ├── asset-adapter/           # Adapter for asset agent I/O
│   ├── stock-adapter/           # Adapter for stock agent I/O
│   └── realty-adapter/          # Adapter for realty agent I/O
│
├── contracts/                   # Shared JSON schemas for inter-service communication
│   ├── task-input/              # Schema for task dispatch format
│   ├── task-output/             # Schema for agent response format
│   ├── event-schema/            # Schema for pub/sub events
│   └── report-schema/          # Schema for generated reports
│
├── db/                          # Database layer
│   ├── migrations/              # SQL migration files
│   └── seeds/                   # Initial seed data (init.sql)
│
├── workflows/                   # Workflow definitions (scheduled & triggered)
│   ├── daily/                   # Daily scheduled workflows
│   ├── weekly/                  # Weekly scheduled workflows
│   └── alerts/                  # Alert-triggered workflows
│
├── docs/                        # Documentation
├── infra/                       # Infrastructure configs (K8s, Terraform, etc.)
├── docker-compose.yml           # Local dev environment
├── .env.example                 # Environment variable template
└── README.md                    # This file
```

## Supported Modules

| Module        | Status     | Description                            |
|---------------|------------|----------------------------------------|
| Chatbot       | Planned    | Conversational interface               |
| Dashboard     | Scaffold   | Admin control panel (Next.js)          |
| DB            | Scaffold   | PostgreSQL with seed schema            |
| A2A           | Planned    | Agent-to-Agent communication protocol  |
| Orchestration | Scaffold   | Core supervisor service (FastAPI)      |
| Judgement     | Scaffold   | Decision evaluation engine             |
| AI Glasses    | Planned    | Visual/AR data overlay                 |

## Tech Stack

- **Backend:** Python 3.12 + FastAPI
- **Frontend:** Next.js 14 + Tailwind CSS
- **Database:** PostgreSQL 16
- **Cache/Queue/PubSub:** Redis 7
- **Containers:** Docker + Docker Compose

## Quick Start — Local (Native)

```bash
# 1. Copy environment file
cp .env.example .env

# 2. Install Python dependencies
pip install -r apps/orchestrator-api/requirements.txt

# 3. Install frontend dependencies
cd apps/admin-dashboard && npm install && cd ../..

# 4. Start all backend services (each in a separate terminal)
uvicorn apps.orchestrator-api.main:app --port 8000 --reload      # Terminal 1
uvicorn apps.gateway-adapter.main:app --port 8001 --reload       # Terminal 2
uvicorn apps.judgement-service.main:app --port 8002 --reload      # Terminal 3
uvicorn apps.report-composer.main:app --port 8003 --reload       # Terminal 4

# 5. Start frontend (another terminal)
cd apps/admin-dashboard && npx next dev --port 3000               # Terminal 5
```

## Quick Start — Docker

```bash
cp .env.example .env
docker compose up --build
```

## URLs & Endpoints

| What                  | URL                           |
|-----------------------|-------------------------------|
| **Frontend (Dashboard)** | http://localhost:3000       |
| **Backend (Orchestrator)** | http://localhost:8000     |
| **Health Check**      | http://localhost:8000/health   |
| **API Docs (Swagger)**| http://localhost:8000/docs     |
| Gateway Adapter       | http://localhost:8001          |
| Judgement Service     | http://localhost:8002          |
| Report Composer       | http://localhost:8003          |

## Port Map

| Service            | Port |
|--------------------|------|
| Admin Dashboard    | 3000 |
| Orchestrator API   | 8000 |
| Gateway Adapter    | 8001 |
| Judgement Service  | 8002 |
| Report Composer    | 8003 |
| PostgreSQL         | 5432 |
| Redis              | 6379 |
