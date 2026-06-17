"""
Assistant Knowledge Base API
============================

Endpoints:
  POST   /assistant/knowledge/upload      — multipart upload (xlsx/pdf/docx/pptx/csv/txt/md)
  POST   /assistant/knowledge/search      — vector similarity search (debug / RAG inspection)
  GET    /assistant/knowledge/files       — list ingested files for an agent
  DELETE /assistant/knowledge/files/{id}  — remove a file + its chunks

All endpoints are agent-scoped via the `agentId` query/body param so the
VIP Assistant only sees VIP knowledge and Realty only sees Realty knowledge.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.base import get_db
from services.knowledge_ingest import (
    SUPPORTED_EXTS,
    delete_file,
    ingest_file,
    list_files,
    rag_retrieve,
)

log = logging.getLogger("assistant_knowledge")
router = APIRouter(prefix="/assistant/knowledge", tags=["assistant-knowledge"])


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_knowledge(
    file: UploadFile = File(...),
    agentId: str = Form(...),
    uploadedBy: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Parse + embed + store a file for an agent's knowledge base.

    Body (multipart/form-data):
      - file:       the document (xlsx, pdf, docx, pptx, csv, txt, md, json)
      - agentId:    which agent owns this knowledge (vip / realty / asset / ...)
      - uploadedBy: optional uploader identifier (email/name)

    Synchronous: returns once the file is fully indexed. For 100MB+ files we
    can move this to a background worker later — for now, the assistant inbox
    UX is "click upload → see chunk count appear" so sync is friendlier.
    """
    if not file.filename:
        raise HTTPException(400, "file has no filename")
    fn = file.filename.lower()
    if not any(fn.endswith(ext) for ext in SUPPORTED_EXTS):
        raise HTTPException(
            400,
            f"Unsupported file type. Supported extensions: {', '.join(SUPPORTED_EXTS)}",
        )
    blob = await file.read()
    if not blob:
        raise HTTPException(400, "empty file")
    # Hard cap to keep one upload from hogging the worker (50MB)
    if len(blob) > 50 * 1024 * 1024:
        raise HTTPException(
            413,
            "File too large (>50MB). Split it into smaller documents.",
        )

    try:
        result = ingest_file(
            db,
            agent_id=agentId,
            filename=file.filename,
            mime_type=file.content_type,
            blob=blob,
            uploaded_by=uploadedBy,
        )
    except RuntimeError as e:
        # No OPENAI key, or embed API rejected the request
        log.exception("ingest failed (config): %s", e)
        raise HTTPException(503, f"Embedding service unavailable: {e}")
    except Exception as e:
        log.exception("ingest failed: %s", e)
        raise HTTPException(500, f"Ingest failed: {e}")

    return {
        "ok": True,
        "file_id":     result["file_id"],
        "filename":    file.filename,
        "chunk_count": result["chunk_count"],
        "status":      result["status"],
        "size_bytes":  len(blob),
    }


# ---------------------------------------------------------------------------
# Search (debug + power-user — the assistant calls rag_retrieve internally)
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    agentId: str = Field(..., description="Agent scope")
    query: str = Field(..., min_length=1)
    topK: int = Field(default=8, ge=1, le=40)
    minSim: float = Field(default=0.35, ge=0.0, le=1.0)


@router.post("/search")
def search_knowledge(req: SearchRequest, db: Session = Depends(get_db)):
    hits = rag_retrieve(
        db,
        agent_id=req.agentId,
        query=req.query,
        top_k=req.topK,
        min_sim=req.minSim,
    )
    return {"ok": True, "hits": hits, "count": len(hits)}


def _run_kb_sync(reset: bool) -> None:
    """Heavy KB sync, run in a background thread so the HTTP call can't time out
    (ingesting 60 reports x 2 agents — slower still when embeddings are on)."""
    from db.base import SessionLocal
    from services.knowledge_sync import (seed_data_dictionary, sync_reports_to_kb,
                                          reset_synced_kb)
    db = SessionLocal()
    try:
        if reset:
            reset_synced_kb(db, agent_ids=("stock", "vip"))
        seed_data_dictionary(db, agent_ids=("stock", "vip"))
        for aid in ("stock", "vip"):
            sync_reports_to_kb(db, agent_id=aid)
    finally:
        db.close()


@router.post("/sync")
def sync_knowledge(reset: bool = False, wait: bool = False,
                   background_tasks: BackgroundTasks = None,
                   db: Session = Depends(get_db)):
    """Populate the RAG knowledge base (Phase 2): ingest the stable data-dictionary
    seed + recent orch_reports for BOTH the stock and vip agents. Idempotent.
    reset=true first deletes prior auto-synced chunks and re-ingests (use after
    switching EMBED_PROVIDER=openai so chunks get embeddings). By default runs in
    the background (returns immediately); pass wait=true to run synchronously."""
    if wait:
        _run_kb_sync(reset)
        from services.knowledge_ingest import rag_retrieve
        n = len(rag_retrieve(db, agent_id="vip", query="추천 종목", top_k=5, min_sim=0.1))
        return {"ok": True, "mode": "sync", "vip_sample_hits": n}
    background_tasks.add_task(_run_kb_sync, reset)
    return {"ok": True, "mode": "background", "note": "KB sync started; poll /search to confirm."}


# ---------------------------------------------------------------------------
# File management
# ---------------------------------------------------------------------------

@router.get("/files")
def list_knowledge_files(agentId: str, db: Session = Depends(get_db)):
    return {"ok": True, "files": list_files(db, agent_id=agentId)}


@router.delete("/files/{file_id}")
def delete_knowledge_file(file_id: str, agentId: str, db: Session = Depends(get_db)):
    n = delete_file(db, agent_id=agentId, file_id=file_id)
    if n == 0:
        raise HTTPException(404, "file not found (or wrong agentId)")
    return {"ok": True, "deleted": n}
