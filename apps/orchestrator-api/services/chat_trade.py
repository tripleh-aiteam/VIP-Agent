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
_PENDING_FILE = Path(__file__).resolve().parent.parent / "data" / "chat_trade_pending.json"
_PENDING: dict = {}          # one slot — a single-boss desk
_TTL = 300.0                 # a preview is valid for 5 minutes


def _save_pending() -> None:
    """The pending slot survives orchestrator restarts (boss 2026-08-26: his 'yes'
    to a buy offer fell to the LLM because a deploy restart wiped the offer)."""
    try:
        _PENDING_FILE.write_text(json.dumps(_PENDING), encoding="utf-8")
    except Exception:
        pass


def _load_pending() -> None:
    if _PENDING:
        return
    try:
        d = json.loads(_PENDING_FILE.read_text(encoding="utf-8"))
        if d and time.time() - float(d.get("ts") or 0) <= _TTL:
            _PENDING.update(d)
    except Exception:
        pass


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
    # PRICE first, then blank its span — a 6-digit price ("at 198000") read as a
    # TICKER code and broke the stock resolve (2026-08-26)
    price = None
    pm = (re.search(r"(\d[\d,]{2,})\s*원", tl) or re.search(r"[@₩]\s*(\d[\d,]{2,})", tl)
          or re.search(r"\bat\s+(\d[\d,]{2,})\b", tl))
    if pm:
        try:
            price = float(pm.group(1).replace(",", ""))
        except Exception:
            price = None
    t_res = (transcript[:pm.start()] + " " + transcript[pm.end():]) if pm else transcript
    tl = t_res.lower()
    from services.assistant_agent import _all_stocks_in_query
    stocks = _all_stocks_in_query(t_res)
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
    # "100 stock(s)" counts too (boss 2026-08-26: 'buy Skhynix 100 stock' fell back
    # to the budget default of 5 — fake money must never shrink his number)
    qm = re.search(r"(\d[\d,]*)\s*(?:주|shares?|share|stocks?|개)", tl)
    if not qm:
        qm = re.search(r"\b(\d{1,6})\b\s*$", tl)     # trailing bare number: "buy samsung 10"
    if qm:
        try:
            qty = max(1, int(qm.group(1).replace(",", "")))
        except Exception:
            qty = None
    all_ = any(w in tl for w in ("all", "전량", "전부", "모두", "다 팔", "다팔"))
    # PERCENT sizes (boss 2026-08-26: "can you sell 10% of LG shares?" built a
    # WHOLE-position confirmation — a % must mean a %; 'half/절반' = 50%)
    pct = None
    pm = re.search(r"(\d{1,3})\s*(?:%|퍼센트|프로|percent)", tl)
    if pm:
        try:
            pct = max(1, min(100, int(pm.group(1))))
        except Exception:
            pct = None
    if pct is None and any(w in tl for w in ("half", "절반", "반만", "반 만")):
        pct = 50
    if pct is not None:
        qty = None
    market = "시장가" in tl or "market" in tl
    return {"side": side, "code": code, "name": name, "qty": qty, "all_": all_,
            "pct": pct, "price": price, "market": market}


def _tick(price: float) -> int:
    """KRX tick size for a price."""
    p = float(price or 0)
    if p < 2000: return 1
    if p < 5000: return 5
    if p < 20000: return 10
    if p < 50000: return 50
    if p < 200000: return 100
    if p < 500000: return 500
    return 1000


