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

    # Auto-detected specialties from knowledge content
    specialties = detect_specialties(db, twin_id, top_n=5)

    # Meaningful readiness label (replaces just %)
    readiness = readiness_label(intelligence_pct, breakdown)

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
        "specialties": specialties,
        "readiness": readiness,
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


# ---------------------------------------------------------------------------
#  Auto-Detected Specialties (from knowledge content)
# ---------------------------------------------------------------------------

# Topic keywords — counts mentions in titles + content of all knowledge entries
_TOPIC_KEYWORDS = {
    "Python":         ["python", "fastapi", "django", "flask", "pip", "uvicorn", "pydantic", "sqlalchemy", "asyncio"],
    "JavaScript/TS":  ["typescript", " javascript", "node.js", "node ", "npm", " react", "next.js", "nextjs", "tailwind", "tsx", "jsx"],
    "AI/LLM":         ["llm", "openai", "anthropic", "claude", "gpt", "prompt", "embedding", "rag", "vector", "ollama", "fine-tun"],
    "Database":       ["postgres", "postgresql", "redis", "sql", "pgvector", "supabase", "migration", "schema", "query"],
    "DevOps":         ["docker", "kubernetes", "k8s", "compose", "ci/cd", "github actions", "deploy", "vercel", "render", "tailscale", "nginx"],
    "Architecture":   ["microservice", "orchestrat", "agent", "twin", "a2a", "monolith", "design pattern", "architecture", "scalab"],
    "API Design":     ["rest", "graphql", "endpoint", "router", "webhook", " api ", "swagger", "openapi"],
    "Frontend":       ["frontend", "ui ", "ux ", "css", "component", "dashboard", "page.tsx"],
    "Reports/Data":   ["report", "summary", "analytics", "dashboard", "metric", "chart", "graph"],
    "Project Mgmt":   ["sprint", "task", "kanban", "roadmap", "deadline", "priority", "milestone"],
    "Real Estate":    ["real estate", "realty", "property", "apartment", "construction", "robot", "vip", "client"],
    "Finance/Stock":  ["stock", "finance", "asset", "portfolio", "investment", "market", "trading"],
}


def detect_specialties(db: Session, twin_id: UUID, top_n: int = 5) -> list[dict]:
    """
    Auto-detect twin's top specialties by scanning knowledge content.
    Returns top-N topics ranked by mention count, with percentage shares.
    """
    knowledge = db.query(TwinKnowledge).filter(TwinKnowledge.twin_id == twin_id).all()
    if not knowledge:
        return []

    scores = defaultdict(int)
    for k in knowledge:
        text = ((k.title or "") + " " + (k.content or "")).lower()
        for topic, keywords in _TOPIC_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    # Count weight: documents matter more than chat Q&A
                    weight = 2 if k.source_type in ("document", "decision", "instruction") else 1
                    scores[topic] += weight
                    break  # one keyword match per (doc, topic) is enough

    if not scores:
        return []

    total = sum(scores.values())
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return [
        {"topic": topic, "score": score, "share_pct": round(score / total * 100)}
        for topic, score in ranked
    ]


# ---------------------------------------------------------------------------
#  Readiness Label (replaces meaningless % score)
# ---------------------------------------------------------------------------

def readiness_label(intelligence_pct: int, breakdown: dict) -> dict:
    """
    Convert numerical % into a meaningful readiness label + description.
    Considers actual task completion, not just knowledge volume.
    """
    docs = breakdown.get("documents", 0)
    rules = breakdown.get("decision_rules", 0)
    tasks_done = breakdown.get("tasks_completed", 0)
    corrections = breakdown.get("corrections", 0)

    # Tier rules — based on real evidence of capability, not just docs
    if tasks_done >= 20 and corrections >= 5 and rules >= 30:
        tier, label, color = 5, "Production Ready", "#10b981"
        desc = "Twin has completed many tasks correctly with proven patterns."
    elif tasks_done >= 5 and rules >= 20:
        tier, label, color = 4, "Trained", "#3b82f6"
        desc = "Twin can handle real tasks. Keep reviewing its work."
    elif rules >= 10 or docs >= 30:
        tier, label, color = 3, "Learning Fast", "#8b5cf6"
        desc = "Twin has solid knowledge. Try assigning real tasks."
    elif rules >= 3 or docs >= 10:
        tier, label, color = 2, "Beginner", "#f59e0b"
        desc = "Twin is learning. Add more decision rules to improve judgment."
    else:
        tier, label, color = 1, "Just Started", "#94a3b8"
        desc = "Upload documents and add rules to train your Twin."

    return {
        "tier": tier,
        "label": label,
        "color": color,
        "description": desc,
        "intelligence_pct": intelligence_pct,
    }
