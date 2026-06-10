"""
VIP AI Platform — Scheduler Service
Reads schedule rules from DB, runs tasks on cron, retries once on failure.
Uses APScheduler for MVP.
"""

import os
from datetime import datetime, timedelta
from uuid import UUID
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from db.base import SessionLocal
from db.models import OrchScheduleRule, OrchTaskDefinition
from services.logger import log
from services.resilience import with_retry, alert, detect_missed_runs

_scheduler: BackgroundScheduler | None = None


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

def _mark_rule_run(rule_id, status: str = "completed"):
    """Record that a schedule fired — last_run_at + status + run_count, so the
    Workflows page shows real execution history."""
    if not rule_id:
        return
    try:
        from db.models import OrchScheduleRule
        db = SessionLocal()
        try:
            rule = db.query(OrchScheduleRule).filter(OrchScheduleRule.id == rule_id).first()
            if rule:
                rule.last_run_at = datetime.utcnow()
                rule.last_run_status = status
                rule.run_count = (rule.run_count or 0) + 1
                db.commit()
        finally:
            db.close()
    except Exception as e:
        log.warning(f"scheduler: mark_rule_run failed: {e}")


def _execute_scheduled_job(rule_id: str, rule_name: str, task_type: str, target_agent_type: str, retry: bool = True):
    """Execute a scheduled job — creates a task, dispatches it. Retries once on failure."""
    from services.task_service import create_task, dispatch_task

    db = SessionLocal()
    trace_id = f"tr-sched-{rule_name}-{int(datetime.utcnow().timestamp())}"

    log.info(
        f"scheduler: firing {rule_name} ({task_type})",
        extra={"trace_id": trace_id, "action": "scheduler.fire"},
    )

    try:
        run = create_task(
            db=db,
            trace_id=trace_id,
            task_type=task_type,
            target_agent_type=target_agent_type,
            initiator_type="system_scheduler",
            initiator_id=f"schedule:{rule_id}",
            source_channel="scheduler",
            input_payload={"scheduled": True, "rule_name": rule_name},
        )

        run = dispatch_task(db, run.id)
        _mark_rule_run(rule_id, run.status or "completed")

        log.info(
            f"scheduler: {rule_name} completed -> {run.status}",
            extra={"trace_id": trace_id, "task_id": str(run.id), "action": "scheduler.completed"},
        )

    except Exception as e:
        _mark_rule_run(rule_id, "failed")
        log.warning(
            f"scheduler: {rule_name} failed: {e}",
            extra={"trace_id": trace_id, "action": "scheduler.failed"},
        )
        # Retry once
        if retry:
            log.info(f"scheduler: retrying {rule_name}", extra={"action": "scheduler.retry"})
            try:
                _execute_scheduled_job(rule_id, rule_name, task_type, target_agent_type, retry=False)
            except Exception as e2:
                log.warning(f"scheduler: retry also failed: {e2}", extra={"action": "scheduler.retry_failed"})
    finally:
        db.close()


def _execute_report_job(rule_name: str, report_type: str, hours_back: int = 24, rule_id=None):
    """Execute a scheduled report composition."""
    from services.report_service import compose_report

    db = SessionLocal()
    trace_id = f"tr-sched-report-{int(datetime.utcnow().timestamp())}"

    log.info(f"scheduler: composing {report_type} report", extra={"trace_id": trace_id, "action": "scheduler.report"})

    try:
        compose_report(db, report_type=report_type, hours_back=hours_back, trace_id=trace_id)
        _mark_rule_run(rule_id, "completed")
        log.info(f"scheduler: {report_type} report done", extra={"action": "scheduler.report.done"})
    except Exception as e:
        _mark_rule_run(rule_id, "failed")
        log.warning(f"scheduler: report failed: {e}", extra={"action": "scheduler.report.failed"})
    finally:
        db.close()


