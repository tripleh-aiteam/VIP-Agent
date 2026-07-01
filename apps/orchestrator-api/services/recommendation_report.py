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
_CONF_KO = {"high": "높음", "medium": "보통", "low": "낮음", "높음": "높음", "보통": "보통", "낮음": "낮음"}


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


def _cur_price(db, code: str) -> Optional[float]:
    """Latest close for sanity-checking levels (guards stale/unadjusted old prices)."""
    from sqlalchemy import text
    try:
        r = db.execute(text("SELECT close FROM raw_daily_prices WHERE ticker=:t "
                            "ORDER BY date DESC LIMIT 1"), {"t": code}).fetchone()
        return float(r[0]) if r and r[0] else None
    except Exception:
        return None


def _levels(d: dict, cur: Optional[float] = None) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """(buy, target, stop). Wave levels ONLY when Wave says BUY; else technicals S/R.
    Any level absurd vs the current price (>3× or <1/3×) is dropped — this kills the
    stale swing-high bug (e.g. 삼성전기 목표 2,417,000원 on a ~340k stock)."""
    wv = d.get("method3_wave") or {}
    if (wv.get("verdict") or "").upper() == "BUY" and wv.get("entry"):
        lv = [wv.get("entry"), wv.get("target"), wv.get("stop")]
    else:
        tech = d.get("technicals") or {}
        lv = [tech.get("support"), tech.get("resistance"), None]
    if cur:
        lv = [(v if (v and 0.33 * cur <= v <= 3.0 * cur) else None) for v in lv]
    return (lv[0], lv[1], lv[2])


def _backdrop(db) -> str:
    """Compact Korean market backdrop from the Kiwoom / Newspaper / YouTube reports."""
    from services.master_report import _latest_report
    parts = ["## 📰 시장 배경 (오늘 아침)"]
    for rtype, label in (("kiwoom_report", "키움 (가격·기술)"),
                         ("newspaper_report", "신문 (뉴스)"),
                         ("youtube_report", "유튜브 (시장 심리)")):
        rep = _latest_report(db, rtype)
        summ = (rep.get("summary_ko") or rep.get("summary") or "").strip() if rep else ""
        parts.append(f"- **{label}:** {summ[:320]}" if summ else f"- **{label}:** (오늘 리포트 없음)")
    return "\n".join(parts)


_ORD_KO = {1: "최우선", 2: "두 번째", 3: "세 번째", 4: "네 번째", 5: "다섯 번째"}


