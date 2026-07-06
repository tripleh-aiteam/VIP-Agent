"""readiness.py — Phase C: the real-money readiness gate, automated.

The agreed rule before trading real money: the chatbot's scalp calls must show a
58%+ win rate after ~0.25% costs across 100+ DECISIVELY graded calls (wins+losses;
flats don't count as evidence either way). This service turns that rule into a
checkable answer — an endpoint and a chat intent ("실전 매매 준비됐어?") — so the
go/no-go decision is read off measured data, never vibes. It also shows the
method-level beta-adjusted record (beat-market rate) as context, because raw win
rates were proven to be market beta in disguise.
"""
from __future__ import annotations

from typing import Any, Optional

GATE_DECISIVE_N = 100      # wins+losses needed before the gate can be judged
GATE_WIN_RATE = 58.0       # % wins (of decisive) needed to pass, after-cost target


def report(db, days: int = 90) -> dict[str, Any]:
    from services.call_grader import scoreboard as chat_sb
    sb = chat_sb(db, days=days)
    out: dict[str, Any] = {"gate": {"need_decisive": GATE_DECISIVE_N,
                                    "need_win_rate": GATE_WIN_RATE}, "days": days}

    by_intent = {}
    for intent, st in (sb.get("by_intent") or {}).items():
        w, l = st.get("win", 0), st.get("loss", 0)
        by_intent[intent] = {"decisive": w + l, "win": w, "loss": l,
                             "flat": st.get("flat", 0), "win_rate": st.get("win_rate")}
    out["chatbot"] = {"overall": sb.get("overall"), "by_intent": by_intent,
                      "open": sb.get("open")}

    sc = by_intent.get("scalp") or {"decisive": 0, "win_rate": None}
    dec_n, wr = sc["decisive"], sc.get("win_rate")
    if dec_n >= GATE_DECISIVE_N and wr is not None and wr >= GATE_WIN_RATE:
        status = "GO"
    elif dec_n >= GATE_DECISIVE_N:
        status = "NO_GO"
    else:
        status = "COLLECTING"
    out["scalp_gate"] = {"status": status, "decisive": dec_n, "win_rate": wr,
                         "progress_pct": min(round(dec_n / GATE_DECISIVE_N * 100), 100)}

    # method-level beta-adjusted context (the honest metric: beat-market, not raw wins)
    try:
        from services.scorekeeper_service import scoreboard as method_sb
        m = method_sb(db).get("by_method") or {}
        out["methods"] = {k: {"graded": v.get("graded"), "win_rate": v.get("win_rate"),
                              "beat_mkt_rate": v.get("beat_mkt_rate"),
                              "avg_excess_return": v.get("avg_excess_return")}
                          for k, v in m.items()}
    except Exception:
        out["methods"] = {}
    return out


