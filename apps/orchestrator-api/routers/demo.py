"""
VIP AI Platform — End-to-End Demo Flow
Full scenario: user request → orchestrator → stock alert → A2A → judgement → report → dashboard
"""

from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db.base import get_db
from services import task_service, a2a_service, report_service
from services.telegram_service import handle_command
from services.logger import log

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/full-flow")
def run_full_demo(db: Session = Depends(get_db)):
    """
    Complete end-to-end demo:
    1. User requests stock analysis
    2. Orchestrator creates task, dispatches to stock agent
    3. Stock agent returns data (auto-judged since requires_judgement=true)
    4. Stock agent sends risk_alert via A2A
    5. Asset agent does portfolio exposure check via A2A
    6. Realty agent does exposure summary via A2A
    7. Daily report composed
    8. Telegram /status simulated
    """
    trace_id = f"tr-demo-e2e-{int(datetime.utcnow().timestamp())}"
    results = []

    # Step 1: Create stock analysis task
    try:
        run = task_service.create_task(
            db=db, trace_id=trace_id, task_type="stock_analysis",
            target_agent_type="stock", initiator_type="user",
            initiator_id="demo-user", source_channel="web",
            input_payload={"symbols": ["AAPL", "GOOGL", "005930.KS"], "demo": True},
        )
        results.append({"step": 1, "action": "Task created", "task_id": str(run.id), "status": run.status})
    except Exception as e:
        results.append({"step": 1, "action": "Task creation failed", "error": str(e)})
        return {"trace_id": trace_id, "steps": results}

    # Step 2: Dispatch to stock agent
    try:
        run = task_service.dispatch_task(db, run.id)
        results.append({"step": 2, "action": "Task dispatched", "status": run.status,
                        "agent": run.target_agent.name if run.target_agent else None,
                        "auto_judged": run.status in ("review_required", "completed", "failed")})
    except Exception as e:
        results.append({"step": 2, "action": "Dispatch failed", "error": str(e)})

    # Step 3: A2A risk alert (stock → asset)
    try:
        a2a_1 = a2a_service.send_message(
            db=db, trace_id=trace_id,
            sender_agent_id="Stock Agent", target_agent_id="Asset Agent",
            message_type="risk_alert", purpose="escalate",
            payload={"alert": "Stock volatility detected", "symbols": ["AAPL", "GOOGL"], "risk_level": "high"},
            proof_of_intent={"reason": "Demo: stock analysis triggered risk alert"},
        )
        results.append({"step": 3, "action": "A2A risk_alert sent", "message_id": a2a_1["message_id"], "is_high_risk": True})
    except Exception as e:
        results.append({"step": 3, "action": "A2A failed", "error": str(e)})

    # Step 4: A2A data_request (asset → stock for exposure check)
    try:
        a2a_2 = a2a_service.send_message(
            db=db, trace_id=trace_id,
            sender_agent_id="Asset Agent", target_agent_id="Stock Agent",
            message_type="data_request", purpose="query",
            payload={"request": "portfolio_exposure", "portfolios": ["PF-1234"]},
            proof_of_intent={"reason": "Demo: checking portfolio exposure after risk alert"},
        )
        results.append({"step": 4, "action": "A2A data_request sent", "message_id": a2a_2["message_id"]})
    except Exception as e:
        results.append({"step": 4, "action": "A2A failed", "error": str(e)})

    # Step 5: A2A report_request (asset → realty for exposure)
    try:
        a2a_3 = a2a_service.send_message(
            db=db, trace_id=trace_id,
            sender_agent_id="Asset Agent", target_agent_id="Real Estate Agent",
            message_type="report_request", purpose="delegate",
            payload={"request": "realty_exposure_summary", "regions": ["Seoul-Gangnam"]},
            proof_of_intent={"reason": "Demo: cross-asset exposure check"},
        )
        results.append({"step": 5, "action": "A2A report_request sent", "message_id": a2a_3["message_id"]})
    except Exception as e:
        results.append({"step": 5, "action": "A2A failed", "error": str(e)})

    # Step 6: Compose daily report
    try:
        report = report_service.compose_report(db, report_type="daily_summary", hours_back=48, trace_id=trace_id)
        results.append({"step": 6, "action": "Daily report composed", "report_id": report["report_id"],
                        "source_runs": report["source_run_count"]})
    except Exception as e:
        results.append({"step": 6, "action": "Report failed", "error": str(e)})

    # Step 7: Telegram /status simulation
    try:
        tg_response = handle_command(db, "admin_000", "demo-chat", "/status", [])
        results.append({"step": 7, "action": "Telegram /status simulated", "response_preview": tg_response[:150]})
    except Exception as e:
        results.append({"step": 7, "action": "Telegram failed", "error": str(e)})

    log.info(f"demo: full flow completed ({len(results)} steps)", extra={"trace_id": trace_id, "action": "demo.completed"})

    return {
        "demo": "full-e2e-flow",
        "trace_id": trace_id,
        "total_steps": len(results),
        "steps": results,
        "verify": {
            "dashboard": "http://localhost:3000",
            "runs": f"http://localhost:8000/runs?limit=5",
            "a2a": f"http://localhost:8000/a2a/messages?trace_id={trace_id}",
            "reports": "http://localhost:8000/reports/",
            "judgement": "http://localhost:8000/judgement/cases",
            "supabase": "Check orch_task_runs, a2a_messages, audit_event_logs, orch_reports",
        },
    }
