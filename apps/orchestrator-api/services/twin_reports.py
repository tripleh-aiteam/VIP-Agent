"""
VIP AI Platform — Twin Report System
Generates various reports for workers and boss.

R1: Morning Twin Report (twin → worker, 9 AM)
R2: Weekly Team Update (boss → all workers)
R3: Evening Handoff data (worker → twin, 6 PM)
R4: Boss Daily Briefing (system → boss, 8 AM)
R5: Monthly Twin Comparison
R6: Task Completion Notification
R7: Boss Message Broadcast
R8: Twin Weekly Self-Report
R9: Worker Absence Auto-Report
"""

from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID
from collections import defaultdict

from sqlalchemy.orm import Session

from db.models import (
    DigitalTwin, TwinKnowledge, TwinActivityLog, TwinTask,
    TwinHandoff, Meeting, MeetingParticipant, DirectMessage,
)
from services import twin_service


# ---------------------------------------------------------------------------
#  R1: Morning Twin Report
# ---------------------------------------------------------------------------

def generate_morning_report(db: Session, twin_id: UUID) -> dict:
    """
    Generate morning report for a worker.
    Shows: what twin did overnight, tasks for today, twin progress.
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return {"error": "Twin not found"}

    cutoff = datetime.utcnow() - timedelta(hours=48)  # Last 48 hours (wider window)

    # 1. Tasks completed recently
    completed_tasks = (
        db.query(TwinTask)
        .filter(TwinTask.twin_id == twin_id, TwinTask.completed_at >= cutoff)
        .filter(TwinTask.status.in_(["done", "review"]))
        .order_by(TwinTask.completed_at.desc())
        .all()
    )

    # Also include ALL tasks still in "review" status (regardless of time)
    review_pending = (
        db.query(TwinTask)
        .filter(TwinTask.twin_id == twin_id, TwinTask.status == "review", TwinTask.needs_review == True)
        .all()
    )
    # Merge without duplicates
    seen_ids = {t.id for t in completed_tasks}
    for t in review_pending:
        if t.id not in seen_ids:
            completed_tasks.append(t)

    completed = [
        {"title": t.title, "status": t.status, "result_preview": (t.result_text or "")[:150]}
        for t in completed_tasks
    ]

    # 2. Items needing review
    review_items = [t for t in completed_tasks if t.status == "review"]
    needs_review = [
        {"title": t.title, "result_preview": (t.result_text or "")[:150]}
        for t in review_items
    ]

    # 3. Today's tasks (todo + in_progress)
    today_tasks = (
        db.query(TwinTask)
        .filter(TwinTask.twin_id == twin_id, TwinTask.status.in_(["todo", "in_progress"]))
        .order_by(
            TwinTask.priority.desc(),
            TwinTask.created_at.asc(),
        )
        .all()
    )
    todays_todo = [
        {
            "title": t.title,
            "priority": t.priority,
            "status": t.status,
            "deadline": t.deadline.isoformat() if t.deadline else None,
            "assigned_by": t.assigned_by or "Boss",
        }
        for t in today_tasks
    ]

    # 4. Messages from boss (unread)
    unread_messages = (
        db.query(DirectMessage)
        .filter(DirectMessage.twin_id == twin_id, DirectMessage.sender_type == "boss", DirectMessage.is_read == False)
        .order_by(DirectMessage.created_at.desc())
        .all()
    )
    boss_messages = [
        {"content": m.content[:150], "time": m.created_at.isoformat() if m.created_at else None}
        for m in unread_messages
    ]

    # 5. Twin self-improvement stats
    self_improve = (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id, TwinActivityLog.action_type == "self_improve", TwinActivityLog.timestamp >= cutoff)
        .all()
    )
    improvements = [
        {"description": a.description, "method": (a.metadata_json or {}).get("method", "")}
        for a in self_improve
        if (a.metadata_json or {}).get("method") not in ("cycle_start",)
    ]

    # 6. Knowledge growth
    knowledge_count = db.query(TwinKnowledge).filter(TwinKnowledge.twin_id == twin_id).count()
    new_knowledge = (
        db.query(TwinKnowledge)
        .filter(TwinKnowledge.twin_id == twin_id, TwinKnowledge.created_at >= cutoff)
        .count()
    )

    # 7. Today's meetings
    from datetime import timezone
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0)
    today_end = today_start + timedelta(days=1)
    meetings = (
        db.query(Meeting)
        .join(MeetingParticipant, MeetingParticipant.meeting_id == Meeting.id)
        .filter(MeetingParticipant.twin_id == twin_id)
        .filter(Meeting.scheduled_at >= today_start, Meeting.scheduled_at < today_end)
        .all()
    )
    todays_meetings = [
        {"title": m.title, "time": m.scheduled_at.isoformat() if m.scheduled_at else None, "type": m.meeting_type}
        for m in meetings
    ]

    # Build intelligence %
    from services.twin_intelligence import get_twin_intelligence
    intel = get_twin_intelligence(db, twin_id)

    return {
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "twin_role": twin.role,
        "generated_at": datetime.utcnow().isoformat(),
        "overnight": {
            "tasks_completed": completed,
            "completed_count": len(completed),
        },
        "needs_review": {
            "items": needs_review,
            "count": len(needs_review),
        },
        "today": {
            "tasks": todays_todo,
            "task_count": len(todays_todo),
            "meetings": todays_meetings,
            "meeting_count": len(todays_meetings),
        },
        "boss_messages": {
            "messages": boss_messages,
            "unread_count": len(boss_messages),
        },
        "self_improvement": {
            "improvements": improvements,
            "count": len(improvements),
        },
        "knowledge": {
            "total": knowledge_count,
            "new_overnight": new_knowledge,
        },
        "intelligence_pct": intel.get("intelligence_pct", 0) if intel else 0,
    }


# ---------------------------------------------------------------------------
#  R2: Weekly Team Update (Boss → All Workers)
# ---------------------------------------------------------------------------

def generate_weekly_update(db: Session, boss_message: str = "") -> dict:
    """
    Generate weekly performance report for all twins.
    Boss can add a personal message.
    """
    from services.twin_intelligence import get_all_twins_intelligence

    twins = db.query(DigitalTwin).all()
    cutoff = datetime.utcnow() - timedelta(days=7)

    # Get intelligence data
    intel_data = get_all_twins_intelligence(db)

    # Per-twin weekly stats
    twin_stats = []
    for twin in twins:
        tasks = (
            db.query(TwinTask)
            .filter(TwinTask.twin_id == twin.id, TwinTask.completed_at >= cutoff)
            .all()
        )
        tasks_done = sum(1 for t in tasks if t.status == "done")
        tasks_rejected = sum(1 for t in tasks if t.review_status == "rejected")
        tasks_total = len(tasks)
        approval_rate = round(tasks_done / max(tasks_total, 1) * 100)

        # Knowledge growth this week
        new_knowledge = (
            db.query(TwinKnowledge)
            .filter(TwinKnowledge.twin_id == twin.id, TwinKnowledge.created_at >= cutoff)
            .count()
        )

        # Self-improvements this week
        self_improve_count = (
            db.query(TwinActivityLog)
            .filter(
                TwinActivityLog.twin_id == twin.id,
                TwinActivityLog.action_type == "self_improve",
                TwinActivityLog.timestamp >= cutoff,
            )
            .count()
        )

        # Find intel for this twin
        twin_intel = next((i for i in intel_data if i["twin_id"] == str(twin.id)), {})

        twin_stats.append({
            "twin_id": str(twin.id),
            "name": twin.name,
            "role": twin.role,
            "department": twin.department,
            "tasks_done": tasks_done,
            "tasks_rejected": tasks_rejected,
            "tasks_total": tasks_total,
            "approval_rate": approval_rate,
            "new_knowledge": new_knowledge,
            "self_improvements": self_improve_count,
            "intelligence_pct": twin_intel.get("intelligence_pct", 0),
        })

    # Sort by tasks done (top performers first)
    twin_stats.sort(key=lambda x: x["tasks_done"], reverse=True)

    # Top performers (top 3)
    top_performers = twin_stats[:3] if len(twin_stats) >= 3 else twin_stats

    # Needs improvement (lowest approval rate or least tasks)
    needs_improvement = [t for t in twin_stats if t["tasks_done"] == 0 or t["approval_rate"] < 50]

    # Company totals
    total_tasks = sum(t["tasks_done"] for t in twin_stats)
    total_knowledge = sum(t["new_knowledge"] for t in twin_stats)
    avg_progress = round(sum(t["intelligence_pct"] for t in twin_stats) / max(len(twin_stats), 1))

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "week_start": cutoff.strftime("%Y-%m-%d"),
        "week_end": datetime.utcnow().strftime("%Y-%m-%d"),
        "boss_message": boss_message,
        "company_stats": {
            "total_tasks_completed": total_tasks,
            "total_new_knowledge": total_knowledge,
            "average_progress": avg_progress,
            "total_twins": len(twin_stats),
        },
        "top_performers": top_performers,
        "needs_improvement": needs_improvement,
        "all_twins": twin_stats,
    }


# ---------------------------------------------------------------------------
#  R3: Evening Handoff (Worker → Twin)
# ---------------------------------------------------------------------------

def get_evening_handoff_data(db: Session, twin_id: UUID) -> dict:
    """
    Get data for evening handoff page.
    Shows: today's summary, unfinished tasks (worker selects which to continue).
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return {"error": "Twin not found"}

    # Today's summary
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0)

    tasks_today = (
        db.query(TwinTask)
        .filter(TwinTask.twin_id == twin_id)
        .all()
    )

    completed_today = [t for t in tasks_today if t.status == "done" and t.completed_at and t.completed_at >= today_start]
    in_progress = [t for t in tasks_today if t.status == "in_progress"]
    todo = [t for t in tasks_today if t.status == "todo"]
    unfinished = in_progress + todo

    # Messages exchanged today
    from db.models import DirectMessage
    messages_today = (
        db.query(DirectMessage)
        .filter(DirectMessage.twin_id == twin_id, DirectMessage.created_at >= today_start)
        .count()
    )

    return {
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "generated_at": datetime.utcnow().isoformat(),
        "today_summary": {
            "tasks_completed": len(completed_today),
            "tasks_total": len(tasks_today),
            "messages_exchanged": messages_today,
        },
        "completed": [
            {"id": str(t.id), "title": t.title, "priority": t.priority}
            for t in completed_today
        ],
        "unfinished": [
            {
                "id": str(t.id),
                "title": t.title,
                "priority": t.priority,
                "status": t.status,
                "description": t.description,
                "progress": "50%" if t.status == "in_progress" else "0%",
            }
            for t in unfinished
        ],
    }


