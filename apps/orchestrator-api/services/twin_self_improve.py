"""
VIP AI Platform — Twin Self-Improvement Engine
Twins teach themselves to get smarter without human intervention.

S1: Self-reflection after task completion
S2: Knowledge gap detection
S3: Correction pattern analysis
S4: Knowledge consolidation
S5: Proactive research before tasks
S6: Scheduled auto-improvement cycle
"""

import json
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID
from collections import defaultdict

from sqlalchemy.orm import Session

from db.models import (
    DigitalTwin, TwinKnowledge, TwinActivityLog, TwinTask,
)
from services import twin_service
from services.llm_client import chat_completion_sync
from services.logger import log


# ---------------------------------------------------------------------------
#  S1: Self-Reflection After Task
# ---------------------------------------------------------------------------

def self_reflect_on_task(db: Session, twin_id: UUID, task_id: UUID) -> dict:
    """
    Twin reviews its own completed task and extracts lessons learned.
    Called automatically after task execution.
    """
    twin = twin_service.get_twin(db, twin_id)
    task = db.query(TwinTask).filter(TwinTask.id == task_id).first()
    if not twin or not task or not task.result_text:
        return {"reflected": False, "reason": "No task or result to reflect on"}

    # Ask LLM to reflect
    reflection_prompt = f"""You just completed a task. Reflect on your work and extract lessons learned.

TASK: {task.title}
DESCRIPTION: {task.description or 'N/A'}
YOUR OUTPUT: {task.result_text[:500]}
REVIEW STATUS: {task.review_status or 'pending'}
{f'BOSS FEEDBACK: {task.review_comment}' if task.review_comment else ''}

Answer these questions concisely:
1. What did I do well?
2. What could I improve next time?
3. What specific rule or knowledge should I remember for similar tasks?

Format as: LESSON: [one clear takeaway]"""

    response = chat_completion_sync(
        system_prompt=f"You are {twin.name}, reflecting on your own work to improve.",
        messages=[{"role": "user", "content": reflection_prompt}],
        max_tokens=200,
        temperature=0.5,
    )

    if response and not response.startswith("[LLM"):
        # Save reflection as knowledge
        twin_service.add_knowledge(
            db, twin_id,
            title=f"Self-reflection: {task.title}",
            content=f"SELF-REFLECTION after completing '{task.title}':\n{response}",
            source_type="decision",
        )
        twin_service.log_activity(
            db, twin_id, "self_improve",
            f"Self-reflected on: {task.title}",
            {"method": "reflection", "task": task.title},
        )
        db.flush()
        return {"reflected": True, "task": task.title, "lesson": response[:200]}

    return {"reflected": False, "reason": "LLM unavailable"}


# ---------------------------------------------------------------------------
#  S2: Knowledge Gap Detection
# ---------------------------------------------------------------------------

def detect_knowledge_gaps(db: Session, twin_id: UUID) -> list[dict]:
    """
    Analyze recent activity to find topics twin was asked about but couldn't answer well.
    Looks for: LLM errors, short responses, repeated questions on same topic.
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return []

    # Get recent activities
    cutoff = datetime.utcnow() - timedelta(days=7)
    activities = (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id, TwinActivityLog.timestamp >= cutoff)
        .order_by(TwinActivityLog.timestamp.desc())
        .all()
    )

    # Find gaps: activities with errors or short responses
    gaps = []
    topic_counts = defaultdict(int)

    for a in activities:
        desc = a.description or ""

        # Detect failed responses
        if "LLM Error" in desc or "LLM Connection" in desc or "unavailable" in desc.lower():
            topic = desc.replace("Processing: ", "").replace("Responded to: ", "")[:60]
            gaps.append({"topic": topic, "reason": "Failed to answer", "timestamp": a.timestamp.isoformat()})

        # Track repeated topics (asked about same thing multiple times = gap)
        if a.action_type == "thinking":
            topic_key = " ".join(desc.lower().split()[:5])
            topic_counts[topic_key] += 1

    # Topics asked 3+ times = knowledge gap
    for topic, count in topic_counts.items():
        if count >= 3:
            gaps.append({"topic": topic, "reason": f"Asked {count} times — needs deeper knowledge", "count": count})

    return gaps


def fill_knowledge_gap(db: Session, twin_id: UUID, topic: str) -> dict:
    """
    Twin researches a knowledge gap using LLM and saves what it learns.
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return {"filled": False}

    research_prompt = f"""You need to learn about this topic for your job as {twin.role}:

TOPIC: {topic}

Research this topic and provide:
1. Key concepts and definitions
2. Important rules or thresholds
3. How this applies to your daily work
4. Common mistakes to avoid

Be specific and practical. Focus on what a {twin.role} needs to know."""

    response = chat_completion_sync(
        system_prompt=f"You are {twin.name}, a {twin.role}. You are studying to improve yourself.",
        messages=[{"role": "user", "content": research_prompt}],
        max_tokens=400,
        temperature=0.5,
    )

    if response and not response.startswith("[LLM"):
        twin_service.add_knowledge(
            db, twin_id,
            title=f"Self-study: {topic[:60]}",
            content=f"SELF-STUDIED TOPIC:\n{response}",
            source_type="instruction",
        )
        twin_service.log_activity(
            db, twin_id, "self_improve",
            f"Self-studied: {topic[:60]}",
            {"method": "gap_fill", "topic": topic},
        )
        db.flush()
        return {"filled": True, "topic": topic, "knowledge": response[:200]}

    return {"filled": False, "reason": "LLM unavailable"}


