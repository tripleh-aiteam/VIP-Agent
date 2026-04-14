"""
VIP AI Platform — Task Service
Core orchestration logic: create tasks, dispatch, handle callbacks, manage status flow.

Status flow: pending -> dispatched -> running -> completed / failed / review_required
"""

from datetime import datetime
from uuid import UUID
from typing import Any

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from sqlalchemy.orm import Session

from db.models import CoreAgent, OrchTaskDefinition, OrchTaskRun
from services.agent_service import resolve_agent
from services.audit_service import record_event
from services.logger import log
from adapters import get_adapter


# ---------------------------------------------------------------------------
# Task creation
# ---------------------------------------------------------------------------

def create_task(
    db: Session,
    trace_id: str,
    task_type: str,
    target_agent_type: str,
    initiator_type: str,
    initiator_id: str,
    source_channel: str | None,
    input_payload: dict[str, Any],
    priority: str = "medium",
) -> OrchTaskRun:
    """Create a task run record in pending state."""

    # Resolve task definition
    task_def = (
        db.query(OrchTaskDefinition)
        .filter(OrchTaskDefinition.task_type == task_type)
        .first()
    )
    if not task_def:
        raise ValueError(f"Unknown task_type: {task_type}")

    # Resolve target agent
    agent = resolve_agent(db, target_agent_type)
    if not agent:
        raise ValueError(f"No active agent for type: {target_agent_type}")

    # Create run
    run = OrchTaskRun(
        task_definition_id=task_def.id,
        initiator_type=initiator_type,
        initiator_id=initiator_id,
        source_channel=source_channel,
        target_agent_id=agent.id,
        trace_id=trace_id,
        input_payload=input_payload,
        status="pending",
    )
    db.add(run)
    db.flush()

    record_event(db, "orchestrator", "task.created", trace_id, {
        "task_run_id": str(run.id),
        "task_type": task_type,
        "agent": agent.name,
        "priority": priority,
    })

    log.info(
        f"task created: {task_type} -> {agent.name}",
        extra={"trace_id": trace_id, "task_id": str(run.id), "agent": agent.name, "action": "task.created"},
    )

    db.commit()
    return run


# ---------------------------------------------------------------------------
# Task dispatch
# ---------------------------------------------------------------------------

