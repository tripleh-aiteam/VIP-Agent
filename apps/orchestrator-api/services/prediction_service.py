"""prediction_service — thin reader over the `model_predictions` table.

The heavy ML (training/scoring) runs OFF Render and writes model_predictions to
Supabase. The orchestrator only READS here (no sklearn/xgboost deps), so it's safe
on the 512MB free tier. Used by the dashboard widget, chatbot tool, and reports.

Every result carries backtest_acc so advice is always shown WITH its track record.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text

NAMES = {
    "012450": "한화에어로스페이스", "047810": "한국항공우주", "079550": "LIG넥스원",
    "064350": "현대로템", "272210": "한화시스템", "329180": "HD현대중공업",
    "009540": "HD한국조선해양", "010140": "삼성중공업", "042660": "한화오션",
    "005930": "삼성전자", "000660": "SK하이닉스", "042700": "한미반도체",
    "373220": "LG에너지솔루션", "006400": "삼성SDI", "005490": "POSCO홀딩스",
    "247540": "에코프로비엠", "005380": "현대차", "000270": "기아",
    "012330": "현대모비스", "034020": "두산에너빌리티", "052690": "한전기술",
    "015760": "한국전력", "035420": "NAVER", "035720": "카카오", "018260": "삼성SDS",
    "207940": "삼성바이오로직스", "068270": "셀트리온", "010950": "S-OIL",
    "078930": "GS", "096770": "SK이노베이션", "105560": "KB금융", "055550": "신한지주",
    "138040": "메리츠금융지주", "003490": "대한항공", "272450": "진에어",
    "039130": "하나투어", "069500": "KODEX 200",
}

_SELECT = (
    "SELECT ticker, as_of, horizon, direction, probability, expected_move_pct, "
    "expected_low_pct, expected_high_pct, confidence, advice, model_name, "
    "backtest_acc, reasoning, created_at FROM model_predictions"
)


def _row(r) -> dict[str, Any]:
    return {
        "ticker": r.ticker, "name": NAMES.get(r.ticker, r.ticker),
        "as_of": str(r.as_of), "horizon": r.horizon, "direction": r.direction,
        "probability": float(r.probability) if r.probability is not None else None,
        "expected_move_pct": float(r.expected_move_pct) if r.expected_move_pct is not None else None,
        "expected_low_pct": float(r.expected_low_pct) if r.expected_low_pct is not None else None,
        "expected_high_pct": float(r.expected_high_pct) if r.expected_high_pct is not None else None,
        "confidence": r.confidence, "advice": r.advice, "model": r.model_name,
        "backtest_acc": float(r.backtest_acc) if r.backtest_acc is not None else None,
        "reasoning": r.reasoning,
    }


def _latest_as_of(db, horizon: int) -> Optional[str]:
    row = db.execute(text("SELECT max(as_of) FROM model_predictions WHERE horizon=:h"),
                     {"h": horizon}).first()
    return row[0] if row and row[0] else None


def list_predictions(db, horizon: int = 5, advice: Optional[str] = None) -> list[dict]:
    asof = _latest_as_of(db, horizon)
    if not asof:
        return []
    q = _SELECT + " WHERE horizon=:h AND as_of=:a"
    params = {"h": horizon, "a": asof}
    if advice:
        q += " AND advice=:adv"
        params["adv"] = advice.upper()
    q += " ORDER BY backtest_acc DESC NULLS LAST"
    return [_row(r) for r in db.execute(text(q), params)]


def get_ticker(db, ticker: str, horizon: int = 5) -> Optional[dict]:
    r = db.execute(text(_SELECT + " WHERE ticker=:t AND horizon=:h ORDER BY as_of DESC LIMIT 1"),
                   {"t": ticker, "h": horizon}).first()
    return _row(r) if r else None


def top_picks(db, advice: str = "BUY", n: int = 5, horizon: int = 5) -> list[dict]:
    rows = list_predictions(db, horizon=horizon, advice=advice)
    # rank BUY/SELL by model track record (backtest_acc), then confidence
    conf_rank = {"높음": 2, "보통": 1, "낮음": 0}
    rows.sort(key=lambda p: (p.get("backtest_acc") or 0, conf_rank.get(p.get("confidence"), 0)),
              reverse=True)
    return rows[:n]


def summary(db, horizon: int = 5) -> dict:
    asof = _latest_as_of(db, horizon)
    if not asof:
        return {"as_of": None, "counts": {}, "buys": [], "sells": []}
    allp = list_predictions(db, horizon=horizon)
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for p in allp:
        counts[p["advice"]] = counts.get(p["advice"], 0) + 1
    return {
        "as_of": asof, "horizon": horizon, "counts": counts,
        "buys": top_picks(db, "BUY", 5, horizon),
        "sells": top_picks(db, "SELL", 5, horizon),
        "disclaimer": "ML 추정치이며 투자 권유가 아닙니다. 각 항목의 백테스트 정확도를 함께 확인하세요.",
    }
