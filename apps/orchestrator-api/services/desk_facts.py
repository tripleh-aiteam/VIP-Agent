# -*- coding: utf-8 -*-
"""desk_facts — THE CHATBOT READS THE DESK'S OWN BOOKS.

Boss 2026-09-10: "make sure our chatbot must know DB — for example how many we
have a stock, how many holding, how much we already sold out, when what time we
sell or buy like this, and all information which is sitting inside our app."

Measured first, then written. Asked the live desk on 09-10, the chatbot could
already answer holdings and P&L, but:

  "오늘 총 몇 번 거래했어?"    → asked an external agent, which said no data
  "현대차 언제 샀어?"          → answered with a HIGH-PRICE FORECAST
  "what time did we buy …?"    → answered with a Menu 3 gate verdict
  "우리 현금 얼마 남았어?"     → "could not get the cash balance"
  "오늘 알고리즘이 뭐 샀어?"   → a deflection

Every one of those numbers was sitting in paper_desk_orders / _account /
_positions the whole time. Nothing here is inferred or asked of an LLM: each
answer is one SQL query against the desk's own tables, so it cannot be
hallucinated and cannot go stale.

The existing chat lanes read only the CHAT family of orders
(`source IN ('chat','chatbot') OR LIKE '%-chat'`); these read EVERYTHING the
desk did, whoever placed it, because "how many times did we trade today" does
not mean "how many times did I personally type an order".
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text as _t

from services.logger import log

KST = timezone(timedelta(hours=9))


def _now_kst() -> datetime:
    return datetime.now(KST)


def _won(v) -> str:
    try:
        return f"₩{float(v):,.0f}"
    except Exception:
        return "-"


def _hhmm(ts) -> str:
    try:
        return ts.astimezone(KST).strftime("%H:%M")
    except Exception:
        return ""


def _md_hm(ts) -> str:
    try:
        return ts.astimezone(KST).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def _actor(src: Optional[str], en: bool) -> str:
    s = (src or "").lower()
    if s in ("chat", "chatbot") or s.endswith("-chat"):
        return "💬 chatbot" if en else "💬 챗봇"
    if s == "semi":
        return "🖥 Menu 3" if en else "🖥 메뉴3"
    if s.startswith("algo"):
        return f"🤖 {s}"
    if s == "guard":
        return "🛡 guard" if en else "🛡 가드"
    return s or ("manual" if en else "수동")


# ---------------------------------------------------------------------------
# 1. WHEN did we buy / sell a stock
# ---------------------------------------------------------------------------

_WHEN_KO = re.compile(r"(언제|몇\s*시에?|무슨\s*시간에?)\s*.{0,12}?(샀|사들|매수했|매수\s*했|팔았|팔아버|매도했|매도\s*했|처분했)")
_WHEN_EN = re.compile(r"\b(when|what\s+time|at\s+what\s+time)\b.{0,24}\b(did|have)\b.{0,16}\b(buy|bought|sell|sold)\b", re.I)
# a FUTURE question is advice, never a record lookup
_FUTURE = re.compile(r"팔아야|사야|살까|팔까|해야\s*할|should\s+i|when\s+to\s+(buy|sell)|when\s+should", re.I)


def is_when_traded(t: Optional[str]) -> bool:
    s = (t or "").strip()
    if not s or _FUTURE.search(s):
        return False
    return bool(_WHEN_KO.search(s) or _WHEN_EN.search(s))


def when_traded(db, code: str, name: str, transcript: str, en: bool) -> Optional[str]:
    """Every fill of this stock, newest first, with the clock time and who did it."""
    side = None
    if re.search(r"팔았|매도|sold|sell", transcript or "", re.I):
        side = "SELL"
    elif re.search(r"샀|매수|bought|buy", transcript or "", re.I):
        side = "BUY"
    q = ("SELECT side, qty, fill_price, filled_at, COALESCE(source,''), realized_pnl "
         "FROM paper_desk_orders WHERE ticker=:t AND status='FILLED' AND filled_at IS NOT NULL")
    p: dict[str, Any] = {"t": str(code).zfill(6)}
    if side:
        q += " AND side=:s"
        p["s"] = side
    rows = db.execute(_t(q + " ORDER BY filled_at DESC LIMIT 12"), p).fetchall()
    if not rows:
        w = ("" if not side else (" buy" if side == "BUY" else " sell")) if en else \
            ("" if not side else (" 매수" if side == "BUY" else " 매도"))
        return (f"📭 {name} 의{w} 체결 기록이 없습니다 — 이 종목은 아직 거래한 적이 없습니다."
                if not en else
                f"📭 No{w} fills on record for {name} — this desk has never traded it.")
    today = _now_kst().strftime("%Y-%m-%d")
    L = [(f"🕐 **{name} 매매 시각** (최근 {len(rows)}건, 최신순)" if not en else
          f"🕐 **When we traded {name}** (last {len(rows)}, newest first)")]
    for sd, qty, fp, at, src, pnl in rows:
        same_day = str(at.astimezone(KST).date()) == today
        when = _hhmm(at) if same_day else _md_hm(at)
        sd_k = "매수" if sd == "BUY" else "매도"
        pnl_s = ""
        if sd == "SELL" and pnl is not None:
            pnl_s = f" · {'실현' if not en else 'realised'} {_won(pnl)}"
        L.append((f"· {when}  {sd_k} {int(qty or 0):,}주 @ {_won(fp)}{pnl_s} · {_actor(src, en)}"
                  if not en else
                  f"· {when}  {sd} {int(qty or 0):,} sh @ {_won(fp)}{pnl_s} · {_actor(src, en)}"))
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 2. HOW MANY trades today
# ---------------------------------------------------------------------------

_COUNT_KO = re.compile(r"(몇\s*번|몇\s*건|횟수|건수).{0,10}(거래|매매|주문|사고|팔)|"
                       r"(거래|매매|주문).{0,10}(몇\s*번|몇\s*건|횟수|건수)")
_COUNT_EN = re.compile(r"how\s+many\s+(trades?|orders?|times).{0,20}(today|so far)?|"
                       r"(trade|order)\s+count|how\s+many\s+times\s+.{0,12}\b(trade|buy|sell)", re.I)


def is_trade_count(t: Optional[str]) -> bool:
    s = (t or "").strip()
    return bool(s) and bool(_COUNT_KO.search(s) or _COUNT_EN.search(s))


def trade_count(db, en: bool) -> str:
    """Today's activity, whoever placed it — fills by side, plus who did them."""
    day0 = _now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.execute(_t(
        "SELECT side, status, COALESCE(source,''), count(*), COALESCE(sum(qty),0), "
        "       COALESCE(sum(fill_price*qty),0) "
        "FROM paper_desk_orders WHERE created_at >= :d "
        "GROUP BY side, status, COALESCE(source,'')"), {"d": day0}).fetchall()
    if not rows:
        return ("📭 오늘은 아직 주문이 하나도 없습니다." if not en
                else "📭 No orders at all today yet.")
    fills = {"BUY": [0, 0, 0.0], "SELL": [0, 0, 0.0]}
    other: dict[str, int] = {}
    actors: dict[str, int] = {}
    for sd, st, src, n, q, amt in rows:
        n = int(n)
        if st == "FILLED":
            b = fills.get(sd)
            if b:
                b[0] += n
                b[1] += int(q or 0)
                b[2] += float(amt or 0)
            actors[_actor(src, en)] = actors.get(_actor(src, en), 0) + n
        else:
            other[st] = other.get(st, 0) + n
    tot = fills["BUY"][0] + fills["SELL"][0]
    L = [(f"📊 **오늘 거래 {tot}건 체결** (모든 주체 합계 · {_now_kst().strftime('%H:%M')} 기준)"
          if not en else
          f"📊 **{tot} fills today** (everyone, as of {_now_kst().strftime('%H:%M')} KST)")]
    L.append((f"· 매수 {fills['BUY'][0]}건 · {fills['BUY'][1]:,}주 · {_won(fills['BUY'][2])}"
              if not en else
              f"· BUY {fills['BUY'][0]} fills · {fills['BUY'][1]:,} sh · {_won(fills['BUY'][2])}"))
    L.append((f"· 매도 {fills['SELL'][0]}건 · {fills['SELL'][1]:,}주 · {_won(fills['SELL'][2])}"
              if not en else
              f"· SELL {fills['SELL'][0]} fills · {fills['SELL'][1]:,} sh · {_won(fills['SELL'][2])}"))
    if actors:
        who = " · ".join(f"{k} {v}" for k, v in sorted(actors.items(), key=lambda kv: -kv[1]))
        L.append((f"· 주체별: {who}" if not en else f"· by actor: {who}"))
    if other:
        st_s = " · ".join(f"{k} {v}" for k, v in sorted(other.items()))
        L.append((f"· 미체결/기타: {st_s}" if not en else f"· not filled: {st_s}"))
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 3. CASH / account
# ---------------------------------------------------------------------------

