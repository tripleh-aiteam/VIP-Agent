"""
VIP AI Platform — Twin Readiness Audit (v4 Phase 1)
Scores every twin 0-100% on completeness for production use in meetings.
Tells the boss exactly what's missing per twin.

Components (weighted):
  - Personality prompt set            (10%)
  - Skills list populated             (10%)
  - Knowledge volume >= 5 docs        (15%)
  - At least 3 decision rules         (10%)
  - At least 1 correction logged      (10%)
  - At least 1 task completed         (10%)
  - At least 1 snapshot saved         (10%)
  - Linked PlatformUser (worker)      (10%)
  - Has attended >= 1 meeting         (10%)
  - Has worker voice profile ready    (5%)
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import (
    DigitalTwin, TwinKnowledge, TwinTask, TwinActivityLog,
    TwinSnapshot, PlatformUser, MeetingParticipant, WorkerVoiceProfile,
)


_COMPONENTS = [
    ("personality", "Personality prompt set", 10),
    ("skills", "Skills list populated", 10),
    ("knowledge_volume", "≥ 5 knowledge docs", 15),
    ("decision_rules", "≥ 3 decision rules", 10),
    ("corrections", "≥ 1 correction recorded", 10),
    ("tasks_done", "≥ 1 task completed", 10),
    ("snapshots", "≥ 1 snapshot saved", 10),
    ("linked_worker", "Linked to a worker (PlatformUser)", 10),
    ("meetings_attended", "Attended ≥ 1 meeting", 10),
    ("voice_profile", "Worker voice profile ready", 5),
]


def audit_twin(db: Session, twin_id: UUID) -> dict:
    twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not twin:
        return {}

    knowledge = db.query(TwinKnowledge).filter(TwinKnowledge.twin_id == twin_id).all()
    n_docs = len(knowledge)
    n_decisions = sum(1 for k in knowledge if k.source_type == "decision")
    n_corrections = sum(
        1 for k in knowledge
        if "correction" in (k.title or "").lower() or "correction" in (k.content or "").lower()[:200]
    )
    n_tasks_done = (
        db.query(TwinTask)
        .filter(TwinTask.twin_id == twin_id, TwinTask.status == "done")
        .count()
    )
    n_snapshots = (
        db.query(TwinSnapshot)
        .filter(TwinSnapshot.twin_id == twin_id)
        .count()
    )
    linked = (
        db.query(PlatformUser)
        .filter(PlatformUser.twin_id == twin_id)
        .first()
    )
    n_meetings = (
        db.query(MeetingParticipant)
        .filter(MeetingParticipant.twin_id == twin_id)
        .count()
    )
    voice_ready = False
    if linked:
        vp = (
            db.query(WorkerVoiceProfile)
            .filter(
                WorkerVoiceProfile.user_id == linked.id,
                WorkerVoiceProfile.status == "ready",
            )
            .first()
        )
        voice_ready = vp is not None

    checks = {
        "personality": bool(twin.personality_prompt and len(twin.personality_prompt) > 30),
        "skills": bool(twin.skills and len(twin.skills) > 0),
        "knowledge_volume": n_docs >= 5,
        "decision_rules": n_decisions >= 3,
        "corrections": n_corrections >= 1,
        "tasks_done": n_tasks_done >= 1,
        "snapshots": n_snapshots >= 1,
        "linked_worker": linked is not None,
        "meetings_attended": n_meetings >= 1,
        "voice_profile": voice_ready,
    }

    total_weight = sum(w for _, _, w in _COMPONENTS)
    got = sum(w for key, _, w in _COMPONENTS if checks.get(key))
    score = round(got / total_weight * 100)

    component_breakdown = [
        {
            "key": key,
            "label": label,
            "weight": weight,
            "passed": bool(checks.get(key)),
        }
        for key, label, weight in _COMPONENTS
    ]
    missing = [c for c in component_breakdown if not c["passed"]]

    return {
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "twin_role": twin.role,
        "score_pct": score,
        "tier": _tier(score),
        "knowledge_docs": n_docs,
        "decision_rules": n_decisions,
        "corrections": n_corrections,
        "tasks_done": n_tasks_done,
        "snapshots": n_snapshots,
        "meetings_attended": n_meetings,
        "linked_worker": linked.name if linked else None,
        "voice_profile_ready": voice_ready,
        "components": component_breakdown,
        "missing": missing,
    }


def audit_all(db: Session) -> dict:
    twins = db.query(DigitalTwin).all()
    items = [audit_twin(db, t.id) for t in twins]
    items.sort(key=lambda x: x.get("score_pct", 0), reverse=True)
    avg = round(sum(x.get("score_pct", 0) for x in items) / max(len(items), 1))
    ready = sum(1 for x in items if x.get("score_pct", 0) >= 80)
    return {
        "total_twins": len(items),
        "average_score_pct": avg,
        "ready_for_production": ready,
        "twins": items,
    }


def _tier(pct: int) -> str:
    if pct >= 90: return "production"
    if pct >= 70: return "trained"
    if pct >= 50: return "learning"
    if pct >= 30: return "starter"
    return "empty"
