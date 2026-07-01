"""recommendation_report.py — the daily morning Recommendation Report.

Ranks the tracked universe with the 3-method decision engine (Method 1 ML +
Method 2 Analysis + Method 3 Wave = services.decision_agent.decide), blends in
the Kiwoom / Newspaper / YouTube daily reports as market backdrop, and produces
a **Top-5 "stocks to buy today"** report with the full per-stock reasoning (the
근거 / proof for WHY each is recommended). Saved to orch_reports and emailed each
morning at 07:30 KST. Advisory only — not investment advice.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.logger import log

KST = timezone(timedelta(hours=9))
# During the pilot only the owner receives it; expand after sign-off.
TEST_RECIPIENTS = ["tripleh.agents@gmail.com"]

_DEC_KO = {"BUY": "매수", "SELL": "매도", "HOLD": "보유"}
_VERD_KO = {"BUY": "매수", "WATCH": "관망", "AVOID": "회피"}


def _universe() -> dict[str, str]:
    """The 51 tracked names we have full 3-method data for (excl. the index proxy)."""
    from services.prediction_service import NAMES
    try:
        from routers.predictions import WAVE_EXTRA_NAMES
    except Exception:
        WAVE_EXTRA_NAMES = {}
    uni = {c: n for c, n in NAMES.items() if c != "069500"}
    uni.update(WAVE_EXTRA_NAMES)
    return uni


def _rank(db) -> list[dict]:
    """Run the 3-method decide on every tracked stock, ranked best-BUY first."""
    from services.decision_agent import decide
    rows: list[dict] = []
    for code, name in _universe().items():
        try:
            d = decide(db, code)
            if isinstance(d, dict) and d.get("decision"):
                rows.append(d)
        except Exception as e:
            log.warning(f"rec-report: decide {code} failed: {str(e)[:100]}")

    def _key(d: dict):
        dec = (d.get("decision") or "").upper()
        pri = 2 if dec == "BUY" else 1 if dec == "HOLD" else 0
        return (pri, float(d.get("score") or 0))
    rows.sort(key=_key, reverse=True)
    return rows


def _levels(d: dict) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """(buy, target, stop). Use Method-3 Wave levels ONLY when Wave says BUY (its
    entry/target are for an active setup); otherwise use technicals support/resistance
    — avoids showing a stale swing-high target for stocks Wave isn't buying."""
    wv = d.get("method3_wave") or {}
    if (wv.get("verdict") or "").upper() == "BUY" and wv.get("entry"):
        return (wv.get("entry"), wv.get("target"), wv.get("stop"))
    tech = d.get("technicals") or {}
    return (tech.get("support"), tech.get("resistance"), None)


def _backdrop(db) -> str:
    """Compact Korean market backdrop from the Kiwoom / Newspaper / YouTube reports."""
    from services.master_report import _latest_report
    parts = ["## 📰 시장 배경 (오늘 아침)"]
    for rtype, label in (("kiwoom_report", "키움 (가격·기술)"),
                         ("newspaper_report", "신문 (뉴스)"),
                         ("youtube_report", "유튜브 (시장 심리)")):
        rep = _latest_report(db, rtype)
        summ = (rep.get("summary") or rep.get("summary_en") or "").strip() if rep else ""
        parts.append(f"- **{label}:** {summ[:320]}" if summ else f"- **{label}:** (오늘 리포트 없음)")
    return "\n".join(parts)


def _pick_block(rank: int, d: dict) -> str:
    """Per-pick reasoning block (the 근거/proof) for one Top-5 stock."""
    name = d.get("name") or d.get("ticker")
    code = d.get("ticker")
    dec = _DEC_KO.get((d.get("decision") or "").upper(), d.get("decision"))
    score = d.get("score")
    m1 = (d.get("method1_ml") or {}).get("call") or "-"
    m1k = _DEC_KO.get((m1 or "").upper(), m1)
    m2 = (d.get("method2_analysis") or {}).get("signal") or "-"
    m2k = {"BUY": "매수 우위", "SELL": "매도 우위", "WATCH": "관망", "HOLD": "관망"}.get((m2 or "").upper(), m2)
    wv = d.get("method3_wave") or {}
    m3 = _VERD_KO.get((wv.get("verdict") or "").upper(), wv.get("verdict") or "-")
    buy, target, stop = _levels(d)

    def _f(v):
        return f"{int(v):,}원" if isinstance(v, (int, float)) and v else "-"
    lines = [
        f"### {rank}. {name} ({code}) — {dec} · 점수 {score}",
        f"- 🤖 **방법 1 (머신러닝):** {m1k}",
        f"- 📈 **방법 2 (분석·수급/호가):** {m2k}",
        f"- 🌊 **방법 3 (파동·엘리엇/피보나치):** {m3}"
        + (f" · 진입 {_f(wv.get('entry'))} / 목표 {_f(wv.get('target'))} / 손절 {_f(wv.get('stop'))} (R:R {wv.get('rr')})"
           if (wv.get("verdict") or "").upper() == "BUY" and wv.get("entry") else ""),
        f"- 🎯 **매매 기준:** 살 가격 {_f(buy)} · 목표 {_f(target)} · 손절 {_f(stop)}",
    ]
    return "\n".join(lines)


