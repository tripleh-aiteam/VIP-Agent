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
import pytz
from sqlalchemy.orm import Session

# Schedule the morning reports directly in Korea time so we never have to reason
# about UTC↔KST day-of-week rollovers. NOTE: APScheduler's from_crontab uses
# Monday=0..Sunday=6 (NOT standard cron's Sunday=0), so day-of-week math on
# 21:xx-UTC crontabs is error-prone — we use named days in this tz instead.
_KST_TZ = pytz.timezone("Asia/Seoul")

from db.base import SessionLocal
from db.models import OrchScheduleRule, OrchTaskDefinition
from services.logger import log
from services.resilience import with_retry, alert, detect_missed_runs
from services.kst import kst_label

_scheduler: BackgroundScheduler | None = None

# ---------------------------------------------------------------------------
# Outbound-report kill-switch (multi-machine de-duplication)
# Every machine (this server + Render + a dev PC) runs the FULL scheduler, so
# without this each one sends the SAME morning reports/emails — the team got the
# recommendation email 3x. Set REPORTS_ENABLED=false on every instance EXCEPT the
# one designated report sender. Read ONCE at startup — a restart applies a
# change. Guards ONLY outbound report/email jobs; trading ticks, the position
# guard, call graders, and data collectors always run.
#
# Default FALSE (flipped 2026-08-19): the old default-true meant any machine
# that started the orchestrator WITHOUT setting the env silently became a
# second sender — exactly what happened when the 08-13 migration server came
# up and the whole team got every report twice for a week. Now an instance
# must OPT IN with REPORTS_ENABLED=true to send anything.
# ---------------------------------------------------------------------------
REPORTS_ENABLED = os.environ.get("REPORTS_ENABLED", "false").lower() == "true"


def _add_report_job(*args, **kwargs):
    """add_job for an OUTBOUND report/email job. Registers it only when this
    instance is the designated report sender (REPORTS_ENABLED); otherwise skips
    it so the team gets exactly ONE copy from the one sender. Returns Job | None.

    Belt-and-braces: the callable is additionally wrapped in a cross-instance
    send claim (see _claim_wrapped below), so even if TWO instances both have
    REPORTS_ENABLED=true, only the first to claim a fire slot in the shared DB
    actually sends — the other skips. REPORTS_ENABLED alone already failed us
    once (the 08-14→08-19 double-emails); the claim makes duplicates
    structurally impossible instead of configuration-dependent."""
    if not REPORTS_ENABLED:
        return None
    if args:
        job_name = kwargs.get("id") or getattr(args[0], "__name__", "report-job")
        args = (_claim_wrapped(job_name, args[0]),) + tuple(args[1:])
    return _scheduler.add_job(*args, **kwargs)


# ---------------------------------------------------------------------------
# Single-flight guard — only ONE run of a given report job at a time.
# Overlapping manual triggers (the dashboard button clicked repeatedly, or a
# manual run landing on top of the scheduled one) used to pile concurrent,
# network+LLM-heavy builds onto one instance, starving the slow Newspaper step
# so the run never reached Master/email. This makes a second trigger a no-op
# while the first is still running — which is what keeps generation consistent.
# ---------------------------------------------------------------------------
import threading as _threading
import functools as _functools

_run_locks: dict[str, "_threading.Lock"] = {}
_run_locks_guard = _threading.Lock()


def _single_flight(name: str):
    """Decorator: skip the call (return None) if another run of `name` is active."""
    def deco(fn):
        @_functools.wraps(fn)
        def wrapper(*a, **kw):
            with _run_locks_guard:
                lk = _run_locks.setdefault(name, _threading.Lock())
            if not lk.acquire(blocking=False):
                log.info(f"{name}: skipped — a run is already in progress",
                         extra={"action": f"{name}.skip_busy"})
                return None
            try:
                return fn(*a, **kw)
            finally:
                try:
                    lk.release()
                except RuntimeError:
                    pass
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# Cross-INSTANCE send claim — duplicates are impossible, not just discouraged.
# _single_flight above only locks within one process; when a second machine
# runs the same scheduler against the same DB (Render once, the migration
# server on 08-14→08-19), every report went out twice. All instances share
# one Supabase, so the DB itself is the referee: before a scheduled send, the
# job atomically claims its fire slot (job id + KST minute) in
# report_send_claims. Exactly one instance wins the INSERT; the rest skip.
# The 5-minute drift window also catches a slow instance whose run starts in
# a later minute (must stay BELOW the fastest guarded cadence — currently
# dip-alert-pass at every 10 min).
# Manual re-sends via the routers call the underlying functions directly and
# are never claimed — only SCHEDULED fires are.
# Fail-open: if the claim table is unreachable the send proceeds — a DB blip
# must never silence the morning reports (worst case is a duplicate, which
# the team survives; a silent no-send they might not notice for days).
# ---------------------------------------------------------------------------
import socket as _socket
from sqlalchemy import text as _sql_text

_CLAIM_DRIFT_WINDOW_MIN = 5
_claims_table_ready = False


def _ensure_claims_table(db) -> None:
    global _claims_table_ready
    if _claims_table_ready:
        return
    db.execute(_sql_text(
        """CREATE TABLE IF NOT EXISTS report_send_claims (
               job_name   text        NOT NULL,
               claim_key  text        NOT NULL,
               host       text,
               claimed_at timestamptz NOT NULL DEFAULT now(),
               PRIMARY KEY (job_name, claim_key)
           )"""))
    # Housekeeping once per process start: the table only needs recent history.
    db.execute(_sql_text(
        "DELETE FROM report_send_claims WHERE claimed_at < now() - interval '14 days'"))
    db.commit()
    _claims_table_ready = True


def _claim_send_slot(job_name: str) -> str | None:
    """Atomically claim this job's current fire slot. Returns the claim key if
    THIS instance won (caller should send), None if another instance did."""
    key = datetime.now(_KST_TZ).strftime("%Y-%m-%d %H:%M")
    db = SessionLocal()
    try:
        _ensure_claims_table(db)
        r = db.execute(_sql_text(
            """INSERT INTO report_send_claims (job_name, claim_key, host)
               SELECT :n, :k, :h
               WHERE NOT EXISTS (
                   SELECT 1 FROM report_send_claims
                   WHERE job_name = :n
                     AND claimed_at > now() - (:w * interval '1 minute')
               )
               ON CONFLICT (job_name, claim_key) DO NOTHING"""),
            {"n": job_name, "k": key, "h": _socket.gethostname(),
             "w": _CLAIM_DRIFT_WINDOW_MIN})
        db.commit()
        return key if r.rowcount == 1 else None
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        log.warning(f"send-claim: claim check failed for {job_name} — sending anyway "
                    f"(fail-open): {str(e)[:120]}",
                    extra={"action": "send_claim.fail_open", "job": job_name})
        return key
    finally:
        db.close()


def _release_send_slot(job_name: str, key: str) -> None:
    """Give the slot back after a FAILED send so the self-heal (or the other
    instance's next pass) isn't blocked by a claim that produced nothing."""
    db = SessionLocal()
    try:
        db.execute(_sql_text(
            "DELETE FROM report_send_claims WHERE job_name = :n AND claim_key = :k"),
            {"n": job_name, "k": key})
        db.commit()
    except Exception:
        pass
    finally:
        db.close()


def _claim_wrapped(job_name: str, fn):
    """Wrap a scheduled outbound job so it runs only if this instance wins the
    cross-instance claim for the current fire slot."""
    @_functools.wraps(fn)
    def wrapper(*a, **kw):
        key = _claim_send_slot(job_name)
        if key is None:
            log.info(f"send-claim: {job_name} skipped — another instance already "
                     f"claimed this send slot",
                     extra={"action": "send_claim.skip", "job": job_name})
            return None
        try:
            return fn(*a, **kw)
        except Exception:
            _release_send_slot(job_name, key)
            raise
    return wrapper


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
    # Real KST label, like every other report job. The old literal pinned the clock
    # to "08:00 KST" and took the calendar date from utcnow() — so a run at 08:00 KST
    # (23:00 UTC the previous day) stamped YESTERDAY's date. That date is what the
    # 08:00 self-heal and the 08:30 watchdog match on, so a wrong stamp reads as
    # "today's report is missing" and triggers a duplicate rebuild.
    from services.kst import kst_label as _kst_label
    kst_now = _kst_label()

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
        # Stamp the REAL KST wall-clock, never a hardcoded "15:30". The old literal
        # made a mis-scheduled run (this job used to fire pre-open) save a 06:30
        # snapshot labelled 15:30 — a row that lied about its own data. Pairing the
        # UTC calendar date with a KST clock time was wrong for the same reason.
        from services.kst import kst_now as _kst_now
        _k = _kst_now()
        kst = _k.strftime("%Y-%m-%d %H:%M") + " KST"
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


@with_retry(max_attempts=2, backoff_seconds=(30, 120), job_name="feed_daily", alert_on_final_failure=False)
def _auto_feed_summaries():
    """Each opted-in twin posts a short daily summary to the Twin Feed."""
    db = SessionLocal()
    try:
        from services.twin_feed import post_daily_summaries
        res = post_daily_summaries(db)
        log.info(f"feed-daily: {res}", extra={"action": "twin.feed_daily"})
    except Exception as e:
        db.rollback()
        log.error(f"feed-daily: error {e}", extra={"action": "twin.feed_daily_error"})
    finally:
        db.close()


@with_retry(max_attempts=2, backoff_seconds=(30, 120), job_name="cloud_pull", alert_on_final_failure=False)
def _auto_cloud_pull():
    """Phase 2 — pull connected cloud sources (Google Drive/Calendar/Gmail, Notion)
    for every twin that has consent + a connection. No-op if no provider keys set."""
    db = SessionLocal()
    try:
        from services.cloud_sources import pull_all_due
        res = pull_all_due(db)
        log.info(f"cloud-pull: {res}", extra={"action": "twin.cloud_pull"})
    except Exception as e:
        db.rollback()
        log.error(f"cloud-pull: error {e}", extra={"action": "twin.cloud_pull_error"})
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


