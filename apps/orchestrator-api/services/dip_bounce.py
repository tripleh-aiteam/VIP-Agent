"""dip_bounce.py — the boss's own strategy, automated: buy the BIG dip, sell the bounce.

Measured on our real hourly data (722 transitions, 2026-07-03): after a ≥1.5% one-hour
drop the NEXT hour closed UP 81.7% of the time (n=71, avg +1.87%). Smaller dips are coin
flips (52–56%) and flat hours are traps (40%) — so the size filter IS the edge.

scan(db) hunts the tracked universe for stocks:
  1. down ≥ MIN_DIP% vs ~1 hour ago (intraday_snapshot_history series, cloud-collected),
  2. with a confirming tape (order book NOT ask-heavy),
  3. no deal-breakers (공매도 과열, market plunging),
and returns render-ready candidates with entry / +target / stop / ~hold time. Every
shown candidate is logged as a chatbot_call (intent='dip_bounce', 60-min horizon), so the
81.7% keeps being MEASURED forward — if the edge decays, the scoreboard will say so.
Advisory only; the 81.7% is history, not a promise.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text

from services.logger import log

MIN_DIP = 1.5          # fallback % drop over ~1h — the measured edge threshold
TARGET_PCT = 1.2       # fallback take-profit
STOP_PCT = 1.0         # fallback stop below entry
LOOKBACK_MIN = (50, 75)  # "1 hour ago" tolerance window
# NOTE: live values come from strategy_params (Autopilot B tunes them nightly within
# rails); the constants above are only the cold-start fallback.


def _params(db) -> tuple[float, float, float]:
    try:
        from services.self_tune import params_get
        p = params_get(db, "dip_bounce")
        return float(p["min_dip"]), float(p["target_pct"]), float(p["stop_pct"])
    except Exception:
        return MIN_DIP, TARGET_PCT, STOP_PCT


def _candidates(db, min_dip: float = MIN_DIP, limit: int = 5) -> list[dict[str, Any]]:
    """Stocks down ≥min_dip% vs ~1h ago, tape-confirmed, ranked by dip size."""
    rows = db.execute(text("""
        WITH cur AS (
          SELECT ticker, price, imbalance, short_ratio, ts
          FROM realtime_snapshot
          WHERE ts > now() - interval '10 minutes' AND price IS NOT NULL
        ), past AS (
          SELECT DISTINCT ON (h.ticker) h.ticker, h.price AS p1h
          FROM intraday_snapshot_history h
          WHERE h.ts BETWEEN now() - interval '75 minutes' AND now() - interval '50 minutes'
            AND h.price IS NOT NULL
          ORDER BY h.ticker, h.ts DESC
        )
        SELECT c.ticker, c.price::float cur, p.p1h::float p1h,
               c.imbalance::float imb, c.short_ratio::float sr
        FROM cur c JOIN past p ON p.ticker = c.ticker
        WHERE p.p1h > 0
    """)).fetchall()
    out = []
    for r in rows:
        dip = (r.cur - r.p1h) / r.p1h * 100.0
        if dip > -min_dip:
            continue
        reasons_ko, reasons_en, ok = [], [], True
        if r.imb is not None and r.imb < -0.15:
            ok = False                                    # heavy ask-side tape → knife
            reasons_ko.append("호가 매도우위 — 아직 떨어지는 칼날")
        if r.sr is not None and float(r.sr) >= 10:
            ok = False
            reasons_ko.append(f"공매도 과열({r.sr}%)")
        out.append({"ticker": r.ticker, "cur": r.cur, "p1h": r.p1h,
                    "dip_pct": round(dip, 2), "imbalance": r.imb, "short_ratio": r.sr,
                    "pass": ok, "veto_ko": " · ".join(reasons_ko)})
    out.sort(key=lambda x: x["dip_pct"])
    return out[:limit * 2]                                # keep some vetoed ones to show


def scan(db, min_dip: Optional[float] = None, limit: int = 5,
         agent_id: Optional[str] = None, lang: Optional[str] = None,
         log_calls: bool = True) -> dict[str, Any]:
    """Full scan → {market_ok, candidates[], reasoning_ko, reasoning_en}.
    min_dip=None → use the Autopilot-tuned parameter set."""
    from services.stock_resolver import display_name, display_name_en

    _dip, _tgt, _stp = _params(db)
    if min_dip is None:
        min_dip = _dip

    mkt = None
    try:
        from services.trading_brief import _mkt_ret_today
        mkt = _mkt_ret_today(db)
    except Exception:
        pass
    market_plunge = mkt is not None and mkt <= -2.5

    cands = _candidates(db, min_dip=min_dip, limit=limit)
    passed = [c for c in cands if c["pass"]][:limit]
    vetoed = [c for c in cands if not c["pass"]][:3]

    # grade every SHOWN candidate — the 81.7% must keep proving itself forward
    if log_calls and passed and not market_plunge:
        try:
            from services.call_grader import log_call
            for c in passed:
                entry = c["cur"]
                log_call(db, ticker=c["ticker"], action="BUY", intent="dip_bounce",
                         ref_price=entry, target=round(entry * (1 + _tgt / 100)),
                         stop=round(entry * (1 - _stp / 100)), horizon_min=60,
                         name=display_name(c["ticker"]), agent_id=agent_id, lang=lang)
        except Exception:
            pass

    # ---- render (KO/EN identical structure) ----
    ko, en = [], []
    ko.append("**📉→📈 낙폭 반등 후보 (1시간 −{:.1f}% 이상 급락 스캔)**".format(min_dip))
    en.append("**📉→📈 Dip-bounce candidates (scan: ≥{:.1f}% drop in ~1h)**".format(min_dip))
    ko.append(f"_실측 근거: 최근 데이터에서 1시간 −1.5% 이상 급락 후 다음 1시간 상승 확률 81.7% "
              f"(71건, 평균 +1.87%). 과거 통계이며 보장이 아닙니다._")
    en.append(f"_Measured basis: after a ≥1.5% one-hour drop, the next hour closed up 81.7% "
              f"of the time in our data (n=71, avg +1.87%). History, not a guarantee._")
    if market_plunge:
        ko += ["", f"🚫 오늘은 지수 급락일(KODEX {mkt:+.2f}%)이라 반등 단타 자체를 쉬는 게 좋습니다 — "
                   "급락장에서는 이 전략의 승률이 검증돼 있지 않습니다."]
        en += ["", f"🚫 The index is plunging today (KODEX {mkt:+.2f}%) — sit this strategy out; "
                   "the edge isn't proven in crash regimes."]
    elif not passed:
        ko += ["", "지금은 조건(−1.5%/1시간 + 호가 확인)을 통과한 종목이 없습니다. "
               "장중에 다시 물어보시거나, 알림이 잡히면 알려드릴게요."]
        en += ["", "No stock passes the filter right now (−1.5%/1h + tape confirm). "
               "Ask again during the session."]
    else:
        for i, c in enumerate(passed, 1):
            nm, nme = display_name(c["ticker"]), display_name_en(c["ticker"])
            entry = c["cur"]
            tgt = round(entry * (1 + _tgt / 100))
            stp = round(entry * (1 - _stp / 100))
            imb_ko = ("호가 매수우위" if (c["imbalance"] or 0) > 0 else "호가 중립")
            imb_en = ("bid-heavy book" if (c["imbalance"] or 0) > 0 else "neutral book")
            ko += ["", f"**{i}. {nm} ({c['ticker']})** — 1시간 {c['dip_pct']:+.2f}%",
                   f"· 진입 {entry:,.0f}원 · 목표 {tgt:,}원 (+{_tgt}%) · 손절 {stp:,}원 (−{_stp}%) · 예상 보유 ~60분",
                   f"· 확인: {imb_ko}" + (f" · 공매도 {c['short_ratio']}%" if c["short_ratio"] is not None else "")]
            en += ["", f"**{i}. {nme} ({c['ticker']})** — 1h {c['dip_pct']:+.2f}%",
                   f"- Entry ₩{entry:,.0f} · Target ₩{tgt:,} (+{_tgt}%) · Stop ₩{stp:,} (−{_stp}%) · hold ~60 min",
                   f"- Confirm: {imb_en}" + (f" · short ratio {c['short_ratio']}%" if c["short_ratio"] is not None else "")]
        ko += ["", "⚠️ 규칙: 목표 도달 시 욕심 없이 매도, 손절가 도달 시 예외 없이 정리. "
               "이 후보들은 자동 채점되어 실제 승률이 계속 기록됩니다."]
        en += ["", "⚠️ Rules: sell at target without greed, cut at the stop without exception. "
               "Every candidate shown here is auto-graded so the real win rate stays measured."]
    if vetoed:
        ko += ["", "제외된 급락 종목 (조건 미달):"]
        en += ["", "Excluded big dips (failed confirmation):"]
        for c in vetoed:
            ko.append(f"· {display_name(c['ticker'])} {c['dip_pct']:+.2f}% — {c['veto_ko']}")
            en.append(f"- {display_name_en(c['ticker'])} {c['dip_pct']:+.2f}% — failed tape/short check")
    ko.append("")
    ko.append("※ 참고 의견이며 투자 권유가 아닙니다.")
    en.append("")
    en.append("Note: reference only, not investment advice.")
    return {"market_ret": mkt, "market_plunge": market_plunge,
            "candidates": passed, "vetoed": vetoed,
            "reasoning_ko": "\n".join(ko), "reasoning_en": "\n".join(en)}