@with_retry(max_attempts=3, backoff_seconds=(30, 120, 300), job_name="auto_daily_reports")
def _auto_daily_reports():
    """
    Automatic daily report pipeline — runs at 8 AM KST (23:00 UTC previous day).
    Sends one report PER AGENT + one combined daily report.
    Each report saved to Dashboard (Reports page) AND sent to Telegram.
    Wrapped with @with_retry: 3 attempts with 30s/2min/5min backoff.
    """
    from services.task_service import create_task, dispatch_task
    from services.report_service import compose_report
    from services.telegram_service import send_alert
    from db.models import OrchReport

    AGENT_REPORTS = [
        {"task_type": "asset_summary", "agent_type": "asset", "name": "Asset Agent", "emoji": "🏢"},
        {"task_type": "stock_analysis", "agent_type": "stock", "name": "Stock Agent", "emoji": "📈"},
        {"task_type": "realty_listing_fetch", "agent_type": "realty", "name": "Real Estate Agent", "emoji": "🏠"},
    ]

    db = SessionLocal()
    base_trace = f"tr-auto-daily-{int(datetime.utcnow().timestamp())}"
    kst_now = datetime.utcnow().strftime("%Y-%m-%d") + " 08:00 KST"

    log.info("auto-report: starting daily pipeline (3 agents + combined)", extra={"trace_id": base_trace, "action": "auto_report.daily.start"})

    agent_results = []

    try:
        # Step 1: Build a meaningful, formatted report PER AGENT from its own
        # live data (Asset/Stock backends + real Realty workbook + OnBid), save
        # to the dashboard, and send to Telegram. Each builder is best-effort.
        from services.agent_report_builder import (
            build_all_reports, format_telegram, report_sections,
        )
        reports = build_all_reports(db, base_trace)
        for rep in reports:
            try:
                agent_report = OrchReport(
                    report_type=f"agent_daily_{rep['agent_type']}",
                    source_run_ids_json=[],
                    content_json={
                        "report_type": f"agent_daily_{rep['agent_type']}",
                        "executive_summary": rep.get("summary") or f"{rep['name']} daily report",
                        "sections": report_sections(rep),
                        "agent": rep["name"],
                        "status": rep["status"],
                        "report": rep,
                        "generated_at": datetime.utcnow().isoformat(),
                        "kst_time": kst_now,
                    },
                    delivery_channel="auto",
                )
                db.add(agent_report)
                db.flush()

                send_alert(format_telegram(rep, kst_now))
                agent_results.append({"agent": rep["name"], "status": rep["status"],
                                      "report_id": str(agent_report.id)})
                log.info(f"auto-report: {rep['name']} saved + sent ({rep['status']})",
                         extra={"trace_id": base_trace, "action": "auto_report.agent"})
            except Exception as e:
                agent_results.append({"agent": rep.get("name", "?"), "status": "failed", "error": str(e)})
                log.warning(f"auto-report: {rep.get('name')} send failed: {e}",
                            extra={"action": "auto_report.agent.failed"})

        # Step 2: Build the combined daily report from the 3 agent reports — it
        # carries each agent's REAL data + bilingual (EN/KO) detail, so the
        # dashboard's language toggle works (replaces the old empty composer).
        from services.agent_report_builder import build_combined_report
        combined = build_combined_report(reports, kst_now)
        combined_report = OrchReport(
            report_type="daily_summary",
            source_run_ids_json=[],
            content_json={
                "report_type": "daily_summary",
                "executive_summary": combined["executive_summary"],
                "sections": combined["sections"],
                "report": {
                    "detail_en": combined["detail_en"],
                    "detail_ko": combined["detail_ko"],
                    "summary_en": combined.get("summary_en", ""),
                    "summary_ko": combined.get("summary_ko", ""),
                },
                "generated_at": datetime.utcnow().isoformat(),
                "kst_time": kst_now,
            },
            delivery_channel="auto",
        )
        db.add(combined_report)
        db.flush()

        # Step 3: Send combined summary to Telegram
        completed = len([r for r in agent_results if r["status"] in ("ok", "partial", "completed")])
        total = len(agent_results)

        combined_lines = [
            f"📊 <b>VIP Daily Summary</b>",
            f"<i>{kst_now}</i>",
            f"",
            f"Agents: {completed}/{total} reported",
        ]
        for r in agent_results:
            icon = {"ok": "✅", "partial": "⚠️", "completed": "✅"}.get(r["status"], "❌")
            combined_lines.append(f"  {icon} {r['agent']}: {r['status']}")
        if combined.get("executive_summary"):
            combined_lines.append("\n<b>Executive Summary</b>")
            combined_lines.append(combined["executive_summary"][:400])
        combined_lines.append("\n<i>View full EN/한국어 report on dashboard → Reports</i>")

        send_alert("\n".join(combined_lines))

        db.commit()
        log.info(f"auto-report: daily pipeline completed ({completed}/{total} agents, 4 reports saved)", extra={"trace_id": base_trace, "action": "auto_report.daily.done"})

    except Exception as e:
        log.warning(f"auto-report: daily pipeline failed: {e}", extra={"trace_id": base_trace, "action": "auto_report.daily.failed"})
    finally:
        db.close()


@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="stock_market_close")
def _capture_stock_market_close():
    """Build the Stock report at Korean market close (15:30 KST) and SAVE it
    (no Telegram). The 8 AM daily pipeline then delivers this close-of-day
    snapshot to the boss. Idea: capture at close, report in the morning."""
    from services.agent_report_builder import build_stock_report, report_sections, _attach_detail
    from db.models import OrchReport

    db = SessionLocal()
    try:
        trace = f"tr-stock-close-{int(datetime.utcnow().timestamp())}"
        rep = build_stock_report(db, trace)
        _attach_detail(rep)  # bilingual 1-page detail captured at close
        kst = datetime.utcnow().strftime("%Y-%m-%d") + " 15:30 KST"
        r = OrchReport(
            report_type="agent_daily_stock",
            source_run_ids_json=[],
            content_json={
                "report_type": "agent_daily_stock",
                "executive_summary": rep.get("summary") or "Stock market close report",
                "sections": report_sections(rep),
                "agent": rep["name"],
                "status": rep["status"],
                "market_close": True,
                "report": rep,
                "generated_at": datetime.utcnow().isoformat(),
                "kst_time": kst,
            },
            delivery_channel="capture",
        )
        db.add(r)
        db.commit()
        log.info(f"stock-close: captured market-close report ({rep['status']})",
                 extra={"trace_id": trace, "action": "stock_close.capture"})
    except Exception as e:
        log.warning(f"stock-close: capture failed: {e}", extra={"action": "stock_close.failed"})
    finally:
        db.close()


@with_retry(max_attempts=3, backoff_seconds=(30, 120, 300), job_name="auto_weekly_report")
def _auto_weekly_report():
    """
    Automatic weekly report — runs every Friday 6:30 PM KST (09:30 UTC).
    Composes from last 7 days + sends to Telegram.
    """
    from services.report_service import compose_report
    from services.telegram_service import send_alert

    db = SessionLocal()
    trace_id = f"tr-auto-weekly-{int(datetime.utcnow().timestamp())}"

    log.info("auto-report: starting weekly pipeline", extra={"trace_id": trace_id, "action": "auto_report.weekly.start"})

    try:
        report = compose_report(db, report_type="weekly_summary", hours_back=168, trace_id=trace_id)

        summary = report.get("executive_summary", "Weekly report generated.")
        sections = report.get("sections", [])
        quality = report.get("quality") or {}

        telegram_lines = [
            f"📋 <b>VIP Weekly Report</b>",
            f"<i>Week ending {datetime.utcnow().strftime('%Y-%m-%d')}</i>",
            "",
            # Phase 1: AI-written executive summary at top
            f"<b>Executive Summary</b>",
            summary[:600] if summary else "(no summary)",
            "",
        ]

        for s in sections:
            if s.get("content") and "No" not in s["content"][:5]:
                telegram_lines.append(f"<b>{s['title']}</b>")
                telegram_lines.append(f"{s['content'][:150]}")
                telegram_lines.append("")

        # Phase 3: quality footer
        if quality and quality.get("grade"):
            telegram_lines.append(f"<i>Report quality: {quality['grade']} ({quality.get('score', 0)}/100)</i>")

        telegram_lines.append(f"<i>Full report on dashboard</i>")

        send_alert("\n".join(telegram_lines))

        log.info("auto-report: weekly pipeline completed", extra={"trace_id": trace_id, "action": "auto_report.weekly.done"})

    except Exception as e:
        log.warning(f"auto-report: weekly pipeline failed: {e}", extra={"trace_id": trace_id, "action": "auto_report.weekly.failed"})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scheduler management
