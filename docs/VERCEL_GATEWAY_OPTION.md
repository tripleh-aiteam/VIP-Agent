# VIP Agent Platform — Gateway Adapter (Optional)

## Why Gateway Is Optional

The current architecture works without the gateway:

```
Frontend (Vercel) → Orchestrator API (direct)
```

The gateway is only needed if you want:
- A public-facing URL that hides the orchestrator
- Rate limiting or API key validation at the edge
- Path-based routing (`/api/*` → orchestrator)
- A future OpenClaw integration layer

**For MVP, skip the gateway. Connect frontend directly to orchestrator.**

## When to Deploy It

| Scenario | Need Gateway? |
|----------|--------------|
| MVP / dev | No |
| Single backend server | No |
| Multiple backend services behind one URL | Yes |
| Public API with rate limiting | Yes |
| OpenClaw integration | Yes |

## What Gateway Does

- Accepts requests at `/api/*`
- Validates path is allowed
- Forwards to Orchestrator with headers/body intact
- Returns orchestrator response
- Provides `/health` with orchestrator connectivity check

## What Gateway Does NOT Do

- Write to database
- Make decisions
- Run business logic
- Store state
- Bypass orchestrator or judgement

## Architecture

```
Without gateway:
  Frontend → Orchestrator API

With gateway:
  Frontend → Gateway (/api/*) → Orchestrator API
```

## Deployment (If Needed)

The gateway is a Python FastAPI service. It cannot run on Vercel (no Python support). Deploy on:
- Railway
- Fly.io
- AWS Lambda (with Mangum adapter)
- VPS

### Root Directory
```
apps/gateway-adapter
```

### Environment Variables
```env
ORCHESTRATOR_API_URL=https://your-orchestrator.example.com
CORS_ALLOWED_ORIGINS=https://vip-agent-dashboard.vercel.app
APP_ENV=production
```

### Start Command
```bash
cd apps/gateway-adapter
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8001
```

### Health Check
```
GET /health → {"gateway":"ok","orchestrator":"ok"}
```

## If You Deploy Gateway, Update Frontend

Change the frontend env var to point to gateway instead of orchestrator:

```env
# Before (direct to orchestrator)
NEXT_PUBLIC_API_BASE_URL=https://orchestrator.example.com

# After (through gateway)
NEXT_PUBLIC_API_BASE_URL=https://gateway.example.com/api
```

## Recommendation

**Skip for now.** Deploy frontend → orchestrator directly. Add gateway later when you need public API management.