def _pick_narrative(rank: int, d: dict, cur: Optional[float]) -> str:
    """A professional PROSE explanation for one pick — why, per method, then a verdict.
    Reads like an analyst note, not a bullet list."""
    name, code = d.get("name") or d.get("ticker"), d.get("ticker")
    dec = (d.get("decision") or "").upper(); deck = _DEC_KO.get(dec, dec)
    score = d.get("score"); conf = _CONF_KO.get(d.get("confidence"), d.get("confidence"))
    m1 = d.get("method1_ml") or {}; m1c = (m1.get("call") or "").upper()
    m1k = _DEC_KO.get(m1c, "보유"); acc, em = m1.get("accuracy_pct"), m1.get("expected_move_pct")
    m2 = d.get("method2_analysis") or {}; m2s = (m2.get("signal") or "").upper()
    m2k = {"BUY": "매수 우위", "SELL": "매도 우위", "WATCH": "관망", "HOLD": "관망"}.get(m2s, "관망")
    m2why = ", ".join((m2.get("reasons") or [])[:3]) or "뚜렷한 특이 신호 없음"
    wv = d.get("method3_wave") or {}; wvv = (wv.get("verdict") or "").upper()
    m3k = _VERD_KO.get(wvv, "데이터 없음")
    tech = d.get("technicals") or {}; techk = tech.get("summary_ko", "중립")
    sup, res = tech.get("support"), tech.get("resistance")
    ns = (d.get("news") or {}).get("score") or 0
    newsk = "호재 우세" if ns > 0 else "악재 우세" if ns < 0 else "중립"
    buy, target, stop = _levels(d, cur)

    def _f(v):
        return f"{int(v):,}원" if isinstance(v, (int, float)) and v else "-"
    m1why = {"BUY": "5일 기준 시장 대비 상대강세(아웃퍼폼)를 예측했습니다",
             "SELL": "5일 기준 시장 대비 상대약세(언더퍼폼)를 예측했습니다",
             "HOLD": "시장 대비 뚜렷한 우위를 찾지 못했습니다(신호 약함)"}.get(m1c, "신호가 약합니다")
    wvwhy = {"BUY": "강한 상승 파동 이후 깊은 눌림목(피보나치 매수권)에 들어와 분할 매수 자리로 판단됩니다",
             "WATCH": "상승 파동 자체는 강하지만 아직 매수 자리는 아니어서 눌림을 기다리는 구간입니다",
             "AVOID": "상승 추세가 약하거나 무효화되어 매매에 부적합합니다"}.get(wvv, "유효한 상승 파동이 확인되지 않았습니다")
    em_txt = (f" (5일 예상 변동 ±{abs(em)}%, 백테스트 정확도 {acc}%)" if em is not None else
              (f" (정확도 {acc}%)" if acc is not None else ""))
    return (
        f"### {rank}. {name} ({code}) — 종합 **{deck}** · 점수 {score} · 확신 {conf}\n\n"
        f"{name}은(는) 3가지 방법과 시장 자료를 종합한 결과 오늘 **{_ORD_KO.get(rank, str(rank)+'순위')} 후보**입니다. "
        f"**머신러닝(방법 1)**은 '{m1k}'으로, {m1why}{em_txt}. "
        f"**분석(방법 2)**은 실시간 호가·수급 기준 '{m2k}'이며, 그 근거는 {m2why}입니다. "
        f"**파동(방법 3)**은 '{m3k}'으로, {wvwhy}. "
        f"기술적으로는 {techk}(지지 {_f(sup)} / 저항 {_f(res)}), 뉴스 흐름은 {newsk}입니다.\n\n"
        f"➡️ **결론:** 위 결과를 모두 반영하면 {name}은(는) 오늘 **{deck}** 관점이 우세합니다. "
        f"매매 기준은 **살 가격 {_f(buy)} · 목표 {_f(target)} · 손절 {_f(stop)}**입니다."
    )