@_single_flight("kiwoom")
@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="kiwoom_daily_report")
def _kiwoom_daily_report(email_override: str | None = None, period: str = "daily", lang: str = "ko"):
    """Daily Kiwoom market report — runs ~6:30 AM KST (after the US close).
    Real OHLCV for the watchlist + LLM structured analysis (EN/KO).
    `email_override` lets the manual trigger send the .docx to a specific
    address for testing; the scheduled run uses KIWOOM_REPORT_EMAIL env."""
    from services.kiwoom_report import build_kiwoom_report, format_kiwoom_telegram
    from services.telegram_service import send_alert
    from db.models import OrchReport

    db = SessionLocal()
    trace = f"tr-kiwoom-{int(datetime.utcnow().timestamp())}"
    kst = kst_label()
    try:
        rep = build_kiwoom_report(db, trace)
        r = OrchReport(
            report_type="kiwoom_report",
            source_run_ids_json=[],
            content_json={
                "report_type": "kiwoom_report", "period": period,
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
            from services.report_email import (send_email_with_docx,
                                               is_configured as _email_ok, DEFAULT_RECIPIENT,
                                               default_recipients)
            # Recipient: "*ALL*" → full list (manual dropdown) → test override → env → default.
            if email_override == "*ALL*":
                to_addr = default_recipients()
            else:
                to_addr = (email_override or os.getenv("KIWOOM_REPORT_EMAIL")
                           or os.getenv("REPORT_EMAIL_TO") or DEFAULT_RECIPIENT)
            if (email_override or os.getenv("SEND_INDIVIDUAL_EMAILS") == "1") and _email_ok() and to_addr:
                # Default Korean; English only when explicitly requested.
                if lang == "en":
                    body_md = rep.get("detail_en") or rep.get("detail_ko") or ""
                    title = "Kiwoom Daily Market Report"
                else:
                    body_md = rep.get("detail_ko") or ""
                    if len(body_md.strip()) < 200 or "same report in korean" in body_md.lower():
                        body_md = rep.get("detail_en") or ""
                    title = "키움 일일 시장 리포트"
                docx_bytes = markdown_to_docx(body_md, title, kst)
                fname = f"Kiwoom_Report_{datetime.utcnow().strftime('%Y%m%d')}.docx"
                res = send_email_with_docx(
                    to_addr, f"[Kiwoom] 일일 시장 리포트 — {kst}",
                    "키움 일일 시장 리포트입니다. 첨부된 Word 파일을 확인해 주세요.",
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


@_single_flight("newspaper")
@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="newspaper_daily_report")
def _newspaper_daily_report(email_override: str | None = None, period: str = "daily", lang: str = "ko"):
    """Daily newspaper (news analysis) report — runs ~7:00 AM KST. Live-news +
    the same price table as Kiwoom. `email_override` is for manual test sends."""
    from services.newspaper_report import build_newspaper_report
    from services.kiwoom_report import format_report_telegram
    from services.telegram_service import send_alert
    from db.models import OrchReport

    db = SessionLocal()
    trace = f"tr-news-{int(datetime.utcnow().timestamp())}"
    kst = kst_label()
    try:
        rep = build_newspaper_report(db, trace)
        r = OrchReport(
            report_type="newspaper_report",
            source_run_ids_json=[],
            content_json={
                "report_type": "newspaper_report", "period": period,
                "executive_summary": rep.get("summary_en") or "Newspaper market analysis",
                "sections": [{"title": "Newspaper Daily", "content": rep.get("table_en", ""), "data": {}}],
                "report": rep,
                "generated_at": datetime.utcnow().isoformat(), "kst_time": kst,
            },
            delivery_channel="auto",
        )
        db.add(r)
        db.commit()

        # Telegram — full report (newspaper-branded), chunked.
        try:
            for chunk in format_report_telegram(rep, kst, lang="ko",
                                                title="Newspaper Market Analysis", emoji="📰"):
                send_alert(chunk)
        except Exception as te:
            log.warning(f"newspaper telegram format failed: {te}")
            send_alert(f"📰 <b>Newspaper Market Analysis</b>\n<i>{kst}</i>\n\n"
                       f"{rep.get('summary_en', '')[:300]}\n\n<i>View → Reports → Newspaper</i>")

        # Email the report as a Word (.docx) attachment.
        try:
            from services.report_docx import markdown_to_docx
            from services.report_email import (send_email_with_docs,
                                               is_configured as _email_ok, DEFAULT_RECIPIENT,
                                               default_recipients)
            if email_override == "*ALL*":
                to_addr = default_recipients()
            else:
                to_addr = (email_override or os.getenv("NEWSPAPER_REPORT_EMAIL")
                           or os.getenv("REPORT_EMAIL_TO") or DEFAULT_RECIPIENT)
            if (email_override or os.getenv("SEND_INDIVIDUAL_EMAILS") == "1") and _email_ok() and to_addr:
                ymd = datetime.utcnow().strftime("%Y%m%d")
                en_md = rep.get("detail_en") or ""
                ko_md = rep.get("detail_ko") or en_md
                if len(ko_md.strip()) < 200 or "same report in korean" in ko_md.lower():
                    ko_md = en_md
                # Attach ONLY the chosen language (default Korean — no English).
                files = []
                if lang == "en" and en_md:
                    files.append((f"Newspaper_Report_EN_{ymd}.docx",
                                  markdown_to_docx(en_md, "Newspaper Market Analysis (English)", kst)))
                else:
                    md = ko_md or en_md
                    if md:
                        files.append((f"Newspaper_Report_{ymd}.docx",
                                      markdown_to_docx(md, "신문 시장 분석 리포트", kst)))
                res = send_email_with_docs(
                    to_addr, f"[Newspaper] 일일 뉴스 분석 — {kst}",
                    "일일 뉴스 시장 분석 리포트입니다. 첨부된 Word 파일을 확인해 주세요.",
                    files)
                log.info(f"newspaper: email {'sent' if res.get('ok') else 'skipped'} -> {to_addr}"
                         f" ({res.get('reason', 'ok')})",
                         extra={"trace_id": trace, "action": "newspaper.email"})
            else:
                log.info("newspaper: email skipped (SMTP not configured or no recipient)",
                         extra={"action": "newspaper.email.skip"})
        except Exception as ee:
            log.warning(f"newspaper: email step failed: {ee}", extra={"action": "newspaper.email.failed"})

        log.info(f"newspaper: daily report saved + sent ({rep['status']})",
                 extra={"trace_id": trace, "action": "newspaper.daily.done"})
    except Exception as e:
        log.warning(f"newspaper: daily report failed: {e}", extra={"action": "newspaper.daily.failed"})
    finally:
        db.close()


@_single_flight("youtube")
@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="youtube_grounded_deliver")
def _youtube_daily_report(email_override: str | None = None, period: str = "daily", lang: str = "ko"):
    """Deliver the GROUNDED YouTube report produced by the colleague's GPU
    pipeline (read-only — we never regenerate it). Always fetches the LATEST
    gpu_youtube row from orch_reports and emails its EXACT subject/body + the 4
    pre-rendered files BYTE-FOR-BYTE. Used by the 6:50 schedule (all members) and
    the on-demand button. `email_override`: "*ALL*" / a single address / a list —
    controls recipients (default = the full recipient list)."""
    from services import youtube_grounded
    from services.report_email import is_configured as _email_ok, default_recipients
    from services.telegram_service import send_alert

    db = SessionLocal()
    trace = f"tr-yt-{int(datetime.utcnow().timestamp())}"
    try:
        if email_override == "*ALL*" or email_override is None:
            recipients = default_recipients()
        elif isinstance(email_override, list):
            recipients = email_override
        else:
            recipients = [email_override]

        if not _email_ok():
            log.info("youtube(grounded): email skipped (SMTP not configured)",
                     extra={"action": "youtube.grounded.skip"})
            return
        res = youtube_grounded.deliver(db, recipients, lang=lang)
        log.info(f"youtube(grounded): {'sent' if res.get('ok') else 'skipped'} -> "
                 f"{recipients} ({res.get('reason', 'ok')})",
                 extra={"trace_id": trace, "action": "youtube.grounded.email"})
        try:
            if res.get("ok"):
                send_alert("📺 <b>YouTube 그라운드 리포트 발송</b>\n"
                           f"<i>{res.get('subject', '')}</i>\n"
                           f"{res.get('n_files', 0)}개 파일 · {len(recipients)}명 수신")
        except Exception:
            pass
    except Exception as e:
        log.warning(f"youtube(grounded): delivery failed: {e}",
                    extra={"action": "youtube.grounded.failed"})
    finally:
        db.close()


@_single_flight("asset")
@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="asset_daily_report")
def _asset_daily_report(email_override: str | None = None, period: str = "daily", lang: str = "ko"):
    """Detailed Asset Agent report — built from the live Asset backend, saved to the
    dashboard + Telegram, and sent as its OWN standalone email with the Korean
    and English .docx (scheduled 7:00 AM KST to all recipients via _asset_daily_all).
    `email_override` sends to a test address; '*ALL*' = the full recipient list."""
    from services.asset_report import build_asset_report
    from services.kiwoom_report import format_report_telegram
    from services.telegram_service import send_alert
    from db.models import OrchReport

    db = SessionLocal()
    trace = f"tr-asset-{int(datetime.utcnow().timestamp())}"
    kst = kst_label()
    try:
        rep = build_asset_report(db, trace)
        r = OrchReport(
            report_type="asset_report",
            source_run_ids_json=[],
            content_json={
                "report_type": "asset_report", "period": period,
                "executive_summary": rep.get("summary_ko") or rep.get("summary_en") or "Asset daily report",
                "sections": [{"title": "Asset Daily", "content": rep.get("table_ko", ""), "data": {}}],
                "report": rep,
                "generated_at": datetime.utcnow().isoformat(), "kst_time": kst,
            },
            delivery_channel="auto",
        )
        db.add(r)
        db.commit()

        # Telegram — the FULL detailed report (asset-branded), chunked.
        try:
            for chunk in format_report_telegram(rep, kst, lang="ko",
                                                title="Asset Agent Report", emoji="🏢"):
                send_alert(chunk)
        except Exception as te:
            log.warning(f"asset telegram format failed: {te}")

        # Email the Korean .docx (or English for lang="en" test sends) as its OWN
        # (test override, *ALL* = full recipient list, or SEND_INDIVIDUAL_EMAILS).
        try:
            from services.report_docx import markdown_to_docx
            from services.report_email import (send_email_with_docs,
                                               is_configured as _email_ok, DEFAULT_RECIPIENT,
                                               default_recipients)
            if email_override == "*ALL*":
                to_addr = default_recipients()
            else:
                to_addr = (email_override or os.getenv("ASSET_REPORT_EMAIL")
                           or os.getenv("REPORT_EMAIL_TO") or DEFAULT_RECIPIENT)
            if (email_override or os.getenv("SEND_INDIVIDUAL_EMAILS") == "1") and _email_ok() and to_addr:
                # ONE file in ONE language (boss 2026-08-19: the morning emails are
                # Korean-only; lang="en" is for on-demand English test sends).
                ymd = datetime.utcnow().strftime("%Y%m%d")
                if lang == "en":
                    body_md = rep.get("detail_en") or rep.get("detail_ko") or ""
                    files = [(f"Asset_Report_EN_{ymd}.docx",
                              markdown_to_docx(body_md, "Asset Agent Detailed Report (English)", kst))] if body_md else []
                    subject = f"[Asset] Asset Agent Detailed Report — {kst}"
                    body_txt = "The detailed Asset Agent report is attached (English)."
                else:
                    body_md = rep.get("detail_ko") or rep.get("detail_en") or ""
                    files = [(f"자산리포트_Asset_{ymd}.docx",
                              markdown_to_docx(body_md, "자산 에이전트 상세 리포트", kst))] if body_md else []
                    subject = f"[Asset] 자산 에이전트 상세 리포트 — {kst}"
                    body_txt = "자산 에이전트 상세 리포트입니다. 첨부된 Word 파일을 확인해 주세요."
                res = send_email_with_docs(to_addr, subject, body_txt, files)
                log.info(f"asset: email {'sent' if res.get('ok') else 'skipped'} -> "
                         f"{len(to_addr) if isinstance(to_addr, list) else 1} recipient(s), "
                         f"{len(files)} file(s) ({res.get('reason', 'ok')})",
                         extra={"trace_id": trace, "action": "asset.email"})
            else:
                log.info("asset: email skipped (SMTP not configured or no recipient)",
                         extra={"action": "asset.email.skip"})
        except Exception as ee:
            log.warning(f"asset: email step failed: {ee}", extra={"action": "asset.email.failed"})

        log.info(f"asset: detailed report saved + sent ({rep['status']})",
                 extra={"trace_id": trace, "action": "asset.daily.done"})
    except Exception as e:
        log.warning(f"asset: daily report failed: {e}", extra={"action": "asset.daily.failed"})
    finally:
        db.close()


@_single_flight("realty")
@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="realty_daily_report")
def _realty_daily_report(email_override: str | None = None, period: str = "daily",
                         lang: str = "ko", notify: bool = True):
    """Daily Real Estate Agent report — sourced from the PARTNER's Supabase
    (table land_investigations). Renders his 일일 현황 digest and ALWAYS saves it to
    the dashboard (VIP Reports menu). Telegram + email delivery happen ONLY when
    `notify=True`. The scheduled 7:05 AM run uses notify=False (dashboard-only —
    per the boss's request to stop emailing the real-estate report); a manual
    trigger with an explicit ?email= can still send on demand."""
    from services.realty_supabase import build_realty_supabase_report as build_realty_report
    from services.kiwoom_report import format_report_telegram
    from services.telegram_service import send_alert
    from db.models import OrchReport

    db = SessionLocal()
    trace = f"tr-realty-{int(datetime.utcnow().timestamp())}"
    kst = kst_label()
    try:
        rep = build_realty_report(db, trace)
        # The partner's Supabase is the only source now — if it's unreachable /
        # unconfigured, save a placeholder but skip Telegram + email (never send an
        # empty 'data unavailable' doc to the boss).
        source_ok = rep.get("status") != "unavailable"
        if not source_ok:
            log.warning(f"realty: Supabase source unavailable ({rep.get('reason')}) — "
                        f"saving placeholder, skipping Telegram + email",
                        extra={"trace_id": trace, "action": "realty.source.unavailable"})
        r = OrchReport(
            report_type="realty_report",
            source_run_ids_json=[],
            content_json={
                "report_type": "realty_report", "period": period,
                "executive_summary": rep.get("summary_ko") or rep.get("summary_en") or "Real estate daily report",
                "sections": [{"title": "Real Estate Daily", "content": rep.get("table_ko", ""), "data": {}}],
                "report": rep,
                "generated_at": datetime.utcnow().isoformat(), "kst_time": kst,
            },
            delivery_channel="auto",
        )
        db.add(r)
        db.commit()

        if source_ok and notify:
            try:
                for chunk in format_report_telegram(rep, kst, lang="ko",
                                                    title="Real Estate Agent Report", emoji="🏠"):
                    send_alert(chunk)
            except Exception as te:
                log.warning(f"realty telegram format failed: {te}")

        if not notify:
            log.info("realty: dashboard-only (notify=False) — Telegram + email skipped",
                     extra={"trace_id": trace, "action": "realty.dashboard_only"})

        try:
            from services.report_docx import markdown_to_docx
            from services.report_email import (send_email_with_docs,
                                               is_configured as _email_ok, DEFAULT_RECIPIENT,
                                               default_recipients)
            if email_override == "*ALL*":
                to_addr = default_recipients()
            else:
                to_addr = (email_override or os.getenv("REALTY_REPORT_EMAIL")
                           or os.getenv("REPORT_EMAIL_TO") or DEFAULT_RECIPIENT)
            if notify and source_ok and (email_override or os.getenv("SEND_INDIVIDUAL_EMAILS") == "1") and _email_ok() and to_addr:
                # ONE file in ONE language (boss 2026-08-19: Korean-only mornings;
                # lang="en" is for on-demand English test sends).
                ymd = datetime.utcnow().strftime("%Y%m%d")
                if lang == "en":
                    body_md = rep.get("detail_en") or rep.get("detail_ko") or ""
                    files = [(f"RealEstate_DailyStatus_EN_{ymd}.docx",
                              markdown_to_docx(body_md, "Real Estate Daily Status (English)", kst))] if body_md else []
                    subject = f"[Real Estate] Real Estate Daily Status — {kst}"
                    body_txt = "The Real Estate daily status report is attached (English)."
                else:
                    body_md = rep.get("detail_ko") or rep.get("detail_en") or ""
                    files = [(f"부동산_일일현황_{ymd}.docx",
                              markdown_to_docx(body_md, "부동산 일일 현황", kst))] if body_md else []
                    subject = f"[Real Estate] 부동산 일일 현황 — {kst}"
                    body_txt = "부동산 일일 현황 리포트입니다. 첨부된 Word 파일을 확인해 주세요."
                res = send_email_with_docs(to_addr, subject, body_txt, files)
                log.info(f"realty: email {'sent' if res.get('ok') else 'skipped'} -> "
                         f"{len(to_addr) if isinstance(to_addr, list) else 1} recipient(s), "
                         f"{len(files)} file(s) ({res.get('reason', 'ok')})",
                         extra={"trace_id": trace, "action": "realty.email"})
            else:
                log.info("realty: email skipped (SMTP not configured or no recipient)",
                         extra={"action": "realty.email.skip"})
        except Exception as ee:
            log.warning(f"realty: email step failed: {ee}", extra={"action": "realty.email.failed"})

        log.info(f"realty: detailed report saved + sent ({rep['status']})",
                 extra={"trace_id": trace, "action": "realty.daily.done"})
    except Exception as e:
        log.warning(f"realty: daily report failed: {e}", extra={"action": "realty.daily.failed"})
    finally:
        db.close()


