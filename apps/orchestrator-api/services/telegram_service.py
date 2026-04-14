"""
VIP AI Platform — Telegram Service
Handles inbound commands, outbound notifications, user linking, action logging.
All actions go through Orchestrator APIs — never bypasses them.
"""

import os
import httpx
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from db.base import SessionLocal
from db.models import TelegramUser, TelegramAction, CoreAgent, OrchTaskRun, AuditJudgementCase
from services.audit_service import record_event
from services.logger import log

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ---------------------------------------------------------------------------
# Outbound — send messages
# ---------------------------------------------------------------------------

def send_message(chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to a Telegram chat."""
    if not BOT_TOKEN:
        log.warning("telegram: no BOT_TOKEN configured, skipping send")
        return False

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(f"{API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            })
            if resp.status_code == 200:
                log.info(f"telegram: sent to {chat_id}", extra={"action": "telegram.send"})
                return True
            else:
                log.warning(f"telegram: send failed {resp.status_code}: {resp.text[:200]}")
                return False
    except Exception as e:
        log.warning(f"telegram: send error: {e}")
        return False


def send_alert(text: str):
    """Send alert to all admin Telegram users."""
    db = SessionLocal()
    try:
        admins = db.query(TelegramUser).filter(
            TelegramUser.role.in_(["admin", "operator"]),
            TelegramUser.status == "active",
        ).all()
        for admin in admins:
            send_message(admin.telegram_user_id, text)
    finally:
        db.close()


def send_daily_headline(summary: str):
    """Send daily report headline to all admin users."""
    text = f"<b>VIP Daily Report</b>\n\n{summary[:500]}"
    send_alert(text)


# ---------------------------------------------------------------------------
# Inbound — handle commands
# ---------------------------------------------------------------------------

# Map Telegram commands to natural language for the intent classifier
TELEGRAM_COMMAND_MAP = {
    "/status": "status",
    "/start": "status",
    "/agents": "show all agents",
    "/report": "show latest report",
    "/run_daily": "run daily report",
    "/run_weekly": "run weekly report",
    "/approvals": "show pending approvals",
    "/approve": "approve case",  # args appended
    "/reject": "reject case",    # args appended
    "/help": "help",
}


def _get_or_create_telegram_session(db: Session, telegram_user_id: str) -> str:
    """Get or create a chat session for a Telegram user. Channel-aware."""
    from db.models import ChatSession

    # Find existing active session for this Telegram user
    session = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == f"tg:{telegram_user_id}", ChatSession.channel == "telegram", ChatSession.status == "active")
        .order_by(ChatSession.updated_at.desc())
        .first()
    )

    if session:
        return str(session.id)

    # Create new session
    from services.chat_service import create_session
    result = create_session(db, user_id=f"tg:{telegram_user_id}", channel="telegram", title="Telegram Chat")
    return result["id"]


def handle_command(
    db: Session,
    telegram_user_id: str,
    chat_id: str,
    command: str,
    args: list[str],
) -> str:
    """
    Process a Telegram command using the SAME intent/chat layer as the dashboard.
    Routes through intent classifier → chat service → consistent responses.
    """

    # Log the Telegram action
    action = TelegramAction(
        telegram_user_id=telegram_user_id,
        action_type="command",
        payload_json={"command": command, "args": args, "chat_id": chat_id, "channel": "telegram"},
        status="processing",
    )
    db.add(action)
    db.flush()

    # Check user authorization
    user = db.query(TelegramUser).filter(TelegramUser.telegram_user_id == telegram_user_id).first()
    if not user:
        action.status = "unauthorized"
        db.commit()
        return "You are not registered. Contact an admin to link your Telegram account."

    trace_id = f"tr-tg-{command.strip('/')}-{int(datetime.utcnow().timestamp())}"

    try:
        # Convert Telegram command to natural language
        if command == "/help":
            action.status = "completed"
            db.commit()
            return _cmd_help()

        natural_text = TELEGRAM_COMMAND_MAP.get(command, command)
        if args:
            natural_text = f"{natural_text} {' '.join(args)}"

        # Route through the chat service (same layer as dashboard)
        from services.chat_service import add_message
        session_id = _get_or_create_telegram_session(db, telegram_user_id)

        from uuid import UUID
        result = add_message(
            db=db,
            session_id=UUID(session_id),
            role="user",
            content=natural_text,
            message_type="command",
            data={"telegram_command": command, "telegram_args": args, "telegram_chat_id": chat_id},
        )

        # Extract response text
        assistant_msg = result.get("assistant_message", {})
        content = assistant_msg.get("content", {})
        response_text = content.get("text", "No response generated.")

        # Convert to Telegram HTML format
        response = _format_for_telegram(response_text, content)

        action.status = "completed"
        action.payload_json = {
            **action.payload_json,
            "intent": content.get("action_result_type", "unknown"),
            "chat_session_id": session_id,
            "response_length": len(response),
        }

    except Exception as e:
        response = f"Error: {e}"
        action.status = "failed"
        action.payload_json = {**action.payload_json, "error": str(e)}

    record_event(db, "telegram", f"telegram.{command.strip('/')}", trace_id, {
        "user": telegram_user_id, "command": command, "status": action.status,
        "routed_through": "chat_service",
    })

    db.commit()
    return response


def _format_for_telegram(text: str, content: dict) -> str:
    """Format chat response for Telegram HTML. Add action type header."""
    action_type = content.get("action_result_type", "")

    # Add header based on action type
    headers = {
        "system_status": "📊 <b>System Status</b>",
        "agent_inspection": "🤖 <b>Agents</b>",
        "workflow_trigger": "⚡ <b>Workflow Result</b>",
        "report_request": "📋 <b>Report</b>",
        "report_explainer": "📋 <b>Report Q&A</b>",
        "approval_action": "⚖️ <b>Approvals</b>",
        "judgement_explanation": "🔍 <b>Judgement</b>",
        "cross_agent_analysis": "🔗 <b>Cross-Agent Analysis</b>",
        "a2a_inspection": "💬 <b>A2A Messages</b>",
        "aiglass_inspection": "👓 <b>AI Glass</b>",
    }

    header = headers.get(action_type, "")
    formatted = f"{header}\n\n{text}" if header else text

    # Truncate for Telegram (4096 char limit)
    if len(formatted) > 4000:
        formatted = formatted[:3990] + "\n\n... (truncated)"

    return formatted


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_help() -> str:
    return (
        "<b>VIP Agent Platform — Commands</b>\n\n"
        "/status — System health overview\n"
        "/agents — List registered agents\n"
        "/report — Latest daily report summary\n"
        "/run_daily — Trigger daily asset summary\n"
        "/run_weekly — Trigger weekly report\n"
        "/approvals — Pending judgement cases\n"
        "/approve {id} — Approve a case\n"
        "/reject {id} — Reject a case\n"
        "/help — Show this message"
    )


def _cmd_status(db: Session) -> str:
    agents = db.query(CoreAgent).filter(CoreAgent.status == "active").count()
    total_runs = db.query(OrchTaskRun).count()
    pending = db.query(OrchTaskRun).filter(OrchTaskRun.status == "pending").count()
    failed = db.query(OrchTaskRun).filter(OrchTaskRun.status == "failed").count()
    review = db.query(AuditJudgementCase).filter(
        AuditJudgementCase.decision.in_(["human_review_required", "conditional_approve"])
    ).count()

    return (
        "<b>VIP System Status</b>\n\n"
        f"Active Agents: <b>{agents}</b>\n"
        f"Total Runs: <b>{total_runs}</b>\n"
        f"Pending: <b>{pending}</b>\n"
        f"Failed: <b>{failed}</b>\n"
        f"Awaiting Review: <b>{review}</b>\n\n"
        "System: Online"
    )


def _cmd_agents(db: Session) -> str:
    agents = db.query(CoreAgent).order_by(CoreAgent.name).all()
    if not agents:
        return "No agents registered."

    lines = ["<b>Registered Agents</b>\n"]
    for a in agents:
        status_icon = "green" if a.status == "active" else "red"
        mock = " [mock]" if a.is_mock else ""
        lines.append(f"{'🟢' if a.status == 'active' else '🔴'} <b>{a.name}</b>{mock}\n   Type: {a.type} | v{a.version} | Priority: {a.priority_score}")

    return "\n".join(lines)


def _cmd_report(db: Session) -> str:
    from db.models import OrchReport
    report = db.query(OrchReport).filter(
        OrchReport.report_type == "daily_summary"
    ).order_by(OrchReport.created_at.desc()).first()

    if not report:
        return "No daily report available yet. Use /run_daily to generate one."

    summary = (report.content_json or {}).get("executive_summary", "No summary")
    created = report.created_at.strftime("%Y-%m-%d %H:%M") if report.created_at else "unknown"

    return f"<b>Latest Daily Report</b>\n<i>{created}</i>\n\n{summary[:600]}"


def _cmd_run_daily(db: Session, trace_id: str) -> str:
    from services.report_service import compose_report
    result = compose_report(db, report_type="daily_summary", hours_back=24, trace_id=trace_id)
    return f"<b>Daily report composed</b>\n\nRuns included: {result['source_run_count']}\n\n{result['executive_summary'][:400]}"


def _cmd_run_weekly(db: Session, trace_id: str) -> str:
    from services.report_service import compose_report
    result = compose_report(db, report_type="weekly_summary", hours_back=168, trace_id=trace_id)
    return f"<b>Weekly report composed</b>\n\nRuns included: {result['source_run_count']}\n\n{result['executive_summary'][:400]}"


def _cmd_approvals(db: Session) -> str:
    cases = db.query(AuditJudgementCase).filter(
        AuditJudgementCase.decision.in_(["human_review_required", "conditional_approve"])
    ).order_by(AuditJudgementCase.created_at.desc()).limit(10).all()

    if not cases:
        return "No pending approvals."

    lines = ["<b>Pending Approvals</b>\n"]
    for c in cases:
        risk_pct = int((c.risk_score or 0) * 100)
        short_id = str(c.id)[:8]
        lines.append(f"{'🔴' if risk_pct >= 70 else '🟡' if risk_pct >= 40 else '🟢'} <code>{short_id}</code> Risk: {risk_pct}% | {c.decision}")
        lines.append(f"   /approve {c.id}")
        lines.append(f"   /reject {c.id}\n")

    return "\n".join(lines)


def _cmd_approve(db: Session, args: list[str], user_id: str, trace_id: str) -> str:
    if not args:
        return "Usage: /approve {case_id}\nUse /approvals to see pending cases."

    from services.judgement_service import approve_case
    try:
        case_id = UUID(args[0])
        result = approve_case(db, case_id, f"telegram:{user_id}", trace_id)
        if not result:
            return f"Case not found: {args[0]}"
        return f"<b>Approved</b>\nCase: <code>{args[0][:8]}...</code>\nTask status: {result['task_status']}"
    except ValueError:
        return f"Invalid case ID: {args[0]}"


def _cmd_reject(db: Session, args: list[str], user_id: str, trace_id: str) -> str:
    if not args:
        return "Usage: /reject {case_id}\nUse /approvals to see pending cases."

    from services.judgement_service import reject_case
    try:
        case_id = UUID(args[0])
        reason = " ".join(args[1:]) if len(args) > 1 else "Rejected via Telegram"
        result = reject_case(db, case_id, f"telegram:{user_id}", trace_id, reason)
        if not result:
            return f"Case not found: {args[0]}"
        return f"<b>Rejected</b>\nCase: <code>{args[0][:8]}...</code>\nTask status: {result['task_status']}"
    except ValueError:
        return f"Invalid case ID: {args[0]}"


# ---------------------------------------------------------------------------
# Webhook setup
# ---------------------------------------------------------------------------

def set_webhook(webhook_url: str) -> bool:
    """Register webhook URL with Telegram."""
    if not BOT_TOKEN:
        log.warning("telegram: no BOT_TOKEN, cannot set webhook")
        return False

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(f"{API_URL}/setWebhook", json={"url": webhook_url})
            ok = resp.json().get("ok", False)
            log.info(f"telegram: webhook {'set' if ok else 'failed'}: {webhook_url}")
            return ok
    except Exception as e:
        log.warning(f"telegram: webhook error: {e}")
        return False


def get_bot_info() -> dict:
    """Get bot information."""
    if not BOT_TOKEN:
        return {"configured": False, "error": "TELEGRAM_BOT_TOKEN not set"}

    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{API_URL}/getMe")
            if resp.status_code == 200:
                data = resp.json().get("result", {})
                return {"configured": True, "bot_name": data.get("first_name"), "username": data.get("username")}
            return {"configured": True, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"configured": bool(BOT_TOKEN), "error": str(e)}
