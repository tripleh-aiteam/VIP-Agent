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
        from services.stock_resolver import display_name_en
        rows.append({"ticker": c["ticker"], "name": sig.get("name") or c.get("name"),
                     "name_en": display_name_en(c["ticker"]),
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

    def _why(r, en=False):
        """Short reason + direction for one pick (from the 3 methods + feasibility)."""
        bits = []
        if r.get("wave") == "BUY":
            bits.append("Wave buy zone (deep pullback)" if en else "파동(엘리엇) 매수 자리")
        if r.get("ml") == "BUY":
            bits.append("ML predicts upside" if en else "머신러닝 상승 예측")
        if not bits:
            bits.append("uptrend intact (Methods 1 & 3)" if en else "중기 상승 추세 유지 (방법 1·3)")
        f = r.get("feasible")
        if f == "yes":
            bits.append("today's volatility easily covers +1%" if en else "오늘 변동성 충분 — +1% 여유")
        elif f == "marginal":
            bits.append("+1% only marginally reachable" if en else "+1% 도달 제한적")
        ent = ({"ENTER": "→ good to enter now", "WAIT": "→ wait for the pullback to the buy price",
                "SKIP": "→ watch only"} if en else
               {"ENTER": "→ 지금 진입 가능", "WAIT": "→ 매수가까지 눌림 대기", "SKIP": "→ 관찰만"}).get(r["entry"], "")
        return " · ".join(bits) + " " + ent

    lines_ko, lines_en = [], []
    for i, r in enumerate(rows, 1):
        tag = {"ENTER": "🟢 진입가능", "WAIT": "🟡 눌림대기", "SKIP": "⚪ 관찰"}.get(r["entry"], "")
        lines_ko.append(
            f"**{i}. {r['name']}**  {tag} · 방향 ▲상승 예상\n"
            f"   • 왜: {_why(r)}\n"
            f"   • 매수 {_f(r['buy'])} · 목표(+{target_pct}%) {_f(r['target'])} · 손절 {_f(r['stop'])}"
            + (f" · ~{r['est']}분" if r.get("est") else ""))
        tag_en = {"ENTER": "🟢 enter", "WAIT": "🟡 wait-dip", "SKIP": "⚪ watch"}.get(r["entry"], "")
        lines_en.append(
            f"**{i}. {r.get('name_en') or r['name']}**  {tag_en} · direction ▲up-bias\n"
            f"   • Why: {_why(r, en=True)}\n"
            f"   • Buy {_f(r['buy'])} · target(+{target_pct}%) {_f(r['target'])} · stop {_f(r['stop'])}"
            + (f" · ~{r['est']}min" if r.get("est") else ""))
    reasoning_ko = ("**📈 오늘의 단타 추천 (상승 편향 + 장중 셋업)**\n\n"
                    + ("\n\n".join(lines_ko) if lines_ko else "오늘은 적합한 단타 셋업이 없습니다.")
                    + "\n\n※ 방법 1·3(머신러닝·파동)으로 상승 방향을 거르고, 방법 2(호가·수급)+변동성으로 진입 자리를 잡았습니다. "
                    "비용 감안 실수익은 목표보다 ~0.25%p 낮습니다. 참고용이며 투자 권유가 아닙니다.")
    reasoning_en = ("**📈 Today's scalp picks (up-bias + intraday setup)**\n\n"
                    + ("\n\n".join(lines_en) if lines_en else "No suitable scalp setups today.")
                    + "\n\n※ Direction filtered by Methods 1 & 3 (ML·Wave), entries from Method 2 (orderbook/flows) + "
                    "volatility. Net ≈ target − 0.25%p after costs. Reference only, not investment advice.")
    return {"picks": rows, "reasoning_ko": reasoning_ko, "reasoning_en": reasoning_en}
