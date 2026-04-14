"""
VIP AI Platform — Schedule Router
GET /schedules, PATCH /schedules/{id}, POST /schedules/{id}/run-now
"""

from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.base import get_db
from services import scheduler_service

router = APIRouter(prefix="/schedules", tags=["schedules"])


class PatchScheduleBody(BaseModel):
    enabled: Optional[bool] = None
    cron_expr: Optional[str] = Field(None, description="Cron expression (e.g., '*/5 * * * *' for every 5 min)")
    name: Optional[str] = None


@router.get("/")
def list_schedules(db: Session = Depends(get_db)):
    """List all schedule rules with next fire time."""
    return scheduler_service.list_rules(db)


@router.get("/{rule_id}")
def get_schedule(rule_id: UUID, db: Session = Depends(get_db)):
    """Get a single schedule rule."""
    rules = scheduler_service.list_rules(db)
    for r in rules:
        if r["id"] == str(rule_id):
            return r
    raise HTTPException(404, "Schedule rule not found")


@router.patch("/{rule_id}")
def update_schedule(rule_id: UUID, body: PatchScheduleBody, db: Session = Depends(get_db)):
    """Update a schedule rule (enable/disable, change cron). Scheduler reloads automatically."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")

    result = scheduler_service.update_rule(db, rule_id, updates)
    if not result:
        raise HTTPException(404, "Schedule rule not found")
    return result


@router.post("/{rule_id}/run-now")
def run_now(rule_id: UUID):
    """Manually trigger a schedule rule immediately. Creates task_run with initiator_type=system_scheduler."""
    result = scheduler_service.run_now(rule_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result