@_single_flight("breaking")
@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="breaking_report", alert_on_final_failure=False)
def _breaking_report(email_override: str | None = None, focus: str | None = None,
                     seed_urls: list[str] | None = None, lang: str = "ko"):
    """Event-driven 🚨 속보 market-impact report — maps news (Korean + international)
    to affected KR stocks with direction/강도/예상밴드/신뢰도. Saved + Telegram +
    (when email_override set or '*ALL*') emailed KO+EN."""
    from services.breaking_report import build_breaking_report
    from services.kiwoom_report import format_report_telegram
    from services.telegram_service import send_alert
    from db.models import OrchReport

    db = SessionLocal()
    trace = f"tr-breaking-{int(datetime.utcnow().timestamp())}"
    kst = kst_label()
    try:
        rep = build_breaking_report(db, trace, focus=focus, seed_urls=seed_urls)
        r = OrchReport(
            report_type="breaking_report",
            source_run_ids_json=[],
            content_json={
                "report_type": "breaking_report", "period": "event",
                "executive_summary": rep.get("summary_ko") or rep.get("summary_en") or "속보 리포트",
                "sections": [{"title": "Breaking", "content": "", "data": {"severity": rep.get("severity")}}],
                "report": rep,
                "severity": rep.get("severity"),
                "generated_at": datetime.utcnow().isoformat(), "kst_time": kst,
            },
            delivery_channel="auto",
        )
        db.add(r)
        db.commit()

        try:
            for chunk in format_report_telegram(rep, kst, lang="ko",
                                                title=f"속보 — 시장영향 (severity {rep.get('severity')}/10)",
                                                emoji="🚨"):
                send_alert(chunk)
        except Exception as te:
            log.warning(f"breaking telegram format failed: {te}")

        try:
            from services.report_docx import markdown_to_docx
            from services.report_email import (send_email_with_docs,
                                               is_configured as _email_ok, DEFAULT_RECIPIENT,
                                               default_recipients)
            if email_override == "*ALL*":
                to_addr = default_recipients()
            else:
                to_addr = (email_override or os.getenv("BREAKING_REPORT_EMAIL")
                           or os.getenv("REPORT_EMAIL_TO") or DEFAULT_RECIPIENT)
            if (email_override or os.getenv("SEND_INDIVIDUAL_EMAILS") == "1") and _email_ok() and to_addr:
                ymd = datetime.utcnow().strftime("%Y%m%d_%H%M")
                ko_md = rep.get("detail_ko") or rep.get("detail_en") or ""
                en_md = rep.get("detail_en") or rep.get("detail_ko") or ""
                files = []
                if ko_md:
                    files.append((f"속보_Breaking_KO_{ymd}.docx",
                                  markdown_to_docx(ko_md, "🚨 속보 — 시장영향 리포트 (한국어)", kst)))
                if en_md:
                    files.append((f"Breaking_Report_EN_{ymd}.docx",
                                  markdown_to_docx(en_md, "Breaking Market-Impact Report (English)", kst)))
                res = send_email_with_docs(
                    to_addr, f"🚨 [속보] 시장영향 리포트 (severity {rep.get('severity')}/10) — {kst}",
                    "시장을 움직일 수 있는 이벤트가 감지되었습니다. 영향받는 종목·방향·강도·예상 변동폭"
                    "(추정)을 정리한 상세 리포트를 첨부합니다 (한/영).\n\n"
                    "A market-moving event was detected — see the attached detailed impact report.",
                    files)
                log.info(f"breaking: email {'sent' if res.get('ok') else 'skipped'} -> "
                         f"{len(to_addr) if isinstance(to_addr, list) else 1} recipient(s) "
                         f"(sev {rep.get('severity')}, {res.get('reason', 'ok')})",
                         extra={"trace_id": trace, "action": "breaking.email"})
        except Exception as ee:
            log.warning(f"breaking: email step failed: {ee}", extra={"action": "breaking.email.failed"})

        log.info(f"breaking: report saved + sent (sev {rep.get('severity')}, {rep['status']})",
                 extra={"trace_id": trace, "action": "breaking.done"})
        return rep
    except Exception as e:
        log.warning(f"breaking: report failed: {e}", extra={"action": "breaking.failed"})
        return None
    finally:
        db.close()


@_single_flight("master")
@with_retry(max_attempts=2, backoff_seconds=(60, 300), job_name="master_daily_report")
def _master_daily_report(email_override: str | None = None, period: str = "daily", lang: str = "ko"):
    """Master synthesis report — reads the day's Kiwoom + Newspaper + YouTube
    reports and produces one consolidated smart summary. Runs ~6:50 AM KST."""
    from services.master_report import build_master_report
    from services.kiwoom_report import format_report_telegram
    from services.telegram_service import send_alert
    from db.models import OrchReport

    db = SessionLocal()
    trace = f"tr-master-{int(datetime.utcnow().timestamp())}"
    kst = kst_label()
    try:
        rep = build_master_report(db, trace)
        r = OrchReport(
            report_type="master_report",
            source_run_ids_json=[],
            content_json={
                "report_type": "master_report", "period": period,
                "executive_summary": rep.get("summary_en") or "Master daily summary",
                "sections": [{"title": "Master Summary", "content": rep.get("table_en", ""), "data": {}}],
                "report": rep,
                "generated_at": datetime.utcnow().isoformat(), "kst_time": kst,
            },
            delivery_channel="auto",
        )
        db.add(r)
        db.commit()

        try:
            for chunk in format_report_telegram(rep, kst, lang="ko",
                                                title="Daily Recommendation", emoji="💡"):
                send_alert(chunk)
        except Exception as te:
            log.warning(f"master telegram format failed: {te}")
            send_alert(f"💡 <b>Daily Recommendation</b>\n<i>{kst}</i>\n\n"
                       f"{rep.get('summary_en', '')[:300]}\n\n<i>View → Reports</i>")

        # Consolidated 'Hello Boss' email: ALL 4 reports (Korean) in one message
        # with a brief intro. This is the single daily delivery.
        try:
            from services.report_docx import markdown_to_docx
            from services.report_email import (send_email_with_docs,
                                               is_configured as _email_ok, default_recipients)
            from services.master_report import _latest_report
            if email_override == "*ALL*":
                recipients = default_recipients()
            elif email_override:
                recipients = [email_override]   # safe single-recipient test
            elif os.getenv("MASTER_REPORT_EMAIL"):
                recipients = [os.getenv("MASTER_REPORT_EMAIL")]
            else:
                recipients = default_recipients()

            if _email_ok() and recipients:
                ymd = (datetime.utcnow().strftime("%Y%m%d"))

                def _ko(rp):
                    # Default Korean; English only when explicitly requested (lang=en).
                    if lang == "en":
                        return (rp or {}).get("detail_en") or (rp or {}).get("detail_ko") or ""
                    md = (rp or {}).get("detail_ko") or (rp or {}).get("detail_en") or ""
                    if len(md.strip()) < 200 or "same report in korean" in md.lower():
                        md = (rp or {}).get("detail_en") or md
                    return md

                # Each report now goes out as its OWN separate email — Kiwoom,
                # Newspaper and YouTube are emailed by their own jobs. This master
                # email carries ONLY the consolidated Recommendation report (which
                # still synthesizes all of them in its content).
                _kor = {"daily": "데일리", "weekly": "주간", "monthly": "월간"}.get(period, "데일리")
                md = _ko(rep)
                # Prepend the ML model's BUY/SELL picks (read-only from model_predictions).
                try:
                    from services import prediction_service as _ps
                    _s = _ps.summary(db)
                    if _s.get("buys") or _s.get("sells"):
                        ln = [f"## 🤖 AI 예측 — 오늘의 매매 신호 (5일 모델, {_s.get('as_of')})",
                              "_per-stock ML 모델 추정치 · 투자 권유 아님 · 모델이 기준선을 이긴 종목만 표시 · "
                              "각 종목 백테스트 정확도 병기_", ""]
                        if _s.get("buys"):
                            ln.append("**📈 매수 후보 (BUY):**")
                            for p in _s["buys"]:
                                ln.append(f"- {p['name']} — 신뢰도 {p['confidence']} · 예상 ±{p.get('expected_high_pct')}%(추정)"
                                          f" · 백테스트 정확도 {round((p.get('backtest_acc') or 0)*100,1)}%")
                        if _s.get("sells"):
                            ln.append("\n**📉 매도/주의 (SELL):**")
                            for p in _s["sells"]:
                                ln.append(f"- {p['name']} — 신뢰도 {p['confidence']}"
                                          f" · 백테스트 정확도 {round((p.get('backtest_acc') or 0)*100,1)}%")
                        md = "\n".join(ln) + "\n\n---\n\n" + (md or "")
                except Exception as _pe:
                    log.warning(f"master: ML picks section skipped: {str(_pe)[:80]}")
                # Prepend overnight notable events that did NOT warrant a separate email
                # (sev < 7 or over the daily cap) — folded into the morning report instead.
                try:
                    from services.breaking_report import recent_events_digest
                    _evs = [e for e in recent_events_digest(db, hours=20) if not e["emailed"]]
                    if _evs:
                        bl = ["## 📰 간밤 주요 이벤트 요약 (개별 속보 미발송분)",
                              "_심각도 7 미만 또는 일일 발송 한도 초과로 즉시 발송하지 않은 시장 이벤트입니다._", ""]
                        for e in _evs[:10]:
                            bl.append(f"- (severity {e['severity']}/10) {e['title']}")
                        md = "\n".join(bl) + "\n\n---\n\n" + (md or "")
                except Exception as _be:
                    log.warning(f"master: breaking digest skipped: {str(_be)[:80]}")
                files = []
                if md:
                    files.append((f"추천_Recommendation_{ymd}.docx",
                                  markdown_to_docx(md, "종합 추천 리포트", kst)))
                intro = (
                    "안녕하세요 사장님,\n\n"
                    f"{kst} 기준 {_kor} 종합 추천 리포트를 보내드립니다 — 키움·신문·유튜브 리포트를 "
                    "종합한 투자 의견 및 일정매매(이벤트 기반) 포인트입니다.\n\n"
                    "첨부된 Word 파일을 확인해 주세요.\n\n감사합니다.\nTripleH AI"
                )
                res = send_email_with_docs(
                    recipients, f"[TripleH] {_kor} 종합 추천 리포트 — {kst}", intro, files)
                log.info(f"master: consolidated email {'sent' if res.get('ok') else 'skipped'} "
                         f"({len(files)} files, {len(recipients)} recipient(s)) "
                         f"({res.get('reason', 'ok')})",
                         extra={"trace_id": trace, "action": "master.email"})
            else:
                log.info("master: email skipped (SMTP not configured or no recipient)",
                         extra={"action": "master.email.skip"})
        except Exception as ee:
            log.warning(f"master: email step failed: {ee}", extra={"action": "master.email.failed"})

        log.info(f"master: daily report saved + sent ({rep['status']})",
                 extra={"trace_id": trace, "action": "master.daily.done"})
    except Exception as e:
        log.warning(f"master: daily report failed: {e}", extra={"action": "master.daily.failed"})
    finally:
        db.close()