# ---------------------------------------------------------------------------
#  S3: Correction Pattern Analysis
# ---------------------------------------------------------------------------

def analyze_correction_patterns(db: Session, twin_id: UUID) -> dict:
    """
    Review ALL past corrections and find patterns.
    If same type of mistake happens multiple times → create a permanent rule.
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return {"analyzed": False}

    # Get all correction-type knowledge
    corrections = (
        db.query(TwinKnowledge)
        .filter(TwinKnowledge.twin_id == twin_id, TwinKnowledge.source_type == "decision")
        .filter(TwinKnowledge.title.ilike("%correction%"))
        .all()
    )

    if len(corrections) < 2:
        return {"analyzed": False, "reason": "Not enough corrections to analyze (need 2+)"}

    # Ask LLM to find patterns
    correction_texts = "\n".join([f"- {c.title}: {c.content[:150]}" for c in corrections[:10]])

    analysis_prompt = f"""Analyze these corrections I received and find patterns:

{correction_texts}

Find:
1. Are there repeated mistakes? What's the common theme?
2. What ONE clear rule would prevent ALL these mistakes?

Format: RULE: [one clear rule to follow always]"""

    response = chat_completion_sync(
        system_prompt=f"You are {twin.name}, analyzing your own mistakes to create rules.",
        messages=[{"role": "user", "content": analysis_prompt}],
        max_tokens=200,
        temperature=0.3,
    )

    if response and not response.startswith("[LLM"):
        # Check if we already have this pattern rule
        existing_patterns = (
            db.query(TwinKnowledge)
            .filter(TwinKnowledge.twin_id == twin_id, TwinKnowledge.title.ilike("%pattern rule%"))
            .count()
        )

        twin_service.add_knowledge(
            db, twin_id,
            title=f"Pattern rule #{existing_patterns + 1} (self-discovered)",
            content=f"SELF-DISCOVERED RULE from analyzing {len(corrections)} corrections:\n{response}",
            source_type="decision",
        )
        twin_service.log_activity(
            db, twin_id, "self_improve",
            f"Discovered pattern from {len(corrections)} corrections",
            {"method": "pattern_analysis", "corrections_analyzed": len(corrections)},
        )
        db.flush()
        return {"analyzed": True, "corrections_reviewed": len(corrections), "rule": response[:200]}

    return {"analyzed": False, "reason": "LLM unavailable"}


# ---------------------------------------------------------------------------
#  S4: Knowledge Consolidation
# ---------------------------------------------------------------------------

def consolidate_knowledge(db: Session, twin_id: UUID) -> dict:
    """
    Take scattered Q&A pairs and knowledge items on similar topics
    and merge them into organized guides.
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return {"consolidated": False}

    knowledge = twin_service.get_knowledge(db, twin_id)
    if len(knowledge) < 5:
        return {"consolidated": False, "reason": "Not enough knowledge to consolidate (need 5+)"}

    # Group knowledge by rough topic (first 3 words of title)
    groups = defaultdict(list)
    for k in knowledge:
        # Skip already consolidated items
        if "consolidated" in (k.title or "").lower() or "self-study" in (k.title or "").lower():
            continue
        topic_key = " ".join((k.title or "").lower().split()[:3])
        groups[topic_key].append(k)

    # Find groups with 3+ items → consolidate
    consolidated_count = 0
    for topic_key, items in groups.items():
        if len(items) < 3:
            continue

        # Ask LLM to consolidate
        item_texts = "\n".join([f"- {k.title}: {k.content[:100]}" for k in items[:8]])

        consolidate_prompt = f"""I have {len(items)} scattered knowledge items on a similar topic. Consolidate them into ONE organized guide.

Items:
{item_texts}

Create a clear, organized guide that covers all the key points from these items. Use bullet points and clear headings."""

        response = chat_completion_sync(
            system_prompt=f"You are {twin.name}, organizing your knowledge into a clear guide.",
            messages=[{"role": "user", "content": consolidate_prompt}],
            max_tokens=500,
            temperature=0.3,
        )

        if response and not response.startswith("[LLM"):
            twin_service.add_knowledge(
                db, twin_id,
                title=f"Consolidated guide: {topic_key[:40]}",
                content=f"CONSOLIDATED GUIDE (merged from {len(items)} items):\n{response}",
                source_type="document",
            )
            consolidated_count += 1

            twin_service.log_activity(
                db, twin_id, "self_improve",
                f"Consolidated {len(items)} items into guide: {topic_key[:40]}",
                {"method": "consolidation", "items_merged": len(items)},
            )

        if consolidated_count >= 3:
            break  # Max 3 consolidations per cycle

    db.flush()
    return {"consolidated": consolidated_count > 0, "guides_created": consolidated_count}


