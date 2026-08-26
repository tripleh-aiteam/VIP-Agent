"""chat_trade — the chatbot's own order desk (boss 2026-08-25: "I will ask what stock
can I buy... then if we say BUY SAMSUNG ELECTRONICS is it possible?").

Two turns, zero LLM:
  1. An imperative BUY/SELL command naming a stock ("buy samsung electronics",
     "삼성전자 10주 매수") builds a PENDING order preview — name, quantity, live
     price, total cost — and asks for the word.
  2. The boss's next "네 / yes" executes it through the SAME place_order chokepoint
     both desks trade through, stamped source='chat' (🧑 human) — so it lands in the
     same positions and trading history, bypassing the algo laws exactly like the
     semi-auto approve click does. "아니요 / no" cancels. Pending orders expire in
     5 minutes.

Quantity: explicit ("10주") wins; a bare BUY defaults to the advised size —
budget ÷ price (budget in data/chat_trade_budget.json, default ₩10,000,000);
a bare SELL defaults to the whole position.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from services.logger import log

_BUDGET_FILE = Path(__file__).resolve().parent.parent / "data" / "chat_trade_budget.json"
_PENDING: dict = {}          # one slot — a single-boss desk
_TTL = 300.0                 # a preview is valid for 5 minutes


def budget() -> int:
    try:
        return int(json.loads(_BUDGET_FILE.read_text(encoding="utf-8")).get("krw") or 10_000_000)
    except Exception:
        return 10_000_000


def advise_qty(price) -> int:
    """The advised share count for one chat order: budget ÷ price, at least 1."""
    try:
        return max(1, int(budget() // float(price)))
    except Exception:
        return 1


_CMD_EN = re.compile(r"^\s*(?:please\s+|pls\s+|now\s+|then\s+|ok\s+|and\s+)*(buy|sell)\b", re.I)
# "I wanna buy X" / "can you buy X for me" are ORDERS too (boss 2026-08-26: "if we say
# Please buy or I wanna buy... it should not buy automatically, must ask one more time")
_CMD_EN2 = re.compile(r"\b(?:i\s+wanna|i\s+want\s+to|i'?d\s+like\s+to|i\s+would\s+like\s+to"
                      r"|can\s+you|could\s+you|please)\s+(buy|sell)\b", re.I)
_KO_BUY = ("사줘", "사 줘", "사자", "매수해", "매수 해", "매수해줘", "매수하자", "매수",
           "사고 싶", "사고싶", "매수하고 싶")
_KO_SELL = ("팔아줘", "팔아 줘", "팔아", "팔자", "매도해", "매도해줘", "매도하자", "매도",
            "전량매도", "팔고 싶", "팔고싶", "매도하고 싶")
# question/advice phrasings are NEVER a command ("매수해도 돼?", "should I buy...")
_ADVICE_BLOCK = ("should", "할까", "살까", "팔까", "괜찮", "어때", "can i", "may i", "could i",
                 "worth", "좋을까", "어떨까", "할지", "해도", "될까", "돼?", "돼요", "됩니까",
                 "why", "왜", "언제", "when", "how much should", "which", "what",
                 # "I wanna buy X — do you think it's good?" is an OPINION ask, not an
                 # order (boss 2026-08-26 dialog)
                 "do you think", "you think", " think", "is it good", "good time",
                 "good idea", "good right now", "opinion")


def parse(transcript: Optional[str]) -> Optional[dict]:
    """An imperative BUY/SELL command naming a stock → {side, code, name, qty, all_}.
    None for questions/advice or when no stock resolves."""
    t = (transcript or "").strip()
    tl = t.lower()
    if not t or len(t) > 120 or any(w in tl for w in _ADVICE_BLOCK):
        return None
    side = None
    m = _CMD_EN.match(tl) or _CMD_EN2.search(tl)
    if m:
        side = "BUY" if m.group(1).lower() == "buy" else "SELL"
    elif any(k in t for k in _KO_SELL):
        side = "SELL"
    elif any(k in t for k in _KO_BUY):
        side = "BUY"
    if not side:
        return None
    from services.assistant_agent import _all_stocks_in_query
    stocks = _all_stocks_in_query(transcript)
    if len(stocks) != 1:
        # hard-typo fallback ("BUY SASMCUNG ELECTROCNICS" resolved to 삼성전자 AND a
        # phantom LG전자): inside an explicit BUY/SELL command the WHOLE remainder can
        # be fuzzy-matched to a single name — the confirmation step catches any wrong
        # resolve before money moves
        import difflib
        from services import stock_resolver as _sr
        _sr._build()
        rem = re.sub(r"^\s*(?:please|pls|now|then|ok|and|buy|sell)\b", "", tl).strip()
        rem = re.sub(r"\d[\d,]*\s*(?:주|shares?|share|개)?|[.,!?]", "", rem).strip()
        if len(rem) >= 4:
            m2 = difflib.get_close_matches(rem, list(_sr._ALIAS.keys()), n=1, cutoff=0.72)
            if m2:
                c2 = _sr._ALIAS[m2[0]]
                stocks = [(c2, _sr.display_name(c2))]
    if len(stocks) != 1:          # one order = one stock; ambiguity is not an order
        return None
    code, name = stocks[0]
    qty = None
    qm = re.search(r"(\d[\d,]*)\s*(?:주|shares?|share|개)", tl)
    if not qm:
        qm = re.search(r"\b(\d{1,6})\b\s*$", tl)     # trailing bare number: "buy samsung 10"
    if qm:
        try:
            qty = max(1, int(qm.group(1).replace(",", "")))
        except Exception:
            qty = None
    all_ = any(w in tl for w in ("all", "전량", "전부", "모두", "다 팔", "다팔"))
    return {"side": side, "code": code, "name": name, "qty": qty, "all_": all_}


def _position_qty(db, code: str) -> int:
    try:
        from sqlalchemy import text
        q = db.execute(text("SELECT qty FROM paper_desk_positions WHERE ticker=:t"),
                       {"t": code}).scalar()
        return int(q or 0)
    except Exception:
        return 0


def _next_open_kst():
    """The nearest KRX opening moment (09:00 KST on the next trading weekday)."""
    from datetime import datetime, timedelta, timezone
    KST = timezone(timedelta(hours=9))
    n = datetime.now(KST)
    if n.weekday() < 5 and (n.hour, n.minute) < (9, 0):
        d = n
    else:
        d = n + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return d.replace(hour=9, minute=0, second=0, microsecond=0), n


_WD_KO = "월화수목금토일"
_WD_EN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def closed_reply(side: str, en: bool) -> str:
    """The polite off-hours refusal with the nearest opening time (boss 2026-08-26:
    'if I ask outside of market hours it should say I could not buy/sell, I could
    help when market is open, then tell nearest opening time')."""
    nxt, now = _next_open_kst()
    hrs = (nxt - now).total_seconds() / 3600
    wait = (f"약 {hrs:.0f}시간 후" if hrs >= 1.5 else f"약 {hrs * 60:.0f}분 후")
    wait_en = (f"in about {hrs:.0f} hours" if hrs >= 1.5 else f"in about {hrs * 60:.0f} minutes")
    act = "매수/매도" if side == "BOTH" else ("매수" if side == "BUY" else "매도")
    act_en = "buy or sell" if side == "BOTH" else side.lower()
    if en:
        return (f"🌙 The market is closed right now, so I can't {act_en} for you — I can help "
                f"as soon as it opens.\n⏰ Nearest opening: **{_WD_EN[nxt.weekday()]} "
                f"{nxt.month}/{nxt.day} 09:00 KST** ({wait_en}). KRX trades 09:00–15:30, Mon–Fri.")
    return (f"🌙 지금은 장외 시간이라 {act}를 도와드릴 수 없습니다 — 장이 열리면 바로 도와드릴게요.\n"
            f"⏰ 가장 가까운 개장: **{nxt.month}월 {nxt.day}일({_WD_KO[nxt.weekday()]}) 09:00 KST** "
            f"({wait}). KRX 정규장은 평일 09:00~15:30입니다.")


def stash_offer(code: str, name: str, en: bool) -> None:
    """A BUY verdict's '도와드릴까요?' offer — the next '네' opens the order preview
    (which then needs its own '네' to execute; money keeps its two-step gate)."""
    _PENDING.clear()
    _PENDING.update({"offer": True, "code": code, "name": name, "ts": time.time(), "en": en})


def build_preview(db, transcript: Optional[str], lang: str) -> Optional[str]:
    cmd = parse(transcript)
    if not cmd:
        return None
    # ANY Hangul in the command → Korean preview ("buy lg 1주" once got the EN preview,
    # which the language guard then LLM-translated into '구매/귀하가' — order text with
    # money numbers must never ride through a translator)
    if re.search(r"[가-힣]", transcript or ""):
        en = False
    else:
        en = str(lang or "").lower().startswith("en") or bool(re.search(r"[a-zA-Z]", transcript or ""))
    return _make_preview(db, cmd["code"], cmd["name"], cmd["side"], cmd["qty"],
                         cmd["all_"], en)


def _make_preview(db, code: str, name: str, side: str, qty_asked: Optional[int],
                  all_: bool, en: bool) -> Optional[str]:
    # market-hours gate FIRST — no order form outside the session
    try:
        from services.kiwoom_tape import market_open
        if not market_open():
            _PENDING.clear()
            return closed_reply(side, en)
    except Exception:
        pass
    cmd = {"code": code, "name": name, "side": side, "qty": qty_asked, "all_": all_}
    from services.paper_desk import BUY_COST_PCT, SELL_COST_PCT, _live_price
    px, kw_name = _live_price(code)
    if px is None:
        return ("⚠️ 현재가를 가져올 수 없어 주문을 만들 수 없습니다 — 종목 코드를 확인해 주세요."
                if not en else
                "⚠️ No live price available for that stock — cannot build the order.")
    pos = _position_qty(db, code)
    if side == "SELL":
        if pos <= 0:
            return (f"⚠️ **{name}** 보유 수량이 없습니다 — 팔 것이 없어 주문을 만들지 않았습니다."
                    if not en else
                    f"⚠️ We hold no **{name}** — nothing to sell, so no order was created.")
        qty = pos if (cmd["all_"] or not cmd["qty"]) else min(cmd["qty"], pos)
    else:
        qty = cmd["qty"] or advise_qty(px)
    fee = BUY_COST_PCT if side == "BUY" else SELL_COST_PCT
    total = px * qty
    _PENDING.clear()
    _PENDING.update({"code": code, "name": name, "side": side, "qty": qty,
                     "px": px, "ts": time.time(), "en": en})
    b = budget()
    qty_note_ko = (f"직접 지정" if cmd["qty"] else
                   (f"보유 전량" if side == "SELL" else f"예산 ₩{b:,.0f} 기준 자동"))
    qty_note_en = ("as you specified" if cmd["qty"] else
                   ("the whole position" if side == "SELL" else f"auto from the ₩{b:,.0f} budget"))
    # the SCORE in the confirmation (boss 2026-08-26: "must ask one more time, like do
    # you really wanna buy, like score like this, then after final approve")
    score_ko = score_en = None
    warn_ko = warn_en = None
    try:
        from services.checklist_reco import _ranking, _year_zone
        row = next((r for r in (_ranking() or {}).get("rows", []) if r.get("code") == code), None)
        zz = _year_zone(code)
        pk, pe = [], []
        if row and row.get("score") is not None:
            pk.append(f"체크리스트 점수 {row['score']}점")
            pe.append(f"checklist score {row['score']}")
        if zz:
            _zl = {"buy": ("🟢 매수구간", "🟢 buying zone"), "sell": ("🔴 매도구간", "🔴 selling zone"),
                   "mid": ("중간 구간", "mid-range")}[zz["zone"]]
            pk.append(f"연중 {zz['pos']}% ({_zl[0]})")
            pe.append(f"{zz['pos']}% of year ({_zl[1]})")
            if side == "BUY" and zz["zone"] == "sell":
                warn_ko = "⚠️ 지금은 매도구간(연중 ≥85%)입니다 — 체크리스트 법칙은 여기서 신규 매수를 권하지 않습니다."
                warn_en = "⚠️ It sits in the SELLING zone (≥85% of year) — the checklist law advises against new buys here."
            if side == "SELL" and zz["zone"] == "buy":
                warn_ko = "⚠️ 지금은 매수구간(연중 ≤15%)입니다 — 법칙상 바닥권에서는 팔지 않습니다."
                warn_en = "⚠️ It sits in the BUYING zone (≤15% of year) — the law never sells the bottom."
        score_ko = " · ".join(pk) if pk else None
        score_en = " · ".join(pe) if pe else None
    except Exception:
        pass
    side_ko = "매수" if side == "BUY" else "매도"
    if en:
        L = [f"🧾 **Order confirmation — {side} {name} ({code})**",
             f"· Quantity: **{qty:,} shares** ({qty_note_en})"
             + (f" — e.g. say '{name} 10 shares {side.lower()}' to change" if not cmd["qty"] else ""),
             f"· Live price: ₩{px:,.0f} → total ~₩{total:,.0f} (fee {fee}%)"]
        if side == "SELL":
            L.append(f"· Position: {pos:,} shares held")
        if score_en:
            L.append(f"· {score_en}")
        if warn_en:
            L.append(warn_en)
        L += ["", f"**Do you really want to {side.lower()}?** Reply **yes** to execute · "
              "**no** to cancel (valid 5 min). Fills as a 💬 chatbot order on the paper "
              "desk at the real live price."]
    else:
        L = [f"🧾 **주문 확인 — {side_ko} {name} ({code})**",
             f"· 수량: **{qty:,}주** ({qty_note_ko})"
             + (f" — 바꾸려면 '{name} 10주 {side_ko}'처럼 말씀하세요" if not cmd["qty"] else ""),
             f"· 현재가: ₩{px:,.0f} → 예상 금액 ~₩{total:,.0f} (수수료 {fee}%)"]
        if side == "SELL":
            L.append(f"· 보유: {pos:,}주")
        if score_ko:
            L.append(f"· {score_ko}")
        if warn_ko:
            L.append(warn_ko)
        L += ["", f"**정말 {side_ko}할까요?** 실행하려면 **네**, 취소는 **아니요** 라고 답해 주세요 "
              "(5분간 유효). 실제 실시간 가격으로 페이퍼 데스크에 💬 챗봇(chatbot) 주문으로 기록됩니다."]
    return "\n".join(L)


_YES = frozenset(("yes", "y", "confirm", "ok", "okay", "go", "execute", "do it", "proceed",
                  "네", "예", "응", "그래", "실행", "실행해", "확인", "오케이", "ㅇㅋ",
                  "좋아", "해줘", "진행", "진행해", "네실행", "예스"))
_NO = frozenset(("no", "n", "cancel", "stop", "dont", "don't", "아니", "아니요", "아니오",
                 "취소", "취소해", "안해", "안 해", "하지마", "하지 마", "노"))


def confirm_check(transcript: Optional[str]) -> Optional[str]:
    """'yes'/'no' when the message answers a FRESH pending order, else None."""
    if not _PENDING or time.time() - _PENDING.get("ts", 0) > _TTL:
        return None
    t = re.sub(r"[\s.,!?~^]+", "", (transcript or "").lower())
    if not t or len(t) > 12:
        return None
    if t in _YES:
        return "yes"
    if t in _NO:
        return "no"
    return None


def finish(db, word: str) -> Optional[str]:
    """Execute or cancel the pending order. Clears the slot either way."""
    if not _PENDING:
        return None
    p = dict(_PENDING)
    _PENDING.clear()
    en = bool(p.get("en"))
    # the advice lane's OFFER ("매수 도와드릴까요?"): "네" opens the real order
    # preview (a fresh pending), "아니요" just drops it — nothing was ordered yet
    if p.get("offer"):
        if word == "no":
            return ("알겠습니다 — 주문 없이 두겠습니다. 언제든 \"{n} 매수\"라고 말씀하세요."
                    .format(n=p["name"]) if not en else
                    f"Understood — no order placed. Say \"buy {p['name']}\" anytime.")
        return _make_preview(db, p["code"], p["name"], "BUY", None, False, en)
    side_ko = "매수" if p["side"] == "BUY" else "매도"
    if word == "no":
        return (f"🚫 취소했습니다 — {side_ko} {p['name']} {p['qty']:,}주 주문은 실행되지 않았습니다."
                if not en else
                f"🚫 Cancelled — the {p['side']} {p['name']} {p['qty']:,}-share order was NOT executed.")
    # the market may have closed between the preview and the "네"
    try:
        from services.kiwoom_tape import market_open
        if not market_open():
            return closed_reply(p["side"], en)
    except Exception:
        pass
    from services.paper_desk import place_order
    res = place_order(db, p["code"], p["side"], int(p["qty"]), order_type="market",
                      source="chatbot", ref_price=p.get("px"), direct=True)
    if not res.get("ok"):
        err = res.get("error") or "unknown"
        log.warning(f"chat_trade order failed: {err}")
        return (f"⚠️ 주문 실패: {err}" if not en else f"⚠️ Order failed: {err}")
    fill = res.get("fill_price") or res.get("live_price") or p.get("px")
    pos = _position_qty(db, p["code"])
    # VERIFY button (boss 2026-08-25, right after his first live chat order: "below
    # you should put button then I can go to the second menu and see is it actually
    # bought or not") — the six live on the Live Kiwoom Desk, everything else on the
    # Checklist Reco Desk
    _six = p["code"] in _SIX
    _dest = "/testing/live" if _six else "/testing/reco"
    _dest_ko = "Live Kiwoom Desk" if _six else "체크리스트 추천 데스크"
    _dest_en = "Live Kiwoom Desk" if _six else "Checklist Reco Desk"
    L = []
    if en:
        L.append(f"✅ **Filled — {p['side']} {p['name']} {p['qty']:,} shares @ ₩{fill:,.0f}** "
                 f"(total ~₩{fill * p['qty']:,.0f})")
        if res.get("realized_pnl") is not None:
            L.append(f"💰 Realized P&L: ₩{res['realized_pnl']:,.0f} ({res.get('realized_pnl_pct', 0):+.2f}%)")
        L.append(f"📒 Position now: {pos:,} shares · recorded as a 💬 chatbot order in the desk history.")
        L.append("")
        L.append(f"[📡 Verify it on the desk → {_dest_en}](nav:{_dest})")
    else:
        L.append(f"✅ **체결 — {side_ko} {p['name']} {p['qty']:,}주 @ ₩{fill:,.0f}** "
                 f"(총 ~₩{fill * p['qty']:,.0f})")
        if res.get("realized_pnl") is not None:
            L.append(f"💰 실현 손익: ₩{res['realized_pnl']:,.0f} ({res.get('realized_pnl_pct', 0):+.2f}%)")
        L.append(f"📒 현재 보유: {pos:,}주 · 데스크 기록에 💬 챗봇(chatbot) 주문으로 남았습니다.")
        L.append("")
        L.append(f"[📡 실제로 샀는지 확인하기 → {_dest_ko}](nav:{_dest})")
    return "\n".join(L)


# the boss's pinned six — they trade (and are verified) on the Live Kiwoom Desk
_SIX = frozenset(("000660", "005930", "035420", "017670", "042660", "034020"))