# ---------------------------------------------------------------------------

def _execute_health_check():
    """Ping all active agents and update reliability scores."""
    from adapters import get_adapter

    db = SessionLocal()
    try:
        from db.models import CoreAgent, AgentHeartbeat
        agents = db.query(CoreAgent).filter(CoreAgent.status.in_(["active", "error"])).all()

        for agent in agents:
            if not agent.endpoint_url:
                continue

            adapter = get_adapter(
                agent_type=agent.type,
                agent_name=agent.name,
                endpoint_url=agent.endpoint_url,
                is_mock=agent.is_mock,
            )
            health = adapter.health_check()
            reachable = health.get("reachable", False)

            # Update reliability score (rolling average)
            old_score = agent.reliability_score or 1.0
            new_point = 1.0 if reachable else 0.0
            agent.reliability_score = round(old_score * 0.8 + new_point * 0.2, 3)

            # Update status
            if reachable and agent.status == "error":
                agent.status = "active"
                log.info(f"health: {agent.name} recovered", extra={"action": "health.recovered"})
            elif not reachable and agent.status == "active":
                agent.status = "error"
                log.warning(f"health: {agent.name} unreachable", extra={"action": "health.unreachable"})

            # Record heartbeat
            db.add(AgentHeartbeat(
                agent_id=agent.id,
                status="healthy" if reachable else "unhealthy",
                latency_ms=health.get("latency_ms", 0),
                metadata_json=health,
            ))

        db.commit()
        log.info(f"health check: pinged {len(agents)} agents", extra={"action": "health.completed"})
    except Exception as e:
        log.warning(f"health check failed: {e}", extra={"action": "health.failed"})
    finally:
        db.close()


# ---------------------------------------------------------------------------
#  Twin Mode Auto-Switch
# ---------------------------------------------------------------------------

def _auto_twin_mode_switch():
    """Check working hours and auto-switch twin modes.
    Working hours (9-18 KST, Mon-Fri): twins → shadow
    After hours: twins → active
    """
    from datetime import timezone, timedelta
    from db.models import DigitalTwin, TwinActivityLog

    db = SessionLocal()
    try:
        kst = timezone(timedelta(hours=9))
        now = datetime.now(kst)
        is_working = 9 <= now.hour < 18 and now.weekday() < 5

        twins = db.query(DigitalTwin).all()
        switched = 0

        for twin in twins:
            # Skip twins in meeting or that had manual handoff recently
            if twin.status == "in_meeting":
                continue

            # Check if worker did manual evening handoff (don't override)
            recent_handoff = (
                db.query(TwinActivityLog)
                .filter(TwinActivityLog.twin_id == twin.id, TwinActivityLog.action_type == "handoff")
                .filter(TwinActivityLog.timestamp >= datetime.utcnow() - timedelta(hours=12))
                .first()
            )
            if recent_handoff and twin.mode == "active":
                continue  # Worker manually handed off — don't switch back to shadow

            if is_working and twin.mode == "active":
                # Working hours — switch to shadow (real workers take over)
                twin.mode = "shadow"
                twin.updated_at = datetime.utcnow()
                switched += 1
                log.info(f"twin-mode: {twin.name} → shadow (working hours)", extra={"action": "twin.mode_auto_shadow"})

            elif not is_working and twin.mode == "shadow":
                # After hours — switch to active (twins take over)
                twin.mode = "active"
                twin.status = "idle"
                twin.updated_at = datetime.utcnow()
                switched += 1
                log.info(f"twin-mode: {twin.name} → active (after hours)", extra={"action": "twin.mode_auto_active"})

        if switched > 0:
            db.commit()
            log.info(f"twin-mode: {switched} twins switched ({'shadow' if is_working else 'active'})",
                     extra={"action": "twin.mode_batch_switch"})
    except Exception as e:
        db.rollback()
        log.error(f"twin-mode: error {e}", extra={"action": "twin.mode_error"})
    finally:
        db.close()


@with_retry(max_attempts=3, backoff_seconds=(60, 180, 600), job_name="twin_morning_handoff")
def _auto_morning_handoff():
    """Generate morning handoff reports for all twins at 9 AM KST."""
    from db.models import DigitalTwin, TwinTask, TwinHandoff, TwinActivityLog

    db = SessionLocal()
    try:
        twins = db.query(DigitalTwin).all()
        handoffs_created = 0

        for twin in twins:
            # Check if handoff already exists for today
            today = datetime.utcnow().date()
            existing = (
                db.query(TwinHandoff)
                .filter(TwinHandoff.twin_id == twin.id)
                .filter(TwinHandoff.date >= datetime(today.year, today.month, today.day))
                .first()
            )
            if existing:
                continue

            # Get tasks completed overnight (last 15 hours to catch after-hours work)
            cutoff = datetime.utcnow() - timedelta(hours=15)

            completed_tasks = (
                db.query(TwinTask)
                .filter(TwinTask.twin_id == twin.id)
                .filter(TwinTask.completed_at >= cutoff)
                .filter(TwinTask.status.in_(["done", "review"]))
                .all()
            )

            tasks_completed = [
                {"task": t.title, "status": t.status, "result": (t.result_text or "")[:200]}
                for t in completed_tasks if t.status == "done"
            ]

            tasks_pending_review = [
                {"task": t.title, "draft": (t.result_text or "")[:200]}
                for t in completed_tasks if t.status == "review"
            ]

            # Get overnight activity count
            activity_count = (
                db.query(TwinActivityLog)
                .filter(TwinActivityLog.twin_id == twin.id)
                .filter(TwinActivityLog.timestamp >= cutoff)
                .count()
            )

            # Only create handoff if there was activity
            if tasks_completed or tasks_pending_review or activity_count > 0:
                summary = f"{twin.name} ({twin.role}): {len(tasks_completed)} tasks completed, {len(tasks_pending_review)} items need review, {activity_count} total activities overnight."

                handoff = TwinHandoff(
                    twin_id=twin.id,
                    date=datetime.utcnow(),
                    tasks_completed=tasks_completed,
                    tasks_pending_review=tasks_pending_review,
                    meeting_notes=[],
                    overnight_summary=summary,
                    reviewed=False,
                )
                db.add(handoff)
                handoffs_created += 1

        if handoffs_created > 0:
            db.commit()
            log.info(f"handoff: {handoffs_created} morning handoffs generated", extra={"action": "twin.handoff_generated"})
        else:
            log.info("handoff: no overnight activity — no handoffs needed", extra={"action": "twin.handoff_skip"})

    except Exception as e:
        db.rollback()
        log.error(f"handoff: error {e}", extra={"action": "twin.handoff_error"})
    finally:
        db.close()


