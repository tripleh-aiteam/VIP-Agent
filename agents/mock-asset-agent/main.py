"""
Mock Asset Agent — Simulates portfolio/asset management responses.
Exposes /health and /execute with realistic latency and failure modes.
"""

import asyncio
import random
from datetime import datetime
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Optional

app = FastAPI(title="Mock Asset Agent", version="0.1.0")

FAILURE_RATE = 0.1  # 10% chance of failure


class ExecuteRequest(BaseModel):
    task_run_id: str
    trace_id: str
    task_type: str
    input_payload: dict[str, Any] = {}
    callback_url: Optional[str] = None


class ExecuteResponse(BaseModel):
    task_run_id: str
    trace_id: str
    agent_id: str = "mock-asset-agent"
    status: str
    summary: Optional[str] = None
    output_payload: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


@app.get("/health")
async def health():
    return {
        "agent": "mock-asset-agent",
        "type": "asset",
        "status": "healthy",
        "version": "0.1.0",
        "is_mock": True,
        "uptime_seconds": random.randint(1000, 50000),
    }


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest):
    # Simulate realistic latency (0.5-2s)
    await asyncio.sleep(random.uniform(0.5, 2.0))

    # Simulate failure
    if random.random() < FAILURE_RATE:
        return ExecuteResponse(
            task_run_id=req.task_run_id,
            trace_id=req.trace_id,
            status="failed",
            error_message="Mock failure: asset data source temporarily unavailable",
        )

    portfolio_id = req.input_payload.get("portfolio_id", "PF-UNKNOWN")

    return ExecuteResponse(
        task_run_id=req.task_run_id,
        trace_id=req.trace_id,
        status="completed",
        summary=f"Portfolio {portfolio_id} analysis complete",
        output_payload={
            "portfolio_id": portfolio_id,
            "total_value": round(random.uniform(500000, 5000000), 2),
            "currency": "KRW",
            "change_pct": round(random.uniform(-5.0, 8.0), 2),
            "asset_count": random.randint(5, 30),
            "top_holdings": [
                {"name": "Samsung Electronics", "weight_pct": 25.3, "change_pct": 1.2},
                {"name": "SK Hynix", "weight_pct": 15.1, "change_pct": -0.8},
                {"name": "NAVER", "weight_pct": 10.5, "change_pct": 3.4},
            ],
            "risk_level": random.choice(["low", "medium", "high"]),
            "last_rebalanced": "2026-04-10",
        },
    )
