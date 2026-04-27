"""
VIP AI Platform — Scheduler Service
Reads schedule rules from DB, runs tasks on cron, retries once on failure.
Uses APScheduler for MVP.
"""

from datetime import datetime, timedelta
from uuid import UUID
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from db.base import SessionLocal
from db.models import OrchScheduleRule, OrchTaskDefinition
from services.logger import log

_scheduler: BackgroundScheduler | None = None


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

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

        log.info(
            f"scheduler: {rule_name} completed -> {run.status}",
            extra={"trace_id": trace_id, "task_id": str(run.id), "action": "scheduler.completed"},
        )

    except Exception as e:
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


def _execute_report_job(rule_name: str, report_type: str, hours_back: int = 24):
    """Execute a scheduled report composition."""
    from services.report_service import compose_report

    db = SessionLocal()
    trace_id = f"tr-sched-report-{int(datetime.utcnow().timestamp())}"

    log.info(f"scheduler: composing {report_type} report", extra={"trace_id": trace_id, "action": "scheduler.report"})

    try:
        compose_report(db, report_type=report_type, hours_back=hours_back, trace_id=trace_id)
        log.info(f"scheduler: {report_type} report done", extra={"action": "scheduler.report.done"})
    except Exception as e:
        log.warning(f"scheduler: report failed: {e}", extra={"action": "scheduler.report.failed"})
    finally:
        db.close()