def _book_offer(code: str, side: str) -> Optional[dict]:
    """The boss's own price-offering method read from the LIVE Kiwoom order book
    (2026-08-26: 'for buying we should be top of the big guy, for selling one row
    down from the big guy'): find the biggest resting wall on our side and queue
    one tick in front of it."""
    try:
        from services.kiwoom_rest import order_book
        ob = order_book(code, ttl=2) or {}
        lvls = [l for l in (ob.get("levels") or []) if l.get("price")]
        mode = "wall"
        if side == "BUY":
            rows = [l for l in lvls if l.get("side") == "bid"]
            if not rows:
                return None
            wall = max(rows, key=lambda l: l.get("qty") or 0)
            limit = wall["price"] + _tick(wall["price"])
            bb = ob.get("best_bid")
            # a wall parked deep below the market queues an order that may never fill
            # (boss 2026-08-26: NAVER waited an hour ₩5,000 under; the hynix order sat
            # 3 ticks below and read as a failed buy) — beyond 3 ticks from the best
            # bid we join the FRONT of the book instead
            if bb and (bb - wall["price"]) > 3 * _tick(bb):
                limit, mode = float(bb), "top"
            ba = ob.get("best_ask")
            if ba and limit >= ba:        # never offer above the ask — that IS the market
                limit = ba
        else:
            rows = [l for l in lvls if l.get("side") == "ask"]
            if not rows:
                return None
            wall = max(rows, key=lambda l: l.get("qty") or 0)
            limit = wall["price"] - _tick(wall["price"])
            ba = ob.get("best_ask")
            if ba and (wall["price"] - ba) > 3 * _tick(ba):
                limit, mode = float(ba), "top"
            bb = ob.get("best_bid")
            if bb and limit <= bb:
                limit = bb
        return {"limit": float(limit), "mode": mode, "wall_price": wall["price"],
                "wall_qty": int(wall.get("qty") or 0),
                "best_bid": ob.get("best_bid"), "best_ask": ob.get("best_ask")}
    except Exception:
        return None


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


def has_cancel_word(t: str) -> bool:
    """'취소' / 'cancel' — typo-tolerant ('cancle' reached the portfolio lane and got
    a holdings lecture, boss 2026-08-26)."""
    if re.search(r"취소|cancel", t):
        return True
    import difflib
    return any(difflib.get_close_matches(w, ("cancel",), n=1, cutoff=0.75)
               for w in re.findall(r"[a-z]{4,9}", t))


def cancel_open(db, transcript: Optional[str], lang: str) -> Optional[str]:
    """'NAVER 주문 취소' / 'cancel my naver order' / 'I want to cancle naver which I
    bought' — cancels the chatbot's OPEN (queued) limit orders. A named stock with a
    matching open order is enough; without a stock, an order word (주문/order/대기)
    cancels all. Bare '취소/no' still belongs to the confirmation flow."""
    t = (transcript or "").lower()
    if not t or not has_cancel_word(t):
        return None
    en = not re.search(r"[가-힣]", transcript or "") and bool(re.search(r"[a-zA-Z]", transcript or ""))
    from sqlalchemy import text as _sqt
    from services.assistant_agent import _all_stocks_in_query
    stocks = _all_stocks_in_query(transcript)
    explicit = any(k in t for k in ("주문", "order", "대기", "waiting", "queue"))
    if not stocks and not explicit:
        return None
    q = ("SELECT id, name, side, qty, limit_price FROM paper_desk_orders "
         "WHERE COALESCE(source,'') IN ('chat','chatbot') AND status='OPEN'")
    params = {}
    if stocks:
        q += " AND ticker=:t"
        params["t"] = stocks[0][0]
    rows = db.execute(_sqt(q), params).fetchall()
    if not rows:
        if not explicit and not stocks:
            return None
        nm = stocks[0][1] if stocks else ""
        return ((f"{nm}에 취소할 대기 주문이 없습니다." if nm else "취소할 대기 주문이 없습니다.")
                if not en else
                (f"No waiting orders on {nm} to cancel." if nm else "No waiting orders to cancel."))
    ids = ",".join(str(int(r[0])) for r in rows)
    db.execute(_sqt(f"UPDATE paper_desk_orders SET status='CANCELLED' WHERE id IN ({ids})"))
    db.commit()
    L = [(f"🚫 대기 주문 {len(rows)}건을 취소했습니다:" if not en
          else f"🚫 Cancelled {len(rows)} waiting order(s):")]
    for r in rows:
        L.append((f"   · {'매수' if r[2] == 'BUY' else '매도'} {r[1]} {int(r[3] or 0):,}주 "
                  f"@ ₩{float(r[4] or 0):,.0f}") if not en else
                 (f"   · {r[2]} {r[1]} {int(r[3] or 0):,} sh @ ₩{float(r[4] or 0):,.0f}"))
    return "\n".join(L)


def stash_offer(code: str, name: str, en: bool) -> None:
    """A BUY verdict's '도와드릴까요?' offer — the next '네' opens the order preview
    (which then needs its own '네' to execute; money keeps its two-step gate)."""
    _PENDING.clear()
    _PENDING.update({"offer": True, "side": "BUY", "code": code, "name": name,
                     "ts": time.time(), "en": en})
    _save_pending()


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
                         cmd["all_"], en, price_asked=cmd.get("price"),
                         market_flag=bool(cmd.get("market")),
                         pct=cmd.get("pct"))