# Deliberately NOT 잔고 / 계좌 / 자산 on their own: in a Korean broker app those
# usually mean the HOLDING list, and the portfolio lane already answers that
# better. Only words that can only mean money.
_CASH_KO = re.compile(r"현금|예수금|현금\s*잔고|남은\s*돈")
_CASH_EN = re.compile(r"\bcash\b|buying\s+power|how\s+much\s+money", re.I)


def is_cash_q(t: Optional[str]) -> bool:
    s = (t or "").strip()
    if not s:
        return False
    return bool(_CASH_KO.search(s) or _CASH_EN.search(s))


def cash_reply(db, en: bool) -> Optional[str]:
    r = db.execute(_t("SELECT cash, start_cash FROM paper_desk_account WHERE id=1")).first()
    if not r:
        return None
    cash, start = float(r[0] or 0), float(r[1] or 0)
    pos = db.execute(_t(
        "SELECT COALESCE(count(*),0), COALESCE(sum(qty*avg_price),0) "
        "FROM paper_desk_positions WHERE qty > 0")).first()
    n_pos, cost = int(pos[0] or 0), float(pos[1] or 0)
    equity = cash + cost
    pnl = equity - start if start else 0.0
    pct = (pnl / start * 100) if start else 0.0
    L = [("💰 **모의투자 계좌**" if not en else "💰 **Paper-trading account**")]
    L.append((f"· 현금: **{_won(cash)}**" if not en else f"· Cash: **{_won(cash)}**"))
    L.append((f"· 보유 {n_pos}종목 · 매입원가 {_won(cost)}" if not en else
              f"· {n_pos} position(s) · cost basis {_won(cost)}"))
    L.append((f"· 총자산(현금+원가): {_won(equity)} · 시작 {_won(start)} → **{pnl:+,.0f}원 ({pct:+.2f}%)**"
              if not en else
              f"· Total (cash + cost): {_won(equity)} · started {_won(start)} → **{pnl:+,.0f} ({pct:+.2f}%)**"))
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 4. WHO traded what today (the algos, not just the chat)
# ---------------------------------------------------------------------------

