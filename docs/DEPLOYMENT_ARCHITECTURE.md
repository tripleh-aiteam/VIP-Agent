# VIP Agent Platform — Deployment Architecture

```
┌─────────────┐
│   Users      │
│  (Browser)   │
└──────┬──────┘
       │ HTTPS
       ▼
┌──────────────────────────────────┐
│         Vercel                    │
│                                   │
│   ┌───────────────────────┐      │
│   │  vip-agent-dashboard   │      │
│   │  (Next.js Frontend)   │      │
│   │                        │      │
│   │  /           Dashboard │      │
│   │  /chat        Chatbot  │      │
│   │  /agents      Agents   │      │
│   │  /workflows   Schedules│      │
│   │  /reports     Reports  │      │
│   │  /judgement   Approvals│      │
│   │  /a2a         Monitor  │      │
│   │  /channels    Telegram │      │
│   │  /ai-glass    Capture  │      │
│   └───────────┬───────────┘      │
│               │                   │
│   Env: NEXT_PUBLIC_API_BASE_URL  │
└───────────────┬──────────────────┘
                │ HTTPS (API calls)
                ▼
┌──────────────────────────────────┐
│     Backend Server                │
│     (Railway / VPS / AWS)        │
│                                   │
│   ┌───────────────────────┐      │
│   │  Orchestrator API      │      │
│   │  (FastAPI :8000)       │      │
│   │                        │      │
│   │  Tasks, Agents, A2A    │      │
│   │  Judgement, Reports    │      │
│   │  Chat, Schedules       │      │
│   │  Telegram, AI Glass    │      │
│   └───────────┬───────────┘      │
│               │                   │
│   ┌───────────┼───────────┐      │
│   │           │           │      │
│   ▼           ▼           ▼      │
│ Asset       Stock       Realty   │
│ Agent       Agent       Agent    │
│ (:9010)     (:9011)     (:9015)  │
│                                   │
│   Env: DATABASE_URL, REDIS_URL   │
│        OPENAI_API_KEY            │
│        CORS_ALLOWED_ORIGINS      │
└───────────────┬──────────────────┘
                │ PostgreSQL (port 5432)
                ▼
┌──────────────────────────────────┐
│         Supabase                  │
│                                   │
│   ┌───────────────────────┐      │
│   │  PostgreSQL Database   │      │
│   │                        │      │
│   │  17 tables             │      │
│   │  core, orch, audit     │      │
│   │  a2a, telegram, chat   │      │
│   │  ai_glass              │      │
│   └───────────────────────┘      │
│                                   │
│   Dashboard: Table Editor        │
│   Real-time data viewer          │
└──────────────────────────────────┘
```

## Data Flow

```
User types in Chat
  → Vercel serves page (static)
  → Browser calls Backend API (HTTPS)
  → Orchestrator processes request
  → Dispatches to Agent (HTTP)
  → Agent returns result
  → Orchestrator stores in Supabase
  → Response sent to Browser
  → Vercel page renders result
```

## Environment Variables Summary

| Where | Variable | Purpose |
|-------|----------|---------|
| Vercel | `NEXT_PUBLIC_API_BASE_URL` | Backend API URL |
| Backend | `DATABASE_URL` | Supabase connection |
| Backend | `REDIS_URL` | Cache/pub-sub |
| Backend | `OPENAI_API_KEY` | LLM Mode |
| Backend | `CORS_ALLOWED_ORIGINS` | Allow Vercel domain |
| Backend | `TELEGRAM_BOT_TOKEN` | Telegram bot (optional) |
