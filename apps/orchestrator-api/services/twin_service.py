"""
VIP AI Platform — Digital Twin Service
CRUD operations, mode switching, knowledge management, activity logging.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import (
    DigitalTwin, TwinKnowledge, TwinActivityLog, TwinTask, TwinHandoff, WorkerStatus,
    TwinKnowledgeEmbedding,
)
from services import embeddings_service
from services.logger import log


# ---------------------------------------------------------------------------
#  Twin CRUD
# ---------------------------------------------------------------------------

def create_twin(
    db: Session,
    name: str,
    role: str,
    department: Optional[str] = None,
    avatar_url: Optional[str] = None,
    personality_prompt: Optional[str] = None,
    skills: Optional[list] = None,
    permission_level: str = "suggest",
    linked_agent_id: Optional[UUID] = None,
) -> DigitalTwin:
    twin = DigitalTwin(
        name=name,
        role=role,
        department=department,
        avatar_url=avatar_url,
        personality_prompt=personality_prompt,
        skills=skills or [],
        permission_level=permission_level,
        linked_agent_id=linked_agent_id,
        mode="shadow",
        status="idle",
    )
    db.add(twin)
    db.flush()
    return twin


def get_twin(db: Session, twin_id: UUID) -> Optional[DigitalTwin]:
    return db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()


def list_twins(db: Session) -> list[DigitalTwin]:
    return db.query(DigitalTwin).order_by(DigitalTwin.created_at.desc()).all()


def update_twin(db: Session, twin_id: UUID, **kwargs) -> Optional[DigitalTwin]:
    twin = get_twin(db, twin_id)
    if not twin:
        return None
    for key, value in kwargs.items():
        if value is not None and hasattr(twin, key):
            setattr(twin, key, value)
    twin.updated_at = datetime.utcnow()
    db.flush()
    return twin


def delete_twin(db: Session, twin_id: UUID) -> bool:
    """Delete a twin and all its data.

    Bulk-deletes child rows with one SQL statement per table instead of the
    ORM's row-by-row cascade (which hangs on data-heavy twins — hundreds of
    knowledge/activity/message rows). Workers and meeting messages reference a
    twin nullably, so we unlink those rather than delete the parent rows.
    """
    twin = get_twin(db, twin_id)
    if not twin:
        return False

    from db.models import (
        MeetingParticipant, MeetingMessage, DirectMessage, TwinSnapshot,
        TwinNotification, MeetingHandRaise, TwinGroupMember, TwinGroupMessage,
        PlatformUser,
    )

    # Clear the twin's own pointer to a task we're about to delete.
    twin.current_task_id = None
    db.flush()

    # Hard-delete rows that belong to this twin (twin_id NOT NULL).
    for Model in (TwinKnowledge, TwinActivityLog, TwinTask, TwinHandoff,
                  MeetingParticipant, DirectMessage, TwinSnapshot,
                  TwinNotification, MeetingHandRaise, TwinGroupMember):
        db.query(Model).filter(Model.twin_id == twin_id).delete(synchronize_session=False)

    # Nullable references → unlink (never delete the worker account).
    db.query(PlatformUser).filter(PlatformUser.twin_id == twin_id).update(
        {"twin_id": None}, synchronize_session=False)
    db.query(MeetingMessage).filter(MeetingMessage.sender_twin_id == twin_id).update(
        {"sender_twin_id": None}, synchronize_session=False)
    db.query(TwinGroupMessage).filter(TwinGroupMessage.sender_twin_id == twin_id).update(
        {"sender_twin_id": None}, synchronize_session=False)

    db.delete(twin)
    db.flush()
    return True


# ---------------------------------------------------------------------------
#  Mode Switching
# ---------------------------------------------------------------------------

def switch_mode(db: Session, twin_id: UUID, mode: str) -> Optional[DigitalTwin]:
    twin = get_twin(db, twin_id)
    if not twin:
        return None
    old_mode = twin.mode
    twin.mode = mode
    twin.updated_at = datetime.utcnow()

    # Log mode change
    log_activity(db, twin_id, "mode_switch", f"Mode changed: {old_mode} → {mode}")
    db.flush()
    return twin


def set_status(db: Session, twin_id: UUID, status: str) -> Optional[DigitalTwin]:
    twin = get_twin(db, twin_id)
    if not twin:
        return None
    twin.status = status
    twin.updated_at = datetime.utcnow()
    db.flush()
    return twin


# ---------------------------------------------------------------------------
#  Knowledge Management
# ---------------------------------------------------------------------------

def add_knowledge(
    db: Session,
    twin_id: UUID,
    title: str,
    content: str,
    source_type: str = "document",
) -> TwinKnowledge:
    knowledge = TwinKnowledge(
        twin_id=twin_id,
        title=title,
        content=content,
        source_type=source_type,
    )
    db.add(knowledge)
    db.flush()
    # Best-effort: embed for semantic recall. Never let it break the insert.
    try:
        _embed_and_store(db, knowledge)
    except Exception as e:
        log.warning(f"add_knowledge: embed failed (will backfill later): {e}")
    return knowledge


# ---------------------------------------------------------------------------
#  Semantic memory (vector embeddings for knowledge)
# ---------------------------------------------------------------------------

def _embed_and_store(db: Session, k: TwinKnowledge) -> bool:
    """Embed a knowledge row's title+content and upsert its vector. Best-effort."""
    if not embeddings_service.available():
        return False
    text = f"{k.title or ''}\n{k.content or ''}".strip()
    vec = embeddings_service.embed_text(text)
    if not vec:
        return False
    existing = db.query(TwinKnowledgeEmbedding).filter(
        TwinKnowledgeEmbedding.knowledge_id == k.id).first()
    if existing:
        existing.embedding = vec
    else:
        db.add(TwinKnowledgeEmbedding(
            knowledge_id=k.id, twin_id=k.twin_id, embedding=vec,
            model=embeddings_service.EMBED_MODEL, dim=len(vec),
        ))
    db.flush()
    return True


