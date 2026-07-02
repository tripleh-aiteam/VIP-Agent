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

    # market backdrop — a scalp list on a crash day must say so up front
    mkt = None
    try:
        from services.trading_brief import _mkt_ret_today
        mkt = _mkt_ret_today(db)
    except Exception:
        pass
    mkt_ko = mkt_en = ""
    if mkt is not None:
        tone_ko = ("시장 우호적 — 단타에 유리한 흐름" if mkt >= 0.3 else
                   "시장 급락 경계 — 반등 단타도 실패 확률 상승, 수량 축소 권장" if mkt <= -1.5 else
                   "시장 약세 — 보수적으로" if mkt < 0 else "시장 중립")
        tone_en = ("supportive tape — good scalping weather" if mkt >= 0.3 else
                   "market plunging — even bounce scalps fail more; size down" if mkt <= -1.5 else
                   "soft tape — be conservative" if mkt < 0 else "neutral tape")
        mkt_ko = f"\n**시장 상황**: KODEX200 오늘 {mkt:+.2f}% — {tone_ko}\n"
        mkt_en = f"\n**Market**: KODEX200 today {mkt:+.2f}% — {tone_en}\n"

    lines_ko, lines_en = [], []
    for i, r in enumerate(rows, 1):
        risk_pct = None
        try:
            if r.get("buy") and r.get("stop"):
                risk_pct = round((float(r["buy"]) - float(r["stop"])) / float(r["buy"]) * 100, 2)
        except Exception:
            pass
        tag = {"ENTER": "🟢 진입가능", "WAIT": "🟡 눌림대기", "SKIP": "⚪ 관찰"}.get(r["entry"], "")
        lines_ko.append(
            f"**{i}. {r['name']}**  {tag} · 방향 ▲상승 예상\n"
            f"   · 왜 이 종목인가: {_why(r)}\n"
            f"   · 매매 계획: 매수 {_f(r['buy'])}원 → 목표 {_f(r['target'])}원(+{target_pct}%) → 손절 {_f(r['stop'])}원"
            + (f"(−{risk_pct}%)" if risk_pct else "")
            + (f" · 예상 ~{r['est']}분" if r.get("est") else "")
            + f"\n   · 실행: {'지금 매수 구간입니다 — 분할로 진입하세요.' if r['entry']=='ENTER' else '매수가까지 기다렸다가 지정가로 받으세요 — 쫓아 사면 손절 폭이 커집니다.'}")
        tag_en = {"ENTER": "🟢 enter", "WAIT": "🟡 wait-dip", "SKIP": "⚪ watch"}.get(r["entry"], "")
        lines_en.append(
            f"**{i}. {r.get('name_en') or r['name']}**  {tag_en} · direction ▲up-bias\n"
            f"   · Why this stock: {_why(r, en=True)}\n"
            f"   · Plan: buy {_f(r['buy'])} → target {_f(r['target'])} (+{target_pct}%) → stop {_f(r['stop'])}"
            + (f" (−{risk_pct}%)" if risk_pct else "")
            + (f" · ~{r['est']}min" if r.get("est") else "")
            + f"\n   · Execution: {'price is in the buy zone — scale in now.' if r['entry']=='ENTER' else 'set a limit order at the buy price and wait — chasing widens your stop.'}")

    foot_ko = ("\n\n**어떻게 골랐나**: 방법 1·3(머신러닝·파동)으로 상승 방향인 종목만 거르고, "
               "방법 2(호가·수급)와 오늘 변동성으로 진입 자리를 잡았습니다. 실수익은 세금·수수료 ~0.25%p 차감 기준입니다.\n"
               "**다음 단계**: 종목별 자세한 계획(실시간 호가 체크·수량 계산 포함)은 \"[종목명] 단타 될까?\", "
               "종합 매수 판단은 \"[종목명] 사도 돼?\"로 물어보세요. 참고용이며 투자 권유가 아닙니다.")
    foot_en = ("\n\n**How these were chosen**: Methods 1 & 3 (ML·Wave) filter for up-bias; Method 2 "
               "(orderbook/flows) + today's volatility set the entries. Net returns assume ~0.25%p costs.\n"
               "**Next step**: for the full per-stock plan (live tape check + share count) ask \"Can I scalp [name]?\", "
               "or \"Should I buy [name]?\" for the position view. Reference only, not investment advice.")

    reasoning_ko = (f"**📈 오늘의 단타 추천 (상승 편향 + 장중 셋업 · 통과 {len(rows)}종목)**\n"
                    + mkt_ko + "\n"
                    + ("\n\n".join(lines_ko) if lines_ko else
                       "오늘은 조건(상승 편향 + 변동성 여유)을 통과한 단타 셋업이 없습니다 — 무리해서 진입하기보다 쉬는 것도 전략입니다.")
                    + foot_ko)
    reasoning_en = (f"**📈 Today's scalp picks (up-bias + intraday setup · {len(rows)} passed)**\n"
                    + mkt_en + "\n"
                    + ("\n\n".join(lines_en) if lines_en else
                       "No setup passed the filters today (up-bias + volatility headroom) — sitting out is also a strategy.")
                    + foot_en)
    return {"picks": rows, "reasoning_ko": reasoning_ko, "reasoning_en": reasoning_en}