@with_retry(max_attempts=2, backoff_seconds=(30, 120), job_name="claude_auto_import", alert_on_final_failure=False)
def _auto_import_claude_sessions():
    """Import recent Claude Code sessions for all twins."""
    db = SessionLocal()
    try:
        from services.claude_auto_import import auto_import_all_twins
        results = auto_import_all_twins(db)
        total = sum(r.get("imported_count", 0) for r in results)
        db.commit()
        log.info(f"claude-auto: imported {total} sessions across {len(results)} twins",
                 extra={"action": "twin.claude_auto_import"})
    except Exception as e:
        db.rollback()
        log.error(f"claude-auto: error {e}", extra={"action": "twin.claude_auto_error"})
    finally:
        db.close()


@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="daily_standing_tasks")
def _auto_assign_daily_standing_tasks():
    """
    Every evening, give each twin one standing daily task scoped to their role.
    Ensures twins always have work overnight, so morning handoffs show real activity.
    """
    db = SessionLocal()
    try:
        from db.models import DigitalTwin, TwinTask
        from datetime import datetime as dt, timedelta

        # Map role/dept → standing task template
        ROLE_TASKS = {
            "Stock Analyst":      "Review today's KOSPI movements and prepare a 5-line summary highlighting top gainers, losers, and any high-risk holdings.",
            "Asset Manager":      "Run a portfolio health check: occupancy, expiring contracts, overdue payments. Flag anything needing attention tomorrow.",
            "Real Estate Manager":"Scan today's listings for changes >5%, identify high-yield opportunities, and note any vacancy spikes.",
            "Vice President":     "Compile a 1-page executive snapshot from today's reports — what worked, what's at risk, what to watch tomorrow.",
            "AI Team Lead":       "Review yesterday's twin activity, identify patterns or recurring issues, and propose 1-2 improvements.",
            "Frontend Developer": "Audit dashboard UI for any rendering issues from today's deploys, list 3 small UX wins to ship tomorrow.",
            "Backend Developer":  "Check API health metrics, identify slow endpoints, and propose 1-2 optimization tasks for tomorrow.",
            "ML Engineer":        "Review LLM cost + latency for today, identify cache opportunities, suggest prompt optimizations.",
            "Operations Manager": "Update project status across all teams, flag any deadline risks, prepare tomorrow's stand-up agenda.",
            "Business Analyst":   "Pull today's KPIs, draft a 1-page summary with trend lines, list 3 strategic questions for VIP.",
            "QA Engineer":        "Run smoke tests on critical paths, document any regressions, prepare bug priority list for tomorrow.",
            "Sales Manager":      "Update the sales pipeline, draft follow-ups for prospects last contacted >5 days ago.",
            "Finance Manager":    "Reconcile today's transactions, flag any anomalies, draft tomorrow's cash position summary.",
            "HR Manager":         "Review attendance, flag any 24h+ absences, prepare any pending HR requests for tomorrow.",
            "General Manager":    "Compile cross-department status, identify any escalations needed for VIP attention.",
        }

        twins = db.query(DigitalTwin).all()
        assigned = 0
        skipped = 0

        for twin in twins:
            # Skip if twin already has a standing task created today (avoid duplicates)
            today_start = dt.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            existing = (
                db.query(TwinTask)
                .filter(TwinTask.twin_id == twin.id, TwinTask.assigned_by == "system_standing", TwinTask.created_at >= today_start)
                .first()
            )
            if existing:
                skipped += 1
                continue

            template = ROLE_TASKS.get(twin.role, f"Review your area of responsibility ({twin.role}) and prepare a brief status note for tomorrow morning.")
            task = TwinTask(
                twin_id=twin.id,
                title=f"Daily Standing Task — {dt.utcnow().strftime('%Y-%m-%d')}",
                description=template,
                priority="medium",
                status="todo",
                assigned_by="system_standing",
                deadline=dt.utcnow() + timedelta(hours=14),
                needs_review=True,
                review_status="pending",
            )
            db.add(task)
            assigned += 1

        db.commit()
        log.info(f"daily-standing-tasks: assigned {assigned} new, skipped {skipped} existing",
                 extra={"action": "twin.standing_tasks", "assigned": assigned, "skipped": skipped})
    except Exception as e:
        db.rollback()
        log.error(f"daily-standing-tasks: error {e}", extra={"action": "twin.standing_tasks_error"})
    finally:
        db.close()


@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="auto_self_improvement", alert_on_final_failure=False)
def _auto_self_improvement():
    """Run self-improvement cycle for all twins."""
    db = SessionLocal()
    try:
        from services.twin_self_improve import run_all_twins_improvement
        results = run_all_twins_improvement(db)
        total = sum(r.get("total_improvements", 0) for r in results)
        db.commit()
        log.info(f"self-improve: cycle complete — {total} total improvements across {len(results)} twins",
                 extra={"action": "twin.self_improve_cycle"})
    except Exception as e:
        db.rollback()
        log.error(f"self-improve: error {e}", extra={"action": "twin.self_improve_error"})
    finally:
        db.close()


