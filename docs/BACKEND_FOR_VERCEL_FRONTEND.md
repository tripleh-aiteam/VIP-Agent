# VIP Agent Platform — Backend Setup for Vercel Frontend

## Overview

The admin dashboard is deployed on Vercel. The backend (orchestrator API) runs separately. This doc explains how to configure the backend to work with the Vercel-hosted frontend.

## Architecture

```
Browser → Vercel (Next.js frontend) → Backend API (FastAPI)
                                          ↓
                                    PostgreSQL (Supabase)
```

- Frontend: Vercel (static + client-side rendering)
- Backend: Any hosting (Railway, VPS, AWS, etc.)
- Database: Supabase PostgreSQL

## CORS Configuration

The backend must allow the Vercel frontend domain in CORS.

### Environment Variable

```env
# Single origin
CORS_ALLOWED_ORIGINS=https://vip-agent-dashboard.vercel.app

# Multiple origins (comma-separated)
CORS_ALLOWED_ORIGINS=https://vip-agent-dashboard.vercel.app,http://localhost:3000

# Allow all (dev only, NOT recommended for production)
CORS_ALLOWED_ORIGINS=
```

### How It Works

In `main.py`:
```python
CORS_ALLOWED_ORIGINS env var → split by comma → passed to CORSMiddleware
If empty → defaults to ["*"] (allow all)
```

## Backend Base URL

The frontend needs the backend URL set as:

```env
# In Vercel environment variables
NEXT_PUBLIC_API_BASE_URL=https://your-backend.example.com
```

Recommended URL format:
- `https://api.vip-agent.example.com` (custom domain)
- `https://vip-backend.railway.app` (Railway)
- `https://your-server.example.com:8000` (VPS)

## Frontend-Facing Endpoints

All endpoints the frontend uses:

| Endpoint | Method | Used By |
|----------|--------|---------|
| `/health` | GET | Dashboard home |
| `/agents` | GET | Dashboard, Agents page |
| `/registry/agents` | GET, POST, PATCH | Agents page |
| `/registry/agents/{id}/heartbeat` | POST | Agents page |
| `/registry/resolve` | GET | Agents page |
| `/runs` | GET | Dashboard, Workflows |
| `/tasks` | POST | Chat (task creation) |
| `/tasks/{id}` | GET | Chat |
| `/tasks/{id}/dispatch` | POST | Chat |
| `/reports/` | GET | Reports page |
| `/reports/compose/{type}` | POST | Reports page |
| `/reports/{id}` | GET | Reports page |
| `/reports/{id}/markdown` | GET | Reports page |
| `/judgement/cases` | GET | Judgement page, Chat |
| `/judgement/cases/{id}/approve` | POST | Judgement page, Chat |
| `/judgement/cases/{id}/reject` | POST | Judgement page, Chat |
| `/a2a/messages` | GET | A2A page |
| `/a2a/status` | GET | A2A page |
| `/a2a/demo/risk-flow` | POST | A2A page |
| `/ai-glass/sessions` | GET | AI Glass page |
| `/ai-glass/stats` | GET | AI Glass page |
| `/ai-glass/capture` | POST | AI Glass page |
| `/chat/health` | GET | Chat page |
| `/chat/sessions` | GET, POST | Chat page |
| `/chat/sessions/{id}/messages` | GET, POST | Chat page |
| `/chat/sessions/{id}/mode` | PATCH | Chat page |
| `/chat/interpret` | POST | Chat page |
| `/schedules/` | GET | Workflows page |
| `/schedules/{id}` | PATCH | Workflows page |
| `/schedules/{id}/run-now` | POST | Workflows page |
| `/channels` | GET | Dashboard, Channels page |
| `/contracts/` | GET | Contracts |
| `/telegram/status` | GET | Channels page |
| `/telegram/simulate` | POST | Channels page |
| `/telegram/users` | GET | Channels page |
| `/demo/full-flow` | POST | Demo |

## What Must Stay Outside Vercel

These components cannot run on Vercel:

| Component | Why |
|-----------|-----|
| Orchestrator API (FastAPI) | Python backend, persistent connections |
| Mock agents | Separate Python services |
| PostgreSQL | Database |
| Redis | Cache/pub-sub |
| APScheduler | Background job scheduler |

## Backend Environment Variables

```env
# Database
DATABASE_URL=postgresql://...

# Redis
REDIS_URL=redis://...

# CORS
CORS_ALLOWED_ORIGINS=https://vip-agent-dashboard.vercel.app,http://localhost:3000

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Chat
CHAT_DEFAULT_MODE=structured
LLM_MODE_ENABLED=true

# App
APP_ENV=production
LOG_LEVEL=info
```

## Checklist

- [ ] Backend deployed and publicly accessible
- [ ] `CORS_ALLOWED_ORIGINS` includes Vercel domain
- [ ] `/health` returns 200
- [ ] Frontend `NEXT_PUBLIC_API_BASE_URL` points to backend
- [ ] All frontend-facing endpoints return data
- [ ] HTTPS enabled on backend
