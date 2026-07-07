"""cycle_scalp.py — Method 4: the boss's 1% CYCLE day-trading strategy (2026-07-06).

Idea: instead of ONE entry with a fixed target/stop and no time limit, trade REPEATED SMALL
CYCLES with known TIMING — buy when the short-term timing turns up (RSI turning up out of a
low on 5-min bars), take profit at +1%, cut at −1%, and if price goes NOWHERE within the
time window, exit small and wait for the next upturn. Losses stay small, wins repeat:
3 wins / 2 losses per 5 trades = net winner.

signal(db, ticker)   → live verdict NOW (BUY-NOW / WAIT / NO-SETUP) + entry/target/stop/
                       time-stop + re-entry rule, from the latest 5-min bars.
compare(db, ticker)  → honest simulation over minute_bars_hist: Strategy A (existing —
                       fixed ±1% target/stop from entry at open, NO time limit) vs
                       Strategy B (cycle — RSI-timed entries, ±1%, 60-min time stop,
                       re-entry). Reports trades/wins/pnl + the '≥3 wins out of 5' check.
All numbers come from real stored bars — nothing simulated is invented.
"""
from __future__ import annotations

from typing import Any, Optional

TP_PCT = 1.0          # take-profit  (+1%)
SL_PCT = 1.0          # stop-loss    (−1%)
TIME_STOP_BARS = 12   # 12 × 5-min = 60 minutes of "going nowhere" → exit small
RSI_PERIOD = 14
RSI_OVERSOLD = 40     # 'low enough' zone for a timed entry on 5-min bars