@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="chatbot_self_improvement", alert_on_final_failure=False)
def _chatbot_self_improvement():
    """
    Chatbot module self-improve cycle (runs every 6h).
    Currently surfaces clusters of failed queries per agent and logs them so
    the team knows what intents to add. Future: auto-promote high-frequency
    correct intents into FAQ + auto-prune low-confidence auto-examples.
    """
    db = SessionLocal()
    try:
        from services.chatbot_self_improve import cluster_failures
        from db.models import ChatbotInteraction
        # Find every distinct agent that's been used in the last 24h
        from sqlalchemy import distinct
        agent_ids = [r[0] for r in db.query(distinct(ChatbotInteraction.agent_id)).all() if r[0]]
        total_suggestions = 0
        for aid in agent_ids:
            failures = cluster_failures(db, aid, hours=168, min_count=3)
            if failures:
                log.info(f"chatbot.self_improve [{aid}]: {len(failures)} skill suggestions",
                         extra={"action": "chatbot.skill_suggest", "agent_id": aid, "count": len(failures)})
                total_suggestions += len(failures)
        log.info(f"chatbot.self_improve cycle complete — {total_suggestions} total suggestions across {len(agent_ids)} agents",
                 extra={"action": "chatbot.self_improve_cycle"})
    except Exception as e:
        db.rollback()
        log.error(f"chatbot.self_improve error: {e}", extra={"action": "chatbot.self_improve_error"})
    finally:
        db.close()


@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="auto_monthly_report")
def _auto_monthly_report():
    """Automatic monthly report — 1st of the month, last ~30 days."""
    from services.report_service import compose_report
    from services.telegram_service import send_alert

    db = SessionLocal()
    trace_id = f"tr-auto-monthly-{int(datetime.utcnow().timestamp())}"
    try:
        report = compose_report(db, report_type="monthly_summary", hours_back=720, trace_id=trace_id)
        summary = report.get("executive_summary", "Monthly report generated.")
        send_alert(
            f"🗓️ <b>VIP Monthly Report</b>\n<i>{datetime.utcnow().strftime('%Y-%m')}</i>\n\n"
            f"{summary[:600]}\n\n<i>View on dashboard → Reports → Monthly</i>"
        )
        log.info("auto-report: monthly done", extra={"trace_id": trace_id, "action": "auto_report.monthly.done"})
    except Exception as e:
        log.warning(f"auto-report: monthly failed: {e}", extra={"action": "auto_report.monthly.failed"})
    finally:
        db.close()


@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="auto_cross_agent_report")
def _auto_cross_agent_report():
    """Automatic cross-agent report — daily; pulls live data from all 3 agents."""
    from services.report_service import compose_cross_agent_report
    from services.telegram_service import send_alert

    db = SessionLocal()
    trace_id = f"tr-auto-cross-{int(datetime.utcnow().timestamp())}"
    try:
        report = compose_cross_agent_report(
            db, agent_types=["asset", "stock", "realty"], trace_id=trace_id)
        summary = report.get("executive_summary", "Cross-agent report generated.")
        send_alert(
            f"🔗 <b>VIP Cross-Agent Report</b>\n<i>{datetime.utcnow().strftime('%Y-%m-%d')}</i>\n\n"
            f"{summary[:500]}\n\n<i>View on dashboard → Reports → Cross-Agent</i>"
        )
        log.info("auto-report: cross-agent done", extra={"trace_id": trace_id, "action": "auto_report.cross.done"})
    except Exception as e:
        log.warning(f"auto-report: cross-agent failed: {e}", extra={"action": "auto_report.cross.failed"})
    finally:
        db.close()


@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="kiwoom_daily_report")
def _kiwoom_daily_report(email_override: str | None = None):
    """Daily Kiwoom market report — runs ~6:30 AM KST (after the US close).
    Real OHLCV for the watchlist + LLM structured analysis (EN/KO).
    `email_override` lets the manual trigger send the .docx to a specific
    address for testing; the scheduled run uses KIWOOM_REPORT_EMAIL env."""
    from services.kiwoom_report import build_kiwoom_report, format_kiwoom_telegram
    from services.telegram_service import send_alert
    from db.models import OrchReport

    db = SessionLocal()
    trace = f"tr-kiwoom-{int(datetime.utcnow().timestamp())}"
    kst = datetime.utcnow().strftime("%Y-%m-%d") + " 06:30 KST"
    try:
        rep = build_kiwoom_report(db, trace)
        r = OrchReport(
            report_type="kiwoom_report",
            source_run_ids_json=[],
            content_json={
                "report_type": "kiwoom_report", "period": "daily",
                "executive_summary": rep.get("summary_en") or "Kiwoom daily report",
                "sections": [{"title": "Kiwoom Daily", "content": rep.get("table_en", ""), "data": {}}],
                "report": rep,
                "generated_at": datetime.utcnow().isoformat(), "kst_time": kst,
            },
            delivery_channel="auto",
        )
        db.add(r)
        db.commit()
        # Send the FULL report (same content + table as the dashboard), split into
        # Telegram-sized chunks. Korean by default; falls back to EN if KO is thin.
        try:
            chunks = format_kiwoom_telegram(rep, kst, lang="ko")
            for chunk in chunks:
                send_alert(chunk)
            log.info(f"kiwoom: telegram sent in {len(chunks)} message(s)",
                     extra={"trace_id": trace, "action": "kiwoom.telegram"})
        except Exception as te:
            # Never let a formatting issue lose the alert — fall back to a summary.
            log.warning(f"kiwoom telegram format failed: {te}")
            send_alert(f"📈 <b>Kiwoom Daily Report</b>\n<i>{kst}</i>\n\n"
                       f"{rep.get('summary_en', '')[:300]}\n\n<i>View → Reports → Kiwoom</i>")

        # Email the report as a Word (.docx) attachment (best-effort; no-op if
        # SMTP / recipient not configured).
        try:
            from services.report_docx import markdown_to_docx
            from services.report_email import send_email_with_docx, is_configured as _email_ok
            to_addr = (email_override or os.getenv("KIWOOM_REPORT_EMAIL")
                       or os.getenv("REPORT_EMAIL_TO") or os.getenv("SMTP_USER")
                       or os.getenv("SMTP_EMAIL"))
            if _email_ok() and to_addr:
                body_md = rep.get("detail_ko") or ""
                if len(body_md.strip()) < 200 or "same report in korean" in body_md.lower():
                    body_md = rep.get("detail_en") or ""
                docx_bytes = markdown_to_docx(body_md, "Kiwoom Daily Market Report", kst)
                fname = f"Kiwoom_Report_{datetime.utcnow().strftime('%Y%m%d')}.docx"
                res = send_email_with_docx(
                    to_addr, f"[Kiwoom] 일일 시장 리포트 — {kst}",
                    "키움 일일 시장 리포트입니다. 첨부된 Word 파일을 확인해 주세요.\n\n"
                    "(Kiwoom daily market report attached as a Word document.)",
                    fname, docx_bytes)
                log.info(f"kiwoom: email {'sent' if res.get('ok') else 'skipped'} -> {to_addr}"
                         f" ({res.get('reason', 'ok')})",
                         extra={"trace_id": trace, "action": "kiwoom.email"})
            else:
                log.info("kiwoom: email skipped (SMTP not configured or no recipient)",
                         extra={"action": "kiwoom.email.skip"})
        except Exception as ee:
            log.warning(f"kiwoom: email step failed: {ee}", extra={"action": "kiwoom.email.failed"})

        log.info(f"kiwoom: daily report saved + sent ({rep['status']})",
                 extra={"trace_id": trace, "action": "kiwoom.daily.done"})
    except Exception as e:
        log.warning(f"kiwoom: daily report failed: {e}", extra={"action": "kiwoom.daily.failed"})
    finally:
        db.close()