def _single_agent_report(agent_type: str):
    """Generate ONE agent's daily report (asset / stock / realty) on demand and
    save it to the dashboard + Telegram. Used by the Agents submenu."""
    from services.agent_report_builder import (build_asset_report, build_stock_report,
                                               build_realty_report, report_sections, format_telegram)
    from services.telegram_service import send_alert
    from db.models import OrchReport
    builders = {"asset": build_asset_report, "stock": build_stock_report, "realty": build_realty_report}
    fn = builders.get(agent_type)
    if not fn:
        log.warning(f"single-agent: unknown agent {agent_type}")
        return
    db = SessionLocal()
    trace = f"tr-agent-{agent_type}-{int(datetime.utcnow().timestamp())}"
    kst = kst_label()
    try:
        rep = fn(db, trace)
        r = OrchReport(
            report_type=f"agent_daily_{agent_type}",
            source_run_ids_json=[],
            content_json={
                "report_type": f"agent_daily_{agent_type}",
                "executive_summary": rep.get("summary") or f"{rep.get('name', agent_type)} report",
                "sections": report_sections(rep), "agent": rep.get("name"),
                "status": rep.get("status"), "report": rep,
                "generated_at": datetime.utcnow().isoformat(), "kst_time": kst,
            },
            delivery_channel="auto",
        )
        db.add(r)
        db.commit()
        try:
            send_alert(format_telegram(rep, kst))
        except Exception:
            pass
        log.info(f"single-agent: {agent_type} report generated", extra={"trace_id": trace, "action": "agent.single.done"})
    except Exception as e:
        log.warning(f"single-agent: {agent_type} failed: {str(e)[:120]}", extra={"action": "agent.single.failed"})
    finally:
        db.close()


def _master_daily_all():
    """Scheduled 6:50 AM KST consolidated email — ALWAYS to the full recipient
    list (default_recipients / REPORT_RECIPIENTS), never a single test address."""
    _master_daily_report(email_override="*ALL*")


def _asset_daily_all():
    """Scheduled 7:00 AM KST — build the detailed Asset report and send it as its
    OWN standalone email (Korean .docx) to the FULL recipient list."""
    _asset_daily_report(email_override="*ALL*")


def _realty_daily_all():
    """Scheduled 7:05 AM KST — build the Real Estate report and send it as its
    OWN standalone email (Korean .docx) to the FULL recipient list.
    (Was dashboard-only for a while; re-enabled 2026-08-19 when the boss set the
    morning lineup to exactly 5 emailed reports, Real Estate among them.)"""
    _realty_daily_report(email_override="*ALL*")


def _kiwoom_daily_all():
    """Kiwoom report as its OWN email to the full recipient list."""
    _kiwoom_daily_report(email_override="*ALL*")


def _newspaper_daily_all():
    """Newspaper report as its OWN email to the full recipient list."""
    _newspaper_daily_report(email_override="*ALL*")


def _youtube_daily_all():
    """YouTube grounded report as its OWN email to the full recipient list."""
    _youtube_daily_report(email_override="*ALL*")


# Weekend (KST Sat+Sun) WEEKLY editions of the 4 market reports — KRX is closed,
# so instead of an empty daily snapshot the boss gets a weekly-labelled report
# (latest available market data) to the full recipient list.
def _kiwoom_weekly_all():
    _kiwoom_daily_report(email_override="*ALL*", period="weekly")


def _newspaper_weekly_all():
    _newspaper_daily_report(email_override="*ALL*", period="weekly")


def _youtube_weekly_all():
    _youtube_daily_report(email_override="*ALL*", period="weekly")


def _master_weekly_all():
    _master_daily_report(email_override="*ALL*", period="weekly")


@with_retry(max_attempts=1, backoff_seconds=(), job_name="ensure_morning_reports",
            alert_on_final_failure=False)
def _ensure_morning_reports():
    """Self-healing report health check — runs 8:00 AM, 11:15 AM and 5:00 PM KST.
    (1) If any expected daily report is MISSING from the dashboard, generate it now
        (idempotent — present reports are skipped, so no duplicates / double-emails).
    (2) If today's cross-agent report is missing or contains an ERROR marker, it is
        regenerated (Telegram/dashboard only — no email spam).
    (3) News-freshness watchdog: if today's newspaper report fetched 0 articles or
        its freshest article is >3 days old, it is regenerated (re-fetches live news).
    This is what makes the reports self-correcting without anyone asking."""
    from db.models import OrchReport
    from services.kst import kst_date, kst_now

    db = SessionLocal()
    try:
        today = kst_date()                      # 'YYYY-MM-DD' (KST)
        weekend = kst_now().weekday() >= 5      # 5=Sat, 6=Sun
        market_period = "weekly" if weekend else "daily"

        def present(rtype: str, period: str | None) -> bool:
            q = db.query(OrchReport).filter(
                OrchReport.report_type == rtype,
                OrchReport.content_json["kst_time"].astext.like(f"{today}%"),
            )
            if period:
                q = q.filter(OrchReport.content_json["period"].astext == period)
            return db.query(q.exists()).scalar()

        # Market reports: daily on KST weekdays, weekly on KST weekends.
        # (Master + YouTube left the morning lineup 2026-08-19 — 5 reports now.)
        market = [
            ("kiwoom_report", _kiwoom_daily_all, _kiwoom_weekly_all),
            ("newspaper_report", _newspaper_daily_all, _newspaper_weekly_all),
        ]
        for rtype, daily_fn, weekly_fn in market:
            if not present(rtype, market_period):
                log.warning(f"ensure: {rtype} missing for {today} ({market_period}) — generating now",
                            extra={"action": "ensure.backfill", "report": rtype})
                (weekly_fn if weekend else daily_fn)()

        # Asset + Real Estate: daily every day (incl. weekends).
        for rtype, fn in (("asset_report", _asset_daily_all), ("realty_report", _realty_daily_all)):
            if not present(rtype, "daily"):
                log.warning(f"ensure: {rtype} missing for {today} — generating now",
                            extra={"action": "ensure.backfill", "report": rtype})
                fn()

        # (Recommendation self-heal removed with the report's retirement from the
        # morning lineup, 2026-08-19 evening — no scheduled send means nothing to
        # backfill. Restore alongside the registration if it ever returns.)

        # Cross-agent report: regenerate if today's is MISSING or contains an ERROR
        # marker (e.g. a transient agent fetch failure). It's Telegram/dashboard only
        # (no email), so regenerating is safe — no inbox spam.
        try:
            _ERR = ("Requester agent not found", "Failed to fetch", "No module named",
                    "[LLM unavailable]", "Traceback (", "Adapter error")
            xrow = (db.query(OrchReport)
                    .filter(OrchReport.report_type == "cross_agent_summary")
                    .order_by(OrchReport.created_at.desc()).first())
            xtoday = bool(xrow and xrow.created_at
                          and (xrow.created_at + timedelta(hours=9)).strftime("%Y-%m-%d") == today)
            xerr = any(m in str(xrow.content_json) for m in _ERR) if xrow else False
            if (not xtoday) or xerr:
                from services.report_service import compose_cross_agent_report
                compose_cross_agent_report(db, agent_types=["asset", "stock", "realty"],
                                           trace_id=f"ensure-cross-{today}")
                log.warning(f"ensure: cross-agent report regenerated "
                            f"({'errored' if xerr else 'stale/missing'})",
                            extra={"action": "ensure.cross_regen"})
        except Exception as ce:
            log.warning(f"ensure: cross-agent regen failed: {str(ce)[:120]}",
                        extra={"action": "ensure.cross_regen_failed"})

        # News freshness watchdog — the newspaper report must carry TODAY's news.
        # If today's report is present but the live fetch failed (0 articles) or its
        # freshest article is >3 days old, regenerate it (re-fetches live news + re-
        # sends). Only fires on a genuine break, so no false re-emails on normal days.
        try:
            import re as _re
            from datetime import date as _date
            nrow = (db.query(OrchReport)
                    .filter(OrchReport.report_type == "newspaper_report",
                            OrchReport.content_json["period"].astext == market_period)
                    .order_by(OrchReport.created_at.desc()).first())
            n_today = bool(nrow and (nrow.content_json or {})
                           and str((nrow.content_json or {}).get("kst_time") or "").startswith(today))
            if n_today:  # missing case is already handled by the backfill loop above
                rep = (nrow.content_json.get("report") or {})
                fs = rep.get("fetch_stats") or {}
                total = sum(v.get("n", 0) for v in fs.values() if isinstance(v, dict))
                dk = (rep.get("detail_ko") or "") + (rep.get("detail_en") or "")
                dates = _re.findall(r"20\d\d-\d\d-\d\d", dk)
                newest_age = 999
                if dates:
                    try:
                        newest_age = (kst_now().date() - _date.fromisoformat(max(dates))).days
                    except Exception:
                        newest_age = 999
                if total == 0 or newest_age > 3:
                    log.warning(f"ensure: newspaper news STALE/empty for {today} "
                                f"(articles={total}, newest_age={newest_age}d) — regenerating",
                                extra={"action": "ensure.news_stale"})
                    (_newspaper_weekly_all if weekend else _newspaper_daily_all)()
        except Exception as ne:
            log.warning(f"ensure: news freshness check failed: {str(ne)[:120]}",
                        extra={"action": "ensure.news_check_failed"})

        # (YouTube staleness check removed 2026-08-19 with the report itself —
        # the external GPU pipeline had been dead since 07-03 and the boss cut
        # YouTube from the morning lineup.)

        log.info(f"ensure: report health check done for {today} ({'weekend' if weekend else 'weekday'})",
                 extra={"action": "ensure.done"})
    except Exception as e:
        log.warning(f"ensure: morning-report safety net failed: {e}", extra={"action": "ensure.failed"})
    finally:
        db.close()


def _watchdog_morning_reports():
    """08:30 KST safety-net (registered 2026-07-29 after a week of silent
    report failure went unnoticed). VERIFIES that today's morning reports
    actually reached the dashboard/DB — the SAME rows the 8:00 self-heal writes.
    If any are still MISSING (after the 6:30-8:00 sends + the 8:00 self-heal),
    it sends ONE alert email to the boss + a Telegram ping, so a silent break is
    caught the same morning instead of a week later.

    Read-only: it does NOT regenerate reports (the self-heal already tried) — it
    only raises the flag. Guarded by REPORTS_ENABLED (registered via
    _add_report_job) so only the designated sender ever alerts."""
    from db.models import OrchReport
    from services.kst import kst_date, kst_now
    from services.report_email import send_plain_email, sender_address, DEFAULT_RECIPIENT

    db = SessionLocal()
    try:
        today = kst_date()                      # 'YYYY-MM-DD' (KST)
        weekend = kst_now().weekday() >= 5
        period = "weekly" if weekend else "daily"

        def present(rtype: str, per: str | None) -> bool:
            q = db.query(OrchReport).filter(
                OrchReport.report_type == rtype,
                OrchReport.content_json["kst_time"].astext.like(f"{today}%"),
            )
            if per:
                q = q.filter(OrchReport.content_json["period"].astext == per)
            return db.query(q.exists()).scalar()

        # The morning emails the team expects — the boss's 4-report lineup
        # (2026-08-19): Kiwoom + Newspaper (daily on weekdays, weekly edition on
        # weekends), Asset + Real Estate (every day). Master, YouTube and (same
        # evening) Recommendation left the lineup on 08-19.
        expected = [
            ("kiwoom_report", period, "Kiwoom"),
            ("newspaper_report", period, "Newspaper"),
            ("asset_report", "daily", "Asset"),
            ("realty_report", "daily", "Real Estate"),
        ]
        missing = [label for rtype, per, label in expected if not present(rtype, per)]

        stale: list[str] = []   # (YouTube stale-source check retired with the report)

        if not missing and not stale:
            log.info(f"watchdog: all morning reports present for {today}",
                     extra={"action": "watchdog.ok"})
            return

        # Missing/stale after the self-heal → alert the boss ONCE (email + Telegram).
        boss = os.getenv("WATCHDOG_ALERT_EMAIL") or sender_address() or DEFAULT_RECIPIENT
        headline = "INCOMPLETE" if missing else "STALE SOURCE"
        subject = f"⚠️ VIP morning reports {headline} — {today} KST"
        body = (
            f"The 08:30 KST watchdog found a problem with today's morning reports.\n\n"
            + (f"MISSING (not in the dashboard/DB even after the 08:00 self-heal):\n"
               f"  {', '.join(missing)}\n\n" if missing else "")
            + (f"STALE (present but the underlying source has stopped updating —\n"
               f"the self-heal CANNOT regenerate these, they need a human):\n"
               f"  {', '.join(stale)}\n\n" if stale else "")
            + "Likely causes: backend was down at 06:30, a source API failed, or "
            "SMTP rejected. Check logs\\backend.err.log around 06:30-08:00 KST.\n"
            "Re-send manually from Reports -> Generate, or "
            "POST /reports/compose/<name>?send_all=true.\n"
        )
        try:
            res = send_plain_email(boss, subject, body)
            log.warning(
                f"watchdog: morning reports {headline} missing={missing} stale={stale} — "
                f"alert email {'sent' if res.get('ok') else 'FAILED'} -> {boss} "
                f"({res.get('reason', 'ok')})",
                extra={"action": "watchdog.alert"})
        except Exception as ee:
            log.warning(f"watchdog: alert email failed: {ee}",
                        extra={"action": "watchdog.alert_failed"})
        try:
            from services.telegram_service import send_alert
            send_alert(f"⚠️ <b>VIP morning reports {headline.lower()}</b> — {today}\n"
                       + (f"Missing: {', '.join(missing)}\n" if missing else "")
                       + (f"Stale: {', '.join(stale)}" if stale else ""))
        except Exception:
            pass
    except Exception as e:
        log.warning(f"watchdog: check failed: {e}", extra={"action": "watchdog.failed"})
    finally:
        db.close()


