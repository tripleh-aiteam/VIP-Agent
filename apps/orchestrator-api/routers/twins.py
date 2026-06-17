"""
VIP AI Platform — Digital Twin Router
CRUD, mode switching, tasks, knowledge, activity, and chat endpoints.
"""

import os
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4
from pathlib import Path

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File
from fastapi.responses import StreamingResponse
import io
from sqlalchemy.orm import Session

from db.base import get_db
from db.models import PlatformUser, TwinTask
from services import twin_service
from services import twin_brain
from services import auth_service, embed_auth
from services.logger import log
from contracts.twin import (
    TwinCreate, TwinUpdate, TwinModeSwitch,
    TwinTaskCreate, TwinTaskUpdate, TwinTaskReview,
    TwinKnowledgeCreate, TwinChatMessage,
    MeetingJoinRequest, MeetingJoinResponse, MeetingLeaveRequest,
    MeetingEscalateRequest, MeetingUtteranceCreate, MeetingActiveSession,
    MeetingListenStartAsterisk, MeetingListenStatus, MeetingListenStopResponse,
    TwinRespondRequest, TwinRespondResponse,
    VoiceConsentRequest, VoiceProfileResponse,
    AutoCreateMeetingRequest, AutoCreateMeetingResponse,
)

router = APIRouter(prefix="/twins", tags=["digital-twins"])


# ---------------------------------------------------------------------------
# LLM Models + File Upload (for Chat menu)
# ---------------------------------------------------------------------------

@router.get("/llm/models")
def list_llm_models():
    """List available LLM models for the chat picker (Claude, OpenAI, Gemini, Ollama)."""
    from services.llm_client import list_available_models
    return {"models": list_available_models()}


