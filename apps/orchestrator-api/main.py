"""
VIP AI Platform — Orchestrator API
Core supervisor service that coordinates all sub-agents and workflows.
All DB writes go through this service — gateway/OpenClaw must never write directly.
"""

# Load .env files BEFORE any other module that reads env vars (db.base, llm_client, etc.)
import os
from pathlib import Path
try:
    from dotenv import load_dotenv
    # Try repo-root .env (vip-ai-platform/.env) — orchestrator runs from apps/orchestrator-api
    repo_root_env = Path(__file__).resolve().parent.parent.parent / ".env"
    if repo_root_env.exists():
        load_dotenv(repo_root_env, override=False)
    # Also load .env right next to main.py if present
    local_env = Path(__file__).resolve().parent / ".env"
    if local_env.exists():
        load_dotenv(local_env, override=False)
except ImportError:
    pass  # dotenv optional — explicit env vars still work

import asyncio

from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy import text
from sqlalchemy.orm import Session
import redis.asyncio as aioredis

from db.base import engine, Base, get_db
from contracts.router import router as contracts_router
from routers.tasks import router as tasks_router
from routers.callbacks import router as callbacks_router
from routers.agents import router as agents_router
from routers.runs import router as runs_router
from routers.registry import router as registry_router
from routers.a2a import router as a2a_router
from routers.judgement import router as judgement_router
from routers.reports import router as reports_router
from routers.telegram import router as telegram_router
from routers.aiglass import router as aiglass_router
from routers.chat import router as chat_router
from routers.chatbot import router as chatbot_router
from routers.demo import router as demo_router
from routers.schedules import router as schedules_router
from routers.users import router as users_router
from routers.auth import router as auth_router
from routers.twins import router as twins_router
from routers.twin_groups import router as twin_groups_router
from routers.control_room import router as control_room_router
from routers.task_board import router as task_board_router
from routers.meetings import router as meetings_router
from routers.voice import router as voice_router, ws_router as voice_ws_router
from routers.chatbot_inbox import router as chatbot_inbox_router, ws_router as chatbot_ws_router
from routers.kakao_webhook import router as kakao_webhook_router
from routers.admin_business import router as admin_business_router
from routers.assistant_knowledge import router as assistant_knowledge_router
from routers.predictions import router as predictions_router
from routers.paper_desk import router as paper_desk_router
from services.scheduler_service import init_scheduler
from services.event_bus import init_event_bus
from services.a2a_triggers import init_triggers
from services.a2a_notifications import init_a2a_notifications
from services.ws_manager import ws_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    # v4: backfill columns added to EXISTING tables since v1. create_all
    # only handles new tables — these ALTERs are idempotent.
    try:
        from services.db_migrate_v4 import apply as _apply_v4_migrations
        _report = _apply_v4_migrations(engine)
        if _report.get("added"):
            from services.logger import log as _log
            _log.info(f"v4 schema migration added columns: {_report['added']}")
    except Exception as _e:
        from services.logger import log as _log
        _log.warning(f"v4 schema migration failed (non-fatal): {_e}")

    init_event_bus(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    init_triggers()
    init_a2a_notifications()

    # Wire event bus → WebSocket broadcast
    from services.event_bus import subscribe
    subscribe("*", lambda msg: ws_manager.broadcast_sync(
        msg.get("channel", "event"),
        {k: v for k, v in msg.items() if k != "channel"},
    ))

    init_scheduler()

    # Warm up the LLM provider connection in the background so the FIRST chat request
    # after a deploy/restart isn't cold (a cold first call was a source of the
    # intermittent "I don't know"). Best-effort, non-blocking — never delays startup.
    async def _warmup_llm():
        try:
            from services.llm_client import chat_completion_sync
            await asyncio.to_thread(
                chat_completion_sync,
                system_prompt="ping",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0.0,
            )
        except Exception:
            pass
    try:
        asyncio.create_task(_warmup_llm())
    except Exception:
        pass

    # Real-time Kiwoom WebSocket order-book collector — feeds /monitoring's live
    # 30-level ladder. Gated to market hours internally; reconnects on failure;
    # disable with WS_ORDERBOOK_COLLECTOR=false. Best-effort — never blocks startup.
    try:
        from services.ws_orderbook_collector import should_run as _ws_ob_should_run, start_in_process as _ws_ob_start
        if _ws_ob_should_run():
            asyncio.create_task(_ws_ob_start())
            print("[startup] WS order-book collector launched")
    except Exception as _e:
        print(f"[startup] WS order-book collector not started: {_e!r}")

    # v4-A: install the Twin Autopilot cron so twins self-improve every N hours.
    try:
        from services.twin_autopilot import register_with_scheduler as _install_autopilot
        _install_autopilot()
    except Exception:
        pass  # autopilot is best-effort; manual /admin/autopilot/run-now still works

    # v4-E: install the auto-join dispatcher so scheduled meetings fire on time
    # even without anyone manually opening the room.
    try:
        from services.twin_meeting_autojoin import register_with_scheduler as _install_autojoin
        _install_autojoin()
    except Exception:
        pass

    # Ensure the voice-recordings Storage bucket exists. Idempotent — no-op
    # if SUPABASE_URL/SUPABASE_SERVICE_KEY aren't configured (dev mode).
    try:
        from services.voice_storage import ensure_bucket as _ensure_voice_bucket
        _ensure_voice_bucket()
    except Exception as _e:
        # Bucket setup failure shouldn't block orchestrator startup —
        # the recording upload path will log clear errors when it fires.
        pass

    # Start the self-hosted voice pipeline's AudioSocket server when enabled.
    # Asterisk connects to this on inbound calls. Off by default until
    # Asterisk is configured + KT SIP trunk is up.
    voice_pipeline_task = None
    if os.getenv("VOICE_AUDIOSOCKET_ENABLED", "0") == "1":
        try:
            from services.voice_pipeline import start_audiosocket_server
            voice_pipeline_task = asyncio.create_task(start_audiosocket_server())
        except Exception as _e:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                f"voice_pipeline: failed to start AudioSocket server: {_e}"
            )

    app.state.redis = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
    yield
    await app.state.redis.close()


