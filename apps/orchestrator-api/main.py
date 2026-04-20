"""
VIP AI Platform — Orchestrator API
Core supervisor service that coordinates all sub-agents and workflows.
All DB writes go through this service — gateway/OpenClaw must never write directly.
"""

from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy import text
from sqlalchemy.orm import Session
import redis.asyncio as aioredis
import os

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
from routers.demo import router as demo_router
from routers.schedules import router as schedules_router
from routers.users import router as users_router
from routers.auth import router as auth_router
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
app.include_router(demo_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(contracts_router)


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