# ---------------------------------------------------------------------------
#  R4: Boss Daily Briefing
# ---------------------------------------------------------------------------

def generate_boss_briefing(db: Session) -> dict:
    """
    Generate daily briefing for boss — summary of ALL twins overnight.
    Shows: total work done, alerts, items needing review, today's schedule.
    """
    twins = db.query(DigitalTwin).all()
    cutoff = datetime.utcnow() - timedelta(hours=15)

    twin_summaries = []
    total_completed = 0
    total_review = 0
    total_failed = 0
    alerts = []

    for twin in twins:
        # Tasks completed overnight
        completed = (
            db.query(TwinTask)
            .filter(TwinTask.twin_id == twin.id, TwinTask.completed_at >= cutoff, TwinTask.status.in_(["done", "review"]))
            .all()
        )
        done_count = sum(1 for t in completed if t.status == "done")
        review_count = sum(1 for t in completed if t.status == "review")
        total_completed += done_count
        total_review += review_count

        # Failed tasks
        failed = (
            db.query(TwinTask)
            .filter(TwinTask.twin_id == twin.id, TwinTask.review_status == "rejected", TwinTask.completed_at >= cutoff)
            .count()
        )
        total_failed += failed

        # Self-improvements
        improvements = (
            db.query(TwinActivityLog)
            .filter(TwinActivityLog.twin_id == twin.id, TwinActivityLog.action_type == "self_improve", TwinActivityLog.timestamp >= cutoff)
            .count()
        )

        # Alerts
        if failed > 0:
            alerts.append({"twin": twin.name, "type": "failed_tasks", "message": f"{twin.name} had {failed} rejected tasks"})

        # Worker messages waiting
        unread = (
            db.query(DirectMessage)
            .filter(DirectMessage.twin_id == twin.id, DirectMessage.sender_type == "worker", DirectMessage.is_read == False)
            .count()
        )
        if unread > 0:
            alerts.append({"twin": twin.name, "type": "unread_reply", "message": f"{twin.name}'s worker sent {unread} unread replies"})

        twin_summaries.append({
            "twin_id": str(twin.id),
            "name": twin.name,
            "role": twin.role,
            "mode": twin.mode,
            "status": twin.status,
            "tasks_done": done_count,
            "tasks_review": review_count,
            "tasks_failed": failed,
            "self_improvements": improvements,
        })

    # Sort by tasks done
    twin_summaries.sort(key=lambda x: x["tasks_done"], reverse=True)

    # Today's meetings
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0)
    today_end = today_start + timedelta(days=1)
    meetings = (
        db.query(Meeting)
        .filter(Meeting.scheduled_at >= today_start, Meeting.scheduled_at < today_end)
        .all()
    )
    todays_meetings = [
        {"title": m.title, "time": m.scheduled_at.isoformat() if m.scheduled_at else None, "type": m.meeting_type}
        for m in meetings
    ]

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "summary": {
            "total_twins": len(twins),
            "twins_worked": sum(1 for t in twin_summaries if t["tasks_done"] > 0 or t["tasks_review"] > 0),
            "total_completed": total_completed,
            "total_review": total_review,
            "total_failed": total_failed,
        },
        "alerts": alerts,
        "twins": twin_summaries,
        "meetings": todays_meetings,
    }