@_single_flight("scorekeeper")
def _scorekeeper_daily():
    """Daily: log today's BUY/SELL calls (both methods) + grade matured ones -> the
    track record (win rate / avg return) the scoreboard shows. Builds the evidence base."""
    db = SessionLocal()
    try:
        from services.scorekeeper_service import log_today, grade_matured
        lg = log_today(db)
        gr = grade_matured(db)
        log.info(f"scorekeeper daily: log={lg} grade={gr}", extra={"action": "scorekeeper.daily"})
    except Exception as e:
        log.warning(f"scorekeeper daily failed: {str(e)[:100]}")
    finally:
        db.close()


@_single_flight("intraday_forecast_tick")
def _intraday_forecast_tick():
    """Hourly during market: predict next ~1h with BOTH methods + grade matured ones.
    Builds the per-method per-hour accuracy that the morning email reports."""
    db = SessionLocal()
    try:
        from services.intraday_forecast import tick
        r = tick(db)
        log.info(f"intraday forecast tick: {r}", extra={"action": "intraday.tick"})
    except Exception as e:
        log.warning(f"intraday forecast tick failed: {str(e)[:120]}")
    finally:
        db.close()


@_single_flight("paper_trader_tick")
def _paper_trader_tick():
    """Every 5 min during market: the paper-trader closes hit/matured virtual trades and
    opens new ones from the bot's own signals — evidence volume for the readiness gate."""
    from db.base import SessionLocal
    _db = SessionLocal()
    try:
        from services.paper_trader import tick as _pt
        r = _pt(_db)
        if r.get("opened") or r.get("closed"):
            log.info(f"paper trader: {r}", extra={"action": "paper.tick"})
    except Exception as e:
        log.warning(f"paper trader tick failed: {str(e)[:120]}")
    finally:
        _db.close()


@_single_flight("paper_morning_report")
def _paper_morning_report():
    """08:20 KST: yesterday's virtual P&L scorecard — 'if you had followed the bot'."""
    from db.base import SessionLocal
    _db = SessionLocal()
    try:
        from services.paper_trader import morning_report as _pm
        r = _pm(_db)
        log.info(f"paper morning report: sent={r.get('sent')}", extra={"action": "paper.report"})
    except Exception as e:
        log.warning(f"paper morning report failed: {str(e)[:120]}")
    finally:
        _db.close()


@_single_flight("self_tune_nightly")
def _self_tune_nightly():
    """Autopilot B — after close: path-accurate replay on the banked 5-min series, tune
    the dip-bounce params within rails (±0.25/night), write the note for the morning email."""
    from db.base import SessionLocal
    _db = SessionLocal()
    try:
        from services.self_tune import run as _st
        r = _st(_db)
        log.info(f"self-tune: {r.get('note')}", extra={"action": "self_tune.nightly"})
    except Exception as e:
        log.warning(f"self-tune failed: {str(e)[:120]}")
    finally:
        _db.close()


@_single_flight("dip_alert_pass")
def _dip_alert_pass():
    """Every 10 min during market: proactive dip-bounce alerts (the boss's own strategy)
    — scan for ≥1.5%/1h dips with tape confirmation; email NEW candidates (DB-deduped
    2h/ticker, daily cap, owner-only pilot). Candidates are auto-graded."""
    from db.base import SessionLocal
    _db = SessionLocal()
    try:
        from services.dip_alert import run as _dar
        r = _dar(_db)
        if r.get("new_alerts"):
            log.info(f"dip alert: {r}", extra={"action": "dip_alert.tick"})
    except Exception as e:
        log.warning(f"dip alert tick failed: {str(e)[:120]}")
    finally:
        _db.close()


@_single_flight("cloud_collector_pass")
def _cloud_collector_pass():
    """Every 2 min during market: server-side Kiwoom collection pass — takes over
    automatically whenever the PC collector isn't writing (fresh-snapshot skip)."""
    try:
        from services.cloud_collector import run_pass
        r = run_pass()
        if r.get("started"):
            log.info(f"cloud collector: {r}", extra={"action": "collector.cloud_tick"})
    except Exception as e:
        log.warning(f"cloud collector tick failed: {str(e)[:120]}")


@_single_flight("intraday_snapshot_bank")
def _intraday_snapshot_bank():
    """Every 5 min during market: append fresh order-flow snapshots to history —
    the training series the future next-30-min model needs (B2 prep)."""
    db = SessionLocal()
    try:
        from services.snapshot_bank import bank
        r = bank(db)
        if r.get("banked_now"):
            log.info(f"snapshot bank: {r}", extra={"action": "intraday.bank"})
    except Exception as e:
        log.warning(f"snapshot bank failed: {str(e)[:120]}")
    finally:
        db.close()


@_single_flight("intraday_morning_report")
def _intraday_morning_report():
    """Before market open: email yesterday's hourly accuracy scorecard (.docx)."""
    db = SessionLocal()
    try:
        from services.intraday_forecast import morning_report
        r = morning_report(db)
        log.info(f"intraday morning report: {r}", extra={"action": "intraday.morning_report"})
    except Exception as e:
        log.warning(f"intraday morning report failed: {str(e)[:120]}")
    finally:
        db.close()


@_single_flight("story_monitor")
def _story_monitor_daily():
    """Daily: track major-news stories (incl. the 3 Mega Projects) + email management an
    executive brief when there are NEW developments."""
    db = SessionLocal()
    try:
        from services.story_tracker import seed_story, daily_monitor, MEGA_PROJECTS as M
        seed_story(db, M["key"], M["topic"], M["queries"], M["tickers"])   # idempotent
        r = daily_monitor(db, email=True)
        log.info(f"story monitor: {r}", extra={"action": "story.monitor"})
    except Exception as e:
        log.warning(f"story monitor failed: {str(e)[:120]}")
    finally:
        db.close()


@_single_flight("dart_disclosures")
def _dart_disclosures_daily():
    """Daily: pull official DART disclosures (실적/수주/유증) -> raw_disclosures. The
    highest-signal 'effective news' for the Daily Trading brief. No-ops without DART_API_KEY."""
    db = SessionLocal()
    try:
        from services.dart_collector import collect
        res = collect(db, days=1)
        log.info(f"dart disclosures daily done: {res}", extra={"action": "dart.daily"})
    except Exception as e:
        log.warning(f"dart disclosures daily failed: {str(e)[:100]}")
    finally:
        db.close()


@_single_flight("news_sentiment")
def _news_sentiment_daily():
    """Daily: collect per-stock news + LLM sentiment into raw_news — accumulates the
    ML training data we currently lack (so we can later retrain WITH the news edge)."""
    db = SessionLocal()
    try:
        from services.news_sentiment_collector import collect_all
        res = collect_all(db)
        log.info(f"news-sentiment daily done: {res}", extra={"action": "news_sentiment.daily"})
    except Exception as e:
        log.warning(f"news-sentiment daily failed: {str(e)[:100]}")
    finally:
        db.close()


# --- Breaking-news monitor: every 15 min, fire on genuinely big NEW events ---
_BREAKING_SEEN: dict[str, float] = {}      # event key -> last-alerted epoch
_BREAKING_SEEN_TTL = 12 * 3600             # don't re-alert the same event within 12h
_BREAKING_DAY = {"day": "", "count": 0}    # per-KST-day email counter
_BREAKING_CAP = 3                          # max immediate emails per day (anti-spam)
_BREAKING_MIN_SEV = 7                      # only fire on severity >= this


def _breaking_monitor():
    """Every 15 min: cheap-detect NEW market-moving events (Korean + international
    news). When one scores >= the severity threshold, build the FULL breaking
    report and email ALL recipients + Telegram — de-duplicated (12h TTL) and capped
    per day so it stays high-signal, not spam."""
    import time as _t
    try:
        from services.breaking_report import triage_events, record_event, mark_emailed
        from services.kst import kst_date as _kd
        # Env-tunable at call time (no redeploy needed). Defaults: EMAIL only on BIG
        # events (sev>=7), max 2/day. EVERYTHING notable (sev>=collect) is still RECORDED
        # for the morning digest + data — the medium events ride the morning report, not
        # a separate email.
        # EMAIL only big events (sev>=7), max 2/day NORMALLY; very-urgent (sev>=9) can
        # push the day's total up to 4. COLLECT everything notable (>=5) for the morning
        # digest. Cap + dedup are DB-backed (breaking_events table) so they SURVIVE Render
        # restarts — the old in-memory counter reset on every restart, which is why >6
        # emails leaked through.
        from sqlalchemy import text as _text
        email_sev = int(os.getenv("BREAKING_MIN_SEV", "7") or 7)
        urgent_sev = int(os.getenv("BREAKING_URGENT_SEV", "9") or 9)
        cap = int(os.getenv("BREAKING_CAP", "2") or 2)
        urgent_cap = int(os.getenv("BREAKING_URGENT_CAP", "4") or 4)
        collect_sev = int(os.getenv("BREAKING_COLLECT_SEV", "5") or 5)
        target = os.getenv("BREAKING_MONITOR_EMAIL") or "*ALL*"
        day = _kd()

        db = SessionLocal()
        try:
            from services.breaking_report import _ensure_events_table
            _ensure_events_table(db)
            # DB-backed dedup: event keys seen in the last 12h (survives restarts).
            seen = {r[0] for r in db.execute(_text(
                "SELECT event_key FROM breaking_events WHERE ts > now() - interval '12 hours'"))}
            events = triage_events(seen_keys=seen, min_sev=collect_sev)
            if not events:
                return
            # 1) RECORD every new notable event (morning digest + data history).
            for ev in events:
                record_event(db, day, ev, emailed=False)
            # 2) EMAIL big ones, DB-counted cap (normal 2, very-urgent up to 4).
            #    MACRO/geopolitical events (Iran/Israel/oil/Ukraine/trade war) are
            #    UNCAPPED + a lower bar (user wants them sent whenever they appear);
            #    dedup (12h key) still prevents resending the SAME event.
            import re as _re
            _MACRO_RE = _re.compile(
                r"iran|israel|lebanon|hezbollah|hormuz|gaza|syria|시리아|중동|"
                r"oil|crude|brent|wti|유가|원유|opec|"
                r"ukraine|russia|우크라이나|러시아|전쟁|\bwar\b|geopolit|지정학|"
                r"tariff|관세|trade\s*war|무역분쟁|무역전쟁", _re.I)
            macro_min_sev = int(os.getenv("BREAKING_MACRO_MIN_SEV", "5") or 5)

            def _is_macro(ev):
                return bool(_MACRO_RE.search(ev.get("title", "") + " " + ev.get("theme", "")))
            macro_events = [e for e in events if _is_macro(e) and e["severity"] >= macro_min_sev]
            stock_events = [e for e in events if not _is_macro(e)]

            # MACRO/geopolitical: ONE consolidated KO+EN report covering all NEW macro
            # events together (no separate spam), UNCAPPED, to all 7. Dedup (12h key)
            # stops resending the same event.
            if macro_events:
                for ev in macro_events:
                    mark_emailed(db, ev["key"])
                focus = "MACRO/지정학·유가·무역 속보 — " + " | ".join(e["title"] for e in macro_events[:5])
                log.info(f"breaking-monitor: MACRO consolidated ({len(macro_events)} events) -> {target}",
                         extra={"action": "breaking.monitor.macro"})
                _breaking_report(email_override=target, focus=focus)

            # STOCK events: keep the high-signal daily cap (normal 2, very-urgent 4).
            emailed_today = db.execute(_text(
                "SELECT count(*) FROM breaking_events WHERE kst_date=:d AND emailed=true"),
                {"d": day}).scalar() or 0
            for ev in stock_events:
                if ev["severity"] < email_sev:
                    continue
                limit = urgent_cap if ev["severity"] >= urgent_sev else cap
                if emailed_today >= limit:
                    continue
                mark_emailed(db, ev["key"])
                emailed_today += 1
                log.info(f"breaking-monitor: email sev {ev['severity']} ({emailed_today}/{limit}) — "
                         f"{ev['title'][:70]} -> {target}",
                         extra={"action": "breaking.monitor.fire"})
                _breaking_report(email_override=target, focus=ev["title"])
        finally:
            db.close()
    except Exception as e:
        log.warning(f"breaking-monitor failed: {str(e)[:120]}",
                    extra={"action": "breaking.monitor.failed"})