def get_twin_embeddings(db: Session, twin_id: UUID) -> dict:
    """Return {knowledge_id(str): vector} for a twin — used at retrieval time."""
    rows = db.query(TwinKnowledgeEmbedding).filter(
        TwinKnowledgeEmbedding.twin_id == twin_id).all()
    return {str(r.knowledge_id): r.embedding for r in rows}


def reindex_twin(db: Session, twin_id: UUID, limit: int = 2000) -> dict:
    """Embed all of a twin's knowledge rows that don't yet have a vector.

    Batched for speed/cost. Returns counts. Safe to re-run (idempotent).
    """
    if not embeddings_service.available():
        return {"ok": False, "reason": "no embedding key", "embedded": 0, "skipped": 0}

    have = {str(kid) for (kid,) in db.query(TwinKnowledgeEmbedding.knowledge_id)
            .filter(TwinKnowledgeEmbedding.twin_id == twin_id).all()}
    docs = (db.query(TwinKnowledge)
            .filter(TwinKnowledge.twin_id == twin_id)
            .order_by(TwinKnowledge.created_at.desc())
            .limit(limit).all())
    todo = [d for d in docs if str(d.id) not in have]

    embedded = 0
    BATCH = 64
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        texts = [f"{d.title or ''}\n{d.content or ''}".strip() for d in chunk]
        vecs = embeddings_service.embed_texts(texts)
        if not vecs or len(vecs) != len(chunk):
            continue
        for d, v in zip(chunk, vecs):
            if not v:
                continue
            db.add(TwinKnowledgeEmbedding(
                knowledge_id=d.id, twin_id=d.twin_id, embedding=v,
                model=embeddings_service.EMBED_MODEL, dim=len(v),
            ))
            embedded += 1
        db.flush()
    db.commit()
    return {"ok": True, "embedded": embedded, "already_had": len(have),
            "total_knowledge": len(docs)}


def get_knowledge(db: Session, twin_id: UUID) -> list[TwinKnowledge]:
    return (
        db.query(TwinKnowledge)
        .filter(TwinKnowledge.twin_id == twin_id)
        .order_by(TwinKnowledge.created_at.desc())
        .all()
    )


def delete_knowledge(db: Session, knowledge_id: UUID) -> bool:
    knowledge = db.query(TwinKnowledge).filter(TwinKnowledge.id == knowledge_id).first()
    if not knowledge:
        return False
    db.delete(knowledge)
    db.flush()
    return True


# ---------------------------------------------------------------------------
#  Activity Logging
# ---------------------------------------------------------------------------

def log_activity(
    db: Session,
    twin_id: UUID,
    action_type: str,
    description: str,
    metadata: Optional[dict] = None,
) -> TwinActivityLog:
    log = TwinActivityLog(
        twin_id=twin_id,
        action_type=action_type,
        description=description,
        metadata_json=metadata or {},
        timestamp=datetime.utcnow(),
    )
    db.add(log)
    db.flush()
    return log


