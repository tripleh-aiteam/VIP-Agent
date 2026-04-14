"""
VIP AI Platform — Judgement Service
Decision engine that evaluates agent outputs and applies business rules.
"""

from fastapi import FastAPI

app = FastAPI(
    title="VIP Judgement Service",
    description="Decision engine for evaluating agent outputs",
    version="0.1.0",
)


@app.get("/")
async def root():
    return {"service": "vip-judgement", "status": "running", "version": "0.1.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}
