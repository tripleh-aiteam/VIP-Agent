"""recommendation_engine — detailed daily KR buy-recommendation engine.

Implements the "Korea Market Daily Investment Recommendation" spec: pick 5 KR
stocks worth buying today, each with 핵심 요약 / 추천 매수가 / 추천 매도가 and three
independent recommendation points, in formal investor Korean with strict writing
rules. Buy/sell prices are computed from REAL price data (close + volatility), not
invented. Picks are upserted into a daily recommendation history (orch_reports,
report_type='recommendation_daily', one row per KST date)."""

from __future__ import annotations

import re
import statistics
from datetime import datetime
from typing import Any, Optional

from services.logger import log


def _universe() -> dict[str, str]:
    """Liquid KR universe {6-digit code: display name (Korean preferred)}."""
    try:
        from services.stock_data_tools import _NAME_TO_TICKER
    except Exception:
        return {}
    out: dict[str, str] = {}
    for name, code in _NAME_TO_TICKER.items():
        is_ko = any("가" <= c <= "힣" for c in name)
        if code not in out or (is_ko and not any("가" <= c <= "힣" for c in out[code])):
            out[code] = name
    return out


def _round_tick(p: Optional[float]) -> Optional[int]:
    """Round a KRW price to a sensible KRX tick."""
    if p is None:
        return None
    p = float(p)
    step = (1000 if p >= 500000 else 500 if p >= 100000 else 100 if p >= 50000
            else 50 if p >= 10000 else 10 if p >= 5000 else 5)
    return int(round(p / step) * step)


def _price_stats(code: str) -> Optional[dict]:
    """Real close / change% / ~14-day volatility / 20d range for one KR code."""
    try:
        from services import naver_stock
        hist = naver_stock.daily_history(code, days=20)
    except Exception:
        hist = []
    closes = [h["close"] for h in (hist or []) if h.get("close")]
    if len(closes) < 2:
        return None
    rets = []
    for i in range(min(len(closes) - 1, 14)):
        a, b = closes[i], closes[i + 1]
        if b:
            rets.append((a - b) / b * 100)
    vol = statistics.pstdev(rets) if len(rets) >= 2 else abs(hist[0].get("change_pct") or 2.0)
    vol = max(1.0, min(vol, 8.0))
    return {"close": closes[0], "change_pct": hist[0].get("change_pct"),
            "vol": round(vol, 2), "high20": max(closes), "low20": min(closes)}


def _targets(stats: dict) -> tuple[Optional[int], Optional[int], float]:
    """Suggested 매수가 (slight-pullback entry) + 매도가 (target) from close + vol."""
    close, vol = stats["close"], stats["vol"]
    buy = _round_tick(close * (1 - min(0.4 * vol, 2.0) / 100))
    tgt_pct = max(4.0, min(2.5 * vol, 12.0))
    sell = _round_tick((buy or close) * (1 + tgt_pct / 100))
    return buy, sell, round(tgt_pct, 1)


def _pick_codes(news: str, universe: dict[str, str], kst_date: str, n: int = 5) -> list[str]:
    """LLM selects n codes from the universe, grounded in the news."""
    from services.llm_client import chat_completion_sync
    uni = "\n".join(f"- {name} ({code})" for code, name in universe.items())
    sysmsg = (
        "You are a Korean equity analyst. From the UNIVERSE below, choose EXACTLY "
        f"{n} stocks most worth BUYING today, grounded in the NEWS and diversified "
        "across sectors. Return ONLY a JSON array of their 6-digit codes "
        '(e.g. ["005930","000660"]). No other text.')
    user = f"DATE {kst_date}\n\nUNIVERSE:\n{uni}\n\nNEWS:\n{(news or '(no live news)')[:5000]}"
    out = chat_completion_sync(system_prompt=sysmsg,
                               messages=[{"role": "user", "content": user[:8000]}],
                               max_tokens=120, temperature=0.5,
                               model="groq-llama-3.3-70b", prefer_paid=True) or ""
    seen: list[str] = []
    for c in re.findall(r"\d{6}", out):
        if c in universe and c not in seen:
            seen.append(c)
    # Backfill from the universe if the LLM returned fewer than n.
    for c in universe:
        if len(seen) >= n:
            break
        if c not in seen:
            seen.append(c)
    return seen[:n]