def _make_preview(db, code: str, name: str, side: str, qty_asked: Optional[int],
                  all_: bool, en: bool, price_asked: Optional[float] = None,
                  market_flag: bool = False,
                  pct: Optional[int] = None) -> Optional[str]:
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
        if pct:
            qty = max(1, min(pos, round(pos * pct / 100)))
        elif cmd["all_"]:
            qty = pos
        elif cmd["qty"]:
            qty = min(cmd["qty"], pos)
        else:
            # NO size given → ASK, never default to the whole position (boss
            # 2026-08-26: "it should ask how many % wanna sell or all")
            _PENDING.clear()
            _PENDING.update({"offer": True, "side": "SELL", "code": code,
                             "name": name, "ts": time.time(), "en": en})
            _save_pending()
            return (f"🧮 **{name}** {pos:,}주 보유 중입니다 — 얼마나 팔까요?\n"
                    f"· 예: **\"10%\"** (= {max(1, round(pos * 0.10)):,}주) · \"절반\" · \"30주\" · \"전량\""
                    if not en else
                    f"🧮 You hold {pos:,} shares of **{name}** — how much should I sell?\n"
                    f"· e.g. **\"10%\"** (= {max(1, round(pos * 0.10)):,} sh) · \"half\" · \"30 shares\" · \"all\"")
    else:
        qty = cmd["qty"] or advise_qty(px)
    fee = BUY_COST_PCT if side == "BUY" else SELL_COST_PCT
    # PRICE OFFERING like a real trader (boss 2026-08-26): his own price wins;
    # otherwise the order book proposes one (in front of the biggest wall);
    # '시장가/market' forces an immediate market order
    offer = None
    limit_price = None
    if price_asked:
        limit_price = float(price_asked)
    elif not market_flag:
        offer = _book_offer(code, side)
        if offer:
            limit_price = offer["limit"]
            # PRICE-BASED cap (boss 2026-08-26: "do not add 30 minutes limitation —
            # add price-based limitation, like for skhynix 2000 and others 1000"):
            # the auto offer never queues more than 2 ticks from the live price
            # (2 ticks = ₩2,000 on SK하이닉스, ₩1,000 on NAVER-class — his numbers)
            _cap = 2 * _tick(px)
            if side == "BUY" and px - limit_price > _cap:
                limit_price = px - _cap
                offer["mode"] = "cap"
                offer["cap"] = _cap
            elif side == "SELL" and limit_price - px > _cap:
                limit_price = px + _cap
                offer["mode"] = "cap"
                offer["cap"] = _cap
    order_type = "limit" if limit_price else "market"
    total = (limit_price or px) * qty
    _PENDING.clear()
    _PENDING.update({"code": code, "name": name, "side": side, "qty": qty,
                     "px": px, "ts": time.time(), "en": en,
                     "order_type": order_type, "limit_price": limit_price})
    _save_pending()
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
        if price_asked:
            L.append(f"· Order: **LIMIT ₩{limit_price:,.0f}** (your price) — waits in the book until touched")
        elif offer and offer.get("mode") == "cap":
            L.append(f"· Order: **LIMIT ₩{limit_price:,.0f}** — price-cap rule: we never queue "
                     f"more than ₩{offer.get('cap', 0):,.0f} from the live price, so the order "
                     f"stays close enough to actually fill · say 'market' for instant fill")
        elif offer and offer.get("mode") == "top":
            L.append(f"· Order: **LIMIT ₩{limit_price:,.0f}** — front of the book "
                     f"(the best {'bid' if side == 'BUY' else 'ask'}): the big wall is too far "
                     f"from the market to wait for, so we queue first in line at the top price · "
                     f"say 'market' for instant fill")
        elif offer:
            L.append(f"· Order: **LIMIT ₩{limit_price:,.0f}** — my offer from the live order book: "
                     f"the biggest {'bid' if side == 'BUY' else 'ask'} wall sits at "
                     f"₩{offer['wall_price']:,.0f} ({offer['wall_qty']:,} sh), we queue "
                     f"{'one tick in front of it' if side == 'BUY' else 'one tick below it'} · "
                     f"say 'market' for instant fill")
        else:
            L.append("· Order: **MARKET** — fills instantly at the live price")
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
        if price_asked:
            L.append(f"· 주문: **지정가 ₩{limit_price:,.0f}** (직접 제시하신 가격) — 가격이 닿을 때까지 호가창에서 대기합니다")
        elif offer and offer.get("mode") == "cap":
            L.append(f"· 주문: **지정가 ₩{limit_price:,.0f}** — 가격 제한 규칙: 현재가에서 "
                     f"₩{offer.get('cap', 0):,.0f} 이상 떨어진 곳에는 줄을 서지 않습니다 "
                     f"(체결될 수 있는 거리 유지) · 바로 {'사려면' if side == 'BUY' else '팔려면'} '시장가'라고 말씀하세요")
        elif offer and offer.get("mode") == "top":
            L.append(f"· 주문: **지정가 ₩{limit_price:,.0f}** — 호가 1순위"
                     f"({'최우선 매수호가' if side == 'BUY' else '최우선 매도호가'})에 줄을 섭니다: "
                     f"큰 벽이 시장에서 너무 멀어 기다리기 아까운 자리라, 맨 앞줄에 섭니다 · "
                     f"바로 {'사려면' if side == 'BUY' else '팔려면'} '시장가'라고 말씀하세요")
        elif offer:
            L.append(f"· 주문: **지정가 ₩{limit_price:,.0f}** — 호가창을 보고 제가 제안하는 가격입니다: "
                     f"제일 큰 {'매수벽' if side == 'BUY' else '매도벽'}이 ₩{offer['wall_price']:,.0f}에 "
                     f"{offer['wall_qty']:,}주 대기 중이라 {'그 바로 위 한 틱에 줄을 섭니다' if side == 'BUY' else '그 바로 아래 한 틱에 줄을 섭니다'} · "
                     f"바로 사려면 '시장가'라고 말씀하세요")
        else:
            L.append("· 주문: **시장가** — 지금 가격에 바로 체결됩니다")
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
                  "좋아", "해줘", "진행", "진행해", "네실행", "예스",
                  # "yes please buy" — the boss's own phrasing when the offer stood
                  "yesplease", "yesbuy", "yespleasebuy", "pleasebuy", "yessell",
                  "네사줘", "네매수", "산다", "사자"))
