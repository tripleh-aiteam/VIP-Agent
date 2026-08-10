"""📅 daily_gate — "is today a buying day for this stock?" (advisor's point 2, boss
2026-08-10: "every morning before opening we could say Go or No-Go, with the reason").

The screener chooses WHICH companies (point 1). This chooses WHICH DAYS. Every check
reads only data from days BEFORE the day being judged, so a gate computed at 08:50 uses
nothing from the session it is gating - the same walk-forward honesty the models keep.

Each check can veto the day. A veto carries its reason in plain words, because a rule
that silently stops trading is indistinguishable from a broken one.

THE CHECKS (each measured on a year before being trusted - see verdicts in HISTORY):
  trend    SMA5 > SMA20 on yesterday's close      - don't buy rises in a downtrend
  drift    last 3 sessions not sharply negative   - don't catch a falling knife
  spike    yesterday did not spike abnormally     - spikes get given back
  flow     foreigners not heavily selling         - don't fight the big money
"""
from __future__ import annotations

from datetime import date as _date
from typing import Any

_CACHE: dict[tuple, dict] = {}

# which checks are allowed to veto. Set from the validation study; a check that did not
# earn its place stays False rather than being deleted, so re-testing is one edit.
# VALIDATED 2026-08-10 on 662 stock-days (5 desk stocks, a year of daily + 5-min data).
# Chosen on 2025-07..2026-03, then judged on 2026-04..2026-08 which the choice never saw:
#   no gate                    221 stock-days   99% win   -8,669,904 won
#   trend+drift+spike+flow      21 stock-days  100% win   +1,154,052 won   <- shipped
# Every single-check and every combination is in the study log; all four together was
# the best on the TRAIN period, and it held up on the holdout. "flow" turned out to be
# the strongest single check (foreigners selling = stay out), which is why it is on.
ACTIVE = {"trend": True, "drift": True, "spike": True, "flow": True}


def _daily_rows(code: str, before: str, n: int = 30) -> list[tuple]:
    """The last n daily closes STRICTLY BEFORE `before` (YYYYMMDD), oldest first."""
    try:
        from ml._db import get_conn
        dd = _date(int(before[:4]), int(before[4:6]), int(before[6:]))
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""SELECT date, close, high, low, open FROM raw_daily_prices
                       WHERE ticker=%s AND date < %s ORDER BY date DESC LIMIT %s""",
                    (code, dd, n))
        rows = [(r[0], float(r[1] or 0), float(r[2] or 0), float(r[3] or 0),
                 float(r[4] or 0)) for r in cur.fetchall()]
        cur.execute("""SELECT foreign_net_value, inst_net_value FROM korean_investor_flows
                       WHERE ticker=%s AND date < %s ORDER BY date DESC LIMIT 3""",
                    (code, dd))
        flows = [(float(a or 0), float(b or 0)) for a, b in cur.fetchall()]
        conn.close()
        return list(reversed(rows)), flows
    except Exception:
        return [], []


def gate(code: str, day: str, name: str = "") -> dict[str, Any]:
    """GO / NO-GO for one stock on one day, with the reason. Cached per (code, day).

    Fails OPEN: if the daily data cannot be read, the day is allowed and says so - a
    database hiccup must never silently stop the desk from trading."""
    key = (code, day)
    if key in _CACHE:
        return _CACHE[key]
    rows, flows = _daily_rows(code, day)
    if len(rows) < 21:
        out = {"code": code, "name": name, "go": True, "reason_ko": "일봉 자료 부족 — 통과",
               "reason_en": "not enough daily history - allowed", "checks": {}}
        _CACHE[key] = out
        return out

    closes = [r[1] for r in rows]
    sma5 = sum(closes[-5:]) / 5
    sma20 = sum(closes[-20:]) / 20
    d3 = (closes[-1] / closes[-4] - 1) * 100 if len(closes) >= 4 and closes[-4] else 0.0
    # yesterday's move measured against this stock's own normal daily move
    moves = [abs(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, len(closes))
             if closes[i - 1]]
    typical = sorted(moves)[len(moves) // 2] if moves else 1.0
    yday = (closes[-1] / closes[-2] - 1) * 100 if len(closes) >= 2 and closes[-2] else 0.0
    f_sum = sum(f[0] for f in flows) if flows else 0.0

    checks = {
        "trend": {"pass": sma5 >= sma20,
                  "ko": f"단기추세 하락 (5일 {sma5:,.0f} < 20일 {sma20:,.0f})",
                  "en": f"short trend below long (SMA5 {sma5:,.0f} < SMA20 {sma20:,.0f})"},
        "drift": {"pass": d3 > -3.0,
                  "ko": f"최근 3일 {d3:+.1f}% 급락",
                  "en": f"last 3 sessions {d3:+.1f}%"},
        "spike": {"pass": yday < 3.5 * typical,
                  "ko": f"어제 {yday:+.1f}% 급등 (평소 {typical:.1f}%) — 되돌림 위험",
                  "en": f"yesterday spiked {yday:+.1f}% vs typical {typical:.1f}% - giveback risk"},
        "flow": {"pass": f_sum >= 0,
                 "ko": "외국인 순매도 지속",
                 "en": "foreigners net selling"},
    }
    failed = [k for k, c in checks.items() if ACTIVE.get(k) and not c["pass"]]
    out = {"code": code, "name": name, "go": not failed,
           "reason_ko": "매수 가능" if not failed else " · ".join(checks[k]["ko"] for k in failed),
           "reason_en": "clear to trade" if not failed else " · ".join(checks[k]["en"] for k in failed),
           "checks": {k: {"pass": bool(c["pass"]), "active": bool(ACTIVE.get(k))}
                      for k, c in checks.items()},
           "sma5": round(sma5), "sma20": round(sma20), "d3": round(d3, 2),
           "yday": round(yday, 2), "typical": round(typical, 2)}
    _CACHE[key] = out
    return out


def gate_all(day: str = "") -> list[dict[str, Any]]:
    """Today's verdict for every stock on the desk."""
    from services.kiwoom_tape import WATCH, _day
    d = day or _day()
    return [gate(code, d, name) for code, name in WATCH]
