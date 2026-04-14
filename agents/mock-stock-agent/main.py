"""
Mock Stock Agent — Simulates market data and risk analysis responses.
Exposes /health and /execute with realistic latency and failure modes.
"""

import asyncio
import random
from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Any, Optional

app = FastAPI(title="Mock Stock Agent", version="0.1.0")

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
    agent_id: str = "mock-stock-agent"
    status: str
    summary: Optional[str] = None
    output_payload: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


@app.get("/health")
async def health():
    return {
        "agent": "mock-stock-agent",
        "type": "stock",
        "status": "healthy",
        "version": "0.1.0",
        "is_mock": True,
        "uptime_seconds": random.randint(1000, 50000),
    }


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest):
    await asyncio.sleep(random.uniform(0.5, 2.5))

    if random.random() < FAILURE_RATE:
        return ExecuteResponse(
            task_run_id=req.task_run_id,
            trace_id=req.trace_id,
            status="failed",
            error_message="Mock failure: market data feed timeout",
        )

    symbols = req.input_payload.get("symbols", ["005930.KS", "AAPL"])

    stock_data = []
    for sym in symbols[:5]:
        stock_data.append({
            "symbol": sym,
            "price": round(random.uniform(50, 500), 2),
            "change_pct": round(random.uniform(-5.0, 7.0), 2),
            "volume": random.randint(100000, 5000000),
            "recommendation": random.choice(["buy", "hold", "sell"]),
            "confidence": round(random.uniform(0.6, 0.95), 2),
        })

    return ExecuteResponse(
        task_run_id=req.task_run_id,
        trace_id=req.trace_id,
        status="completed",
        summary=f"Market analysis for {len(symbols)} symbols complete",
        output_payload={
            "symbols_analyzed": len(symbols),
            "market_sentiment": random.choice(["bullish", "neutral", "bearish"]),
            "risk_score": round(random.uniform(0.1, 0.9), 2),
            "stocks": stock_data,
            "market_summary": {
                "index": "KOSPI",
                "value": round(random.uniform(2400, 2800), 2),
                "change_pct": round(random.uniform(-2.0, 3.0), 2),
            },
        },
    )
