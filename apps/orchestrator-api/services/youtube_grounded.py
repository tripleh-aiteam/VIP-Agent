"""youtube_grounded — read + surface the colleague's GROUNDED YouTube report.

An external GPU pipeline (Whisper transcription + grounded insight extraction)
writes ONE row each morning to ``orch_reports`` with:
    report_type = 'youtube_report'  AND  delivery_channel = 'gpu_youtube'
and pre-rendered .docx/.pdf files in Supabase Storage (public URLs in
``content_json.files``). We ONLY read + pass it through — never regenerate.

We ALWAYS use the LATEST such row (by created_at), so if the colleague re-runs
and writes a newer row, VIP picks it up automatically.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from sqlalchemy import desc

from services.logger import log
from db.models import OrchReport

# Download order = attachment order (KO first, then EN).
_FILE_ORDER = ("docx_ko_url", "pdf_ko_url", "docx_en_url", "pdf_en_url")


def latest_row(db) -> Optional[OrchReport]:
    """Most-recent GROUNDED YouTube report row (gpu_youtube), or None.

    Uses ONLY delivery_channel='gpu_youtube' — never the deprecated price-table
    'youtube_report' rows that the old pipeline wrote."""
    return (db.query(OrchReport)
            .filter(OrchReport.report_type == "youtube_report",
                    OrchReport.delivery_channel == "gpu_youtube")
            .order_by(desc(OrchReport.created_at))
            .first())


def _download(url: str, timeout: float = 45.0) -> Optional[bytes]:
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True)
        if r.status_code == 200 and r.content:
            return r.content
        log.warning(f"youtube_grounded: download HTTP {r.status_code} {url[:70]}")
    except Exception as e:
        log.warning(f"youtube_grounded: download failed {url[:70]}: {str(e)[:80]}")
    return None


def fetch_files(content: dict) -> list[tuple[str, bytes]]:
    """Download the 4 report files BYTE-FOR-BYTE → [(filename, bytes), ...].
    Missing/failed URLs are skipped (never fabricated)."""
    files = (content or {}).get("files") or {}
    out: list[tuple[str, bytes]] = []
    for key in _FILE_ORDER:
        url = files.get(key)
        if not url:
            continue
        data = _download(url)
        if data:
            fname = url.rstrip("/").split("/")[-1] or f"youtube_{key}"
            out.append((fname, data))
    return out


def latest_payload(db) -> Optional[dict[str, Any]]:
    """Structured payload for the web UI tab — the latest grounded report's
    content_json plus row id/date. None if no grounded row exists yet."""
    row = latest_row(db)
    if not row:
        return None
    c = row.content_json or {}
    return {
        "row_id": str(row.id),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "generated_at_kst": c.get("generated_at_kst"),
        "window": c.get("window"),
        "email_subject": c.get("email_subject"),
        "files": c.get("files") or {},
        "report": c.get("report") or {},
    }


def deliver(db, recipients, *, lang: str = "ko") -> dict[str, Any]:
    """Email the LATEST grounded YouTube report to ``recipients`` using the
    pipeline's EXACT email_subject + email_body_ko and attaching the 4 files
    byte-for-byte. ``recipients`` may be a string or a list. Returns a status
    dict (never raises)."""
    row = latest_row(db)
    if not row:
        return {"ok": False, "reason": "no grounded youtube report row yet"}
    c = row.content_json or {}
    subject = c.get("email_subject") or "유튜브 시장 리포트"
    body = (c.get("email_body_ko")
            or "유튜브 시장 분석 리포트입니다. 첨부된 파일을 확인해 주세요.")
    files = fetch_files(c)
    if not files:
        return {"ok": False, "reason": "no downloadable files in the latest row"}
    try:
        from services.report_email import send_email_with_docs
        res = send_email_with_docs(recipients, subject, body, files)
    except Exception as e:
        return {"ok": False, "reason": f"send failed: {str(e)[:120]}"}
    out = {
        "ok": bool(res.get("ok")),
        "reason": res.get("reason"),
        "row_id": str(row.id),
        "date": (c.get("generated_at_kst") or str(row.created_at) or "")[:10],
        "n_files": len(files),
        "to": res.get("to"),
        "subject": subject,
    }
    log.info(f"youtube_grounded: delivered {out['n_files']} files "
             f"({'ok' if out['ok'] else out.get('reason')}) row={out['row_id']}",
             extra={"action": "youtube.grounded.deliver"})
    return out
