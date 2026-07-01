"""scalp_watchlist.py — Milestone 4.3: "오늘 단타할 종목 뭐가 좋아?"

Returns today's short-term watchlist: up-bias stocks (from the daily 3-method
recommendation, i.e. Methods 1 & 3 direction filter) that ALSO have a workable
intraday setup (from scalp_signal — Method 2 + volatility + walls). One chat answer,
no dashboard. Bilingual. Advisory only.
"""
from __future__ import annotations

from typing import Any


def _candidates(db) -> list[dict]:
    """Up-bias candidates: latest recommendation picks (decide-ranked), else a liquid set."""
    try:
        from db.models import OrchReport
        r = (db.query(OrchReport)
             .filter(OrchReport.report_type == "recommendation_report")
             .order_by(OrchReport.created_at.desc()).first())
        picks = ((r.content_json or {}).get("report") or {}).get("picks") if r else None
        if picks:
            return picks
    except Exception:
        pass
    # fallback: a few liquid names (still bias-filtered by scalp_signal below)
    from services.stock_resolver import display_name
    return [{"ticker": c, "name": display_name(c)} for c in
            ("005930", "000660", "005380", "003490", "079550", "042700", "012450", "064350")]


def build(db, n: int = 5, target_pct: float = 1.0) -> dict[str, Any]:
    """Rank candidates by intraday setup + up-bias → top n scalp watchlist."""
    from services.day_trade import scalp_signal
    rows = []
    for c in _candidates(db)[: n * 2 + 4]:
        try:
            sig = scalp_signal(db, c["ticker"], target_pct)
        except Exception:
            continue
        if sig.get("entry") in ("AVOID", None) or sig.get("feasible") == "unlikely":
            continue                                   # bearish bias or no room today → drop
        rows.append({"ticker": c["ticker"], "name": sig.get("name") or c.get("name"),
                     "entry": sig.get("entry"), "feasible": sig.get("feasible"),
                     "buy": (sig.get("buy_zone") or [None])[0], "target": sig.get("target_price"),
                     "stop": sig.get("stop_price"), "est": sig.get("est_minutes"),
                     "wave": sig.get("wave_bias"), "ml": sig.get("ml_bias")})
    # ENTER first, then WAIT; drop SKIP if we have enough
    rank = {"ENTER": 0, "WAIT": 1, "SKIP": 2}
    rows.sort(key=lambda x: rank.get(x["entry"], 3))
    rows = [r for r in rows if r["entry"] != "SKIP"][:n] or rows[:n]

    def _f(v):
        return f"{int(v):,}" if v else "-"
    lines_ko, lines_en = [], []
    for i, r in enumerate(rows, 1):
        tag = {"ENTER": "🟢 진입가능", "WAIT": "🟡 눌림대기", "SKIP": "⚪ 관찰"}.get(r["entry"], "")
        lines_ko.append(f"{i}. {r['name']} — {tag} · 매수 {_f(r['buy'])} · 목표(+{target_pct}%) {_f(r['target'])} · 손절 {_f(r['stop'])}"
                        + (f" · ~{r['est']}분" if r.get("est") else ""))
        tag_en = {"ENTER": "🟢 enter", "WAIT": "🟡 wait-dip", "SKIP": "⚪ watch"}.get(r["entry"], "")
        lines_en.append(f"{i}. {r['name']} — {tag_en} · buy {_f(r['buy'])} · target(+{target_pct}%) {_f(r['target'])} · stop {_f(r['stop'])}"
                        + (f" · ~{r['est']}min" if r.get("est") else ""))
    reasoning_ko = ("**📈 오늘의 단타 후보 (상승 편향 + 장중 셋업)**\n\n" + ("\n".join(lines_ko) if lines_ko else "오늘은 적합한 단타 셋업이 없습니다.")
                    + "\n\n※ 방법 1·3으로 상승 편향을 거르고, 방법 2+변동성으로 진입 자리를 잡았습니다. 비용 감안 실수익은 목표보다 ~0.25%p 낮습니다. 참고용.")
    reasoning_en = ("**📈 Today's scalp watchlist (up-bias + intraday setup)**\n\n" + ("\n".join(lines_en) if lines_en else "No suitable scalp setups today.")
                    + "\n\n※ Filtered for up-bias by Methods 1 & 3, entries from Method 2 + volatility. Net ≈ target − 0.25%p after costs. Reference only.")
    return {"picks": rows, "reasoning_ko": reasoning_ko, "reasoning_en": reasoning_en}
