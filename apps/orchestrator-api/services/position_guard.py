"""position_guard.py — the boss's UNIVERSAL selling brain for paper positions.

Spec (boss 2026-07-10 + 2026-07-13, his words condensed):
  • applies to ALL companies he holds (not just the 20 board stocks)
  • rise +1% → the engine re-checks "will it rise again?" → yes: HOLD and keep
    checking (ride it) → the moment it falls 1% from its PEAK:
        auto-trading ON  → SELL automatically
        auto-trading OFF → RECOMMEND "please sell now" (semi-auto strip)
  • starts by falling: at −1% the engine checks "will it recover?" →
        not bullish → sell/recommend immediately
        bullish     → hold (grace) — but NEVER past the −2% HARD floor
  • the −2% hard floor and the trail act regardless of engine mood (discipline
    beats hope — his 퍼시스 −4.11% rot is exactly what this prevents)

Positions currently held by an OPEN auto-trade are skipped here — the auto-trader
manages its own trades with its own doors (now including the same trail).
Runs from paper_desk.state() (~4s while a page is open) + the 5-min cron backstop.
Paper money only.
"""
from __future__ import annotations

import logging
import time as _time
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger("vip.position_guard")

SOFT_STOP_PCT = 1.0     # −1%: sell unless the engine sees a recovery
HARD_STOP_PCT = 2.0     # −2%: sell no matter what (hope is not a strategy)
ARM_PCT = 1.0           # +1% above avg arms the trailing profit lock…
TRAIL_PCT = 1.0         # …which fires 1% below the peak

# The boss's 20 FOCUS companies — the semi-auto board's fixed panel list
# (the GUARD itself now protects EVERY held stock; this list is only the board)
GUARD_CODES = [
    "000660",  # SK하이닉스 (boss: first)
    "005930",  # 삼성전자 (second)
    "042660",  # 한화오션
    "035420",  # NAVER
    "009150",  # 삼성전기
    "373220",  # LG에너지솔루션
    "005380",  # 현대차
    "000270",  # 기아
    "005490",  # POSCO홀딩스
    "035720",  # 카카오
    "051910",  # LG화학
    "006400",  # 삼성SDI
    "105560",  # KB금융
    "055550",  # 신한지주
    "012450",  # 한화에어로스페이스
    "329180",  # HD현대중공업
    "034020",  # 두산에너빌리티
    "010140",  # 삼성중공업
    "042700",  # 한미반도체
    "066570",  # LG전자
]

_DDL = ("CREATE TABLE IF NOT EXISTS guard_peaks ("
        " ticker TEXT PRIMARY KEY, avg_price DOUBLE PRECISION,"
        " peak DOUBLE PRECISION, updated_at TIMESTAMPTZ DEFAULT now())")

_sell_cooldown: dict[str, float] = {}   # ticker → ts of a FAILED guard sell (anti-spam)
_bull_cache: dict[str, tuple[float, bool]] = {}   # ticker → (ts, bullish) 120s


def _ensure(db) -> None:
    try:
        db.execute(text(_DDL))
        db.commit()
    except Exception:
        db.rollback()


def _bullish(db, code: str) -> bool:
    """Cheap 'will it rise again?' check: live 5-min flow UP or the 1-hour AI ≥55%.
    Cached 120s — it's consulted only when a trigger fires."""
    hit = _bull_cache.get(code)
    if hit and _time.time() - hit[0] < 120:
        return hit[1]
    bull = False
    try:
        from services.micro_trend import micro_read
        m = micro_read(db, code) or {}
        if m.get("verdict") == "UP":
            bull = True
    except Exception:
        db.rollback()
    if not bull:
        try:
            from services.hourly_model import prob_up_1h
            p = prob_up_1h(db, code)
            if p is not None and p >= 0.55:
                bull = True
        except Exception:
            db.rollback()
    _bull_cache[code] = (_time.time(), bull)
    return bull


