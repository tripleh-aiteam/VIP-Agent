"""
VIP AI Platform — Agent Router
GET /agents, POST /agents/register, POST /agents/upload-data
"""

import csv
import io
from pathlib import Path

from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from db.base import get_db
from services import agent_service

router = APIRouter(tags=["agents"])

# Where uploaded CSVs land (matches adapters.csv_data_adapter.UPLOADS_DIR)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
UPLOAD_BASE = _REPO_ROOT / "data" / "uploads"
UPLOAD_BASE.mkdir(parents=True, exist_ok=True)


# --- Expected CSV columns per agent type (returned in error messages so user knows the format) ---
EXPECTED_COLUMNS = {
    "asset":  ["name", "type", "value_krw", "monthly_income_krw", "occupancy"],
    "realty": ["address", "type", "price_krw", "size_sqm", "vacancy"],
    "stock":  ["symbol", "shares", "avg_buy_krw"],
}


class RegisterAgentBody(BaseModel):
    name: str = Field(...)
    type: str = Field(..., description="asset | stock | realty | custom")
    endpoint_url: str = Field(...)
    trace_id: str = Field(default="system")
    version: str = Field(default="0.1.0")
    owner_team: Optional[str] = None
    auth_type: str = Field(default="none")
    is_mock: bool = Field(default=False)
    capabilities: Optional[dict] = None


@router.get("/agents")
def list_agents(db: Session = Depends(get_db)):
    """List all registered agents."""
    agents = agent_service.list_agents(db)
    return [
        {
            "id": str(a.id),
            "name": a.name,
            "type": a.type,
            "version": a.version,
            "owner_team": a.owner_team,
            "endpoint_url": a.endpoint_url,
            "auth_type": a.auth_type,
            "status": a.status,
            "is_mock": a.is_mock,
            "capabilities": a.capabilities_json,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in agents
    ]


@router.post("/agents/upload-data")
async def upload_agent_data(agent_type: str, file: UploadFile = File(...)):
    """
    Upload real CSV data for an agent (asset, realty, stock).
    File is saved to data/uploads/{agent_type}/latest.csv and read by the
    CSV-driven adapter when UPLOADED_DATA_ENABLED=true is in .env.

    Expected columns per agent type are returned in the response on success
    so the user can verify the parser detected their data correctly.
    """
    agent_type = agent_type.lower().strip()
    if agent_type not in EXPECTED_COLUMNS:
        raise HTTPException(400, f"Unknown agent_type '{agent_type}'. Use: {list(EXPECTED_COLUMNS.keys())}")

    raw = await file.read()
    if len(raw) > 10_000_000:
        raise HTTPException(413, "File too large (max 10MB).")

    # Decode + validate as CSV
    try:
        text = raw.decode("utf-8-sig", errors="replace")
    except Exception as e:
        raise HTTPException(400, f"Could not decode file: {e}")

    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    rows = list(reader)
    if not rows:
        raise HTTPException(400, "CSV is empty.")

    expected = EXPECTED_COLUMNS[agent_type]
    missing = [c for c in expected if c not in headers]

    # Save to data/uploads/{agent_type}/latest.csv
    target_dir = UPLOAD_BASE / agent_type
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "latest.csv"
    with open(target_path, "wb") as f:
        f.write(raw)

    return {
        "uploaded": True,
        "agent_type": agent_type,
        "filename": file.filename,
        "rows": len(rows),
        "headers_detected": headers,
        "expected_columns": expected,
        "missing_columns": missing,
        "saved_to": str(target_path.relative_to(_REPO_ROOT)),
        "active": missing == [] and bool(rows),
        "next_step": (
            "Set UPLOADED_DATA_ENABLED=true in .env and restart orchestrator to use this data."
            if missing == []
            else f"Add missing columns: {missing}. Then re-upload."
        ),
    }


@router.get("/agents/upload-data/status")
def upload_data_status():
    """Show what's currently uploaded for each agent type."""
    out = {}
    import os
    for agent_type in EXPECTED_COLUMNS:
        path = UPLOAD_BASE / agent_type / "latest.csv"
        if path.exists():
            stat = path.stat()
            try:
                with open(path, encoding="utf-8-sig") as f:
                    rows = sum(1 for _ in csv.DictReader(f))
            except Exception:
                rows = 0
            out[agent_type] = {
                "uploaded": True,
                "rows": rows,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            }
        else:
            out[agent_type] = {"uploaded": False}
    out["enabled"] = os.getenv("UPLOADED_DATA_ENABLED", "false").lower() in ("true", "1", "yes")
    out["yahoo_enabled"] = os.getenv("YAHOO_FINANCE_ENABLED", "false").lower() in ("true", "1", "yes")
    return out


@router.post("/agents/register", status_code=201)
def register_agent(body: RegisterAgentBody, db: Session = Depends(get_db)):
    """Register a new agent or update an existing one."""
    agent = agent_service.register_agent(
        db=db,
        name=body.name,
        agent_type=body.type,
        endpoint_url=body.endpoint_url,
        trace_id=body.trace_id,
        version=body.version,
        owner_team=body.owner_team,
        auth_type=body.auth_type,
        is_mock=body.is_mock,
        capabilities=body.capabilities,
    )
    db.commit()
    return {
        "registered": True,
        "id": str(agent.id),
        "name": agent.name,
        "type": agent.type,
        "status": agent.status,
    }
