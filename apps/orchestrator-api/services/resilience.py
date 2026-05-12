"""
VIP AI Platform — Phase 3 Resilience Layer

Provides:
- @with_retry: exponential-backoff retry decorator for scheduler jobs + adapters
- alert(): persistent in-DB alert + optional Telegram push for failures
- record_job_run(): tracks last-run-at, success/failure, duration for the
                     /health/dashboard endpoint.

All persisted state goes through OrchEvent so we don't add new tables.
The health dashboard reads recent events to render the traffic-light status.
"""

from __future__ import annotations

import functools
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Any

from services.logger import log


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def with_retry(
    max_attempts: int = 3,
    backoff_seconds: tuple[int, ...] = (5, 30, 90),
    job_name: str | None = None,
    alert_on_final_failure: bool = True,
):
    """
    Wrap a function with exponential-backoff retry.

    Usage:
        @with_retry(max_attempts=3, backoff_seconds=(10, 60, 300))
        def my_scheduler_job():
            ...

    On final failure (after all retries), an alert is recorded and optionally
    pushed to Telegram (if alert_on_final_failure=True).
    """
    def decorator(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            name = job_name or fn.__name__
            started_at = time.time()
            last_err: str = ""

            for attempt in range(1, max_attempts + 1):
                try:
                    result = fn(*args, **kwargs)
                    record_job_run(name, success=True, duration_s=time.time() - started_at, attempts=attempt)
                    if attempt > 1:
                        log.info(
                            f"resilience: {name} succeeded on attempt {attempt}",
                            extra={"action": "resilience.retry_success", "job": name, "attempts": attempt},
                        )
                    return result
                except Exception as e:
                    last_err = f"{type(e).__name__}: {str(e)[:200]}"
                    log.warning(
                        f"resilience: {name} attempt {attempt}/{max_attempts} failed: {last_err}",
                        extra={"action": "resilience.retry_attempt", "job": name, "attempt": attempt, "error": last_err},
                    )
                    if attempt < max_attempts:
                        wait = backoff_seconds[attempt - 1] if attempt - 1 < len(backoff_seconds) else backoff_seconds[-1]
                        time.sleep(wait)

            # All retries exhausted
            duration = time.time() - started_at
            record_job_run(name, success=False, duration_s=duration, attempts=max_attempts, error=last_err)

            if alert_on_final_failure:
                try:
                    alert(
                        kind="scheduler_failure",
                        title=f"⚠️ Scheduled job FAILED: {name}",
                        body=f"After {max_attempts} attempts, {name} could not complete.\nLast error: {last_err}\nDuration: {duration:.1f}s",
                        severity="error",
                    )
                except Exception:
                    pass  # Don't let alerting itself crash
            log.error(
                f"resilience: {name} EXHAUSTED retries ({max_attempts})",
                extra={"action": "resilience.retry_exhausted", "job": name, "error": last_err},
            )
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Job run tracking — persists to OrchEvent for the health dashboard
# ---------------------------------------------------------------------------

def record_job_run(
    job_name: str,
    success: bool,
    duration_s: float,
    attempts: int = 1,
    error: str | None = None,
) -> None:
    """Persist a scheduler job run to OrchEvent (used by /health/dashboard)."""
    from db.base import SessionLocal
    from db.models import AuditEventLog
    db = SessionLocal()
    try:
        evt = AuditEventLog(
            source="scheduler",
            event_type=f"job.{'success' if success else 'failure'}",
            trace_id=f"job-{job_name}-{int(time.time())}",
            payload_json={
                "job": job_name,
                "success": success,
                "duration_seconds": round(duration_s, 2),
                "attempts": attempts,
                "error": error,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        db.add(evt)
        db.commit()
    except Exception as e:
        log.warning(f"resilience: record_job_run failed: {e}", extra={"action": "resilience.record_failed"})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Alert service — persistent + Telegram push
# ---------------------------------------------------------------------------

def alert(
    kind: str,
    title: str,
    body: str = "",
    severity: str = "info",
) -> None:
    """
    Record an alert to DB AND attempt Telegram push.
    severity: "info" | "warning" | "error" | "critical"
    """
    from db.base import SessionLocal
    from db.models import AuditEventLog
    db = SessionLocal()
    try:
        evt = AuditEventLog(
            source="alert",
            event_type=f"alert.{severity}",
            trace_id=f"alert-{kind}-{int(time.time())}",
            payload_json={
                "kind": kind,
                "title": title,
                "body": body,
                "severity": severity,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        db.add(evt)
        db.commit()
    except Exception as e:
        log.warning(f"alert: persist failed: {e}", extra={"action": "alert.persist_failed"})
    finally:
        db.close()

    # Optional Telegram push
    try:
        from services.telegram_service import send_alert
        emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🚨", "critical": "🔴"}.get(severity, "📢")
        msg_lines = [f"{emoji} <b>{title}</b>"]
        if body:
            msg_lines.append("")
            msg_lines.append(body[:500])
        send_alert("\n".join(msg_lines))
    except Exception:
        pass  # Telegram optional

    # PROACTIVE pillar — push to any connected @triple-h/chatbot panel
    # so the boss/worker hears the alert via the chatbot, not just Telegram.
    try:
        from services.event_bus import publish
        publish("chatbot.proactive", {
            "kind": kind,
            "title": title,
            "body": body[:500],
            "severity": severity,
            "speak": severity in ("warning", "error", "critical"),
        })
    except Exception:
        pass  # event bus optional


# ---------------------------------------------------------------------------
# Health dashboard data — read recent job runs + alerts
# ---------------------------------------------------------------------------

def get_health_dashboard(hours_back: int = 24) -> dict:
    """Build the health dashboard view: status of every scheduler job + recent alerts."""
    from db.base import SessionLocal
    from db.models import AuditEventLog
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        # All job events in last 24h
        job_events = (
            db.query(AuditEventLog)
            .filter(
                AuditEventLog.source == "scheduler",
                AuditEventLog.event_type.in_(["job.success", "job.failure"]),
                AuditEventLog.created_at >= cutoff,
            )
            .order_by(AuditEventLog.created_at.desc())
            .all()
        )

        # Group by job name → latest status
        by_job: dict[str, dict] = {}
        for evt in job_events:
            payload = evt.payload_json or {}
            job_name = payload.get("job", "unknown")
            if job_name in by_job:
                by_job[job_name]["runs_24h"] += 1
                if not payload.get("success"):
                    by_job[job_name]["failures_24h"] += 1
                continue
            by_job[job_name] = {
                "job": job_name,
                "last_run_at": evt.created_at.isoformat() if evt.created_at else None,
                "last_success": payload.get("success", False),
                "last_duration_s": payload.get("duration_seconds", 0),
                "last_attempts": payload.get("attempts", 1),
                "last_error": payload.get("error"),
                "runs_24h": 1,
                "failures_24h": 0 if payload.get("success") else 1,
                "status": (
                    "green" if payload.get("success") else
                    "red"
                ),
            }

        # Recent alerts
        alerts_q = (
            db.query(AuditEventLog)
            .filter(
                AuditEventLog.source == "alert",
                AuditEventLog.created_at >= cutoff,
            )
            .order_by(AuditEventLog.created_at.desc())
            .limit(20)
            .all()
        )
        alerts_out = []
        for a in alerts_q:
            p = a.payload_json or {}
            alerts_out.append({
                "title": p.get("title", ""),
                "body": p.get("body", ""),
                "severity": p.get("severity", "info"),
                "kind": p.get("kind", ""),
                "timestamp": a.created_at.isoformat() if a.created_at else None,
            })

        # Summary
        total_jobs = len(by_job)
        green_jobs = sum(1 for j in by_job.values() if j["status"] == "green")
        red_jobs = total_jobs - green_jobs

        return {
            "summary": {
                "total_jobs": total_jobs,
                "green": green_jobs,
                "red": red_jobs,
                "alerts_24h": len(alerts_out),
                "overall_status": "green" if red_jobs == 0 else ("yellow" if red_jobs <= 1 else "red"),
            },
            "jobs": list(by_job.values()),
            "alerts": alerts_out,
            "as_of": datetime.utcnow().isoformat(),
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Restart-safe scheduler — detect missed runs on startup
# ---------------------------------------------------------------------------

# Expected daily jobs and their cron hours (UTC) — used to detect missed runs.
# Keys MUST match the job_name passed to with_retry.
EXPECTED_DAILY_RUNS = {
    "auto_daily_reports":  23,   # 23:00 UTC = 8:00 AM KST
    "twin_morning_handoff": 0,   # 00:00 UTC = 9:00 AM KST
    "daily_standing_tasks": 9,   # 09:00 UTC = 18:00 KST
    "auto_self_improvement": None,  # every 6h, can't easily check missed
}


def detect_missed_runs() -> list[dict]:
    """
    On startup, check whether each expected job ran today.
    Returns list of {job, expected_hour, last_run, missed} entries.
    Caller decides whether to fire missed jobs immediately.
    """
    from db.base import SessionLocal
    from db.models import AuditEventLog
    db = SessionLocal()
    out: list[dict] = []
    try:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        for job_name, expected_hour in EXPECTED_DAILY_RUNS.items():
            if expected_hour is None:
                continue  # not a fixed-time job
            expected_at = today_start.replace(hour=expected_hour)
            if expected_at > now:
                continue  # not yet due today

            # Has it run today after expected_at?
            last_event = (
                db.query(AuditEventLog)
                .filter(
                    AuditEventLog.source == "scheduler",
                    AuditEventLog.event_type == "job.success",
                    AuditEventLog.created_at >= expected_at,
                )
                .order_by(AuditEventLog.created_at.desc())
                .first()
            )
            ran_today = last_event is not None and (last_event.payload_json or {}).get("job") == job_name

            if not ran_today:
                out.append({
                    "job": job_name,
                    "expected_at": expected_at.isoformat(),
                    "missed": True,
                    "detected_at": now.isoformat(),
                })
    finally:
        db.close()
    return out
