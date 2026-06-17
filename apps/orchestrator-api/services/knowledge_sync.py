"""
knowledge_sync — feed the RAG knowledge base (assistant_knowledge_chunks) with the
content the platform already produces, so the chatbot can answer from REAL grounded
text instead of the LLM's memory (Phase 2: smarter via RAG, avoid hallucination).

Two sources:
  • orch_reports rows (Kiwoom / Newspaper / YouTube / Recommendation / master) —
    the freshest domain knowledge, ingested as text documents.
  • a small, STABLE data-dictionary seed (what each metric means, the covered
    universe, how the bot decides) — stable knowledge that never goes stale, so
    RAG always has something to ground "what does X mean / how do you decide".

Dedup: each report becomes a KB file named ``report::{type}::{id}``; we skip it if
that file already exists for the agent. Reuses the proven ``ingest_file`` pipeline
(parses + embeds when EMBED_PROVIDER is set, text-only otherwise).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from services.knowledge_ingest import ingest_file
from services.logger import log


def _report_to_text(cj: Any, _depth: int = 0) -> str:
    """Flatten an arbitrary content_json into readable text (recursive)."""
    if cj is None or _depth > 6:
        return ""
    if isinstance(cj, str):
        return cj
    if isinstance(cj, (int, float, bool)):
        return str(cj)
    out: list[str] = []
    if isinstance(cj, dict):
        # Prefer human-readable fields first, then everything else.
        for key in ("title", "headline", "summary", "text", "body", "content",
                    "section_ko", "analysis", "report", "markdown"):
            if key in cj and cj[key]:
                out.append(_report_to_text(cj[key], _depth + 1))
        for k, v in cj.items():
            if k in ("title", "headline", "summary", "text", "body", "content",
                     "section_ko", "analysis", "report", "markdown"):
                continue
            if k in ("embedding", "source_run_ids", "run_id", "id"):
                continue
            piece = _report_to_text(v, _depth + 1)
            if piece and len(piece) > 1:
                label = k if isinstance(k, str) else ""
                out.append(f"{label}: {piece}" if label else piece)
    elif isinstance(cj, (list, tuple)):
        for item in cj:
            piece = _report_to_text(item, _depth + 1)
            if piece:
                out.append(piece)
    return "\n".join(p for p in out if p and p.strip())


def _existing_report_files(db: Session, agent_id: str) -> set[str]:
    try:
        rows = db.execute(sa_text("""
            SELECT filename FROM assistant_knowledge_files
            WHERE agent_id = :a AND filename LIKE 'report::%'
        """), {"a": agent_id}).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def sync_reports_to_kb(db: Session, *, agent_id: str = "stock",
                       max_reports: int = 60) -> dict[str, Any]:
    """Ingest recent orch_reports into the agent's KB (skips already-ingested).
    Returns {ingested, skipped, chunks}."""
    from db.models import OrchReport

    have = _existing_report_files(db, agent_id)
    ingested = skipped = chunks = 0
    try:
        rows = (db.query(OrchReport)
                .order_by(OrchReport.created_at.desc())
                .limit(max_reports).all())
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "ingested": 0}

    for r in rows:
        fname = f"report::{r.report_type}::{r.id}"
        if fname in have:
            skipped += 1
            continue
        body = _report_to_text(r.content_json)
        if not body or len(body.strip()) < 40:
            skipped += 1
            continue
        created = r.created_at.strftime("%Y-%m-%d") if r.created_at else ""
        header = f"# {r.report_type} ({created})\n\n"
        try:
            res = ingest_file(db, agent_id=agent_id, filename=f"{fname}.md",
                              mime_type="text/markdown",
                              blob=(header + body).encode("utf-8"),
                              uploaded_by="knowledge_sync")
            db.commit()
            ingested += 1
            chunks += int(res.get("chunk_count") or 0)
        except Exception as e:
            db.rollback()
            log.warning(f"knowledge_sync: ingest {fname} failed: {str(e)[:120]}")
            skipped += 1
    log.info(f"knowledge_sync[{agent_id}]: ingested={ingested} skipped={skipped} chunks={chunks}",
             extra={"action": "knowledge.sync"})
    return {"ok": True, "agent_id": agent_id, "ingested": ingested,
            "skipped": skipped, "chunks": chunks}


def reset_synced_kb(db: Session, *, agent_ids: tuple[str, ...] = ("stock", "vip")) -> dict[str, Any]:
    """Delete only the auto-synced KB files (report:: and seed::) so they can be
    re-ingested — e.g. after switching EMBED_PROVIDER to openai so chunks get real
    embeddings. Does NOT touch boss-uploaded documents."""
    deleted = {}
    for aid in agent_ids:
        try:
            rows = db.execute(sa_text("""
                SELECT id FROM assistant_knowledge_files
                WHERE agent_id = :a AND (filename LIKE 'report::%' OR filename LIKE 'seed::%')
            """), {"a": aid}).fetchall()
            ids = [r[0] for r in rows]
            for fid in ids:
                db.execute(sa_text("DELETE FROM assistant_knowledge_chunks WHERE file_id = :f"), {"f": fid})
                db.execute(sa_text("DELETE FROM assistant_knowledge_files WHERE id = :f"), {"f": fid})
            db.commit()
            deleted[aid] = len(ids)
        except Exception as e:
            db.rollback()
            deleted[aid] = f"error: {str(e)[:80]}"
    return {"ok": True, "deleted_files": deleted}


def ingest_one_report(db: Session, report, *, agent_id: str = "stock") -> bool:
    """Ingest a single freshly-generated report row into the KB (auto-ingest hook).
    Best-effort; returns True if a new file was indexed."""
    try:
        fname = f"report::{report.report_type}::{report.id}"
        if fname in _existing_report_files(db, agent_id):
            return False
        body = _report_to_text(report.content_json)
        if not body or len(body.strip()) < 40:
            return False
        created = report.created_at.strftime("%Y-%m-%d") if report.created_at else ""
        blob = (f"# {report.report_type} ({created})\n\n" + body).encode("utf-8")
        ingest_file(db, agent_id=agent_id, filename=f"{fname}.md",
                    mime_type="text/markdown", blob=blob, uploaded_by="auto_sync")
        db.commit()
        return True
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        log.warning(f"knowledge_sync.ingest_one: {str(e)[:120]}")
        return False


# --- Stable data-dictionary seed -------------------------------------------
_SEED_FILENAME = "seed::stock-data-dictionary.md"

_SEED_DOC = """# 주식 어시스턴트 데이터 사전 & 해석 가이드

