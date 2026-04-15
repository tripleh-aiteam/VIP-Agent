"""
VIP AI Platform — Chat Service
Session management, message storage, placeholder response generation.
Chatbot is the human-facing interface — VIP Orchestrator remains the brain.
All actions go through orchestrator/judgement/audit — never bypassed.
"""

from datetime import datetime
from uuid import UUID, uuid4
from typing import Any

from sqlalchemy.orm import Session

from db.models import ChatSession, ChatMessage
from services.audit_service import record_event
from services.logger import log


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(
    db: Session,
    user_id: str,
    channel: str = "web",
    title: str = "New Chat",
    mode: str = "structured",
) -> dict:
    """Create a new chat session."""
    import os
    default_mode = os.getenv("CHAT_DEFAULT_MODE", "structured")
    trace_id = f"tr-chat-{int(datetime.utcnow().timestamp())}"

    session = ChatSession(
        user_id=user_id,
        channel=channel,
        mode=mode or default_mode,
        title=title,
        status="active",
    )
    db.add(session)
    db.flush()

    # Add system welcome message
    welcome = ChatMessage(
        session_id=session.id,
        role="system",
        message_type="plain_text",
        content_json={
            "text": "Welcome to VIP Agent Platform. I can help you with:\n- Check system status\n- Run tasks (asset, stock, realty)\n- View reports\n- Check pending approvals\n- Monitor agents\n\nType your request or use commands like /status, /agents, /report",
        },
        trace_id=trace_id,
    )
    db.add(welcome)

    record_event(db, "chatbot", "chat.session_created", trace_id, {
        "session_id": str(session.id), "user_id": user_id, "channel": channel,
    })

    log.info(f"chat: session created for {user_id}", extra={"trace_id": trace_id, "action": "chat.session_created"})

    db.commit()
    return _session_to_dict(session)


def list_sessions(db: Session, user_id: str | None = None, limit: int = 20) -> list[dict]:
    q = db.query(ChatSession)
    if user_id:
        q = q.filter(ChatSession.user_id == user_id)
    sessions = q.order_by(ChatSession.updated_at.desc()).limit(limit).all()
    return [_session_to_dict(s) for s in sessions]


