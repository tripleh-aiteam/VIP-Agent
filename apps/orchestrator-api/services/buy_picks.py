"""buy_picks.py — "뭘 사면 좋아? / what stock should I buy?" with NO stock named.

This question used to fall through to a raw LLM chain that produced vague, sometimes
truncated answers. Now it's DETERMINISTIC and complete: run the full 3-method decide()
over today's ranked candidates (the morning recommendation picks, else a liquid set),
present the BUYs with levels, per-method verdicts, market context, per-pick sizing
(when the user's budget is known) and the real graded track record. If nothing is a
BUY (e.g. a crash day), say so honestly and show the best WATCH setups with the
trigger levels that would change the answer. KO == EN, VIP == AI Advisor.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

SCAN_MAX = 8          # candidates to run decide() over (parallel, ~3 batches of 3)

# ALWAYS-SCANNED heavyweights (boss 2026-07-09: asked "what should I buy?" → "no stock
# passes", then "should I buy Samsung?" → "yes" — nonsense to him, and he was right.
# Cause: the scan universe was ONLY the morning picks, which that day were all
# financials — Samsung was never scanned. The core names get a seat every time so the
# broad question can never miss a BUY the per-stock question would give.)
CORE = ["005930", "000660", "005380"]


def _candidates(db) -> list[str]:
    picks: list[str] = []
    try:
        from db.models import OrchReport
        r = (db.query(OrchReport)
             .filter(OrchReport.report_type == "recommendation_report")
             .order_by(OrchReport.created_at.desc()).first())
        p = ((r.content_json or {}).get("report") or {}).get("picks") if r else None
        if p:
            picks = [str(x.get("ticker")).zfill(6) for x in p if x.get("ticker")]
    except Exception:
        pass
    base = ["079550", "042700", "012450", "064350", "003490"]
    merged = picks[:5] + CORE + base
    seen: set[str] = set()
    out: list[str] = []
    for c in merged:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:SCAN_MAX]


def _decide_many(tickers: list[str]) -> list[dict]:
    """decide() per ticker in parallel — each worker gets its OWN session (decide is
    not thread-safe on a shared one)."""
    from db.base import SessionLocal
    from services.decision_agent import decide

    def _one(tk):
        s = SessionLocal()
        try:
            return decide(s, tk)
        except Exception:
            return None
        finally:
            s.close()

    out = []
    ex = ThreadPoolExecutor(max_workers=3)         # no `with`: its exit would block on the
    try:                                            # slow worker despite the timeout
        futs = {ex.submit(_one, tk): tk for tk in tickers}
        for f in as_completed(futs, timeout=75):
            try:
                d = f.result()
                if d:
                    out.append(d)
            except Exception:
                pass
    except Exception:
        pass                                       # keep what finished in time
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return out


def _fmt(v) -> str:
    try:
        return f"{int(float(v)):,}"
    except Exception:
        return "-"


def _pct(v, px) -> str:
    """' (+3.2%)' — level as a distance from the current price, so it means something."""
    try:
        p = (float(v) / float(px) - 1) * 100
        return f" ({p:+.1f}%)"
    except Exception:
        return ""


def _sh(v, en: bool) -> str:
    try:
        n = int(float(v))
    except Exception:
        return "-"
    if en:
        return f"{'+' if n >= 0 else '−'}{abs(n):,} shares net {'bought' if n >= 0 else 'sold'}"
    return f"{abs(n):,}주 {'순매수' if n >= 0 else '순매도'}"


def _pick_block(d: dict, i: int, budget: Optional[int], en: bool) -> str:
    """One candidate, at the SAME depth as the single-stock decision report: every method
    shows its verdict first, then WHY with today's real numbers and what they mean."""
    name = d.get("name") or d.get("ticker")
    if en:
        try:
            from services.stock_resolver import display_name_en
            name = display_name_en(d.get("ticker") or "") or name
        except Exception:
            pass
    dec = (d.get("decision") or "HOLD").upper()
    dec = dec if dec in ("BUY", "SELL") else "HOLD"
    conf = d.get("confidence") or "low"
    px = d.get("price")
    m1 = d.get("method1_ml") or {}
    m2 = d.get("method2_analysis") or {}
    m3 = d.get("method3_wave") or {}
    tech = d.get("technicals") or {}
    news = d.get("news") or {}
    flows = d.get("flows") or {}
    news_s = news.get("score") or 0

    # levels: wave levels when it's a wave BUY, else box support/resistance
    wave_buy = m3.get("verdict") == "BUY" and m3.get("entry")
    entry = m3.get("entry") if wave_buy else tech.get("support")
    stop = m3.get("stop") if wave_buy else (
        int(tech["support"] * 0.98) if tech.get("support") else None)
    target = m3.get("target") if wave_buy else tech.get("resistance")

    conf_ko = {"high": "높음", "medium": "보통", "low": "낮음"}.get(conf, conf)
    m1c = (m1.get("call") or "HOLD").upper()
    m2c = (m2.get("signal") or "WATCH").upper()
    m3c = (m3.get("verdict") or "-")
    acc = m1.get("accuracy_pct")
    em = m1.get("expected_move_pct")
    wsc = m3.get("wave_score")
    rr = m3.get("rr")
    sup, res = tech.get("support"), tech.get("resistance")
    pos = tech.get("pos_in_range")
    f5, i5 = flows.get("foreign_5d"), flows.get("inst_5d")
    tag = flows.get("tag_en") if en else flows.get("tag")
    titles = [t for t in (news.get("titles") or [])][:2]
    backing = [n for n, c in (("ML", m1c), ("Analysis" if en else "분석", m2c),
                              ("Wave" if en else "파동", m3c)) if str(c).upper() == "BUY"]

    size_line = None
    if budget and dec == "BUY" and px:
        try:
            from services.position_size import size_position
            s = size_position(budget, float(px), float(stop) if stop else None)
            if s:
                size_line = (f"Size (budget ₩{budget:,}): {s['shares']:,} shares ≈ ₩{s['cost']:,}"
                             + (f" · −₩{s['risk_won']:,} if stopped" if s.get("risk_won") else "")
                             ) if en else (
                            f"수량(자금 {budget:,}원): {s['shares']:,}주 ≈ {s['cost']:,}원"
                            + (f" · 손절 시 −{s['risk_won']:,}원" if s.get("risk_won") else ""))
        except Exception:
            pass

    if en:
        head = {"BUY": "🟢 BUY", "SELL": "🔴 avoid", "HOLD": "🟡 WATCH"}[dec]
        acc_note = ("" if acc is None else
                    " — below coin-flip on this stock lately, so its opinion gets less weight here"
                    if float(acc) < 50 else
                    " — it has been right more often than not on this stock, so this carries weight")
        pos_note = ("near the ceiling — chasing here means buying at the expensive end of the range"
                    if isinstance(pos, (int, float)) and pos >= 60 else
                    "near the floor — price itself is at the attractive end, what's missing is confirmation"
                    if isinstance(pos, (int, float)) and pos <= 40 else
                    "mid-box — direction needs to pick a side first")
        L = [f"**{i}. {name} — {head} (confidence {conf})**  ·  now ₩{_fmt(px)} · engine score {d.get('score', '-')}"]
        L.append(f"   - **① ML (xgboost)** → **{m1c}** — the 20-year model scores this stock's odds of "
                 f"beating KOSPI over the next 5 days"
                 + (f"; expected 5-day move {float(em):+.2f}%" if em is not None else "")
                 + (f", recent accuracy on this name {acc}%{acc_note}" if acc is not None else "") + ".")
        fl_bits = []
        if f5 is not None:
            fl_bits.append(f"foreign 5-day {_sh(f5, True)}")
        if i5 is not None:
            fl_bits.append(f"institutions 5-day {_sh(i5, True)}")
        if tag:
            fl_bits.append(f"pattern: {tag}")
        L.append(f"   - **② Analysis (flows · box)** → **{m2c}** — "
                 + (", ".join(fl_bits) + ". " if fl_bits else "")
                 + (f"Price sits at {pos}% of its 20-day box (support ₩{_fmt(sup)} ↔ resistance ₩{_fmt(res)}) — {pos_note}."
                    if pos is not None else f"20-day box: support ₩{_fmt(sup)} ↔ resistance ₩{_fmt(res)}."))
        if str(m3c).upper() == "BUY":
            L.append(f"   - **③ Wave (Elliott · Fibonacci)** → **BUY** — wave score {wsc}: the pullback looks "
                     f"complete, so it maps an entry at ₩{_fmt(m3.get('entry'))} / target ₩{_fmt(m3.get('target'))} / "
                     f"stop ₩{_fmt(m3.get('stop'))}" + (f" (risk:reward {rr})" if rr else "") + ".")
        else:
            L.append(f"   - **③ Wave (Elliott · Fibonacci)** → **{m3c}**"
                     + (f" — wave score {wsc}: " if wsc is not None else " — ")
                     + "the decline hasn't formed a finished pullback structure yet, so there is no wave entry to buy.")
        if tech.get("summary_en"):
            L.append(f"   - **④ Technicals**: {tech['summary_en']} — the stock's own chart vs. what the market tape allows.")
        L.append(f"   - **News ({'positive' if news_s > 0 else 'negative' if news_s < 0 else 'neutral'})**: "
                 + (" / ".join(titles) if titles else "no market-moving headlines in the last few days."))
        L.append(f"   - **Trade plan if it fires**: entry ~₩{_fmt(entry)} → target ₩{_fmt(target)}{_pct(target, px)}"
                 f" · stop ₩{_fmt(stop)}{_pct(stop, px)}")
        if size_line:
            L.append(f"   - {size_line}")
        if dec != "BUY":
            L.append(f"   - **Why it's not a BUY today**: only {len(backing)} of 3 methods say buy"
                     + (f" ({', '.join(backing)})" if backing else "")
                     + f" — below the confirmation bar. **Flips to BUY when**: a volume-backed break above "
                       f"₩{_fmt(res)}{_pct(res, px)}, or a held bounce off ₩{_fmt(sup)}{_pct(sup, px)} with flows turning positive.")
        else:
            L.append(f"   - **Why it IS a buy**: {len(backing)} of 3 methods back it ({', '.join(backing)}) "
                     f"and nothing vetoes it — still, enter near ₩{_fmt(entry)}, not by chasing.")
        return "\n".join(L)

    head = {"BUY": "🟢 매수", "SELL": "🔴 회피", "HOLD": "🟡 관망"}[dec]
    acc_note = ("" if acc is None else
                " — 최근 이 종목 적중률이 50% 아래라 ML 의견 비중은 낮춰 봅니다"
                if float(acc) < 50 else
                " — 이 종목에서는 절반 이상 맞혀 와서 의견에 무게가 있습니다")
    pos_note = ("천장 쪽이라 지금 추격하면 박스에서 비싸게 사는 셈"
                if isinstance(pos, (int, float)) and pos >= 60 else
                "바닥 쪽이라 가격 위치는 매력적 — 부족한 건 방향 확인"
                if isinstance(pos, (int, float)) and pos <= 40 else
                "박스 중간이라 방향이 정해지길 기다리는 자리")
    L = [f"**{i}. {name} — {head} (확신 {conf_ko})**  ·  현재가 {_fmt(px)}원 · 종합점수 {d.get('score', '-')}"]
    L.append(f"   - **① 머신러닝 (xgboost)** → **{m1c}** — 20년 데이터를 학습한 모델이 향후 5일 코스피 대비 "
             f"초과수익 확률을 계산한 결과입니다"
             + (f". 예상 5일 수익률 {float(em):+.2f}%" if em is not None else "")
             + (f", 최근 이 종목 적중률 {acc}%{acc_note}" if acc is not None else "") + ".")
    fl_bits = []
    if f5 is not None:
        fl_bits.append(f"외국인 5일 {_sh(f5, False)}")
    if i5 is not None:
        fl_bits.append(f"기관 5일 {_sh(i5, False)}")
    if tag:
        fl_bits.append(f"수급 패턴: {tag}")
    L.append(f"   - **② 분석 (수급·박스권)** → **{m2c}** — "
             + (" · ".join(fl_bits) + ". " if fl_bits else "")
             + (f"현재가는 20일 박스(지지 {_fmt(sup)}원 ↔ 저항 {_fmt(res)}원)의 {pos}% 지점 — {pos_note}입니다."
                if pos is not None else f"20일 박스: 지지 {_fmt(sup)}원 ↔ 저항 {_fmt(res)}원."))
    if str(m3c).upper() == "BUY":
        L.append(f"   - **③ 파동 (엘리엇·피보나치)** → **매수** — 파동 점수 {wsc}: 조정이 마무리된 자리로 판단, "
                 f"진입 {_fmt(m3.get('entry'))}원 / 목표 {_fmt(m3.get('target'))}원 / 손절 {_fmt(m3.get('stop'))}원"
                 + (f" (손익비 {rr})" if rr else "") + ".")
    else:
        L.append(f"   - **③ 파동 (엘리엇·피보나치)** → **{m3c}**"
                 + (f" — 파동 점수 {wsc}: " if wsc is not None else " — ")
                 + "아직 되돌림 구조가 완성되지 않아 파동상 매수 자리가 아닙니다.")
    if tech.get("summary_ko"):
        L.append(f"   - **④ 기술적 흐름**: {tech['summary_ko']} — 종목 자체 차트와 시장 상황을 함께 봐야 하는 이유입니다.")
    L.append(f"   - **뉴스 ({'호재 우세' if news_s > 0 else '악재 우세' if news_s < 0 else '중립'})**: "
             + (" / ".join(titles) if titles else "최근 며칠 주가를 움직일 만한 헤드라인 없음."))
    L.append(f"   - **매매 계획(신호 점등 시)**: 진입 ~{_fmt(entry)}원 → 목표 {_fmt(target)}원{_pct(target, px)}"
             f" · 손절 {_fmt(stop)}원{_pct(stop, px)}")
    if size_line:
        L.append(f"   - {size_line}")
    if dec != "BUY":
        L.append(f"   - **오늘 매수가 아닌 이유**: 3개 방법 중 매수 의견이 {len(backing)}개"
                 + (f"({', '.join(backing)})" if backing else "")
                 + f"뿐 — 확신 기준(방향 일치 + 시장 확인)에 못 미칩니다. **매수 전환 조건**: 저항 "
                   f"{_fmt(res)}원{_pct(res, px)} 거래량 동반 돌파, 또는 지지 {_fmt(sup)}원{_pct(sup, px)} 반등 확인 + 수급 개선.")
    else:
        L.append(f"   - **왜 매수인가**: 3개 방법 중 {len(backing)}개({', '.join(backing)})가 매수를 지지하고 "
                 f"거부 조건이 없습니다 — 다만 추격 말고 진입가 {_fmt(entry)}원 부근에서 분할로.")
    return "\n".join(L)