def dispatch_task(db: Session, task_run_id: UUID) -> OrchTaskRun:
    """Dispatch a pending task to its assigned agent. Retry-safe: skips if already dispatched."""

    run = db.query(OrchTaskRun).filter(OrchTaskRun.id == task_run_id).first()
    if not run:
        raise ValueError(f"Task run not found: {task_run_id}")

    # Retry-safe: only dispatch from pending
    if run.status != "pending":
        log.info(
            f"dispatch skipped: already {run.status}",
            extra={"trace_id": run.trace_id, "task_id": str(run.id), "action": "dispatch.skipped"},
        )
        return run

    agent = db.query(CoreAgent).filter(CoreAgent.id == run.target_agent_id).first()
    if not agent:
        raise ValueError(f"Agent not found for task run: {task_run_id}")

    # Update status to dispatched
    run.status = "dispatched"
    run.started_at = datetime.utcnow()
    db.flush()

    record_event(db, "orchestrator", "task.dispatched", run.trace_id, {
        "task_run_id": str(run.id),
        "agent": agent.name,
        "endpoint": agent.endpoint_url,
    })

    # Dispatch via adapter layer
    task_type = run.task_definition.task_type if run.task_definition else "unknown"
    adapter = get_adapter(
        agent_type=agent.type,
        agent_name=agent.name,
        endpoint_url=agent.endpoint_url or "",
        timeout_seconds=30,
        auth_type=agent.auth_type,
    )

    if agent.endpoint_url:
        result = adapter.execute(
            task_run_id=str(run.id),
            trace_id=run.trace_id,
            task_type=task_type,
            input_payload=run.input_payload or {},
        )

        if result.success:
            run.output_payload = result.output_payload

            # Auto-route through judgement if required
            needs_judgement = run.task_definition and run.task_definition.requires_judgement
            if needs_judgement:
                from services.judgement_service import evaluate as judge_evaluate
                run.status = "review_required"
                run.finished_at = datetime.utcnow()
                db.flush()

                judgement_result = judge_evaluate(
                    db=db,
                    trace_id=run.trace_id,
                    task_run_id=run.id,
                    task_type=task_type,
                    agent_id=agent.name,
                    agent_output=result.output_payload or {},
                )
                # judgement_service already updates run.status based on decision
                log.info(
                    f"auto-judgement: {judgement_result['decision']} (risk={judgement_result['risk_score']})",
                    extra={"trace_id": run.trace_id, "task_id": str(run.id), "action": "dispatch.auto_judgement"},
                )
            else:
                run.status = "completed"
                run.finished_at = datetime.utcnow()

            record_event(db, "orchestrator", f"task.{run.status}", run.trace_id, {
                "task_run_id": str(run.id),
                "agent": agent.name,
                "summary": result.summary,
                "via_adapter": True,
                "judged": needs_judgement,
            })
            log.info(
                f"adapter dispatch success: {agent.name} -> {run.status}",
                extra={"trace_id": run.trace_id, "task_id": str(run.id), "agent": agent.name, "action": "dispatch.adapter.success"},
            )
        elif result.status == "failed" and "offline" in (result.error_message or ""):
            # Agent offline — fallback for mock agents
            if agent.is_mock:
                run.status = "completed"
                run.output_payload = {
                    "mock_fallback": True,
                    "message": f"Mock {agent.type} agent offline — simulated response",
                    "agent": agent.name,
                }
                run.finished_at = datetime.utcnow()
                log.info(f"mock fallback: {agent.name}", extra={"trace_id": run.trace_id, "action": "dispatch.mock_fallback"})
            else:
                run.status = "failed"
                run.error_message = result.error_message
                run.finished_at = datetime.utcnow()
        else:
            run.status = "failed"
            run.error_message = result.error_message
            run.finished_at = datetime.utcnow()
            record_event(db, "orchestrator", "task.failed", run.trace_id, {
                "task_run_id": str(run.id),
                "agent": agent.name,
                "error": result.error_message,
            })
    else:
        # No endpoint — immediate mock completion
        run.status = "completed"
        run.output_payload = {"mock": True, "message": f"No endpoint for {agent.name}", "agent": agent.name}
        run.finished_at = datetime.utcnow()

    db.commit()
    return run


# ---------------------------------------------------------------------------
# Callback handling
# ---------------------------------------------------------------------------

def handle_agent_callback(
    db: Session,
    task_run_id: UUID,
    trace_id: str,
    agent_id: str,
    status: str,
    output_payload: dict[str, Any] | None,
    error_message: str | None,
    summary: str | None = None,
) -> OrchTaskRun:
    """Process an agent's callback result."""

    run = db.query(OrchTaskRun).filter(OrchTaskRun.id == task_run_id).first()
    if not run:
        raise ValueError(f"Task run not found: {task_run_id}")

    # Only accept callbacks for dispatched/running tasks
    if run.status not in ("dispatched", "running"):
        log.warning(
            f"callback rejected: task is {run.status}",
            extra={"trace_id": trace_id, "task_id": str(run.id), "action": "callback.rejected"},
        )
        raise ValueError(f"Cannot accept callback: task is {run.status}")

    # Check if task definition requires judgement
    needs_judgement = False
    if run.task_definition and run.task_definition.requires_judgement and status == "completed":
        status = "review_required"
        needs_judgement = True

    run.status = status
    run.output_payload = output_payload
    run.error_message = error_message
    run.finished_at = datetime.utcnow()
    db.flush()

    record_event(db, "orchestrator", f"task.{status}", trace_id, {
        "task_run_id": str(run.id),
        "agent_id": agent_id,
        "summary": summary,
        "needs_judgement": needs_judgement,
    })

    log.info(
        f"callback received: {status}",
        extra={"trace_id": trace_id, "task_id": str(run.id), "action": f"task.{status}"},
    )

    db.commit()
    return run


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_task_run(db: Session, task_run_id: UUID) -> OrchTaskRun | None:
    return db.query(OrchTaskRun).filter(OrchTaskRun.id == task_run_id).first()


def list_runs(
    db: Session,
    status: str | None = None,
    limit: int = 50,
) -> list[OrchTaskRun]:
    q = db.query(OrchTaskRun)
    if status:
        q = q.filter(OrchTaskRun.status == status)
    return q.order_by(OrchTaskRun.started_at.desc().nullslast()).limit(limit).all()
