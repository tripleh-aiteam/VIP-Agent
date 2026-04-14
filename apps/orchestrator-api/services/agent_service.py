"""
VIP AI Platform — Agent Service
Resolves which agent handles a task, registers new agents, dispatches work.
"""

from uuid import UUID
from sqlalchemy.orm import Session
from db.models import CoreAgent
from services.audit_service import record_event
from services.logger import log


def resolve_agent(db: Session, target_agent_type: str) -> CoreAgent | None:
    """Find an active agent matching the requested type."""
    agent = (
        db.query(CoreAgent)
        .filter(CoreAgent.type == target_agent_type, CoreAgent.status == "active")
        .first()
    )
    if agent:
        log.info(
            f"resolved agent: {agent.name}",
            extra={"agent": agent.name, "action": "resolve_agent"},
        )
    else:
        log.warning(f"no active agent for type: {target_agent_type}")
    return agent


def register_agent(
    db: Session,
    name: str,
    agent_type: str,
    endpoint_url: str,
    trace_id: str,
    version: str = "0.1.0",
    owner_team: str | None = None,
    auth_type: str = "none",
    is_mock: bool = False,
    capabilities: dict | None = None,
) -> CoreAgent:
    """Register a new agent or update an existing one."""
    existing = db.query(CoreAgent).filter(CoreAgent.name == name).first()

    if existing:
        existing.endpoint_url = endpoint_url
        existing.version = version
        existing.status = "active"
        existing.capabilities_json = capabilities or existing.capabilities_json
        db.flush()

        record_event(db, "orchestrator", "agent.updated", trace_id, {
            "agent_name": name, "endpoint": endpoint_url,
        })
        log.info(f"agent updated: {name}", extra={"agent": name, "action": "agent.updated"})
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
    )
    db.add(agent)
    db.flush()

    record_event(db, "orchestrator", "agent.registered", trace_id, {
        "agent_name": name, "agent_type": agent_type, "endpoint": endpoint_url,
    })
    log.info(f"agent registered: {name}", extra={"agent": name, "action": "agent.registered"})
    return agent


def get_agent_by_id(db: Session, agent_id: UUID) -> CoreAgent | None:
    return db.query(CoreAgent).filter(CoreAgent.id == agent_id).first()


def list_agents(db: Session) -> list[CoreAgent]:
    return db.query(CoreAgent).order_by(CoreAgent.name).all()
