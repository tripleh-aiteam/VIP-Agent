"""
VIP AI Platform — Judgement Service
Creates judgement cases, manages approvals, links to task runs.
"""

from datetime import datetime
from uuid import UUID
from typing import Any

from sqlalchemy.orm import Session

from db.models import AuditJudgementCase, AuditApprovalRequest, OrchTaskRun
from services.judgement_engine import make_decision
from services.audit_service import record_event
from services.logger import log


def evaluate(
    db: Session,
    trace_id: str,
    task_run_id: UUID,
    task_type: str,
    agent_id: str,
    agent_output: dict[str, Any],
    rules: list[str] | None = None,
    context: dict[str, Any] | None = None,
    require_human_approval: bool = False,
) -> dict:
    """
    Run full judgement pipeline on a task run's output.
    Creates judgement_case and approval_request if needed.
    """
    ctx = context or {}
    ctx["agent_id"] = agent_id
    ctx["task_type"] = task_type
    if rules:
        ctx["active_rules"] = rules

    # Run the engine
    result = make_decision(agent_output, ctx)

    # Force human review if explicitly requested
    if require_human_approval and result["decision"] == "auto_approve":
        result["decision"] = "human_review_required"
        result["reasoning"] += " | Human approval explicitly required"

    # Create judgement case
    case = AuditJudgementCase(
        task_run_id=task_run_id,
        rule_result=result["rule_result"],
        model_result=f"risk_{result['risk_level']}",
        risk_score=result["risk_score"] / 100.0,  # normalize to 0-1
        decision=result["decision"],
        evidence_json={
            "rule_details": result["rule_details"],
            "risk_factors": result["risk_factors"],
            "reasoning": result["reasoning"],
            "agent_output_keys": list(agent_output.keys()),
        },
    )
    db.add(case)
    db.flush()

    # Create approval request if needed
    approval_id = None
    if result["decision"] in ("human_review_required", "conditional_approve"):
        approval = AuditApprovalRequest(
            judgement_case_id=case.id,
            requested_by="orchestrator",
            decision="pending",
        )
        db.add(approval)
        db.flush()
        approval_id = str(approval.id)

    # Update task run status based on decision
    run = db.query(OrchTaskRun).filter(OrchTaskRun.id == task_run_id).first()
    if run:
        if result["decision"] == "auto_approve":
            run.status = "completed"
        elif result["decision"] == "rejected":
            run.status = "failed"
            run.error_message = f"Judgement rejected: {result['reasoning']}"
        else:
            run.status = "review_required"
        run.finished_at = datetime.utcnow()

    # Audit
    record_event(db, "judgement", f"judgement.{result['decision']}", trace_id, {
        "case_id": str(case.id),
        "task_run_id": str(task_run_id),
        "risk_score": result["risk_score"],
        "decision": result["decision"],
    })

    log.info(
        f"judgement: {result['decision']} (risk={result['risk_score']}) for task {task_run_id}",
        extra={"trace_id": trace_id, "action": f"judgement.{result['decision']}"},
    )

    db.commit()

    return {
        "case_id": str(case.id),
        "task_run_id": str(task_run_id),
        "trace_id": trace_id,
        "rule_result": result["rule_result"],
        "rule_details": result["rule_details"],
        "risk_score": result["risk_score"],
        "risk_level": result["risk_level"],
        "risk_factors": result["risk_factors"],
        "decision": result["decision"],
        "reasoning": result["reasoning"],
        "approval_id": approval_id,
        "requires_approval": result["decision"] in ("human_review_required", "conditional_approve"),
    }


def list_cases(
    db: Session,
    decision: str | None = None,
    limit: int = 50,
) -> list[dict]:
    q = db.query(AuditJudgementCase)
    if decision:
        q = q.filter(AuditJudgementCase.decision == decision)
    cases = q.order_by(AuditJudgementCase.created_at.desc()).limit(limit).all()
    return [_case_to_dict(c) for c in cases]


def get_case(db: Session, case_id: UUID) -> dict | None:
    case = db.query(AuditJudgementCase).filter(AuditJudgementCase.id == case_id).first()
    if not case:
        return None
    return _case_to_dict(case)


def approve_case(db: Session, case_id: UUID, approved_by: str, trace_id: str) -> dict | None:
    """Approve a pending judgement case."""
    case = db.query(AuditJudgementCase).filter(AuditJudgementCase.id == case_id).first()
    if not case:
        return None

    # Update approval request
    approval = (
        db.query(AuditApprovalRequest)
        .filter(AuditApprovalRequest.judgement_case_id == case_id, AuditApprovalRequest.decision == "pending")
        .first()
    )
    if approval:
        approval.approved_by = approved_by
        approval.decision = "approved"
        approval.decided_at = datetime.utcnow()

    # Update case decision
    case.decision = "auto_approve"

    # Update linked task run
    run = db.query(OrchTaskRun).filter(OrchTaskRun.id == case.task_run_id).first()
    if run and run.status == "review_required":
        run.status = "completed"
        run.finished_at = datetime.utcnow()

    record_event(db, "judgement", "judgement.approved", trace_id, {
        "case_id": str(case_id), "approved_by": approved_by,
    })

    db.commit()
    return {"case_id": str(case_id), "decision": "approved", "approved_by": approved_by, "task_status": run.status if run else None}


def reject_case(db: Session, case_id: UUID, rejected_by: str, trace_id: str, reason: str = "") -> dict | None:
    """Reject a pending judgement case."""
    case = db.query(AuditJudgementCase).filter(AuditJudgementCase.id == case_id).first()
    if not case:
        return None

    approval = (
        db.query(AuditApprovalRequest)
        .filter(AuditApprovalRequest.judgement_case_id == case_id, AuditApprovalRequest.decision == "pending")
        .first()
    )
    if approval:
        approval.approved_by = rejected_by
        approval.decision = "denied"
        approval.decided_at = datetime.utcnow()

    case.decision = "rejected"

    run = db.query(OrchTaskRun).filter(OrchTaskRun.id == case.task_run_id).first()
    if run and run.status == "review_required":
        run.status = "failed"
        run.error_message = f"Rejected by {rejected_by}: {reason}"
        run.finished_at = datetime.utcnow()

    record_event(db, "judgement", "judgement.rejected", trace_id, {
        "case_id": str(case_id), "rejected_by": rejected_by, "reason": reason,
    })

    db.commit()
    return {"case_id": str(case_id), "decision": "rejected", "rejected_by": rejected_by, "task_status": run.status if run else None}


def _case_to_dict(c: AuditJudgementCase) -> dict:
    return {
        "id": str(c.id),
        "task_run_id": str(c.task_run_id),
        "rule_result": c.rule_result,
        "model_result": c.model_result,
        "risk_score": c.risk_score,
        "decision": c.decision,
        "evidence": c.evidence_json,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "approval_requests": [
            {
                "id": str(ar.id),
                "requested_by": ar.requested_by,
                "approved_by": ar.approved_by,
                "decision": ar.decision,
                "decided_at": ar.decided_at.isoformat() if ar.decided_at else None,
            }
            for ar in c.approval_requests
        ],
    }