_NO = frozenset(("no", "n", "cancel", "stop", "dont", "don't", "아니", "아니요", "아니오",
                 "취소", "취소해", "안해", "안 해", "하지마", "하지 마", "노"))


def qty_reply(db, transcript: Optional[str]) -> Optional[str]:
    """'15 shares please' / '15주' / '15' while an offer or preview stands — the boss
    is choosing the SIZE (2026-08-26: his '15 shares please' fell to the LLM). Builds
    a fresh confirmation at that size for the pending stock/side."""
    _load_pending()
    if not _PENDING or time.time() - _PENDING.get("ts", 0) > _TTL:
        return None
    t = (transcript or "").strip().lower()
    p = dict(_PENDING)
    # percent / half / all replies while the size question stands
    pm = re.fullmatch(r"(\d{1,3})\s*(?:%|퍼센트|프로|percent)\s*"
                      r"(?:please|주세요|요|로|팔아|매도)?[.! ]*", t)
    if pm or t in ("절반", "반만", "half") or t in ("전량", "전부", "all", "다"):
        return _make_preview(db, p["code"], p.get("name") or p["code"],
                             p.get("side") or "BUY", None,
                             t in ("전량", "전부", "all", "다"), bool(p.get("en")),
                             pct=(50 if (not pm and t in ("절반", "반만", "half"))
                                  else (max(1, min(100, int(pm.group(1)))) if pm else None)))
    m = re.fullmatch(r"(\d[\d,]*)\s*(?:주|shares?|share|stocks?|개)?\s*"
                     r"(?:please|주세요|요|로|사줘|매수|매도)?[.! ]*", t)
    if not m:
        return None
    try:
        qty = max(1, int(m.group(1).replace(",", "")))
    except Exception:
        return None
    return _make_preview(db, p["code"], p.get("name") or p["code"],
                         p.get("side") or "BUY", qty, False, bool(p.get("en")))


