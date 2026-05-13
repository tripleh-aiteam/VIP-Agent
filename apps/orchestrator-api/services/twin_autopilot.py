"""
VIP AI Platform — Twin Autopilot (v4 Phase 1)
Runs the existing self-improvement cycle on every twin on a schedule —
turns the twins from "configured" into "always-learning".

Wires into services.scheduler_service (APScheduler). Falls back to a
manual /admin/autopilot/run-now endpoint when the scheduler isn't up.

Each run per twin:
  - S1 self_reflect on recent completed tasks
  - S2 detect knowledge gaps
  - S3 analyze correction patterns
  - S4 consolidate knowledge (dedupe + cluster)
  - Take auto-snapshot if intelligence_pct crossed a milestone
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from db.base import SessionLocal
from db.models import DigitalTwin, TwinSnapshot
from services import twin_self_improve, twin_intelligence, twin_service
from services.logger import log


_AUTOPILOT_INTERVAL_HOURS = int(os.getenv("TWIN_AUTOPILOT_INTERVAL_HOURS", "6"))
_SNAPSHOT_INTERVAL_PCT = int(os.getenv("TWIN_AUTOPILOT_SNAPSHOT_PCT", "10"))


def run_all_twins_cycle() -> dict:
    """Top-level callable used by APScheduler. Opens its own DB session.
    Returns aggregate stats so the admin endpoint can surface them.
    """
    db = SessionLocal()
    started_at = datetime.utcnow()
    summary: list[dict] = []
    try:
        twins = db.query(DigitalTwin).all()
        for twin in twins:
            try:
                metric_before = twin_intelligence.get_twin_intelligence(db, twin.id)
                pct_before = metric_before.get("intelligence_pct", 0) if metric_before else 0
                result = twin_self_improve.run_self_improvement_cycle(db, twin.id)
                metric_after = twin_intelligence.get_twin_intelligence(db, twin.id)
                pct_after = metric_after.get("intelligence_pct", 0) if metric_after else 0
                snapshot_taken = _maybe_snapshot(db, twin, pct_before, pct_after)
                summary.append({
                    "twin_id": str(twin.id),
                    "twin_name": twin.name,
                    "intelligence_before": pct_before,
                    "intelligence_after": pct_after,
                    "delta": pct_after - pct_before,
                    "snapshot_taken": snapshot_taken,
                    "result": result,
                })
            except Exception as e:
                log.warning(f"twin_autopilot: twin {twin.name} cycle failed: {e}")
                summary.append({"twin_id": str(twin.id), "twin_name": twin.name, "error": str(e)})
        db.commit()
    finally:
        db.close()

    return {
        "started_at": started_at.isoformat(),
        "ended_at": datetime.utcnow().isoformat(),
        "twins_processed": len(summary),
        "twins": summary,
    }


def _maybe_snapshot(db, twin: DigitalTwin, pct_before: int, pct_after: int) -> bool:
    """Snapshot when a twin crosses a percentage milestone."""
    before_band = pct_before // _SNAPSHOT_INTERVAL_PCT
    after_band = pct_after // _SNAPSHOT_INTERVAL_PCT
    if after_band <= before_band:
        return False
    try:
        from services.twin_snapshots import create_snapshot
        create_snapshot(
            db, twin.id,
            version_name=f"auto-{pct_after}%-{datetime.utcnow().strftime('%Y%m%d-%H%M')}",
            notes=f"Autopilot snapshot — intelligence crossed {after_band * _SNAPSHOT_INTERVAL_PCT}%",
        )
        return True
    except Exception as e:
        log.warning(f"twin_autopilot: snapshot for {twin.name} failed: {e}")
        return False


def register_with_scheduler() -> dict:
    """Install the autopilot cron job. Call from main.py lifespan startup."""
    try:
        from services.scheduler_service import _scheduler   # type: ignore
        if _scheduler is None:
            return {"installed": False, "reason": "scheduler not initialized"}
        _scheduler.add_job(
            run_all_twins_cycle,
            "interval",
            hours=_AUTOPILOT_INTERVAL_HOURS,
            id="twin-autopilot",
            replace_existing=True,
            next_run_time=datetime.utcnow(),
        )
        return {
            "installed": True,
            "interval_hours": _AUTOPILOT_INTERVAL_HOURS,
            "snapshot_threshold_pct": _SNAPSHOT_INTERVAL_PCT,
        }
    except Exception as e:
        return {"installed": False, "reason": str(e)}


def get_status() -> dict:
    """Inspect whether the autopilot job is scheduled and when it next fires."""
    try:
        from services.scheduler_service import _scheduler   # type: ignore
        if _scheduler is None:
            return {"installed": False, "reason": "scheduler not initialized"}
        job = _scheduler.get_job("twin-autopilot")
        if not job:
            return {"installed": False, "reason": "job not registered"}
        return {
            "installed": True,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "interval_hours": _AUTOPILOT_INTERVAL_HOURS,
        }
    except Exception as e:
        return {"installed": False, "reason": str(e)}
