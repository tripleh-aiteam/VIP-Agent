"""
VIP AI Platform — Agent Registry Service
Capability-based agent registration, routing, and health tracking.
No hardcoded if-statements — routing uses registry data only.
"""

from datetime import datetime
from uuid import UUID
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import desc

from db.models import CoreAgent, AgentHeartbeat
from services.audit_service import record_event
from services.logger import log


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_agents(
    db: Session,
    status: str | None = None,
    agent_type: str | None = None,
    is_mock: bool | None = None,
) -> list[CoreAgent]:
    """List agents with optional filters."""
    q = db.query(CoreAgent)
    if status:
        q = q.filter(CoreAgent.status == status)
    if agent_type:
        q = q.filter(CoreAgent.type == agent_type)
    if is_mock is not None:
        q = q.filter(CoreAgent.is_mock == is_mock)
    return q.order_by(desc(CoreAgent.priority_score), CoreAgent.name).all()


def get_agent(db: Session, agent_id: UUID) -> CoreAgent | None:
    return db.query(CoreAgent).filter(CoreAgent.id == agent_id).first()


def register_agent(
    db: Session,
    trace_id: str,
    name: str,
    agent_type: str,
    endpoint_url: str,
    version: str = "0.1.0",
    owner_team: str | None = None,
    auth_type: str = "none",
    is_mock: bool = False,
    capabilities: dict | None = None,
    supported_task_types: list[str] | None = None,
    supported_channels: list[str] | None = None,
    priority_score: int = 100,
    description: str | None = None,
) -> CoreAgent:
    """Register a new agent or update an existing one by name."""
    existing = db.query(CoreAgent).filter(CoreAgent.name == name).first()

    if existing:
        existing.type = agent_type
        existing.endpoint_url = endpoint_url
        existing.version = version
        existing.owner_team = owner_team or existing.owner_team
        existing.auth_type = auth_type
        existing.is_mock = is_mock
        existing.capabilities_json = capabilities or existing.capabilities_json
        existing.supported_task_types = supported_task_types or existing.supported_task_types
        existing.supported_channels = supported_channels or existing.supported_channels
        existing.priority_score = priority_score
        existing.description = description or existing.description
        existing.status = "active"
        existing.updated_at = datetime.utcnow()
        db.flush()

        record_event(db, "registry", "agent.updated", trace_id, {"agent": name})
        log.info(f"registry: agent updated — {name}", extra={"agent": name, "action": "registry.updated"})
        return existing

    agent = CoreAgent(
        name=name,
        type=agent_type,
        version=version,
        owner_team=owner_team,
        endpoint_url=endpoint_url,
        auth_type=auth_type,
        status="active",
        is_mock=is_mock,
        capabilities_json=capabilities or {},
        supported_task_types=supported_task_types or [],
        supported_channels=supported_channels or [],
        priority_score=priority_score,
        reliability_score=1.0,
        description=description,
    )
    db.add(agent)
    db.flush()

    record_event(db, "registry", "agent.registered", trace_id, {
        "agent": name, "type": agent_type, "endpoint": endpoint_url,
    })
    log.info(f"registry: agent registered — {name}", extra={"agent": name, "action": "registry.registered"})
    return agent


def update_agent(
    db: Session,
    agent_id: UUID,
    trace_id: str,
    updates: dict[str, Any],
) -> CoreAgent | None:
    """Partial update of an agent's fields."""
    agent = db.query(CoreAgent).filter(CoreAgent.id == agent_id).first()
    if not agent:
        return None

    allowed_fields = {
        "endpoint_url", "version", "owner_team", "auth_type", "status",
        "is_mock", "capabilities_json", "supported_task_types",
        "supported_channels", "priority_score", "description",
    }

    for key, value in updates.items():
        if key in allowed_fields:
            setattr(agent, key, value)

    agent.updated_at = datetime.utcnow()
    db.flush()

    record_event(db, "registry", "agent.patched", trace_id, {
        "agent": agent.name, "fields": list(updates.keys()),
    })
    return agent


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def record_heartbeat(
    db: Session,
    agent_id: UUID,
    status: str,
    latency_ms: int | None = None,
    metadata: dict | None = None,
) -> AgentHeartbeat | None:
    """Record a heartbeat from an agent and update its reliability score."""
    agent = db.query(CoreAgent).filter(CoreAgent.id == agent_id).first()
    if not agent:
        return None

    hb = AgentHeartbeat(
        agent_id=agent_id,
        status=status,
        latency_ms=latency_ms,
        metadata_json=metadata or {},
    )
    db.add(hb)

    # Update agent status based on heartbeat
    if status == "healthy":
        agent.status = "active"
        agent.reliability_score = min(1.0, (agent.reliability_score or 0.5) * 0.9 + 0.1)
    elif status == "degraded":
        agent.status = "active"
        agent.reliability_score = max(0.0, (agent.reliability_score or 0.5) * 0.8)
    elif status == "offline":
        agent.status = "inactive"
        agent.reliability_score = max(0.0, (agent.reliability_score or 0.5) * 0.5)

    agent.updated_at = datetime.utcnow()
    db.flush()

    log.info(
        f"heartbeat: {agent.name} — {status} (reliability={agent.reliability_score:.2f})",
        extra={"agent": agent.name, "action": "heartbeat"},
    )
    return hb


# ---------------------------------------------------------------------------
# Capability-based routing (no hardcoded names!)
# ---------------------------------------------------------------------------

def select_best_agent(
    db: Session,
    agent_type: str,
    task_type: str | None = None,
    channel: str | None = None,
) -> CoreAgent | None:
    """
    Select the best active agent matching:
    1. agent type
    2. task_type capability (if specified)
    3. channel support (if specified)
    4. highest priority_score
    5. highest reliability_score as tiebreaker

    NO hardcoded agent names — purely data-driven.
    """
    candidates = (
        db.query(CoreAgent)
        .filter(CoreAgent.type == agent_type, CoreAgent.status == "active")
        .order_by(desc(CoreAgent.priority_score), desc(CoreAgent.reliability_score))
        .all()
    )

    for agent in candidates:
        # Check task_type capability
        if task_type and agent.supported_task_types:
            if task_type not in agent.supported_task_types:
                continue

        # Check channel support
        if channel and agent.supported_channels:
            if channel not in agent.supported_channels:
                continue

        log.info(
            f"routing: selected {agent.name} (priority={agent.priority_score}, reliability={agent.reliability_score:.2f})",
            extra={"agent": agent.name, "action": "routing.selected"},
        )
        return agent

    log.warning(f"routing: no eligible agent for type={agent_type} task={task_type} channel={channel}")
    return None