def init_scheduler():
    """Initialize the scheduler and load enabled rules from DB."""
    global _scheduler

    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(daemon=True)
    _load_rules_from_db()

    # Add agent health check — every 5 minutes
    _scheduler.add_job(
        _execute_health_check,
        CronTrigger.from_crontab("*/5 * * * *"),
        id="agent-health-check",
        replace_existing=True,
    )
    log.info("scheduler: health check registered (every 5 min)", extra={"action": "scheduler.health_registered"})

    # Auto daily reports — 8:00 AM KST = 23:00 UTC (previous day)
    # Sends 3 individual agent reports + 1 combined summary
    _scheduler.add_job(
        _auto_daily_reports,
        CronTrigger.from_crontab("0 23 * * *"),
        id="auto-daily-reports",
        replace_existing=True,
    )
    log.info("scheduler: auto daily reports registered (23:00 UTC = 8:00 AM KST)", extra={"action": "scheduler.auto_daily_registered"})

    # Stock market-close capture — 15:30 KST = 06:30 UTC, weekdays (KRX trading days)
    _scheduler.add_job(
        _capture_stock_market_close,
        CronTrigger.from_crontab("30 6 * * 1-5"),
        id="stock-market-close",
        replace_existing=True,
    )
    log.info("scheduler: stock market-close capture registered (06:30 UTC = 15:30 KST)", extra={"action": "scheduler.stock_close_registered"})

    # Autonomous A2A alerts — agents watch their own live data and proactively
    # warn each other / the boss. Hourly during business hours (UTC 0-10 =
    # KST 9-19), weekdays. De-duplicated so it doesn't spam.
    from services.autonomous_alerts import run_autonomous_alerts
    _scheduler.add_job(
        run_autonomous_alerts,
        CronTrigger.from_crontab("0 0-10 * * 1-5"),
        id="autonomous-alerts",
        replace_existing=True,
    )
    log.info("scheduler: autonomous A2A alerts registered (hourly, KST business hrs)", extra={"action": "scheduler.auto_alerts_registered"})

    # Auto monthly report — 1st of the month, 23:00 UTC (= 8 AM KST on the 1st)
    _scheduler.add_job(
        _auto_monthly_report,
        CronTrigger.from_crontab("0 23 1 * *"),
        id="auto-monthly-report",
        replace_existing=True,
    )
    log.info("scheduler: auto monthly report registered (1st of month, 8 AM KST)", extra={"action": "scheduler.auto_monthly_registered"})

    # Auto cross-agent report — daily 23:30 UTC (after the daily pipeline)
    _scheduler.add_job(
        _auto_cross_agent_report,
        CronTrigger.from_crontab("30 23 * * *"),
        id="auto-cross-agent-report",
        replace_existing=True,
    )
    log.info("scheduler: auto cross-agent report registered (daily 23:30 UTC)", extra={"action": "scheduler.auto_cross_registered"})

    # Kiwoom daily report — 6:30 AM KST (after US close) = 21:30 UTC, weekdays KST
    # (UTC Sun-Thu 21:30 = Mon-Fri 06:30 KST).
    _scheduler.add_job(
        _kiwoom_daily_report,
        CronTrigger.from_crontab("30 21 * * 0-4"),
        id="kiwoom-daily-report",
        replace_existing=True,
    )
    log.info("scheduler: Kiwoom daily report registered (21:30 UTC = 6:30 AM KST)", extra={"action": "scheduler.kiwoom_registered"})

    # Auto weekly report — Friday 6:30 PM KST = 09:30 UTC Friday
    _scheduler.add_job(
        _auto_weekly_report,
        CronTrigger.from_crontab("30 9 * * 5"),
        id="auto-weekly-report",
        replace_existing=True,
    )
    log.info("scheduler: auto weekly report registered (09:30 UTC Friday = 18:30 KST Friday)", extra={"action": "scheduler.auto_weekly_registered"})

    # Twin auto-mode-switch — every 1 minute
    # Checks working hours (9-18 KST, Mon-Fri) and switches twin modes
    _scheduler.add_job(
        _auto_twin_mode_switch,
        CronTrigger.from_crontab("* * * * *"),
        id="twin-auto-mode-switch",
        replace_existing=True,
    )
    log.info("scheduler: twin auto-mode-switch registered (every 1 min)", extra={"action": "scheduler.twin_mode_registered"})

    # Twin morning handoff — 9:00 AM KST = 00:00 UTC
    _scheduler.add_job(
        _auto_morning_handoff,
        CronTrigger.from_crontab("0 0 * * 1-5"),
        id="twin-morning-handoff",
        replace_existing=True,
    )
    log.info("scheduler: twin morning handoff registered (00:00 UTC = 9:00 AM KST, Mon-Fri)", extra={"action": "scheduler.twin_handoff_registered"})

    # Twin self-improvement — runs every 6 hours
    _scheduler.add_job(
        _auto_self_improvement,
        CronTrigger.from_crontab("0 */6 * * *"),
        id="twin-self-improvement",
        replace_existing=True,
    )
    log.info("scheduler: twin self-improvement registered (every 6 hours)", extra={"action": "scheduler.twin_self_improve_registered"})

    # Chatbot self-improvement — runs every 6 hours, offset 30 min from twin
    _scheduler.add_job(
        _chatbot_self_improvement,
        CronTrigger.from_crontab("30 */6 * * *"),
        id="chatbot-self-improvement",
        replace_existing=True,
    )
    log.info("scheduler: chatbot self-improvement registered (every 6 hours, :30 past)",
             extra={"action": "scheduler.chatbot_self_improve_registered"})

    # Claude Code auto-import — every hour
    _scheduler.add_job(
        _auto_import_claude_sessions,
        CronTrigger.from_crontab("15 * * * *"),
        id="claude-auto-import",
        replace_existing=True,
    )
    log.info("scheduler: claude auto-import registered (every hour at :15)", extra={"action": "scheduler.claude_import_registered"})

    # Daily standing tasks — assign 1 standard task per twin every day at 18:00 KST = 09:00 UTC
    # Ensures twins always have something to do overnight, so morning handoff isn't empty
    _scheduler.add_job(
        _auto_assign_daily_standing_tasks,
        CronTrigger.from_crontab("0 9 * * *"),
        id="daily-standing-tasks",
        replace_existing=True,
    )
    log.info("scheduler: daily standing tasks registered (09:00 UTC = 18:00 KST)", extra={"action": "scheduler.daily_tasks_registered"})

    # Voice campaign runner — every 30 seconds, dial the next queued recipient
    # for each running campaign across all agents, respecting per-campaign
    # pacing + working hours. See services/campaign_runner.py
    from services.campaign_runner import tick as _voice_campaign_tick
    _scheduler.add_job(
        _voice_campaign_tick,
        "interval",
        seconds=30,
        id="voice-campaign-runner",
        replace_existing=True,
    )
    log.info("scheduler: voice campaign runner registered (every 30s)",
             extra={"action": "scheduler.voice_runner_registered"})

    # Voice recording retention — daily at 03:00 UTC = 12:00 KST
    # Deletes Storage objects + DB rows past retention_expires_at.
    from services.voice_storage import cleanup_expired_recordings as _voice_retention
    _scheduler.add_job(
        _voice_retention,
        CronTrigger.from_crontab("0 3 * * *"),
        id="voice-recording-retention",
        replace_existing=True,
    )
    log.info("scheduler: voice recording retention registered (daily 03:00 UTC)",
             extra={"action": "scheduler.voice_retention_registered"})

    # Chatbot morning report — daily at 23:00 UTC = 08:00 KST next day.
    # Aggregates yesterday's chat + call activity per agent + Telegram delivers.
    from services.chatbot_morning_report import deliver_morning_reports_all_agents as _morning_report
    _scheduler.add_job(
        _morning_report,
        CronTrigger.from_crontab("0 23 * * *"),
        id="chatbot-morning-report",
        replace_existing=True,
    )
    log.info("scheduler: chatbot morning report registered (23:00 UTC = 08:00 KST)",
             extra={"action": "scheduler.chatbot_morning_report_registered"})

    # Chatbot mode-override expiry — every minute, clears overrides where
    # mode_expires_at has passed (so "back in 2 hours" actually flips back
    # to IN at the 2-hour mark without manual intervention).
    from services.chatbot_mode_detector import expire_overdue_overrides as _mode_expire
    _scheduler.add_job(
        _mode_expire,
        CronTrigger.from_crontab("* * * * *"),
        id="chatbot-mode-expire",
        replace_existing=True,
    )
    log.info("scheduler: chatbot mode expiry tick registered (every 1 min)",
             extra={"action": "scheduler.chatbot_mode_expire_registered"})

    # Chatbot email poll — every 2 minutes; pulls UNSEEN messages for each
    # configured agent and feeds them through the inbound reply pipeline.
    # Env-gated so it doesn't run in dev without IMAP creds.
    if os.getenv("CHATBOT_EMAIL_POLL_ENABLED", "0") == "1":
        from services.chatbot_email_ingest import poll_all_agents as _email_poll
        _scheduler.add_job(
            _email_poll,
            CronTrigger.from_crontab("*/2 * * * *"),
            id="chatbot-email-poll",
            replace_existing=True,
        )
        log.info(
            "scheduler: chatbot email poll registered (every 2 min)",
            extra={"action": "scheduler.chatbot_email_poll_registered"},
        )

    # Assistant self-improvement — nightly cycle that researches the top
    # recurring low-confidence questions and learns them into each agent's KB.
    # 17:00 UTC = 02:00 KST (quiet hours). Best-effort.
    try:
        _scheduler.add_job(
            _run_assistant_improvement,
            CronTrigger.from_crontab("0 17 * * *"),
            id="assistant-self-improve",
            replace_existing=True,
            max_instances=1,   # never overlap a long-running cycle
            coalesce=True,     # collapse missed runs into one
        )
        log.info("scheduler: assistant self-improvement registered (17:00 UTC = 02:00 KST)",
                 extra={"action": "scheduler.assistant_improve_registered"})
    except Exception as _e:
        log.warning(f"scheduler: could not register assistant self-improve: {_e}")

    _scheduler.start()
    log.info("scheduler: started", extra={"action": "scheduler.started"})


