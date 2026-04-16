"""
VIP AI Platform — A2A Notification Service
Sends notifications to Telegram and dashboard when A2A events occur.
Subscribes to event bus channels for real-time alerting.
"""

from datetime import datetime
from typing import Any

from db.base import SessionLocal
from db.models import AuditEventLog
from services.event_bus import subscribe
from services.logger import log


# ---------------------------------------------------------------------------
# Notification formatters
# ---------------------------------------------------------------------------

def _format_risk_alert(message: dict) -> str:
    """Format a risk_alert A2A message for Telegram notification."""
    sender = message.get("sender_agent_id", "Unknown Agent")
    payload = message.get("payload", {})
    alert_level = payload.get("alert_level", "unknown")
    trigger = payload.get("trigger", "unknown")
    trace_id = message.get("trace_id", "")

    level_emoji = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(alert_level, "⚪")

    lines = [
        f"{level_emoji} <b>A2A Risk Alert</b>",
        f"",
        f"<b>From:</b> {sender}",
        f"<b>Level:</b> {alert_level.upper()}",
        f"<b>Trigger:</b> {trigger}",
    ]

    if payload.get("index"):
        lines.append(f"<b>Index:</b> {payload['index']} ({payload.get('change_pct', 'N/A')}%)")
    if payload.get("affected_sectors"):
        lines.append(f"<b>Sectors:</b> {', '.join(payload['affected_sectors'])}")

    lines.append(f"\n<code>trace: {trace_id}</code>")
    return "\n".join(lines)


def _format_data_response(message: dict) -> str:
    """Format a report_response for Telegram notification."""
    sender = message.get("sender_agent_id", "Unknown Agent")
    payload = message.get("payload", {})
    data_type = payload.get("data_type", "data")
    success = payload.get("success", False)
    trace_id = message.get("trace_id", "")

    icon = "✅" if success else "❌"

    lines = [
        f"{icon} <b>A2A Data Response</b>",
        f"",
        f"<b>From:</b> {sender}",
        f"<b>Type:</b> {data_type}",
        f"<b>Status:</b> {'Success' if success else 'Failed'}",
    ]

    summary = payload.get("summary")
    if summary:
        lines.append(f"<b>Summary:</b> {summary[:200]}")

    lines.append(f"\n<code>trace: {trace_id}</code>")
    return "\n".join(lines)


def _format_escalation(message: dict) -> str:
    """Format an escalation_request for Telegram notification."""
    sender = message.get("sender_agent_id", "Unknown Agent")
    payload = message.get("payload", {})
    reason = message.get("proof_of_intent", {}).get("reason", payload.get("reason", "No reason provided"))
    trace_id = message.get("trace_id", "")

    lines = [
        f"🚨 <b>A2A Escalation</b>",
        f"",
        f"<b>From:</b> {sender}",
        f"<b>Reason:</b> {reason}",
        f"\n<code>trace: {trace_id}</code>",
    ]
    return "\n".join(lines)


