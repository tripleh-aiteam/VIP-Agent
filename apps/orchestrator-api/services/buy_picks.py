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

SCAN_MAX = 6          # candidates to run decide() over (parallel, ~2 batches of 3)


def _candidates(db) -> list[str]:
    try:
        from db.models import OrchReport
        r = (db.query(OrchReport)
             .filter(OrchReport.report_type == "recommendation_report")
             .order_by(OrchReport.created_at.desc()).first())
        picks = ((r.content_json or {}).get("report") or {}).get("picks") if r else None
        if picks:
            return [str(p.get("ticker")).zfill(6) for p in picks if p.get("ticker")]
    except Exception:
        pass
    return ["005930", "000660", "005380", "079550", "042700", "012450", "064350", "003490"]


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
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_one, tk): tk for tk in tickers}
        try:
            for f in as_completed(futs, timeout=75):
                try:
                    d = f.result()
                    if d:
                        out.append(d)
                except Exception:
                    pass
        except Exception:
            pass                                   # keep what finished in time
    return out


def _fmt(v) -> str:
    try:
        return f"{int(float(v)):,}"
    except Exception:
        return "-"


_EN_NAMES = {"SK하이닉스": "SK Hynix", "삼성전자": "Samsung Electronics",
             "삼성전기": "Samsung Electro-Mechanics", "SK스퀘어": "SK Square",
             "한미반도체": "Hanmi Semiconductor", "카카오": "Kakao",
             "현대차": "Hyundai Motor", "한화오션": "Hanwha Ocean"}


def _pick_block(d: dict, i: int, budget: Optional[int], en: bool) -> str:
    name = d.get("name") or d.get("ticker")
    if en:
        name = _EN_NAMES.get(name, name)
    dec = (d.get("decision") or "HOLD").upper()
    conf = d.get("confidence") or "low"
    px = d.get("price")
    m1 = d.get("method1_ml") or {}
    m2 = d.get("method2_analysis") or {}
    m3 = d.get("method3_wave") or {}
    tech = d.get("technicals") or {}
    news_s = ((d.get("news") or {}).get("score") or 0)

    # levels: wave levels when it's a wave BUY, else box support/resistance
    entry = m3.get("entry") if (m3.get("verdict") == "BUY" and m3.get("entry")) else tech.get("support")
    stop = m3.get("stop") if (m3.get("verdict") == "BUY" and m3.get("stop")) else (
        int(tech["support"] * 0.98) if tech.get("support") else None)
    target = m3.get("target") if (m3.get("verdict") == "BUY" and m3.get("target")) else tech.get("resistance")

    dec_ko = {"BUY": "매수", "SELL": "매도", "HOLD": "관망"}[dec if dec in ("BUY", "SELL") else "HOLD"]
    conf_ko = {"high": "높음", "medium": "보통", "low": "낮음"}.get(conf, conf)
    m1c = (m1.get("call") or "HOLD").upper()
    m2c = (m2.get("signal") or "WATCH").upper()
    m3c = (m3.get("verdict") or "-")
    acc = m1.get("accuracy_pct")

    size_line = None
    if budget and dec == "BUY" and px:
        try:
            from services.position_size import size_position
            s = size_position(budget, float(px), float(stop) if stop else None)
            if s:
                size_line = (f"수량(자금 {budget:,}원): {s['shares']:,}주 ≈ {s['cost']:,}원"
                             + (f" · 손절 시 −{s['risk_won']:,}원" if s.get("risk_won") else ""))
                if en:
                    size_line = (f"Size (budget ₩{budget:,}): {s['shares']:,} shares ≈ ₩{s['cost']:,}"
                                 + (f" · −₩{s['risk_won']:,} if stopped" if s.get("risk_won") else ""))
        except Exception:
            pass

    if en:
        head = {"BUY": "🟢 BUY", "SELL": "🔴 avoid", "HOLD": "🟡 WATCH"}[dec if dec in ("BUY", "SELL") else "HOLD"]
        L = [f"**{i}. {name} — {head} (confidence {conf})**  ·  now ₩{_fmt(px)}",
             f"   · Methods: ML {m1c}" + (f" (acc {acc}%)" if acc is not None else "")
             + f" · Analysis {m2c} · Wave {m3c}"
             + f" · news {'positive' if news_s > 0 else 'negative' if news_s < 0 else 'neutral'}",
             f"   · Entry ~₩{_fmt(entry)} · target ₩{_fmt(target)} · stop ₩{_fmt(stop)}"]
        if size_line:
            L.append(f"   · {size_line}")
        if dec != "BUY":
            L.append(f"   · Becomes a buy: break above ₩{_fmt(tech.get('resistance'))} or a held bounce off ₩{_fmt(tech.get('support'))}")
        return "\n".join(L)
    head = {"BUY": "🟢 매수", "SELL": "🔴 회피", "HOLD": "🟡 관망"}[dec if dec in ("BUY", "SELL") else "HOLD"]
    L = [f"**{i}. {name} — {head} (확신 {conf_ko})**  ·  현재가 {_fmt(px)}원",
         f"   · 방법: ML {m1c}" + (f" (정확도 {acc}%)" if acc is not None else "")
         + f" · 분석 {m2c} · 파동 {m3c}"
         + f" · 뉴스 {'호재 우세' if news_s > 0 else '악재 우세' if news_s < 0 else '중립'}",
         f"   · 진입 ~{_fmt(entry)}원 · 목표 {_fmt(target)}원 · 손절 {_fmt(stop)}원"]
    if size_line:
        L.append(f"   · {size_line}")
    if dec != "BUY":
        L.append(f"   · 매수 전환 조건: 저항 {_fmt(tech.get('resistance'))}원 돌파 또는 지지 {_fmt(tech.get('support'))}원 반등 확인")
    return "\n".join(L)


def build(db, n: int = 3, transcript: str = "", user_key: Optional[str] = None,
          lang: Optional[str] = None) -> dict[str, Any]:
    en = str(lang or "").lower().startswith("en")

    budget = None
    try:
        from services.position_size import parse_budget, recall_budget, remember_budget
        budget = parse_budget(transcript)
        if budget and user_key:
            remember_budget(db, user_key, budget)
        elif user_key:
            budget = recall_budget(db, user_key)
    except Exception:
        pass

    results = _decide_many(_candidates(db)[:SCAN_MAX])
    results.sort(key=lambda d: -(d.get("score") or 0))
    buys = [d for d in results if (d.get("decision") or "").upper() == "BUY"][:n]
    watches = [d for d in results if d not in buys][: max(n - len(buys), 2 if not buys else 0)]

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
        parts.append("\n" + "\n\n".join(blocks) if blocks else "\n(no data)")
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
        parts.append("\n" + "\n\n".join(blocks) if blocks else "\n(데이터 없음)")
        if not budget:
            parts.append("\n💰 자금 규모를 한 번 알려주시면 (예: \"500만원으로\") 종목별 수량까지 계산해 드립니다.")
        parts.append("\n종목별 상세 분석은 \"[종목명] 사도 돼?\"로 물어보세요. "
                     "참고용 종합 의견이며, 투자 권유나 수익 보장이 아닙니다.")
        text = "\n".join(parts) + (tr_line or "")

    return {"buys": buys, "watches": watches, "scanned": len(results),
            "reasoning_ko" if not en else "reasoning_en": text,
            "reply": text}
