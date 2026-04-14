"""
VIP AI Platform — Audit Service
Writes audit_event_logs for every important action in the system.
"""

from sqlalchemy.orm import Session
from db.models import AuditEventLog
from services.logger import log


def record_event(
    db: Session,
    source: str,
    event_type: str,
    trace_id: str,
    payload: dict | None = None,
):
    """Write an audit event log entry."""
    event = AuditEventLog(
        source=source,
        event_type=event_type,
        trace_id=trace_id,
        payload_json=payload or {},
    )
    db.add(event)
    db.flush()

    log.info(
        f"audit: {event_type}",
        extra={"trace_id": trace_id, "action": event_type},
    )
    return event