def _run_assistant_improvement():
    """Scheduler entry — runs the assistant self-improvement cycle on its own DB
    session. Never raises (scheduler jobs must not crash the loop)."""
    try:
        from db.base import SessionLocal
        from services.assistant_learning import nightly_improve_cycle
        db = SessionLocal()
        try:
            result = nightly_improve_cycle(db)
            log.info(f"scheduler: assistant self-improve done -> {result.get('summary')}",
                     extra={"action": "scheduler.assistant_improve_done"})
        finally:
            db.close()
    except Exception as e:
        log.warning(f"scheduler: assistant self-improve failed: {e}",
                    extra={"action": "scheduler.assistant_improve_failed"})

    # === Phase 3: Restart-safe catch-up ===
    # If we were down when a daily job should have fired, run it now (delayed 30s
    # so the scheduler is fully up). Each catch-up runs in a background thread.
    try:
        missed = detect_missed_runs()
        if missed:
            import threading
            for m in missed:
                job_name = m["job"]
                fn = {
                    "auto_daily_reports":   _auto_daily_reports,
                    "twin_morning_handoff": _auto_morning_handoff,
                    "daily_standing_tasks": _auto_assign_daily_standing_tasks,
                }.get(job_name)
                if not fn:
                    continue
                log.info(f"scheduler: catch-up firing missed job {job_name}",
                         extra={"action": "scheduler.catchup", "job": job_name})
                threading.Thread(
                    target=fn, daemon=True,
                    name=f"catchup-{job_name}",
                ).start()
            alert(
                kind="catchup_fired",
                title=f"📅 Caught up {len(missed)} missed scheduled job(s)",
                body="\n".join(f"- {m['job']} (was due at {m['expected_at']})" for m in missed),
                severity="info",
            )
    except Exception as e:
        log.warning(f"scheduler: catch-up check failed: {e}", extra={"action": "scheduler.catchup_failed"})