_RULES = (
    "작성 규칙(반드시 준수):\n"
    "- 개인 투자자가 이해하기 쉬운 정중한 한국어 존댓말로 작성합니다.\n"
    "- 과장되거나 단정적인 표현(반드시 상승/확실한 수익/꼭 매수 등)을 쓰지 않습니다.\n"
    "- 'AI 관련주' 같은 짧은 테마 표현 대신 사업적 영향(매출·마진·수요·수주 등)을 구체적으로 설명합니다.\n"
    "- '실적이 좋다' 같은 모호한 문장 대신 매출 증가·영업이익 개선·신규 수주·가이던스 상향 등 구체적 근거를 제시합니다.\n"
    "- 수동적 표현('늘어날 수 있다')을 줄이고 '뒷받침합니다/강화합니다/유효합니다/확인됩니다/기여할 것으로 보입니다'와 같은 분석적 결론을 사용합니다.\n"
    "- RSI·MACD·골든크로스·과매도 같은 기술적 용어를 직접 노출하지 말고 투자자 친화적 표현으로 바꿉니다.\n"
    "- 뉴스/공시 인용 시 출처 범주(최근 뉴스 / 기업 공시 / 가격 데이터 / 수급 동향)를 명시합니다.\n"
    "- 데이터가 부족하거나 근거가 약하면 그 한계를 분명히 밝힙니다.\n"
)