_WHO_KO = re.compile(r"(알고|알고리즘|엔진|봇|메뉴\s*3|가드).{0,12}(뭐|무엇|어떤|샀|팔|매수|매도|거래)")
_WHO_EN = re.compile(r"\b(algo\w*|engine|bot|guard|menu\s*3)\b.{0,20}\b(buy|bought|sell|sold|trade[ds]?)\b", re.I)


def is_who_traded(t: Optional[str]) -> bool:
    s = (t or "").strip()
    return bool(s) and bool(_WHO_KO.search(s) or _WHO_EN.search(s))


def who_traded(db, en: bool) -> str:
    day0 = _now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.execute(_t(
        "SELECT COALESCE(source,''), side, name, qty, fill_price, filled_at "
        "FROM paper_desk_orders WHERE created_at >= :d AND status='FILLED' "
        "ORDER BY filled_at DESC LIMIT 25"), {"d": day0}).fetchall()
    if not rows:
        return ("📭 오늘 체결된 거래가 없습니다." if not en else "📭 Nothing filled today.")
    L = [(f"🤖 **오늘 체결 내역 — 주체별** (최신 {len(rows)}건)" if not en else
          f"🤖 **Today's fills, by actor** (latest {len(rows)})")]
    for src, sd, nm, qty, fp, at in rows:
        sd_k = ("매수" if sd == "BUY" else "매도") if not en else sd
        L.append(f"· {_hhmm(at)}  {_actor(src, en)}  {sd_k} {nm} {int(qty or 0):,}"
                 + ("주" if not en else " sh") + f" @ {_won(fp)}")
    return "\n".join(L)