def _load_rules_from_db():
    """Load all enabled schedule rules and register jobs."""
    db = SessionLocal()
    try:
        rules = db.query(OrchScheduleRule).filter(OrchScheduleRule.enabled == True).all()

        for rule in rules:
            task_def = db.query(OrchTaskDefinition).filter(OrchTaskDefinition.id == rule.target_task_definition_id).first()
            if not task_def:
                continue

            job_id = f"schedule-{rule.id}"

            # Check if this is a report rule (name contains 'report' or 'summary')
            if "report" in rule.name or "summary" in rule.name.replace("_", " "):
                if "weekly" in rule.name:
                    report_type = "weekly_summary"
                    hours = 168
                elif "alert" in rule.name:
                    report_type = "urgent_alert_summary"
                    hours = 4
                else:
                    report_type = "daily_summary"
                    hours = 24

                _scheduler.add_job(
                    _execute_report_job,
                    CronTrigger.from_crontab(rule.cron_expr),
                    id=job_id,
                    replace_existing=True,
                    args=[rule.name, report_type, hours, str(rule.id)],
                )
            else:
                _scheduler.add_job(
                    _execute_scheduled_job,
                    CronTrigger.from_crontab(rule.cron_expr),
                    id=job_id,
                    replace_existing=True,
                    args=[str(rule.id), rule.name, task_def.task_type, task_def.target_agent_type],
                )

            log.info(
                f"scheduler: loaded rule '{rule.name}' cron='{rule.cron_expr}'",
                extra={"action": "scheduler.rule_loaded"},
            )

        log.info(f"scheduler: {len(rules)} rules loaded", extra={"action": "scheduler.rules_loaded"})
    finally:
        db.close()


def reload_rules():
    """Reload all rules (after enable/disable changes)."""
    global _scheduler
    if _scheduler:
        _scheduler.remove_all_jobs()
        _load_rules_from_db()
        log.info("scheduler: rules reloaded", extra={"action": "scheduler.reloaded"})


def run_now(rule_id: UUID) -> dict:
    """Manually trigger a schedule rule immediately."""
    db = SessionLocal()
    try:
        rule = db.query(OrchScheduleRule).filter(OrchScheduleRule.id == rule_id).first()
        if not rule:
            return {"error": "Rule not found"}

        task_def = db.query(OrchTaskDefinition).filter(OrchTaskDefinition.id == rule.target_task_definition_id).first()
        if not task_def:
            return {"error": "Task definition not found"}

        # Check if report or task
        if "report" in rule.name or "summary" in rule.name.replace("_", " "):
            report_type = "weekly_summary" if "weekly" in rule.name else "daily_summary"
            hours = 168 if "weekly" in rule.name else 24
            _execute_report_job(rule.name, report_type, hours)
            return {"triggered": True, "rule": rule.name, "type": "report", "report_type": report_type}
        else:
            _execute_scheduled_job(str(rule.id), rule.name, task_def.task_type, task_def.target_agent_type)
            return {"triggered": True, "rule": rule.name, "type": "task", "task_type": task_def.task_type}
    finally:
        db.close()


def list_rules(db: Session) -> list[dict]:
    """List all schedule rules with next fire time."""
    rules = db.query(OrchScheduleRule).order_by(OrchScheduleRule.name).all()
    result = []
    for r in rules:
        task_def = db.query(OrchTaskDefinition).filter(OrchTaskDefinition.id == r.target_task_definition_id).first()

        # Get next fire time from scheduler
        next_fire = None
        if _scheduler:
            job = _scheduler.get_job(f"schedule-{r.id}")
            if job and job.next_run_time:
                next_fire = job.next_run_time.isoformat()

        result.append({
            "id": str(r.id),
            "name": r.name,
            "cron_expr": r.cron_expr,
            "enabled": r.enabled,
            "task_type": task_def.task_type if task_def else None,
            "target_agent_type": task_def.target_agent_type if task_def else None,
            "next_fire_time": next_fire,
            "last_run_at": r.last_run_at.isoformat() if getattr(r, "last_run_at", None) else None,
            "last_run_status": getattr(r, "last_run_status", None),
            "run_count": getattr(r, "run_count", 0) or 0,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return result


def update_rule(db: Session, rule_id: UUID, updates: dict) -> dict | None:
    """Update a schedule rule (enable/disable, change cron)."""
    rule = db.query(OrchScheduleRule).filter(OrchScheduleRule.id == rule_id).first()
    if not rule:
        return None

    if "enabled" in updates:
        rule.enabled = updates["enabled"]
    if "cron_expr" in updates:
        rule.cron_expr = updates["cron_expr"]
    if "name" in updates:
        rule.name = updates["name"]

    db.commit()
    reload_rules()

    return {"updated": True, "id": str(rule.id), "name": rule.name, "enabled": rule.enabled, "cron_expr": rule.cron_expr}