def run(db) -> dict[str, Any]:
    """One guard pass over ALL held positions. Auto ON → executes the sells;
    auto OFF → returns them as recommendations for the semi-auto strip.
    Returns {"sells": [...], "alerts": [...]}."""
    _ensure(db)
    out: dict[str, Any] = {"sells": [], "alerts": []}
    try:
        rows = db.execute(text(
            "SELECT ticker, name, qty, avg_price FROM paper_desk_positions "
            "WHERE qty > 0")).fetchall()
    except Exception:
        db.rollback()
        return out
    if not rows:
        return out
    auto_held: set = set()
    auto_on = False
    try:
        auto_held = {r[0] for r in db.execute(text(
            "SELECT ticker FROM auto_trades WHERE status='OPEN'")).fetchall()}
        from services.auto_trader import is_enabled
        auto_on = is_enabled(db)
    except Exception:
        db.rollback()
    from services.paper_desk import _live_price, place_order
    for tk, name, qty, avg in rows:
        if tk in auto_held:            # the auto-trade's own doors manage this one
            continue
        px, _nm = _live_price(tk)
        if not px or not avg:
            continue
        avg = float(avg)
        # peak tracking — reset when the lot changed (new avg = new position)
        peak = float(px)
        try:
            r = db.execute(text(
                "SELECT avg_price, peak FROM guard_peaks WHERE ticker=:t"), {"t": tk}).first()
            if r and r[0] is not None and abs(float(r[0]) - avg) < 0.5:
                peak = max(float(r[1] or px), float(px))
            db.execute(text(
                "INSERT INTO guard_peaks (ticker, avg_price, peak) VALUES (:t,:a,:p) "
                "ON CONFLICT (ticker) DO UPDATE SET avg_price=:a, peak=:p, updated_at=now()"),
                {"t": tk, "a": avg, "p": peak})
            db.commit()
        except Exception:
            db.rollback()
        pnl = (float(px) / avg - 1) * 100
        armed = peak >= avg * (1 + ARM_PCT / 100)
        reason: Optional[str] = None
        why_ko = why_en = ""
        plain_ko = plain_en = ""
        # 🔴 RIDING alert (boss 2026-07-13): while armed and still above the trail,
        # SHOW the hold decision loudly — "it will rise again → don't sell yet"
        if armed and float(px) > peak * (1 - TRAIL_PCT / 100) and _bullish(db, tk):
            out["alerts"].append({
                "ticker": tk, "name": name, "qty": int(qty), "price": float(px),
                "pnl_pct": round(pnl, 2), "action": "HOLD_RIDE",
                "reason_ko": (f"계속 오를 여력 — 팔지 마세요! 고점 ₩{peak:,.0f} · "
                              f"하락 시작하면 ₩{peak * (1 - TRAIL_PCT / 100):,.0f}(고점 -{TRAIL_PCT:.0f}%)에서 매도 알림"),
                "reason_en": (f"More upside likely — DON'T sell yet! Peak ₩{peak:,.0f} · "
                              f"if it turns down, sell alert at ₩{peak * (1 - TRAIL_PCT / 100):,.0f} (peak −{TRAIL_PCT:.0f}%)")})
        if armed and float(px) <= peak * (1 - TRAIL_PCT / 100):
            reason = "GUARD_TRAIL"
            why_ko = f"고점 ₩{peak:,.0f} 대비 -{TRAIL_PCT:.0f}% — 이익 보호, 지금 파세요"
            why_en = f"-{TRAIL_PCT:.0f}% off the ₩{peak:,.0f} peak — protect the profit, sell now"
            plain_ko = "쉽게: 오르던 힘이 꺾였어요. 지금 팔면 번 것을 지키고, 다음 신호가 오면 더 좋은 가격에 다시 살 수 있어요."
            plain_en = "In plain words: the climb has bent over. Selling now keeps what you earned — the next signal lets you re-enter at a better price."
        elif float(px) <= avg * (1 - HARD_STOP_PCT / 100):
            reason = "GUARD_HARD"
            why_ko = f"평단 대비 -{HARD_STOP_PCT:.0f}% 도달 — 무조건 손절선, 지금 파세요"
            why_en = f"-{HARD_STOP_PCT:.0f}% below your buy price — the unconditional floor, sell now"
            plain_ko = "쉽게: -2%는 '희망 금지'선이에요. 여기서 안 팔면 -5%가 되는 걸 이미 겪었죠 — 작게 끊는 것이 계좌를 지킵니다."
            plain_en = "In plain words: −2% is the no-hope line. We've seen what happens past it (−5%) — cutting small protects the account."
        elif float(px) <= avg * (1 - SOFT_STOP_PCT / 100):
            if _bullish(db, tk):
                # engine sees a recovery → HOLD (grace zone between −1% and −2%)
                out["alerts"].append({
                    "ticker": tk, "name": name, "qty": int(qty), "price": float(px),
                    "pnl_pct": round(pnl, 2), "action": "HOLD_BOUNCE",
                    "reason_ko": f"-{SOFT_STOP_PCT:.0f}% 이탈했지만 엔진이 반등 가능성을 봅니다 — "
                                 f"보유 (단, -{HARD_STOP_PCT:.0f}%가 되면 예외 없이 매도)",
                    "reason_en": f"below -{SOFT_STOP_PCT:.0f}% but the engine sees a bounce — "
                                 f"holding (sold without exception at -{HARD_STOP_PCT:.0f}%)"})
                continue
            reason = "GUARD_STOP"
            why_ko = f"평단 대비 -{SOFT_STOP_PCT:.0f}% + 반등 근거 없음 — 지금 파세요"
            why_en = f"-{SOFT_STOP_PCT:.0f}% below your buy + no recovery signs — sell now"
            plain_ko = "쉽게: 산 가격보다 -1% 내려왔는데, 다시 오를 근거를 엔진이 찾지 못했어요. 작은 손실로 끊는 게 큰 손실을 막는 길입니다."
            plain_en = "In plain words: it's −1% below your buy and the engine finds no reason it recovers. A small cut now prevents a big one later."
        if not reason:
            continue
        if not auto_on:
            # SEMI/MANUAL: recommend, don't act (boss 2026-07-13)
            out["alerts"].append({
                "ticker": tk, "name": name, "qty": int(qty), "price": float(px),
                "pnl_pct": round(pnl, 2), "action": "SELL_NOW",
                "reason_ko": why_ko, "reason_en": why_en,
                "plain_ko": plain_ko, "plain_en": plain_en, "kind": reason})
            continue
        # AUTO: act — with the anti-spam cooldown after a failed sell
        if _time.time() - _sell_cooldown.get(tk, 0) < 1800:
            continue
        r = place_order(db, tk, "SELL", int(qty), "market")
        if r.get("ok"):
            _sell_cooldown.pop(tk, None)
            try:
                db.execute(text("DELETE FROM guard_peaks WHERE ticker=:t"), {"t": tk})
                db.commit()
            except Exception:
                db.rollback()
            out["sells"].append({"ticker": tk, "qty": int(qty), "reason": reason,
                                 "fill": r.get("fill_price"), "avg": avg,
                                 "peak": round(peak)})
            logger.info("position_guard SELL %s x%s (%s) fill=%s", tk, qty, reason,
                        r.get("fill_price"))
        else:
            _sell_cooldown[tk] = _time.time()
            logger.warning("position_guard SELL %s failed (%s) — 30min cooldown",
                           tk, r.get("reason") or r.get("error"))
    return out