def build(db) -> dict[str, Any]:
    """Build the daily Top-5 recommendation report (Korean markdown + structured picks)."""
    now = datetime.now(KST)
    date = now.strftime("%Y-%m-%d")
    ranked = _rank(db)
    top5 = ranked[:5]
    buys = sum(1 for d in ranked if (d.get("decision") or "").upper() == "BUY")
    curs = {d.get("ticker"): _cur_price(db, d.get("ticker")) for d in top5}

    # at-a-glance table
    tbl = ["| # | 종목 | 판단 | 점수 | 방법1 | 방법2 | 방법3 | 살 가격 | 목표 |",
           "|---|------|------|------|-------|-------|-------|---------|------|"]
    for i, d in enumerate(top5, 1):
        buy, target, _stop = _levels(d, curs.get(d.get("ticker")))
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

    # market stance from the number of BUY signals
    stance = ("전반적으로 **매수 우위의 강세 국면**입니다" if buys >= 5 else
              "**선별적 매수가 유효한 중립 시장**입니다" if buys >= 2 else
              "**신규 매수보다 관망·방어가 우선인 신중 국면**입니다")
    t1 = top5[0] if top5 else {}
    t2 = top5[1] if len(top5) > 1 else {}
    final = (
        "## 🧭 오늘의 최종 결론\n\n"
        "이 추천은 다음 순서로 도출했습니다 — "
        "① **키움 리포트**로 가격·기술적 시장 상황을 확인하고, "
        "② **장 마감 후 네이버 시세**로 당일 종가와 수급을 반영했으며, "
        "③ **신문·유튜브 리포트**로 뉴스 흐름과 시장 심리를 파악한 뒤, "
        f"④ **3가지 방법(머신러닝·분석·파동)**으로 전체 {len(ranked)}개 종목을 정량 평가했습니다.\n\n"
        f"이 모든 결과를 종합한 결과, 오늘 시장의 매수 신호는 **{buys}종목**으로 {stance}. "
        + (f"최우선 후보는 **{t1.get('name')}**(종합 점수 {t1.get('score')})이며"
           + (f", **{t2.get('name')}**(점수 {t2.get('score')})가 뒤를 잇습니다. " if t2 else ". ") if t1 else "")
        + "신호가 제한적인 날에는 무리한 신규 매수보다, 상위 후보 중심의 **분할 매수와 손절 기준 준수**를 권장합니다."
    )

    md = "\n".join([
        f"# 💡 데일리 추천 리포트 — {date}",
        "",
        "본 리포트는 **키움 리포트(가격·기술) · 네이버 장마감 시세 · 신문(뉴스) · 유튜브(시장 심리)** 자료와 "
        "**3가지 방법(① 머신러닝 · ② 분석[호가·수급·박스권] · ③ 파동[엘리엇·피보나치])**을 순서대로 종합해 "
        "매일 아침 자동으로 작성됩니다. 아래는 오늘의 매수 후보 상위 5종목과 그 상세 근거입니다.",
        f"(전체 {len(ranked)}종목 분석 · 매수 신호 {buys}종목)",
        "",
        _backdrop(db),
        "",
        f"## 🏆 오늘의 추천 후보 5종목 (종합 점수순)",
        "🟢 = 매수 · ⚪ = 보유/관심. 매수 신호가 5개 미만인 날은 상위 관심 종목으로 채웁니다.",
        "\n".join(tbl),
        "",
        "## 🔎 종목별 상세 근거",
        "\n\n".join(_pick_narrative(i, d, curs.get(d.get("ticker"))) for i, d in enumerate(top5, 1)),
        "",
        final,
        "",
        "---",
        "※ 키움·네이버·신문·유튜브와 3가지 방법을 종합한 AI 참고 의견이며, 투자 권유나 수익 보장이 아닙니다. "
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
    subject = f"[VIP] 데일리 추천 리포트 — {rep['date']}"
    # SHORT email body (3-4 lines) — the full report is the attached .docx.
    picks = rep.get("picks") or []
    top = picks[0] if picks else {}
    top_line = (f"최우선 후보: {top.get('name')} ({_DEC_KO.get((top.get('decision') or '').upper(), '-')}, 점수 {top.get('score')})"
                if top else "오늘 뚜렷한 매수 후보 없음")
    body = (
        f"{rep['date']} 데일리 추천 리포트입니다.\n"
        f"키움·네이버·신문·유튜브 + 3가지 방법(머신러닝·분석·파동)을 종합한 오늘의 매수 후보 상위 5종목입니다.\n"
        f"오늘 매수 신호 {rep['buys']}종목 · {top_line}.\n"
        f"자세한 근거와 매매 기준(살 가격/목표/손절)은 첨부된 리포트를 확인해 주세요."
    )
    try:
        docx = markdown_to_docx(title=f"데일리 추천 리포트 {rep['date']}", markdown_text=md)
        res = send_email_with_docs(to, subject, body, [(f"recommendation_{rep['date']}.docx", docx)])
    except Exception as e:
        log.warning(f"rec-report: email failed: {str(e)[:150]}")
        res = {"ok": False, "reason": str(e)[:150]}
    return {"sent": res, "to": to, "date": rep["date"], "picks": rep["picks"], "analyzed": rep["analyzed"]}
