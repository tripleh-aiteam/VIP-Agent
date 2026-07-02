"""movers.py — B3: "지금 움직이는 종목?" — live movers among the tracked universe.

Ranks the ~51 tracked stocks by what a scalper cares about RIGHT NOW: today's move %
and volume vs its own 20-day average (scaled by how much of the session has elapsed,
so a 10:00 AM volume isn't unfairly compared to a full day). A stock makes the list
on |move| ≥ 1% or session-adjusted volume ≥ 2x normal. Quotes come from the same
live source the rest of the chatbot uses (Kiwoom in-market / Naver fallback).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

MIN_MOVE_PCT = 1.0
MIN_VOL_RATIO = 2.0


def _session_fraction() -> float:
    """Elapsed fraction of the 09:00–15:30 KST session (floor 8% so the open isn't /0)."""
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    elapsed = (now.hour * 60 + now.minute) - 540
    return min(max(elapsed / 390.0, 0.08), 1.0)


def movers(db, n: int = 5) -> dict[str, Any]:
    from services.assistant_agent import _kr_market_open_now, _live_price_for_code
    from services.prediction_service import NAMES

    rows = db.execute(text(
        "SELECT ticker, avg(volume) av FROM ("
        "  SELECT ticker, volume, row_number() OVER (PARTITION BY ticker ORDER BY date DESC) rn"
        "  FROM raw_daily_prices WHERE ticker = ANY(:t) AND volume > 0 "
        "  AND date < CURRENT_DATE) x "
        "WHERE rn <= 20 GROUP BY ticker"), {"t": list(NAMES)}).fetchall()
    avg_vol = {r.ticker: float(r.av) for r in rows if r.av}

    in_market = _kr_market_open_now()
    frac = _session_fraction() if in_market else 1.0

    quotes: dict[str, dict] = {}
    ex = ThreadPoolExecutor(max_workers=8)            # no `with`: its exit would block on
    try:                                               # the slow worker despite the timeout
        futs = {ex.submit(_live_price_for_code, tk, nm): tk for tk, nm in NAMES.items()}
        for f in as_completed(futs, timeout=15):
            try:
                q = f.result()
                if q:
                    quotes[futs[f]] = q
            except Exception:
                pass
    except Exception:
        pass                                          # keep whatever finished in time
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    items = []
    for tk, q in quotes.items():
        try:
            chg = float(q.get("change_pct") or 0.0)
        except (TypeError, ValueError):
            chg = 0.0
        vr = None
        try:                                       # volume may arrive as "1,234,567" or None
            vol = float(str(q.get("volume")).replace(",", "")) if q.get("volume") else None
            if vol and avg_vol.get(tk):
                vr = round(vol / (avg_vol[tk] * frac), 1)
        except (TypeError, ValueError):
            pass
        if abs(chg) >= MIN_MOVE_PCT or (vr is not None and vr >= MIN_VOL_RATIO):
            items.append({"ticker": tk, "name": q.get("name") or NAMES.get(tk, tk),
                          "price": q.get("price"), "change_pct": round(chg, 2),
                          "vol_ratio": vr,
                          "score": abs(chg) + (max(vr - 1.0, 0.0) if vr else 0.0)})
    items.sort(key=lambda x: -x["score"])
    top = items[:n]

    when_ko = "실시간" if in_market else "오늘 마감 기준"
    when_en = "live" if in_market else "as of today's close"
    if not top:
        ko = (f"🔥 지금 움직이는 종목 ({when_ko}) — 추적 {len(quotes)}종목 중 기준(±{MIN_MOVE_PCT}% 이상 "
              f"또는 거래량 평소 {MIN_VOL_RATIO}배 이상)을 넘는 종목이 없습니다. 조용한 장입니다.")
        en = (f"🔥 Movers ({when_en}) — none of the {len(quotes)} tracked stocks exceed the bar "
              f"(±{MIN_MOVE_PCT}% move or {MIN_VOL_RATIO}x normal volume). A quiet tape.")
        return {"items": [], "reasoning_ko": ko, "reasoning_en": en}

    def _line(i, m, en=False):
        arrow = "📈" if m["change_pct"] > 0 else "📉" if m["change_pct"] < 0 else "•"
        vol_ko = f" · 거래량 평소의 {m['vol_ratio']}배" if m["vol_ratio"] else ""
        vol_en = f" · volume {m['vol_ratio']}x normal" if m["vol_ratio"] else ""
        px = f"{int(m['price']):,}" if m.get("price") else "-"
        if en:
            return f"{i}. {arrow} **{m['name']}** {m['change_pct']:+.1f}%{vol_en} (₩{px})"
        return f"{i}. {arrow} **{m['name']}** {m['change_pct']:+.1f}%{vol_ko} ({px}원)"

    ko = (f"🔥 지금 움직이는 종목 ({when_ko} · 추적 {len(quotes)}종목):\n"
          + "\n".join(_line(i + 1, m) for i, m in enumerate(top))
          + f"\n\n단타 각도가 궁금하면 '{top[0]['name']} 단타 될까?'처럼 물어보세요.")
    en = (f"🔥 Movers ({when_en} · {len(quotes)} tracked):\n"
          + "\n".join(_line(i + 1, m, en=True) for i, m in enumerate(top))
          + f"\n\nFor a scalp read, ask e.g. \"Can I scalp {top[0]['name']}?\"")
    return {"items": top, "reasoning_ko": ko, "reasoning_en": en}
