# -*- coding: utf-8 -*-
"""chat_briefing — Step 3 of the boss's 4-step plan (2026-08-27): "오늘 브리핑" /
"brief me" answers with one composed morning view — desk six, holdings & P&L,
today's chatbot orders, the checklist's current Top 3 — every number read from
the live desk and records, zero LLM.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text as _sq

from services.logger import log

KST = timezone(timedelta(hours=9))
_RANK_DIR = Path(__file__).resolve().parent.parent / "data" / "reco_rank"
_SIX = (("000660", "SK하이닉스"), ("005930", "삼성전자"), ("035420", "NAVER"),
        ("017670", "SK텔레콤"), ("042660", "한화오션"), ("034020", "두산에너빌리티"))


def is_briefing_q(transcript: Optional[str]) -> bool:
    t = (transcript or "").strip()
    if len(t) > 40:
        return False
    return bool(re.search(
        r"브리핑|briefing|brief\s*me|모닝\s*브리프|오늘\s*(상황|시장\s*어때|어때\s*\??$)"
        r"|morning\s*brief|today'?s?\s*(situation|summary)\b", t, re.IGNORECASE))


def _top3_now() -> list[dict]:
    try:
        files = sorted(_RANK_DIR.glob("*.jsonl"))
        if not files:
            return []
        last = files[-1].read_text(encoding="utf-8").strip().splitlines()[-1]
        return (json.loads(last).get("rows") or [])[:3]
    except Exception:
        return []


def reply(db, lang: str) -> str:
    en = str(lang or "").lower().startswith("en")
    now = datetime.now(KST)
    try:
        from services.kiwoom_tape import market_open
        opened = market_open()
    except Exception:
        opened = False
    mk = (("장중 🟢" if opened else "장 마감 ⚪") if not en
          else ("market OPEN 🟢" if opened else "market CLOSED ⚪"))
    L = [f"📋 **{'오늘 브리핑' if not en else 'Daily briefing'}** — "
         f"{now.strftime('%Y-%m-%d %H:%M')} KST · {mk}"]

    # 1) the six
    L.append("")
    L.append("**📡 데스크 6종목**" if not en else "**📡 The desk six**")
    try:
        from services.paper_desk import fast_price
        for code, name in _SIX:
            px, chg, _t, _s = fast_price(code)
            if px:
                c = f" ({chg:+.2f}%)" if chg is not None else ""
                L.append(f"· {name}: ₩{float(px):,.0f}{c}")
    except Exception as e:
        log.warning(f"briefing six failed: {str(e)[:80]}")

    # 2) holdings + P&L
    try:
        rows = db.execute(_sq(
            "SELECT p.ticker, p.qty, p.avg_price FROM paper_desk_positions p "
            "WHERE p.qty > 0 ORDER BY p.qty * p.avg_price DESC LIMIT 8")).fetchall()
        if rows:
            L.append("")
            L.append("**💼 보유 포지션**" if not en else "**💼 Holdings**")
            from services.paper_desk import fast_price
            from services.stock_resolver import resolve_one
            tot_pnl = 0.0
            for r in rows:
                px, _c, _t, _s = fast_price(r[0])
                nm = None
                try:
                    _rc, nm = resolve_one(str(r[0]))
                except Exception:
                    pass
                nm = nm or r[0]
                if px and r[2]:
                    pnl = (float(px) - float(r[2])) * int(r[1])
                    tot_pnl += pnl
                    pct = (float(px) / float(r[2]) - 1) * 100
                    mark = "🟢" if pnl >= 0 else "🔴"
                    L.append(f"· {mark} {nm} {int(r[1]):,}주 @ ₩{float(r[2]):,.0f} → "
                             f"₩{float(px):,.0f} ({pct:+.1f}%)")
                else:
                    L.append(f"· {nm} {int(r[1]):,}주")
            L.append((f"평가손익 합계: **₩{tot_pnl:,.0f}**" if not en
                      else f"Unrealized P&L: **₩{tot_pnl:,.0f}**"))
        else:
            L.append("")
            L.append("💼 보유 포지션 없음" if not en else "💼 No open positions")
    except Exception as e:
        log.warning(f"briefing holdings failed: {str(e)[:80]}")

    # 3) today's chatbot orders
    try:
        r = db.execute(_sq(
            "SELECT COUNT(*) FILTER (WHERE status='FILLED'), "
            "COUNT(*) FILTER (WHERE status='OPEN') FROM paper_desk_orders "
            "WHERE (COALESCE(source,'') IN ('chat','chatbot') OR COALESCE(source,'') LIKE '%-chat') "
            "AND created_at::date = (now() at time zone 'Asia/Seoul')::date")).fetchone()
        filled, waiting = int(r[0] or 0), int(r[1] or 0)
        if filled or waiting:
            L.append("")
            L.append((f"💬 오늘 챗봇 주문: 체결 {filled}건 · 대기 {waiting}건 — "
                      f"상세는 \"오늘 챗봇으로 뭐 샀지?\"") if not en else
                     (f"💬 Chatbot orders today: {filled} filled · {waiting} waiting — "
                      f"details: \"what did I buy today?\""))
    except Exception:
        pass

    # 4) conditional orders standing
    try:
        from services.chat_conditional import _load as _cond_load
        conds = _cond_load()
        if conds:
            L.append((f"🎯 조건 주문 대기: {len(conds)}건 — \"조건 주문 보여줘\"" if not en
                      else f"🎯 Standing conditional orders: {len(conds)} — \"show conditional orders\""))
    except Exception:
        pass

    # 5) checklist top 3 right now
    top = _top3_now()
    if top:
        L.append("")
        L.append("**🏆 체크리스트 Top 3 (현재)**" if not en
                 else "**🏆 Checklist Top 3 (now)**")
        for i, r in enumerate(top, 1):
            sc = f" — {r['avg']:.0f}{'점' if not en else ''}" if r.get("avg") else ""
            L.append(f"{i}. {r.get('name')}{sc}")
        L.append(("판단이 필요하시면 \"1등 살까?\"라고 물어보세요." if not en
                  else "Want a verdict? Ask \"should I buy the top one?\""))
    return "\n".join(L)