이 문서는 주식 챗봇이 답변을 근거(grounding)로 삼는 안정적 참고 지식이다. 시세처럼
변하는 값은 담지 않고, 의미·해석·동작 방식만 담는다.

## 챗봇이 직접 조회하는 실데이터 (출처)
- 현재가/시세: NAVER 실시간 (장중) + 시간외(NXT). 절대 기억으로 만들지 않는다.
- 과거 종가/일봉: get_historical_price / stock_get_daily_history (NAVER 일봉 OHLCV).
- 수급(외국인·기관·개인 순매수), 공매도, 투자자별 흐름: 키움/네이버 실데이터.
- 뉴스: 최근 Google 뉴스 검색.
- 추천: 뉴스+기술분석+다중 LLM 교차검증 앙상블.

## 지표 해석 (용어 → 쉬운 의미)
- 등락률(change_pct): 전일 종가 대비 오늘 가격 변화율. ▲ 상승 / ▼ 하락.
- 수급: 외국인·기관의 순매수(매수-매도). 지속 순매수면 매수 주체가 우호적.
- 공매도 비율: 하락에 베팅한 거래 비중. 급증은 약세 신호로 해석될 수 있음.
- 거래량 급증: 평소보다 거래가 몰림 → 변동성/관심 확대 신호.
- 이동평균(MA5/20/60): 단기·중기 추세선. 종가가 위면 강세, 아래면 약세 경향.
- 목표가/손절가: 분석 도구가 제시한 매도/방어 기준선.

## 답변 원칙 (환각 방지)
- 가격·수치는 반드시 실데이터 도구 결과나 검색 근거에서만 인용한다.
- 과거 특정 날짜의 가격은 get_historical_price로 조회하고 추측하지 않는다.
- 근거가 없으면 "확인되지 않음"이라고 말하거나 웹 검색으로 보강한다.
- 투자 판단에는 항상 데이터 근거(추세·수급·뉴스)와 리스크를 함께 제시한다.
- 최종 책임은 사용자에게 있다는 면책은 UI 하단 고정 문구로 처리한다.
"""


def seed_data_dictionary(db: Session, *, agent_ids: tuple[str, ...] = ("stock", "vip")) -> dict[str, Any]:
    """Ingest the stable data-dictionary doc once per agent (idempotent)."""
    done = {}
    for aid in agent_ids:
        try:
            have = _existing_report_files(db, aid) | {
                r[0] for r in db.execute(sa_text(
                    "SELECT filename FROM assistant_knowledge_files "
                    "WHERE agent_id=:a AND filename=:f"), {"a": aid, "f": _SEED_FILENAME}).fetchall()
            }
            if _SEED_FILENAME in have:
                done[aid] = "exists"
                continue
            ingest_file(db, agent_id=aid, filename=_SEED_FILENAME,
                        mime_type="text/markdown", blob=_SEED_DOC.encode("utf-8"),
                        uploaded_by="knowledge_seed")
            db.commit()
            done[aid] = "ingested"
        except Exception as e:
            db.rollback()
            done[aid] = f"error: {str(e)[:80]}"
    return {"ok": True, "seed": done}
