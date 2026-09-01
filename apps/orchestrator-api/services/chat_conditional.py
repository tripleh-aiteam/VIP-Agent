# -*- coding: utf-8 -*-
"""chat_conditional — Step 3 of the boss's 4-step plan (2026-08-27): standing
conditional orders by chat. "삼성전자 260,000원 되면 사줘" / "if skhynix drops to
1,500,000 buy 5" → confirmed once ("네") → stored → the watchdog checks it on
every poll and fires a market order the moment the price crosses. The trigger IS
the boss's pre-authorization (he confirmed at set-time), and the fill announces
itself as a 🎯 alert plus the normal ✅ fill alert.

Rules survive restarts (data/chat_conditional.json). List: "조건 주문 보여줘".
Cancel: "조건 주문 취소" (all) or with a stock name (that one).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from services.logger import log

_FILE = Path(__file__).resolve().parent.parent / "data" / "chat_conditional.json"
_MAX_RULES = 10


def _load() -> list[dict]:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rules: list[dict]) -> None:
    try:
        _FILE.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


_COND_CUE = re.compile(
    r"되면|가면|닿으면|도달하면|찍으면|떨어지면|내려오면|오르면|올라가면|넘으면|이하면|이상이면|이하로|이상으로"
    r"|\bif\b|\bwhen\b|\breaches\b|\bhits\b|\bdrops?\s+to\b|\bfalls?\s+to\b|\bgoes?\s+(?:up|down)\s+to\b"
    r"|\btouches\b|\bbreaks\b|\bcrosses\b", re.IGNORECASE)
_PRICE_RE = re.compile(r"([\d,]{2,})\s*(?:원|won|₩)?", re.IGNORECASE)
_BUY_CUE = re.compile(r"사줘|사자|사\s*줘|매수|사라|\bbuy\b|사고\s*싶|사기", re.IGNORECASE)
_SELL_CUE = re.compile(r"팔아|팔자|매도|팔아줘|\bsell\b", re.IGNORECASE)
_QTY_RE = re.compile(r"(\d+)\s*(?:주|shares?|stocks?|개)", re.IGNORECASE)
_LIST_CUE = re.compile(r"조건\s*주문|conditional\s*orders?|예약\s*주문|standing\s*orders?",
                       re.IGNORECASE)


def is_conditional(transcript: Optional[str]) -> bool:
    """A trading phrase carrying an if/when trigger."""
    t = transcript or ""
    return bool(_COND_CUE.search(t) and (_BUY_CUE.search(t) or _SELL_CUE.search(t))
                and _PRICE_RE.search(t.replace(",", "")))


def is_list_q(transcript: Optional[str]) -> bool:
    t = transcript or ""
    if not _LIST_CUE.search(t):
        return False
    return bool(re.search(r"보여|목록|리스트|뭐|몇|있|show|list|any|what", t, re.IGNORECASE))


def is_cancel_q(transcript: Optional[str]) -> bool:
    t = transcript or ""
    return bool(_LIST_CUE.search(t)
                and re.search(r"취소|삭제|지워|없애|cancel|remove|delete|clear", t, re.IGNORECASE))


def parse(db, transcript: str) -> Optional[dict]:
    """→ {code, name, side, qty, trigger, direction} or None."""
    try:
        from services.stock_resolver import resolve_one
        code, name = resolve_one(transcript)
    except Exception:
        code = name = None
    if not code:
        return None
    side = "SELL" if (_SELL_CUE.search(transcript) and not _BUY_CUE.search(transcript)) else "BUY"
    # the trigger price = the largest plausible number that isn't the qty
    nums = [int(m.replace(",", "")) for m in
            re.findall(r"[\d,]{2,}", transcript) if int(m.replace(",", "")) >= 100]
    if not nums:
        return None
    trigger = float(max(nums))
    qm = _QTY_RE.search(transcript)
    qty = int(qm.group(1)) if qm else None
    if qty and qty == trigger:
        qty = None
    try:
        from services.paper_desk import fast_price
        px, _c, _t, _s = fast_price(code)
    except Exception:
        px = None
    if not px:
        return None
    if abs(trigger - float(px)) / float(px) > 0.30:
        return None                       # a 30%+ far trigger is probably a mis-parse
    direction = "down" if trigger < float(px) else "up"
    return {"code": code, "name": name or code, "side": side, "qty": qty,
            "trigger": trigger, "direction": direction, "px_now": float(px)}


def make_preview(db, transcript: str, lang: str) -> Optional[str]:
    """Build the set-confirmation and stash it as the pending '네' target."""
    cmd = parse(db, transcript)
    if not cmd:
        return None
    from services.chat_trade import text_lang_en
    en = text_lang_en(transcript, lang)
    if not cmd["qty"]:
        try:
            from services.chat_trade import advise_qty
            cmd["qty"] = advise_qty(cmd["trigger"])
        except Exception:
            cmd["qty"] = 1
    # ride the chat_trade pending slot so the ordinary "네/아니요" flow answers it
    try:
        from services import chat_trade as _ct
        _ct._load_pending()
        _ct._PENDING.clear()
        _ct._PENDING.update({"cond": True, "ts": time.time(), "en": en, **cmd})
        _ct._save_pending()
    except Exception as e:
        log.warning(f"chat_conditional stash failed: {str(e)[:100]}")
        return None
    side_ko = "매수" if cmd["side"] == "BUY" else "매도"
    arrow = ("내려오면" if cmd["direction"] == "down" else "올라가면")
    arrow_en = ("falls to" if cmd["direction"] == "down" else "rises to")
    if en:
        return (f"🎯 **Conditional order — confirm?**\n"
                f"· When **{cmd['name']}** {arrow_en} **₩{cmd['trigger']:,.0f}** "
                f"(now ₩{cmd['px_now']:,.0f}) → **{cmd['side']} {cmd['qty']:,} shares** at market\n"
                f"· I watch the price and fire it the moment it crosses — during market "
                f"hours, and I alert you (🎯) when it happens.\n"
                f"Reply **\"yes\"** to set it, \"no\" to drop it.")
    return (f"🎯 **조건 주문 — 설정할까요?**\n"
            f"· **{cmd['name']}** 이(가) **₩{cmd['trigger']:,.0f}** 에 {arrow} "
            f"(현재 ₩{cmd['px_now']:,.0f}) → **{side_ko} {cmd['qty']:,}주** 시장가 실행\n"
            f"· 제가 가격을 지켜보다가 닿는 순간 자동 실행합니다 (장중에만) — 실행되면 "
            f"🎯 알림으로 알려드립니다.\n"
            f"**\"네\"** 라고 하시면 설정, \"아니요\"면 취소합니다.")


def store(p: dict) -> str:
    """Called by chat_trade.finish on '네' for a cond pending. Returns the reply."""
    rules = _load()
    rid = int(time.time())
    rules = [r for r in rules if not (r["code"] == p["code"] and r["side"] == p["side"])]
    rules.append({"id": rid, "code": p["code"], "name": p["name"], "side": p["side"],
                  "qty": int(p["qty"]), "trigger": float(p["trigger"]),
                  "direction": p["direction"], "created": time.time(),
                  "en": bool(p.get("en"))})
    _save(rules[-_MAX_RULES:])
    en = bool(p.get("en"))
    side_ko = "매수" if p["side"] == "BUY" else "매도"
    if en:
        return (f"🎯 **Set.** I'm watching {p['name']} — the moment it "
                f"{'falls to' if p['direction'] == 'down' else 'rises to'} "
                f"₩{p['trigger']:,.0f}, I {p['side']} {p['qty']:,} shares and alert you. "
                f"See/cancel anytime: \"show conditional orders\" / \"cancel conditional orders\".")
    return (f"🎯 **설정 완료.** {p['name']} 이(가) ₩{p['trigger']:,.0f}에 "
            f"{'내려오는' if p['direction'] == 'down' else '올라가는'} 순간 "
            f"{side_ko} {p['qty']:,}주 실행하고 알려드립니다. "
            f"확인은 \"조건 주문 보여줘\", 취소는 \"조건 주문 취소\"라고 말씀하세요.")


def list_reply(lang: str) -> str:
    rules = _load()
    en = str(lang or "").lower().startswith("en")
    if not rules:
        return ("등록된 조건 주문이 없습니다. 예: \"삼성전자 260,000원 되면 5주 사줘\"" if not en
                else "No conditional orders set. e.g. \"buy 5 samsung when it hits 260,000\"")
    L = ["🎯 **조건 주문 목록**" if not en else "🎯 **Conditional orders**"]
    for r in rules:
        side_ko = "매수" if r["side"] == "BUY" else "매도"
        if en:
            L.append(f"· {r['name']} — {r['side']} {r['qty']:,} sh when price "
                     f"{'falls to' if r['direction'] == 'down' else 'rises to'} ₩{r['trigger']:,.0f}")
        else:
            L.append(f"· {r['name']} — ₩{r['trigger']:,.0f}에 "
                     f"{'내려오면' if r['direction'] == 'down' else '올라가면'} {side_ko} {r['qty']:,}주")
    L.append("취소: \"조건 주문 취소\"" if not en else "Cancel: \"cancel conditional orders\"")
    return "\n".join(L)


def cancel_reply(transcript: str, lang: str) -> str:
    rules = _load()
    en = str(lang or "").lower().startswith("en")
    if not rules:
        return ("취소할 조건 주문이 없습니다." if not en else "No conditional orders to cancel.")
    try:
        from services.stock_resolver import resolve_one
        code, _n = resolve_one(transcript)
    except Exception:
        code = None
    if code:
        kept = [r for r in rules if r["code"] != code]
        dropped = [r for r in rules if r["code"] == code]
        if not dropped:
            return ("그 종목의 조건 주문은 없습니다." if not en
                    else "No conditional order on that stock.")
        _save(kept)
        n = dropped[0]["name"]
        return (f"🚫 {n} 조건 주문을 취소했습니다." if not en
                else f"🚫 Cancelled the conditional order on {n}.")
    _save([])
    return (f"🚫 조건 주문 {len(rules)}건을 모두 취소했습니다." if not en
            else f"🚫 Cancelled all {len(rules)} conditional order(s).")


def check_and_fire(db, emit) -> None:
    """Watchdog hook (market hours only). `emit(icon, text_ko, text_en, code)`."""
    rules = _load()
    if not rules:
        return
    keep = []
    from services.paper_desk import fast_price
    for r in rules:
        try:
            px, _c, _t, _s = fast_price(r["code"])
        except Exception:
            px = None
        if not px:
            keep.append(r)
            continue
        crossed = (float(px) <= r["trigger"] if r["direction"] == "down"
                   else float(px) >= r["trigger"])
        if not crossed:
            keep.append(r)
            continue
        try:
            from services.paper_desk import place_order
            res = place_order(db, r["code"], r["side"], int(r["qty"]),
                              order_type="market", source="chatbot",
                              ref_price=float(px), direct=True)
            if res.get("ok"):
                fill = res.get("fill_price") or px
                side_ko = "매수" if r["side"] == "BUY" else "매도"
                emit("🎯",
                     f"🎯 **조건 발동 — {r['name']}** ₩{r['trigger']:,.0f} 도달 → "
                     f"{side_ko} {int(r['qty']):,}주 체결 @ ₩{float(fill):,.0f}",
                     f"🎯 **Condition fired — {r['name']}** hit ₩{r['trigger']:,.0f} → "
                     f"{r['side']} {int(r['qty']):,} sh filled @ ₩{float(fill):,.0f}",
                     r["code"])
            else:
                emit("⚠️",
                     f"⚠️ 조건 발동했지만 주문 실패 — {r['name']}: {res.get('error') or '?'}",
                     f"⚠️ Condition fired but the order failed — {r['name']}: {res.get('error') or '?'}",
                     r["code"])
        except Exception as e:
            log.warning(f"chat_conditional fire failed: {str(e)[:120]}")
            keep.append(r)               # keep the rule; retry next poll
    _save(keep)