def build(db) -> dict[str, Any]:
    """Build the daily Top-5 recommendation report (Korean markdown + structured picks)."""
    now = datetime.now(KST)
    date = now.strftime("%Y-%m-%d")
    ranked = _rank(db)
    top5 = ranked[:5]
    buys = sum(1 for d in ranked if (d.get("decision") or "").upper() == "BUY")

    # Top-5 summary table
    tbl = ["| # | 종목 | 판단 | 점수 | 방법1 | 방법2 | 방법3 | 살 가격 | 목표 |",
           "|---|------|------|------|-------|-------|-------|---------|------|"]
    for i, d in enumerate(top5, 1):
        buy, target, _stop = _levels(d)
        m1 = _DEC_KO.get(((d.get("method1_ml") or {}).get("call") or "").upper(), "-")
        m2 = {"BUY": "매수", "SELL": "매도", "WATCH": "관망", "HOLD": "관망"}.get(
            ((d.get("method2_analysis") or {}).get("signal") or "").upper(), "-")
        m3 = _VERD_KO.get(((d.get("method3_wave") or {}).get("verdict") or "").upper(), "-")
        _bf = f"{int(buy):,}" if buy else "-"
        _tf = f"{int(target):,}" if target else "-"
        _dec = (d.get("decision") or "").upper()
        _mark = "🟢" if _dec == "BUY" else "⚪"
        tbl.append(f"| {i} | {d.get('name')} ({d.get('ticker')}) | {_mark} {_DEC_KO.get(_dec,'-')} "
                   f"| {d.get('score')} | {m1} | {m2} | {m3} | {_bf} | {_tf} |")

    md = "\n".join([
        f"# 💡 데일리 추천 리포트 — {date}",
        f"3가지 방법(머신러닝·분석·파동) + 키움·신문·유튜브를 종합한 오늘의 매수 후보 TOP 5입니다.",
        f"(전체 {len(ranked)}종목 분석 · 매수 신호 {buys}종목)",
        "",
        _backdrop(db),
        "",
        f"## 🏆 오늘의 TOP 5 후보 (종합 점수순 · 매수 신호 {buys}종목)",
        "🟢 = 매수(BUY) · ⚪ = 보유/관심(HOLD). 오늘 매수 신호가 5개 미만이면 상위 관심 종목으로 채웁니다.",
        "\n".join(tbl),
        "",
        "## 🔎 종목별 추천 근거 (왜 추천하는가)",
        "\n\n".join(_pick_block(i, d) for i, d in enumerate(top5, 1)),
        "",
        "---",
        "※ 3가지 방법과 키움·뉴스·유튜브를 종합한 AI 참고 의견이며, 투자 권유나 수익 보장이 아닙니다. "
        "매매 전 반드시 직접 확인하세요.",
    ])
    picks = [{"rank": i, "ticker": d.get("ticker"), "name": d.get("name"),
              "decision": d.get("decision"), "score": d.get("score")}
             for i, d in enumerate(top5, 1)]
    return {"date": date, "markdown_ko": md, "picks": picks,
            "buys": buys, "analyzed": len(ranked)}


def send(db, recipients: Optional[list[str]] = None) -> dict[str, Any]:
    """Build + save + email the recommendation report as a .docx. Pilot → owner only."""
    from db.models import OrchReport
    from services.docx_export import markdown_to_docx
    from services.report_email import send_email_with_docs

    rep = build(db)
    md = rep["markdown_ko"]

    # save to orch_reports (Reports page + history)
    try:
        row = OrchReport(report_type="recommendation_report",
                         content_json={"report_type": "recommendation_report",
                                       "summary": f"오늘의 TOP 5 매수 추천 ({rep['date']})",
                                       "report": rep, "markdown": md})
        db.add(row); db.commit()
    except Exception as e:
        log.warning(f"rec-report: save failed: {str(e)[:120]}")
        db.rollback()

    to = recipients or TEST_RECIPIENTS
    subject = f"[VIP] 데일리 추천 리포트 — {rep['date']} (TOP 5 매수)"
    try:
        docx = markdown_to_docx(title=f"데일리 추천 리포트 {rep['date']}", markdown_text=md)
        res = send_email_with_docs(to, subject, md, [(f"recommendation_{rep['date']}.docx", docx)])
    except Exception as e:
        log.warning(f"rec-report: email failed: {str(e)[:150]}")
        res = {"ok": False, "reason": str(e)[:150]}
    return {"sent": res, "to": to, "date": rep["date"], "picks": rep["picks"], "analyzed": rep["analyzed"]}
