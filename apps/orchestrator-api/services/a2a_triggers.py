"""
VIP AI Platform — A2A Event-Driven Triggers
Subscribes to A2A events and automatically kicks off follow-up actions.
E.g., a risk_alert from Stock Agent triggers a portfolio review from Asset Agent.
"""

from typing import Any

from sqlalchemy.orm import Session

from db.base import SessionLocal
from services.event_bus import subscribe
from services.audit_service import record_event
from services.logger import log


# ---------------------------------------------------------------------------
# Trigger definitions: event pattern -> action
# ---------------------------------------------------------------------------

TRIGGERS = [
    {
        "name": "risk_alert_portfolio_review",
        "description": "When Stock Agent sends risk_alert, request portfolio review from Asset Agent",
        "event_channel": "a2a.inbound.risk_alert",
        "condition": lambda msg: msg.get("payload", {}).get("alert_level") in ("high", "critical"),
        "action": "request_data",
        "action_config": {
            "requester": "Stock Agent",
            "target_type": "asset",
            "data_request": "portfolio_exposure_review",
        },
    },
    {
        "name": "risk_alert_realty_check",
        "description": "When a high risk_alert arrives, also check real estate exposure",
        "event_channel": "a2a.inbound.risk_alert",
        "condition": lambda msg: msg.get("payload", {}).get("alert_level") == "critical",
        "action": "request_data",
        "action_config": {
            "requester": "Stock Agent",
            "target_type": "realty",
            "data_request": "realty_exposure_check",
        },
    },
    {
        "name": "escalation_judgement",
        "description": "When an escalation_request arrives, flag for judgement review",
        "event_channel": "a2a.inbound.escalation_request",
        "condition": lambda msg: True,
        "action": "flag_judgement",
        "action_config": {},
    },
    {
        "name": "data_response_log",
        "description": "When agent data arrives, log it for dashboard visibility",
        "event_channel": "a2a.inbound.report_response",
        "condition": lambda msg: True,
        "action": "audit_log",
        "action_config": {},
    },
]


# ---------------------------------------------------------------------------
# Action executors
# ---------------------------------------------------------------------------

def _execute_request_data(trigger: dict, message: dict):
    """Execute a cross-agent data request as a trigger action."""
    from services.a2a_service import request_data_from_agent

    config = trigger["action_config"]
    trace_id = message.get("trace_id", "tr-trigger-auto")

    db = SessionLocal()
    try:
        result = request_data_from_agent(
            db=db,
            requester_agent_id=config["requester"],
            target_agent_type=config["target_type"],
            trace_id=f"{trace_id}-trigger",
            data_request=config["data_request"],
            context={
                "triggered_by": trigger["name"],
                "source_message": message.get("message_id"),
            },
        )
        log.info(
            f"trigger [{trigger['name']}]: data request completed, success={result['success']}",
            extra={"trace_id": trace_id, "action": f"trigger.{trigger['name']}.completed"},
        )
    except Exception as e:
        log.warning(
            f"trigger [{trigger['name']}]: failed - {e}",
            extra={"trace_id": trace_id, "action": f"trigger.{trigger['name']}.failed"},
        )
    finally:
        db.close()


def _execute_flag_judgement(trigger: dict, message: dict):
    """Flag a message for judgement review."""
    trace_id = message.get("trace_id", "tr-trigger-auto")
    db = SessionLocal()
    try:
        record_event(db, "a2a_trigger", f"trigger.judgement_flagged", trace_id, {
            "trigger": trigger["name"],
            "source_message": message.get("message_id"),
            "sender": message.get("sender_agent_id"),
            "requires_review": True,
        })
        db.commit()
        log.info(
            f"trigger [{trigger['name']}]: flagged for judgement",
            extra={"trace_id": trace_id, "action": f"trigger.{trigger['name']}.flagged"},
        )
    except Exception as e:
        log.warning(f"trigger [{trigger['name']}]: flag failed - {e}")
    finally:
        db.close()


def _execute_audit_log(trigger: dict, message: dict):
    """Log event for dashboard visibility."""
    trace_id = message.get("trace_id", "tr-trigger-auto")
    db = SessionLocal()
    try:
        record_event(db, "a2a_trigger", f"trigger.data_received", trace_id, {
            "trigger": trigger["name"],
            "source_message": message.get("message_id"),
            "sender": message.get("sender_agent_id"),
            "message_type": message.get("message_type"),
        })
        db.commit()
    except Exception as e:
        log.warning(f"trigger [{trigger['name']}]: audit log failed - {e}")
    finally:
        db.close()


ACTION_EXECUTORS = {
    "request_data": _execute_request_data,
    "flag_judgement": _execute_flag_judgement,
    "audit_log": _execute_audit_log,
}


# ---------------------------------------------------------------------------
# Trigger handler (called by event bus)
# ---------------------------------------------------------------------------

def _handle_trigger_event(trigger: dict, message: dict):
    """Evaluate trigger condition and execute action if matched."""
    try:
        if trigger["condition"](message):
            executor = ACTION_EXECUTORS.get(trigger["action"])
            if executor:
                log.info(
                    f"trigger [{trigger['name']}]: condition matched, executing {trigger['action']}",
                    extra={"action": f"trigger.{trigger['name']}.matched"},
                )
                executor(trigger, message)
            else:
                log.warning(f"trigger [{trigger['name']}]: unknown action {trigger['action']}")
    except Exception as e:
        log.warning(f"trigger [{trigger['name']}]: error - {e}")


# ---------------------------------------------------------------------------
# Initialize triggers (called at app startup)
# ---------------------------------------------------------------------------

def init_triggers():
    """Subscribe all triggers to their event channels."""
    for trigger in TRIGGERS:
        channel = trigger["event_channel"]
        subscribe(channel, lambda msg, t=trigger: _handle_trigger_event(t, msg))
        log.info(
            f"trigger registered: {trigger['name']} on {channel}",
            extra={"action": "trigger.registered"},
        )

    log.info(
        f"a2a_triggers: {len(TRIGGERS)} triggers initialized",
        extra={"action": "triggers.init"},
    )


def list_triggers() -> list[dict]:
    """Return all registered triggers for API/dashboard visibility."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "event_channel": t["event_channel"],
            "action": t["action"],
            "action_config": {k: v for k, v in t["action_config"].items() if k != "condition"},
        }
        for t in TRIGGERS
    ]
