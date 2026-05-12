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
from routers.control_room import router as control_room_router
from routers.task_board import router as task_board_router
from routers.meetings import router as meetings_router
from routers.voice import router as voice_router, ws_router as voice_ws_router
from routers.chatbot_inbox import router as chatbot_inbox_router, ws_router as chatbot_ws_router
from routers.kakao_webhook import router as kakao_webhook_router
from services.scheduler_service import init_scheduler
from services.event_bus import init_event_bus
from services.a2a_triggers import init_triggers
from services.a2a_notifications import init_a2a_notifications
from services.ws_manager import ws_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
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

_cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] if _cors_origins_env else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
app.include_router(control_room_router)
app.include_router(task_board_router)
app.include_router(meetings_router)
app.include_router(voice_router)
app.include_router(voice_ws_router)
app.include_router(chatbot_inbox_router)
app.include_router(chatbot_ws_router)
app.include_router(kakao_webhook_router)


# ---------------------------------------------------------------------------
#  Health & status
# ---------------------------------------------------------------------------

@app.get("/", tags=["health"])
async def root():
    return {"service": "vip-orchestrator", "status": "running", "version": "0.2.0"}


@app.get("/health", tags=["health"])
def health(db: Session = Depends(get_db)):
    """Health check — confirms DB connectivity."""
    try:
        result = db.execute(text("SELECT 1")).scalar()
        db_status = "connected" if result == 1 else "error"
    except Exception as e:
        db_status = f"error: {e}"

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