def _format_workflow_complete(message: dict) -> str:
    """Format a workflow completion notification."""
    workflow = message.get("workflow", "unknown")
    tasks_completed = message.get("tasks_completed", 0)
    tasks_total = message.get("tasks_total", 0)
    a2a_sent = message.get("a2a_sent", 0)
    trace_id = message.get("trace_id", "")

    icon = "✅" if tasks_completed == tasks_total else "⚠️"

    lines = [
        f"{icon} <b>Cross-Agent Workflow Complete</b>",
        f"",
        f"<b>Workflow:</b> {workflow}",
        f"<b>Tasks:</b> {tasks_completed}/{tasks_total} completed",
        f"<b>A2A Messages:</b> {a2a_sent}",
        f"\n<code>trace: {trace_id}</code>",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dashboard notification (persisted as audit events for frontend polling)
# ---------------------------------------------------------------------------

def _store_dashboard_notification(
    notification_type: str,
    title: str,
    body: str,
    trace_id: str,
    severity: str = "info",
    metadata: dict | None = None,
):
    """Store a notification in audit log + platform_notifications for bell."""
    db = SessionLocal()
    try:
        # Audit log (existing)
        event = AuditEventLog(
            source="a2a_notification",
            event_type=f"notification.{notification_type}",
            trace_id=trace_id,
            payload_json={
                "title": title,
                "body": body,
                "severity": severity,
                "notification_type": notification_type,
                "timestamp": datetime.utcnow().isoformat(),
                **(metadata or {}),
            },
        )
        db.add(event)

        # Platform notification (for bell badge)
        from services.user_service import create_notification
        create_notification(
            db, title=title, body=body, severity=severity,
            notification_type=notification_type, trace_id=trace_id,
        )

        db.commit()
    except Exception as e:
        log.warning(f"notification: failed to store dashboard notification: {e}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Notification handlers (called by event bus)
# ---------------------------------------------------------------------------

def _handle_risk_alert(message: dict):
    """Handle inbound risk_alert — notify Telegram admins + store dashboard notification."""
    from services.telegram_service import send_alert

    text = _format_risk_alert(message)
    send_alert(text)

    alert_level = message.get("payload", {}).get("alert_level", "unknown")
    _store_dashboard_notification(
        "risk_alert",
        f"Risk Alert: {alert_level.upper()}",
        text,
        message.get("trace_id", ""),
        severity="warning" if alert_level in ("high", "critical") else "info",
        metadata={"sender": message.get("sender_agent_id"), "alert_level": alert_level},
    )

    log.info("a2a notification: risk_alert sent to Telegram + dashboard", extra={"action": "notification.risk_alert"})


def _handle_data_response(message: dict):
    """Handle inbound report_response — store for dashboard."""
    _store_dashboard_notification(
        "data_response",
        f"Data from {message.get('sender_agent_id', 'agent')}",
        _format_data_response(message),
        message.get("trace_id", ""),
        severity="info",
        metadata={"sender": message.get("sender_agent_id")},
    )


def _handle_escalation(message: dict):
    """Handle escalation — notify Telegram + dashboard."""
    from services.telegram_service import send_alert

    text = _format_escalation(message)
    send_alert(text)

    _store_dashboard_notification(
        "escalation",
        "Escalation Request",
        text,
        message.get("trace_id", ""),
        severity="critical",
        metadata={"sender": message.get("sender_agent_id")},
    )

    log.info("a2a notification: escalation sent to Telegram + dashboard", extra={"action": "notification.escalation"})


def _handle_workflow_complete(message: dict):
    """Handle cross-agent workflow completion — store for dashboard."""
    from services.telegram_service import send_alert

    text = _format_workflow_complete(message)

    # Only Telegram-notify if there were failures
    tasks_completed = message.get("tasks_completed", 0)
    tasks_total = message.get("tasks_total", 0)
    if tasks_completed < tasks_total:
        send_alert(text)

    _store_dashboard_notification(
        "workflow_complete",
        f"Workflow: {message.get('workflow', 'unknown')}",
        text,
        message.get("trace_id", ""),
        severity="info" if tasks_completed == tasks_total else "warning",
    )


# ---------------------------------------------------------------------------
# Initialize (called at app startup)
# ---------------------------------------------------------------------------

def init_a2a_notifications():
    """Subscribe notification handlers to A2A event bus channels."""
    subscribe("a2a.inbound.risk_alert", _handle_risk_alert)
    subscribe("a2a.inbound.escalation_request", _handle_escalation)
    subscribe("a2a.inbound.report_response", _handle_data_response)
    subscribe("a2a.workflow.completed", _handle_workflow_complete)

    log.info(
        "a2a_notifications: 4 notification handlers registered",
        extra={"action": "notifications.init"},
    )


def get_notifications(db, limit: int = 50, severity: str | None = None) -> list[dict]:
    """Get recent A2A notifications from audit log for dashboard display."""
    q = db.query(AuditEventLog).filter(
        AuditEventLog.source == "a2a_notification"
    )
    if severity:
        q = q.filter(AuditEventLog.payload_json["severity"].astext == severity)

    events = q.order_by(AuditEventLog.created_at.desc()).limit(limit).all()

    return [
        {
            "id": str(e.id),
            "type": e.event_type,
            "trace_id": e.trace_id,
            "title": (e.payload_json or {}).get("title", ""),
            "severity": (e.payload_json or {}).get("severity", "info"),
            "timestamp": (e.payload_json or {}).get("timestamp"),
            "metadata": {k: v for k, v in (e.payload_json or {}).items()
                        if k not in ("title", "body", "severity", "notification_type", "timestamp")},
        }
        for e in events
    ]
