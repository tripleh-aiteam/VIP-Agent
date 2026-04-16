"""
VIP AI Platform — AI Glass Capture Contract
Event format for spatial capture sessions from AI Glasses / AR devices.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class CaptureType(str, Enum):
    video = "video"
    photo = "photo"
    spatial_3d = "spatial_3d"
    audio = "audio"
    mixed = "mixed"


class ProcessingStatus(str, Enum):
    initiated = "initiated"
    uploading = "uploading"
    processing = "processing"
    done = "done"
    error = "error"


class GeoLocation(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    altitude_m: Optional[float] = None
    accuracy_m: Optional[float] = None


class AIGlassCaptureEvent(BaseModel):
    """Event emitted when AI Glasses capture spatial data for a property."""

    capture_id: UUID = Field(default_factory=uuid4)
    trace_id: str = Field(...)
    agent_id: str = Field(..., description="Realty agent handling this capture")
    device_id: str = Field(..., description="AI Glass device identifier")
    capture_type: CaptureType = Field(...)
    property_ref: Optional[str] = Field(None, description="Property reference or listing ID")
    location: Optional[GeoLocation] = None
    video_uri: Optional[str] = None
    audio_uri: Optional[str] = None
    photo_uris: list[str] = Field(default_factory=list)
    model_3d_uri: Optional[str] = None
    processing_status: ProcessingStatus = Field(default=ProcessingStatus.initiated)
    metadata: dict[str, Any] = Field(default_factory=dict, description="Device-specific metadata (FPS, resolution, etc)")
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    version: str = Field(default="1.0")

    model_config = {"protected_namespaces": (), "json_schema_extra": {"examples": [
        {
            "trace_id": "tr-20260413-glass-001",
            "agent_id": "Real Estate Agent",
            "device_id": "glass-device-A1",
            "capture_type": "spatial_3d",
            "property_ref": "PROP-2026-0413",
            "location": {"latitude": 37.5665, "longitude": 126.9780},
            "processing_status": "initiated",
            "metadata": {"fps": 30, "resolution": "4K"},
        }
    ]}}
