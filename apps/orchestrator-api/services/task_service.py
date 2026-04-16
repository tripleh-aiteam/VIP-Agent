"""
VIP AI Platform — Task Service
Core orchestration logic: create tasks, dispatch, handle callbacks, manage status flow.

Status flow: pending -> dispatched -> running -> completed / failed / review_required
"""

from datetime import datetime
from uuid import UUID
from typing import Any
import time

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
# Retry & Circuit Breaker Config
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = [1, 3, 5]  # wait between retries

# Circuit breaker: track consecutive failures per agent
_agent_failures: dict[str, int] = {}  # agent_id -> consecutive failure count
CIRCUIT_BREAKER_THRESHOLD = 3  # failures before tripping
CIRCUIT_BREAKER_COOLDOWN = 300  # seconds before trying again


_agent_tripped_at: dict[str, float] = {}  # agent_id -> timestamp when circuit tripped


def _is_circuit_open(agent_id: str) -> bool:
    """Check if circuit breaker is tripped for an agent."""
    if agent_id not in _agent_failures:
        return False
    if _agent_failures[agent_id] < CIRCUIT_BREAKER_THRESHOLD:
        return False
    # Check cooldown
    tripped_at = _agent_tripped_at.get(agent_id, 0)
    if time.time() - tripped_at > CIRCUIT_BREAKER_COOLDOWN:
        # Cooldown expired — reset and allow retry
        _agent_failures[agent_id] = 0
        log.info(f"circuit breaker: reset for {agent_id} after cooldown", extra={"action": "circuit_breaker.reset"})
        return False
    return True


def _record_agent_success(agent_id: str):
    """Reset failure count on success."""
    _agent_failures[agent_id] = 0


def _record_agent_failure(agent_id: str):
    """Increment failure count. Trip circuit if threshold reached."""
    _agent_failures[agent_id] = _agent_failures.get(agent_id, 0) + 1
    if _agent_failures[agent_id] >= CIRCUIT_BREAKER_THRESHOLD:
        _agent_tripped_at[agent_id] = time.time()
        log.warning(
            f"circuit breaker: TRIPPED for {agent_id} after {_agent_failures[agent_id]} failures",
            extra={"action": "circuit_breaker.tripped"},
        )


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
        is_mock=agent.is_mock,
        timeout_seconds=30,
        auth_type=agent.auth_type,
    )

    # Check circuit breaker
    agent_key = str(agent.id)
    if _is_circuit_open(agent_key):
        run.status = "failed"
        run.error_message = f"Circuit breaker open: {agent.name} has failed {CIRCUIT_BREAKER_THRESHOLD}+ times. Cooldown {CIRCUIT_BREAKER_COOLDOWN}s."
        run.finished_at = datetime.utcnow()
        record_event(db, "orchestrator", "task.circuit_breaker", run.trace_id, {
            "task_run_id": str(run.id), "agent": agent.name,
        })
        log.warning(f"circuit breaker: skipping {agent.name}", extra={"trace_id": run.trace_id, "action": "dispatch.circuit_breaker"})
        db.commit()
        return run

    if agent.endpoint_url:
        # Retry loop
        result = None
        attempts = 0
        for attempt in range(MAX_RETRIES):
            attempts = attempt + 1
            result = adapter.execute(
                task_run_id=str(run.id),
                trace_id=run.trace_id,
                task_type=task_type,
                input_payload=run.input_payload or {},
            )
            if result.success:
                break
            # Don't retry if it's a clear application error (not a connection issue)
            if result.error_message and "offline" not in result.error_message and "Timeout" not in result.error_message and "Connection" not in (result.error_message or ""):
                break
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF_SECONDS[attempt] if attempt < len(RETRY_BACKOFF_SECONDS) else 5
                log.info(f"retry {attempt + 1}/{MAX_RETRIES}: {agent.name} — waiting {wait}s", extra={"trace_id": run.trace_id, "action": "dispatch.retry"})
                time.sleep(wait)

        if attempts > 1:
            record_event(db, "orchestrator", "task.retried", run.trace_id, {
                "task_run_id": str(run.id), "agent": agent.name, "attempts": attempts, "success": result.success,
            })

        if result.success:
            _record_agent_success(agent_key)
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
            _record_agent_failure(agent_key)
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
                run.error_message = f"[{attempts} attempts] {result.error_message}"
                run.finished_at = datetime.utcnow()
        else:
            _record_agent_failure(agent_key)
            run.status = "failed"
            run.error_message = f"[{attempts} attempts] {result.error_message}"
            run.finished_at = datetime.utcnow()
            record_event(db, "orchestrator", "task.failed", run.trace_id, {
                "task_run_id": str(run.id),
                "agent": agent.name,
                "error": result.error_message,
                "attempts": attempts,
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