def get_activity(db: Session, twin_id: UUID, limit: int = 50) -> list[TwinActivityLog]:
    return (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id)
        .order_by(TwinActivityLog.timestamp.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
#  Task Management
# ---------------------------------------------------------------------------

def create_task(
    db: Session,
    twin_id: UUID,
    title: str,
    description: Optional[str] = None,
    priority: str = "medium",
    deadline: Optional[datetime] = None,
    assigned_by: str = "vip",
    meeting_id: Optional[UUID] = None,
) -> TwinTask:
    task = TwinTask(
        twin_id=twin_id,
        title=title,
        description=description,
        priority=priority,
        deadline=deadline,
        assigned_by=assigned_by,
        assigned_in_meeting_id=meeting_id,
        status="todo",
        needs_review=False,
    )
    db.add(task)
    db.flush()

    log_activity(db, twin_id, "task_assigned", f"New task: {title} (priority: {priority})")
    return task


def get_tasks(db: Session, twin_id: UUID) -> list[TwinTask]:
    return (
        db.query(TwinTask)
        .filter(TwinTask.twin_id == twin_id)
        .order_by(TwinTask.created_at.desc())
        .all()
    )


def get_all_tasks(db: Session, status: Optional[str] = None) -> list[TwinTask]:
    query = db.query(TwinTask)
    if status:
        query = query.filter(TwinTask.status == status)
    return query.order_by(TwinTask.created_at.desc()).all()


def update_task_status(
    db: Session,
    task_id: UUID,
    status: str,
    result_text: Optional[str] = None,
    result_json: Optional[dict] = None,
) -> Optional[TwinTask]:
    task = db.query(TwinTask).filter(TwinTask.id == task_id).first()
    if not task:
        return None
    task.status = status
    if status == "in_progress" and not task.started_at:
        task.started_at = datetime.utcnow()
    if status in ("review", "done"):
        task.completed_at = datetime.utcnow()
    if status == "review":
        task.needs_review = True
        task.review_status = "pending"
    if result_text:
        task.result_text = result_text
    if result_json:
        task.result_json = result_json
    db.flush()
    return task


def review_task(
    db: Session,
    task_id: UUID,
    review_status: str,
    reviewed_by: str = "vip",
    comment: Optional[str] = None,
) -> Optional[TwinTask]:
    task = db.query(TwinTask).filter(TwinTask.id == task_id).first()
    if not task:
        return None
    task.review_status = review_status
    task.reviewed_by = reviewed_by
    task.review_comment = comment
    if review_status == "approved":
        task.status = "done"
        # Auto-post a high-level milestone to the Twin Feed (only if owner opted in).
        try:
            from services import twin_feed
            twin_feed.autopost(db, task.twin_id, f"✅ Finished: {task.title}", kind="win")
        except Exception:
            pass

    # --- Feedback → Knowledge Loop ---
    # When rejected: save the correction so twin never repeats the mistake
    if review_status == "rejected" and comment:
        add_knowledge(
            db,
            twin_id=task.twin_id,
            title=f"Correction: {task.title}",
            content=(
                f"CORRECTION from {reviewed_by}:\n"
                f"Task: {task.title}\n"
                f"What I did wrong: {(task.result_text or '')[:300]}\n"
                f"Feedback: {comment}\n"
                f"RULE: Do NOT repeat this mistake. Follow the feedback above."
            ),
            source_type="decision",
        )
        log_activity(
            db, task.twin_id, "feedback",
            f"Learned from rejection: {comment[:80]}",
            {"task": task.title, "feedback": comment},
        )

    # When approved: save as positive reinforcement
    if review_status == "approved" and task.result_text:
        add_knowledge(
            db,
            twin_id=task.twin_id,
            title=f"Approved approach: {task.title}",
            content=(
                f"APPROVED WORK:\n"
                f"Task: {task.title}\n"
                f"What I did: {task.result_text[:300]}\n"
                f"Result: Boss approved this approach. Use similar approach for similar tasks."
            ),
            source_type="decision",
        )
        log_activity(
            db, task.twin_id, "feedback",
            f"Positive reinforcement: {task.title} approved",
            {"task": task.title},
        )

    db.flush()
    return task


# ---------------------------------------------------------------------------
#  Twin Summary (for Control Room / Dashboard)
# ---------------------------------------------------------------------------

def get_twin_summary(db: Session, twin_id: UUID) -> dict:
    twin = get_twin(db, twin_id)
    if not twin:
        return {}
    current_task = None
    if twin.current_task_id:
        ct = db.query(TwinTask).filter(TwinTask.id == twin.current_task_id).first()
        if ct:
            current_task = {"id": str(ct.id), "title": ct.title, "status": ct.status}

    last_activity = (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id)
        .order_by(TwinActivityLog.timestamp.desc())
        .first()
    )

    return {
        "id": str(twin.id),
        "name": twin.name,
        "role": twin.role,
        "department": twin.department,
        "mode": twin.mode,
        "status": twin.status,
        "permission_level": twin.permission_level,
        "current_task": current_task,
        "last_activity": last_activity.description if last_activity else None,
        "last_active_at": last_activity.timestamp.isoformat() if last_activity else None,
    }


def get_all_twin_summaries(db: Session) -> list[dict]:
    twins = list_twins(db)
    return [get_twin_summary(db, twin.id) for twin in twins]
