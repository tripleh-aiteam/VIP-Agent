"""
VIP AI Platform — Cross-Agent Action Planner
Executes coordinated multi-agent workflows from chat.
Deterministic workflow mapping for MVP — no autonomous planning.
"""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from services.task_service import create_task, dispatch_task
from services import a2a_service, report_service
from services.audit_service import record_event
from services.event_bus import publish
from services.logger import log


# ---------------------------------------------------------------------------
# Workflow definitions (deterministic mapping)
# ---------------------------------------------------------------------------

WORKFLOWS = {
    "risk_check": {
        "name": "Cross-Agent Risk Check",
        "steps": [
            {"task_type": "stock_analysis", "agent_type": "stock", "label": "Stock Market Analysis"},
            {"task_type": "asset_summary", "agent_type": "asset", "label": "Portfolio Review"},
        ],
        "a2a_messages": [
            {"sender": "Stock Agent", "target": "Asset Agent", "type": "risk_alert", "purpose": "escalate",
             "payload": {"alert": "Risk check triggered from chat", "type": "cross_agent_risk_check"}},
        ],
        "compose_report": "daily_summary",
    },
    "full_executive": {
        "name": "Full Executive Summary",
        "steps": [
            {"task_type": "asset_summary", "agent_type": "asset", "label": "Asset Summary"},
            {"task_type": "stock_analysis", "agent_type": "stock", "label": "Stock Analysis"},
            {"task_type": "realty_listing_fetch", "agent_type": "realty", "label": "Real Estate Summary"},
        ],
        "a2a_messages": [],
        "compose_report": "daily_summary",
    },
    "comparison": {
        "name": "Asset vs Stock Comparison",
        "steps": [
            {"task_type": "asset_summary", "agent_type": "asset", "label": "Asset View"},
            {"task_type": "stock_analysis", "agent_type": "stock", "label": "Stock View"},
        ],
        "a2a_messages": [
            {"sender": "Asset Agent", "target": "Stock Agent", "type": "data_request", "purpose": "query",
             "payload": {"request": "cross_comparison", "context": "Asset vs Stock comparison"}},
        ],
        "compose_report": None,
    },
    "realty_market": {
        "name": "Real Estate + Market Risk",
        "steps": [
            {"task_type": "realty_listing_fetch", "agent_type": "realty", "label": "Real Estate Summary"},
            {"task_type": "stock_analysis", "agent_type": "stock", "label": "Market Risk Check"},
        ],
        "a2a_messages": [
            {"sender": "Real Estate Agent", "target": "Stock Agent", "type": "data_request", "purpose": "query",
             "payload": {"request": "market_risk_for_realty", "context": "Cross-checking market conditions"}},
        ],
        "compose_report": None,
    },
}


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