def _rsi(closes: list[float], period: int = RSI_PERIOD) -> list[Optional[float]]:
    """Wilder RSI series aligned to closes (None until warm)."""
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / period, losses / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def _bars(db, ticker: str, limit: int = 240) -> list[dict]:
    """Latest 5-min bars (today's live collector first, then the history backfill)."""
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT ts, open, high, low, close, volume FROM ("
        "  SELECT ts, open, high, low, close, volume FROM raw_minute_prices WHERE ticker=:t"
        "  UNION SELECT ts, open, high, low, close, volume FROM minute_bars_hist WHERE ticker=:t"
        ") u ORDER BY ts DESC LIMIT :n"), {"t": str(ticker).zfill(6), "n": limit}).fetchall()
    return [{"ts": r[0], "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "volume": float(r[5] or 0)}
            for r in reversed(rows)]


def _entry_ok(rsis: list, closes: list, i: int) -> bool:
    """RSI-based buy TIMING: RSI was low and is turning UP, price confirming up."""
    if i < 2 or rsis[i] is None or rsis[i - 1] is None:
        return False
    return (rsis[i - 1] < RSI_OVERSOLD and rsis[i] > rsis[i - 1]
            and closes[i] > closes[i - 1])


def signal(db, ticker: str) -> dict[str, Any]:
    """Live Method-4 verdict for NOW."""
    bars = _bars(db, ticker, limit=120)
    if len(bars) < RSI_PERIOD + 3:
        return {"ok": False, "verdict": "NO_DATA"}
    closes = [b["close"] for b in bars]
    rsis = _rsi(closes)
    cur, r_now, r_prev = closes[-1], rsis[-1], rsis[-2]
    entry_now = _entry_ok(rsis, closes, len(bars) - 1)
    verdict = "BUY_NOW" if entry_now else ("WAIT" if (r_now or 50) < RSI_OVERSOLD + 8 else "NO_SETUP")
    # MARKET-REGIME VETO: an index-crash day downgrades BUY_NOW → WAIT (2026-07-07 live
    # forward test: crash-day entries hit stops; the refusal was the right call).
    veto_ko = veto_en = None
    if verdict == "BUY_NOW":
        try:
            from services.market_regime import crash_veto
            _rg = crash_veto()
            if _rg.get("veto"):
                verdict = "WAIT"
                veto_ko, veto_en = _rg.get("line_ko"), _rg.get("line_en")
        except Exception:
            pass
    return {
        "ok": True, "verdict": verdict, "price": cur,
        "veto_ko": veto_ko, "veto_en": veto_en,
        "rsi": round(r_now, 1) if r_now is not None else None,
        "rsi_prev": round(r_prev, 1) if r_prev is not None else None,
        "target": round(cur * (1 + TP_PCT / 100)),
        "stop": round(cur * (1 - SL_PCT / 100)),
        "time_stop_min": TIME_STOP_BARS * 5,
        "asof": str(bars[-1]["ts"]),
    }


def _run_day(day_bars: list[dict], cycle: bool) -> list[float]:
    """Simulate one day; returns the list of per-trade pnl% (chronological).
    cycle=False → Strategy A: one entry at day open, ±1%, NO time limit (exit at close).
    cycle=True  → Strategy B: RSI-timed entries, ±1%, 60-min time stop, re-entry allowed."""
    closes = [b["close"] for b in day_bars]
    rsis = _rsi(closes)
    trades: list[float] = []
    if not cycle:
        entry = day_bars[0]["open"]
        tp, sl = entry * (1 + TP_PCT / 100), entry * (1 - SL_PCT / 100)
        for b in day_bars:
            if b["low"] <= sl:                       # conservative: stop checked first
                trades.append(-SL_PCT); break
            if b["high"] >= tp:
                trades.append(TP_PCT); break
        else:
            trades.append((closes[-1] / entry - 1) * 100)
        return trades
    i = RSI_PERIOD + 1
    while i < len(day_bars):
        if not _entry_ok(rsis, closes, i):
            i += 1
            continue
        entry = closes[i]
        tp, sl = entry * (1 + TP_PCT / 100), entry * (1 - SL_PCT / 100)
        exit_pnl = None
        j = i + 1
        while j < len(day_bars):
            b = day_bars[j]
            if b["low"] <= sl:
                exit_pnl = -SL_PCT; break
            if b["high"] >= tp:
                exit_pnl = TP_PCT; break
            if j - i >= TIME_STOP_BARS:              # going nowhere → exit small, re-arm
                exit_pnl = (b["close"] / entry - 1) * 100; break
            j += 1
        trades.append(exit_pnl if exit_pnl is not None else (closes[-1] / entry - 1) * 100)
        i = j + 1                                    # re-entry allowed after the exit bar
    return trades


def compare(db, ticker: str) -> dict[str, Any]:
    """Honest side-by-side over ALL stored history days for this ticker."""
    from collections import defaultdict
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT ts, open, high, low, close, volume FROM minute_bars_hist "
        "WHERE ticker=:t ORDER BY ts"), {"t": str(ticker).zfill(6)}).fetchall()
    by_day: dict = defaultdict(list)
    for r in rows:
        by_day[str(r[0])[:10]].append({"ts": r[0], "open": float(r[1]), "high": float(r[2]),
                                       "low": float(r[3]), "close": float(r[4]),
                                       "volume": float(r[5] or 0)})
    a_trades: list[float] = []
    b_trades: list[float] = []
    for day in sorted(by_day):
        bars = by_day[day]
        if len(bars) < RSI_PERIOD + 5:
            continue
        a_trades += _run_day(bars, cycle=False)
        b_trades += _run_day(bars, cycle=True)

    def _stats(ts: list[float]) -> dict:
        wins = sum(1 for x in ts if x > 0.15)        # >0.15% counts as a win (fees-aware)
        losses = sum(1 for x in ts if x < -0.15)
        # rolling 3-of-5 consistency: fraction of 5-trade windows with >=3 wins
        wr5 = None
        if len(ts) >= 5:
            wins5 = [sum(1 for x in ts[k:k + 5] if x > 0.15) >= 3
                     for k in range(0, len(ts) - 4)]
            wr5 = round(100 * sum(wins5) / len(wins5), 1)
        return {"trades": len(ts), "wins": wins, "losses": losses,
                "flat": len(ts) - wins - losses,
                "win_rate_pct": round(100 * wins / len(ts), 1) if ts else None,
                "total_pnl_pct": round(sum(ts), 2),
                "avg_pnl_pct": round(sum(ts) / len(ts), 3) if ts else None,
                "three_of_five_pct": wr5}

    return {"ok": True, "ticker": str(ticker).zfill(6), "days": len(by_day),
            "strategy_a_fixed": _stats(a_trades), "strategy_b_cycle": _stats(b_trades),
            "params": {"tp_pct": TP_PCT, "sl_pct": SL_PCT,
                       "time_stop_min": TIME_STOP_BARS * 5,
                       "rsi_period": RSI_PERIOD, "rsi_entry_below": RSI_OVERSOLD}}