# ---------------------------------------------------------------------------
#  S5: Proactive Research Before Tasks
# ---------------------------------------------------------------------------

def research_before_task(db: Session, twin_id: UUID, task_id: UUID) -> dict:
    """
    Before starting a task, twin checks if it has enough knowledge.
    If not, it researches the topic first.
    """
    twin = twin_service.get_twin(db, twin_id)
    task = db.query(TwinTask).filter(TwinTask.id == task_id).first()
    if not twin or not task:
        return {"researched": False}

    # Check existing knowledge for task-related topics
    knowledge = twin_service.get_knowledge(db, twin_id)
    task_words = set((task.title + " " + (task.description or "")).lower().split())
    task_words = {w for w in task_words if len(w) > 3}

    # Count knowledge overlap
    overlap = 0
    for k in knowledge:
        k_words = set((k.title + " " + k.content[:200]).lower().split())
        overlap += len(task_words & k_words)

    # If low overlap → research first
    if overlap >= 5:
        return {"researched": False, "reason": "Already have enough knowledge for this task"}

    research_prompt = f"""You are about to work on this task:

TASK: {task.title}
DESCRIPTION: {task.description or 'N/A'}

Before starting, research what you need to know. Provide:
1. Key concepts needed for this task
2. Important steps or procedures
3. Common pitfalls to avoid

Be specific to the task."""

    response = chat_completion_sync(
        system_prompt=f"You are {twin.name}, a {twin.role}. You are preparing for a task by studying.",
        messages=[{"role": "user", "content": research_prompt}],
        max_tokens=300,
        temperature=0.5,
    )

    if response and not response.startswith("[LLM"):
        twin_service.add_knowledge(
            db, twin_id,
            title=f"Task prep: {task.title[:50]}",
            content=f"PRE-TASK RESEARCH for '{task.title}':\n{response}",
            source_type="instruction",
        )
        twin_service.log_activity(
            db, twin_id, "self_improve",
            f"Researched before task: {task.title[:50]}",
            {"method": "proactive_research", "task": task.title},
        )
        db.flush()
        return {"researched": True, "task": task.title, "knowledge": response[:200]}

    return {"researched": False, "reason": "LLM unavailable"}


# ---------------------------------------------------------------------------
#  S6: Full Self-Improvement Cycle
# ---------------------------------------------------------------------------

def run_self_improvement_cycle(db: Session, twin_id: UUID) -> dict:
    """
    Run the full self-improvement cycle for a twin.
    Called by scheduler or manually.
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return {"error": "Twin not found"}

    results = {
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "timestamp": datetime.utcnow().isoformat(),
        "improvements": [],
    }

    twin_service.log_activity(
        db, twin_id, "self_improve",
        "Starting self-improvement cycle...",
        {"method": "cycle_start"},
    )

    # S1: Reflect on recent completed tasks (last 24 hours)
    cutoff = datetime.utcnow() - timedelta(hours=24)
    recent_tasks = (
        db.query(TwinTask)
        .filter(
            TwinTask.twin_id == twin_id,
            TwinTask.status.in_(["done", "review"]),
            TwinTask.completed_at >= cutoff,
        )
        .all()
    )
    for task in recent_tasks[:3]:  # Max 3 reflections per cycle
        reflection = self_reflect_on_task(db, twin_id, task.id)
        if reflection.get("reflected"):
            results["improvements"].append({
                "type": "reflection",
                "detail": f"Reflected on: {task.title}",
            })

    # S2: Detect and fill knowledge gaps
    gaps = detect_knowledge_gaps(db, twin_id)
    for gap in gaps[:2]:  # Max 2 gap fills per cycle
        filled = fill_knowledge_gap(db, twin_id, gap["topic"])
        if filled.get("filled"):
            results["improvements"].append({
                "type": "gap_filled",
                "detail": f"Studied: {gap['topic'][:50]}",
            })

    # S3: Analyze correction patterns
    pattern_result = analyze_correction_patterns(db, twin_id)
    if pattern_result.get("analyzed"):
        results["improvements"].append({
            "type": "pattern_rule",
            "detail": f"Created rule from {pattern_result['corrections_reviewed']} corrections",
        })

    # S4: Consolidate knowledge
    consolidation = consolidate_knowledge(db, twin_id)
    if consolidation.get("consolidated"):
        results["improvements"].append({
            "type": "consolidation",
            "detail": f"Created {consolidation['guides_created']} organized guides",
        })

    # Log completion
    improvement_count = len(results["improvements"])
    twin_service.log_activity(
        db, twin_id, "self_improve",
        f"Self-improvement cycle complete: {improvement_count} improvements made",
        {"method": "cycle_complete", "improvements": improvement_count, "details": results["improvements"]},
    )

    db.flush()
    results["total_improvements"] = improvement_count
    return results


def run_all_twins_improvement(db: Session) -> list[dict]:
    """Run self-improvement for ALL twins. Called by scheduler."""
    twins = twin_service.list_twins(db)
    results = []
    for twin in twins:
        result = run_self_improvement_cycle(db, twin.id)
        results.append(result)
        db.flush()
    return results