def execute_cross_agent_workflow(
    db: Session,
    workflow_key: str,
    trace_id: str,
) -> dict:
    """Execute a multi-agent workflow: create tasks, dispatch, A2A, optionally compose report."""

    workflow = WORKFLOWS.get(workflow_key)
    if not workflow:
        return {"error": f"Unknown workflow: {workflow_key}", "available": list(WORKFLOWS.keys())}

    record_event(db, "cross-agent", "workflow.started", trace_id, {
        "workflow": workflow_key, "name": workflow["name"],
        "steps": len(workflow["steps"]), "a2a_count": len(workflow.get("a2a_messages", [])),
    })

    log.info(
        f"cross-agent: starting {workflow['name']} ({len(workflow['steps'])} steps)",
        extra={"trace_id": trace_id, "action": "cross_agent.started"},
    )

    results = {
        "workflow": workflow_key,
        "workflow_name": workflow["name"],
        "trace_id": trace_id,
        "task_results": [],
        "a2a_results": [],
        "report": None,
        "summary": "",
        "linked_ids": {"task_run_ids": [], "a2a_message_ids": [], "report_id": None},
    }

    # Step 1: Execute all tasks
    for step in workflow["steps"]:
        try:
            run = create_task(
                db=db, trace_id=trace_id,
                task_type=step["task_type"], target_agent_type=step["agent_type"],
                initiator_type="user", initiator_id="chatbot-cross-agent",
                source_channel="chat",
                input_payload={"from_cross_agent": True, "workflow": workflow_key},
            )
            run = dispatch_task(db, run.id)

            task_result = {
                "label": step["label"],
                "task_type": step["task_type"],
                "agent": run.target_agent.name if run.target_agent else step["agent_type"],
                "status": run.status,
                "task_run_id": str(run.id),
            }

            # Extract key metrics from output
            output = run.output_payload or {}
            if step["agent_type"] == "asset":
                task_result["metrics"] = {
                    "total_value": output.get("total_value"),
                    "risk_level": output.get("risk_level"),
                    "asset_count": output.get("asset_count"),
                }
            elif step["agent_type"] == "stock":
                task_result["metrics"] = {
                    "risk_score": output.get("risk_score"),
                    "sentiment": output.get("market_sentiment"),
                    "stocks_analyzed": output.get("symbols_analyzed"),
                }
            elif step["agent_type"] == "realty":
                task_result["metrics"] = {
                    "total_listings": output.get("total_listings"),
                    "avg_vacancy": output.get("avg_vacancy_pct"),
                    "avg_yield": output.get("avg_yield_pct"),
                }

            results["task_results"].append(task_result)
            results["linked_ids"]["task_run_ids"].append(str(run.id))

        except Exception as e:
            results["task_results"].append({
                "label": step["label"], "status": "failed", "error": str(e),
            })

    # Step 2: Execute A2A data flows (real cross-agent data requests)
    for msg in workflow.get("a2a_messages", []):
        try:
            if msg["type"] == "data_request":
                # Use the real cross-agent data request flow
                target_type = _agent_name_to_type(msg["target"])
                data_result = a2a_service.request_data_from_agent(
                    db=db,
                    requester_agent_id=msg["sender"],
                    target_agent_type=target_type,
                    trace_id=trace_id,
                    data_request=msg["payload"].get("request", "general_query"),
                    context=msg["payload"],
                )
                results["a2a_results"].append({
                    "type": msg["type"], "sender": msg["sender"], "target": msg["target"],
                    "request_message_id": data_result["request_message_id"],
                    "response_message_id": data_result["response_message_id"],
                    "data_success": data_result["success"],
                    "data_summary": data_result.get("summary"),
                })
                results["linked_ids"]["a2a_message_ids"].extend(data_result["a2a_chain"])
                # Store fetched data for potential use by other steps
                results.setdefault("cross_agent_data", {})[target_type] = data_result.get("data", {})
            else:
                # Non-data messages (risk_alert, escalation, etc.)
                a2a_result = a2a_service.send_message(
                    db=db, trace_id=trace_id,
                    sender_agent_id=msg["sender"], target_agent_id=msg["target"],
                    message_type=msg["type"], purpose=msg["purpose"],
                    payload=msg["payload"],
                    proof_of_intent={"reason": f"Cross-agent workflow: {workflow['name']}"},
                )
                results["a2a_results"].append({
                    "type": msg["type"], "sender": msg["sender"], "target": msg["target"],
                    "message_id": a2a_result["message_id"],
                })
                results["linked_ids"]["a2a_message_ids"].append(a2a_result["message_id"])
        except Exception as e:
            results["a2a_results"].append({"type": msg["type"], "error": str(e)})

    # Step 3: Compose report if configured
    if workflow.get("compose_report"):
        try:
            report = report_service.compose_report(
                db, report_type=workflow["compose_report"],
                hours_back=24, trace_id=trace_id,
            )
            results["report"] = {
                "report_id": report["report_id"],
                "type": report["report_type"],
                "summary": report["executive_summary"][:300],
                "source_runs": report["source_run_count"],
            }
            results["linked_ids"]["report_id"] = report["report_id"]
        except Exception as e:
            results["report"] = {"error": str(e)}

    # Step 4: Build human-readable summary
    results["summary"] = _build_summary(results)

    record_event(db, "cross-agent", "workflow.completed", trace_id, {
        "workflow": workflow_key,
        "tasks_completed": len([t for t in results["task_results"] if t.get("status") == "completed"]),
        "tasks_total": len(results["task_results"]),
        "a2a_sent": len(results["a2a_results"]),
        "has_report": results["report"] is not None,
    })

    log.info(
        f"cross-agent: {workflow['name']} completed",
        extra={"trace_id": trace_id, "action": "cross_agent.completed"},
    )

    # Publish workflow completion for notifications
    publish("a2a.workflow.completed", {
        "workflow": workflow_key,
        "workflow_name": workflow["name"],
        "trace_id": trace_id,
        "tasks_completed": len([t for t in results["task_results"] if t.get("status") == "completed"]),
        "tasks_total": len(results["task_results"]),
        "a2a_sent": len(results["a2a_results"]),
    })

    return results


_AGENT_NAME_TYPE_MAP = {
    "Asset Agent": "asset",
    "Stock Agent": "stock",
    "Real Estate Agent": "realty",
}

def _agent_name_to_type(name: str) -> str:
    """Map agent display name to agent type for adapter routing."""
    return _AGENT_NAME_TYPE_MAP.get(name, name.lower().replace(" agent", "").strip())


def _build_summary(results: dict) -> str:
    """Build a human-readable summary from cross-agent results."""
    lines = [f"Workflow: {results['workflow_name']}\n"]

    for task in results["task_results"]:
        icon = "✅" if task.get("status") == "completed" else "❌"
        lines.append(f"{icon} {task['label']}: {task.get('status', 'unknown')}")
        metrics = task.get("metrics", {})
        if metrics:
            metric_parts = []
            for k, v in metrics.items():
                if v is not None:
                    if isinstance(v, float):
                        metric_parts.append(f"{k}={v:.1f}")
                    else:
                        metric_parts.append(f"{k}={v}")
            if metric_parts:
                lines.append(f"   {', '.join(metric_parts)}")

    if results["a2a_results"]:
        lines.append(f"\nA2A: {len(results['a2a_results'])} inter-agent message(s) sent")

    if results.get("report") and not results["report"].get("error"):
        lines.append(f"\nReport: {results['report'].get('summary', '')[:200]}")

    completed = len([t for t in results["task_results"] if t.get("status") == "completed"])
    total = len(results["task_results"])
    lines.append(f"\nResult: {completed}/{total} tasks completed")

    return "\n".join(lines)
