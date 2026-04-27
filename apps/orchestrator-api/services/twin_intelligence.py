"""
VIP AI Platform — Twin Intelligence Metrics
Calculates how smart each twin is and tracks growth over time.

Metrics:
- Knowledge score (documents, decisions, instructions, styles)
- Learning velocity (new knowledge per day)
- Chat training score (auto-learned from conversations)
- Feedback score (corrections received + approval rate)
- Task performance (tasks completed, approval rate)
- Overall intelligence % (weighted combination)
"""

from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID
from collections import defaultdict

from sqlalchemy.orm import Session
from sqlalchemy import func

from db.models import (
    DigitalTwin, TwinKnowledge, TwinActivityLog, TwinTask,
)


# ---------------------------------------------------------------------------
#  Intelligence Score Weights
# ---------------------------------------------------------------------------

WEIGHTS = {
    "knowledge_docs": 3,       # Each document = 3 points
    "knowledge_decisions": 5,  # Each decision rule = 5 points
    "knowledge_instructions": 4,
    "knowledge_styles": 4,
    "chat_learned": 2,         # Each auto-learned Q&A = 2 points
    "feedback_correction": 8,  # Each correction = 8 points (strongest learning)
    "feedback_approval": 3,    # Each approval = 3 points
    "task_completed": 4,       # Each completed task = 4 points
}

# Max score for 100% (adjustable)
MAX_SCORE = 500


# ---------------------------------------------------------------------------
#  Calculate Intelligence Score
# ---------------------------------------------------------------------------

def get_twin_intelligence(db: Session, twin_id: UUID) -> dict:
    """Calculate comprehensive intelligence metrics for a twin."""

    twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not twin:
        return {}

    # Count knowledge by type
    knowledge = db.query(TwinKnowledge).filter(TwinKnowledge.twin_id == twin_id).all()
    knowledge_by_type = defaultdict(int)
    for k in knowledge:
        knowledge_by_type[k.source_type] += 1

    # Count auto-learned from chat
    chat_learned = (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id, TwinActivityLog.action_type == "auto_learn")
        .count()
    )

    # Count feedback (corrections + approvals)
    feedback_activities = (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id, TwinActivityLog.action_type == "feedback")
        .all()
    )
    corrections = sum(1 for f in feedback_activities if "rejection" in (f.description or "").lower() or "correction" in (f.description or "").lower())
    approvals = sum(1 for f in feedback_activities if "approved" in (f.description or "").lower() or "reinforcement" in (f.description or "").lower())

    # Task stats
    tasks = db.query(TwinTask).filter(TwinTask.twin_id == twin_id).all()
    tasks_completed = sum(1 for t in tasks if t.status == "done")
    tasks_total = len(tasks)
    approval_rate = round(approvals / max(approvals + corrections, 1) * 100)

    # Calculate raw score
    raw_score = (
        knowledge_by_type.get("document", 0) * WEIGHTS["knowledge_docs"]
        + knowledge_by_type.get("decision", 0) * WEIGHTS["knowledge_decisions"]
        + knowledge_by_type.get("instruction", 0) * WEIGHTS["knowledge_instructions"]
        + knowledge_by_type.get("style", 0) * WEIGHTS["knowledge_styles"]
        + chat_learned * WEIGHTS["chat_learned"]
        + corrections * WEIGHTS["feedback_correction"]
        + approvals * WEIGHTS["feedback_approval"]
        + tasks_completed * WEIGHTS["task_completed"]
    )

    # Intelligence percentage (capped at 100)
    intelligence_pct = min(100, round(raw_score / MAX_SCORE * 100))

    # Breakdown for chart
    breakdown = {
        "documents": knowledge_by_type.get("document", 0),
        "decision_rules": knowledge_by_type.get("decision", 0),
        "instructions": knowledge_by_type.get("instruction", 0),
        "styles": knowledge_by_type.get("style", 0),
        "chat_learned": chat_learned,
        "corrections": corrections,
        "approvals": approvals,
        "tasks_completed": tasks_completed,
    }

    return {
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "twin_role": twin.role,
        "intelligence_pct": intelligence_pct,
        "raw_score": raw_score,
        "max_score": MAX_SCORE,
        "total_knowledge": len(knowledge),
        "approval_rate": approval_rate,
        "tasks_completed": tasks_completed,
        "tasks_total": tasks_total,
        "breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
#  Learning Growth Over Time (for charts)
# ---------------------------------------------------------------------------

def get_learning_timeline(db: Session, twin_id: UUID, days: int = 30) -> list[dict]:
    """Get daily learning growth for timeline chart."""

    cutoff = datetime.utcnow() - timedelta(days=days)

    # Get all knowledge items with dates
    knowledge = (
        db.query(TwinKnowledge)
        .filter(TwinKnowledge.twin_id == twin_id, TwinKnowledge.created_at >= cutoff)
        .all()
    )

    # Get all activities
    activities = (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id, TwinActivityLog.timestamp >= cutoff)
        .all()
    )

    # Group by date
    daily = defaultdict(lambda: {"knowledge": 0, "chat_learned": 0, "corrections": 0, "approvals": 0, "tasks": 0})

    for k in knowledge:
        day = k.created_at.strftime("%Y-%m-%d") if k.created_at else None
        if day:
            daily[day]["knowledge"] += 1

    for a in activities:
        day = a.timestamp.strftime("%Y-%m-%d") if a.timestamp else None
        if not day:
            continue
        if a.action_type == "auto_learn":
            daily[day]["chat_learned"] += 1
        elif a.action_type == "feedback":
            if "correction" in (a.description or "").lower() or "rejection" in (a.description or "").lower():
                daily[day]["corrections"] += 1
            elif "approved" in (a.description or "").lower():
                daily[day]["approvals"] += 1
        elif a.action_type == "task_completed":
            daily[day]["tasks"] += 1

    # Fill gaps and build timeline
    timeline = []
    cumulative_score = 0

    for i in range(days):
        date = (datetime.utcnow() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        d = daily.get(date, {"knowledge": 0, "chat_learned": 0, "corrections": 0, "approvals": 0, "tasks": 0})

        day_score = (
            d["knowledge"] * 3
            + d["chat_learned"] * 2
            + d["corrections"] * 8
            + d["approvals"] * 3
            + d["tasks"] * 4
        )
        cumulative_score += day_score

        timeline.append({
            "date": date,
            "knowledge_added": d["knowledge"],
            "chat_learned": d["chat_learned"],
            "corrections": d["corrections"],
            "approvals": d["approvals"],
            "tasks_done": d["tasks"],
            "day_score": day_score,
            "cumulative_score": cumulative_score,
            "intelligence_pct": min(100, round(cumulative_score / MAX_SCORE * 100)),
        })

    return timeline


# ---------------------------------------------------------------------------
#  All Twins Comparison (for boss dashboard)
# ---------------------------------------------------------------------------

def get_all_twins_intelligence(db: Session) -> list[dict]:
    """Get intelligence metrics for all twins — for boss comparison view."""
    twins = db.query(DigitalTwin).order_by(DigitalTwin.created_at).all()
    results = []
    for twin in twins:
        metrics = get_twin_intelligence(db, twin.id)
        if metrics:
            results.append(metrics)
    # Sort by intelligence % descending
    results.sort(key=lambda x: x["intelligence_pct"], reverse=True)
    return results
