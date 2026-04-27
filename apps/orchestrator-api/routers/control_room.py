"""
VIP AI Platform — Control Room Router
Live monitoring view: all twins/workers status, [Watch] feed, boss interrupt.
"""

from uuid import UUID
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.base import get_db
from services import control_room_service

router = APIRouter(prefix="/control-room", tags=["control-room"])


class InterruptMessage(BaseModel):
    message: str = Field(..., description="Boss's interrupt message to the twin")


@router.get("/status")
def get_control_room_status(db: Session = Depends(get_db)):
    """Get full Control Room view: time info, stats, all twins with status."""
    return control_room_service.get_control_room_status(db)


@router.get("/twin/{twin_id}/watch")
def watch_twin(twin_id: UUID, limit: int = 30, db: Session = Depends(get_db)):
    """Live activity feed for a specific twin ([Watch] button)."""
    feed = control_room_service.get_twin_live_feed(db, twin_id, limit=limit)
    return {"twin_id": str(twin_id), "feed": feed, "count": len(feed)}


@router.post("/twin/{twin_id}/interrupt")
def interrupt_twin(twin_id: UUID, body: InterruptMessage, db: Session = Depends(get_db)):
    """Boss interrupts a working twin with a direct message."""
    result = control_room_service.interrupt_twin(db, twin_id, body.message)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    db.commit()
    return result


@router.get("/summary")
def get_everyone_summary(db: Session = Depends(get_db)):
    """Quick summary: what is everyone doing right now."""
    return control_room_service.get_everyone_summary(db)
