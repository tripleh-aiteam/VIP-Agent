# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

VIP AI Agent Platform — an enterprise multi-agent orchestration system for VIP asset/stock/realty management. Two human-facing surfaces (boss dashboard + per-employee twin portal), one shared backend brain (orchestrator), pluggable domain agents (asset / stock / realty), and digital twins that learn from their owner's work over time.

## Run / Develop

The platform has six services. For local dev, the orchestrator runs natively (uvicorn `--reload`) and the two Next.js apps run via `npm run dev`. Docker Compose is used for production-style builds and CI.

```bash
# Backend (Postgres + Redis + all FastAPI services)
docker compose up -d

# Or run the orchestrator natively (faster reload, easier to debug)
cd apps/orchestrator-api
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# VIP Boss Dashboard (port 3000)
cd apps/admin-dashboard && npm run dev

# Twin Portal — per-worker view (port 3001)
cd apps/twin-portal && npm run dev

# Single test (pytest, in orchestrator-api)
cd apps/orchestrator-api
pytest tests/test_intents.py -k test_specific_function -v

# All tests
pytest tests/
```

`.env` at the repo root is loaded automatically by `apps/orchestrator-api/main.py` via `python-dotenv` (with `override=False` so explicit shell exports win). `.env.supabase` holds the production Supabase Postgres URL — required when the orchestrator should see real twin data.

## Important: Default DB Is Supabase, Not Local Postgres

`.env` defaults `DATABASE_URL` to `localhost:5432` (a Docker Postgres that may be empty). The actual production data — twins, knowledge, tasks, activity logs — lives in Supabase (`aws-1-ap-northeast-2.pooler.supabase.com`) defined in `.env.supabase`. When starting the orchestrator natively, export `DATABASE_URL` from `.env.supabase` first or you'll see "0 twins" and assume the data was deleted. It wasn't.

## Architecture — Read These Together

Three patterns govern how the codebase fits together. Understanding these unlocks everything else; without them, individual files look arbitrary.

### 1. Orchestrator-as-Brain

`apps/orchestrator-api` is the **only** service that writes to the database. Every other service — gateway, judgement, report-composer, the two frontends, agents, twins — calls into it via HTTP. The orchestrator owns:

- Task lifecycle (`services/task_service.py`)
- Twin behavior (8 `services/twin_*.py` files)
- A2A coordination (`services/a2a_service.py` — 738 lines, the largest single service)
- Scheduler (`services/scheduler_service.py` — 7+ cron jobs)
- LLM routing (`services/llm_client.py`)

`apps/gateway-adapter` ("OpenClaw") is just a front door / router. It does NOT make decisions — it forwards to the orchestrator. New domain agents go in `agents/`, their adapters in `adapters/`, and their wire contracts in `contracts/`.

### 2. Twin System (the "Smart Like Me" Layer)

A twin is a per-employee AI representation backed by `DigitalTwin` rows + a stack of services that give it intelligence:

| Service | Role |
|---------|------|
| `twin_brain.py` | LLM thinking + tool execution + 6-layer system prompt + auto-knowledge extraction |
| `twin_service.py` | CRUD + tasks + knowledge + activity logging |
| `twin_reports.py` | Morning / evening / weekly reports + handoff workflow (817 lines) |
| `twin_self_improve.py` | Auto-improvement cycle every 6h |
| `twin_intelligence.py` | Composite "readiness tier" + auto-detected specialties |
| `twin_snapshots.py` | Versioned save/restore of twin state |
| `twin_notifications.py` | Per-twin alert queue |
| `twin_access.py` | Worker can only access their own twin (admin sees all) |

Twins have three **modes** (`shadow` / `active` / `handoff`) that the scheduler auto-switches based on Korean working hours. Shadow = passive learning during the day, active = working independently after hours, handoff = preparing morning report at 9 AM KST. The "evening handoff → morning report" flow at `services/twin_reports.py:746` is the canonical 24/7 workflow — worker submits tasks before bed, twin executes overnight, worker reviews in the morning.

The `_check_twin_access` dependency in `routers/twins.py` enforces tenant isolation: workers see only their `twin_id`; users with role `admin/operator/viewer` see everything.

### 3. Multi-Provider LLM Client with Fallback Chain

`services/llm_client.py` exposes one entry point — `chat_completion_sync(system_prompt, messages, model=None)` — that routes to Anthropic / OpenAI / Google Gemini / local Ollama based on a friendly model name. The catalog at the top of the file (`MODEL_CATALOG`) maps `claude-sonnet-4-6` → `("anthropic", "claude-sonnet-4-5")` etc.