def _auto_daily_reports():
    """
    Automatic daily report pipeline — runs at 8 AM KST (23:00 UTC previous day).
    Sends one report PER AGENT + one combined daily report.
    Each report saved to Dashboard (Reports page) AND sent to Telegram.
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
        # Step 1: Fetch data from EACH agent, save report, send Telegram
        for agent_info in AGENT_REPORTS:
            trace_id = f"{base_trace}-{agent_info['agent_type']}"
            try:
                run = create_task(
                    db=db, trace_id=trace_id,
                    task_type=agent_info["task_type"],
                    target_agent_type=agent_info["agent_type"],
                    initiator_type="system_scheduler",
                    initiator_id="auto-daily-report",
                    source_channel="scheduler",
                    input_payload={"auto_report": True},
                )
                run = dispatch_task(db, run.id)

                output = run.output_payload or {}
                status_icon = "✅" if run.status == "completed" else "❌"

                # Build per-agent report content
                agent_summary = f"{agent_info['name']} Daily Report — {kst_now}"
                sections = []
                telegram_lines = [
                    f"{agent_info['emoji']} <b>{agent_info['name']} — Daily Report</b>",
                    f"<i>{kst_now}</i>",
                    "",
                ]

                if run.status == "completed" and output:
                    if agent_info["agent_type"] == "asset":
                        portfolio = output.get("portfolio", {})
                        contracts = output.get("contracts", {})
                        metrics = {
                            "Properties": portfolio.get("total_properties", 0),
                            "Occupancy": f"{100 - portfolio.get('vacancy_rate', 0):.1f}%",
                            "Contracts": contracts.get("total", 0),
                            "Cash Balance": f"{output.get('cash', {}).get('total_balance', 0):,.0f} KRW",
                            "Risk": output.get("risk_level", "N/A"),
                        }
                    elif agent_info["agent_type"] == "stock":
                        metrics = {
                            "Stocks Analyzed": output.get("symbols_analyzed", output.get("news_count", "N/A")),
                            "Sentiment": output.get("market_sentiment", "N/A"),
                            "Risk Score": output.get("risk_score", "N/A"),
                        }
                    else:
                        metrics = {
                            "Listings": output.get("total_listings", 0),
                            "Avg Vacancy": f"{output.get('avg_vacancy_pct', 0)}%",
                            "Avg Yield": f"{output.get('avg_yield_pct', 0)}%",
                            "Trend": output.get("market_trend", "N/A"),
                        }

                    for k, v in metrics.items():
                        telegram_lines.append(f"{k}: {v}")

                    sections.append({
                        "title": agent_info["name"],
                        "content": "\n".join(f"{k}: {v}" for k, v in metrics.items()),
                        "data": metrics,
                    })
                    agent_summary = f"{agent_info['name']}: " + ", ".join(f"{k}={v}" for k, v in list(metrics.items())[:3])
                else:
                    telegram_lines.append(f"Status: {run.status}")
                    if run.error_message:
                        telegram_lines.append(f"Error: {run.error_message[:100]}")
                    sections.append({"title": agent_info["name"], "content": f"Status: {run.status}", "data": {}})

                telegram_lines.append(f"\n{status_icon} Status: {run.status}")

                # Save per-agent report to DB (appears on Reports page)
                agent_report = OrchReport(
                    report_type=f"agent_daily_{agent_info['agent_type']}",
                    source_run_ids_json=[str(run.id)],
                    content_json={
                        "report_type": f"agent_daily_{agent_info['agent_type']}",
                        "executive_summary": agent_summary,
                        "sections": sections,
                        "agent": agent_info["name"],
                        "generated_at": datetime.utcnow().isoformat(),
                        "kst_time": kst_now,
                    },
                    delivery_channel="auto",
                )
                db.add(agent_report)
                db.flush()

                send_alert("\n".join(telegram_lines))
                agent_results.append({"agent": agent_info["name"], "status": run.status, "report_id": str(agent_report.id)})

                log.info(f"auto-report: {agent_info['name']} saved + sent", extra={"trace_id": trace_id, "action": "auto_report.agent"})

            except Exception as e:
                agent_results.append({"agent": agent_info["name"], "status": "failed", "error": str(e)})
                log.warning(f"auto-report: {agent_info['name']} failed: {e}", extra={"action": "auto_report.agent.failed"})

        # Step 2: Compose combined daily report (also saved to DB by compose_report)
        report = compose_report(db, report_type="daily_summary", hours_back=24, trace_id=base_trace)

        # Step 3: Send combined summary to Telegram
        completed = len([r for r in agent_results if r["status"] == "completed"])
        total = len(agent_results)

        combined_lines = [
            f"📊 <b>VIP Daily Summary</b>",
            f"<i>{kst_now}</i>",
            f"",
            f"Agents: {completed}/{total} reported successfully",
        ]
        for r in agent_results:
            icon = "✅" if r["status"] == "completed" else "❌"
            combined_lines.append(f"  {icon} {r['agent']}: {r['status']}")

        summary = report.get("executive_summary", "")
        if summary:
            combined_lines.append(f"\n{summary[:300]}")

        combined_lines.append(f"\n<i>View on dashboard → Reports</i>")

        send_alert("\n".join(combined_lines))

        db.commit()
        log.info(f"auto-report: daily pipeline completed ({completed}/{total} agents, 4 reports saved)", extra={"trace_id": base_trace, "action": "auto_report.daily.done"})

    except Exception as e:
        log.warning(f"auto-report: daily pipeline failed: {e}", extra={"trace_id": base_trace, "action": "auto_report.daily.failed"})
    finally:
        db.close()


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

        telegram_lines = [
            f"📋 <b>VIP Weekly Report</b>",
            f"<i>Week ending {datetime.utcnow().strftime('%Y-%m-%d')}</i>",
            "",
        ]

        for s in sections:
            if s.get("content") and "No" not in s["content"][:5]:
                telegram_lines.append(f"<b>{s['title']}</b>")
                telegram_lines.append(f"{s['content'][:150]}")
                telegram_lines.append("")

        telegram_lines.append(f"\n<i>Full report on dashboard</i>")

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

    # Claude Code auto-import — every hour
    _scheduler.add_job(
        _auto_import_claude_sessions,
        CronTrigger.from_crontab("15 * * * *"),
        id="claude-auto-import",
        replace_existing=True,
    )
    log.info("scheduler: claude auto-import registered (every hour at :15)", extra={"action": "scheduler.claude_import_registered"})

    _scheduler.start()
    log.info("scheduler: started", extra={"action": "scheduler.started"})


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
                    args=[rule.name, report_type, hours],
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