def reply_text(db, lang: Optional[str] = None) -> str:
    en = str(lang or "").lower().startswith("en")
    r = report(db)
    g = r["scalp_gate"]
    status, dec_n, wr, prog = g["status"], g["decisive"], g["win_rate"], g["progress_pct"]
    bi = r["chatbot"]["by_intent"]

    def _row(intent, label_ko, label_en):
        st = bi.get(intent)
        if not st:
            return None
        wr_txt = f"{st['win_rate']}%" if st.get("win_rate") is not None else "-"
        if en:
            return f"- {label_en}: {st['win']}W·{st['loss']}L·{st['flat']} flat (win rate {wr_txt})"
        return f"· {label_ko}: {st['win']}승·{st['loss']}패·무승부 {st['flat']} (승률 {wr_txt})"

    rows = [x for x in (_row("scalp", "단타", "Scalp"), _row("decision", "매수/매도 판단", "Buy/sell calls"),
                        _row("position", "보유 조언", "Position advice")) if x]

    m = r.get("methods") or {}
    mrows = []
    for k, label_ko, label_en in (("analysis", "분석", "Analysis"), ("ml", "ML", "ML"),
                                  ("wave", "파동", "Wave"), ("consensus", "합의", "Consensus")):
        v = m.get(k)
        if not v or not v.get("graded"):
            continue
        if en:
            mrows.append(f"- {label_en}: {v['graded']} graded · beat-market {v.get('beat_mkt_rate')}% "
                         f"· excess {v.get('avg_excess_return')}%")
        else:
            mrows.append(f"· {label_ko}: {v['graded']}건 · 시장수익률 상회 {v.get('beat_mkt_rate')}% "
                         f"· 초과수익 {v.get('avg_excess_return')}%")

    # paper-trader ledger — the virtual "if you had followed the bot" record, the
    # fastest-growing evidence stream (30-60 trades/day vs ~5 chat calls)
    paper_ko = paper_en = []
    try:
        from services.paper_trader import scorecard
        sc = scorecard(db, days=7)
        c = sc["cumulative"]
        if c["n"]:
            paper_ko = ["", "**가상 매매(페이퍼) 성적 — 봇 신호를 그대로 따라했다면**",
                        f"· 누적 {c['n']}건 · {c['wins']}승 {c['losses']}패 · 합계 {c['total_net_pct']:+.2f}% (비용 차감)"
                        + f" · 미청산 {sc['open_now']}건"]
            paper_en = ["", "**Paper-trading record — following the bot's own signals**",
                        f"- Cumulative {c['n']} trades · {c['wins']}W {c['losses']}L · total {c['total_net_pct']:+.2f}% net"
                        + f" · {sc['open_now']} open"]
    except Exception:
        pass

    if en:
        head = {"GO": "🟢 GATE PASSED — small real-money scalping is justified by the data.",
                "NO_GO": "🔴 GATE FAILED — enough data, but the win rate is below the bar. Do NOT trade real money on these signals yet.",
                "COLLECTING": f"🟡 COLLECTING EVIDENCE — {dec_n}/{GATE_DECISIVE_N} decisive scalp calls graded ({prog}%). Too early to judge."}[status]
        L = [f"**📏 Real-money readiness check**", "", head, "",
             f"**The gate**: {GATE_DECISIVE_N}+ decisive scalp calls at {GATE_WIN_RATE}%+ win rate (after ~0.25% costs)."
             + (f" Current: {dec_n} decisive, win rate {wr}%." if wr is not None else f" Current: {dec_n} decisive."),
             "", "**Chatbot record (last 90d, really graded)**", *rows]
        if mrows:
            L += ["", "**Method record (beta-adjusted — the honest metric)**", *mrows]
        L += paper_en
        L += ["", "Keep using the chatbot daily during market hours — every call is graded automatically. "
              "Ask me this question anytime; the verdict updates with the data."]
        return "\n".join(L)

    head = {"GO": "🟢 게이트 통과 — 데이터 기준으로 소액 실전 단타를 시작해도 됩니다.",
            "NO_GO": "🔴 게이트 미달 — 표본은 충분하지만 승률이 기준 미만입니다. 아직 실전 투입은 안 됩니다.",
            "COLLECTING": f"🟡 증거 수집 중 — 결정적으로 채점된 단타 콜 {dec_n}/{GATE_DECISIVE_N}건 ({prog}%). 아직 판단하기 이릅니다."}[status]
    L = [f"**📏 실전 매매 준비도 점검**", "", head, "",
         f"**게이트 기준**: 결정적(승/패) 단타 콜 {GATE_DECISIVE_N}건 이상 + 승률 {GATE_WIN_RATE}% 이상 (비용 ~0.25% 감안)."
         + (f" 현재: {dec_n}건 · 승률 {wr}%." if wr is not None else f" 현재: {dec_n}건."),
         "", "**챗봇 실측 성적 (최근 90일, 실제 채점)**", *rows]
    if mrows:
        L += ["", "**방법별 성적 (베타 조정 — 정직한 지표)**", *mrows]
    L += paper_ko
    L += ["", "장중에 챗봇을 매일 쓰시면 모든 콜이 자동 채점됩니다. "
          "이 질문은 언제든 다시 물어보세요 — 데이터가 쌓일수록 판정이 갱신됩니다."]
    return "\n".join(L)