Critical detail: env vars are read **at call time** via `_env()` (not at module load) so adding keys to `.env` and restarting the orchestrator picks them up. Anthropic timeout is 300s — long-form research reports (6000 tokens) take 2-4 minutes. The fallback chain is: requested model → default OpenAI → local Ollama → friendly error message.

When you see `[LLM unavailable]` errors in chat or task results, it almost always means the requested provider failed AND OpenAI fell back AND Ollama isn't installed. Check `services/llm_client.py:chat_completion_sync` to trace.

## Frontend — Two Audiences, One Backend

`apps/admin-dashboard` (port 3000) is for the **boss/CEO**. It has 18 pages — twins, control-room, handoff, meetings (multi-twin rooms), meeting-notes (real-world voice → bilingual KR/EN summary), task-board, agents, workflows, reports, judgement, a2a, channels, ai-glass, chat, settings. CORS allowlist for the orchestrator is set in `.env`'s `CORS_ALLOWED_ORIGINS`.

`apps/twin-portal` (port 3001) is for **workers**. Single dashboard at `/dashboard` with six tabs (Home / Teach / Chat / Review / Messages / Reports) and three Teach sub-tabs (Upload / Decision Rules / Import AI Sessions / Connected Tools / Knowledge Base). Workers log in with `email + password`, the dashboard reads `twin_id` from `localStorage`, and all API calls send `X-User-Email` so `_check_twin_access` can scope responses.

Chat sessions and folders persist in `localStorage` (no backend storage for those yet) — see the `useEffect` block in `dashboard/page.tsx` that reads/writes `twin-chat-sessions-{twinId}`.

## Scheduler Cadence

`services/scheduler_service.py` registers seven cron jobs at startup. The most important to know about when debugging twin behavior:

- **Twin auto-mode-switch** — every 1 minute. Honors a "12-hour grace period" after manual handoff so the worker's evening handoff isn't overwritten when daytime hits.
- **Claude Code auto-import** — every hour at `:15`. Reads `~/.claude/projects/*/*.jsonl` and ingests new sessions into the twin's knowledge with dedup by session ID prefix.
- **Twin self-improvement** — every 6 hours. Runs reflection / pattern analysis / consolidation across all twins.
- **Morning handoff** — 9 AM KST Mon-Fri.
- **Daily reports** — 8 AM KST.
- **Weekly report** — Friday 6:30 PM KST.
- **Agent health check** — every 5 min.

## Adding a New Agent

1. Mock implementation goes in `agents/mock-{name}-agent/`.
2. Real adapter in `adapters/real_{name}_adapter.py` (wraps the external service / API).
3. Wire contract (input/output JSON schemas) in `contracts/{name}/`.
4. Register in DB via `db/seed.py` so it shows up in `/registry/agents`.

The orchestrator picks up new agents through `registry_service.py` — no code change needed in routing.

## Knowledge Loop (Why Twins Get Smarter)

When a worker chats with their twin, `twin_brain.think()` does five things in order:

1. Loads conversation memory (last 5 boss-worker DMs + recent activity log)
2. Selects relevant knowledge via priority-weighted scoring (`_select_relevant_knowledge` — corrections +15, hard rules +10, decisions/long docs +8, recency boost, size penalty)
3. Builds a **6-layer system prompt** (`build_system_prompt`): identity, hard rules, communication style, knowledge, task instructions, permission level
4. Calls the LLM with that context
5. Auto-extracts substantive Q&As as new knowledge entries (`_auto_extract_knowledge`) — this is why twins improve from chat alone

Corrections submitted via the Review tab become highest-priority knowledge entries (title prefix `Correction:`) with score +15, ensuring twins never repeat the same mistake.

## File Output: Markdown → DOCX

`services/docx_export.py` converts twin task results (Markdown) into styled `.docx` for download. Endpoint: `GET /twins/{twin_id}/tasks/{task_id}/download.docx`. Used by the boss/worker to grab overnight research reports as Word files. Supports headings (#/##/###), bullets, numbered lists, tables (`| col |`), code blocks, bold/italic/inline-code.

## Daily Changes Log

`Daily_changes.md` is updated by the human team (not auto-generated) and is the canonical narrative log of what was built/changed each day, with file references. When investigating "why does this code look like this?", check this file first — it often explains intent that isn't obvious from the diff.

## Deployment Notes

`ENTERPRISE_DEPLOYMENT.md` describes the on-premise deployment plan (Ubuntu + Docker + Tailscale VPN + Keycloak + vLLM + pgvector). The Mac desktop app is built via `.github/workflows/build-desktop.yml` on `workflow_dispatch` and currently uses an iframe-to-Vercel pattern (`apps/admin-dashboard/src-tauri/frontend/index.html` loads `oasisvip.vercel.app`). Windows builds were removed in commit `d57fcb1`; Twin Portal is not yet wrapped in Tauri.
