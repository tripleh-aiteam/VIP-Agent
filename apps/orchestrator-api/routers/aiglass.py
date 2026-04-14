"""
VIP AI Platform — AI Glass Router
POST /ai-glass/capture, GET /ai-glass/sessions, GET /ai-glass/sessions/{id},
PATCH /ai-glass/sessions/{id}/status, GET /ai-glass/stats
"""

from uuid import UUID
from pydantic import BaseModel, Field
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.base import get_db
from services import aiglass_service

router = APIRouter(prefix="/ai-glass", tags=["ai-glass"])


class CaptureBody(BaseModel):
    trace_id: str = Field(default="system")
    agent_id: str = Field(default="mock-realty-agent")
    device_id: str = Field(..., description="AI Glass device identifier")
    capture_type: str = Field(default="spatial_3d", description="video | photo | spatial_3d | audio | mixed")
    property_ref: Optional[str] = Field(None, description="Property reference or listing ID")
    video_uri: Optional[str] = None
    audio_uri: Optional[str] = None
    model_3d_uri: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    model_config = {"json_schema_extra": {"examples": [
        {
            "trace_id": "tr-glass-001",
            "device_id": "glass-device-A1",
            "capture_type": "spatial_3d",
            "property_ref": "PROP-2026-0414",
            "video_uri": "s3://vip-captures/glass-A1/2026-04-14/capture.mp4",
            "audio_uri": "s3://vip-captures/glass-A1/2026-04-14/audio.wav",
            "metadata": {"fps": 30, "resolution": "4K", "stereo": True, "location": {"lat": 37.5665, "lng": 126.978}}
        }
    ]}}


class StatusUpdateBody(BaseModel):
    status: str = Field(..., description="pending | processing | completed | failed | manual_review")
    trace_id: str = Field(default="system")
    metadata: Optional[dict[str, Any]] = None


@router.post("/capture", status_code=201)
def create_capture(body: CaptureBody, db: Session = Depends(get_db)):
    """Intake a new AI Glass capture session. Queues mock processing automatically."""
    try:
        return aiglass_service.create_capture_session(
            db=db, trace_id=body.trace_id, agent_id=body.agent_id,
            device_id=body.device_id, capture_type=body.capture_type,
            property_ref=body.property_ref, video_uri=body.video_uri,
            audio_uri=body.audio_uri, model_3d_uri=body.model_3d_uri,
            metadata=body.metadata,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/sessions")
def list_sessions(
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List capture sessions with optional status filter."""
    return aiglass_service.list_sessions(db, status=status, limit=limit)


@router.get("/sessions/{session_id}")
def get_session(session_id: UUID, db: Session = Depends(get_db)):
    """Get a single capture session with full metadata."""
    s = aiglass_service.get_session(db, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


@router.patch("/sessions/{session_id}/status")
def update_status(session_id: UUID, body: StatusUpdateBody, db: Session = Depends(get_db)):
    """Manually update a session's processing status."""
    try:
        result = aiglass_service.update_status(db, session_id, body.status, body.trace_id, body.metadata)
        if not result:
            raise HTTPException(404, "Session not found")
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """Get processing statistics."""
    return aiglass_service.get_stats(db)