def build(db, source: str = "master", news: Optional[str] = None,
          extra_facts: str = "", emphasis: str = "") -> dict[str, Any]:
    """Build the detailed 5-stock recommendation grounded in ``source``'s data.

    source: 'kiwoom' (price/수급/공매도 driven) | 'newspaper' (news driven) |
    'master' (synthesis). ``news`` lets the caller pass its own news (else we
    fetch). ``extra_facts`` is appended to the LLM facts (e.g. Kiwoom 수급/공매도);
    ``emphasis`` adds a source-specific instruction. Returns
    {section_ko, picks, date, source}."""
    from services.kst import kst_date as _kst_date
    kst_date = _kst_date()
    universe = _universe()
    if not universe:
        return {"section_ko": "", "picks": [], "date": kst_date, "source": source}
    if news is None:
        try:
            from services.kiwoom_report import _market_news
            news = _market_news()
        except Exception:
            news = ""

    codes = _pick_codes(news, universe, kst_date)
    picks: list[dict] = []
    facts_lines: list[str] = []
    for code in codes:
        st = _price_stats(code)
        name = universe.get(code, code)
        if not st:
            facts_lines.append(f"- {name}({code}): 가격 데이터 부족(분석 한계 명시 필요)")
            picks.append({"ticker": code, "name": name, "data_ok": False})
            continue
        buy, sell, tgt = _targets(st)
        picks.append({"ticker": code, "name": name, "data_ok": True,
                      "close": st["close"], "change_pct": st["change_pct"],
                      "vol": st["vol"], "buy": buy, "sell": sell, "target_pct": tgt})
        facts_lines.append(
            f"- {name}({code}): 종가={st['close']:,}원, 일간등락={st['change_pct']}%, "
            f"최근변동성≈{st['vol']}%/일, 20일범위 {st['low20']:,}~{st['high20']:,}원 · "
            f"제안 매수가≈{buy:,}원, 제안 매도가≈{sell:,}원(목표 +{tgt}%)")

    sysmsg = (
        "당신은 키움증권의 시니어 한국주식 애널리스트입니다. 아래 5개 종목에 대한 "
        "'## 오늘의 투자 추천 5종목' 섹션만 작성하세요. 각 종목마다 다음을 포함합니다:\n"
        "1) 핵심 요약: 추천 이유를 한 문장으로.\n"
        "2) 추천 매수가: 제공된 '제안 매수가'를 기준으로 진입가를 제시(현재가·일간등락·변동성 반영).\n"
        "3) 추천 매도가: 제공된 '제안 매도가'를 기준으로 차익실현 목표가 제시.\n"
        "4) 추천 포인트 세 가지: 각각 독립된 문장으로. 첫째는 구체적 촉매(실적·수요 증가·"
        "공급계약·정책 수혜·신제품·주주환원 등), 둘째는 그 촉매가 매출·마진·현금흐름·수급·"
        "밸류에이션을 어떻게 개선하는지, 셋째는 뉴스/공시/가격/수급에 근거한 추가 점검 포인트와 "
        "리스크. '실적 영향'·'검증된 근거'·'투자 위험' 같은 고정 라벨은 쓰지 마세요.\n"
        "반드시 실제 제공된 숫자(종가·매수가·매도가)를 인용하세요. 가격을 임의로 지어내지 마세요.\n"
        + (f"{emphasis}\n" if emphasis else "")
        + _RULES +
        "\n출력은 이 섹션 마크다운만. 다른 설명 금지.")
    user = (f"날짜(KST): {kst_date}\n\n종목별 실제 가격/변동성 데이터:\n"
            + "\n".join(facts_lines)
            + (f"\n\n추가 데이터(해당 종목 수급/공매도 등):\n{extra_facts}" if extra_facts else "")
            + f"\n\n최근 뉴스/시장 동향:\n{(news or '(라이브 뉴스 없음 — 한계를 밝히고 일반적 촉매로 작성)')[:5000]}")

    section_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        out = chat_completion_sync(system_prompt=sysmsg,
                                   messages=[{"role": "user", "content": user[:12000]}],
                                   max_tokens=4000, temperature=0.45,
                                   model="groq-llama-3.3-70b", prefer_paid=True) or ""
        if out.strip() and not out.lstrip().startswith(("[LLM unavailable]", "[server error]")):
            section_ko = out.strip()
            if not section_ko.lstrip().startswith("#"):
                section_ko = "## 오늘의 투자 추천 5종목\n\n" + section_ko
    except Exception as e:
        log.warning(f"recommendation_engine: LLM failed: {str(e)[:90]}")

    return {"section_ko": section_ko, "picks": picks, "date": kst_date, "source": source}


def upsert_history(db, date: str, picks: list[dict], section_ko: str,
                   source: str = "master") -> Optional[str]:
    """Upsert the day's picks into the recommendation history — one orch_reports
    row per (KST date, source). Re-running the same day/source updates that row;
    new days (or other sources) accumulate."""
    try:
        from db.models import OrchReport
        from sqlalchemy import desc
    except Exception:
        return None
    content = {"report_type": "recommendation_daily", "kst_date": date,
               "source": source, "picks": picks, "section_ko": section_ko,
               "generated_at": datetime.utcnow().isoformat()}
    try:
        row = (db.query(OrchReport)
               .filter(OrchReport.report_type == "recommendation_daily")
               .filter(OrchReport.content_json["source"].astext == source)
               .order_by(desc(OrchReport.created_at)).first())
        if row and (row.content_json or {}).get("kst_date") == date:
            row.content_json = content                      # upsert today's row
        else:
            row = OrchReport(report_type="recommendation_daily",
                             source_run_ids_json=[], content_json=content,
                             delivery_channel="recommendation")
            db.add(row)
        db.commit()
        log.info(f"recommendation_engine: upserted {len(picks)} picks for {date}",
                 extra={"action": "recommendation.upsert"})
        return str(row.id)
    except Exception as e:
        log.warning(f"recommendation_engine: upsert failed: {str(e)[:90]}")
        try:
            db.rollback()
        except Exception:
            pass
        return None
