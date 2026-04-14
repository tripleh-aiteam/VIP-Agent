"""
VIP AI Platform — Report Contracts
ReportDraft: intermediate report before finalization.
FinalReport: completed, deliverable report.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ReportType(str, Enum):
    daily_summary = "daily_summary"
    weekly_digest = "weekly_digest"
    alert = "alert"
    audit = "audit"
    portfolio = "portfolio"
    custom = "custom"


class ReportSection(BaseModel):
    title: str = Field(...)
    content: str = Field(...)
    data: Optional[dict[str, Any]] = None
    charts: list[str] = Field(default_factory=list, description="URIs to chart images or data")


class ReportDraft(BaseModel):
    """Intermediate report assembled from task run outputs before finalization."""

    draft_id: UUID = Field(default_factory=uuid4)
    trace_id: str = Field(...)
    report_type: ReportType = Field(...)
    title: str = Field(...)
    source_run_ids: list[UUID] = Field(default_factory=list, description="Task runs that contributed data")
    sections: list[ReportSection] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict, description="Unprocessed data for further assembly")
    generated_by: str = Field(default="report-composer")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    version: str = Field(default="1.0")

    model_config = {"json_schema_extra": {"examples": [
        {
            "trace_id": "tr-20260413-001",
            "report_type": "daily_summary",
            "title": "Daily Portfolio Summary — 2026-04-13",
            "sections": [
                {"title": "Overview", "content": "Portfolio value increased by 2.3%"},
                {"title": "Top Movers", "content": "AAPL +5.1%, TSLA -2.0%"},
            ],
        }
    ]}}


class FinalReport(BaseModel):
    """Finalized report ready for delivery."""

    report_id: UUID = Field(default_factory=uuid4)
    trace_id: str = Field(...)
    draft_id: Optional[UUID] = Field(None, description="Draft this report was generated from")
    report_type: ReportType = Field(...)
    title: str = Field(...)
    sections: list[ReportSection] = Field(...)
    delivery_channels: list[str] = Field(default_factory=list, description="Channels to deliver to (web, telegram, email)")
    recipient_ids: list[str] = Field(default_factory=list)
    judgement_id: Optional[UUID] = Field(None, description="Judgement case if report was reviewed")
    approved: bool = Field(default=True)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    delivered_at: Optional[datetime] = None
    version: str = Field(default="1.0")

    model_config = {"json_schema_extra": {"examples": [
        {
            "trace_id": "tr-20260413-001",
            "report_type": "daily_summary",
            "title": "Daily Portfolio Summary — 2026-04-13",
            "sections": [
                {"title": "Overview", "content": "Portfolio value increased by 2.3%"},
            ],
            "delivery_channels": ["web", "telegram"],
            "recipient_ids": ["user-001"],
        }
    ]}}
