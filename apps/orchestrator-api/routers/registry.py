"""
VIP AI Platform — Agent Registry Router
GET/POST /registry/agents, PATCH /registry/agents/{id}, POST /registry/agents/{id}/heartbeat
"""

from uuid import UUID
from pydantic import BaseModel, Field
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.base import get_db
from services import registry_service

router = APIRouter(prefix="/registry", tags=["registry"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegisterAgentBody(BaseModel):
    name: str = Field(..., description="Unique agent name")
    type: str = Field(..., description="Agent type: asset, stock, realty, custom, etc.")
    endpoint_url: str = Field(...)
    trace_id: str = Field(default="system")
    version: str = Field(default="0.1.0")
    owner_team: Optional[str] = None
    auth_type: str = Field(default="none")
    is_mock: bool = Field(default=False)
    capabilities: Optional[dict] = None
    supported_task_types: Optional[list[str]] = Field(None, description="Task types this agent can handle")
    supported_channels: Optional[list[str]] = Field(None, description="Channels this agent supports")
    priority_score: int = Field(default=100, description="Higher = preferred in routing")
    description: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {
            "name": "acme-stock-agent",
            "type": "stock",
            "endpoint_url": "https://acme.example.com/agent",
            "trace_id": "tr-register-001",
            "version": "1.0.0",
            "owner_team": "acme-team",
            "is_mock": False,
            "capabilities": {"actions": ["fetch_market_data", "analyze_trends", "predict"]},
            "supported_task_types": ["stock_analysis"],
            "supported_channels": ["web", "telegram"],
            "priority_score": 150,
            "description": "Production stock analysis agent by Acme Corp",
        }
    ]}}


class PatchAgentBody(BaseModel):
    endpoint_url: Optional[str] = None
    version: Optional[str] = None
    owner_team: Optional[str] = None
    auth_type: Optional[str] = None
    status: Optional[str] = None
    is_mock: Optional[bool] = None
    capabilities_json: Optional[dict] = None
    supported_task_types: Optional[list[str]] = None
    supported_channels: Optional[list[str]] = None
    priority_score: Optional[int] = None
    description: Optional[str] = None
    trace_id: str = Field(default="system")


class HeartbeatBody(BaseModel):
    status: str = Field(..., description="healthy | degraded | offline")
    latency_ms: Optional[int] = None
    metadata: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {
            "status": "healthy",
            "latency_ms": 42,
            "metadata": {"cpu_pct": 23.5, "memory_mb": 512, "queue_depth": 0},
        }
    ]}}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent_to_dict(a) -> dict:
    return {
        "id": str(a.id),
        "name": a.name,
        "type": a.type,
        "version": a.version,
        "owner_team": a.owner_team,
        "endpoint_url": a.endpoint_url,
        "auth_type": a.auth_type,
        "status": a.status,
        "is_mock": a.is_mock,
        "capabilities": a.capabilities_json,
        "supported_task_types": a.supported_task_types,
        "supported_channels": a.supported_channels,
        "priority_score": a.priority_score,
        "reliability_score": a.reliability_score,
        "description": a.description,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/agents")
def list_agents(
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None, alias="agent_type"),
    is_mock: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    """List all registered agents. Filterable by status, type, is_mock."""
    agents = registry_service.list_agents(db, status=status, agent_type=type, is_mock=is_mock)
    return [_agent_to_dict(a) for a in agents]


@router.get("/agents/{agent_id}")
def get_agent(agent_id: UUID, db: Session = Depends(get_db)):
    """Get a single agent by ID with full registry details."""
    agent = registry_service.get_agent(db, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return _agent_to_dict(agent)


@router.post("/agents", status_code=201)
def register_agent(body: RegisterAgentBody, db: Session = Depends(get_db)):
    """Register a new agent or update existing. No hardcoding needed — just register and route."""
    agent = registry_service.register_agent(
        db=db,
        trace_id=body.trace_id,
        name=body.name,
        agent_type=body.type,
        endpoint_url=body.endpoint_url,
        version=body.version,
        owner_team=body.owner_team,
        auth_type=body.auth_type,
        is_mock=body.is_mock,
        capabilities=body.capabilities,
        supported_task_types=body.supported_task_types,
        supported_channels=body.supported_channels,
        priority_score=body.priority_score,
        description=body.description,
    )
    db.commit()
    return {"registered": True, **_agent_to_dict(agent)}


@router.patch("/agents/{agent_id}")
def patch_agent(agent_id: UUID, body: PatchAgentBody, db: Session = Depends(get_db)):
    """Partially update an agent's registry entry."""
    updates = body.model_dump(exclude_none=True, exclude={"trace_id"})
    if not updates:
        raise HTTPException(400, "No fields to update")

    agent = registry_service.update_agent(db, agent_id, body.trace_id, updates)
    if not agent:
        raise HTTPException(404, "Agent not found")
    db.commit()
    return {"updated": True, **_agent_to_dict(agent)}


@router.post("/agents/{agent_id}/heartbeat")
def agent_heartbeat(agent_id: UUID, body: HeartbeatBody, db: Session = Depends(get_db)):
    """Record a heartbeat from an agent. Updates reliability score automatically."""
    hb = registry_service.record_heartbeat(
        db=db,
        agent_id=agent_id,
        status=body.status,
        latency_ms=body.latency_ms,
        metadata=body.metadata,
    )
    if not hb:
        raise HTTPException(404, "Agent not found")
    db.commit()

    agent = registry_service.get_agent(db, agent_id)
    return {
        "recorded": True,
        "agent": agent.name,
        "agent_status": agent.status,
        "reliability_score": agent.reliability_score,
        "heartbeat_id": str(hb.id),
    }


@router.get("/resolve")
def resolve_agent(
    agent_type: str = Query(...),
    task_type: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Test the routing logic — find the best agent for a given type/task/channel."""
    agent = registry_service.select_best_agent(db, agent_type, task_type, channel)
    if not agent:
        raise HTTPException(404, f"No eligible agent for type={agent_type} task={task_type} channel={channel}")
    return {"selected": True, **_agent_to_dict(agent)}