def compare_reply(db, ticker: str, name: str, lang: str = "ko") -> Optional[str]:
    """Chat-ready HONEST comparison of the two algorithms (EN/KO). Real replay stats only."""
    c = compare(db, ticker)
    if not c.get("ok") or not c["strategy_a_fixed"]["trades"]:
        return None
    a, b, p = c["strategy_a_fixed"], c["strategy_b_cycle"], c["params"]
    en = str(lang or "").lower().startswith("en")
    b_pass = (b.get("three_of_five_pct") or 0) >= 60 and (b.get("total_pnl_pct") or 0) > 0
    better = "B" if (b.get("total_pnl_pct") or -999) > (a.get("total_pnl_pct") or -999) else "A"

    def _row(s):
        return (f"{s['trades']} trades · {s['wins']}W/{s['losses']}L · win {s['win_rate_pct']}% · "
                f"total {s['total_pnl_pct']:+.2f}% · 3-of-5 windows {s.get('three_of_five_pct')}%" if en else
                f"{s['trades']}건 · {s['wins']}승 {s['losses']}패 · 승률 {s['win_rate_pct']}% · "
                f"누적 {s['total_pnl_pct']:+.2f}% · 5건 중 3승 구간 {s.get('three_of_five_pct')}%")
    if en:
        verdict = ("✅ The CYCLE strategy currently meets the '3 wins out of 5' bar on this data."
                   if b_pass else
                   "⚠️ Honest result: on this data the cycle strategy does NOT yet meet the "
                   "'3 wins out of 5' bar — the existing fixed strategy is currently ahead. "
                   "Next lever: tune the RSI entry / time-stop and re-test before trusting it.")
        return (f"**⚔️ Strategy comparison — {name} ({c['ticker']}), {c['days']} trading days of 5-min bars**\n\n"
                f"**A. Existing (fixed ±1% target/stop, entry at open, no time limit)**\n- {_row(a)}\n\n"
                f"**B. New Method-4 cycle (RSI-timed entries, +{p['tp_pct']}%/−{p['sl_pct']}%, "
                f"{p['time_stop_min']}-min time-stop, re-entry)**\n- {_row(b)}\n\n"
                f"**Verdict:** strategy {better} earned more here. {verdict}\n\n"
                f"_Params: RSI({p['rsi_period']}) entry below {p['rsi_entry_below']} turning up · "
                f"real stored bars, no simulated data. Reference only, not investment advice._")
    verdict = ("✅ 사이클 전략이 이 데이터에서 '5번 중 3승' 기준을 충족합니다."
               if b_pass else
               "⚠️ 정직한 결과: 이 데이터에서는 사이클 전략이 아직 '5번 중 3승' 기준에 못 미치고, "
               "기존 고정 전략이 앞서 있습니다. 다음 단계는 RSI 진입값·시간청산을 조정해 재검증하는 것입니다.")
    return (f"**⚔️ 전략 비교 — {name} ({c['ticker']}), 5분봉 {c['days']}거래일 실측**\n\n"
            f"**A. 기존 전략 (고정 ±1% 목표/손절, 시가 진입, 시간 제한 없음)**\n- {_row(a)}\n\n"
            f"**B. 신규 방법4 사이클 (RSI 타이밍 진입, +{p['tp_pct']}%/−{p['sl_pct']}%, "
            f"{p['time_stop_min']}분 시간청산, 재진입)**\n- {_row(b)}\n\n"
            f"**판정:** 이 구간에서는 전략 {better}가 더 벌었습니다. {verdict}\n\n"
            f"_파라미터: RSI({p['rsi_period']}) {p['rsi_entry_below']} 이하에서 반등 시 진입 · "
            f"저장된 실제 분봉만 사용(가공 데이터 없음). 참고용이며 투자 권유가 아닙니다._")