@router.get("/llm/ping")
def ping_llm_model(
    model: Optional[str] = None,
    x_user_email: Optional[str] = Header(default=None),
    x_user_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """ADMIN-ONLY diagnostic. Health-check a model (or all available catalog
    models) by calling it directly with NO fallback, so a broken model id is
    visible instead of masked. Each ping triggers a real (tiny) paid API call,
    so it requires an admin/operator login session.
    Usage: /twins/llm/ping?model=gpt-5.5  — or no model to ping available models.
    """
    user = auth_service.verify_session_token(db, x_user_email or "", x_user_token or "")
    if not user or user.role not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Admin session required")

    from services.llm_client import ping_model, list_available_models
    if model:
        return ping_model(model)
    # ping-all: only models whose API key is configured, capped — avoids pointless
    # calls and bounds the work per request.
    available = [m["id"] for m in list_available_models() if m.get("available")][:25]
    results = [ping_model(m) for m in available]
    return {
        "total": len(results),
        "ok": sum(1 for r in results if r["ok"]),
        "failed": [r for r in results if not r["ok"]],
        "results": results,
    }


@router.post("/{twin_id}/upload")
async def upload_file(twin_id: UUID, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a file (PDF/Excel/DOCX/text) and extract its text for chat context."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    from services.file_extract import extract_text
    data = await file.read()
    if len(data) > 10_000_000:  # 10 MB hard cap
        raise HTTPException(status_code=413, detail="File too large (max 10MB)")

    result = extract_text(file.filename or "upload", data)

    return {
        "filename": file.filename,
        "size_bytes": len(data),
        "kind": result["kind"],
        "ok": result["ok"],
        "note": result["note"],
        "text": result["text"],
    }


def _auth_enforced() -> bool:
    """AUTH_ENFORCE gates the transition from grace mode to hard enforcement."""
    return os.getenv("AUTH_ENFORCE", "off").strip().lower() in ("on", "1", "true", "yes")


def _check_twin_access(
    twin_id: UUID,
    x_user_email: Optional[str] = Header(None),
    x_user_token: Optional[str] = Header(None),
    x_embed_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Authorize access to a specific twin.

    Accepted principals:
      - A signed boss-embed token (X-Embed-Token) scoped to THIS twin.
      - A verified admin/operator/viewer login session → any twin.
      - A verified worker login session → only their own twin.

    Unauthenticated / unverified callers are denied once AUTH_ENFORCE is on.
    During rollout (AUTH_ENFORCE off, the default) they are allowed but logged,
    so we can confirm every legitimate caller authenticates before enforcing.
    """
    # 1. Boss-embed signed token, scoped to this specific twin.
    payload = embed_auth.verify_embed_token(x_embed_token)
    if payload is not None:
        if str(payload.get("tid")) == str(twin_id):
            return
        raise HTTPException(status_code=403, detail="Embed token does not authorize this twin")

    # 2. Verified login session (worker or admin).
    if x_user_email and x_user_token:
        user = auth_service.verify_session_token(db, x_user_email, x_user_token)
        if user:
            if user.role in ("admin", "operator", "viewer"):
                return  # Boss roles can access any twin
            if user.role == "worker":
                if user.twin_id and str(user.twin_id) == str(twin_id):
                    return
                raise HTTPException(status_code=403, detail="You can only access your own twin")
            raise HTTPException(status_code=403, detail="Not authorized for this twin")

    # 3. No valid principal.
    if _auth_enforced():
        raise HTTPException(status_code=401, detail="Authentication required")
    log.warning(
        f"[auth-grace] unauthenticated twin access allowed (would 401 under AUTH_ENFORCE): "
        f"twin={twin_id} email={x_user_email or '-'} embed={'y' if x_embed_token else 'n'}"
    )
    return


def _check_twin_owner_access(
    twin_id: UUID,
    x_user_email: Optional[str] = Header(None),
    x_user_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """PRIVACY WALL — owner-only. A twin's knowledge, chats and messages are
    PRIVATE to the worker who owns that twin (the person it learns from).

    NOT accessible by the boss, by admin role, or via an embed token — by design,
    so workers teach freely knowing the boss can't read it. The boss still sees
    status/readiness/activity via the (admin-allowed) monitoring endpoints.
    Always enforced, independent of AUTH_ENFORCE (privacy is never in 'grace').
    The boss can own + access their own Boss Twin if their account is linked to it.
    """
    if x_user_email and x_user_token:
        user = auth_service.verify_session_token(db, x_user_email, x_user_token)
        if user and user.twin_id and str(user.twin_id) == str(twin_id):
            return  # the owner
    raise HTTPException(status_code=403, detail="Private to the twin's owner")


def _require_admin(
    x_user_email: Optional[str] = Header(None),
    x_user_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Boss-only (admin/operator). Used for cross-twin orchestration (discussions).
    Always enforced (no grace fail-open) — this can reach twins' private brains."""
    if x_user_email and x_user_token:
        user = auth_service.verify_session_token(db, x_user_email, x_user_token)
        if user and user.role in ("admin", "operator"):
            return
    raise HTTPException(status_code=403, detail="Admin only")


# ---------------------------------------------------------------------------
#  Twin CRUD
# ---------------------------------------------------------------------------

@router.get("")
def list_twins(db: Session = Depends(get_db)):
    """List all digital twins."""
    twins = twin_service.list_twins(db)
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "role": t.role,
            "department": t.department,
            "avatar_url": t.avatar_url,
            "personality_prompt": t.personality_prompt,
            "skills": t.skills or [],
            "mode": t.mode,
            "permission_level": t.permission_level,
            "status": t.status,
            "current_task_id": str(t.current_task_id) if t.current_task_id else None,
            "linked_agent_id": str(t.linked_agent_id) if t.linked_agent_id else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in twins
    ]


# ---------------------------------------------------------------------------
#  Intelligence Metrics (must be before /{twin_id} to avoid route conflict)
# ---------------------------------------------------------------------------

@router.get("/intelligence/all")
def get_all_intelligence(db: Session = Depends(get_db)):
    """Get intelligence metrics for all twins (boss dashboard)."""
    from services.twin_intelligence import get_all_twins_intelligence
    return get_all_twins_intelligence(db)


@router.get("/summary/all")
def get_all_summaries(db: Session = Depends(get_db)):
    """Get summary of all twins for Control Room view."""
    return twin_service.get_all_twin_summaries(db)


@router.get("/handoff/today")
def get_today_handoffs(db: Session = Depends(get_db)):
    """Get all handoffs for today (morning report for boss)."""
    from db.models import TwinHandoff, DigitalTwin
    from datetime import datetime as dt, timedelta
    cutoff = dt.utcnow() - timedelta(hours=24)
    handoffs = db.query(TwinHandoff).filter(TwinHandoff.created_at >= cutoff).order_by(TwinHandoff.created_at.desc()).all()
    result = []
    for h in handoffs:
        twin = db.query(DigitalTwin).filter(DigitalTwin.id == h.twin_id).first()
        result.append({
            "id": str(h.id), "twin_id": str(h.twin_id), "twin_name": twin.name if twin else "Unknown",
            "twin_role": twin.role if twin else "", "date": h.date.isoformat() if h.date else None,
            "tasks_completed": h.tasks_completed or [], "tasks_pending_review": h.tasks_pending_review or [],
            "meeting_notes": h.meeting_notes or [], "overnight_summary": h.overnight_summary,
            "reviewed": h.reviewed, "reviewed_at": h.reviewed_at.isoformat() if h.reviewed_at else None,
            "created_at": h.created_at.isoformat() if h.created_at else None,
        })
    total_completed = sum(len(h.get("tasks_completed", [])) for h in result)
    total_review = sum(len(h.get("tasks_pending_review", [])) for h in result)
    unreviewed = sum(1 for h in result if not h["reviewed"])
    return {"handoffs": result, "stats": {"twins_worked": len(result), "tasks_completed": total_completed, "items_need_review": total_review, "unreviewed_handoffs": unreviewed}}


# ---------------------------------------------------------------------------
#  Twin CRUD (dynamic /{twin_id} routes below)
# ---------------------------------------------------------------------------

@router.get("/{twin_id}")
def get_twin(twin_id: UUID, db: Session = Depends(get_db), _=Depends(_check_twin_access)):
    """Get a specific digital twin's full profile."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    return {
        "id": str(twin.id),
        "name": twin.name,
        "role": twin.role,
        "department": twin.department,
        "avatar_url": twin.avatar_url,
        "personality_prompt": twin.personality_prompt,
        "skills": twin.skills or [],
        "mode": twin.mode,
        "permission_level": twin.permission_level,
        "status": twin.status,
        "current_task_id": str(twin.current_task_id) if twin.current_task_id else None,
        "linked_agent_id": str(twin.linked_agent_id) if twin.linked_agent_id else None,
        "created_at": twin.created_at.isoformat() if twin.created_at else None,
        "updated_at": twin.updated_at.isoformat() if twin.updated_at else None,
    }


@router.post("", status_code=201)
def create_twin(body: TwinCreate, db: Session = Depends(get_db)):
    """Create a new digital twin."""
    twin = twin_service.create_twin(
        db=db,
        name=body.name,
        role=body.role,
        department=body.department,
        avatar_url=body.avatar_url,
        personality_prompt=body.personality_prompt,
        skills=body.skills,
        permission_level=body.permission_level.value,
        linked_agent_id=body.linked_agent_id,
    )
    db.commit()
    return {
        "created": True,
        "id": str(twin.id),
        "name": twin.name,
        "role": twin.role,
        "mode": twin.mode,
        "status": twin.status,
    }


@router.patch("/{twin_id}")
def update_twin(twin_id: UUID, body: TwinUpdate, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Update a digital twin's profile."""
    update_data = body.model_dump(exclude_none=True)
    if "permission_level" in update_data:
        update_data["permission_level"] = update_data["permission_level"].value
    twin = twin_service.update_twin(db, twin_id, **update_data)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    db.commit()
    return {"updated": True, "id": str(twin.id), "name": twin.name}


@router.delete("/{twin_id}")
def delete_twin(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Delete a digital twin."""
    success = twin_service.delete_twin(db, twin_id)
    if not success:
        raise HTTPException(status_code=404, detail="Twin not found")
    db.commit()
    return {"deleted": True, "id": str(twin_id)}


# ---------------------------------------------------------------------------
#  Mode Switching
# ---------------------------------------------------------------------------

@router.post("/{twin_id}/mode")
def switch_mode(twin_id: UUID, body: TwinModeSwitch, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Switch twin mode: shadow / active / handoff."""
    twin = twin_service.switch_mode(db, twin_id, body.mode.value)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    db.commit()
    return {"id": str(twin.id), "name": twin.name, "mode": twin.mode}


# ---------------------------------------------------------------------------
#  Tasks
# ---------------------------------------------------------------------------

@router.get("/{twin_id}/tasks")
def get_twin_tasks(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get all tasks assigned to a twin."""
    tasks = twin_service.get_tasks(db, twin_id)
    return [
        {
            "id": str(t.id),
            "twin_id": str(t.twin_id),
            "title": t.title,
            "description": t.description,
            "status": t.status,
            "priority": t.priority,
            "deadline": t.deadline.isoformat() if t.deadline else None,
            "assigned_by": t.assigned_by,
            "needs_review": t.needs_review,
            "review_status": t.review_status,
            "review_comment": t.review_comment,
            "result_text": t.result_text,
            "result_json": t.result_json,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        }
        for t in tasks
    ]


@router.post("/{twin_id}/tasks", status_code=201)
def create_twin_task(twin_id: UUID, body: TwinTaskCreate, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Assign a new task to a twin."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    task = twin_service.create_task(
        db=db,
        twin_id=twin_id,
        title=body.title,
        description=body.description,
        priority=body.priority.value,
        deadline=body.deadline,
    )
    db.commit()
    return {"created": True, "id": str(task.id), "title": task.title, "twin": twin.name}


class TaskExecuteBody(BaseModel):
    model: Optional[str] = None  # e.g. "claude-sonnet-4-6", "claude-opus-4-7"


@router.get("/{twin_id}/tasks/{task_id}/download.docx")
def download_task_as_docx(twin_id: UUID, task_id: UUID, db: Session = Depends(get_db)):
    """Download a task's result as a Microsoft Word .docx file."""
    task = db.query(TwinTask).filter(TwinTask.id == task_id, TwinTask.twin_id == twin_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.result_text:
        raise HTTPException(status_code=400, detail="Task has no result yet — execute it first")

    from services.docx_export import markdown_to_docx
    twin = twin_service.get_twin(db, twin_id)
    docx_bytes = markdown_to_docx(
        title=task.title,
        markdown_text=task.result_text,
        author=f"{twin.name} ({twin.role})" if twin else "Twin",
        subtitle=task.description or "",
    )

    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in task.title)[:80]
    filename = f"{safe_name}.docx"
    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{twin_id}/tasks/{task_id}/execute")
def execute_task(twin_id: UUID, task_id: UUID, body: Optional[TaskExecuteBody] = None, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Make a twin work on a task (long-form, up to ~6000 words). Pass {model} to pick LLM."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    model = body.model if body else None
    result = twin_brain.execute_task(db, twin_id, task_id, model=model)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    db.commit()
    return result


@router.post("/{twin_id}/execute-all")
def execute_all_tasks(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Execute all pending tasks for a twin."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    results = twin_brain.execute_pending_tasks(db, twin_id)
    db.commit()
    return {"twin": twin.name, "tasks_executed": len(results), "results": results}


@router.post("/{twin_id}/tasks/{task_id}/approve")
def approve_task(twin_id: UUID, task_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Worker approves a task that was awaiting review. Marks task done + logs approval feedback."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    task = twin_service.review_task(db, task_id, review_status="approved", reviewed_by="worker")
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    twin_service.log_activity(
        db, twin_id, "feedback",
        f"Worker approved: {task.title}",
        {"task_id": str(task_id), "outcome": "approved"},
    )
    db.commit()
    return {"approved": True, "task_id": str(task_id), "title": task.title, "status": task.status}


@router.post("/{twin_id}/tasks/{task_id}/reject")
def reject_task(twin_id: UUID, task_id: UUID, body: TwinTaskReview, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Worker rejects a task with correction. Marks task back to todo + saves correction as knowledge."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    task = twin_service.review_task(db, task_id, review_status="rejected", reviewed_by="worker", comment=body.review_comment)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    twin_service.log_activity(
        db, twin_id, "feedback",
        f"Worker rejected: {task.title} — correction saved",
        {"task_id": str(task_id), "outcome": "rejected", "comment": (body.review_comment or "")[:200]},
    )
    db.commit()
    return {"rejected": True, "task_id": str(task_id), "title": task.title, "status": task.status}


# ---------------------------------------------------------------------------
#  Knowledge
# ---------------------------------------------------------------------------

@router.get("/{twin_id}/knowledge")
def get_twin_knowledge(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Get all knowledge documents for a twin."""
    knowledge = twin_service.get_knowledge(db, twin_id)
    return [
        {
            "id": str(k.id),
            "twin_id": str(k.twin_id),
            "title": k.title,
            "content": k.content,
            "source_type": k.source_type,
            "created_at": k.created_at.isoformat() if k.created_at else None,
        }
        for k in knowledge
    ]


@router.post("/{twin_id}/knowledge/reindex")
def reindex_twin_knowledge(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Embed all of this twin's knowledge that lacks a vector (semantic memory).
    Idempotent — safe to re-run; only embeds what's missing."""
    if not twin_service.get_twin(db, twin_id):
        raise HTTPException(status_code=404, detail="Twin not found")
    return twin_service.reindex_twin(db, twin_id)


@router.post("/admin/reindex-all")
def reindex_all_knowledge(
    x_user_email: Optional[str] = Header(default=None),
    x_user_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Admin: embed missing vectors for EVERY twin (one-time backfill).

    Always requires an admin/operator session — never fails open (this triggers
    paid embedding calls). For per-twin backfill use POST /{id}/knowledge/reindex.
    """
    user = auth_service.verify_session_token(db, x_user_email or "", x_user_token or "")
    if not user or user.role not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Admin session required")
    results = []
    for tw in twin_service.list_twins(db):
        try:
            r = twin_service.reindex_twin(db, tw.id)
            results.append({"twin": tw.name, **r})
        except Exception as e:
            results.append({"twin": tw.name, "ok": False, "error": str(e)[:200]})
    total = sum(r.get("embedded", 0) for r in results)
    return {"total_embedded": total, "twins": results}


class ObserveBody(BaseModel):
    content: str                       # raw session text / code diff / notes
    source: Optional[str] = "session"  # e.g. "claude-code", "chatgpt", "git", "notes"
    kind: Optional[str] = "ai_session"


@router.post("/{twin_id}/observe")
def observe_twin(twin_id: UUID, body: ObserveBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Watch & Learn: push a raw work session/observation; the twin distills it
    into reusable knowledge (auto-embedded). Owner-only — only the worker feeds
    their own twin; the boss never sees this content (privacy wall).

    Gated on the worker's Watch-&-Learn consent: if the worker has not opted in
    from Settings, capture is refused (403). Consent is privacy-default OFF."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    if not getattr(twin, "learning_consent", False):
        raise HTTPException(
            status_code=403,
            detail="Watch-&-Learn is off. Turn on learning consent in your Twin → Settings to let your twin learn from your AI work.")
    from services import watch_learn
    return watch_learn.observe_and_learn(
        db, twin_id, body.content, source=body.source or "session", kind=body.kind or "ai_session")


class ConsentBody(BaseModel):
    learning_consent: bool


@router.get("/{twin_id}/consent")
def get_learning_consent(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Read the worker's Watch-&-Learn consent state. Owner-only."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    return {
        "learning_consent": bool(getattr(twin, "learning_consent", False)),
        "learning_consent_at": twin.learning_consent_at.isoformat() if getattr(twin, "learning_consent_at", None) else None,
    }


@router.post("/{twin_id}/consent")
def set_learning_consent(twin_id: UUID, body: ConsentBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Worker opts in/out of Watch-&-Learn capture. Owner-only. Privacy-default
    OFF — nothing is captured until the worker turns this on here."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    twin.learning_consent = bool(body.learning_consent)
    twin.learning_consent_at = datetime.utcnow() if body.learning_consent else None
    db.commit()
    try:
        twin_service.log_activity(
            db, twin_id, "consent",
            f"Worker {'enabled' if body.learning_consent else 'disabled'} Watch-&-Learn", {})
        db.commit()
    except Exception:
        pass
    return {
        "ok": True,
        "learning_consent": bool(twin.learning_consent),
        "learning_consent_at": twin.learning_consent_at.isoformat() if twin.learning_consent_at else None,
    }


@router.get("/{twin_id}/sources")
def get_learning_sources(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Phase 1 — per-source breakdown of what the twin has learned. Owner-only.
    Counts are derived from the "[source]" tag the capture client puts on each
    learned item, so the portal can show ChatGPT / Claude Code / Notion / etc."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    from services.watch_learn import SUPPORTED_SOURCES
    from db.models import TwinKnowledge
    from sqlalchemy import or_
    total = db.query(TwinKnowledge).filter(TwinKnowledge.twin_id == twin_id).count()
    sources = []
    counted = 0
    for key, label in SUPPORTED_SOURCES.items():
        cond = TwinKnowledge.title.like(f"[{key}]%")
        # Legacy items (imported before source-tagging) were Claude Code sessions
        # tagged "[claude auto]" / "Consolidated guide:" — count them as claude-code
        # so existing knowledge isn't shown as 0.
        if key == "claude-code":
            cond = or_(cond,
                       TwinKnowledge.title.like("%[claude auto]%"),
                       TwinKnowledge.title.like("Consolidated guide:%"))
        n = db.query(TwinKnowledge).filter(
            TwinKnowledge.twin_id == twin_id, cond,
        ).count()
        counted += n
        sources.append({"source": key, "label": label, "items": n})
    return {
        "total": total,
        "untagged": max(0, total - counted),
        "consent": bool(getattr(twin, "learning_consent", False)),
        "sources": sources,
    }


# ---------------------------------------------------------------------------
#  Phase 2 — cloud auto-pull (Google Drive/Calendar/Gmail, Notion)
# ---------------------------------------------------------------------------
@router.get("/{twin_id}/cloud/status")
def cloud_status(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Which cloud providers are configured + which this twin has connected. Owner-only."""
    from services import cloud_sources
    from db.models import OAuthConnection
    conns = db.query(OAuthConnection).filter(OAuthConnection.twin_id == twin_id).all()
    connected = {c.provider: {"email": c.connected_email, "status": c.status,
                              "last_pull_at": c.last_pull_at.isoformat() if c.last_pull_at else None}
                 for c in conns}
    return {"providers": cloud_sources.provider_status(), "connected": connected}


@router.get("/{twin_id}/oauth/google/auth-url")
def google_auth_url_ep(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """OAuth URL for the worker to authorize Google (Drive+Calendar+Gmail). Owner-only."""
    from services import cloud_sources
    if not cloud_sources.google_configured():
        raise HTTPException(400, "Google not configured by admin (GOOGLE_CLIENT_ID/SECRET).")
    if not twin_service.get_twin(db, twin_id):
        raise HTTPException(404, "Twin not found")
    return {"auth_url": cloud_sources.google_auth_url(str(twin_id))}


@router.get("/oauth/google/callback")
def google_oauth_callback(code: str = "", state: str = "", db: Session = Depends(get_db)):
    """Google redirects here after the worker authorizes. Public (browser redirect):
    exchanges the code, stores tokens for the twin (state=twin_id), then bounces
    back to the portal."""
    from fastapi.responses import RedirectResponse
    from services import cloud_sources
    portal = os.getenv("WORKER_APP_URL", "https://vip-twin-portal.vercel.app").rstrip("/")
    ok = False
    # state is a signed, expiring token minted only by the owner-gated auth-url
    # endpoint — never trust a raw twin_id from this public callback.
    verified_twin_id = cloud_sources.verify_state(state) if state else None
    if code and verified_twin_id:
        try:
            ok = cloud_sources.google_exchange_and_store(db, code, verified_twin_id)
        except Exception:
            ok = False
    return RedirectResponse(f"{portal}/dashboard?google={'connected' if ok else 'failed'}")


@router.post("/{twin_id}/cloud/pull")
def cloud_pull_now(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Manually trigger a cloud pull for this twin (owner-only). Consent-gated."""
    from services import cloud_sources
    return cloud_sources.pull_twin(db, twin_id)


# ---------------------------------------------------------------------------
#  Phase 3 — twin-to-twin communication
# ---------------------------------------------------------------------------
class PeerMessageBody(BaseModel):
    to_twin_id: UUID
    content: str


class AskTwinBody(BaseModel):
    to_twin_id: UUID
    question: str


class DiscussBody(BaseModel):
    topic: str
    twin_ids: list[UUID]
    rounds: Optional[int] = 1


@router.get("/{twin_id}/peer-messages")
def get_peer_messages(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """This twin's conversations with other twins. Owner-only."""
    from services import twin_comms
    return {"messages": twin_comms.inbox(db, twin_id)}


@router.post("/{twin_id}/peer-messages")
def post_peer_message(twin_id: UUID, body: PeerMessageBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """This twin sends a message to another twin. Owner-only (you act as your twin)."""
    from services import twin_comms
    if not twin_service.get_twin(db, body.to_twin_id):
        raise HTTPException(404, "Recipient twin not found")
    m = twin_comms.send_message(db, twin_id, body.to_twin_id, body.content)
    return {"ok": True, "id": str(m.id)}


@router.post("/{twin_id}/ask-twin")
def ask_another_twin(twin_id: UUID, body: AskTwinBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """This twin asks another twin a question; that twin's brain answers from its
    own knowledge. Owner-only (you trigger your twin to ask)."""
    from services import twin_comms
    if not twin_service.get_twin(db, body.to_twin_id):
        raise HTTPException(404, "Target twin not found")
    return twin_comms.ask_twin(db, twin_id, body.to_twin_id, body.question)


@router.post("/discuss")
def twins_discuss(body: DiscussBody, db: Session = Depends(get_db), _ac=Depends(_require_admin)):
    """Boss-only: have several twins discuss a topic, each in their own voice,
    seeing what the others said. Only twins whose owners enabled 'Help other
    twins' participate (privacy wall). Returns the transcript."""
    from services import twin_comms
    return twin_comms.discuss(db, body.topic, body.twin_ids, rounds=body.rounds or 1)


class PeerHelpBody(BaseModel):
    peer_help_enabled: bool


@router.get("/{twin_id}/peer-help")
def get_peer_help(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Whether this twin will answer other twins' questions. Owner-only."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(404, "Twin not found")
    return {"peer_help_enabled": bool(getattr(twin, "peer_help_enabled", False))}


@router.post("/{twin_id}/peer-help")
def set_peer_help(twin_id: UUID, body: PeerHelpBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Owner opts this twin in/out of answering other twins (privacy-default OFF)."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(404, "Twin not found")
    twin.peer_help_enabled = bool(body.peer_help_enabled)
    db.commit()
    return {"ok": True, "peer_help_enabled": bool(twin.peer_help_enabled)}


class ProposeActionBody(BaseModel):
    action_type: str
    summary: Optional[str] = ""
    payload: Optional[dict] = None
    proposed_by: Optional[str] = "twin"


class ReviewActionBody(BaseModel):
    comment: Optional[str] = ""


def _action_dict(a) -> dict:
    return {
        "id": str(a.id), "action_type": a.action_type, "summary": a.summary,
        "status": a.status, "proposed_by": a.proposed_by, "reviewed_by": a.reviewed_by,
        "result": a.result, "payload": a.payload,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "executed_at": a.executed_at.isoformat() if a.executed_at else None,
    }


@router.post("/{twin_id}/actions/propose")
def propose_twin_action(twin_id: UUID, body: ProposeActionBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Propose an action the twin wants to take. It stays PENDING until approved."""
    from services import twin_actions
    if not twin_actions.is_known(body.action_type):
        raise HTTPException(status_code=400, detail=f"Unknown action_type. Known: {list(twin_actions.ACTION_REGISTRY)}")
    a = twin_actions.propose_action(db, twin_id, body.action_type, body.summary or body.action_type,
                                    body.payload, body.proposed_by or "twin")
    return _action_dict(a)


@router.get("/{twin_id}/actions")
def list_twin_actions(twin_id: UUID, status: Optional[str] = None, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """List a twin's proposed/executed actions (the approval queue + audit trail)."""
    from services import twin_actions
    return [_action_dict(a) for a in twin_actions.list_actions(db, twin_id, status)]


@router.post("/{twin_id}/actions/{action_id}/approve")
def approve_twin_action(twin_id: UUID, action_id: UUID, x_user_email: Optional[str] = Header(None),
                        db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Approve a pending action → executes it immediately (audited)."""
    from services import twin_actions
    a = twin_actions.approve_action(db, twin_id, action_id, x_user_email or "owner")
    if not a:
        raise HTTPException(status_code=404, detail="Action not found")
    return _action_dict(a)


@router.post("/{twin_id}/actions/{action_id}/reject")
def reject_twin_action(twin_id: UUID, action_id: UUID, body: ReviewActionBody,
                       x_user_email: Optional[str] = Header(None),
                       db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Reject a pending action (it never executes)."""
    from services import twin_actions
    a = twin_actions.reject_action(db, twin_id, action_id, x_user_email or "owner", body.comment or "")
    if not a:
        raise HTTPException(status_code=404, detail="Action not found")
    return _action_dict(a)


@router.post("/{twin_id}/eval")
def run_twin_eval(twin_id: UUID, n: int = 5, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Owner-only fidelity eval — measures how well the twin recalls & uses what
    it has learned (0-100), with per-question detail. Trust check before acting."""
    if not twin_service.get_twin(db, twin_id):
        raise HTTPException(status_code=404, detail="Twin not found")
    from services import twin_eval
    return twin_eval.run_eval(db, twin_id, n=min(max(n, 1), 10))


@router.post("/{twin_id}/knowledge", status_code=201)
def add_twin_knowledge(twin_id: UUID, body: TwinKnowledgeCreate, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Add a knowledge document to a twin."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    knowledge = twin_service.add_knowledge(
        db=db,
        twin_id=twin_id,
        title=body.title,
        content=body.content,
        source_type=body.source_type.value,
    )
    db.commit()
    return {"created": True, "id": str(knowledge.id), "title": knowledge.title}


@router.delete("/{twin_id}/knowledge/{knowledge_id}")
def delete_twin_knowledge(twin_id: UUID, knowledge_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Delete a knowledge document."""
    success = twin_service.delete_knowledge(db, knowledge_id)
    if not success:
        raise HTTPException(status_code=404, detail="Knowledge doc not found")
    db.commit()
    return {"deleted": True, "id": str(knowledge_id)}


# ---------------------------------------------------------------------------
#  Activity (for Control Room [Watch])
# ---------------------------------------------------------------------------

@router.get("/{twin_id}/activity")
def get_twin_activity(twin_id: UUID, limit: int = 50, db: Session = Depends(get_db)):
    """Get recent activity log for a twin (used by Control Room [Watch])."""
    activities = twin_service.get_activity(db, twin_id, limit=limit)
    return [
        {
            "id": str(a.id),
            "twin_id": str(a.twin_id),
            "action_type": a.action_type,
            "description": a.description,
            "metadata": a.metadata_json,
            "timestamp": a.timestamp.isoformat() if a.timestamp else None,
        }
        for a in activities
    ]


@router.get("/{twin_id}/summary")
def get_twin_summary(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get summary of a specific twin."""
    summary = twin_service.get_twin_summary(db, twin_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Twin not found")
    return summary


# ---------------------------------------------------------------------------
#  Chat with Twin (1-on-1)
# ---------------------------------------------------------------------------

@router.post("/{twin_id}/chat")
def chat_with_twin(twin_id: UUID, body: TwinChatMessage, x_user_email: Optional[str] = Header(None), db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Send a message to a twin and get an intelligent response."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    # Set twin as working while thinking
    twin_service.set_status(db, twin_id, "working")
    db.flush()

    # Detect if boss is chatting (no worker email header = boss)
    is_boss = not x_user_email
    if is_boss:
        # Save boss message as DirectMessage so worker sees it
        from db.models import DirectMessage
        dm = DirectMessage(twin_id=twin_id, sender_type="boss", content=body.message)
        db.add(dm)
        try:
            from services.twin_notifications import notify
            notify(db, twin_id, "boss_message", f"Boss chatted with your twin", body.message[:100])
        except Exception:
            pass
        db.flush()

    # Get response from twin brain (model param routes to correct LLM provider)
    response = twin_brain.think(db, twin_id, body.message, model=body.model)

    db.commit()

    # Which provider/model ACTUALLY answered. If a picked model (e.g. a Gemini
    # one) failed and the fallback chain kicked in, this reveals the real source
    # instead of the requested model — so "Gemini answered" can be verified.
    from services.llm_client import get_last_provider
    return {
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "twin_role": twin.role,
        "message": body.message,
        "response": response,
        "requested_model": body.model,
        "answered_by": get_last_provider(),
    }


@router.post("/handoff/{handoff_id}/review")
def review_handoff(handoff_id: UUID, db: Session = Depends(get_db)):
    """Mark a handoff as reviewed by boss."""
    from db.models import TwinHandoff
    from datetime import datetime

    handoff = db.query(TwinHandoff).filter(TwinHandoff.id == handoff_id).first()
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")

    handoff.reviewed = True
    handoff.reviewed_at = datetime.utcnow()
    db.commit()

    return {"reviewed": True, "id": str(handoff_id)}


# ---------------------------------------------------------------------------
#  Direct Messages (Boss ↔ Worker)
# ---------------------------------------------------------------------------

class SendMessageBody(BaseModel):
    content: str
    sender_type: str = "boss"   # boss | worker


@router.get("/{twin_id}/messages")
def get_messages(twin_id: UUID, limit: int = 50, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Get the private worker↔twin conversation. Owner-only (privacy wall)."""
    from db.models import DirectMessage, DigitalTwin

    twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    messages = (
        db.query(DirectMessage)
        .filter(DirectMessage.twin_id == twin_id)
        .order_by(DirectMessage.created_at.asc())
        .limit(limit)
        .all()
    )

    return {
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "messages": [
            {
                "id": str(m.id),
                "sender_type": m.sender_type,
                "content": m.content,
                "is_read": m.is_read,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
        "unread_count": sum(1 for m in messages if not m.is_read),
    }


@router.post("/{twin_id}/messages")
def send_message(twin_id: UUID, body: SendMessageBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_owner_access)):
    """Send a message (boss → worker or worker → boss). v4-K: if the boss
    message contains both a meeting intent and a time, auto-schedule a
    meeting with THIS twin invited and reply with the room link.
    """
    from db.models import DirectMessage, DigitalTwin

    twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    msg = DirectMessage(
        twin_id=twin_id,
        sender_type=body.sender_type,
        content=body.content,
    )
    db.add(msg)

    # Log activity
    twin_service.log_activity(
        db, twin_id,
        "boss_message" if body.sender_type == "boss" else "worker_reply",
        f"{'Boss' if body.sender_type == 'boss' else 'Worker'}: {body.content[:80]}",
    )

    schedule_result = None
    if body.sender_type == "boss":
        try:
            from services import twin_meeting_intent, twin_meeting_scheduler
            if twin_meeting_intent.detect_meeting_intent(body.content):
                # DM-scoped: target ONLY this twin
                from services.time_parser import parse_meeting_time
                ti = parse_meeting_time(body.content) or {
                    "scheduled_at_utc": datetime.utcnow(),
                    "kind": "now",
                    "matched": "(implicit)",
                    "delta_seconds": 0,
                    "human": "right now",
                }
                from db.models import Meeting, MeetingParticipant
                fire_now = ti["delta_seconds"] <= 30
                meeting = Meeting(
                    title=f"DM meeting — {twin.name}",
                    meeting_type="one_on_one",
                    status="active" if fire_now else "scheduled",
                    scheduled_at=ti["scheduled_at_utc"],
                    started_at=datetime.utcnow() if fire_now else None,
                    created_by="vip_dm",
                    is_voice=True,
                )
                db.add(meeting)
                db.flush()
                owner = (
                    db.query(PlatformUser)
                    .filter(PlatformUser.twin_id == twin_id)
                    .first()
                )
                for_user_id = owner.id if owner else None
                if fire_now:
                    from services import twin_meeting_session
                    twin_meeting_session.join_meeting(
                        db, twin_id=twin_id, meeting_id=meeting.id,
                        for_user_id=for_user_id,
                        authority="answer_factual",
                        authorized_by_user_id=None,
                        reason="DM-scheduled meeting (immediate)",
                    )
                else:
                    participant = MeetingParticipant(
                        meeting_id=meeting.id,
                        twin_id=twin_id,
                        participant_type="twin_proxy" if for_user_id else "twin",
                        for_user_id=for_user_id,
                        meeting_authority="answer_factual",
                        session_status="invited",
                        joined_at=datetime.utcnow(),
                    )
                    db.add(participant)
                schedule_result = {
                    "ok": True,
                    "meeting_id": str(meeting.id),
                    "meeting_status": meeting.status,
                    "scheduled_at": ti["scheduled_at_utc"].isoformat(),
                    "scheduled_at_human": ti.get("human"),
                    "meeting_room_url": f"/meetings/{meeting.id}/room",
                }
                # Auto-reply as the twin so the boss sees confirmation in the DM
                reply = DirectMessage(
                    twin_id=twin_id,
                    sender_type="worker",
                    content=(
                        f"🗓 Scheduled meeting {ti.get('human')}. "
                        f"Room: /meetings/{meeting.id}/room"
                    ),
                )
                db.add(reply)
        except Exception as _e:
            schedule_result = {"ok": False, "reason": str(_e)}

    db.commit()
    return {
        "sent": True,
        "id": str(msg.id),
        "sender_type": body.sender_type,
        "twin_name": twin.name,
        "schedule_result": schedule_result,
    }


@router.post("/{twin_id}/messages/read")
def mark_messages_read(twin_id: UUID, reader: str = "worker", db: Session = Depends(get_db)):
    """Mark all messages as read (worker reads boss messages, or boss reads worker replies)."""
    from db.models import DirectMessage

    sender_to_mark = "boss" if reader == "worker" else "worker"
    messages = (
        db.query(DirectMessage)
        .filter(DirectMessage.twin_id == twin_id, DirectMessage.sender_type == sender_to_mark, DirectMessage.is_read == False)
        .all()
    )
    for m in messages:
        m.is_read = True
    db.commit()
    return {"marked_read": len(messages)}


# ---------------------------------------------------------------------------
#  Feedback / Corrections
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  Reports
# ---------------------------------------------------------------------------

class BroadcastBody(BaseModel):
    message: str
    priority: str = "normal"    # normal | urgent


@router.post("/broadcast")
def broadcast_message(body: BroadcastBody, db: Session = Depends(get_db)):
    """Boss sends a message to ALL workers at once."""
    from db.models import DirectMessage, DigitalTwin
    from services.twin_notifications import notify

    twins = db.query(DigitalTwin).all()
    sent_count = 0

    for twin in twins:
        # Save as direct message
        msg = DirectMessage(
            twin_id=twin.id,
            sender_type="boss",
            content=f"{'🚨 URGENT: ' if body.priority == 'urgent' else ''}{body.message}",
        )
        db.add(msg)

        # Create notification
        notify(db, twin.id,
            "boss_message",
            f"{'🚨 ' if body.priority == 'urgent' else ''}Message from Boss",
            body.message,
        )
        sent_count += 1

    db.commit()
    return {"sent": True, "twins_notified": sent_count, "message": body.message, "priority": body.priority}


@router.get("/reports/absences")
def get_worker_absences(hours: int = 24, db: Session = Depends(get_db)):
    """Check which workers haven't logged in (absent for X hours)."""
    from services.twin_reports import check_worker_absences
    absent = check_worker_absences(db, hours_threshold=hours)
    return {"absent_workers": absent, "count": len(absent), "threshold_hours": hours}


# ---------------------------------------------------------------------------
#  Meeting Recording & Summary
# ---------------------------------------------------------------------------

class MeetingTranscriptBody(BaseModel):
    transcript: str
    meeting_title: str = "Meeting"
    participants: list[str] = []
    meeting_id: Optional[str] = None
    save_to_twin_ids: list[str] = []


@router.post("/meetings/summarize")
def summarize_meeting(body: MeetingTranscriptBody, db: Session = Depends(get_db)):
    """Generate bilingual summary (Korean + English) from meeting transcript."""
    from services.meeting_recorder import generate_meeting_summary, save_meeting_to_twin_knowledge

    meeting_uuid = UUID(body.meeting_id) if body.meeting_id else None
    result = generate_meeting_summary(
        db, body.transcript, body.meeting_title, meeting_uuid, body.participants,
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Save to specified twins' knowledge
    for twin_id_str in body.save_to_twin_ids:
        try:
            save_meeting_to_twin_knowledge(
                db, UUID(twin_id_str), body.meeting_title,
                result.get("english_summary", ""), result.get("korean_summary", ""),
                result.get("action_items", []),
            )
        except Exception:
            pass

    db.commit()
    return result


# ---------------------------------------------------------------------------
#  Live Meeting Attendance (Sprint 1 — twin-attends-meeting feature)
# ---------------------------------------------------------------------------

@router.post("/{twin_id}/meetings/join", response_model=MeetingJoinResponse, status_code=201)
def twin_join_meeting(
    twin_id: UUID,
    body: MeetingJoinRequest,
    x_user_email: Optional[str] = Header(None),
    db: Session = Depends(get_db),
    _ac=Depends(_check_twin_access),
):
    """Twin joins a live meeting on the worker's behalf. Worker (or boss)
    sets the authority level. The voice pipeline (Sprint 2) consumes the
    returned participant_id to stream audio in/out.
    """
    from db.models import Meeting
    from services import twin_meeting_session
    from services.meeting_rate_limiter import check_join_allowed, RateLimitError

    # Sprint 6: enforce rate limits before any DB write
    try:
        check_join_allowed(db, twin_id)
    except RateLimitError as e:
        raise HTTPException(status_code=429, detail=str(e))

    meeting = db.query(Meeting).filter(Meeting.id == body.meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Resolve who is authorizing this proxy attendance
    authorized_by_id = None
    if x_user_email:
        user = db.query(PlatformUser).filter(PlatformUser.email == x_user_email).first()
        if user:
            authorized_by_id = user.id

    try:
        participant = twin_meeting_session.join_meeting(
            db,
            twin_id=twin_id,
            meeting_id=body.meeting_id,
            for_user_id=body.for_user_id,
            authority=body.authority.value,
            authorized_by_user_id=authorized_by_id,
            reason=body.reason,
        )
    except ValueError as e:
        # Validation failure (twin missing, meeting ended, duplicate join) — 400 is clearer than 404
        raise HTTPException(status_code=400, detail=str(e))

    # Move meeting to active if it isn't already
    if meeting.status == "scheduled":
        from datetime import datetime as _dt
        meeting.status = "active"
        meeting.started_at = _dt.utcnow()

    db.commit()
    return MeetingJoinResponse(
        participant_id=participant.id,
        meeting_id=participant.meeting_id,
        twin_id=participant.twin_id,
        authority=body.authority,
        session_status=participant.session_status,
        joined_at=participant.joined_at,
        is_voice=meeting.is_voice,
    )


@router.post("/{twin_id}/meetings/{meeting_id}/leave")
def twin_leave_meeting(
    twin_id: UUID,
    meeting_id: UUID,
    body: Optional[MeetingLeaveRequest] = None,
    db: Session = Depends(get_db),
    _ac=Depends(_check_twin_access),
):
    """Twin leaves the meeting. By default generates a bilingual summary,
    promotes commitments to review-tasks, and DMs the worker.
    """
    from services import twin_meeting_session

    generate_summary = body.generate_summary if body else True
    reason = body.reason if body else None
    try:
        result = twin_meeting_session.leave_meeting(
            db, twin_id, meeting_id, generate_summary=generate_summary, reason=reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    db.commit()
    return result


@router.post("/{twin_id}/meetings/{meeting_id}/escalate")
def twin_escalate_in_meeting(
    twin_id: UUID,
    meeting_id: UUID,
    body: MeetingEscalateRequest,
    db: Session = Depends(get_db),
    _ac=Depends(_check_twin_access),
):
    """Twin can't answer without the worker. DMs the worker; Sprint 2 also
    plays a Korean stall phrase aloud while waiting.
    """
    from services import twin_meeting_session
    try:
        result = twin_meeting_session.escalate(
            db, twin_id, meeting_id, body.question, body.stall_phrase_kr,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    db.commit()
    return result


@router.get("/{twin_id}/meetings/active")
def twin_active_meetings(
    twin_id: UUID,
    db: Session = Depends(get_db),
    _ac=Depends(_check_twin_access),
):
    """List meetings this twin is currently attending."""
    from services import twin_meeting_session
    sessions = twin_meeting_session.list_active_sessions(db, twin_id)
    return {"twin_id": str(twin_id), "active_sessions": sessions, "count": len(sessions)}


@router.post("/{twin_id}/meetings/{meeting_id}/utterances", status_code=201)
def record_meeting_utterance(
    twin_id: UUID,
    meeting_id: UUID,
    body: MeetingUtteranceCreate,
    db: Session = Depends(get_db),
):
    """Internal endpoint — voice pipeline (Whisper STT) pushes one utterance
    per speaker turn into the audit log. Twin commitments are auto-promoted
    to review-tasks at /leave.
    """
    from db.models import MeetingParticipant
    from services import twin_meeting_session

    # Find the participant row (if speaker is the twin)
    participant_id = None
    if body.speaker_role == "twin":
        participant = (
            db.query(MeetingParticipant)
            .filter(
                MeetingParticipant.meeting_id == meeting_id,
                MeetingParticipant.twin_id == twin_id,
                MeetingParticipant.session_status.in_(("active", "escalated")),
            )
            .first()
        )
        if participant:
            participant_id = participant.id

    utterance = twin_meeting_session.record_utterance(
        db,
        meeting_id=meeting_id,
        participant_id=participant_id,
        speaker_role=body.speaker_role,
        speaker_label=body.speaker_label,
        text=body.text,
        text_korean=body.text_korean,
        audio_url=body.audio_url,
        is_commitment=body.is_commitment,
        requires_worker_review=body.requires_worker_review,
        confidence=body.confidence,
        latency_ms=body.latency_ms,
    )
    db.commit()
    return {
        "id": str(utterance.id),
        "spoken_at": utterance.spoken_at.isoformat(),
        "is_commitment": utterance.is_commitment,
    }


# ---------------------------------------------------------------------------
#  Auto-create meeting from natural language (Sprint 8)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  Hybrid meeting room finalize (Sprint 10)
# ---------------------------------------------------------------------------

@router.post("/meetings/{meeting_id}/finalize")
def finalize_meeting_endpoint(
    meeting_id: UUID,
    send_emails: bool = True,
    save_to_twins: bool = True,
    db: Session = Depends(get_db),
):
    """End a hybrid meeting room. Generates Korean+English summary, saves
    it to every attending twin's knowledge base, and emails participants
    (when SMTP is configured). Replaces the old 'leave + summarize' flow
    for the Zoom-style multi-attendee case.
    """
    from services import twin_meeting_finalizer
    result = twin_meeting_finalizer.finalize_meeting(
        db, meeting_id, send_emails=send_emails, save_to_twins=save_to_twins,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("reason", "finalize failed"))
    return result


@router.post("/meetings/auto-create", response_model=AutoCreateMeetingResponse)
def auto_create_meeting(
    body: AutoCreateMeetingRequest,
    x_user_email: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Parse a natural-language meeting request (Korean or English) and
    auto-create a Meeting + auto-join the named twins. Hookable from:
    1) the Assistant page text box, 2) the floating voice overlay
    (already calls /chat/voice which routes here on meeting intent).

    Examples:
      "Let's have a meeting with Kim and Davronbek"
      "회의하자 김현성 트윈과 다브론벡 트윈"
      "Start a meeting with the AI team"
    """
    from services import twin_meeting_intent

    authorized_by_id = None
    if x_user_email:
        user = db.query(PlatformUser).filter(PlatformUser.email == x_user_email).first()
        if user:
            authorized_by_id = user.id

    return twin_meeting_intent.auto_create_meeting_from_text(
        db,
        text=body.text,
        authorized_by_user_id=authorized_by_id,
        authority=body.authority.value,
        meeting_type=body.meeting_type,
        title=body.title,
    )


# ---------------------------------------------------------------------------
#  Twin Speaks in Meeting (Sprint 3)
# ---------------------------------------------------------------------------

@router.post("/{twin_id}/meetings/{meeting_id}/twin-respond", response_model=TwinRespondResponse)
async def twin_respond_in_meeting(
    twin_id: UUID,
    meeting_id: UUID,
    body: TwinRespondRequest,
    db: Session = Depends(get_db),
    _ac=Depends(_check_twin_access),
):
    """Generate the twin's reply to a meeting prompt. Applies the authority
    gate — escalates instead of speaking if a commitment would exceed the
    twin's authorization. Renders TTS audio when speak_aloud=True and
    returns the audio URL the dashboard can play back.
    """
    from services import twin_meeting_orchestrator
    try:
        result = await twin_meeting_orchestrator.respond_in_meeting(
            db, twin_id, meeting_id,
            prompt=body.prompt,
            model=body.model,
            speak_aloud=body.speak_aloud,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


# ---------------------------------------------------------------------------
#  Worker Voice Clone (Sprint 4)
# ---------------------------------------------------------------------------

@router.post("/voice/users/{user_id}/consent", response_model=VoiceProfileResponse, status_code=201)
def voice_record_consent(
    user_id: UUID,
    body: VoiceConsentRequest,
    db: Session = Depends(get_db),
):
    """Worker explicitly consents to voice cloning. Required before sample
    upload. Returns the (created or updated) voice profile.
    """
    from services import voice_clone
    try:
        profile = voice_clone.record_consent(db, user_id, body.consent_text)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    db.commit()
    return voice_clone.get_profile(db, user_id)


@router.post("/voice/users/{user_id}/sample", status_code=201)
async def voice_upload_sample(
    user_id: UUID,
    file: UploadFile = File(..., description="WAV recording (>=30s, ideally 1-3 min)"),
    db: Session = Depends(get_db),
):
    """Upload a clean voice sample. Consent must already be on file."""
    from services import voice_clone
    data = await file.read()
    if len(data) > 50_000_000:
        raise HTTPException(status_code=413, detail="Voice sample too large (max 50MB)")
    try:
        result = voice_clone.store_voice_sample(db, user_id, data, file.filename or "sample.wav")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return result


@router.post("/voice/users/{user_id}/train")
def voice_start_training(
    user_id: UUID,
    db: Session = Depends(get_db),
):
    """Kick off MeloTTS fine-tune. Sprint 4 stub auto-marks ready; real
    training will run on a GPU node and update the row asynchronously.
    """
    from services import voice_clone
    try:
        result = voice_clone.start_training(db, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return result


@router.get("/voice/users/{user_id}/profile", response_model=Optional[VoiceProfileResponse])
def voice_get_profile(user_id: UUID, db: Session = Depends(get_db)):
    from services import voice_clone
    profile = voice_clone.get_profile(db, user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="No voice profile for this user")
    return profile


@router.delete("/voice/users/{user_id}/consent")
def voice_revoke_consent(user_id: UUID, db: Session = Depends(get_db)):
    """Worker revokes consent. Marks profile 'revoked' and prevents twin
    from using their cloned voice. Sample file is retained until purge job
    runs (Sprint 6 retention policy).
    """
    from services import voice_clone
    result = voice_clone.revoke_consent(db, user_id)
    db.commit()
    return result


# ---------------------------------------------------------------------------
#  Live STT Listener (Sprint 2)
# ---------------------------------------------------------------------------

@router.post("/{twin_id}/meetings/{meeting_id}/listen/upload", status_code=202)
async def start_listening_upload(
    twin_id: UUID,
    meeting_id: UUID,
    file: UploadFile = File(..., description="WAV file (mono or stereo, any sample rate)"),
    speaker_label: str = "Meeting Audio",
    db: Session = Depends(get_db),
    _ac=Depends(_check_twin_access),
):
    """Upload a WAV recording of a meeting. The listener transcribes it
    chunk-by-chunk via Whisper and writes MeetingUtterance rows in real
    time. Returns a session_id immediately; poll /listen/status to watch
    progress.
    """
    from services import twin_voice_listener

    # Persist upload to a temp file under .uploads/meetings/
    upload_dir = Path("uploads") / "meeting_audio"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    out_path = upload_dir / f"{meeting_id}_{uuid4()}{safe_suffix}"
    data = await file.read()
    if len(data) > 50_000_000:  # 50 MB cap
        raise HTTPException(status_code=413, detail="Audio file too large (max 50MB)")
    out_path.write_bytes(data)

    try:
        session_id = twin_voice_listener.start_listening_from_file(
            twin_id=twin_id,
            meeting_id=meeting_id,
            audio_path=str(out_path),
            speaker_label=speaker_label,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start listener: {e}")

    return {
        "session_id": session_id,
        "twin_id": str(twin_id),
        "meeting_id": str(meeting_id),
        "source": "file",
        "audio_filename": file.filename,
        "audio_bytes": len(data),
        "status_url": f"/twins/{twin_id}/meetings/{meeting_id}/listen/status?session_id={session_id}",
    }


@router.post("/{twin_id}/meetings/{meeting_id}/listen/asterisk", status_code=202)
def start_listening_asterisk(
    twin_id: UUID,
    meeting_id: UUID,
    body: MeetingListenStartAsterisk,
    db: Session = Depends(get_db),
    _ac=Depends(_check_twin_access),
):
    """Attach to a live Asterisk SIP channel. Sprint 2 returns a stub
    error; Sprint 3 wires the AudioSocket bridge.
    """
    from services import twin_voice_listener
    session_id = twin_voice_listener.start_listening_from_asterisk(
        twin_id=twin_id,
        meeting_id=meeting_id,
        asterisk_channel_id=body.asterisk_channel_id,
        speaker_label=body.speaker_label,
    )
    status = twin_voice_listener.get_status(session_id)
    return status


@router.get("/{twin_id}/meetings/{meeting_id}/listen/status")
def get_listening_status(
    twin_id: UUID,
    meeting_id: UUID,
    session_id: Optional[str] = None,
    _ac=Depends(_check_twin_access),
):
    """Poll for a single session's status (pass session_id) or list every
    active listener for this meeting.
    """
    from services import twin_voice_listener
    if session_id:
        status = twin_voice_listener.get_status(session_id)
        if not status:
            raise HTTPException(status_code=404, detail="Listener session not found")
        return status
    return {
        "meeting_id": str(meeting_id),
        "listeners": twin_voice_listener.list_active_listeners(meeting_id),
    }


@router.post("/{twin_id}/meetings/{meeting_id}/listen/stop")
def stop_listening(
    twin_id: UUID,
    meeting_id: UUID,
    session_id: str,
    _ac=Depends(_check_twin_access),
):
    """Cancel a running listener session."""
    from services import twin_voice_listener
    return twin_voice_listener.stop_listening(session_id)


@router.get("/{twin_id}/meetings/{meeting_id}/utterances")
def list_meeting_utterances(
    twin_id: UUID,
    meeting_id: UUID,
    redact: bool = False,
    db: Session = Depends(get_db),
    _ac=Depends(_check_twin_access),
):
    """Replay the meeting transcript with speaker attribution. Pass
    ?redact=true to mask PII (phones, emails, RRN, cards) — Sprint 6.
    """
    from db.models import MeetingUtterance
    rows = (
        db.query(MeetingUtterance)
        .filter(MeetingUtterance.meeting_id == meeting_id)
        .order_by(MeetingUtterance.spoken_at.asc())
        .all()
    )
    out_rows = [
        {
            "id": str(u.id),
            "speaker_role": u.speaker_role,
            "speaker_label": u.speaker_label,
            "text": u.text,
            "text_korean": u.text_korean,
            "audio_url": u.audio_url,
            "spoken_at": u.spoken_at.isoformat() if u.spoken_at else None,
            "is_commitment": u.is_commitment,
            "requires_worker_review": u.requires_worker_review,
            "confidence": u.confidence,
            "latency_ms": u.latency_ms,
        }
        for u in rows
    ]
    if redact:
        from services.pii_redactor import redact_utterances
        out_rows = redact_utterances(out_rows)
    return {
        "meeting_id": str(meeting_id),
        "twin_id": str(twin_id),
        "count": len(rows),
        "redacted": redact,
        "utterances": out_rows,
    }


# ---------------------------------------------------------------------------
#  Sprint 6 — Admin / Ops endpoints (metrics + retention + rate limits)
# ---------------------------------------------------------------------------

@router.get("/admin/meeting-metrics/system")
def get_system_meeting_metrics(
    since_days: int = 30,
    db: Session = Depends(get_db),
):
    """System-wide meeting health for the ops dashboard."""
    from services import meeting_metrics
    from services.meeting_rate_limiter import current_limits
    return {
        "metrics": meeting_metrics.system_wide(db, since_days=since_days),
        "rate_limits": current_limits(),
        "recent_escalations": meeting_metrics.recent_escalations(db, limit=10),
    }


@router.get("/admin/meeting-metrics/twin/{twin_id}")
def get_twin_meeting_metrics(
    twin_id: UUID,
    since_days: int = 30,
    db: Session = Depends(get_db),
):
    """Per-twin meeting health: commitments, escalations, latency."""
    from services import meeting_metrics
    summary = meeting_metrics.per_twin_summary(db, twin_id, since_days=since_days)
    if not summary:
        raise HTTPException(status_code=404, detail="Twin not found")
    return summary


@router.post("/admin/retention/purge")
def admin_run_retention(
    dry_run: bool = True,
    db: Session = Depends(get_db),
):
    """Manually trigger the retention purge job. Pass dry_run=false to
    actually delete files. Wire this to the existing scheduler_service
    for daily auto-cleanup.
    """
    from services import meeting_retention
    return meeting_retention.run_all(db, dry_run=dry_run)


@router.get("/admin/rate-limits")
def admin_get_rate_limits():
    """Read current rate-limit configuration + in-memory state."""
    from services.meeting_rate_limiter import current_limits
    return current_limits()


# ---------------------------------------------------------------------------
#  v4-A: Twin Autopilot — periodic self-improvement
# ---------------------------------------------------------------------------

@router.get("/admin/autopilot/status")
def admin_autopilot_status():
    from services import twin_autopilot
    return twin_autopilot.get_status()


@router.post("/admin/autopilot/run-now")
def admin_autopilot_run_now():
    """Trigger one autopilot cycle immediately (any-time manual run)."""
    from services import twin_autopilot
    return twin_autopilot.run_all_twins_cycle()


@router.post("/admin/autopilot/install")
def admin_autopilot_install():
    """Install / re-install the autopilot cron job in the running scheduler.
    Idempotent — safe to call after a code change.
    """
    from services import twin_autopilot
    return twin_autopilot.register_with_scheduler()


# ---------------------------------------------------------------------------
#  v4-E: Twin auto-join dispatcher controls
# ---------------------------------------------------------------------------

@router.get("/admin/autojoin/status")
def admin_autojoin_status():
    from services import twin_meeting_autojoin
    return twin_meeting_autojoin.get_status()


@router.post("/admin/autojoin/install")
def admin_autojoin_install():
    from services import twin_meeting_autojoin
    return twin_meeting_autojoin.register_with_scheduler()


@router.post("/admin/autojoin/run-now")
def admin_autojoin_run_now():
    """Manually trigger one autojoin sweep — useful for testing scheduled meetings."""
    from services import twin_meeting_autojoin
    return twin_meeting_autojoin.run_once()


# ---------------------------------------------------------------------------
#  v4-J: Backfill avatars for every twin lacking one
# ---------------------------------------------------------------------------

@router.post("/admin/avatars/backfill")
def admin_backfill_avatars(db: Session = Depends(get_db)):
    """Assign a DiceBear avatar URL to every twin that doesn't have one yet."""
    from services import twin_avatar
    from db.models import DigitalTwin
    twins = db.query(DigitalTwin).filter(
        (DigitalTwin.avatar_url == None) | (DigitalTwin.avatar_url == "")  # noqa: E711
    ).all()
    updated = 0
    for t in twins:
        t.avatar_url = twin_avatar.url_for_twin(str(t.id), t.name)
        updated += 1
    db.commit()
    return {"updated": updated, "total_checked": len(twins)}


# ---------------------------------------------------------------------------
#  v4-B: Twin Readiness Audit
# ---------------------------------------------------------------------------

@router.get("/admin/readiness/all")
def admin_readiness_all(db: Session = Depends(get_db)):
    """Audit every twin and return per-twin completeness scores + missing pieces."""
    from services import twin_readiness
    return twin_readiness.audit_all(db)


@router.get("/admin/readiness/twin/{twin_id}")
def admin_readiness_twin(twin_id: UUID, db: Session = Depends(get_db)):
    from services import twin_readiness
    result = twin_readiness.audit_twin(db, twin_id)
    if not result:
        raise HTTPException(status_code=404, detail="Twin not found")
    return result


# ---------------------------------------------------------------------------
#  Daily / Monthly / Weekly Reports
# ---------------------------------------------------------------------------

@router.get("/reports/boss-briefing")
def get_boss_briefing(db: Session = Depends(get_db)):
    """Get daily briefing for boss — all twins overnight summary."""
    from services.twin_reports import generate_boss_briefing
    return generate_boss_briefing(db)


@router.get("/reports/monthly")
def get_monthly_comparison(db: Session = Depends(get_db)):
    """Get monthly twin comparison report (boss view)."""
    from services.twin_reports import generate_monthly_comparison
    return generate_monthly_comparison(db)


@router.get("/reports/weekly")
def get_weekly_update(db: Session = Depends(get_db)):
    """Get weekly team performance report (boss view)."""
    from services.twin_reports import generate_weekly_update
    return generate_weekly_update(db)


class WeeklyMessageBody(BaseModel):
    message: str = ""


@router.post("/reports/weekly/send")
def send_weekly_update(body: WeeklyMessageBody, db: Session = Depends(get_db)):
    """Boss generates weekly report with personal message and sends to all workers."""
    from services.twin_reports import generate_weekly_update
    report = generate_weekly_update(db, boss_message=body.message)

    # Save as direct message to all twins
    twins = db.query(DigitalTwin).all()
    from db.models import DirectMessage

    summary = f"Weekly Update from Boss:\n\n{body.message}\n\nTeam Performance: {report['company_stats']['total_tasks_completed']} tasks completed, avg progress {report['company_stats']['average_progress']}%"

    for twin in twins:
        msg = DirectMessage(
            twin_id=twin.id,
            sender_type="boss",
            content=summary,
        )
        db.add(msg)

    db.commit()
    return {"sent": True, "twins_notified": len(twins), "report": report}


# ---------------------------------------------------------------------------
#  Worker Notifications
# ---------------------------------------------------------------------------

@router.get("/{twin_id}/notifications")
def get_twin_notifications(twin_id: UUID, unread_only: bool = False, limit: int = 20, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get notifications for a twin (task completed, etc.)."""
    from services.twin_notifications import get_notifications, get_unread_count
    notifications = get_notifications(db, twin_id, unread_only=unread_only, limit=limit)
    unread = get_unread_count(db, twin_id)
    return {
        "notifications": [
            {
                "id": str(n.id),
                "type": n.type,
                "title": n.title,
                "body": n.body,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
        "unread_count": unread,
    }


@router.post("/{twin_id}/notifications/read-all")
def mark_all_notifications_read(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Mark all notifications as read."""
    from services.twin_notifications import mark_all_read
    count = mark_all_read(db, twin_id)
    db.commit()
    return {"marked_read": count}


@router.get("/{twin_id}/reports/weekly-self")
def get_weekly_self_report(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get twin's weekly self-report — what it learned and achieved this week."""
    from services.twin_reports import generate_weekly_self_report
    report = generate_weekly_self_report(db, twin_id)
    if "error" in report:
        raise HTTPException(status_code=404, detail=report["error"])
    return report


class EveningHandoffBody(BaseModel):
    selected_task_ids: list[str] = []
    new_tasks: list[dict] = []
    instructions: str = ""


@router.get("/{twin_id}/reports/evening")
def get_evening_handoff(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get evening handoff data — today's summary + unfinished tasks."""
    from services.twin_reports import get_evening_handoff_data
    return get_evening_handoff_data(db, twin_id)


@router.post("/{twin_id}/reports/evening/handoff")
def submit_evening_handoff(twin_id: UUID, body: EveningHandoffBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Worker submits evening handoff — select tasks + add new + instructions → twin takes over."""
    from services.twin_reports import process_evening_handoff
    result = process_evening_handoff(db, twin_id, body.selected_task_ids, body.new_tasks, body.instructions)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    db.commit()
    return result


@router.get("/{twin_id}/reports/morning")
def get_morning_report(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get morning report for a worker — what twin did overnight + today's tasks."""
    from services.twin_reports import generate_morning_report
    report = generate_morning_report(db, twin_id)
    if "error" in report:
        raise HTTPException(status_code=404, detail=report["error"])
    return report


# ---------------------------------------------------------------------------
#  Twin Version Snapshots (from colleague.skill)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  AI Session Import (Claude Code, ChatGPT, Gemini)
# ---------------------------------------------------------------------------

@router.get("/claude-projects/list")
def list_claude_code_projects():
    """List all Claude Code projects on the PC."""
    from services.claude_auto_import import list_claude_projects
    return list_claude_projects()


class ClaudeAutoImportBody(BaseModel):
    project_filter: Optional[str] = None   # e.g. "c--Users-TRIPLEH-Desktop-VIP-Agent"
    hours: int = 24
    max_sessions: int = 10


@router.post("/{twin_id}/import/claude-auto")
def auto_import_claude(twin_id: UUID, body: ClaudeAutoImportBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Auto-import Claude Code sessions from PC files — no copy-paste needed."""
    from services.claude_auto_import import import_recent_sessions
    result = import_recent_sessions(db, twin_id, body.project_filter, body.hours, body.max_sessions)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    db.commit()
    return result


class ClaudeImportBody(BaseModel):
    session_text: str
    session_title: Optional[str] = None
    auto_extract: bool = True


class GenericAIImportBody(BaseModel):
    session_text: str
    source: str = "chatgpt"   # chatgpt | gemini | claude | bard | copilot
    session_title: Optional[str] = None


@router.post("/{twin_id}/import/claude")
def import_claude(twin_id: UUID, body: ClaudeImportBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Import a Claude Code session — twin learns from your coding work."""
    from services.claude_import import import_claude_session
    result = import_claude_session(db, twin_id, body.session_text, body.session_title, body.auto_extract)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    db.commit()
    return result


@router.post("/{twin_id}/import/ai-session")
def import_ai_session(twin_id: UUID, body: GenericAIImportBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Import a ChatGPT/Gemini/Claude web conversation."""
    from services.claude_import import import_generic_ai_session
    result = import_generic_ai_session(db, twin_id, body.session_text, body.source, body.session_title)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    db.commit()
    return result


class SnapshotBody(BaseModel):
    version_name: str
    notes: str = ""


@router.get("/{twin_id}/snapshots")
def list_twin_snapshots(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """List all snapshots for a twin."""
    from services.twin_snapshots import list_snapshots
    return list_snapshots(db, twin_id)


@router.post("/{twin_id}/snapshots", status_code=201)
def create_twin_snapshot(twin_id: UUID, body: SnapshotBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Create a snapshot of current twin state (save/backup)."""
    from services.twin_snapshots import create_snapshot
    snapshot = create_snapshot(db, twin_id, body.version_name, body.notes)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Twin not found")
    db.commit()
    return {
        "created": True,
        "id": str(snapshot.id),
        "version_name": snapshot.version_name,
        "knowledge_count": snapshot.knowledge_count,
        "intelligence_pct": snapshot.intelligence_pct,
    }


@router.post("/{twin_id}/snapshots/{snapshot_id}/restore")
def restore_twin_snapshot(twin_id: UUID, snapshot_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Restore twin to a previous snapshot."""
    from services.twin_snapshots import restore_snapshot
    result = restore_snapshot(db, snapshot_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    db.commit()
    return result


@router.delete("/{twin_id}/snapshots/{snapshot_id}")
def delete_twin_snapshot(twin_id: UUID, snapshot_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Delete a snapshot."""
    from services.twin_snapshots import delete_snapshot
    success = delete_snapshot(db, snapshot_id)
    if not success:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    db.commit()
    return {"deleted": True}


@router.post("/{twin_id}/self-improve")
def run_self_improvement(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Manually trigger self-improvement cycle for a twin."""
    from services.twin_self_improve import run_self_improvement_cycle
    result = run_self_improvement_cycle(db, twin_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    db.commit()
    return result


@router.get("/{twin_id}/self-improve/history")
def get_self_improvement_history(twin_id: UUID, limit: int = 20, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get recent self-improvement activities."""
    from db.models import TwinActivityLog
    activities = (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id, TwinActivityLog.action_type == "self_improve")
        .order_by(TwinActivityLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(a.id),
            "description": a.description,
            "metadata": a.metadata_json,
            "timestamp": a.timestamp.isoformat(),
        }
        for a in activities
    ]


@router.get("/{twin_id}/intelligence")
def get_twin_intel(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get intelligence metrics for a specific twin."""
    from services.twin_intelligence import get_twin_intelligence
    result = get_twin_intelligence(db, twin_id)
    if not result:
        raise HTTPException(status_code=404, detail="Twin not found")
    return result


@router.get("/{twin_id}/intelligence/timeline")
def get_intel_timeline(twin_id: UUID, days: int = 30, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get learning growth timeline (daily data for charts)."""
    from services.twin_intelligence import get_learning_timeline
    return get_learning_timeline(db, twin_id, days=days)


# ---------------------------------------------------------------------------
#  Google Drive Integration
# ---------------------------------------------------------------------------

@router.get("/gdrive/status")
def gdrive_status():
    """Check if Google Drive integration is configured."""
    from services.gdrive_service import is_configured
    return {"configured": is_configured()}


@router.get("/{twin_id}/gdrive/auth-url")
def get_gdrive_auth_url(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get Google OAuth URL for worker to authorize Drive access."""
    from services.gdrive_service import is_configured, get_auth_url
    if not is_configured():
        raise HTTPException(400, "Google Drive not configured. Admin needs to set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")
    return {"auth_url": get_auth_url(str(twin_id))}


@router.post("/{twin_id}/gdrive/connect")
def connect_gdrive(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Exchange OAuth code and store tokens (called after redirect)."""
    # This will be called from the frontend callback page
    return {"status": "endpoint ready — implement callback flow"}


@router.post("/{twin_id}/gdrive/pull")
def pull_gdrive_docs(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Manually trigger Google Drive document pull."""
    from services.gdrive_service import is_configured
    if not is_configured():
        raise HTTPException(400, "Google Drive not configured")
    # TODO: Pull docs using stored access token
    return {"status": "Google Drive pull — requires OAuth setup first", "twin_id": str(twin_id)}


class CorrectionBody(BaseModel):
    task_title: str
    what_was_wrong: str
    correct_approach: str


@router.post("/{twin_id}/correct")
def submit_correction(twin_id: UUID, body: CorrectionBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Worker or boss submits a correction — twin learns from it."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    # Save as knowledge
    twin_service.add_knowledge(
        db, twin_id,
        title=f"Correction: {body.task_title}",
        content=(
            f"CORRECTION:\n"
            f"Task: {body.task_title}\n"
            f"What was wrong: {body.what_was_wrong}\n"
            f"Correct approach: {body.correct_approach}\n"
            f"RULE: Always follow the correct approach above."
        ),
        source_type="decision",
    )

    twin_service.log_activity(
        db, twin_id, "feedback",
        f"Learned correction: {body.task_title}",
        {"wrong": body.what_was_wrong[:100], "correct": body.correct_approach[:100]},
    )

    db.commit()
    return {"saved": True, "twin": twin.name, "correction": body.task_title}
