"""scalp_watchlist.py — Milestone 4.3: "오늘 단타할 종목 뭐가 좋아?"

Returns today's short-term watchlist: up-bias stocks (from the daily 3-method
recommendation, i.e. Methods 1 & 3 direction filter) that ALSO have a workable
intraday setup (from scalp_signal — Method 2 + volatility + walls). One chat answer,
no dashboard. Bilingual. Advisory only.
"""
from __future__ import annotations

from typing import Any, Optional


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
    rows, rejects = [], []
    for c in _candidates(db)[: n * 2 + 4]:
        try:
            sig = scalp_signal(db, c["ticker"], target_pct)
        except Exception:
            continue
        if sig.get("entry") in ("AVOID", None) or sig.get("feasible") == "unlikely":
            # keep the rejection WITH its numbers — an empty list must explain itself
            from services.stock_resolver import display_name_en as _dne
            rejects.append({"ticker": c["ticker"], "name": sig.get("name") or c.get("name"),
                            "name_en": _dne(c["ticker"]), "entry": sig.get("entry"),
                            "feasible": sig.get("feasible"), "ml": sig.get("ml_bias"),
                            "wave": sig.get("wave_bias"), "current": sig.get("current"),
                            "exp_move": sig.get("exp_move_pct"),
                            "regime_veto": sig.get("regime_veto")})
            continue                                   # bearish bias or no room today → drop
        from services.stock_resolver import display_name_en
        rows.append({"ticker": c["ticker"], "name": sig.get("name") or c.get("name"),
                     "name_en": display_name_en(c["ticker"]),
                     "entry": sig.get("entry"), "feasible": sig.get("feasible"),
                     "buy": (sig.get("buy_zone") or [None])[0], "target": sig.get("target_price"),
                     "stop": sig.get("stop_price"), "est": sig.get("est_minutes"),
                     "wave": sig.get("wave_bias"), "ml": sig.get("ml_bias"),
                     "current": sig.get("current"), "rr": sig.get("rr"),
                     "net_pct": sig.get("net_pct"), "m2": sig.get("m2_bias"),
                     "ask_wall": sig.get("ask_wall"), "rt": sig.get("rt_check")})
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
    # live crash-veto reading (KOSPI/KOSDAQ intraday) — when it fires, the answer must show
    # the veto's OWN numbers, not just the (possibly stale) KODEX line above
    try:
        from services.market_regime import crash_veto
        _rg = crash_veto()
        if _rg and _rg.get("veto"):
            if _rg.get("line_ko"):
                mkt_ko += _rg["line_ko"] + "\n"
            if _rg.get("line_en"):
                mkt_en += _rg["line_en"] + "\n"
    except Exception:
        pass

    # measured turn rhythm per pick — the boss's timing layer, cheap enough for ≤5 rows
    def _turn_bits(tk: str) -> tuple[Optional[str], Optional[str]]:
        try:
            from services.turn_engine import rhythm, live_turn_status
            rh = rhythm(db, tk) or {}
            st = live_turn_status(db, tk) or {}
            if not rh.get("dn_min"):
                return None, None
            live_ko = live_en = ""
            if st.get("leg"):
                _lg = "하락" if st["leg"] == "down" else "상승"
                _lge = "falling" if st["leg"] == "down" else "rising"
                live_ko = (f" · 지금 {_lg} {st.get('leg_run_min')}분째 ({st.get('leg_run_pct'):+.2f}%)"
                           f" · 바닥턴 점수 {st.get('bottom_turn_score')}")
                live_en = (f" · currently {_lge} for {st.get('leg_run_min')} min ({st.get('leg_run_pct'):+.2f}%)"
                           f" · bottom-turn score {st.get('bottom_turn_score')}")
            ko = (f"이 종목 실측 리듬: 하락 {rh['dn_min']}분/{rh['dn_pct']:+.2f}% → "
                  f"상승 {rh['up_min']}분/+{abs(rh['up_pct']):.2f}%{live_ko}")
            en = (f"measured rhythm: falls {rh['dn_min']} min/{rh['dn_pct']:+.2f}% → "
                  f"rises {rh['up_min']} min/+{abs(rh['up_pct']):.2f}%{live_en}")
            return ko, en
        except Exception:
            return None, None

    lines_ko, lines_en = [], []
    for i, r in enumerate(rows, 1):
        risk_pct = None
        try:
            if r.get("buy") and r.get("stop"):
                risk_pct = round((float(r["buy"]) - float(r["stop"])) / float(r["buy"]) * 100, 2)
        except Exception:
            pass
        ml = (r.get("ml") or "HOLD").upper()
        wv = (r.get("wave") or "-").upper()
        m2 = (r.get("m2") or "").upper()
        rt = r.get("rt") or {}
        feas = r.get("feasible")
        tko, ten = _turn_bits(r["ticker"])
        ml_note_ko = ("모델이 5일 기준 상승 우위를 봅니다" if ml == "BUY"
                      else "모델은 방향 우위를 못 찾음 — 방향은 파동·추세가 맡습니다")
        ml_note_en = ("the model sees 5-day upside edge" if ml == "BUY"
                      else "the model finds no directional edge — trend/wave carry the direction here")
        wv_note_ko = ("깊은 눌림 후 매수 자리 — 진입/목표/손절이 파동 구조에서 나왔습니다" if wv == "BUY"
                      else "파동상 매수 자리는 아니지만 상승 구조는 유지")
        wv_note_en = ("deep-pullback buy zone — entry/target/stop come from the wave structure" if wv == "BUY"
                      else "not a wave entry, but the up-structure is intact")
        m2_bits_ko, m2_bits_en = [], []
        if rt.get("imbalance") is not None:
            m2_bits_ko.append(f"호가 불균형 {rt['imbalance']}")
            m2_bits_en.append(f"book imbalance {rt['imbalance']}")
        if r.get("ask_wall"):
            m2_bits_ko.append(f"매도벽 {_f(r['ask_wall'])}원 — 목표가 이 아래면 실현 가능성↑")
            m2_bits_en.append(f"ask wall at {_f(r['ask_wall'])} — target below it = easier fill")
        feas_ko = {"yes": "오늘 변동성이 +1%를 여유 있게 커버", "marginal": "+1% 도달이 빠듯 — 목표 축소 고려"}.get(feas, "")
        feas_en = {"yes": "today's range covers +1% with room", "marginal": "+1% is a stretch today — consider a smaller target"}.get(feas, "")
        net = r.get("net_pct")
        tag = {"ENTER": "🟢 진입가능", "WAIT": "🟡 눌림대기", "SKIP": "⚪ 관찰"}.get(r["entry"], "")
        L = [f"**{i}. {r['name']}**  {tag} · 방향 ▲상승 예상"
             + (f" · 현재가 {_f(r.get('current'))}원" if r.get("current") else ""),
             f"   - **① 머신러닝** → {ml} — {ml_note_ko}.",
             f"   - **③ 파동(엘리엇)** → {wv} — {wv_note_ko}."]
        if m2_bits_ko or m2:
            L.append(f"   - **② 호가·수급(장중)**{f' → {m2}' if m2 else ''}"
                     + (f" — {' · '.join(m2_bits_ko)}." if m2_bits_ko else "."))
        if feas_ko:
            L.append(f"   - **오늘 변동성**: {feas_ko}.")
        L.append(f"   - **매매 계획**: 매수 {_f(r['buy'])}원 → 목표 {_f(r['target'])}원(+{target_pct}%) → 손절 {_f(r['stop'])}원"
                 + (f"(−{risk_pct}%)" if risk_pct else "")
                 + (f" · 손익비 {r['rr']}" if r.get("rr") else "")
                 + (f" · 예상 ~{r['est']}분" if r.get("est") else "")
                 + (f" · 비용 차감 순수익 ≈ +{net}%" if net is not None else ""))
        if tko:
            L.append(f"   - **⏱ 턴 타이밍**: {tko}.")
        L.append(f"   - **실행**: {'지금 매수 구간입니다 — 분할로 진입하세요.' if r['entry']=='ENTER' else '매수가까지 기다렸다가 지정가로 받으세요 — 쫓아 사면 손절 폭이 커집니다.'}"
                 f" **무효화 조건**: 손절가 {_f(r['stop'])}원 이탈 시 이 셋업은 무효 — 미련 없이 끊고 다음 눌림을 기다리세요.")
        lines_ko.append("\n".join(L))

        tag_en = {"ENTER": "🟢 enter", "WAIT": "🟡 wait-dip", "SKIP": "⚪ watch"}.get(r["entry"], "")
        E = [f"**{i}. {r.get('name_en') or r['name']}**  {tag_en} · direction ▲up-bias"
             + (f" · now {_f(r.get('current'))}" if r.get("current") else ""),
             f"   - **① ML** → {ml} — {ml_note_en}.",
             f"   - **③ Wave (Elliott)** → {wv} — {wv_note_en}."]
        if m2_bits_en or m2:
            E.append(f"   - **② Orderbook · flows (live)**{f' → {m2}' if m2 else ''}"
                     + (f" — {' · '.join(m2_bits_en)}." if m2_bits_en else "."))
        if feas_en:
            E.append(f"   - **Today's volatility**: {feas_en}.")
        E.append(f"   - **Plan**: buy {_f(r['buy'])} → target {_f(r['target'])} (+{target_pct}%) → stop {_f(r['stop'])}"
                 + (f" (−{risk_pct}%)" if risk_pct else "")
                 + (f" · R:R {r['rr']}" if r.get("rr") else "")
                 + (f" · ~{r['est']}min" if r.get("est") else "")
                 + (f" · net after costs ≈ +{net}%" if net is not None else ""))
        if ten:
            E.append(f"   - **⏱ Turn timing**: {ten}.")
        E.append(f"   - **Execution**: {'price is in the buy zone — scale in now.' if r['entry']=='ENTER' else 'set a limit order at the buy price and wait — chasing widens your stop.'}"
                 f" **Invalidation**: a break below the stop {_f(r['stop'])} kills this setup — cut without hesitation and wait for the next dip.")
        lines_en.append("\n".join(E))

    foot_ko = ("\n\n**어떻게 골랐나**: 방법 1·3(머신러닝·파동)으로 상승 방향인 종목만 거르고, "
               "방법 2(호가·수급)와 오늘 변동성으로 진입 자리를 잡았습니다. 실수익은 세금·수수료 ~0.25%p 차감 기준입니다.\n"
               "**다음 단계**: 종목별 자세한 계획(실시간 호가 체크·수량 계산 포함)은 \"[종목명] 단타 될까?\", "
               "종합 매수 판단은 \"[종목명] 사도 돼?\"로 물어보세요. 참고용이며 투자 권유가 아닙니다.")
    foot_en = ("\n\n**How these were chosen**: Methods 1 & 3 (ML·Wave) filter for up-bias; Method 2 "
               "(orderbook/flows) + today's volatility set the entries. Net returns assume ~0.25%p costs.\n"
               "**Next step**: for the full per-stock plan (live tape check + share count) ask \"Can I scalp [name]?\", "
               "or \"Should I buy [name]?\" for the position view. Reference only, not investment advice.")

    # Rejection diagnostics — when the list is empty (or thin) the answer must show its
    # work: per candidate, WHICH check failed with today's numbers and the return trigger.
    # Also surfaces missing Method-1 data instead of silently judging without it.
    diag_ko = diag_en = ""
    if rejects and len(rows) < n:
        ml_missing = sum(1 for r in rejects if not r.get("ml"))
        dko, den = [], []
        for r in rejects[:6]:
            why_ko, why_en, flip_ko, flip_en = [], [], [], []
            if r.get("regime_veto"):
                why_ko.append("시장 급락 베토(지수 −2% 이상) — 신규 롱 금지")
                why_en.append("market-crash veto (index ≤ −2%) — no new longs")
                flip_ko.append("지수 낙폭 회복")
                flip_en.append("index recovers")
            if r.get("ml") == "SELL":
                why_ko.append("방법1 머신러닝 '매도' — 5일 기준 하락 우위")
                why_en.append("Method 1 ML says SELL — 5-day downside edge")
                flip_ko.append("ML이 중립/매수로 전환")
                flip_en.append("ML flips to HOLD/BUY")
            elif not r.get("ml"):
                why_ko.append("방법1 ML 예측 데이터 없음(파이프라인 갱신 중) — ML 제외 평가")
                why_en.append("Method 1 ML data missing (pipeline updating) — judged without ML")
            if r.get("wave") == "AVOID":
                why_ko.append("방법3 파동 '회피' — 상승 파동 구조가 약하거나 붕괴")
                why_en.append("Method 3 Wave says AVOID — up-wave weak or broken")
                flip_ko.append("파동 구조 회복(되돌림 완성)")
                flip_en.append("wave structure repairs (pullback completes)")
            if r.get("feasible") == "unlikely":
                _em = r.get("exp_move")
                why_ko.append(f"오늘 예상 변동폭 ±{_em}%로는 목표 +{target_pct}% 도달이 어려움" if _em is not None
                              else f"오늘 변동성으로는 +{target_pct}% 도달이 어려움")
                why_en.append(f"today's expected range ±{_em}% can't reach +{target_pct}%" if _em is not None
                              else f"today's volatility can't reach +{target_pct}%")
                flip_ko.append("변동성 확대(거래 활발해질 때)")
                flip_en.append("volatility expands")
            if not why_ko:
                why_ko.append("종합 기준 미달"); why_en.append("below the combined bar")
            _px_ko = f" (현재가 {_f(r.get('current'))}원)" if r.get("current") else ""
            _px_en = f" (now {_f(r.get('current'))})" if r.get("current") else ""
            dko.append(f"   - **{r['name']}**{_px_ko} — ❌ " + " · ".join(why_ko)
                       + (f". **복귀 조건**: {', '.join(flip_ko)}." if flip_ko else "."))
            den.append(f"   - **{r.get('name_en') or r['name']}**{_px_en} — ❌ " + " · ".join(why_en)
                       + (f". **Comes back when**: {', '.join(flip_en)}." if flip_en else "."))
        head_ko = ("\n\n**🔍 왜 통과 종목이 없나 — 후보별 진단**\n" if not rows
                   else "\n\n**🔍 탈락 후보 진단 (참고)**\n")
        head_en = ("\n\n**🔍 Why nothing passed — per-candidate diagnosis**\n" if not rows
                   else "\n\n**🔍 Rejected candidates (for reference)**\n")
        diag_ko = head_ko + "\n".join(dko)
        diag_en = head_en + "\n".join(den)
        if ml_missing >= max(2, len(rejects) // 2):
            diag_ko += (f"\n\n⚠️ **방법 1(머신러닝) 예측 데이터가 후보 {ml_missing}/{len(rejects)}개에서 "
                        "비어 있습니다** (예측 파이프라인 갱신 중일 수 있음). 그동안은 파동·변동성·호가 "
                        "기준으로만 평가하며, ML 데이터가 돌아오면 판단 정확도가 올라갑니다.")
            diag_en += (f"\n\n⚠️ **Method 1 (ML) predictions are missing for {ml_missing}/{len(rejects)} "
                        "candidates** (the prediction pipeline may be mid-update). Until it returns, "
                        "picks are judged on Wave + volatility + orderbook only.")
        _empty_plan_ko = ("\n\n**📌 오늘의 행동 지침**: 통과 종목이 없는 날 억지로 들어가는 것이 단타에서 "
                          "제일 비싼 실수입니다. 위 복귀 조건들이 실제로 바뀌는지 30분~1시간 간격으로 다시 "
                          "물어보시고, 그 사이엔 관심 종목의 리듬을 확인해 두세요(\"[종목명] 언제 반등해?\"). "
                          "현금도 포지션입니다.")
        _empty_plan_en = ("\n\n**📌 What to actually do today**: forcing an entry on a no-pick day is the "
                          "most expensive scalping mistake. Re-ask every 30-60 min to see if the return "
                          "triggers above actually flip, and meanwhile check your watch-stocks' rhythm "
                          "(\"when will [name] turn up?\"). Cash is also a position.")
        if not rows:
            diag_ko += _empty_plan_ko
            diag_en += _empty_plan_en

    reasoning_ko = (f"**📈 오늘의 단타 추천 (상승 편향 + 장중 셋업 · 통과 {len(rows)}종목)**\n"
                    + mkt_ko + "\n"
                    + ("\n\n".join(lines_ko) if lines_ko else
                       "오늘은 조건(상승 편향 + 변동성 여유)을 통과한 단타 셋업이 없습니다 — 무리해서 진입하기보다 쉬는 것도 전략입니다.")
                    + diag_ko
                    + foot_ko)
    reasoning_en = (f"**📈 Today's scalp picks (up-bias + intraday setup · {len(rows)} passed)**\n"
                    + mkt_en + "\n"
                    + ("\n\n".join(lines_en) if lines_en else
                       "No setup passed the filters today (up-bias + volatility headroom) — sitting out is also a strategy.")
                    + diag_en
                    + foot_en)
    return {"picks": rows, "rejects": rejects, "reasoning_ko": reasoning_ko, "reasoning_en": reasoning_en}