# ---------------------------------------------------------------------------
#  R9: Worker Absence Auto-Report
# ---------------------------------------------------------------------------

def check_worker_absences(db: Session, hours_threshold: int = 24) -> list[dict]:
    """
    Check which workers haven't logged in for X hours.
    Returns list of absent workers with their twin's auto-mode status.
    """
    from db.models import PlatformUser

    cutoff = datetime.utcnow() - timedelta(hours=hours_threshold)

    # Get all workers with twins
    workers = (
        db.query(PlatformUser)
        .filter(PlatformUser.role == "worker", PlatformUser.has_twin == True)
        .all()
    )

    absent = []
    for worker in workers:
        # Check if absent (never logged in or last login before cutoff)
        is_absent = not worker.last_login_at or worker.last_login_at < cutoff

        if not is_absent:
            continue

        # Get twin info
        twin = None
        if worker.twin_id:
            twin = db.query(DigitalTwin).filter(DigitalTwin.id == worker.twin_id).first()

        # Get twin's recent activity
        recent_tasks = 0
        if twin:
            recent_tasks = (
                db.query(TwinTask)
                .filter(TwinTask.twin_id == twin.id, TwinTask.completed_at >= cutoff)
                .count()
            )

        days_absent = 0
        if worker.last_login_at:
            days_absent = (datetime.utcnow() - worker.last_login_at).days
        else:
            days_absent = 999  # Never logged in

        absent.append({
            "worker_id": str(worker.id),
            "worker_name": worker.name,
            "worker_email": worker.email,
            "department": getattr(worker, "department", None),
            "last_login": worker.last_login_at.isoformat() if worker.last_login_at else "Never",
            "days_absent": days_absent,
            "twin_name": twin.name if twin else "No twin",
            "twin_mode": twin.mode if twin else "N/A",
            "twin_status": twin.status if twin else "N/A",
            "twin_tasks_done_while_absent": recent_tasks,
        })

    # Sort by most absent first
    absent.sort(key=lambda x: x["days_absent"], reverse=True)

    return absent