def _knowledge_sync_job():
    """Feed the RAG knowledge base with the day's fresh reports (Phase 2), so the
    chatbot grounds answers in real content. Runs after the morning reports."""
    from db.base import SessionLocal
    db = SessionLocal()
    try:
        from services.knowledge_sync import seed_data_dictionary, sync_reports_to_kb
        seed_data_dictionary(db, agent_ids=("stock", "vip"))
        res = {aid: sync_reports_to_kb(db, agent_id=aid) for aid in ("stock", "vip")}
        log.info(f"scheduler: knowledge sync done {res}", extra={"action": "scheduler.knowledge_sync"})
    except Exception as e:
        log.warning(f"scheduler: knowledge sync failed: {str(e)[:120]}", extra={"action": "scheduler.knowledge_sync_failed"})
    finally:
        db.close()


@_single_flight("allreports")
def run_all_reports_now(email_override: str | None = None, lang: str = "ko"):
    """On-demand: generate ALL 4 reports with the freshest data RIGHT NOW, then
    the master sends the consolidated email. Runs the sources first (so the master
    reads fresh ones), then the master. Used by the 'Generate Now' button.
    `email_override` (optional) sends only to that address (test); otherwise the
    master emails the full recipient list. `lang` controls the email language
    (default 'ko' = Korean only; 'en' for English)."""
    log.info("run-all: on-demand generation started", extra={"action": "runall.start"})
    for fn, label in ((_kiwoom_daily_report, "kiwoom"),
                      (_newspaper_daily_report, "newspaper")):
        try:
            fn(lang=lang)  # sources save to dashboard (individual email stays off)
        except Exception as e:
            log.warning(f"run-all: {label} failed: {str(e)[:120]}", extra={"action": "runall.src.failed"})
    # (The grounded YouTube report is now bundled INTO the consolidated master
    # email below — no separate YouTube email.)
    try:
        _master_daily_report(email_override=email_override, lang=lang)  # emails the consolidated 4-file
    except Exception as e:
        log.warning(f"run-all: master failed: {str(e)[:120]}", extra={"action": "runall.master.failed"})
    log.info("run-all: on-demand generation done", extra={"action": "runall.done"})


