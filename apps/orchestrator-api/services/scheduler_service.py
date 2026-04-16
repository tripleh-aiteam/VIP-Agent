"""
VIP AI Platform — Scheduler Service
Reads schedule rules from DB, runs tasks on cron, retries once on failure.
Uses APScheduler for MVP.
"""

from datetime import datetime
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
    Each report goes to both Dashboard and Telegram.
    """
    from services.task_service import create_task, dispatch_task
    from services.report_service import compose_report
    from services.telegram_service import send_alert

    # All agents that should produce daily reports
    AGENT_REPORTS = [
        {"task_type": "asset_summary", "agent_type": "asset", "name": "Asset Agent", "emoji": "🏢"},
        {"task_type": "stock_analysis", "agent_type": "stock", "name": "Stock Agent", "emoji": "📈"},
        {"task_type": "realty_listing_fetch", "agent_type": "realty", "name": "Real Estate Agent", "emoji": "🏠"},
    ]

    db = SessionLocal()
    base_trace = f"tr-auto-daily-{int(datetime.utcnow().timestamp())}"

    log.info("auto-report: starting daily pipeline (3 agents + combined)", extra={"trace_id": base_trace, "action": "auto_report.daily.start"})

    agent_results = []

    try:
        # Step 1: Fetch fresh data from EACH agent and send individual Telegram report
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

                # Build per-agent Telegram message
                output = run.output_payload or {}
                status_icon = "✅" if run.status == "completed" else "❌"

                telegram_lines = [
                    f"{agent_info['emoji']} <b>{agent_info['name']} — Daily Report</b>",
                    "",
                ]

                if run.status == "completed" and output:
                    # Extract key metrics per agent type
                    if agent_info["agent_type"] == "asset":
                        portfolio = output.get("portfolio", {})
                        contracts = output.get("contracts", {})
                        telegram_lines.append(f"Properties: {portfolio.get('total_properties', 0)}")
                        telegram_lines.append(f"Occupancy: {100 - portfolio.get('vacancy_rate', 0):.1f}%")
                        telegram_lines.append(f"Contracts: {contracts.get('total', 0)}")
                        telegram_lines.append(f"Cash Balance: {output.get('cash', {}).get('total_balance', 0):,.0f} KRW")
                        telegram_lines.append(f"Risk: {output.get('risk_level', 'N/A')}")
                    elif agent_info["agent_type"] == "stock":
                        telegram_lines.append(f"Stocks Analyzed: {output.get('symbols_analyzed', output.get('news_count', 'N/A'))}")
                        telegram_lines.append(f"Sentiment: {output.get('market_sentiment', 'N/A')}")
                        telegram_lines.append(f"Risk Score: {output.get('risk_score', 'N/A')}")
                    elif agent_info["agent_type"] == "realty":
                        telegram_lines.append(f"Listings: {output.get('total_listings', 0)}")
                        telegram_lines.append(f"Avg Vacancy: {output.get('avg_vacancy_pct', 0)}%")
                        telegram_lines.append(f"Avg Yield: {output.get('avg_yield_pct', 0)}%")
                        telegram_lines.append(f"Trend: {output.get('market_trend', 'N/A')}")
                else:
                    telegram_lines.append(f"Status: {run.status}")
                    if run.error_message:
                        telegram_lines.append(f"Error: {run.error_message[:100]}")

                telegram_lines.append(f"\n{status_icon} Status: {run.status}")
                telegram_lines.append(f"<code>{trace_id}</code>")

                send_alert("\n".join(telegram_lines))
                agent_results.append({"agent": agent_info["name"], "status": run.status})

                log.info(f"auto-report: {agent_info['name']} done -> {run.status}", extra={"trace_id": trace_id, "action": "auto_report.agent"})

            except Exception as e:
                agent_results.append({"agent": agent_info["name"], "status": "failed", "error": str(e)})
                log.warning(f"auto-report: {agent_info['name']} failed: {e}", extra={"action": "auto_report.agent.failed"})

        # Step 2: Compose combined daily report
        report = compose_report(db, report_type="daily_summary", hours_back=24, trace_id=base_trace)

        # Step 3: Send combined summary to Telegram
        completed = len([r for r in agent_results if r["status"] == "completed"])
        total = len(agent_results)

        combined_lines = [
            f"📊 <b>VIP Daily Summary</b>",
            f"",
            f"Agents: {completed}/{total} reported successfully",
        ]
        for r in agent_results:
            icon = "✅" if r["status"] == "completed" else "❌"
            combined_lines.append(f"  {icon} {r['agent']}: {r['status']}")

        summary = report.get("executive_summary", "")
        if summary:
            combined_lines.append(f"\n{summary[:300]}")

        combined_lines.append(f"\n<i>Full report on dashboard</i>")

        send_alert("\n".join(combined_lines))

        log.info(f"auto-report: daily pipeline completed ({completed}/{total} agents)", extra={"trace_id": base_trace, "action": "auto_report.daily.done"})

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