app = FastAPI(
    title="VIP Orchestrator API",
    description="Core supervisor for the VIP AI Agent Platform. All task routing, dispatch, and audit goes through here.",
    version="0.2.0",
    lifespan=lifespan,
)


# ── Multi-tenant data isolation for the chatbot API ──────────────────────────
# Guards every /api/chatbot/<agent_id>/<...> data path so a logged-in buyer can
# only reach THEIR business. DISABLED until CHATBOT_API_SECRET is set (so single
# -tenant use is unaffected). Added BEFORE CORS so 401s still carry CORS headers.
import hmac as _hmac
from starlette.responses import JSONResponse as _JSONResponse

_ISOLATION_RESERVED = {"admin", "resolve-tenant", "claim-legacy-agent", "webhook", "security"}


@app.middleware("http")
async def _chatbot_tenant_isolation(request, call_next):
    secret = os.getenv("CHATBOT_API_SECRET", "")
    if secret and request.method != "OPTIONS":
        path = request.url.path
        if path.startswith("/api/chatbot/"):
            rest = path[len("/api/chatbot/"):]
            seg = rest.split("/", 1)[0]
            if seg and "/" in rest and seg not in _ISOLATION_RESERVED:
                agent_id = seg
                from services import tenant_config as _tc
                _cfg = _tc.get_tenant_config(agent_id)
                managed = bool(_cfg and _cfg.get("app_tenant_id"))
                if not managed:
                    # NOT a managed (app-linked) tenant. Exempt ONLY:
                    #  - explicitly-public standalone agents (allowlist), or
                    #  - a known chatbot tenant not yet linked to an app account
                    #    (e.g. legacy 'aiglass' before the owner connects).
                    # DENY unknown agent ids — no fail-open to arbitrary ids.
                    _public = {
                        a.strip().lower()
                        for a in os.getenv(
                            "CHATBOT_PUBLIC_AGENTS", "vip,stock,asset,realty,aiglass"
                        ).split(",")
                        if a.strip()
                    }
                    if _cfg is not None or agent_id in _public:
                        return await call_next(request)
                    return _JSONResponse(
                        {"detail": "Unknown or unauthorized business"}, status_code=404
                    )
                # Managed tenant → require a valid token (or super-admin) below.
                ok = False
                admin = os.getenv("ADMIN_API_TOKEN", "")
                xadmin = request.headers.get("x-admin-token", "")
                if admin and xadmin and _hmac.compare_digest(xadmin, admin):
                    ok = True
                else:
                    authz = request.headers.get("authorization", "")
                    tok = authz[7:].strip() if authz.lower().startswith("bearer ") else ""
                    from services.chatbot_auth import verify_token
                    p = verify_token(tok)
                    if p and p.get("agent_id") == agent_id:
                        ok = True
                if not ok:
                    return _JSONResponse(
                        {"detail": "Not authorized for this business"}, status_code=401
                    )
    return await call_next(request)

_cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] if _cors_origins_env else ["*"]

# Always allow:
#   - LAN origins on any port (laptop demos over Wi-Fi)
#   - Every *.vercel.app subdomain (VIP, Realty, Asset, preview deploys)
#   - Specific tripleh deploys
# This is in addition to whatever CORS_ALLOWED_ORIGINS env var lists.
_lan_origin_regex = (
    r"^https?://("
    r"localhost(:\d+)?"
    r"|127\.0\.0\.1(:\d+)?"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}(:\d+)?"
    r"|192\.168\.\d{1,3}\.\d{1,3}(:\d+)?"
    r"|[a-zA-Z0-9-]+\.vercel\.app"
    r"|[a-zA-Z0-9-]+\.tripleh\.app"
    r")$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_lan_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # 60s instead of FastAPI's default 600s — keeps stale CORS rejections
    # from sticking around in browsers after we adjust the allowlist.
    max_age=60,
)