def init_scheduler():
    """Initialize the scheduler and load enabled rules from DB."""
    global _scheduler

    if _scheduler and _scheduler.running:
        return

    # PIN the scheduler to KST. Without an explicit timezone APScheduler adopts the
    # HOST's local zone, so every bare `from_crontab(...)` below silently meant a
    # different wall-clock hour on Render (UTC container) than on the Korean office
    # PC — the 07:00 KST Asset report was firing at 22:00 KST, and the "15:30 KST"
    # market-close capture was running at 06:30 KST (pre-open). The whole schedule is
    # defined against Korean business hours, so it is pinned here and every trigger
    # below states its KST time literally.
    _scheduler = BackgroundScheduler(daemon=True, timezone=_KST_TZ)

    if not REPORTS_ENABLED:
        log.info("scheduler: reports disabled on this instance (REPORTS_ENABLED=false) — "
                 "outbound report/email jobs will NOT be registered; another instance "
                 "sends them. Trading, guard, grader, and collector jobs still run.",
                 extra={"action": "scheduler.reports_disabled"})

    _load_rules_from_db()

    # Add agent health check — every 5 minutes
    _scheduler.add_job(
        _execute_health_check,
        CronTrigger.from_crontab("*/5 * * * *"),
        id="agent-health-check",
        replace_existing=True,
    )
    log.info("scheduler: health check registered (every 5 min)", extra={"action": "scheduler.health_registered"})

    # 🛡️ POSITION-GUARD HEARTBEAT — every 60s during KST market hours (boss 2026-07-13:
    # LG화학 was cut at -2.65% because the guard's pulse depended on an open browser
    # page / the 5-min cron; this makes the -1%/-2%/trail lines fire on time, always).
    def _guard_heartbeat():
        from datetime import datetime, timedelta, timezone
        kst = datetime.now(timezone(timedelta(hours=9)))
        if kst.weekday() >= 5 or not (9 * 60 <= kst.hour * 60 + kst.minute <= 15 * 60 + 30):
            return
        db = SessionLocal()
        try:
            from services.position_guard import run as _grun
            _grun(db)
        except Exception as e:
            log.warning(f"guard heartbeat failed: {str(e)[:120]}")
        finally:
            db.close()

    _scheduler.add_job(
        _guard_heartbeat,
        "interval", seconds=60,
        id="position-guard-heartbeat",
        replace_existing=True,
        max_instances=1, coalesce=True,
    )
    log.info("scheduler: position-guard heartbeat registered (60s, market hours)")

    # ⚡ ALGORITHM-2 SCALP HEARTBEAT — every 15s during KST market hours (boss
    # 2026-07-14 ripple scalper: buy the upturn, sell the small win, repeat).
    # Also fills manual-mode auto-sell LIMIT orders server-side. tick() itself
    # gates on market hours + its own ON/OFF switch; this only gates the days.
    def _scalp_heartbeat():
        from datetime import datetime, timedelta, timezone
        kst = datetime.now(timezone(timedelta(hours=9)))
        if kst.weekday() >= 5 or not (9 * 60 <= kst.hour * 60 + kst.minute <= 15 * 60 + 25):
            return
        db = SessionLocal()
        try:
            from services.scalp_trader import tick as _stick
            r = _stick(db)
            if r.get("opened") or r.get("closed"):
                log.info(f"scalp tick: {r}", extra={"action": "scalp.tick"})
        except Exception as e:
            log.warning(f"scalp heartbeat failed: {str(e)[:120]}")
        finally:
            db.close()

    _scheduler.add_job(
        _scalp_heartbeat,
        "interval", seconds=15,
        id="scalp-heartbeat",
        replace_existing=True,
        max_instances=1, coalesce=True,
    )
    log.info("scheduler: algorithm-2 scalp heartbeat registered (15s, market hours)")

    # ⚡ 5s EXIT PULSE — take/stop watch on open scalp positions only (boss
    # 2026-07-15: a fast drop fell through the −1% line between 15s beats).
    def _scalp_exit_pulse():
        from datetime import datetime, timedelta, timezone
        kst = datetime.now(timezone(timedelta(hours=9)))
        if kst.weekday() >= 5 or not (9 * 60 <= kst.hour * 60 + kst.minute <= 15 * 60 + 25):
            return
        db = SessionLocal()
        try:
            from services.scalp_trader import exit_pulse
            r = exit_pulse(db)
            if r.get("closed"):
                log.info(f"scalp exit pulse: {r}", extra={"action": "scalp.pulse"})
        except Exception as e:
            log.warning(f"scalp exit pulse failed: {str(e)[:120]}")
        finally:
            db.close()
        # Phase A (2026-07-16): Algorithm 1 auto exits ride the same 5s pulse —
        # stops fire at −1.0% instead of slipping to −1.63% on the 5-min cron
        db2 = SessionLocal()
        try:
            from services.auto_trader import exit_pulse as _a1_pulse
            r2 = _a1_pulse(db2)
            if r2.get("closed"):
                log.info(f"algo1 exit pulse: {r2}", extra={"action": "auto.pulse"})
        except Exception as e:
            log.warning(f"algo1 exit pulse failed: {str(e)[:120]}")
        finally:
            db2.close()

    _scheduler.add_job(
        _scalp_exit_pulse,
        "interval", seconds=5,
        id="scalp-exit-pulse",
        replace_existing=True,
        max_instances=1, coalesce=True,
    )
    log.info("scheduler: algorithm-2 exit pulse registered (5s, market hours)")

    # 🌙 overnight hold-or-sell calls: grade yesterday's against today's REAL
    # open every market morning (boss 2026-07-16 — the advisor's track record)
    def _grade_overnight():
        db = SessionLocal()
        try:
            from services.overnight_gap import grade_calls
            r = grade_calls(db)
            if r.get("graded"):
                log.info(f"overnight calls graded: {r}", extra={"action": "overnight.grade"})
        except Exception as e:
            log.warning(f"overnight grading failed: {str(e)[:120]}")
        finally:
            db.close()

    _scheduler.add_job(
        _grade_overnight,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=6,
                    timezone="Asia/Seoul"),
        id="overnight-grade",
        replace_existing=True,
        max_instances=1, coalesce=True,
    )
    log.info("scheduler: overnight-call grading registered (09:06 KST Mon-Fri)")

    # 🏁 3-strategy shadow tournament (boss 2026-07-20): Algo1 / Ripple / Candle
    # trade the same basket in parallel virtual books, 30s tick during market.
    def _tournament_tick():
        from datetime import datetime, timedelta, timezone
        kst = datetime.now(timezone(timedelta(hours=9)))
        if kst.weekday() >= 5 or not (9 * 60 <= kst.hour * 60 + kst.minute <= 15 * 60 + 22):
            return
        db = SessionLocal()
        try:
            from services.strategy_tournament import tick
            r = tick(db)
            if r.get("opened") or r.get("closed"):
                log.info(f"tournament: {r}", extra={"action": "tournament.tick"})
        except Exception as e:
            log.warning(f"tournament tick failed: {str(e)[:120]}")
        finally:
            db.close()

    _scheduler.add_job(
        _tournament_tick, "interval", seconds=30,
        id="strategy-tournament", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    def _tournament_open_reset():
        from services.strategy_tournament import _reset_daily
        _reset_daily()

    _scheduler.add_job(
        _tournament_open_reset,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone="Asia/Seoul"),
        id="tournament-reset", replace_existing=True, max_instances=1, coalesce=True,
    )

    def _tournament_report():
        db = SessionLocal()
        try:
            from services.strategy_tournament import report
            txt = report(db, "ko")
            log.info(f"tournament report:\n{txt}", extra={"action": "tournament.report"})
            try:
                from services.report_email import send_plain_email, default_recipients
                for _to in default_recipients():
                    send_plain_email(_to, "🏁 오늘 전략 대결 결과 (Algo1 vs 잔물결 vs 캔들 3-2)", txt)
            except Exception as _e:
                log.warning(f"tournament email failed: {str(_e)[:120]}")
        except Exception as e:
            log.warning(f"tournament report failed: {str(e)[:120]}")
        finally:
            db.close()

    # 15:25 tournament result EMAIL — UNREGISTERED 2026-08-19 (boss: "please stop
    # sending this"). The 30s tournament tick above still runs and the results
    # stay on the dashboard; _tournament_report remains callable manually.
    log.info("scheduler: strategy tournament registered (30s tick; 15:25 result email retired 2026-08-19)")

    # Hourly snapshot capture — every hour at :05. Saves one 'part' per report
    # type (newspaper/youtube/kiwoom) WITHOUT emailing; the 6 AM build reads all
    # ~24 parts of the day and synthesises the big report. Plus daily cleanup.
    from services.hourly_capture import capture_hourly as _capture_hourly
    _scheduler.add_job(
        _capture_hourly,
        CronTrigger.from_crontab("5 * * * *"),
        id="hourly-snapshot-capture",
        replace_existing=True,
    )
    log.info("scheduler: hourly snapshot capture registered (every hour :05)",
             extra={"action": "scheduler.hourly_registered"})

    # Auto daily reports — 8:00 AM KST. Sends 3 individual agent reports + 1
    # combined summary.
    _add_report_job(
        _auto_daily_reports,
        CronTrigger(hour=8, minute=0, timezone=_KST_TZ),
        id="auto-daily-reports",
        replace_existing=True,
    )
    REPORTS_ENABLED and log.info("scheduler: auto daily reports registered (8:00 AM KST)", extra={"action": "scheduler.auto_daily_registered"})

    # Stock market-close capture — 15:30 KST, weekdays (KRX trading days). This
    # MUST run after the close: the row it writes is labelled "market_close" and
    # the 8 AM pipeline delivers it as the close-of-day snapshot.
    _scheduler.add_job(
        _capture_stock_market_close,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=_KST_TZ),
        id="stock-market-close",
        replace_existing=True,
    )
    log.info("scheduler: stock market-close capture registered (15:30 KST, Mon-Fri)", extra={"action": "scheduler.stock_close_registered"})

    # 📤 Push the day's real bars OUT to the AI Advisor — 15:40 KST, weekdays, after
    # the close capture above has settled. VIP dials out; the Advisor never dials in, so
    # the company server keeps no inbound door open. Off unless ADVISOR_PUSH_* is set,
    # and it only ever sends the tickers listed in advisor_push.symbols() — that list IS
    # the permission grant. Failures are logged and dropped: sharing data must never be
    # able to disturb trading.
    def _push_bars_to_advisor():
        try:
            from services import advisor_push
            if not advisor_push.enabled():
                return
            r = advisor_push.push_all()
            log.info(f"advisor push: {r.get('sent')}/{r.get('total')}",
                     extra={"action": "scheduler.advisor_push"})
        except Exception as e:
            log.warning(f"advisor push failed: {str(e)[:120]}",
                        extra={"action": "scheduler.advisor_push_failed"})

    _scheduler.add_job(
        _push_bars_to_advisor,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=40, timezone=_KST_TZ),
        id="advisor-bar-push",
        replace_existing=True,
        max_instances=1, coalesce=True,
    )
    log.info("scheduler: advisor bar push registered (15:40 KST, Mon-Fri)", extra={"action": "scheduler.advisor_push_registered"})

    # Autonomous A2A alerts — agents watch their own live data and proactively
    # warn each other / the boss. Hourly during KST business hours (09:00-19:00),
    # weekdays. De-duplicated so it doesn't spam.
    from services.autonomous_alerts import run_autonomous_alerts
    _add_report_job(
        run_autonomous_alerts,
        CronTrigger(day_of_week="mon-fri", hour="9-19", minute=0, timezone=_KST_TZ),
        id="autonomous-alerts",
        replace_existing=True,
    )
    REPORTS_ENABLED and log.info("scheduler: autonomous A2A alerts registered (hourly 09:00-19:00 KST, Mon-Fri)", extra={"action": "scheduler.auto_alerts_registered"})

    # Auto monthly report — 1st of the month, 8:00 AM KST.
    _add_report_job(
        _auto_monthly_report,
        CronTrigger(day=1, hour=8, minute=0, timezone=_KST_TZ),
        id="auto-monthly-report",
        replace_existing=True,
    )
    REPORTS_ENABLED and log.info("scheduler: auto monthly report registered (1st of month, 8:00 AM KST)", extra={"action": "scheduler.auto_monthly_registered"})

    # Auto cross-agent report — daily 8:30 AM KST (after the daily pipeline).
    _add_report_job(
        _auto_cross_agent_report,
        CronTrigger(hour=8, minute=30, timezone=_KST_TZ),
        id="auto-cross-agent-report",
        replace_existing=True,
    )
    REPORTS_ENABLED and log.info("scheduler: auto cross-agent report registered (daily 8:30 AM KST)", extra={"action": "scheduler.auto_cross_registered"})

    # ----- Market reports (the boss's 4-report morning lineup, 2026-08-19:
    #       Kiwoom 6:30 / Newspaper 6:32 / Asset 7:00 / Real Estate 7:05 —
    #       YouTube, the 6:50 Master email, and the 7:30 Recommendation retired) -----
    # KRX is closed on weekends, so these run the DAILY edition on KST weekdays
    # (Mon-Fri) and a WEEKLY edition on KST Sat+Sun. Scheduled in Asia/Seoul time
    # with named days so there is NO UTC rollover / dow-numbering ambiguity.

    # Kiwoom — 6:30 AM KST daily on weekdays; weekly edition on the weekend.
    _add_report_job(
        _kiwoom_daily_all,
        CronTrigger(day_of_week="mon-fri", hour=6, minute=30, timezone=_KST_TZ),
        id="kiwoom-daily-report",
        replace_existing=True,
    )
    _add_report_job(
        _kiwoom_weekly_all,
        CronTrigger(day_of_week="sat,sun", hour=6, minute=30, timezone=_KST_TZ),
        id="kiwoom-weekend-weekly",
        replace_existing=True,
    )
    REPORTS_ENABLED and log.info("scheduler: Kiwoom registered (6:30 KST — daily Mon-Fri, weekly Sat/Sun, all recipients)", extra={"action": "scheduler.kiwoom_registered"})

    # Newspaper — 6:32 AM KST daily on weekdays; weekly on the weekend.
    _add_report_job(
        _newspaper_daily_all,
        CronTrigger(day_of_week="mon-fri", hour=6, minute=32, timezone=_KST_TZ),
        id="newspaper-daily-report",
        replace_existing=True,
    )
    _add_report_job(
        _newspaper_weekly_all,
        CronTrigger(day_of_week="sat,sun", hour=6, minute=32, timezone=_KST_TZ),
        id="newspaper-weekend-weekly",
        replace_existing=True,
    )
    REPORTS_ENABLED and log.info("scheduler: Newspaper registered (6:32 KST — daily Mon-Fri, weekly Sat/Sun, all recipients)", extra={"action": "scheduler.newspaper_registered"})

    # YouTube report — REMOVED from the schedule 2026-08-19 (boss's 5-report
    # lineup). Its external GPU pipeline had been dead since 2026-07-03 anyway, so
    # the 6:40 slot produced nothing for six weeks. The service files stay for a
    # manual POST /reports/compose/youtube if the pipeline ever comes back.

    # Daily 3-method Recommendation Report — 7:30 AM KST Mon-Fri, AFTER Kiwoom(6:30)
    # and Newspaper(6:32) so their digests feed the market backdrop.
    # Owner signed off 2026-07-01 → sends to the full team (DEFAULT_RECIPIENTS).
    def _recommendation_daily():
        from db.base import SessionLocal
        from services.recommendation_report import send
        _db = SessionLocal()
        try:
            r = send(_db)
            log.info(f"scheduler: recommendation report → {r.get('to')} (picks={len(r.get('picks', []))})",
                     extra={"action": "scheduler.recommendation.done"})
        except Exception as e:
            log.warning(f"scheduler: recommendation report failed: {e}", extra={"action": "scheduler.recommendation.failed"})
        finally:
            _db.close()
    # 7:30 Recommendation email — UNREGISTERED 2026-08-19 evening (boss: "for now
    # we do not need"). The morning lineup is 4 reports (Kiwoom/Newspaper/Asset/
    # Real Estate). _recommendation_daily and recommendation_report.send() stay
    # for manual/on-demand sends; re-register here if the boss wants it back.
    log.info("scheduler: Recommendation report NOT scheduled (retired from morning lineup 2026-08-19)")

    # M1.2 — grade chatbot advice calls every 30 min during market (+ once after close),
    # so the hit-rate (chatbot_scoreboard) matures. Idempotent; external cron can also hit
    # POST /predictions/chatbot-grade in case Render is asleep.
    def _grade_chatbot_calls():
        from db.base import SessionLocal
        from services.call_grader import grade_open
        _db = SessionLocal()
        try:
            r = grade_open(_db)
            if r.get("graded"):
                log.info(f"scheduler: graded {r['graded']} chatbot calls", extra={"action": "scheduler.callgrade"})
        except Exception as e:
            log.warning(f"scheduler: chatbot grade failed: {e}", extra={"action": "scheduler.callgrade.failed"})
        finally:
            _db.close()
    _scheduler.add_job(
        _grade_chatbot_calls,
        CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/30", timezone=_KST_TZ),
        id="chatbot-call-grader", replace_existing=True,
    )
    log.info("scheduler: chatbot call-grader registered (every 30m, 9-16 KST)", extra={"action": "scheduler.callgrade_registered"})

    # Asset Agent detailed report — its OWN standalone email at 7:00 AM KST =
    # 22:00 UTC, to ALL recipients, Korean .docx (Korean-only since 2026-08-19).
    _add_report_job(
        _asset_daily_all,
        CronTrigger(hour=7, minute=0, timezone=_KST_TZ),
        id="asset-daily-report",
        replace_existing=True,
    )
    REPORTS_ENABLED and log.info("scheduler: Asset detailed report registered (7:00 AM KST, all recipients, Korean)", extra={"action": "scheduler.asset_registered"})

    # Real Estate report — 7:05 AM KST every day. Its OWN standalone email
    # (Korean .docx) to all recipients since 2026-08-19 — part of the boss's
    # 5-report morning lineup (dashboard + Telegram + email).
    _add_report_job(
        _realty_daily_all,
        CronTrigger(hour=7, minute=5, timezone=_KST_TZ),
        id="realty-daily-report",
        replace_existing=True,
    )
    REPORTS_ENABLED and log.info("scheduler: Real Estate report registered (7:05 AM KST, all recipients, Korean)", extra={"action": "scheduler.realty_registered"})

    # Breaking-news monitor — every 15 min, 24/7. Detects big NEW market-moving
    # events and fires the impact report to ALL recipients (severity-gated, deduped,
    # capped per day). Catches overnight global events (e.g. foreign defense deals).
    _add_report_job(   # EMAILS all recipients (dedup is per-process only) → guard it
        _breaking_monitor,
        CronTrigger.from_crontab("*/15 * * * *"),
        id="breaking-monitor",
        replace_existing=True,
        max_instances=1,   # never overlap a scan
        coalesce=True,
    )
    REPORTS_ENABLED and log.info("scheduler: breaking-news monitor registered (every 15 min, 24/7, severity-gated)", extra={"action": "scheduler.breaking_registered"})

    # Daily per-stock news + sentiment collector -> raw_news (ML training data).
    # 16:20 KST — AFTER the KR close, so the day's news has settled.
    _scheduler.add_job(
        _news_sentiment_daily,
        CronTrigger(hour=16, minute=20, timezone=_KST_TZ),
        id="news-sentiment-daily",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    log.info("scheduler: news-sentiment collector registered (16:20 KST daily -> raw_news)", extra={"action": "scheduler.news_sentiment_registered"})

    # Daily DART disclosures -> raw_disclosures (Daily Trading brief's effective-news).
    # 16:35 KST. No-ops without DART_API_KEY.
    _scheduler.add_job(
        _dart_disclosures_daily,
        CronTrigger(hour=16, minute=35, timezone=_KST_TZ),
        id="dart-disclosures-daily",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    log.info("scheduler: DART disclosures collector registered (16:35 KST daily -> raw_disclosures)", extra={"action": "scheduler.dart_registered"})

    # Scorekeeper — log today's BUY/SELL calls + grade matured ones. 16:45 KST
    # (after predict + the daily collectors). Builds the per-method track record.
    _scheduler.add_job(
        _scorekeeper_daily,
        CronTrigger(hour=16, minute=45, timezone=_KST_TZ),
        id="scorekeeper-daily",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    log.info("scheduler: scorekeeper registered (16:45 KST daily -> signal_log track record)", extra={"action": "scheduler.scorekeeper_registered"})

    # Intraday 2-method hourly forward test — every hour at :05 during KST market hours
    # (09:05–15:05 KST), Mon–Fri. Predicts next ~1h with BOTH methods + grades matured
    # ones. (Also exposed at POST /predictions/intraday/tick for external cron so it
    # survives Render free-tier sleep — that path is idempotent, so both can drive it.)
    _scheduler.add_job(
        _intraday_forecast_tick,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute=5, timezone=_KST_TZ),
        id="intraday-forecast-tick",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    # Morning scorecard email — 08:00 KST daily (before market open).
    _add_report_job(   # 08:00 KST scorecard EMAIL — guarded (the forecast tick stays)
        _intraday_morning_report,
        CronTrigger(hour=8, minute=0, timezone=_KST_TZ),
        id="intraday-morning-report",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    # B2 prep — bank order-flow snapshots into history every 5 min during market
    # (09:00–15:55 KST, Mon–Fri). Idempotent; also exposed at
    # POST /predictions/intraday/bank for the external cron.
    _scheduler.add_job(
        _intraday_snapshot_bank,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=_KST_TZ),
        id="intraday-snapshot-bank",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    # Cloud collector — every 2 min during market (09:00–15:59 KST):
    # the SERVER polls Kiwoom itself (Render IPs are registered), so live 호가/수급 flows
    # with no PC. Skips instantly when the PC collector's snapshot is fresh (<90s) —
    # automatic failover, never double work. Also exposed at POST /predictions/collector/pass
    # so a FREE external cron keeps it alive through Render free-tier sleep.
    _scheduler.add_job(
        _cloud_collector_pass,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/2", timezone=_KST_TZ),
        id="cloud-collector-pass",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    # Dip-bounce alerts — every 10 min during market (also at POST /predictions/dip-alert
    # for the external cron). The bot proactively flags ≥1.5%/1h dips it would trade.
    _add_report_job(   # EMAILS dip candidates to recipients → guard (Render sends them)
        _dip_alert_pass,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/10", timezone=_KST_TZ),
        id="dip-alert-pass",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    # LOCAL LLM keep-warm — the fallback star must never cold-load mid-chat
    # ("hello" took 22.8s because Ollama had unloaded qwen3-vl 30b after 5 idle
    # minutes, boss 2026-08-27 "for one question taking 30 sec"). A no-prompt
    # /api/generate pins it in VRAM with keep_alive=24h; every 20 min re-pins,
    # so even an Ollama restart is warm again within minutes. First run ~15s
    # (the one cold load), later runs instant.
    def _ollama_keep_warm():
        try:
            import httpx as _hx
            _star = (os.getenv("OLLAMA_PICKER_MODEL")
                     or "qwen3-vl:30b-a3b-instruct-q4_K_M")
            _hx.post((os.getenv("OLLAMA_URL") or "http://localhost:11434")
                     + "/api/generate",
                     json={"model": _star, "keep_alive": "24h"}, timeout=180)
        except Exception:
            pass                       # Ollama down/absent — nothing to warm
    from apscheduler.triggers.interval import IntervalTrigger as _IvT
    from datetime import datetime as _dtw, timedelta as _tdw
    _scheduler.add_job(
        _ollama_keep_warm,
        _IvT(minutes=20),
        next_run_time=_dtw.now() + _tdw(seconds=20),   # warm right after boot
        id="ollama-keep-warm",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    # PRE-OPEN DESK REFRESH (boss 2026-09-02 18:3x audit: three of Menu 3's
    # four new rooms had ZERO tape - 현대로템, 한화시스템, 한국항공우주 - because
    # refresh_watch() is only ever called by hand through /daily-pick?refresh=1.
    # Nothing scheduled it, so the collector kept yesterday's stocks and any
    # newly-picked room would have been blind at the bell. Runs 08:30 KST on
    # weekdays: recomputes today's picks and re-points the collector while the
    # market is still shut, which is the only time a swap is safe.
    def _pre_open_desk():
        try:
            from services.daily_pick import save_picks, _today
            from services.kiwoom_tape import refresh_watch, WATCH
            save_picks(_today())
            refresh_watch(force=True)
            log.info(f"pre-open desk refresh: watching {len(WATCH)} stocks",
                     extra={"action": "desk.preopen"})
        except Exception as e:
            log.warning(f"pre-open desk refresh failed: {str(e)[:120]}",
                        extra={"action": "desk.preopen.fail"})
    # AFTER-MARKET CLOSE CAPTURE (boss 2026-09-03 evening: "we have to compare
    # with the 9am price and one day before 20:00 price - how can we get this
    # data?"). Kiwoom serves nothing after the bell - tested live during an
    # open 시간외 session, every tape stopped at 15:30:2x - so the print is
    # taken from Naver's overMarketPriceInfo. KRX's after-hours single-price
    # session ends at 18:00, so 18:05 records a settled number.
    def _after_hours_capture():
        try:
            from services.after_hours import record
            from services.daily_pick import score_five, _today
            from services.kiwoom_tape import WATCH
            codes = list(WATCH) or []
            if not codes:
                codes = [(c, n) for c, n in (score_five(20) or [])]
            got = record(_today(), codes)
            log.info(f"after-hours capture: {len(got)}/{len(codes)} prints",
                     extra={"action": "tape.afterhours"})
        except Exception as e:
            log.warning(f"after-hours capture failed: {str(e)[:120]}",
                        extra={"action": "tape.afterhours.fail"})
    _scheduler.add_job(
        _after_hours_capture,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=5, timezone=_KST_TZ),
        id="after-hours-capture",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        _pre_open_desk,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=30, timezone=_KST_TZ),
        id="pre-open-desk-refresh",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    # DESK-HISTORY KEEP-WARM (boss 2026-09-02 14:5x: "trading history is not
    # loading"). /live/warm existed but NOTHING EVER CALLED IT - only Ollama had
    # a keep-warm - so the family-trades cache was filled only by a real page
    # view. After every backend restart the first reader paid a full cold replay
    # (~17-20s on the 17-stock desk, per view), and a day of deploying his rule
    # changes restarts the backend a dozen times. Warmed right after boot and
    # kept alive through the session; the 20s stale-serve does the rest.
    def _desk_history_warm():
        try:
            from routers.paper_desk import live_warm as _lw
            _lw()
        except Exception as _we:
            log.warning(f"desk warm failed: {str(_we)[:120]}",
                        extra={"action": "desk.warm.fail"})
    _scheduler.add_job(
        _desk_history_warm,
        _IvT(minutes=10),
        next_run_time=_dtw.now() + _tdw(seconds=45),   # warm right after boot
        id="desk-history-warm",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    # Paper trader — every 5 min during market: virtual execution of the bot's signals
    # (readiness-gate evidence at 10x speed). Morning scorecard at 08:20 KST.
    _scheduler.add_job(
        _paper_trader_tick,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=_KST_TZ),
        id="paper-trader-tick",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    _add_report_job(   # 08:20 KST paper P&L scorecard EMAIL — guarded (tick stays)
        _paper_morning_report,
        CronTrigger(hour=8, minute=20, timezone=_KST_TZ),
        id="paper-morning-report",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    # Autopilot B — nightly self-tuning at 16:40 KST, DAILY incl. weekends:
    # the PC tops up minute_bars_hist at 16:10, so weekend passes retune on the full
    # multi-week series and Monday opens on the freshest evidence.
    _scheduler.add_job(
        _self_tune_nightly,
        CronTrigger(hour=16, minute=40, timezone=_KST_TZ),
        id="self-tune-nightly",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    log.info("scheduler: intraday 2-method hourly forward test registered (hourly 09–15 KST + 08:00 KST email + 5-min snapshot banking + 2-min cloud collector)", extra={"action": "scheduler.intraday_registered"})

    # Major-news story monitor — twice daily (09:00 + 16:00 KST) so
    # follow-ups (e.g. the 3 Mega Projects 2 PM announcement) are caught + emailed same day.
    _add_report_job(   # emails an exec brief on NEW developments → guard it
        _story_monitor_daily,
        CronTrigger(hour="9,16", minute=0, timezone=_KST_TZ),
        id="story-monitor",
        replace_existing=True, max_instances=1, coalesce=True,
    )
    REPORTS_ENABLED and log.info("scheduler: story monitor registered (09:00 + 16:00 KST)", extra={"action": "scheduler.story_registered"})

    # Master 6:50 morning synthesis — REMOVED from the schedule 2026-08-19: the
    # boss's morning lineup is exactly 5 standalone reports (Kiwoom / Newspaper /
    # Asset / Real Estate / Recommendation), so the consolidated morning email is
    # retired. The Friday-evening WEEKLY and month-end MONTHLY Master editions
    # below stay — they serve a different purpose than the morning batch, and
    # _master_daily_report remains callable manually via POST /reports/compose/master.

    # Safety net — 8:00 AM KST daily: backfill ANY morning report missing from the
    # dashboard (transient failure / missed run), so the boss always has today's
    # set automatically and nobody has to ask. Idempotent (skips what's present).
    # Runs 3x daily in KST: 8:00 AM (right after the morning reports), plus a
    # mid-morning 11:15 AM and an afternoon 5:00 PM check-and-fix pass.
    for _h, _m in ((8, 0), (11, 15), (17, 0)):
        _add_report_job(   # report self-heal — guarded so only the sender backfills+emails
            _ensure_morning_reports,
            CronTrigger(day_of_week="*", hour=_h, minute=_m, timezone=_KST_TZ),
            id=f"ensure-report-health-{_h:02d}{_m:02d}",
            replace_existing=True,
        )
    REPORTS_ENABLED and log.info("scheduler: report health check registered (8:00 / 11:15 / 17:00 KST daily)", extra={"action": "scheduler.ensure_registered"})

    # Watchdog — 08:30 AM KST daily: after the 6:30-8:00 sends + the 8:00 self-
    # heal, VERIFY today's morning reports actually landed; if any are missing,
    # send ONE alert email (+ Telegram) to the boss. This is the never-silent net
    # added after morning reports broke unnoticed for a week (2026-07-22..29).
    # Guarded so only the designated sender alerts.
    _add_report_job(
        _watchdog_morning_reports,
        CronTrigger(day_of_week="*", hour=8, minute=30, timezone=_KST_TZ),
        id="watchdog-morning-reports",
        replace_existing=True,
    )
    REPORTS_ENABLED and log.info("scheduler: morning-report watchdog registered (8:30 KST daily)", extra={"action": "scheduler.watchdog_registered"})

    # Knowledge-base sync (RAG / Phase 2) — 7:10 AM KST, after the
    # morning reports are written, so the chatbot grounds answers in fresh content.
    _scheduler.add_job(
        _knowledge_sync_job,
        CronTrigger(hour=7, minute=10, timezone=_KST_TZ),
        id="knowledge-sync",
        replace_existing=True,
    )
    log.info("scheduler: knowledge sync registered (22:10 UTC = 7:10 AM KST)", extra={"action": "scheduler.knowledge_sync_registered"})

    # ---- Weekly + Monthly 추천/종합 reports — RETIRED at the boss's order
    # (2026-08-31 17:2x, right after the month-end "[TripleH] 월간 종합 추천
    # 리포트" landed: "I wanna stop this... it should not send at all - not
    # only me, the other 7 people also should not receive"). The Fri 17:00/
    # 17:20 weekly and month-end 17:00/17:20 monthly registrations are gone;
    # the morning 5-report batch is untouched. All compose functions remain
    # callable manually via POST /reports/compose/* if ever wanted again.
    # NOTE: any OTHER instance running an older build with REPORTS_ENABLED=
    # true (the 08-14 migration server) can still send its own copy - it
    # needs this commit or REPORTS_ENABLED=false to fall silent too.
    log.info("scheduler: weekly/monthly reco reports RETIRED (boss 2026-08-31)",
             extra={"action": "scheduler.weekly_monthly_retired"})

    # Auto weekly report — Friday 6:30 PM KST
    _add_report_job(
        _auto_weekly_report,
        CronTrigger(day_of_week="fri", hour=18, minute=30, timezone=_KST_TZ),
        id="auto-weekly-report",
        replace_existing=True,
    )
    REPORTS_ENABLED and log.info("scheduler: auto weekly report registered (09:30 UTC Friday = 18:30 KST Friday)", extra={"action": "scheduler.auto_weekly_registered"})

    # Twin auto-mode-switch — every 1 minute
    # Checks working hours (9-18 KST, Mon-Fri) and switches twin modes
    _scheduler.add_job(
        _auto_twin_mode_switch,
        CronTrigger.from_crontab("* * * * *"),
        id="twin-auto-mode-switch",
        replace_existing=True,
    )
    log.info("scheduler: twin auto-mode-switch registered (every 1 min)", extra={"action": "scheduler.twin_mode_registered"})

    # Twin morning handoff — 9:00 AM KST
    _scheduler.add_job(
        _auto_morning_handoff,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=_KST_TZ),
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

    # Cloud auto-pull (Google/Notion) — every 2 hours, offset 20 min
    _scheduler.add_job(
        _auto_cloud_pull,
        CronTrigger.from_crontab("20 */2 * * *"),
        id="twin-cloud-pull",
        replace_existing=True,
    )
    log.info("scheduler: cloud auto-pull registered (every 2 hours)", extra={"action": "scheduler.cloud_pull_registered"})

    # Twin Feed daily summaries — 6 PM KST, Mon-Fri
    _scheduler.add_job(
        _auto_feed_summaries,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone=_KST_TZ),
        id="twin-feed-daily",
        replace_existing=True,
    )
    log.info("scheduler: twin feed daily summaries registered (09:00 UTC, Mon-Fri)", extra={"action": "scheduler.feed_daily_registered"})

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

    # Daily standing tasks — assign 1 standard task per twin every day at 18:00 KST
    # Ensures twins always have something to do overnight, so morning handoff isn't empty
    _scheduler.add_job(
        _auto_assign_daily_standing_tasks,
        CronTrigger(hour=18, minute=0, timezone=_KST_TZ),
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

    # Keep-warm — every 12 min (< Render's 15-min idle-spin-down), ping BOTH this
    # orchestrator's OWN public URL and the Stock-Advisor backend. The self-ping is an
    # INBOUND request to Render's router, which keeps VIP itself awake (so the first
    # chat after login isn't a cold start → no intermittent "I don't know"); the Stock
    # ping keeps the peer warm too. Best-effort; failures ignored.
    def _keep_warm_ping():
        import httpx as _hx, os as _os
        targets = []
        _self = (_os.getenv("VIP_PUBLIC_URL") or _os.getenv("RENDER_EXTERNAL_URL")
                 or "https://vip-orchestrator.onrender.com").rstrip("/")
        _stock = (_os.getenv("STOCK_BACKEND_URL")
                  or "https://stock-advisor-agent-9qwi.onrender.com").rstrip("/")
        for base in (_self, _stock):
            for _p in ("/health", "/chat/health", "/"):
                try:
                    r = _hx.get(f"{base}{_p}", timeout=20)
                    if r.status_code < 500:
                        break
                except Exception:
                    continue
    _scheduler.add_job(
        _keep_warm_ping,
        "interval",
        minutes=12,
        id="stock-backend-keep-warm",
        replace_existing=True,
    )
    log.info("scheduler: keep-warm registered (self + stock backend, every 12min)",
             extra={"action": "scheduler.keep_warm_registered"})

    # Voice recording retention — daily at 12:00 KST
    # Deletes Storage objects + DB rows past retention_expires_at.
    from services.voice_storage import cleanup_expired_recordings as _voice_retention
    _scheduler.add_job(
        _voice_retention,
        CronTrigger(hour=12, minute=0, timezone=_KST_TZ),
        id="voice-recording-retention",
        replace_existing=True,
    )
    log.info("scheduler: voice recording retention registered (daily 03:00 UTC)",
             extra={"action": "scheduler.voice_retention_registered"})

    # Chatbot morning report — daily at 08:00 KST.
    # Aggregates yesterday's chat + call activity per agent + Telegram delivers.
    from services.chatbot_morning_report import deliver_morning_reports_all_agents as _morning_report
    _add_report_job(
        _morning_report,
        CronTrigger(hour=8, minute=0, timezone=_KST_TZ),
        id="chatbot-morning-report",
        replace_existing=True,
    )
    REPORTS_ENABLED and log.info("scheduler: chatbot morning report registered (23:00 UTC = 08:00 KST)",
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
    # 02:00 KST (quiet hours). Best-effort.
    try:
        _scheduler.add_job(
            _run_assistant_improvement,
            CronTrigger(hour=2, minute=0, timezone=_KST_TZ),
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
                    # report catch-up only on the designated sender (else duplicate email)
                    "auto_daily_reports":   _auto_daily_reports if REPORTS_ENABLED else None,
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
                # DB-driven report rule — also an outbound report; skip on non-senders.
                if not REPORTS_ENABLED:
                    continue
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
