"""
VIP AI Platform — Gateway Adapter
Public-facing entry point. Normalizes requests and forwards to the Orchestrator.

Rules:
- Gateway is NOT the brain — Orchestrator is
- Gateway NEVER writes to the database directly
- Gateway NEVER makes decisions
- Gateway only: accept, normalize, forward, return
"""

import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_API_URL", "http://localhost:8000")
GATEWAY_ENV = os.getenv("APP_ENV", "development")
CORS_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "")

app = FastAPI(
    title="VIP Gateway Adapter",
    description="Public entry point — forwards all requests to the Orchestrator API",
    version="0.2.0",
)

_cors_list = [o.strip() for o in CORS_ORIGINS.split(",") if o.strip()] if CORS_ORIGINS else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {"service": "vip-gateway", "status": "running", "version": "0.2.0", "env": GATEWAY_ENV}


@app.get("/health")
async def health():
    """Gateway health + orchestrator connectivity check."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{ORCHESTRATOR_URL}/health")
            orch = resp.json() if resp.status_code == 200 else {"status": "unreachable"}
    except Exception:
        orch = {"status": "unreachable"}

    return {
        "gateway": "ok",
        "orchestrator": orch.get("status", "unknown"),
        "orchestrator_url": ORCHESTRATOR_URL,
    }


# ---------------------------------------------------------------------------
# Forward routes — proxy to Orchestrator
# ---------------------------------------------------------------------------

ALLOWED_FORWARD_PATHS = [
    "/health", "/agents", "/runs", "/reports", "/channels",
    "/registry", "/schedules", "/judgement", "/a2a",
    "/ai-glass", "/chat", "/contracts", "/telegram", "/demo",
    "/tasks", "/callbacks",
]


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
async def forward_to_orchestrator(path: str, request: Request):
    """
    Forward /api/* requests to the Orchestrator.
    Gateway normalizes the path and passes headers/body through.
    """
    # Validate path prefix
    target_path = f"/{path}"
    allowed = any(target_path.startswith(p) for p in ALLOWED_FORWARD_PATHS)
    if not allowed:
        raise HTTPException(403, f"Path not allowed through gateway: {target_path}")

    # Build forwarded request
    url = f"{ORCHESTRATOR_URL}{target_path}"
    headers = dict(request.headers)
    headers.pop("host", None)

    try:
        body = await request.body()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
                params=dict(request.query_params),
            )
        return resp.json()
    except httpx.ConnectError:
        raise HTTPException(502, "Orchestrator unreachable")
    except Exception as e:
        raise HTTPException(500, f"Gateway forward error: {e}")