def build(db, n: int = 3, transcript: str = "", user_key: Optional[str] = None,
          lang: Optional[str] = None) -> dict[str, Any]:
    en = str(lang or "").lower().startswith("en")

    budget = None
    try:
        from services.position_size import recall_budget, remember_budget, stated_budget
        budget = stated_budget(transcript)       # cue-gated: a bare price is NOT a budget
        if user_key:
            if budget:
                remember_budget(db, user_key, budget)
            else:
                budget = recall_budget(db, user_key)
    except Exception:
        pass

    results = _decide_many(_candidates(db)[:SCAN_MAX])
    results.sort(key=lambda d: -(d.get("score") or 0))
    buys = [d for d in results if (d.get("decision") or "").upper() == "BUY"][:n]
    watches = [d for d in results if d not in buys][: max(n - len(buys), 2 if not buys else 0)]

    # ⚡ 1-HOUR TRADE CANDIDATES (boss 2026-07-09: chatbot, auto-trading and the decision
    # engine must speak as one) — the broad buy question also shows the live intraday
    # setups the auto-trader buys from, so "what should I buy?" can never say "nothing"
    # while the auto desk is happily opening a scalp. Cached scan → cheap.
    setup_ko_l: list[str] = []
    setup_en_l: list[str] = []
    try:
        from services.intraday_setup import scan as _iscan
        _act = (_iscan(db).get("act_now") or [])[:3]
        for s in _act:
            _tb = s.get("target_band") or [None, None]
            _pr = s.get("ai_1h_prob")
            _prk = f" · 🤖 {_pr}%" if _pr is not None else ""
            setup_ko_l.append(
                f"- **{s.get('name')}** — 지금 진입, 목표 {_fmt(_tb[0])}원 · 손절 {_fmt(s.get('stop'))}원 · "
                f"최대 60분 (확신 {s.get('confidence')}%{_prk})")
            setup_en_l.append(
                f"- **{s.get('en_name') or s.get('name')}** — enter now, target ₩{_fmt(_tb[0])} · "
                f"stop ₩{_fmt(s.get('stop'))} · max 60 min (conf {s.get('confidence')}%{_prk})")
    except Exception:
        pass
    setup_sec_ko = ("\n**⚡ 1시간 단타 후보 (자동매매가 보는 것과 동일)**\n"
                    + ("\n".join(setup_ko_l) if setup_ko_l
                       else "- 지금은 없음 — 1시간 스캐너 기준 안전한 자리가 없습니다"))
    setup_sec_en = ("\n**⚡ 1-hour trade candidates (exactly what the auto-trader sees)**\n"
                    + ("\n".join(setup_en_l) if setup_en_l
                       else "- none right now — the 1-hour scanner finds no safe setup at this moment"))
    if buys:
        # the BUY blocks below are a DIFFERENT horizon — say so, or an empty ⚡ list next
        # to green BUY badges reads as a contradiction (boss caught exactly this).
        setup_sec_ko += ("\n- 참고: 아래 🟢 매수 신호는 **며칠 보유용(투자 관점)** 입니다 — 60분 전용인 "
                         "자동매매는 대상이 아니며, 직접 매수해서 며칠 들고 가는 용도예요.")
        setup_sec_en += ("\n- Note: the 🟢 BUY signals below are **multi-day position ideas** — the "
                         "60-minute auto-trader doesn't trade those; they're for you to buy and hold "
                         "over days.")

    mkt_line = None
    try:
        from services.trading_brief import _mkt_ret_today
        mkt = _mkt_ret_today(db)
        if mkt is not None:
            tone_ko = "시장 우호적" if mkt >= 0.3 else "시장 급락 경계" if mkt <= -1.5 else "시장 약세 부담" if mkt < 0 else "시장 중립"
            tone_en = "supportive tape" if mkt >= 0.3 else "market plunging — caution" if mkt <= -1.5 else "soft tape" if mkt < 0 else "neutral tape"
            mkt_line = (f"KODEX200 today {mkt:+.2f}% — {tone_en}" if en
                        else f"KODEX200 오늘 {mkt:+.2f}% — {tone_ko}")
    except Exception:
        pass

    blocks = []
    i = 1
    for d in buys + watches:
        blocks.append(_pick_block(d, i, budget, en))
        i += 1

    tr_line = None
    try:
        from services.call_grader import track_record_line
        tr_line = track_record_line(db, "decision", lang)
    except Exception:
        pass

    # Plain-words action guide — what a friend would tell you to actually DO today.
    if en:
        if buys:
            plan = (f"**📌 What to actually do today**: {len(buys)} name(s) cleared the 3-method bar — "
                    "enter near the listed entry (not by chasing), keep the stop non-negotiable, and "
                    "size so one stop-out doesn't hurt. The WATCH names below are next in line, not buys yet.")
        else:
            plan = ("**📌 What to actually do today**: with the tape like this, the honest play is patience — "
                    "buying now means fighting the whole market, and even the best setup above lacks method "
                    "backing. Keep this list open, watch the exact trigger levels on each card, and let the "
                    "stock PROVE the turn (a volume-backed break or a held bounce) before a single won goes in. "
                    "Missing the first 1% of a real move costs far less than catching a falling one.")
    else:
        if buys:
            plan = (f"**📌 오늘의 행동 지침**: 3-method 기준을 통과한 종목이 {len(buys)}개 — 추격 말고 표기된 "
                    "진입가 부근에서 분할 진입, 손절은 예외 없이 지키고, 한 번의 손절이 아프지 않게 수량을 정하세요. "
                    "아래 관망 종목은 '다음 순번'이지 아직 매수가 아닙니다.")
        else:
            plan = ("**📌 오늘의 행동 지침**: 이런 시장에서는 기다리는 것이 정답에 가깝습니다 — 지금 사는 건 시장 "
                    "전체와 싸우는 일이고, 위 후보들 중 가장 좋은 자리도 아직 방법들의 지지가 부족합니다. 각 카드의 "
                    "전환 조건(거래량 동반 돌파 또는 지지 반등 확인)을 지켜보다가, 종목이 스스로 방향을 증명한 뒤에 "
                    "들어가세요. 진짜 반등의 첫 1%를 놓치는 비용이 떨어지는 칼을 잡는 비용보다 훨씬 쌉니다.")

    if en:
        if buys:
            head = f"**🛒 What to buy now — {len(buys)} BUY signal(s) from the 3-method engine (scanned {len(results)})**"
        else:
            head = (f"**🛒 Honest answer: NO stock passes the 3-method BUY gate right now** "
                    f"(scanned {len(results)}). Below are the best setups to WATCH and the exact "
                    f"trigger that would turn them into buys.")
        parts = [head]
        if mkt_line:
            parts.append(f"\n**Market**: {mkt_line}")
        parts.append(setup_sec_en)
        parts.append("\n" + "\n\n".join(blocks) if blocks else "\n(no data)")
        parts.append("\n" + plan)
        if not budget:
            parts.append("\n💰 Tell me your budget once (e.g. \"with 5 million won\") and I'll add share counts.")
        parts.append("\nAsk \"should I buy [name]?\" for the full per-stock breakdown. "
                     "Reference only — a reasoned synthesis, not a guarantee.")
        text = "\n".join(parts) + (tr_line or "")
    else:
        if buys:
            head = f"**🛒 지금 살 만한 종목 — 3-method 기준 매수 신호 {len(buys)}개 (스캔 {len(results)}종목)**"
        else:
            head = (f"**🛒 솔직한 답변: 지금은 3-method 기준을 통과하는 매수 신호 종목이 없습니다** "
                    f"(스캔 {len(results)}종목). 아래는 관찰할 만한 후보와, 매수로 바뀌는 조건입니다.")
        parts = [head]
        if mkt_line:
            parts.append(f"\n**시장 상황**: {mkt_line}")
        parts.append(setup_sec_ko)
        parts.append("\n" + "\n\n".join(blocks) if blocks else "\n(데이터 없음)")
        parts.append("\n" + plan)
        if not budget:
            parts.append("\n💰 자금 규모를 한 번 알려주시면 (예: \"500만원으로\") 종목별 수량까지 계산해 드립니다.")
        parts.append("\n종목별 상세 분석은 \"[종목명] 사도 돼?\"로 물어보세요. "
                     "참고용 종합 의견이며, 투자 권유나 수익 보장이 아닙니다.")
        text = "\n".join(parts) + (tr_line or "")

    # DETAIL LAYER — same as the single-stock reports: grounded LLM deep dive over the
    # scan's own numbers (strictly no new facts). Best-effort; skipped on failure.
    try:
        from services.assistant_agent import _elaborate_answer
        compact = {"market": mkt_line,
                   "picks": [{"name": d.get("name"), "decision": d.get("decision"),
                              "score": d.get("score"), "price": d.get("price"),
                              "ml": d.get("method1_ml"), "analysis": d.get("method2_analysis"),
                              "wave": d.get("method3_wave"),
                              "technicals": {k: (d.get("technicals") or {}).get(k)
                                             for k in ("support", "resistance", "pos_in_range",
                                                       "summary_ko", "summary_en")},
                              "news_score": (d.get("news") or {}).get("score"),
                              "flows": {k: (d.get("flows") or {}).get(k)
                                        for k in ("tag", "tag_en", "foreign_5d", "inst_5d")}}
                             for d in (buys + watches)]}
        _extra = _elaborate_answer(transcript or ("which stock should I buy now" if en else "지금 뭐 살까"),
                                   lang or ("en" if en else "ko"),
                                   [{"tool": "buy_picks_scan", "result": compact}])
        if _extra:
            text = text + "\n\n" + _extra
    except Exception:
        pass

    return {"buys": buys, "watches": watches, "scanned": len(results),
            "reasoning_ko" if not en else "reasoning_en": text,
            "reply": text}