def confirm_check(transcript: Optional[str]) -> Optional[str]:
    """'yes'/'no' when the message answers a FRESH pending order, else None."""
    _load_pending()
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
    _load_pending()
    if not _PENDING:
        return None
    p = dict(_PENDING)
    _PENDING.clear()
    _save_pending()
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
    _ot = p.get("order_type") or "market"
    res = place_order(db, p["code"], p["side"], int(p["qty"]), order_type=_ot,
                      limit_price=p.get("limit_price"), source="chatbot",
                      ref_price=p.get("px"), direct=True)
    if _ot == "limit" and res.get("ok") and res.get("status") == "OPEN":
        # queued in the book — the trading loop fills it when the price touches
        lp = p.get("limit_price") or 0
        side_word = "매수" if p["side"] == "BUY" else "매도"
        _gap = ""
        try:
            _pxn = float(p.get("px") or 0)
            if _pxn and p["side"] == "BUY" and _pxn > lp:
                _gap = (f" (now ₩{_pxn:,.0f} — needs a ₩{_pxn - lp:,.0f} dip to fill)" if en
                        else f" (현재가 ₩{_pxn:,.0f} — ₩{_pxn - lp:,.0f} 내려오면 체결)")
            elif _pxn and p["side"] == "SELL" and _pxn < lp:
                _gap = (f" (now ₩{_pxn:,.0f} — needs a ₩{lp - _pxn:,.0f} rise to fill)" if en
                        else f" (현재가 ₩{_pxn:,.0f} — ₩{lp - _pxn:,.0f} 올라오면 체결)")
        except Exception:
            pass
        L = ([f"🕐 **Order queued — {p['side']} {p['name']} {p['qty']:,} shares, LIMIT ₩{lp:,.0f}**",
              "It is now waiting in the book and fills AUTOMATICALLY the moment the price touches "
              f"₩{lp:,.0f}{_gap}. I'll record it as a 💬 chatbot order when it fills. "
              f"Cancel anytime: \"cancel {p['name']} order\"."]
             if en else
             [f"🕐 **대기 주문 접수 — {side_word} {p['name']} {p['qty']:,}주 · 지정가 ₩{lp:,.0f}**",
              f"호가창에 줄을 섰습니다. 가격이 ₩{lp:,.0f}에 닿는 순간 자동으로 체결되고 "
              f"💬 챗봇 주문으로 기록됩니다{_gap}. 취소는 \"{p['name']} 주문 취소\"라고 말씀하세요."])
        L += ["", _desk_links(en)]
        return "\n".join(L)
    if not res.get("ok"):
        err = res.get("error") or "unknown"
        log.warning(f"chat_trade order failed: {err}")
        return (f"⚠️ 주문 실패: {err}" if not en else f"⚠️ Order failed: {err}")
    fill = res.get("fill_price") or res.get("live_price") or p.get("px")
    pos = _position_qty(db, p["code"])
    L = []
    if en:
        L.append(f"✅ **Filled — {p['side']} {p['name']} {p['qty']:,} shares @ ₩{fill:,.0f}** "
                 f"(total ~₩{fill * p['qty']:,.0f})")
        if res.get("realized_pnl") is not None:
            L.append(f"💰 Realized P&L: ₩{res['realized_pnl']:,.0f} ({res.get('realized_pnl_pct', 0):+.2f}%)")
        L.append(f"📒 Position now: {pos:,} shares · recorded as a 💬 chatbot order in the desk history.")
        L.append("")
        L.append(_desk_links(True))
    else:
        L.append(f"✅ **체결 — {side_ko} {p['name']} {p['qty']:,}주 @ ₩{fill:,.0f}** "
                 f"(총 ~₩{fill * p['qty']:,.0f})")
        if res.get("realized_pnl") is not None:
            L.append(f"💰 실현 손익: ₩{res['realized_pnl']:,.0f} ({res.get('realized_pnl_pct', 0):+.2f}%)")
        L.append(f"📒 현재 보유: {pos:,}주 · 데스크 기록에 💬 챗봇(chatbot) 주문으로 남았습니다.")
        L.append("")
        L.append(_desk_links(False))
    return "\n".join(L)


# the boss's pinned six — they trade (and are verified) on the Live Kiwoom Desk
_SIX = frozenset(("000660", "005930", "035420", "017670", "042660", "034020"))


def _desk_links(en: bool) -> str:
    """BOTH menus, always (boss 2026-08-26: 'it is offering after buying only menu 2 —
    it should offer both')."""
    if en:
        return ("[📡 Menu 1 — Live Kiwoom Desk](nav:/testing/live) · "
                "[📡 Menu 2 — Checklist Reco Desk](nav:/testing/reco)")
    return ("[📡 메뉴1 — Live Kiwoom Desk](nav:/testing/live) · "
            "[📡 메뉴2 — 체크리스트 추천 데스크](nav:/testing/reco)")