# ---------------------------------------------------------------------------
#  R8: Twin Weekly Self-Report
# ---------------------------------------------------------------------------

def generate_weekly_self_report(db: Session, twin_id: UUID) -> dict:
    """
    Twin writes its own weekly summary to the worker.
    "Here's what I did this week, what I learned, where I improved."
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return {"error": "Twin not found"}

    cutoff = datetime.utcnow() - timedelta(days=7)

    # Tasks this week
    tasks = (
        db.query(TwinTask)
        .filter(TwinTask.twin_id == twin_id, TwinTask.created_at >= cutoff)
        .all()
    )
    tasks_done = [t for t in tasks if t.status == "done"]
    tasks_review = [t for t in tasks if t.status == "review"]
    tasks_rejected = [t for t in tasks if t.review_status == "rejected"]

    # Knowledge growth
    knowledge_start = (
        db.query(TwinKnowledge)
        .filter(TwinKnowledge.twin_id == twin_id, TwinKnowledge.created_at < cutoff)
        .count()
    )
    knowledge_now = db.query(TwinKnowledge).filter(TwinKnowledge.twin_id == twin_id).count()
    knowledge_added = knowledge_now - knowledge_start

    # Knowledge by type this week
    new_knowledge = (
        db.query(TwinKnowledge)
        .filter(TwinKnowledge.twin_id == twin_id, TwinKnowledge.created_at >= cutoff)
        .all()
    )
    knowledge_by_type = defaultdict(int)
    for k in new_knowledge:
        knowledge_by_type[k.source_type] += 1

    # Self-improvements
    self_improve = (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id, TwinActivityLog.action_type == "self_improve", TwinActivityLog.timestamp >= cutoff)
        .all()
    )
    improve_details = [
        a.description for a in self_improve
        if (a.metadata_json or {}).get("method") not in ("cycle_start", "cycle_complete")
    ]

    # Chat interactions
    chat_count = (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id, TwinActivityLog.action_type.in_(["thinking", "responding"]), TwinActivityLog.timestamp >= cutoff)
        .count()
    )

    # Intelligence progress
    from services.twin_intelligence import get_twin_intelligence, get_learning_timeline
    intel = get_twin_intelligence(db, twin_id)
    timeline = get_learning_timeline(db, twin_id, days=7)
    week_start_score = timeline[0]["intelligence_pct"] if timeline else 0
    week_end_score = timeline[-1]["intelligence_pct"] if timeline else 0
    score_change = week_end_score - week_start_score

    # Strongest area
    breakdown = intel.get("breakdown", {}) if intel else {}
    areas = [
        ("documents", breakdown.get("documents", 0)),
        ("decision rules", breakdown.get("decision_rules", 0)),
        ("chat learning", breakdown.get("chat_learned", 0)),
        ("corrections", breakdown.get("corrections", 0)),
    ]
    strongest = max(areas, key=lambda x: x[1])
    weakest = min(areas, key=lambda x: x[1])

    return {
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "twin_role": twin.role,
        "generated_at": datetime.utcnow().isoformat(),
        "period": f"{cutoff.strftime('%b %d')} — {datetime.utcnow().strftime('%b %d')}",
        "tasks": {
            "completed": len(tasks_done),
            "pending_review": len(tasks_review),
            "rejected": len(tasks_rejected),
            "total": len(tasks),
            "completed_titles": [t.title for t in tasks_done[:5]],
        },
        "knowledge": {
            "added_this_week": knowledge_added,
            "total": knowledge_now,
            "by_type": dict(knowledge_by_type),
        },
        "self_improvement": {
            "count": len(improve_details),
            "details": improve_details[:5],
        },
        "chat_interactions": chat_count,
        "progress": {
            "current_pct": week_end_score,
            "change": score_change,
            "direction": "up" if score_change > 0 else "down" if score_change < 0 else "same",
        },
        "analysis": {
            "strongest_area": strongest[0],
            "weakest_area": weakest[0],
        },
    }


# ---------------------------------------------------------------------------
#  R5: Monthly Twin Comparison
# ---------------------------------------------------------------------------

def generate_monthly_comparison(db: Session) -> dict:
    """
    Generate monthly comparison report for all twins.
    Shows: growth over the month, rankings, best/worst performers.
    """
    from services.twin_intelligence import get_all_twins_intelligence, get_learning_timeline

    twins = db.query(DigitalTwin).all()
    cutoff = datetime.utcnow() - timedelta(days=30)

    twin_monthly = []
    for twin in twins:
        # Tasks this month
        tasks = (
            db.query(TwinTask)
            .filter(TwinTask.twin_id == twin.id, TwinTask.created_at >= cutoff)
            .all()
        )
        tasks_completed = sum(1 for t in tasks if t.status == "done")
        tasks_rejected = sum(1 for t in tasks if t.review_status == "rejected")
        tasks_total = len(tasks)

        # Knowledge this month
        knowledge_added = (
            db.query(TwinKnowledge)
            .filter(TwinKnowledge.twin_id == twin.id, TwinKnowledge.created_at >= cutoff)
            .count()
        )
        total_knowledge = db.query(TwinKnowledge).filter(TwinKnowledge.twin_id == twin.id).count()

        # Self-improvements this month
        self_improvements = (
            db.query(TwinActivityLog)
            .filter(TwinActivityLog.twin_id == twin.id, TwinActivityLog.action_type == "self_improve", TwinActivityLog.timestamp >= cutoff)
            .count()
        )

        # Chat interactions this month
        chat_count = (
            db.query(TwinActivityLog)
            .filter(TwinActivityLog.twin_id == twin.id, TwinActivityLog.action_type.in_(["thinking", "responding"]), TwinActivityLog.timestamp >= cutoff)
            .count()
        )

        # Corrections received
        corrections = (
            db.query(TwinActivityLog)
            .filter(TwinActivityLog.twin_id == twin.id, TwinActivityLog.action_type == "feedback", TwinActivityLog.timestamp >= cutoff)
            .count()
        )

        # Timeline for sparkline
        timeline = get_learning_timeline(db, twin.id, days=30)
        daily_scores = [d["day_score"] for d in timeline]

        # Growth (first week avg vs last week avg)
        first_week = sum(daily_scores[:7])
        last_week = sum(daily_scores[-7:])
        growth_trend = "up" if last_week > first_week else "down" if last_week < first_week else "flat"

        # Intelligence
        from services.twin_intelligence import get_twin_intelligence
        intel = get_twin_intelligence(db, twin.id)

        twin_monthly.append({
            "twin_id": str(twin.id),
            "name": twin.name,
            "role": twin.role,
            "department": twin.department,
            "intelligence_pct": intel.get("intelligence_pct", 0) if intel else 0,
            "tasks_completed": tasks_completed,
            "tasks_rejected": tasks_rejected,
            "tasks_total": tasks_total,
            "approval_rate": round(tasks_completed / max(tasks_total, 1) * 100),
            "knowledge_added": knowledge_added,
            "total_knowledge": total_knowledge,
            "self_improvements": self_improvements,
            "chat_interactions": chat_count,
            "corrections": corrections,
            "growth_trend": growth_trend,
            "daily_scores": daily_scores,
        })

    # Rankings
    twin_monthly.sort(key=lambda x: x["intelligence_pct"], reverse=True)

    # Best/Worst
    most_active = max(twin_monthly, key=lambda x: x["tasks_completed"]) if twin_monthly else None
    most_improved = max(twin_monthly, key=lambda x: x["knowledge_added"]) if twin_monthly else None
    needs_attention = [t for t in twin_monthly if t["tasks_completed"] == 0 and t["knowledge_added"] == 0]

    # Company averages
    avg_intelligence = round(sum(t["intelligence_pct"] for t in twin_monthly) / max(len(twin_monthly), 1))
    total_tasks = sum(t["tasks_completed"] for t in twin_monthly)
    total_knowledge = sum(t["knowledge_added"] for t in twin_monthly)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "period": f"{cutoff.strftime('%Y-%m-%d')} to {datetime.utcnow().strftime('%Y-%m-%d')}",
        "company_summary": {
            "total_twins": len(twin_monthly),
            "avg_intelligence": avg_intelligence,
            "total_tasks_completed": total_tasks,
            "total_knowledge_added": total_knowledge,
        },
        "highlights": {
            "most_active": {"name": most_active["name"], "tasks": most_active["tasks_completed"]} if most_active else None,
            "most_improved": {"name": most_improved["name"], "knowledge": most_improved["knowledge_added"]} if most_improved else None,
            "needs_attention_count": len(needs_attention),
        },
        "twins": twin_monthly,
    }


def process_evening_handoff(
    db: Session,
    twin_id: UUID,
    selected_task_ids: list[str],
    new_tasks: list[dict],
    instructions: str,
) -> dict:
    """
    Process the evening handoff from worker.
    1. Mark selected tasks for twin to continue
    2. Create new overnight tasks
    3. Save instructions as temporary knowledge
    4. Switch twin to active mode
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return {"error": "Twin not found"}

    # 1. Selected tasks → ensure they're in todo/in_progress
    continued_tasks = []
    for task_id in selected_task_ids:
        task = db.query(TwinTask).filter(TwinTask.id == task_id).first()
        if task and task.status in ("todo", "in_progress"):
            continued_tasks.append(task.title)

    # 2. Create new overnight tasks
    created_tasks = []
    for nt in new_tasks:
        if nt.get("title"):
            task = twin_service.create_task(
                db, twin_id,
                title=nt["title"],
                description=nt.get("description"),
                priority=nt.get("priority", "medium"),
                assigned_by="worker",
            )
            created_tasks.append(task.title)

    # 3. Save worker instructions as temporary knowledge
    if instructions.strip():
        twin_service.add_knowledge(
            db, twin_id,
            title=f"Tonight's instructions ({datetime.utcnow().strftime('%Y-%m-%d')})",
            content=f"WORKER'S INSTRUCTIONS FOR TONIGHT:\n{instructions}\n\nFollow these instructions carefully when working on tonight's tasks.",
            source_type="instruction",
        )

    # 4. Switch twin to active mode
    twin_service.switch_mode(db, twin_id, "active")
    twin_service.set_status(db, twin_id, "idle")

    # 5. Log handoff
    twin_service.log_activity(
        db, twin_id, "handoff",
        f"Evening handoff: {len(continued_tasks)} tasks to continue, {len(created_tasks)} new tasks",
        {
            "continued": continued_tasks,
            "new_tasks": created_tasks,
            "has_instructions": bool(instructions.strip()),
        },
    )

    db.flush()

    return {
        "success": True,
        "twin_name": twin.name,
        "twin_mode": "active",
        "tasks_to_continue": continued_tasks,
        "new_tasks_created": created_tasks,
        "instructions_saved": bool(instructions.strip()),
    }
