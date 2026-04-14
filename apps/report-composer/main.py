"""
VIP AI Platform — Report Composer
Generates structured reports from agent outputs and judgement results.
"""

from fastapi import FastAPI

app = FastAPI(
    title="VIP Report Composer",
    description="Report generation service",
    version="0.1.0",
)


@app.get("/")
async def root():
    return {"service": "vip-report-composer", "status": "running", "version": "0.1.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}
