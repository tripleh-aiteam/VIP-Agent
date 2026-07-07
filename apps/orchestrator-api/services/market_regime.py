"""market_regime.py — index-crash veto for NEW long scalp entries (2026-07-07).

Born from the first LIVE forward test: on a −4.5% KOSPI morning the scalp advisor said
ENTER on 3 stocks (2 stops hit); its one refusal was the only clearly right call. Like the
wave method's index-plunge filter: when the market itself is crashing, buying dips on
individual names is a low-odds trade — refuse new longs and say why.

Threshold via SCALP_MARKET_VETO_PCT env (default 2.0 = veto when KOSPI or KOSDAQ is down
more than 2% intraday). Uses the cached Naver index fetch (120s) — no extra latency.
"""
from __future__ import annotations

import os
from typing import Any


def crash_veto() -> dict[str, Any]:
    """{veto, kospi_pct, kosdaq_pct, line_ko, line_en}. Never raises; missing data → no veto."""
    try:
        thr = float(os.getenv("SCALP_MARKET_VETO_PCT", "2.0"))
    except Exception:
        thr = 2.0
    kospi = kosdaq = None
    try:
        from services.decision_agent import _market_indicators
        mi = _market_indicators() or {}
        kospi = (mi.get("kospi") or {}).get("pct")
        kosdaq = (mi.get("kosdaq") or {}).get("pct")
    except Exception:
        pass
    worst = min([p for p in (kospi, kosdaq) if p is not None], default=None)
    veto = worst is not None and worst <= -thr
    parts = []
    if kospi is not None:
        parts.append(f"코스피 {kospi:+.2f}%")
    if kosdaq is not None:
        parts.append(f"코스닥 {kosdaq:+.2f}%")
    idx_txt = " · ".join(parts) or "지수 데이터 없음"
    idx_txt_en = idx_txt.replace("코스피", "KOSPI").replace("코스닥", "KOSDAQ").replace("지수 데이터 없음", "no index data")
    return {
        "veto": bool(veto), "kospi_pct": kospi, "kosdaq_pct": kosdaq, "threshold_pct": thr,
        "line_ko": (f"🚫 시장 급락일 필터: {idx_txt} — 지수가 {thr:.0f}% 넘게 빠진 날은 신규 단타 롱 진입을 "
                    f"권하지 않습니다 (지수 급락일에 개별 종목 반등을 잡는 건 확률이 낮습니다). "
                    f"지수 안정(−{thr:.0f}% 이내 회복) 후 다시 물어보시거나, '반등할 종목'으로 낙폭 과대 후보만 관찰하세요."),
        "line_en": (f"🚫 Market-crash filter: {idx_txt_en} — with the index down more than {thr:.0f}%, "
                    f"new long scalps aren't advised (catching single-stock bounces in an index plunge is low-odds). "
                    f"Ask again once the index stabilises, or watch oversold candidates via the dip-bounce list."),
    }