# Serve uploaded media (twin_voice TTS output, voice clone samples) so the
# dashboard can play them via <audio src="/static/twin_voice/...wav">.
from fastapi.staticfiles import StaticFiles
from pathlib import Path as _Path
_uploads_root = _Path(__file__).resolve().parent / "uploads"
_uploads_root.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_uploads_root)), name="static")

# Register routers
app.include_router(tasks_router)
app.include_router(callbacks_router)
app.include_router(agents_router)
app.include_router(runs_router)
app.include_router(registry_router)
app.include_router(a2a_router)
app.include_router(judgement_router)
app.include_router(reports_router)
app.include_router(schedules_router)
app.include_router(telegram_router)
app.include_router(aiglass_router)
app.include_router(chat_router)
app.include_router(chatbot_router)
app.include_router(demo_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(contracts_router)
app.include_router(twins_router)
app.include_router(twin_groups_router)
app.include_router(control_room_router)
app.include_router(task_board_router)
app.include_router(meetings_router)
app.include_router(voice_router)
app.include_router(voice_ws_router)
app.include_router(chatbot_inbox_router)
app.include_router(chatbot_ws_router)
app.include_router(kakao_webhook_router)
app.include_router(admin_business_router)
app.include_router(assistant_knowledge_router)
app.include_router(predictions_router)
app.include_router(paper_desk_router)


# ---------------------------------------------------------------------------
#  Health & status
# ---------------------------------------------------------------------------

@app.api_route("/", methods=["GET", "HEAD"], tags=["health"])
async def root():
    return {"service": "vip-orchestrator", "status": "running", "version": "0.2.0"}


@app.api_route("/health", methods=["GET", "HEAD"], tags=["health"])
def health(db: Session = Depends(get_db)):
    """Health check — confirms DB connectivity AND keeps the Kakao fast-path
    caches warm (UptimeRobot pings this every 5 min), so a customer's first
    message after a pause still replies inside Kakao's 5s window."""
    try:
        result = db.execute(text("SELECT 1")).scalar()
        db_status = "connected" if result == 1 else "error"
    except Exception as e:
        db_status = f"error: {e}"

    # Keep Kakao caches warm (best-effort, never affects health status).
    try:
        from routers.kakao_webhook import warm_kakao_caches
        warm_kakao_caches(db)
    except Exception:
        pass

    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "redis": "configured",
        "websocket_clients": ws_manager.client_count,
        "version": "0.2.0",
    }


@app.get("/health/db", tags=["health"])
def health_db(db: Session = Depends(get_db)):
    """Dedicated DB health check."""
    try:
        result = db.execute(text("SELECT 1")).scalar()
        table_count = db.execute(
            text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalar()
        return {"status": "connected", "ping": result == 1, "tables": table_count}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/health/dashboard", tags=["health"])
def health_dashboard(hours: int = 24):
    """
    Phase 3 — full system health view.
    Returns traffic-light status for every scheduler job + recent alerts + summary.
    Used by the /health-dashboard UI page.
    """
    from services.resilience import get_health_dashboard
    return get_health_dashboard(hours_back=hours)


@app.get("/health/alerts", tags=["health"])
def health_alerts(hours: int = 24, severity: str = "all"):
    """List recent alerts (info / warning / error / critical)."""
    from db.models import AuditEventLog
    from datetime import datetime, timedelta
    db = next(get_db())
    try:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        q = db.query(AuditEventLog).filter(
            AuditEventLog.source == "alert",
            AuditEventLog.created_at >= cutoff,
        )
        if severity != "all":
            q = q.filter(AuditEventLog.event_type == f"alert.{severity}")
        events = q.order_by(AuditEventLog.created_at.desc()).limit(100).all()
        return [
            {
                "title": (e.payload_json or {}).get("title", ""),
                "body":  (e.payload_json or {}).get("body", ""),
                "severity": (e.payload_json or {}).get("severity", "info"),
                "kind":  (e.payload_json or {}).get("kind", ""),
                "timestamp": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]
    finally:
        db.close()


# ---------------------------------------------------------------------------
#  WebSocket — real-time push to dashboard
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            # Keep connection alive, receive pings
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


@app.get("/channels", tags=["channels"])
def list_channels(db: Session = Depends(get_db)):
    """List all registered channels."""
    from db.models import CoreChannel
    channels = db.query(CoreChannel).all()
    return [
        {"id": str(c.id), "type": c.type, "status": c.status, "config": c.config_json}
        for c in channels
    ]
