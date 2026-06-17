"""
VIP AI Platform — Twin Actions (do things, safely).

Turns a twin from "drafts ideas" into "takes actions" — WITH a human approval
gate. A twin proposes an action; it sits as `pending`; the owner approves; only
then does it execute. Every action is recorded (the ProposedAction row is the
audit trail: who proposed, who approved, what happened).

v1 ships the SAFE, no-credential actions (create a task, send an internal
message, save a note). External actions (email, calendar) are registered as
stubs that report "needs integration" until their OAuth is wired — so the
framework is ready and nothing fails silently.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from db.models import ProposedAction, TwinTask, DirectMessage
from services import twin_service
from services.logger import log


# ---------------------------------------------------------------------------
#  Executors — each returns a human-readable result string (raises on failure)
# ---------------------------------------------------------------------------

def _exec_create_task(db: Session, twin_id, payload: dict) -> str:
    title = (payload.get("title") or "").strip() or "Task"
    task = TwinTask(
        twin_id=twin_id,
        title=title[:255],
        description=(payload.get("description") or "")[:5000],
        priority=payload.get("priority", "medium"),
        status="todo",
        assigned_by="twin_action",
    )
    db.add(task)
    db.flush()
    return f"Created task '{title}' (id {task.id})"


def _exec_send_message(db: Session, twin_id, payload: dict) -> str:
    content = (payload.get("content") or "").strip()
    if not content:
        raise ValueError("send_message needs content")
    dm = DirectMessage(twin_id=twin_id, sender_type="twin", content=content[:5000])
    db.add(dm)
    db.flush()
    return f"Sent message: {content[:80]}"


def _exec_add_note(db: Session, twin_id, payload: dict) -> str:
    title = (payload.get("title") or "Note").strip()[:200]
    content = (payload.get("content") or "").strip()
    if not content:
        raise ValueError("add_note needs content")
    twin_service.add_knowledge(db, twin_id, title=title, content=content, source_type="document")
    return f"Saved note '{title}'"


def _exec_send_email(db: Session, twin_id, payload: dict) -> str:
    """Send a real email via the company SMTP. Approval-gated: a human approved
    this exact recipient/subject/body before it sends."""
    from services import report_email
    to = payload.get("to") or payload.get("to_email")
    subject = (payload.get("subject") or "").strip()
    body = (payload.get("body") or payload.get("content") or "").strip()
    if not to:
        raise ValueError("send_email needs a 'to' recipient")
    if not body:
        raise ValueError("send_email needs a 'body'")
    res = report_email.send_plain_email(to, subject or "(no subject)", body)
    if not res.get("ok"):
        raise RuntimeError(f"email send failed: {res.get('reason')}")
    return f"Email sent to {', '.join(res.get('to', [to] if isinstance(to, str) else to))}"


def _exec_calendar_event(db: Session, twin_id, payload: dict) -> str:
    """Create a Google Calendar event on the twin owner's calendar (needs the
    twin to have connected Google). Approval-gated."""
    from services import cloud_sources
    summary = (payload.get("summary") or payload.get("title") or "").strip()
    start = (payload.get("start") or payload.get("start_iso") or "").strip()
    end = (payload.get("end") or payload.get("end_iso") or start).strip()
    if not summary or not start:
        raise ValueError("calendar_event needs 'summary' and 'start' (ISO datetime)")
    res = cloud_sources.create_calendar_event(
        db, twin_id, summary, start, end,
        description=payload.get("description", ""),
        attendees=payload.get("attendees"))
    if not res.get("ok"):
        raise RuntimeError(f"calendar event failed: {res.get('error')}")
    return f"Calendar event '{summary}' created" + (f" ({res['link']})" if res.get("link") else "")


def _exec_needs_integration(db: Session, twin_id, payload: dict) -> str:
    raise RuntimeError("This action type needs an integration (OAuth) that isn't configured yet")


# action_type -> (executor, needs_external_setup)
ACTION_REGISTRY = {
    "create_task":  (_exec_create_task, False),
    "send_message": (_exec_send_message, False),
    "add_note":     (_exec_add_note, False),
    "send_email":   (_exec_send_email, False),       # real SMTP send (approval-gated)
    "calendar_event": (_exec_calendar_event, False), # real Google Calendar (approval-gated)
    # Registered but not yet wired — ready for when their credentials are added.
    "post_slack":   (_exec_needs_integration, True),
}


def is_known(action_type: str) -> bool:
    return action_type in ACTION_REGISTRY


# ---------------------------------------------------------------------------
#  Lifecycle: propose -> approve/reject -> execute (audited)
# ---------------------------------------------------------------------------

def propose_action(db: Session, twin_id, action_type: str, summary: str,
                   payload: Optional[dict] = None, proposed_by: str = "twin") -> ProposedAction:
    a = ProposedAction(
        twin_id=twin_id, action_type=action_type,
        summary=(summary or action_type)[:300], payload=payload or {},
        status="pending", proposed_by=proposed_by,
    )
    db.add(a)
    db.flush()
    try:
        twin_service.log_activity(db, twin_id, "action_proposed",
                                  f"Proposed: {a.summary}", {"action_type": action_type})
    except Exception:
        pass
    db.commit()
    return a


def list_actions(db: Session, twin_id, status: Optional[str] = None, limit: int = 50):
    q = db.query(ProposedAction).filter(ProposedAction.twin_id == twin_id)
    if status:
        q = q.filter(ProposedAction.status == status)
    return q.order_by(ProposedAction.created_at.desc()).limit(limit).all()


def reject_action(db: Session, twin_id, action_id, reviewer_email: str, comment: str = "") -> Optional[ProposedAction]:
    # Scope to twin_id (IDOR guard): an owner may only review THEIR twin's actions.
    a = db.query(ProposedAction).filter(
        ProposedAction.id == action_id, ProposedAction.twin_id == twin_id).first()
    if not a or a.status != "pending":
        return a
    a.status = "rejected"
    a.reviewed_by = reviewer_email
    a.review_comment = comment
    db.commit()
    return a


def approve_action(db: Session, twin_id, action_id, reviewer_email: str) -> Optional[ProposedAction]:
    """Approve a pending action and execute it immediately. The row records the
    full audit (approver + result + timestamp). Scoped to twin_id (IDOR guard)."""
    a = db.query(ProposedAction).filter(
        ProposedAction.id == action_id, ProposedAction.twin_id == twin_id).first()
    if not a or a.status != "pending":
        return a
    a.reviewed_by = reviewer_email
    a.status = "approved"
    entry = ACTION_REGISTRY.get(a.action_type)
    if not entry:
        a.status = "failed"
        a.result = f"Unknown action type: {a.action_type}"
        db.commit()
        return a
    executor, _ = entry
    try:
        a.result = executor(db, a.twin_id, a.payload or {})
        a.status = "executed"
        a.executed_at = datetime.utcnow()
        twin_service.log_activity(db, a.twin_id, "action_executed",
                                  f"Executed: {a.summary}", {"action_type": a.action_type})
    except Exception as e:
        a.status = "failed"
        a.result = f"{e.__class__.__name__}: {e}"
        log.warning(f"approve_action: execution failed: {e}")
    db.commit()
    return a
