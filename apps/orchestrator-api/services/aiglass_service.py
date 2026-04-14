"""
VIP AI Platform — AI Glass Service (MVP)
Capture session intake, mock processing, status management.
No real FFmpeg/3D pipeline — clear placeholders for future expansion.
"""

import threading
import time
import random
from datetime import datetime
from uuid import UUID, uuid4
from typing import Any

from sqlalchemy.orm import Session

from db.base import SessionLocal
from db.models import RealtySpatialCaptureSession, CoreAgent
from services.audit_service import record_event
from services.logger import log


# ---------------------------------------------------------------------------
# Intake — create capture session
# ---------------------------------------------------------------------------

def create_capture_session(
    db: Session,
    trace_id: str,
    agent_id: str,
    device_id: str,
    capture_type: str,
    property_ref: str | None = None,
    video_uri: str | None = None,
    audio_uri: str | None = None,
    model_3d_uri: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict:
    """Create a new capture session and queue it for processing."""

    # Resolve agent
    agent = db.query(CoreAgent).filter(CoreAgent.name == agent_id).first()
    if not agent:
        agent = db.query(CoreAgent).filter(CoreAgent.type == "realty", CoreAgent.status == "active").first()
    if not agent:
        raise ValueError("No realty agent found")

    session = RealtySpatialCaptureSession(
        agent_id=agent.id,
        device_id=device_id,
        property_ref=property_ref,
        video_uri=video_uri,
        audio_uri=audio_uri,
        model_3d_uri=model_3d_uri,
        metadata_json=metadata or {},
        processing_status="pending",
    )
    db.add(session)
    db.flush()

    record_event(db, "ai-glass", "capture.created", trace_id, {
        "session_id": str(session.id),
        "device_id": device_id,
        "capture_type": capture_type,
        "property_ref": property_ref,
        "has_video": bool(video_uri),
        "has_audio": bool(audio_uri),
    })

    log.info(
        f"ai-glass: capture session created ({capture_type}) device={device_id}",
        extra={"trace_id": trace_id, "action": "capture.created"},
    )

    db.commit()

    # Queue mock processing in background
    _queue_mock_processing(str(session.id), trace_id)

    return _session_to_dict(session)


# ---------------------------------------------------------------------------
# Mock processing worker (placeholder for real FFmpeg/3D pipeline)
# ---------------------------------------------------------------------------

def _queue_mock_processing(session_id: str, trace_id: str, attempt: int = 1):
    """Queue a mock processing job in a background thread."""
    def _worker():
        time.sleep(random.uniform(2, 5))  # Simulate processing time
        _run_mock_processing(session_id, trace_id, attempt)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def _run_mock_processing(session_id: str, trace_id: str, attempt: int):
    """
    Mock processing worker.
    PLACEHOLDER: Replace with real FFmpeg / 3D conversion pipeline.
    Simulates success/failure. Retries up to 3 times, then marks for manual review.
    """
    db = SessionLocal()
    try:
        session = db.query(RealtySpatialCaptureSession).filter(
            RealtySpatialCaptureSession.id == UUID(session_id)
        ).first()
        if not session:
            return

        # Update to processing
        session.processing_status = "processing"
        session.metadata_json = {
            **(session.metadata_json or {}),
            "processing_attempt": attempt,
            "processing_started": datetime.utcnow().isoformat(),
        }
        db.commit()

        log.info(
            f"ai-glass: processing attempt {attempt} for {session_id[:8]}",
            extra={"trace_id": trace_id, "action": "capture.processing"},
        )

        # Simulate processing (80% success rate)
        success = random.random() > 0.2

        if success:
            # PLACEHOLDER: In production, this would run FFmpeg, generate 3D model, etc.
            session.processing_status = "completed"
            session.model_3d_uri = session.model_3d_uri or f"s3://vip-models/mock/{session_id[:8]}_model.glb"
            session.metadata_json = {
                **(session.metadata_json or {}),
                "processing_completed": datetime.utcnow().isoformat(),
                "mock_result": {
                    "frames_processed": random.randint(100, 3000),
                    "duration_seconds": round(random.uniform(10, 120), 1),
                    "resolution": "4K",
                    "model_vertices": random.randint(10000, 500000),
                    "file_size_mb": round(random.uniform(5, 200), 1),
                },
            }

            record_event(db, "ai-glass", "capture.completed", trace_id, {
                "session_id": session_id, "attempt": attempt,
            })
            log.info(f"ai-glass: processing completed for {session_id[:8]}", extra={"action": "capture.completed"})

        else:
            if attempt >= 3:
                # Mark for manual review after 3 failures
                session.processing_status = "manual_review"
                session.metadata_json = {
                    **(session.metadata_json or {}),
                    "processing_failed": datetime.utcnow().isoformat(),
                    "failure_reason": "Max retries exceeded — requires manual review",
                    "total_attempts": attempt,
                }
                record_event(db, "ai-glass", "capture.manual_review", trace_id, {
                    "session_id": session_id, "attempts": attempt,
                })
                log.warning(f"ai-glass: {session_id[:8]} marked for manual review after {attempt} attempts")
            else:
                # Retry
                session.processing_status = "pending"
                session.metadata_json = {
                    **(session.metadata_json or {}),
                    f"retry_{attempt}_at": datetime.utcnow().isoformat(),
                    "failure_reason": f"Mock processing failure (attempt {attempt})",
                }
                log.info(f"ai-glass: retrying {session_id[:8]} (attempt {attempt + 1})")
                db.commit()
                _queue_mock_processing(session_id, trace_id, attempt + 1)
                return

        db.commit()

    except Exception as e:
        log.warning(f"ai-glass: processing error: {e}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Status updates
# ---------------------------------------------------------------------------

def update_status(db: Session, session_id: UUID, status: str, trace_id: str, metadata_update: dict | None = None) -> dict | None:
    session = db.query(RealtySpatialCaptureSession).filter(RealtySpatialCaptureSession.id == session_id).first()
    if not session:
        return None

    valid_statuses = {"pending", "processing", "completed", "failed", "manual_review"}
    if status not in valid_statuses:
        raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")

    session.processing_status = status
    if metadata_update:
        session.metadata_json = {**(session.metadata_json or {}), **metadata_update}

    record_event(db, "ai-glass", f"capture.{status}", trace_id, {"session_id": str(session_id)})
    db.commit()
    return _session_to_dict(session)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def list_sessions(db: Session, status: str | None = None, limit: int = 50) -> list[dict]:
    q = db.query(RealtySpatialCaptureSession)
    if status:
        q = q.filter(RealtySpatialCaptureSession.processing_status == status)
    sessions = q.order_by(RealtySpatialCaptureSession.created_at.desc()).limit(limit).all()
    return [_session_to_dict(s) for s in sessions]


def get_session(db: Session, session_id: UUID) -> dict | None:
    s = db.query(RealtySpatialCaptureSession).filter(RealtySpatialCaptureSession.id == session_id).first()
    if not s:
        return None
    return _session_to_dict(s)


def get_stats(db: Session) -> dict:
    total = db.query(RealtySpatialCaptureSession).count()
    pending = db.query(RealtySpatialCaptureSession).filter(RealtySpatialCaptureSession.processing_status == "pending").count()
    processing = db.query(RealtySpatialCaptureSession).filter(RealtySpatialCaptureSession.processing_status == "processing").count()
    completed = db.query(RealtySpatialCaptureSession).filter(RealtySpatialCaptureSession.processing_status == "completed").count()
    failed = db.query(RealtySpatialCaptureSession).filter(RealtySpatialCaptureSession.processing_status.in_(["failed", "manual_review"])).count()
    return {"total": total, "pending": pending, "processing": processing, "completed": completed, "failed": failed}


def _session_to_dict(s: RealtySpatialCaptureSession) -> dict:
    return {
        "id": str(s.id),
        "agent_id": str(s.agent_id),
        "device_id": s.device_id,
        "property_ref": s.property_ref,
        "video_uri": s.video_uri,
        "audio_uri": s.audio_uri,
        "model_3d_uri": s.model_3d_uri,
        "processing_status": s.processing_status,
        "metadata": s.metadata_json,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
