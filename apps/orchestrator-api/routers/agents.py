"""
VIP AI Platform — Agent Router
GET /agents, POST /agents/register
"""

from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db.base import get_db
from services import agent_service

router = APIRouter(tags=["agents"])


class RegisterAgentBody(BaseModel):
    name: str = Field(...)
    type: str = Field(..., description="asset | stock | realty | custom")
    endpoint_url: str = Field(...)
    trace_id: str = Field(default="system")
    version: str = Field(default="0.1.0")
    owner_team: Optional[str] = None
    auth_type: str = Field(default="none")
    is_mock: bool = Field(default=False)
    capabilities: Optional[dict] = None


@router.get("/agents")
def list_agents(db: Session = Depends(get_db)):
    """List all registered agents."""
    agents = agent_service.list_agents(db)
    return [
        {
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
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in agents
    ]


@router.post("/agents/register", status_code=201)
def register_agent(body: RegisterAgentBody, db: Session = Depends(get_db)):
    """Register a new agent or update an existing one."""
    agent = agent_service.register_agent(
        db=db,
        name=body.name,
        agent_type=body.type,
        endpoint_url=body.endpoint_url,
        trace_id=body.trace_id,
        version=body.version,
        owner_team=body.owner_team,
        auth_type=body.auth_type,
        is_mock=body.is_mock,
        capabilities=body.capabilities,
    )
    db.commit()
    return {
        "registered": True,
        "id": str(agent.id),
        "name": agent.name,
        "type": agent.type,
        "status": agent.status,
    }
