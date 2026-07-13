"""smart_size.py — engine-PREDICTED position sizing (boss 2026-07-13).

Replaces the flat "10% of equity" arithmetic with the professional formula:

  1. RISK CORE   : every trade risks the same money — RISK_PCT of equity — so
                   shares = risk_budget ÷ stop_distance (tight stop → more shares,
                   wide stop → fewer; equal pain when wrong)
  2. CONVICTION  : the signal's quality scales the bet ×0.8 … ×1.2 —
                   confidence, 🤖 AI 1-hour probability (Machine Learning) and
                   📊 layer-⑩ pattern up-rate (the graph-movement history)
  3. SAFETY CAPS : ≤ CONC_CAP of equity in one stock; cheap/thin stocks
                   (<₩100k/share) additionally capped at CHEAP_VALUE_CAP so a
                   ₩4B account can't dump ₩400M into a small cap (Friday's rot)

Conviction weights are STARTER values — they get re-fitted from the graded trade
record once it's ~2 weeks old (which multipliers actually predicted wins).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger("vip.smart_size")

RISK_PCT = 0.5            # % of equity risked per trade (industry 0.5–1%)
CONC_CAP = 0.20           # max fraction of equity in one position
CHEAP_PX = 100_000
CHEAP_VALUE_CAP = 200_000_000   # small caps: ≤ ₩200M per position (liquidity guard)
MULT_MIN, MULT_MAX = 0.8, 1.2


def _equity(db) -> Optional[float]:
    """Fast equity approximation: cash + positions at avg price (sizing doesn't
    need tick precision, and this avoids the heavy live-price sweep)."""
    try:
        cash = db.execute(text("SELECT cash FROM paper_desk_account WHERE id=1")).scalar()
        posv = db.execute(text(
            "SELECT COALESCE(SUM(qty*avg_price),0) FROM paper_desk_positions")).scalar()
        if cash is None:
            return None
        return float(cash) + float(posv or 0)
    except Exception:
        db.rollback()
        return None


def suggest(db, price: float, stop_pct: Optional[float],
            conf: Optional[int] = None, ai_prob: Optional[int] = None,
            pattern_up: Optional[int] = None) -> Optional[dict[str, Any]]:
    """Engine-predicted size for one trade. None when inputs are unusable."""
    try:
        price = float(price)
        if price <= 0:
            return None
        eq = _equity(db)
        if not eq or eq <= 0:
            return None
        sp = float(stop_pct) if stop_pct else 1.0
        sp = max(0.4, min(sp, 3.0))
        risk_won = eq * RISK_PCT / 100.0
        base_val = risk_won / (sp / 100.0)
        # conviction multiplier — ML (AI prob), 📊 pattern, and the fused confidence
        mult = 0.9
        parts_ko: list[str] = []
        parts_en: list[str] = []
        if conf is not None and conf >= 70:
            mult += 0.1
            parts_ko.append(f"확신 {conf}%")
            parts_en.append(f"conf {conf}%")
        if ai_prob is not None and ai_prob >= 55:
            mult += 0.1
            parts_ko.append(f"AI {ai_prob}%")
            parts_en.append(f"AI {ai_prob}%")
        if pattern_up is not None and pattern_up >= 55:
            mult += 0.1
            parts_ko.append(f"과거패턴 {pattern_up}%")
            parts_en.append(f"pattern {pattern_up}%")
        if (conf is not None and conf < 65) and not parts_ko:
            mult -= 0.1
        mult = max(MULT_MIN, min(MULT_MAX, mult))
        val = base_val * mult
        capped_by = None
        if val > eq * CONC_CAP:
            val = eq * CONC_CAP
            capped_by = "conc"
        if price < CHEAP_PX and val > CHEAP_VALUE_CAP:
            val = CHEAP_VALUE_CAP
            capped_by = "liquidity"
        qty = int(val // price)
        if qty < 1:
            qty = 1
        val = qty * price
        risk_at_stop = val * sp / 100.0
        line_ko = (f"엔진 산출 수량: 손절까지 -{sp:g}%인 이 거래에서 계좌의 {RISK_PCT:g}%"
                   f"(₩{risk_won:,.0f})만 잃도록 역산 → 기본 ₩{base_val:,.0f}"
                   + (f" × 확신가중 {mult:.1f} ({' · '.join(parts_ko)})" if parts_ko else f" × {mult:.1f}")
                   + (" → 한 종목 20% 상한 적용" if capped_by == "conc" else "")
                   + (" → 소형주 유동성 상한(₩2억) 적용" if capped_by == "liquidity" else "")
                   + f" = {qty:,}주 (≈₩{val:,.0f} · 손절 시 실제 손실 ≈₩{risk_at_stop:,.0f})")
        line_en = (f"Engine-sized: with a -{sp:g}% stop this trade risks only {RISK_PCT:g}% of equity"
                   f" (₩{risk_won:,.0f}) → base ₩{base_val:,.0f}"
                   + (f" × conviction {mult:.1f} ({' · '.join(parts_en)})" if parts_en else f" × {mult:.1f}")
                   + (" → 20%-per-stock cap" if capped_by == "conc" else "")
                   + (" → small-cap liquidity cap (₩200M)" if capped_by == "liquidity" else "")
                   + f" = {qty:,} shares (≈₩{val:,.0f} · a stop-out costs ≈₩{risk_at_stop:,.0f})")
        return {"qty": qty, "value": round(val), "mult": round(mult, 2),
                "risk_won": round(risk_won), "risk_at_stop": round(risk_at_stop),
                "capped_by": capped_by, "line_ko": line_ko, "line_en": line_en}
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("smart_size failed: %s", str(e)[:100])
        return None
