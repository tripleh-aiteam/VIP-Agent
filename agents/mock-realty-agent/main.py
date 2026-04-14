"""
Mock Realty Agent — Simulates property listing and vacancy analysis.
Exposes /health and /execute with realistic latency and failure modes.
"""

import asyncio
import random
from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Any, Optional

app = FastAPI(title="Mock Realty Agent", version="0.1.0")

FAILURE_RATE = 0.1


class ExecuteRequest(BaseModel):
    task_run_id: str
    trace_id: str
    task_type: str
    input_payload: dict[str, Any] = {}
    callback_url: Optional[str] = None


class ExecuteResponse(BaseModel):
    task_run_id: str
    trace_id: str
    agent_id: str = "mock-realty-agent"
    status: str
    summary: Optional[str] = None
    output_payload: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


@app.get("/health")
async def health():
    return {
        "agent": "mock-realty-agent",
        "type": "realty",
        "status": "healthy",
        "version": "0.1.0",
        "is_mock": True,
        "uptime_seconds": random.randint(1000, 50000),
    }


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest):
    await asyncio.sleep(random.uniform(0.5, 3.0))

    if random.random() < FAILURE_RATE:
        return ExecuteResponse(
            task_run_id=req.task_run_id,
            trace_id=req.trace_id,
            status="failed",
            error_message="Mock failure: property database connection lost",
        )

    region = req.input_payload.get("region", "Seoul-Gangnam")

    properties = []
    for i in range(random.randint(3, 8)):
        properties.append({
            "property_id": f"PROP-{random.randint(1000, 9999)}",
            "address": f"{random.randint(1, 300)} {random.choice(['Gangnam-daero', 'Teheran-ro', 'Seolleung-ro', 'Bongeunsa-ro'])}, {region}",
            "type": random.choice(["apartment", "office", "retail", "mixed"]),
            "price_krw": random.randint(500000000, 5000000000),
            "size_sqm": random.randint(30, 300),
            "vacancy_pct": round(random.uniform(0, 15), 1),
            "yield_pct": round(random.uniform(3.0, 8.0), 1),
        })

    return ExecuteResponse(
        task_run_id=req.task_run_id,
        trace_id=req.trace_id,
        status="completed",
        summary=f"Found {len(properties)} properties in {region}",
        output_payload={
            "region": region,
            "total_listings": len(properties),
            "avg_vacancy_pct": round(sum(p["vacancy_pct"] for p in properties) / len(properties), 1),
            "avg_yield_pct": round(sum(p["yield_pct"] for p in properties) / len(properties), 1),
            "properties": properties,
            "market_trend": random.choice(["rising", "stable", "declining"]),
        },
    )