def info(db, code: str) -> Optional[dict[str, Any]]:
    """Guard status for one stock's panel: holding, protection lines, armed state."""
    _ensure(db)
    try:
        row = db.execute(text(
            "SELECT qty, avg_price FROM paper_desk_positions WHERE ticker=:t AND qty>0"),
            {"t": str(code).zfill(6)}).first()
    except Exception:
        db.rollback()
        return None
    if not row:
        return None
    qty, avg = int(row[0]), float(row[1])
    peak = None
    try:
        r = db.execute(text("SELECT peak FROM guard_peaks WHERE ticker=:t"),
                       {"t": str(code).zfill(6)}).first()
        peak = float(r[0]) if r and r[0] is not None else None
    except Exception:
        db.rollback()
    auto_managed = False
    try:
        auto_managed = bool(db.execute(text(
            "SELECT 1 FROM auto_trades WHERE status='OPEN' AND ticker=:t"),
            {"t": str(code).zfill(6)}).first())
    except Exception:
        db.rollback()
    armed = peak is not None and peak >= avg * (1 + ARM_PCT / 100)
    return {"qty": qty, "avg": avg, "peak": peak, "armed": armed,
            "auto_managed": auto_managed,
            "stop_line": round(avg * (1 - SOFT_STOP_PCT / 100)),
            "hard_line": round(avg * (1 - HARD_STOP_PCT / 100)),
            "trail_line": round(peak * (1 - TRAIL_PCT / 100)) if armed and peak else None}