def get_session(db: Session, session_id: UUID) -> dict | None:
    s = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not s:
        return None
    return _session_to_dict(s)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def add_message(
    db: Session,
    session_id: UUID,
    role: str,
    content: str,
    message_type: str = "plain_text",
    data: dict | None = None,
) -> dict:
    """Add a message to a session and generate assistant response."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise ValueError("Session not found")

    trace_id = f"tr-chat-msg-{int(datetime.utcnow().timestamp())}"

    # Store user message
    user_msg = ChatMessage(
        session_id=session_id,
        role=role,
        message_type=message_type,
        content_json={"text": content, "data": data or {}},
        trace_id=trace_id,
    )
    db.add(user_msg)
    db.flush()

    # Update session title if first user message
    user_msgs = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id, ChatMessage.role == "user"
    ).count()
    if user_msgs == 1:
        session.title = content[:80]

    session.updated_at = datetime.utcnow()

    # Mode-aware interpretation
    from services.interpreters import get_interpreter
    from services.formatters import get_formatter

    session_mode = session.mode or "structured"
    interpreter = get_interpreter(session_mode)
    formatter = get_formatter(session_mode)

    # Classify intent using mode-appropriate interpreter
    intent_result = interpreter.interpret(content)

    # Store intent in user message
    user_msg.content_json = {
        "text": content,
        "data": data or {},
        "intent": intent_result.to_dict(),
        "mode": session_mode,
    }

    record_event(db, "chatbot", "chat.message_received", trace_id, {
        "session_id": str(session_id), "role": role, "message_type": message_type,
        "intent": intent_result.intent, "confidence": intent_result.confidence,
        "mode": session_mode,
    })

    # Generate assistant response (deterministic action execution — same for both modes)
    raw_response = _generate_response_from_intent(db, session, intent_result, trace_id)

    # Format response using mode-appropriate formatter
    assistant_response = formatter.format(raw_response)

    assistant_msg = ChatMessage(
        session_id=session_id,
        role="assistant",
        message_type=assistant_response["type"],
        content_json=assistant_response["content"],
        trace_id=trace_id,
    )
    db.add(assistant_msg)

    record_event(db, "chatbot", "chat.response_sent", trace_id, {
        "session_id": str(session_id), "response_type": assistant_response["type"],
    })

    db.commit()

    return {
        "user_message": _msg_to_dict(user_msg),
        "assistant_message": _msg_to_dict(assistant_msg),
        "trace_id": trace_id,
    }


def get_messages(db: Session, session_id: UUID, limit: int = 100) -> list[dict]:
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
        .all()
    )
    return [_msg_to_dict(m) for m in msgs]


# ---------------------------------------------------------------------------
# Response generation (placeholder — future LLM integration point)
# ---------------------------------------------------------------------------

def _generate_response_from_intent(db: Session, session: ChatSession, intent_result, trace_id: str) -> dict:
    """Generate response based on classified intent. Routes through orchestrator — never bypasses."""
    intent = intent_result.intent
    entities = intent_result.entities

    if intent == "cross_agent_analysis":
        workflow = entities.get("workflow", "risk_check")
        return _response_cross_agent(db, workflow, trace_id)
    elif intent == "system_status":
        return _response_status(db, trace_id)
    elif intent == "agent_inspection":
        return _response_agents(db, trace_id)
    elif intent == "workflow_trigger":
        task_type = entities.get("task_type")
        agent_type = entities.get("agent_type")
        report_type = entities.get("report_type")
        # "run daily report" / "run weekly report" → compose report
        if report_type and not task_type:
            return _response_compose_report(db, report_type, trace_id)
        # "run asset/stock/realty" → dispatch task
        return _response_run_task(db, task_type or "asset_summary", agent_type or "asset", trace_id)
    elif intent == "report_explainer":
        return _response_report_qa(db, session, intent_result.original_text, trace_id)
    elif intent == "report_request":
        report_type = entities.get("report_type", "daily_summary")
        return _response_report(db, trace_id, report_type)
    elif intent == "approval_action":
        action = entities.get("action")
        case_id = entities.get("case_id") or entities.get("case_id_prefix")
        filter_type = entities.get("filter")
        if action and case_id:
            return _response_approve_reject(db, case_id, action, trace_id)
        if filter_type == "high_risk":
            return _response_high_risk_cases(db, trace_id)
        return _response_approvals(db, trace_id)
    elif intent == "judgement_explanation":
        case_id = entities.get("case_id") or entities.get("case_id_prefix")
        task_id = entities.get("task_id_prefix")
        if task_id and not case_id:
            return _response_why_pending(db, task_id, trace_id)
        return _response_judgement(db, trace_id, case_id)
    elif intent == "a2a_inspection":
        return _response_a2a(db, trace_id)
    elif intent == "aiglass_inspection":
        return _response_aiglass(db, trace_id)
    elif intent == "help":
        return _response_help()
    else:
        return _response_default(intent_result.original_text)


def _generate_response(db: Session, session: ChatSession, user_input: str, trace_id: str) -> dict:
    """Legacy — kept for compatibility. Uses intent classifier internally."""
    from services.intent_service import classify
    intent_result = classify(user_input)
    return _generate_response_from_intent(db, session, intent_result, trace_id)

    text = user_input.lower().strip()

    # Command-style inputs
    if text.startswith("/status") or "status" in text:
        return _response_status(db, trace_id)
    elif text.startswith("/agents") or "agents" in text and "list" in text:
        return _response_agents(db, trace_id)
    elif text.startswith("/report") or "report" in text:
        return _response_report(db, trace_id)
    elif text.startswith("/approvals") or "approval" in text or "pending" in text:
        return _response_approvals(db, trace_id)
    elif "run" in text and ("asset" in text or "portfolio" in text):
        return _response_run_task(db, "asset_summary", "asset", trace_id)
    elif "run" in text and ("stock" in text or "market" in text):
        return _response_run_task(db, "stock_analysis", "stock", trace_id)
    elif "run" in text and ("realt" in text or "property" in text):
        return _response_run_task(db, "realty_listing_fetch", "realty", trace_id)
    elif text.startswith("/help") or "help" in text:
        return _response_help()
    else:
        return _response_default(user_input)


def _response_cross_agent(db: Session, workflow: str, trace_id: str) -> dict:
    """Execute a cross-agent workflow and return structured summary."""
    from services.cross_agent_service import execute_cross_agent_workflow

    try:
        result = execute_cross_agent_workflow(db, workflow, trace_id)

        if "error" in result:
            return {"type": "plain_text", "content": {"text": f"Workflow error: {result['error']}"}}

        # Build task metrics for card
        task_metrics = []
        for t in result.get("task_results", []):
            task_metrics.append({
                "label": t["label"],
                "status": t.get("status"),
                "agent": t.get("agent"),
                "metrics": t.get("metrics", {}),
            })

        return {
            "type": "workflow_result",
            "content": {
                "text": result["summary"],
                "data": {
                    "workflow": result["workflow"],
                    "workflow_name": result["workflow_name"],
                    "tasks": task_metrics,
                    "tasks_completed": len([t for t in task_metrics if t["status"] == "completed"]),
                    "tasks_total": len(task_metrics),
                    "a2a_count": len(result.get("a2a_results", [])),
                    "has_report": isinstance(result.get("report"), dict) and not result.get("report", {}).get("error"),
                    "report_summary": (result.get("report") or {}).get("summary", ""),
                },
                "action_result_type": "cross_agent_analysis",
                "trace_id": trace_id,
                "linked_object_ids": result.get("linked_ids", {}),
            },
        }
    except Exception as e:
        return {"type": "plain_text", "content": {"text": f"Cross-agent workflow failed: {e}"}}


def _response_status(db: Session, trace_id: str) -> dict:
    from db.models import CoreAgent, OrchTaskRun, AuditJudgementCase
    agents_active = db.query(CoreAgent).filter(CoreAgent.status == "active").count()
    agents_total = db.query(CoreAgent).count()
    runs_total = db.query(OrchTaskRun).count()
    runs_active = db.query(OrchTaskRun).filter(OrchTaskRun.status.in_(["pending", "dispatched", "running"])).count()
    runs_failed = db.query(OrchTaskRun).filter(OrchTaskRun.status == "failed").count()
    runs_completed = db.query(OrchTaskRun).filter(OrchTaskRun.status == "completed").count()
    pending_judgement = db.query(AuditJudgementCase).filter(
        AuditJudgementCase.decision.in_(["human_review_required", "conditional_approve"])
    ).count()

    text = (
        f"System is online.\n\n"
        f"Agents: {agents_active} active / {agents_total} total\n"
        f"Task Runs: {runs_total} total ({runs_completed} completed, {runs_active} active, {runs_failed} failed)\n"
        f"Pending Judgement: {pending_judgement}"
    )

    return {
        "type": "workflow_result",
        "content": {
            "text": text,
            "data": {
                "agents_active": agents_active, "agents_total": agents_total,
                "runs_total": runs_total, "runs_active": runs_active,
                "runs_completed": runs_completed, "runs_failed": runs_failed,
                "pending_judgement": pending_judgement, "status": "online",
            },
            "action_result_type": "system_status",
            "trace_id": trace_id,
        },
    }


def _response_agents(db: Session, trace_id: str) -> dict:
    from db.models import CoreAgent
    agents = db.query(CoreAgent).order_by(CoreAgent.priority_score.desc(), CoreAgent.name).all()

    unhealthy = [a for a in agents if a.status != "active"]
    low_reliability = [a for a in agents if (a.reliability_score or 1.0) < 0.7]

    lines = [f"{len(agents)} registered agent(s):\n"]
    for a in agents:
        icon = "🟢" if a.status == "active" else "🔴"
        mock = " [mock]" if a.is_mock else ""
        rel = f" reliability={int((a.reliability_score or 0)*100)}%" if a.reliability_score is not None else ""
        lines.append(f"{icon} {a.name}{mock} — {a.type} v{a.version} priority={a.priority_score}{rel}")

    if unhealthy:
        lines.append(f"\n⚠ {len(unhealthy)} unhealthy agent(s): {', '.join(a.name for a in unhealthy)}")
    if low_reliability:
        lines.append(f"⚠ {len(low_reliability)} low reliability: {', '.join(a.name for a in low_reliability)}")

    return {
        "type": "workflow_result",
        "content": {
            "text": "\n".join(lines),
            "data": {
                "count": len(agents), "unhealthy": len(unhealthy), "low_reliability": len(low_reliability),
                "agents": [{"name": a.name, "type": a.type, "status": a.status, "is_mock": a.is_mock, "priority": a.priority_score} for a in agents],
            },
            "action_result_type": "agent_inspection",
            "trace_id": trace_id,
        },
    }


def _response_report(db: Session, trace_id: str, report_type: str = "daily_summary") -> dict:
    from db.models import OrchReport
    report = db.query(OrchReport).filter(OrchReport.report_type == report_type).order_by(OrchReport.created_at.desc()).first()

    if not report:
        return {"type": "plain_text", "content": {"text": f"No {report_type.replace('_',' ')} available yet. Say 'run daily report' to generate one."}}

    summary = (report.content_json or {}).get("executive_summary", "No summary")
    sections = (report.content_json or {}).get("sections", [])
    section_titles = [s.get("title", "") for s in sections]

    return {
        "type": "report_summary",
        "content": {
            "text": f"{summary[:500]}\n\nSections: {', '.join(section_titles)}",
            "data": {
                "report_type": report_type,
                "sections": section_titles,
                "source_run_count": len(report.source_run_ids_json or []),
            },
            "action_result_type": "report_request",
            "trace_id": trace_id,
            "linked_object_ids": {"report_id": str(report.id)},
        },
    }


def _response_compose_report(db: Session, report_type: str, trace_id: str) -> dict:
    from services.report_service import compose_report
    rtype = report_type if report_type in ("daily_summary", "weekly_summary", "urgent_alert_summary") else "daily_summary"
    hours = 168 if "weekly" in rtype else 24
    try:
        result = compose_report(db, report_type=rtype, hours_back=hours, trace_id=trace_id)
        return {
            "type": "workflow_result",
            "content": {
                "text": f"Report composed: {rtype.replace('_',' ').title()}\n\n{result['executive_summary'][:400]}",
                "data": {"source_runs": result["source_run_count"], "report_type": rtype},
                "action_result_type": "workflow_trigger",
                "trace_id": trace_id,
                "linked_object_ids": {"report_id": result["report_id"]},
            },
        }
    except Exception as e:
        return {"type": "plain_text", "content": {"text": f"Failed to compose report: {e}"}}


def _response_approvals(db: Session, trace_id: str) -> dict:
    from db.models import AuditJudgementCase
    cases = db.query(AuditJudgementCase).filter(
        AuditJudgementCase.decision.in_(["human_review_required", "conditional_approve"])
    ).order_by(AuditJudgementCase.created_at.desc()).all()

    if not cases:
        return {"type": "approval_result", "content": {"text": "No pending approvals.", "data": {"count": 0}, "action_result_type": "approval_action", "trace_id": trace_id}}

    lines = [f"{len(cases)} pending approval(s):\n"]
    for c in cases:
        risk_pct = int((c.risk_score or 0) * 100)
        lines.append(f"• {str(c.id)[:8]}... Risk: {risk_pct}% — {c.decision}")
        lines.append(f"  Say: approve {c.id} or reject {c.id}")

    return {
        "type": "approval_result",
        "content": {
            "text": "\n".join(lines),
            "data": {"cases": [{"id": str(c.id), "risk": int((c.risk_score or 0)*100), "decision": c.decision} for c in cases], "count": len(cases)},
            "action_result_type": "approval_action",
            "trace_id": trace_id,
            "linked_object_ids": {"judgement_case_ids": [str(c.id) for c in cases]},
        },
    }


def _response_high_risk_cases(db: Session, trace_id: str) -> dict:
    """Show cases with high risk scores."""
    from db.models import AuditJudgementCase
    cases = db.query(AuditJudgementCase).filter(AuditJudgementCase.risk_score >= 0.4).order_by(AuditJudgementCase.risk_score.desc()).limit(10).all()

    if not cases:
        return {"type": "approval_result", "content": {"text": "No high-risk cases found.", "data": {"count": 0}, "action_result_type": "approval_action", "trace_id": trace_id}}

    lines = [f"Found {len(cases)} high-risk case(s):\n"]
    case_list = []
    for c in cases:
        risk_pct = int((c.risk_score or 0) * 100)
        evidence = c.evidence_json or {}
        failed_rules = [r for r in evidence.get("rule_details", []) if not r.get("passed")]

        lines.append(f"{'🔴' if risk_pct >= 70 else '🟡'} {str(c.id)[:8]}... — Risk: {risk_pct}% — {c.decision}")
        if failed_rules:
            lines.append(f"   Failed: {', '.join(r['rule'] for r in failed_rules[:3])}")
        if c.decision in ("human_review_required", "conditional_approve"):
            lines.append(f"   → approve {c.id}")
            lines.append(f"   → reject {c.id}")

        case_list.append({
            "id": str(c.id), "risk": risk_pct, "decision": c.decision,
            "failed_rules": [r["rule"] for r in failed_rules],
            "actionable": c.decision in ("human_review_required", "conditional_approve"),
        })

    return {
        "type": "approval_result",
        "content": {
            "text": "\n".join(lines),
            "data": {"cases": case_list, "count": len(cases), "filter": "high_risk"},
            "action_result_type": "approval_action",
            "trace_id": trace_id,
            "linked_object_ids": {"judgement_case_ids": [str(c.id) for c in cases]},
        },
    }


def _response_why_pending(db: Session, task_id_prefix: str, trace_id: str) -> dict:
    """Explain why a task is pending or stuck."""
    from db.models import OrchTaskRun, AuditJudgementCase

    # Find task by prefix
    runs = db.query(OrchTaskRun).all()
    run = None
    for r in runs:
        if str(r.id).startswith(task_id_prefix):
            run = r
            break

    if not run:
        return {"type": "plain_text", "content": {"text": f"Task not found with prefix: {task_id_prefix}"}}

    lines = [f"Task: {str(run.id)[:8]}...", f"Type: {run.task_definition.task_type if run.task_definition else '?'}", f"Status: {run.status}"]

    if run.status == "review_required":
        case = db.query(AuditJudgementCase).filter(AuditJudgementCase.task_run_id == run.id).first()
        if case:
            risk_pct = int((case.risk_score or 0) * 100)
            lines.append(f"\nBlocked by judgement review:")
            lines.append(f"  Case: {str(case.id)[:8]}... Risk: {risk_pct}%")
            lines.append(f"  Decision: {case.decision}")
            evidence = case.evidence_json or {}
            if evidence.get("reasoning"):
                lines.append(f"  Reason: {evidence['reasoning'][:150]}")
            lines.append(f"\n→ approve {case.id}")
            lines.append(f"→ reject {case.id}")
        else:
            lines.append("\nAwaiting judgement review — no case created yet.")
    elif run.status == "pending":
        lines.append("\nTask is waiting to be dispatched. It has not been sent to an agent yet.")
    elif run.status in ("dispatched", "running"):
        lines.append(f"\nTask is currently being processed by {run.target_agent.name if run.target_agent else 'an agent'}.")
    elif run.status == "failed":
        lines.append(f"\nTask failed: {run.error_message or 'No error details'}")

    return {
        "type": "workflow_result",
        "content": {
            "text": "\n".join(lines),
            "data": {"task_id": str(run.id), "status": run.status},
            "action_result_type": "judgement_explanation",
            "trace_id": trace_id,
            "linked_object_ids": {"task_run_id": str(run.id)},
        },
    }


def _check_permission(action: str) -> tuple[bool, str]:
    """Permission check placeholder. Returns (allowed, reason)."""
    # PLACEHOLDER: In production, check user role, org permissions, etc.
    # For MVP, all actions are allowed with a warning
    return True, "Permission granted (MVP: all actions allowed. Production will enforce role-based access.)"


def _response_approve_reject(db: Session, case_id: str, action: str, trace_id: str) -> dict:
    """Execute approve or reject on a specific case with permission check."""
    from db.models import AuditJudgementCase
    from services.judgement_service import approve_case, reject_case
    from uuid import UUID

    # Permission check
    allowed, perm_msg = _check_permission(action)

    # Find case by full ID or prefix
    case = None
    try:
        case = db.query(AuditJudgementCase).filter(AuditJudgementCase.id == UUID(case_id)).first()
    except ValueError:
        # Try prefix match
        cases = db.query(AuditJudgementCase).all()
        for c in cases:
            if str(c.id).startswith(case_id):
                case = c
                break

    if not case:
        return {"type": "plain_text", "content": {"text": f"Case not found: {case_id}. Use 'show pending approvals' to see cases."}}

    if not allowed:
        return {"type": "plain_text", "content": {"text": f"Permission denied: {perm_msg}"}}

    risk_pct = int((case.risk_score or 0) * 100)
    evidence = case.evidence_json or {}
    failed_rules = [r for r in evidence.get("rule_details", []) if not r.get("passed")]

    try:
        if action == "approve":
            result = approve_case(db, case.id, "chatbot", trace_id)
            lines = [
                f"✅ Case Approved",
                f"Case: {str(case.id)[:8]}...",
                f"Previous risk: {risk_pct}% — {case.rule_result}",
                f"Task status: {result['task_status']}",
                f"\n{perm_msg}",
            ]
        else:
            result = reject_case(db, case.id, "chatbot", trace_id, "Rejected via chatbot")
            lines = [
                f"❌ Case Rejected",
                f"Case: {str(case.id)[:8]}...",
                f"Risk score: {risk_pct}%",
                f"Failed rules: {', '.join(r['rule'] for r in failed_rules) if failed_rules else 'none'}",
                f"Task status: {result['task_status']}",
                f"\n{perm_msg}",
            ]

        return {
            "type": "approval_result",
            "content": {
                "text": "\n".join(lines),
                "data": {
                    **result,
                    "risk_score": risk_pct,
                    "rule_result": case.rule_result,
                    "failed_rules": [r["rule"] for r in failed_rules],
                    "action_taken": action,
                },
                "action_result_type": "approval_action",
                "trace_id": trace_id,
                "linked_object_ids": {"judgement_case_id": str(case.id)},
            },
        }
    except Exception as e:
        return {"type": "plain_text", "content": {"text": f"Failed to {action}: {e}"}}


def _response_run_task(db: Session, task_type: str, agent_type: str, trace_id: str) -> dict:
    from services.task_service import create_task, dispatch_task

    try:
        run = create_task(
            db=db, trace_id=trace_id, task_type=task_type, target_agent_type=agent_type,
            initiator_type="user", initiator_id="chatbot", source_channel="chat",
            input_payload={"from_chatbot": True},
        )
        run = dispatch_task(db, run.id)

        agent_name = run.target_agent.name if run.target_agent else agent_type
        output = run.output_payload or {}

        # Use formatted report if available, otherwise show summary
        report_text = output.get("report_text") or output.get("summary") or ""
        if report_text:
            display_text = report_text
        else:
            display_text = f"Task completed: {task_type} → {agent_name}"

        return {
            "type": "workflow_result",
            "content": {
                "text": display_text,
                "data": {"task_type": task_type, "agent": agent_name, "status": run.status, "risk_level": output.get("risk_level")},
                "action_result_type": "workflow_trigger",
                "trace_id": trace_id,
                "linked_object_ids": {"task_run_id": str(run.id)},
            },
        }
    except Exception as e:
        return {"type": "plain_text", "content": {"text": f"Failed to run task: {e}"}}


def _response_judgement(db: Session, trace_id: str, case_id: str | None = None) -> dict:
    from db.models import AuditJudgementCase

    # Specific case explanation
    if case_id:
        from uuid import UUID
        case = None
        try:
            case = db.query(AuditJudgementCase).filter(AuditJudgementCase.id == UUID(case_id)).first()
        except ValueError:
            cases_all = db.query(AuditJudgementCase).all()
            for c in cases_all:
                if str(c.id).startswith(case_id):
                    case = c
                    break

        if case:
            evidence = case.evidence_json or {}
            risk_pct = int((case.risk_score or 0) * 100)
            rules = evidence.get("rule_details", [])
            failed_rules = [r for r in rules if not r.get("passed")]
            factors = evidence.get("risk_factors", [])

            lines = [
                f"Judgement Case: {str(case.id)[:8]}...",
                f"Decision: {case.decision}",
                f"Risk Score: {risk_pct}%",
                f"Rule Result: {case.rule_result}",
            ]
            if evidence.get("reasoning"):
                lines.append(f"\nReasoning: {evidence['reasoning']}")
            if failed_rules:
                lines.append(f"\nFailed Rules ({len(failed_rules)}):")
                for r in failed_rules:
                    lines.append(f"  • {r['rule']}: {r['reason']} [{r['severity']}]")
            if factors:
                lines.append(f"\nRisk Factors:")
                for f in factors:
                    lines.append(f"  • {f['factor']}: +{f['points']}pts — {f['detail']}")

            return {
                "type": "workflow_result",
                "content": {
                    "text": "\n".join(lines),
                    "data": {"risk_score": risk_pct, "decision": case.decision, "failed_rules": len(failed_rules), "factors": len(factors)},
                    "action_result_type": "judgement_explanation",
                    "trace_id": trace_id,
                    "linked_object_ids": {"judgement_case_id": str(case.id), "task_run_id": str(case.task_run_id)},
                },
            }

    # General listing
    cases = db.query(AuditJudgementCase).order_by(AuditJudgementCase.created_at.desc()).limit(5).all()
    if not cases:
        return {"type": "plain_text", "content": {"text": "No judgement cases found."}}

    lines = [f"Recent {len(cases)} judgement case(s):\n"]
    for c in cases:
        risk_pct = int((c.risk_score or 0) * 100)
        evidence = c.evidence_json or {}
        lines.append(f"• {str(c.id)[:8]}... Risk: {risk_pct}% → {c.decision}")
        if evidence.get("reasoning"):
            lines.append(f"  {evidence['reasoning'][:80]}")

    return {
        "type": "workflow_result",
        "content": {
            "text": "\n".join(lines),
            "data": {"count": len(cases)},
            "action_result_type": "judgement_explanation",
            "trace_id": trace_id,
            "linked_object_ids": {"judgement_case_ids": [str(c.id) for c in cases]},
        },
    }


def _response_a2a(db: Session, trace_id: str) -> dict:
    from db.models import A2AMessage
    msgs = db.query(A2AMessage).order_by(A2AMessage.created_at.desc()).limit(5).all()
    if not msgs:
        return {"type": "plain_text", "content": {"text": "No A2A messages found."}}

    lines = [f"Recent {len(msgs)} A2A message(s):"]
    for m in msgs:
        sender = m.sender_agent.name if m.sender_agent else "?"
        target = m.target_agent.name if m.target_agent else "?"
        lines.append(f"• {m.message_type}: {sender} → {target} [{m.status}]")

    return {"type": "workflow_result", "content": {"text": "\n".join(lines), "data": {"count": len(msgs)}}}


def _response_aiglass(db: Session, trace_id: str) -> dict:
    from db.models import RealtySpatialCaptureSession
    sessions = db.query(RealtySpatialCaptureSession).order_by(RealtySpatialCaptureSession.created_at.desc()).limit(5).all()
    if not sessions:
        return {"type": "plain_text", "content": {"text": "No AI Glass capture sessions found."}}

    lines = [f"Recent {len(sessions)} capture session(s):"]
    for s in sessions:
        lines.append(f"• {s.device_id} — {s.property_ref or 'no ref'} [{s.processing_status}]")

    return {"type": "workflow_result", "content": {"text": "\n".join(lines), "data": {"count": len(sessions)}}}


def _response_report_qa(db: Session, session: ChatSession, question: str, trace_id: str) -> dict:
    """Answer follow-up questions about reports using stored data only."""
    from services.report_qa_service import load_report_context, answer_question

    # Session memory: check if a report is already focused
    # Look at recent messages for a report_id reference
    focused_report_id = None
    if session.messages:
        for msg in reversed(session.messages[-10:]):
            linked = (msg.content_json or {}).get("linked_object_ids", {})
            if linked.get("report_id"):
                focused_report_id = linked["report_id"]
                break

    # Load report context
    report_ctx = load_report_context(db, report_id=focused_report_id)
    if not report_ctx:
        return {"type": "plain_text", "content": {"text": "No report available to explain. Try 'run daily report' first, then ask questions about it."}}

    # Answer the question
    result = answer_question(report_ctx, question)

    sections_text = ""
    if result["sections_used"]:
        sections_text = f"\n\nBased on: {', '.join(result['sections_used'])}"

    return {
        "type": "report_summary",
        "content": {
            "text": result["answer"] + sections_text,
            "data": {
                "question_category": result["question_category"],
                "sections_used": result["sections_used"],
                "report_type": result["report_type"],
                "grounded": result["grounded"],
            },
            "action_result_type": "report_explainer",
            "trace_id": trace_id,
            "linked_object_ids": {"report_id": result["report_id"]},
        },
    }


def _response_help() -> dict:
    return {
        "type": "plain_text",
        "content": {
            "text": "I can help you with:\n\n"
                    "- \"status\" — System health overview\n"
                    "- \"list agents\" — Show registered agents\n"
                    "- \"report\" — Latest daily report\n"
                    "- \"approvals\" — Pending judgement cases\n"
                    "- \"run asset summary\" — Trigger asset analysis\n"
                    "- \"run stock analysis\" — Trigger stock check\n"
                    "- \"run realty listing\" — Trigger property search\n"
                    "- \"help\" — Show this message",
        },
    }


def _response_default(user_input: str) -> dict:
    return {
        "type": "plain_text",
        "content": {
            "text": f"I received your message: \"{user_input}\"\n\n"
                    "I'm currently in MVP mode with pattern-based responses. "
                    "Try: status, agents, report, approvals, or run [asset/stock/realty].\n\n"
                    "Natural language understanding will be added in a future update.",
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def update_session_mode(db: Session, session_id: UUID, mode: str) -> dict | None:
    """Update the mode of a chat session."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        return None
    if mode not in ("structured", "llm", "ai_assist"):
        raise ValueError("Mode must be 'structured' or 'llm'")
    session.mode = mode
    session.updated_at = datetime.utcnow()
    db.commit()
    return _session_to_dict(session)


def _session_to_dict(s: ChatSession) -> dict:
    return {
        "id": str(s.id),
        "user_id": s.user_id,
        "channel": s.channel,
        "mode": s.mode or "structured",
        "title": s.title,
        "status": s.status,
        "message_count": len(s.messages) if s.messages else 0,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _msg_to_dict(m: ChatMessage) -> dict:
    return {
        "id": str(m.id),
        "session_id": str(m.session_id),
        "role": m.role,
        "message_type": m.message_type,
        "content": m.content_json,
        "trace_id": m.trace_id,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }
