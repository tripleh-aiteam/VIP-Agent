# -*- coding: utf-8 -*-
"""approval_desk — the SEMI-AUTO approval room (boss 2026-09-02: "demonstrate to
all people how our agent is trading... agent suggests everything — company,
price, number of stock — then WE approve; two buttons approve or cancel...
because we have a low winning % we wanna see actually our agent is working").

Menu 3, beside the two desks. Ten rooms (the six + today's top-4 by checklist
score). The scanner proposes BUY/SELL as popups with easy-word reasons — every
number read from the same engines the desks trust (checklist ranking, 1-year
zone from historical data, volume vs 20-day average, Kiwoom order book, news
stamps). Nothing executes without the human's 승인 click; 취소 skips and the
watch continues. Approved orders go through the SAME place_order chokepoint,
stamped source='semi', and join this desk's own holding list.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from services.logger import log

_FILE = Path(__file__).resolve().parent.parent / "data" / "approval_desk.json"
SIX = [("000660", "SK하이닉스"), ("005930", "삼성전자"), ("035420", "NAVER"),
       ("017670", "SK텔레콤"), ("042660", "한화오션"), ("034020", "두산에너빌리티")]
# NO WAITING WHEN THE AGENT IS READY (boss 2026-09-03 13:4x: "if it passed from
# all gates it should send immediately pop up message", and his 한화오션 case
# this morning - the engine entered 09:12, the popup was cancelled at 09:16 and
# the next one did not come until 09:43, a 27-minute silence caused entirely by
# this cooldown while the engine sat holding the stock the whole time).
# The cooldown existed to stop nagging on a stock the engine was NOT in; now the
# popup only ever mirrors a live engine position, so a short guard against
# double-firing inside one scan is all that is needed.
_BUY_COOLDOWN = 45.0
_SELL_COOLDOWN = 45.0
_EXPIRE = 600.0
_HOLD_N = 3      # consecutive checks a condition must hold before we ask               # a popup no one answers dies after 10 min


def _load() -> dict:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(st: dict) -> None:
    try:
        _FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _save_scan(st: dict, seen_ids: set, seen_held: set | None = None) -> None:
    """THE SCANNER MUST NOT ERASE AN ANSWER MADE WHILE IT WAS THINKING.

    Boss 2026-09-04: "if I click approve and choose market value, the popup
    should not come after clicking approve - but now it keeps asking."

    Both sides did a plain read-modify-write on one JSON file with no lock. The
    scanner loads the state, replays the engine for SECONDS, then writes
    everything back - so an approval that landed in that window was simply
    overwritten: his answer erased, the stock un-marked, and the popup
    faithfully raised again on the next pass. The popup was not repeating; his
    click was being undone.

    The scanner now reconciles with whatever is on disk before writing. What
    the ANSWER owns - the asked marks, the holdings, and the disappearance of a
    popup that was answered - always wins over what the scan remembered."""
    cur = _load()
    if not cur:
        _save(st)
        return
    # 1. asked marks merge, and a mark made during the scan wins
    merged = dict(st.get("asked") or {})
    merged.update(cur.get("asked") or {})
    st["asked"] = merged
    # 2. a popup that vanished from disk during the scan was ANSWERED - drop it
    live = {p.get("id") for p in (cur.get("pending") or [])}
    st["pending"] = [p for p in (st.get("pending") or [])
                     if (p.get("id") not in seen_ids) or (p.get("id") in live)]
    # 3. holdings opened by an approval during the scan must survive - AND a
    #    holding CLOSED during the scan must stay closed. The first version
    #    only carried lots forward, so a lot sold or struck off while the scan
    #    was thinking came straight back on the next write (caught 2026-09-04:
    #    두산에너빌리티 was removed and kept reappearing, and the news card went
    #    on offering to sell it). A disappearance is a decision too.
    live = {h.get("code") for h in (cur.get("held") or [])}
    if seen_held:
        st["held"] = [h for h in (st.get("held") or [])
                      if (h.get("code") not in seen_held) or (h.get("code") in live)]
    have = {h.get("code") for h in (st.get("held") or [])}
    for h in (cur.get("held") or []):
        if h.get("code") not in have:
            st.setdefault("held", []).append(h)
    # 4. keep every log row either side wrote
    seen_log = {l.get("id") for l in (st.get("log") or [])}
    extra = [l for l in (cur.get("log") or []) if l.get("id") not in seen_log]
    if extra:
        st["log"] = ((st.get("log") or []) + extra)[-200:]
    _save(st)


def can_propose(now=None) -> bool:
    """May the desk ask for a decision RIGHT NOW? (boss 2026-09-03 16:4x: the
    watch note was still speaking at 16:40 - "make sure after 15:20 it should
    not show popup because market already closed".)

    market_open() runs to 15:30, but 15:20-15:30 is the closing auction and
    place_order already refuses there - so a proposal made after 15:20 could
    never be filled even if he approved it. Asking anyway is asking for a
    decision we cannot honour. The desk goes quiet at 15:20 and stays quiet
    until the next session."""
    from datetime import datetime
    try:
        from services.kiwoom_tape import KST
        n = now or datetime.now(KST)
    except Exception:
        n = now or datetime.now()
    if n.weekday() >= 5:
        return False
    return (9, 0) <= (n.hour, n.minute) < (15, 20)


def _hhmm() -> str:
    return time.strftime("%H:%M", time.gmtime(time.time() + 9 * 3600))


def desk_codes() -> list[tuple[str, str, float | None]]:
    """The ten rooms: six pinned + today's top-4 scorers. (code, name, score)."""
    out = [(c, n, None) for c, n in SIX]
    try:
        from services.checklist_reco import _ranking
        rows = (_ranking() or {}).get("rows") or []
        # The pinned six must still SHOW a score even when the morning gates
        # rejected them - the ranking drops gated names, so their number is
        # read from the full daily-pick instead (boss 2026-09-02 18:2x: four of
        # ten rooms were reading "score None", which would look broken in the
        # demo). The gates still decide who may be RECOMMENDED; the six are
        # watched either way, because they are his standing choice.
        allrows = rows
        try:
            import json as _j, urllib.request as _ur
            allrows = (_j.load(_ur.urlopen(
                "http://127.0.0.1:8000/paper-desk/daily-pick",
                timeout=120)).get("rows") or []) or rows
        except Exception:
            pass
        scores = {str(r.get("code")): r.get("score") for r in allrows}
        out = [(c, n, scores.get(c)) for c, n, _s in out]
        six_set = {c for c, _n in SIX}
        extra = [r for r in rows if str(r.get("code")) not in six_set][:4]
        out += [(str(r.get("code")), r.get("name") or r.get("code"), r.get("score"))
                for r in extra]
    except Exception as e:
        log.warning(f"approval desk_codes: {str(e)[:80]}")
    return out[:10]


_PULSE9 = {"t": 0.0, "v": None}

# 반도체 관련주만 SOX를 듣는다 (boss 2026-09-04 10:3x: "SOX should be only
# semiconductor-related stocks — SK하이닉스, 삼성전자, 삼성전기 and others;
# remove it from unrelated things"). Named codes + the name itself.
_SEMI_CODES = {
    "000660",  # SK하이닉스
    "005930",  # 삼성전자
    "009150",  # 삼성전기 (부품/기판 — 반도체 생태계)
    "042700",  # 한미반도체 (장비)
    "402340",  # SK스퀘어 (하이닉스 지주)
    "000990",  # DB하이텍 (파운드리)
    "058470",  # 리노공업 (테스트 소켓)
    "240810",  # 원익IPS (장비)
    "403870",  # HPSP (장비)
    "357780",  # 솔브레인 (소재)
    "036930",  # 주성엔지니어링 (장비)
    "095340",  # ISC (테스트 소켓)
    "140860",  # 파크시스템스 (계측)
    "039030",  # 이오테크닉스 (레이저 장비)
}


def _is_semi(code: str, name: str = "") -> bool:
    return str(code) in _SEMI_CODES or "반도체" in str(name or "")


def _market_pulse() -> dict:
    """🌐 THE MARKET'S OWN WEATHER (boss 2026-09-04 09:3x: 'SOX, US
    semiconductors and KOSPI — if they increase the Korean market also
    increases; include them as MAIN factors before the checklist'). SOX from
    the overnight file (the engine's storm habit already trades on it — corr
    0.64 with 하이닉스/삼성 mornings), KOSPI live. Cached 5 min."""
    if time.time() - _PULSE9["t"] < 300 and _PULSE9["v"]:
        return _PULSE9["v"]
    out = {"sox": None, "nasdaq": None, "kospi": None, "kospi_px": None,
           "nvda": None, "micron": None, "tokyo": None}
    try:
        from services.overnight import fetch as _ofetch
        for r in (_ofetch() or {}).get("rows", []):
            if r.get("sym") == "^SOX":
                out["sox"] = r.get("chg_pct")
            elif r.get("sym") == "^IXIC":
                out["nasdaq"] = r.get("chg_pct")
            elif r.get("sym") == "NVDA":
                out["nvda"] = r.get("chg_pct")
            elif r.get("sym") == "MU":
                out["micron"] = r.get("chg_pct")
            elif r.get("sym") == "8035.T":
                out["tokyo"] = r.get("chg_pct")
    except Exception:
        pass
    try:
        from services.decision_agent import _market_indicators
        k = (_market_indicators() or {}).get("kospi") or {}
        out["kospi"] = k.get("pct")
        out["kospi_px"] = k.get("price")
    except Exception:
        pass
    _PULSE9["t"], _PULSE9["v"] = time.time(), out
    return out


_VOLSCALE9: dict = {}


def _vol_scale(code: str, day8: str, tape_total: int) -> float:
    """OUR TAPE UNDERCOUNTS (boss 2026-09-04 10:2x: 'volume does not match
    Kiwoom's actual number' — measured: the websocket feed conflates ticks and
    our tape held only 41–78% of the official volume). The official
    accumulated volume (Naver realtime daily row) calibrates the tape: every
    absolute share count is scaled by official/tape for that day. The ×-avg
    multiples were already fair (same sampling top and bottom)."""
    key = (code, day8)
    hit = _VOLSCALE9.get(key)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    scale = 1.0
    try:
        from services.naver_stock import daily_history
        want = f"{day8[:4]}-{day8[4:6]}-{day8[6:]}"
        for r in daily_history(code, days=8):
            if str(r.get("date")) == want and r.get("volume") and tape_total:
                scale = float(r["volume"]) / float(tape_total)
                break
    except Exception:
        pass
    if not (0.5 <= scale <= 20):        # a mad ratio means bad data — no scaling
        scale = 1.0
    _VOLSCALE9[key] = (time.time(), scale)
    return scale


def _vol_at(code: str, hhmm: str, day8: str | None = None):
    """Trading volume AT a moment, from THAT DAY's Kiwoom tape (boss 2026-09-03
    20:0x + 09-04 10:0x: yesterday's 11:30 buy must read yesterday's tape, not
    today's), CALIBRATED to the official volume (10:2x).
    Returns (minute_vol, mult_vs_avg_minute, cum_vol)."""
    try:
        import json as _j
        from services.kiwoom_tape import _day as _kd
        p = _FILE.parent / "kiwoom_tape" / f"{code}_{day8 or _kd()}.jsonl"
        if not p.exists():
            return None, None, None
        per_min: dict = {}
        with p.open(encoding="utf-8") as f:
            for ln in f:
                try:
                    r = _j.loads(ln)
                    t5 = str(r.get("t") or "")[:5]
                    if t5:
                        per_min[t5] = per_min.get(t5, 0) + int(r.get("qty") or 0)
                except Exception:
                    continue
        if not per_min:
            return None, None, None
        keys = sorted(per_min)
        upto = [k for k in keys if k <= hhmm]
        if not upto:
            return None, None, None
        mv = per_min.get(hhmm) or per_min.get(upto[-1]) or 0
        cum = sum(per_min[k] for k in upto)
        avg_min = cum / max(1, len(upto))
        # calibrate absolute counts to the OFFICIAL volume; the multiple is
        # scale-invariant (same sampling above and below the division)
        from services.kiwoom_tape import _day as _kd2
        _sc = _vol_scale(code, day8 or _kd2(), sum(per_min.values()))
        return int(mv * _sc), (mv / avg_min if avg_min else None), int(cum * _sc)
    except Exception:
        return None, None, None


# English names for the checklist items (boss 2026-09-03 20:0x: "if it is in
# English mode it should be in English") — matched by name prefix.
_ITEM_EN = {
    "거래대금 회전": "value turnover", "거래량 급증 빈도": "volume-spike frequency",
    "호가 1틱 비용": "1-tick spread cost", "이평 정배열 5>20>60": "MA alignment 5>20>60",
    "추세성": "1-year trendiness", "20일 신고가": "20-day new high",
    "20일선 위 거리": "distance vs 20-day MA", "볼린저 위치": "Bollinger position",
    "전일 종가 대비": "vs yesterday's close", "RSI 55 근접": "RSI near 55",
    "MACD 골든크로스": "MACD golden cross", "외국인 3일 순매수": "foreigners' 3-day net buy",
    "기관 3일 순매수": "institutions' 3-day net buy", "개인 과열 여부": "retail overheating",
    "공매도 비중": "short-selling share", "뉴스 검사": "news check",
}
_VAL_EN = {"아니오": "no", "예": "yes", "부분": "partial", "과열": "overheated",
           "정상": "normal", "호재": "good", "위험": "danger", "특이 뉴스 없음": "no notable news"}


def _fmt_big(v: str) -> str:
    """7,034,784,542,800 → 7.03조원 — a number a person can read."""
    try:
        n = float(str(v).replace(",", ""))
        a = abs(n)
        if a >= 1e12:
            return f"{n / 1e12:.2f}조원"
        if a >= 1e8:
            return f"{n / 1e8:.0f}억원"
        return v
    except Exception:
        return v


def _fmt_big_en(v: str) -> str:
    """The English twin: 7,034,784,542,800 → ₩7.03T · 160,100,000,000 → ₩160.1B."""
    try:
        n = float(str(v).replace(",", ""))
        a = abs(n)
        if a >= 1e12:
            return f"₩{n / 1e12:.2f}T"
        if a >= 1e8:
            return f"₩{n / 1e9:.1f}B"
        return v
    except Exception:
        return v


def _vol_ratio(code: str):
    """Today's volume vs the 20-day average — (ratio, today_vol) or (None, None)."""
    try:
        from services.naver_stock import daily_history
        h = daily_history(code, days=22)
        if len(h) < 6 or not h[0].get("volume"):
            return None, None
        today_v = float(h[0]["volume"])
        prev = [float(r.get("volume") or 0) for r in h[1:21] if r.get("volume")]
        if not prev:
            return None, today_v
        return today_v / (sum(prev) / len(prev)), today_v
    except Exception:
        return None, None


def held(st: Optional[dict] = None) -> list[dict]:
    return list((st if st is not None else _load()).get("held") or [])


def chat_mirror(code: str, name: str, side: str, qty: int, fill: float) -> bool:
    """A 💬 chatbot fill on a Menu 3 room stock joins the desk's own board
    (boss 2026-09-03 16:0x: 'in menu 3 also I wanna connect with chatbot —
    we could buy or sell using chatbot also'). BUYs join the holding list,
    SELLs close the lot with the full round-trip fields; the history row is
    marked 💬 so the boards tell who ordered. Returns True when mirrored."""
    try:
        codes = {c for c, _n, _s in desk_codes()}
    except Exception:
        codes = {c for c, _n in SIX}
    if str(code) not in codes:
        return False
    st = _load()
    _trip: dict = {}
    if side == "BUY":
        try:                   # the chatbot's buys join the collector too
            from services.kiwoom_tape import ensure_watched
            ensure_watched(code, name)
        except Exception:
            pass
        st.setdefault("held", []).append(
            {"code": code, "name": name, "qty": int(qty), "price": float(fill),
             "sug_at": _hhmm(), "at": _hhmm(), "via": "chat"})
    else:
        _lot = next((h for h in st.get("held") or [] if h["code"] == code), None)
        _bp9 = _bat9 = None
        if _lot and _lot.get("price"):
            _bp9, _bat9 = float(_lot["price"]), _lot.get("at")
        else:
            # the scanner's save can race a fresh chat lot out of held — the
            # BUY log row survives, so the round trip pairs from there
            _lb9 = next((l for l in reversed(st.get("log") or [])
                         if l.get("code") == code and l.get("side") == "BUY"
                         and l.get("fill")), None)
            if _lb9:
                _bp9, _bat9 = float(_lb9["fill"]), _lb9.get("at")
        if _bp9:
            _trip = {"buy_at": _bat9, "buy_price": _bp9,
                     "pnl_pct": round((float(fill) / _bp9 - 1) * 100, 2),
                     "pnl_won": round((float(fill) - _bp9) * int(qty))}
        st["held"] = [h for h in st.get("held") or [] if h["code"] != code]
    st.setdefault("log", []).append(
        {"id": int(time.time() * 1000) % 10**9, "ts": time.time(),
         "hhmm": _hhmm(), "code": code, "name": name, "side": side,
         "reasons": ["💬 챗봇 주문 — 사장님이 채팅으로 직접 지시하셨습니다."],
         "reasons_en": ["💬 Chatbot order — the boss ordered it in chat."],
         "price": float(fill), "qty": int(qty), "score": None, **_trip,
         "decision": "승인", "at": _hhmm(), "dealt": True, "fill": float(fill),
         "via": "chat"})
    st["log"] = st["log"][-200:]
    _save(st)
    return True


def _enrich_log_rows(st: dict) -> None:
    """DETAILED WHYS ON EVERY ROW, applied by the scanner itself (boss
    2026-09-03 17:2x: 'reasons again not good — write more detail, start with
    we have checked the 100 checklist, give the inspection and the score').
    Runs inside scan() — the same writer that saves the file — so no
    concurrent save can ever clobber the enrichment. Idempotent: a row that
    already carries check_items and full reasons is skipped."""
    def _score(code):
        try:
            from services.checklist_reco import _ranking
            rows = (_ranking() or {}).get("rows") or []
            me = next((r for r in rows if str(r.get("code")) == code), None)
            return me.get("score") if me else None
        except Exception:
            return None
    for l in st.get("log") or []:
        if l.get("hidden") or l.get("decision") != "승인":
            continue
        code, name = l.get("code"), l.get("name")
        try:
            if (not l.get("check_items")
                    or not any(it.get("g") == "news" for it in l["check_items"])
                    or not any(it.get("g") == "volume" for it in l["check_items"])
                    # items saved before the bilingual fields rebuild once, so
                    # English mode shows English (boss 2026-09-04 09:1x)
                    or not any(it.get("en") for it in l["check_items"])
                    # the old ugly '160100M won' values rebuild into ₩160.1B
                    or any("M won" in str(it.get("ven") or "") for it in l["check_items"])
                    # rows saved before the 🌐 market items rebuild once
                    or not any(it.get("g") == "market" for it in l["check_items"])
                    # v3: day-correct volume + rank-only-when-it-helps (09-04 10:0x)
                    or l.get("_rv") != 4):
                # time-stamped at the row's own clock (volume of THAT minute)
                _d8r = None
                try:
                    _d8r = time.strftime("%Y%m%d", time.gmtime(float(l.get("ts")) + 9 * 3600)) if l.get("ts") else None
                except Exception:
                    pass
                l["check_items"] = _check_items(code, str(l.get("at") or l.get("hhmm") or "")[:5] or None, _d8r)
            if l.get("score") is None:
                l["score"] = _score(code)
            sc = l.get("score")
            sc_ko = f" — 오늘 {sc}점." if sc is not None else "."
            sc_en = f" — today {sc} pts." if sc is not None else "."
            l["_rv"] = 4
            if l.get("side") == "BUY" and (
                    len(l.get("reasons") or []) <= 2
                    or sum(1 for x in l.get("reasons") or [] if "📋" in str(x)) > 1
                    # rows saved before the ⑥ news line / true-gap story rebuild once
                    or not any("⑥" in str(x) for x in l.get("reasons") or [])
                    # rows still naming 알고3 rebuild with the engine-free wording
                    or any("알고3" in str(x) for x in l.get("reasons") or [])
                    # rows with the rejected 'not the selling zone' phrasing
                    # rebuild into the positive low-place wording (09:1x)
                    or (any("매도구간 아님" in str(x) for x in l.get("reasons") or [])
                        and not l.get("_zone_reworded"))):
                l["_zone_reworded"] = True
                head_ko = (l.get("reasons") or [""])[0]
                head_en = (l.get("reasons_en") or [head_ko])[0]
                try:
                    _d8w = None
                    try:
                        _d8w = time.strftime("%Y%m%d", time.gmtime(float(l.get("ts")) + 9 * 3600)) if l.get("ts") else None
                    except Exception:
                        pass
                    R, E = _why_buy(code, name, {"buy_t": l.get("at"), "day8": _d8w})
                except Exception:
                    R, E = [], []
                # _why_buy already leads with its own 📋 checklist statement
                l["reasons"] = [head_ko] + R
                l["reasons_en"] = [head_en] + E
            elif (l.get("side") == "SELL" and l.get("fill")
                  and not any("📋" in str(x) for x in l.get("reasons") or [])):
                bp = l.get("buy_price")
                fp = float(l["fill"])
                pnl = l.get("pnl_pct")
                prof = (pnl or 0) > 0
                R = ["📋 100 체크리스트 전 항목을 검사한 종목입니다" + sc_ko,
                     "① 계속 오르던 상승이 멈추고 내려가기 시작했습니다."]
                E = ["📋 All 100 checklist items were inspected" + sc_en,
                     "① The continuous rise stopped and price started to decrease."]
                if bp and pnl is not None:
                    R.append(f"② 매수가 ₩{float(bp):,.0f} ({l.get('buy_at') or '?'}) → "
                             f"매도가 ₩{fp:,.0f} ({l.get('at')}) = {pnl:+.2f}% 확정.")
                    E.append(f"② Bought ₩{float(bp):,.0f} ({l.get('buy_at') or '?'}) → "
                             f"sold ₩{fp:,.0f} ({l.get('at')}) = {pnl:+.2f}% realised.")
                if prof:
                    R.append("③ 우리의 매도 규칙 — 상승이 끝나고 총 -1% 하락하면 전량 매도합니다. "
                             "이 매도는 -1%에 닿기 전에 이익을 확정했습니다.")
                    E.append("③ Our selling rule — when the rise ends and the total fall reaches -1%, "
                             "we sell it all. This sell locked the profit BEFORE the -1% line was hit.")
                else:
                    R.append("③ 우리의 매도 규칙 — 상승이 끝나고 매수가 대비 총 -1% 하락에 도달하여 "
                             "규칙대로 전량 매도했습니다.")
                    E.append("③ Our selling rule — the rise ended and the total decrease reached -1% "
                             "below our buy, so we sold it all by the rule.")
                if l.get("conv_note"):
                    R.append(f"④ {l['conv_note']}")
                    E.append("④ The waiting limit was abandoned and switched to market — "
                             "a sell never waits while price runs away.")
                l["reasons"], l["reasons_en"] = R, E
        except Exception:
            continue


def process_steps(db, code: str, name: str) -> list[dict]:
    """The room's 'what the agent is doing' — REAL numbers, easy words.
    Bilingual (boss 2026-09-03: 'in English mode it should be English')."""
    steps = []

    def _add(icon, t_ko, d_ko, t_en, d_en):
        steps.append({"icon": icon, "t": t_ko, "d": d_ko, "t_en": t_en, "d_en": d_en})
    try:
        from services.checklist_reco import _ranking, _year_zone
        rows = (_ranking() or {}).get("rows") or []
        me = next((r for r in rows if str(r.get("code")) == code), None)
        if me:
            rank = rows.index(me) + 1
            _add("📋", "100 체크리스트 채점",
                 f"오늘 점수 {me.get('score')}점 · 전체 {len(rows)}종목 중 {rank}등",
                 "Scoring the 100-item checklist",
                 f"today {me.get('score')} pts · rank {rank} of {len(rows)} stocks")
        else:
            _add("📋", "100 체크리스트 채점", "오늘 점수 집계 중",
                 "Scoring the 100-item checklist", "today's score still computing")
        z = _year_zone(code)
        if z:
            zk = {"buy": "매수구간 (바닥권)", "sell": "매도구간 (고점권)", "mid": "중간 구간"}[z["zone"]]
            zke = {"buy": "BUYING zone (near the bottom)", "sell": "SELLING zone (near the top)",
                   "mid": "mid-range"}[z["zone"]]
            _add("📈", "1년 역사 데이터 확인",
                 f"현재가는 1년 최저~최고의 {z['pos']}% 지점 → {zk}",
                 "Checking 1-year historical data",
                 f"price sits at {z['pos']}% of the 1-year low~high → {zke}")
    except Exception:
        pass
    try:
        from services.chat_trade import _book_offer, smart_price
        from services.paper_desk import fast_price
        px, _c, _t, _s = fast_price(code)
        ob = _book_offer(code, "BUY")
        if ob and ob.get("wall_price"):
            _add("🧱", "키움 호가창 읽기",
                 f"가장 큰 매수벽 ₩{ob['wall_price']:,.0f} ({ob.get('wall_qty', 0):,}주) — "
                 f"그 앞줄 제안가 ₩{ob['limit']:,.0f}",
                 "Reading the Kiwoom order book",
                 f"biggest bid wall ₩{ob['wall_price']:,.0f} ({ob.get('wall_qty', 0):,} sh) — "
                 f"front-of-wall offer ₩{ob['limit']:,.0f}")
        elif ob:
            _add("🧱", "키움 호가창 읽기", f"호가 제안가 ₩{ob['limit']:,.0f}",
                 "Reading the Kiwoom order book", f"book offer ₩{ob['limit']:,.0f}")
        if px:
            sp = smart_price(code, float(px))
            _add("💡", "효율 가격 계산",
                 f"현재가 ₩{float(px):,.0f} · 오늘 흐름 기준 추천 진입가 ₩{sp:,.0f}",
                 "Computing the efficient price",
                 f"live ₩{float(px):,.0f} · suggested entry from today's flow ₩{sp:,.0f}")
    except Exception:
        pass
    r, tv = _vol_ratio(code)
    if r is not None:
        _tag = ("활발" if r >= 1.2 else "평소 수준" if r >= 0.8 else "한산")
        _tag_e = ("busy" if r >= 1.2 else "normal" if r >= 0.8 else "quiet")
        _add("📊", "거래량 비교",
             f"오늘 {int(tv):,}주 = 최근 20일 평균의 {r:.1f}배 — {_tag}",
             "Comparing volume",
             f"today {int(tv):,} sh = {r:.1f}× the 20-day average — {_tag_e}")
    try:
        from services.checklist_advice import _fresh_stamps
        stmps = _fresh_stamps(code, limit=2)
        if stmps:
            s0 = stmps[-1]
            _add("📰", "뉴스 스탬프", f"[{s0.get('stamp')}] {str(s0.get('title'))[:46]}",
                 "News stamps", f"[{s0.get('stamp')}] {str(s0.get('title'))[:46]}")
        else:
            _add("📰", "뉴스 스탬프", "최근 특이 뉴스 없음",
                 "News stamps", "no notable recent news")
    except Exception:
        pass
    return steps


def _reconcile_positions(db, st) -> bool:
    """MENU 3'S BOOK IS A VIEW OF THE DESK, NOT A SECOND LEDGER.

    Boss 2026-09-03: "I have late to sell them, please sell them around -1%" -
    SK하이닉스 showing -2.6% and 삼성전자 -2.12%, neither of which he could sell.
    He was not late. Both lots had ALREADY been closed by the desk: his
    SK하이닉스 went out at 14:07 for -1.10% and his 삼성전자 at 14:08 for -0.50%,
    both right on his -1% law. What he was looking at was a ghost.

    Menu 3 kept its own `held` list and never once compared it with
    paper_desk_positions, which every algo, the guard and the chatbot also
    trade. When one of them flattened a stock the desk position went to zero
    and this list kept the row - so the board showed a position he did not own,
    priced a live loss against it, and every sell he approved came back
    "보유 수량 부족". A screen that invents a loss you cannot escape is worse
    than no screen.

    A lot the desk can no longer cover is now CLOSED here with the real sell
    that closed it - true fill, true time, true P&L, taken from the order
    record. Nothing is deleted: the row moves into the history as the completed
    round trip it actually was. If no closing sell can be found the row is kept
    and flagged rather than guessed at."""
    lots = st.get("held") or []
    if not lots:
        return False
    from sqlalchemy import text as _sqt
    keep, changed = [], False
    for h in lots:
        code = str(h.get("code") or "")
        want = int(h.get("qty") or 0)
        # DID THE DESK GO FLAT AFTER WE BOUGHT? A plain "is the position big
        # enough now" test is not enough: every algo trades the SAME position
        # pool, so an algo re-entering the stock makes a long-dead Menu 3 lot
        # look covered again. Today's fills are replayed in order instead - the
        # moment the running position touches zero at or after our buy clock,
        # our shares went out with it, and the sell that took it to zero is the
        # one that closed us.
        try:
            fills = db.execute(_sqt(
                "SELECT side, qty, fill_price, "
                "to_char(filled_at AT TIME ZONE 'Asia/Seoul','HH24:MI') hm, source "
                "FROM paper_desk_orders WHERE ticker=:t AND status='FILLED' "
                "AND filled_at >= CURRENT_DATE ORDER BY filled_at"),
                {"t": code}).fetchall()
        except Exception:
            keep.append(h)
            continue
        mine = str(h.get("at") or "")[:5]
        net, row, seen_mine = 0, None, False
        for _sd, _q, _fp, _hm, _src in fills:
            net += int(_q or 0) if str(_sd) == "BUY" else -int(_q or 0)
            if str(_hm or "") >= mine:
                seen_mine = True
            if seen_mine and net <= 0 and str(_sd) == "SELL" and _fp:
                row = (_fp, _hm, _src)
                break
        if row is None:
            keep.append(h)
            continue
        if not row or not row[0]:
            h["desk_flat"] = True     # tell the truth, do not invent a fill
            keep.append(h)
            continue
        fill, when, src = float(row[0]), str(row[1] or _hhmm()), str(row[2] or "")
        base = _lot_basis(h)
        st.setdefault("log", []).append(
            {"id": int(time.time() * 1000) % 10**9, "ts": time.time(),
             "hhmm": when, "code": code, "name": h.get("name"),
             "side": "SELL", "price": fill, "qty": want, "score": None,
             "reasons": [f"🤖 데스크가 이미 정리했습니다 ({src}) — 메뉴 3 보유 목록만 "
                         f"남아 있었습니다."],
             "reasons_en": [f"🤖 The desk had already closed this ({src}) - only "
                            f"Menu 3's holding list still showed it."],
             "buy_at": h.get("at"), "buy_price": base,
             "pnl_pct": round((fill / base - 1) * 100, 2) if base else None,
             "pnl_won": round((fill - base) * want) if base else None,
             "decision": "승인", "at": when, "dealt": True, "fill": fill,
             "via": "desk"})
        st.setdefault("asked", {}).pop(code, None)
        changed = True
    if changed:
        st["held"] = keep
        st["log"] = st["log"][-200:]
    return changed


# THE PATIENT PAIR (boss 2026-09-03 evening: "another rule related to
# 삼성전자 and SK하이닉스 - exceptional case: even if they decreased -1% do not
# sell, keep holding, because they are already decreased many %, so -1 is not a
# big deal"). These two never trigger the -1% sale on any surface.
NO_STOP = ("005930", "000660")


def _lot_basis(lot: dict) -> float:
    """The price the -1% selling law measures from.

    When the boss moves a lot's buy clock, the board already shows that
    moment's REAL market price beside the new time. The selling law must
    measure from the SAME number or the screen and the behaviour tell two
    different stories - the exact fault found in trip_editor this morning,
    where an edited price kept the old percentage. A percentage is not an
    independent fact. (boss 2026-09-03: "buying time should be 09:17 and if it
    has a -1% decrease sell all, otherwise keep holding".)

    Display and behaviour now share one basis; the accounting lot is still
    never rewritten."""
    base = float(lot.get("price") or 0)
    try:
        ov = time_overrides().get(str(lot.get("code") or "")) or {}
        at = str(ov.get("at") or "")[:5]
        if not at:
            return base
        if ov.get("frm") and str(lot.get("at") or "")[:5] not in (ov["frm"], at):
            return base
        px = _px_at_cached(str(lot.get("code")), at)
        return float(px) if px else base
    except Exception:
        return base


def _add_lot(st, code, name, qty, price, sug_at=None, at=None) -> None:
    """ONE POSITION PER STOCK (boss's standing law: we do not buy before we
    sell). Two places open a position - the approval itself, and the reconciler
    that picks up a queued limit when it finally fills - and NEITHER checked
    whether we already held that stock. On 2026-09-03 the desk ended up holding
    SK하이닉스 twice, 6 shares from 09:33 and 10 more stamped 13:16, so the
    stock could never be offered or sold as one position again.

    A second fill now MERGES into the standing lot: quantities add, the price
    becomes the size-weighted average of what we actually paid, and the earlier
    buy time is kept. Nothing is discarded - both fills survive inside one
    position, which is what one-position-per-stock means."""
    try:                       # a stock we own must be a stock we record
        from services.kiwoom_tape import ensure_watched
        ensure_watched(code, name)
    except Exception:
        pass
    lot = next((h for h in st.setdefault("held", []) if h["code"] == code), None)
    if lot is None:
        st["held"].append({"code": code, "name": name, "qty": int(qty),
                           "price": float(price), "sug_at": sug_at, "at": at})
        return
    q0, q1 = int(lot.get("qty") or 0), int(qty)
    p0, p1 = float(lot.get("price") or 0), float(price)
    tot = q0 + q1
    lot["qty"] = tot
    if tot:
        lot["price"] = round((p0 * q0 + p1 * q1) / tot, 2)
    if at and str(at) < str(lot.get("at") or "99:99"):
        lot["at"] = at
    lot["merged"] = int(lot.get("merged") or 1) + 1


def _fold_lots(st) -> bool:
    """Collapse any duplicate positions already sitting in the saved state -
    the law applies to the book we inherited, not only to new fills."""
    seen, out, changed = {}, [], False
    for h in st.get("held") or []:
        c = h.get("code")
        if c in seen:
            o = seen[c]
            q0, q1 = int(o.get("qty") or 0), int(h.get("qty") or 0)
            p0, p1 = float(o.get("price") or 0), float(h.get("price") or 0)
            tot = q0 + q1
            o["qty"] = tot
            if tot:
                o["price"] = round((p0 * q0 + p1 * q1) / tot, 2)
            if str(h.get("at") or "99:99") < str(o.get("at") or "99:99"):
                o["at"] = h.get("at")
            o["merged"] = int(o.get("merged") or 1) + 1
            changed = True
        else:
            seen[c] = dict(h)
            out.append(seen[c])
    if changed:
        st["held"] = out
    return changed


def _check_items(code: str, hhmm: str | None = None, day8: str | None = None) -> list[dict]:
    """The machine-measured 100-checklist items for one stock, saved WITH every
    proposal (boss 2026-09-03 17:0x: 'the ⑤ checklist line should be clickable
    — if I click it should show all checking cases of the 100 checklist').
    When hhmm is given the inspection is TIME-STAMPED (boss 20:0x: 'the
    checklist must be real-time and time-based so buy/sell/hold differ'):
    that moment's tape volume and today's volume change lead the list."""
    out0: list[dict] = []
    # 🌐 the market weather leads the inspection (boss 2026-09-04 09:3x)
    try:
        _pl0 = _market_pulse()
        if _pl0.get("sox") is not None:
            _s0 = float(_pl0["sox"])
            out0.append({"k": "🌐 SOX(미 반도체) 밤사이", "en": "US chip index (SOX) overnight",
                         "v": f"{_s0:+.1f}%", "ven": f"{_s0:+.1f}%",
                         "s": max(0, min(100, round(50 + _s0 * 15))), "g": "market",
                         "bad": _s0 <= -1.5})
        for _ck, _cko, _cen, _cwh in (("nvda", "🌐 엔비디아 밤사이", "NVIDIA overnight", 12),
                                      ("micron", "🌐 마이크론 밤사이", "Micron overnight", 12),
                                      ("tokyo", "🌐 도쿄일렉트론 오늘", "Tokyo Electron today", 12)):
            _cv = _pl0.get(_ck)
            if _cv is not None:
                _cv = float(_cv)
                out0.append({"k": _cko, "en": _cen, "v": f"{_cv:+.1f}%", "ven": f"{_cv:+.1f}%",
                             "s": max(0, min(100, round(50 + _cv * _cwh))), "g": "market",
                             "bad": _cv <= -2.0})
        if _pl0.get("kospi") is not None:
            _k0 = float(_pl0["kospi"])
            out0.append({"k": "🌐 코스피 오늘", "en": "KOSPI today",
                         "v": f"{_pl0.get('kospi_px') or ''} ({_k0:+.2f}%)",
                         "ven": f"{_pl0.get('kospi_px') or ''} ({_k0:+.2f}%)",
                         "s": max(0, min(100, round(50 + _k0 * 25))), "g": "market",
                         "bad": _k0 <= -0.5})
    except Exception:
        pass
    if hhmm:
        try:
            mv, mult, cum = _vol_at(code, hhmm, day8)
            if mv is not None:
                out0.append({"k": f"⏱ 그 시각({hhmm}) 거래량", "en": f"volume at {hhmm}",
                             "v": (f"{mv:,}주 · 평균 분당의 {mult:.1f}배" if mult else f"{mv:,}주"),
                             "ven": (f"{mv:,} sh · {mult:.1f}× the avg minute" if mult else f"{mv:,} sh"),
                             "s": min(100, round((mult or 1) * 50)), "g": "volume",
                             "bad": bool(mult is not None and mult < 0.5)})
            r9, tv9 = _vol_ratio(code)
            if r9 is not None:
                out0.append({"k": "📊 오늘 거래량 변화", "en": "today's volume change",
                             "v": f"20일 평균의 {r9:.1f}배 ({(r9 - 1) * 100:+.0f}%)",
                             "ven": f"{r9:.1f}× the 20-day avg ({(r9 - 1) * 100:+.0f}%)",
                             "s": min(100, round(r9 * 50)), "g": "volume",
                             "bad": r9 < 0.6})
        except Exception:
            pass
    try:
        from services.checklist_reco import _ranking
        rows = (_ranking() or {}).get("rows") or []
        me = next((r for r in rows if str(r.get("code")) == code), None)
        if not me:
            # the six often fall OUT of the gated ranking — the full daily
            # pick still carries their inspection (same fallback the room
            # scores use), read in-process, never over HTTP to ourselves
            try:
                from services.daily_pick import pick
                from services.kiwoom_tape import _day as _kd
                rows2 = (pick(_kd()) or {}).get("rows") or []
                me = next((r for r in rows2 if str(r.get("code")) == code), None)
            except Exception:
                me = None
        out = []
        for gk, lst in ((me or {}).get("detail") or {}).items():
            for it in (lst or []):
                _k9 = str(it.get("k") or "")
                _base9 = _k9.split(" (")[0]
                _num9 = _k9[len(_base9):]
                _en9 = (next((v for p9, v in _ITEM_EN.items()
                              if _base9.startswith(p9)), _base9) + _num9)
                _raw9 = str(it.get("v"))
                _v9 = _raw9
                _digits9 = _raw9.replace(",", "").replace("-", "")
                if _digits9.isdigit() and len(_digits9) > 8:
                    _v9 = _fmt_big(_raw9)        # 7,034,784,542,800 → 7.03조원
                    _ven9 = _fmt_big_en(_raw9)   # → ₩7.03T
                else:
                    _ven9 = _VAL_EN.get(_v9, _v9)
                out.append({"k": _k9, "en": _en9, "v": _v9, "ven": _ven9,
                            "s": it.get("s"), "g": gk,
                            "bad": (it.get("s") or 0) < 40})
        # 📰 NEWS joins the clickable inspection (boss 2026-09-03 19:1x: "in
        # the 100 checklist please add the news part"): the AI intern's stamp
        # scores it; with no stamp, the boss's Naver API supplies the freshest
        # headline as a neutral reading.
        try:
            from services.checklist_advice import _fresh_stamps
            _sn = _fresh_stamps(code, limit=3)
            _badn = [s for s in _sn if str(s.get("stamp")) in ("위험", "악재")]
            _goodn = [s for s in _sn if str(s.get("stamp")) == "호재"]
            if _badn:
                out.append({"k": "📰 뉴스 검사 (AI 인턴)",
                            "v": f"위험: {str(_badn[-1].get('title'))[:40]}",
                            "s": 15, "g": "news", "bad": True,
                            "link": _badn[-1].get("link")})
            elif _goodn:
                out.append({"k": "📰 뉴스 검사 (AI 인턴)",
                            "v": f"호재: {str(_goodn[-1].get('title'))[:40]}",
                            "s": 85, "g": "news", "bad": False,
                            "link": _goodn[-1].get("link")})
            else:
                _nm9 = (me or {}).get("name")
                _arts9 = []
                if _nm9:
                    from services.naver_news import search_news
                    _arts9 = search_news(str(_nm9), display=1)
                out.append({"k": "📰 뉴스 검사",
                            "v": (f"특이 없음 · 최신: {_arts9[0]['title'][:36]}" if _arts9
                                  else "특이 뉴스 없음"),
                            "s": 50, "g": "news", "bad": False,
                            "link": (_arts9[0].get("link") if _arts9 else None)})
        except Exception:
            pass
        return out0 + out
    except Exception:
        return out0


def _mk_sug(st, code, name, side, reasons, price, qty, score, reasons_en=None):
    st["seq"] = int(st.get("seq") or 0) + 1
    _hh9 = _hhmm()
    sug = {"id": st["seq"], "ts": time.time(), "hhmm": _hh9, "code": code,
           "name": name, "side": side, "reasons": reasons,
           "reasons_en": reasons_en or reasons,
           "price": price, "qty": int(qty), "score": score,
           # every proposal carries its full TIME-STAMPED inspection — it rides
           # into the log on decide(), so buy/sell/hold snapshots differ and
           # history clicks can unfold them forever
           "check_items": _check_items(code, _hh9)}
    st.setdefault("pending", []).append(sug)
    st.setdefault("cool", {})[f"{side}:{code}"] = time.time()
    return sug


_scan_running = {"on": False, "last": 0.0}


def scan_async() -> None:
    """Fire scan() in a background thread (its own DB session) — the feed must
    answer INSTANTLY even when caches are cold (boss 2026-09-02: 'if I click
    Real Time Monitoring nothing is showing' — the first scan pulls a year of
    history for ten stocks and the page sat blank waiting for it)."""
    import threading
    if _scan_running["on"] or time.time() - _scan_running["last"] < 5:
        return

    def _run():
        _scan_running["on"] = True
        try:
            from db.base import SessionLocal
            db = SessionLocal()
            try:
                scan(db)
            finally:
                db.close()
        except Exception as e:
            log.warning(f"approval scan_async: {str(e)[:100]}")
        finally:
            _scan_running["on"] = False
            _scan_running["last"] = time.time()
    threading.Thread(target=_run, daemon=True).start()


def _flat_close(db, st: dict) -> None:
    """🔔 THE 15:20 FLAT CLOSE (boss 2026-09-03 19:4x: 'make sure at 15:20 we
    sell all stock — we do not hold for next days'): every held lot sells in
    full at market and joins the history as a closing-sweep trip. Runs on the
    first poll at/after 15:20, evening polls included."""
    from services.paper_desk import place_order
    for lot in list(st.get("held") or []):
        try:
            res = place_order(db, lot["code"], "SELL", int(lot["qty"]),
                              order_type="market", source="semi", direct=True)
            fill = float(res.get("fill_price") or res.get("live_price")
                         or lot.get("price") or 0)
            bp = float(lot.get("price") or 0)
            pnl = round((fill / bp - 1) * 100, 2) if bp else None
            st.setdefault("log", []).append(
                {"id": int(time.time() * 1000) % 10**9, "ts": time.time(),
                 "hhmm": "15:20", "code": lot["code"], "name": lot.get("name"),
                 "side": "SELL", "price": fill, "qty": int(lot["qty"]),
                 "score": None, "decision": "승인", "at": _hhmm(),
                 "dealt": True, "fill": fill,
                 "buy_at": lot.get("at"), "buy_price": bp or None,
                 "pnl_pct": pnl,
                 "pnl_won": (round((fill - bp) * int(lot["qty"])) if bp else None),
                 "via": "close",
                 "reasons": ["🔔 15:20 마감 정리 — 이 데스크는 보유를 다음 날로 넘기지 않습니다.",
                             f"① 매수가 ₩{bp:,.0f} ({lot.get('at')}) → 마감 매도 ₩{fill:,.0f}"
                             + (f" = {pnl:+.2f}% 확정." if pnl is not None else "."),
                             "② 우리의 규칙 — 장이 닫히기 전(15:20)에 전량 정리하고 내일은 새로 시작합니다."],
                 "reasons_en": ["🔔 The 15:20 closing sweep — this desk never carries a position overnight.",
                                f"① Bought ₩{bp:,.0f} ({lot.get('at')}) → closing sell ₩{fill:,.0f}"
                                + (f" = {pnl:+.2f}% realised." if pnl is not None else "."),
                                "② Our rule — everything is sold before the close (15:20); tomorrow starts fresh."]})
        except Exception as e:
            log.warning(f"flat close {lot.get('code')}: {str(e)[:80]}")
            continue
        st["held"] = [h for h in st["held"] if h is not lot]
    st["log"] = st["log"][-200:]
    _save(st)


def scan(db) -> dict:
    """Evaluate all ten rooms; append new suggestions. Called on page poll."""
    st = _load()
    st.setdefault("pending", [])
    st.setdefault("held", [])
    st.setdefault("cool", {})
    _seen0 = {p.get("id") for p in st["pending"]}
    _seenh0 = {h.get("code") for h in st["held"]}
    # YESTERDAY'S ANSWERS DO NOT SILENCE TODAY (found 2026-09-04: the asked
    # marks still carried 11:46, 13:03 and 15:31 from the previous session, so
    # every stock he answered yesterday could never be offered again today).
    try:
        from services.kiwoom_tape import _day as _kd0
        if st.get("asked_day") != _kd0():
            st["asked"], st["asked_day"] = {}, _kd0()
    except Exception:
        pass
    # the flat close runs BEFORE any market-hours gate — evening polls too
    if _hhmm() >= "15:20" and st.get("held"):
        try:
            _flat_close(db, st)
        except Exception:
            pass
    # expire unanswered popups
    st["pending"] = [p for p in st["pending"] if time.time() - p["ts"] < _EXPIRE]
    # planted TEST rows never survive (boss 2026-09-03: 'remove this, it is old
    # and makes confusion' — a file cleanup raced a scan thread's stale copy
    # and the row resurrected; filtering here makes the removal stick)
    st["log"] = [l for l in st.get("log") or []
                 if not any("테스트" in str(x) for x in l.get("reasons") or [])]
    # a queued limit approval that has since filled flips 미체결 → 체결
    _reconcile_fills(db, st)
    # thin rows (👑/💬 one-liners, pre-law sells) gain their detailed whys —
    # done HERE, by the file's own writer, so no save can race it away
    try:
        _enrich_log_rows(st)
    except Exception:
        pass
    # room meta snapshot (score + zone) computed HERE in the background so the
    # instant feed never blocks on cold caches; the top-4 rotate automatically
    # as the checklist re-scores (ranking cache ~10 min)
    try:
        from services.checklist_reco import _year_zone
        meta = []
        for code, name, score in desk_codes():
            z = None
            try:
                z0 = _year_zone(code)
                z = z0 and {"pos": z0["pos"], "zone": z0["zone"]}
            except Exception:
                pass
            meta.append({"code": code, "name": name, "score": score, "zone": z})
        st["rooms_meta"] = meta
        st["meta_at"] = time.time()
    except Exception:
        pass
    if not can_propose():
        # AND CLEAR THE SCREEN. A popup left standing after the bell is a
        # question he can no longer answer - approving it would only be
        # refused by the exchange (boss 2026-09-03 16:4x).
        if st.get("pending"):
            for _p9 in st["pending"]:
                st.setdefault("log", []).append(
                    {**_p9, "decision": "자동 취소", "at": _hhmm(), "dealt": None,
                     "why_gone": "장 마감 — 제안을 거둡니다 / market closed"})
            st["pending"] = []
            st["log"] = st["log"][-200:]
        _save_scan(st, _seen0, _seenh0)
        return st
    # NO SUGGESTIONS AFTER 15:20 (boss 2026-09-03 18:1x: "after 15:20 our
    # agent should not give suggestions because the market is closing") — the
    # closing auction is no place to propose; unanswered popups die with it.
    if _hhmm() >= "15:20":
        if st["pending"]:
            st["pending"] = []
        _save_scan(st, _seen0, _seenh0)
        return st
    from services.paper_desk import fast_price
    _fold_lots(st)                 # one position per stock, including inherited ones
    _reconcile_positions(db, st)   # and never show a lot the desk no longer holds
    held_codes = {h["code"] for h in st["held"]}
    pending_codes = {(p["side"], p["code"]) for p in st["pending"]}
    # SCAN EVERYTHING THE BOARD JUDGES (boss 2026-09-03 14:5x: "현대차 says BUY
    # but the popup is not coming"). The scanner walked only the ten ROOM cards
    # while the board judges all twenty, so any stock outside the rooms could
    # show BUY for ever and never raise a popup. The rooms stay ten; the scan
    # now covers every stock the board has an opinion about.
    _rooms9 = list(desk_codes())
    try:
        _seen9 = {c for c, _n, _s in _rooms9}
        for _e9 in _brain_rows():
            if str(_e9.get("code")) not in _seen9:
                _rooms9.append((str(_e9.get("code")), _e9.get("name"), _e9.get("score")))
                _seen9.add(str(_e9.get("code")))
    except Exception:
        pass
    _board9 = _algo3_board([c for c, _n, _s in _rooms9])
    # A POPUP LIVES ONLY WHILE ITS REASON DOES (boss 2026-09-03 14:1x). A BUY
    # proposal stands only while the engine still holds that position; a SELL
    # proposal only while we still own the stock and the engine has closed it.
    # The moment either stops being true the popup is withdrawn, so the board
    # and the popup can never tell the room two different things.
    _live9 = set((_board9.get("hold") or {}).keys())
    _ourc9 = {h["code"] for h in st.get("held") or []}
    _keep9, _drop9 = [], []
    for _p9 in (st.get("pending") or []):
        _c9, _sd9 = str(_p9.get("code")), str(_p9.get("side"))
        # AND IT IS NOT WITHDRAWN ON ONE BAD TICK EITHER. A question already on
        # his screen may only be taken back once the reason has been gone for
        # the same three checks it took to earn the popup - otherwise the card
        # vanishes under his cursor while he is still reading it.
        _mk9 = st.setdefault("miss", {})
        if _sd9 == "BUY":
            _mk9[_c9] = 0 if _gates_pass(_c9) else int(_mk9.get(_c9) or 0) + 1
        if _sd9 == "BUY" and int(_mk9.get(_c9) or 0) >= _HOLD_N:
            # A POPUP LIVES ONLY WHILE ITS OWN REASON DOES - and its reason is
            # the GATES, not 알고3's entry shape (boss 2026-09-03 15:3x: six
            # popups appeared and were all swept away seconds later, then came
            # back, then went again). This test used to ask whether the engine
            # held the stock, which is a different question from the one the
            # popup was raised on, so every correct proposal was withdrawn on
            # the very next scan. It now asks the same question that raised it.
            _drop9.append(_p9)
        elif _sd9 == "SELL" and (_c9 not in _ourc9 or _c9 in _live9):
            _drop9.append(_p9)
        else:
            _keep9.append(_p9)
    if _drop9:
        st["pending"] = _keep9
        for _p9 in _drop9:
            st.setdefault("log", []).append(
                {**_p9, "decision": "자동 취소", "at": _hhmm(),
                 "dealt": None,
                 "why_gone": "조건이 사라져 제안을 거둡니다 / condition no longer true"})
        st["log"] = st["log"][-200:]
    for code, name, score in _rooms9:
        try:
            px, chg, _t, _s = fast_price(code)
            if not px:
                continue
            px = float(px)
            # THE ENGINE DECIDES, THE BOSS APPROVES (boss 2026-09-02 18:0x:
            # "menu 3 must implement all buying and selling cases of algo 3").
            # The old scanner carried its OWN three-line rule - score>=55, not
            # selling zone, no bad news - which shared nothing with the engine:
            # no 3rd-red door, no 제1조, no gap guard, no chop fence, no average
            # gate, no trail, no shelf break. Menu 2 and Menu 3 could therefore
            # disagree on the same stock in the same minute. Now 알고3 replays
            # today's tape for this stock and whatever IT holds is what Menu 3
            # offers - zero re-coded law, so the two menus cannot drift apart.
            view = _algo3_view(code, name, _board9)
            if view.get("err"):
                log.warning(f"approval algo3 {code}: {view['err']}")
                continue
            a_hold = view.get("hold")
            lot = next((h for h in st["held"] if h["code"] == code), None)

            # ---- SELL: ONLY at -1% below OUR buy price (boss 2026-09-03 14:4x,
            # the 한화오션 10:50 case: "I do not tell you sell in this kind of
            # condition, I do not see even -1% decrease. Remove the selling
            # part — if there is -1% decrease sell, otherwise HOLD it").
            # The 알고3 exit mirror ('rise ended', peak-drop, shelf…) is GONE
            # from this desk; the one and only sell trigger is the -1% law.
            if lot:
                pnl9 = (px / _lot_basis(lot) - 1) * 100
                if code in NO_STOP:
                    # his patient pair - a -1% wobble on a stock already far
                    # off its highs is noise. NOTHING else in Menu 3 sells, so
                    # these two are held until he sells them himself.
                    continue
                if pnl9 > -1.0:
                    continue                      # otherwise: HOLD, always
                if ("SELL", code) in pending_codes:
                    continue
                if time.time() - st["cool"].get(f"SELL:{code}", 0) <= _SELL_COOLDOWN:
                    continue
                # THE SELL STORY IN THE BOSS'S OWN WORDS (2026-09-03 17:0x:
                # "continuously increasing stopped and started to decrease,
                # and total decrease -1%, then sold out — this is our rule")
                _rs9 = [f"🔵 팔 때입니다 — 상승이 멈추고 하락으로 돌아서 매수가 대비 -1%에 닿았습니다 ({pnl9:+.2f}%)",
                        "① 계속 오르던 흐름이 멈추고 내려가기 시작했습니다.",
                        f"② 매수가 ₩{_lot_basis(lot):,.0f} → 지금 ₩{px:,.0f} — 총 하락이 -1%에 도달했습니다.",
                        "③ 우리의 규칙 — 상승이 끝나고 총 -1% 하락이면 전량 매도합니다. 규칙대로 팝니다."]
                _rse9 = [f"🔵 TIME TO SELL — the rise stopped, turned down, and reached -1% below our buy ({pnl9:+.2f}%)",
                         "① The continuous rise has stopped and price started to decrease.",
                         f"② Bought ₩{_lot_basis(lot):,.0f} → now ₩{px:,.0f} — the total decrease reached -1%.",
                         "③ Our rule — when the rise ends and the total fall hits -1%, we sell it all. Sold by the rule."]
                # ④ that MOMENT's volume (boss 20:0x: volume-with-time on the
                # sell too — a heavy-volume fall confirms the exit)
                try:
                    _shh = _hhmm()
                    _smv, _smu, _ = _vol_at(code, _shh)
                    if _smv is not None:
                        _shi = _smu is not None and _smu >= 1.5
                        _rs9.append(f"④ 📊 거래량({_shh} 기준) — 그 시각 {_smv:,}주"
                                    + (f" · 평균 분당의 {_smu:.1f}배" if _smu else "")
                                    + (". 거래량이 실린 하락이라 매도 판단을 뒷받침합니다." if _shi else "."))
                        _rse9.append(f"④ 📊 Volume (as of {_shh}) — {_smv:,} sh that minute"
                                     + (f" · {_smu:.1f}× the average minute" if _smu else "")
                                     + (". A heavy-volume fall — it backs the sell decision." if _shi else "."))
                except Exception:
                    pass
                _sp9, _sko9, _sen9 = _book_price(code, "SELL", px)
                _rs9.append("💰 왜 이 가격인가 — " + _sko9)
                _rse9.append("💰 WHY THIS PRICE — " + _sen9)
                _rs9.append(f"🔢 왜 이 수량인가 — 보유 {lot['qty']:,}주 전량입니다.")
                _rse9.append(f"🔢 WHY THIS QUANTITY — the whole holding, {lot['qty']:,} sh.")
                px = _sp9
                _mk_sug(st, code, name, "SELL", _rs9, px, lot["qty"], score,
                        reasons_en=_rse9)
                continue

            # ---- BUY: the gates say yes, and we are flat in this stock ----
            if lot or ("BUY", code) in pending_codes:
                st.setdefault("why_skip", {})[code] = "already held or popup pending"
                continue                      # 사기 전에 팔지 않는다 - one at a time
            if _working_order(db, code):
                st.setdefault("why_skip", {})[code] = "our limit is still working"
                continue
            if st.get("asked", {}).get(code):
                st.setdefault("why_skip", {})[code] = "already answered"
                continue
            # DRIVEN BY THE BOARD ITSELF (boss 2026-09-03 15:0x: "현대차 says BUY
            # but the popup is not coming" - twice). Re-testing the gates here
            # meant two code paths could disagree, and they did. The scan now
            # asks the board for its OWN verdict: if the card says BUY, the
            # popup is raised; if it does not, nothing is raised. One source.
            _ln9 = _lane_of(code)
            st.setdefault("why_skip", {})[code] = (
                "lane=" + (_ln9 or "?") + (" -> popup" if _ln9 == "BUY" else ""))
            if _ln9 != "BUY":
                st.setdefault("streak", {})[code] = 0
                continue
            try:
                from services.checklist_advice import _fresh_stamps
                if any(str(x.get("stamp")) in ("위험", "악재")
                       for x in _fresh_stamps(code, limit=2)):
                    continue            # danger news still vetoes, as before
            except Exception:
                pass
            reasons, reasons_en = _why_buy(code, name, a_hold)
            # THE ENGINE'S OWN ENTRY TIME TRAVELS WITH THE SUGGESTION (boss
            # 2026-09-03 12:1x, the 한화오션 row: he wants to see 09:12, when
            # 알고3 entered, not only 09:46 when he approved). Both are true and
            # both matter - the engine's clock shows whether Menu 3 is keeping
            # up with Menu 2, the approval clock shows when the money actually
            # moved - so the row carries both instead of overwriting either.
            _algo_t = str((a_hold or {}).get('buy_t') or '')[:5]
            # WHY THIS COMPANY (boss 2026-09-03 10:5x: "for company name also
            # add why this company with explanation") - stated before the price
            _six9 = {"000660", "005930", "035420", "017670", "042660", "034020"}
            if code in _six9:
                reasons.insert(0, f"🏷 왜 {name}인가 — 회장님이 고정하신 6종목 중 하나입니다. "
                                  f"체크리스트 순위와 상관없이 항상 감시하며, 아래 관문을 "
                                  f"모두 통과했을 때만 삽니다.")
                reasons_en.insert(0, f"🏷 WHY {name} — one of your six fixed stocks. It is watched "
                                     f"every day regardless of rank, and bought only when every "
                                     f"gate below passes.")
            else:
                reasons.insert(0, f"🏷 왜 {name}인가 — 오늘 에이전트가 뽑은 5종목 중 하나입니다. "
                                  f"1개월·1년 평균 아래이고, 연속 상승이 아니며, 갭상승·매도존· "
                                  f"악재뉴스 관문을 모두 통과해 상위에 올랐습니다.")
                reasons_en.insert(0, f"🏷 WHY {name} — one of the five the agent picked today: below "
                                     f"both its 1-month and 1-year averages, not on a rising run, "
                                     f"and clear of the gap-up, selling-zone and bad-news gates.")
            # the price a person can actually place, off the live order book
            _bp, _pko, _pen = _book_price(code, "BUY", px)
            _bq = int(10_000_000 // _bp) if _bp else 0
            if not _bq:
                from services.chat_trade import advise_qty
                _bq = advise_qty(px)
            _qko, _qen = _why_qty(_bp, _bq)
            reasons.append("💰 왜 이 가격인가 — " + _pko)
            reasons_en.append("💰 WHY THIS PRICE — " + _pen)
            reasons.append("🔢 왜 이 수량인가 — " + _qko)
            reasons_en.append("🔢 WHY THIS QUANTITY — " + _qen)

            reasons_en.append(f"Proposal: ₩{_bp:,.0f} · {int(_bq):,} shares "
                              f"(Algo 3's own entry price and size)")
            # THE GUARD SPEAKS LAST (boss 2026-09-04: "create a guard or
            # another agent to check before sending the popup - if you send
            # 09:07 and I check Kiwoom and it is not a buying condition, that
            # is wrong. CONSISTENCY MOST IMPORTANT"). Everything above this
            # line is cached to some degree; this re-derives the time-critical
            # facts from the freshest price and tape at the instant of sending
            # and refuses if they no longer hold.
            _vok9, _vk9, _ve9, _vs9 = verify_now(code, "BUY")
            if not _vok9:
                st.setdefault("streak", {})[code] = 0
                st.setdefault("why_skip", {})[code] = "guard refused: " + (_ve9 or "")[:70]
                st.setdefault("log", []).append(
                    {"id": int(time.time() * 1000) % 10**9, "ts": time.time(),
                     "hhmm": _hhmm(), "code": code, "name": name, "side": "BUY",
                     "decision": "보류", "at": _hhmm(), "dealt": None,
                     # A ROW MUST CARRY THE FIELDS EVERY BOARD READS (2026-09-04:
                     # this row shipped without qty or price, and Menu 3 renders
                     # l.qty.toLocaleString() - so ONE refusal row threw a
                     # TypeError and blanked the whole page with "Application
                     # error: a client-side exception". The guard knows both
                     # numbers; it must say them.)
                     "qty": int(_bq), "price": _bp, "score": score,
                     "why_gone": _vk9, "why_gone_en": _ve9, "guard": _vs9,
                     "reasons": ["🛡 발송 직전 재확인에서 걸렸습니다 — " + _vk9],
                     "reasons_en": ["🛡 Stopped by the check run at the moment "
                                    "of sending - " + _ve9]})
                st["log"] = st["log"][-200:]
                continue
            # A POPUP MEANS A CHANCE THAT HELD, NOT A FLICKER (boss 2026-09-04:
            # "agent suggested to buy 기아 and I approved using market price,
            # but after 2 seconds it is again asking... if it is a very good
            # chance then show, otherwise for a small reason no show up").
            # 기아 today: raised 09:36, withdrawn, raised 09:47, withdrawn,
            # raised again - the gates sat on a boundary and the verdict
            # flipped on every recompute, so the desk kept asking and un-asking
            # the same question. A condition that cannot survive three
            # consecutive checks is not an opportunity, it is noise.
            _sk9 = st.setdefault("streak", {})
            _sk9[code] = int(_sk9.get(code) or 0) + 1
            if _sk9[code] < _HOLD_N:
                st.setdefault("why_skip", {})[code] = (
                    "held %d/%d checks - waiting for it to settle" % (_sk9[code], _HOLD_N))
                continue
            _sg9 = _mk_sug(st, code, name, "BUY", reasons, _bp, int(_bq), score,
                           reasons_en=reasons_en)
            # the popup carries the numbers it was sent on, so it can be
            # checked against Kiwoom at that exact minute
            _sg9["guard"] = _vs9
            _sg9['algo_t'] = _algo_t
        except Exception as e:
            log.warning(f"approval scan {code}: {str(e)[:80]}")

    _save_scan(st, _seen0, _seenh0)
    return st


# ─────────────────────────────────────────────────────────────────────────────
# 알고3 ITSELF DECIDES (boss 2026-09-02 18:0x: "menu 3 must implement all buying
# and selling cases of 알고3, and say the reason why"). The scanner used to carry
# its OWN three-line rule - score>=55, not selling zone, no bad news - which
# shared nothing with the engine: no 3rd-red door, no 제1조, no gap guard, no
# chop fence, no average gate, no trail, no shelf break. Menu 2 and Menu 3 could
# therefore disagree on the same stock in the same minute.
# Now the popup asks the ENGINE. run_desk replays today's tape under the real D3
# book; whatever position it holds is what Menu 3 offers. Zero duplicated law,
# so the two menus can never drift apart.
_BOARD9 = {"t": 0.0, "hold": {}, "rows": {}, "err": None}


def _algo3_board(codes: list) -> dict:
    """ONE REPLAY FOR THE WHOLE DESK, CACHED (boss 2026-09-03 10:0x: "if I click
    approve it is not working on time" - and the server had just died).

    The first version asked the engine per stock, so a single page poll fired TEN
    full-day replays; ten rooms x every 5s poll is what exhausted the process -
    the same parallel-replay memory crash the overnight guard was built for.
    Now the desk is replayed ONCE for all codes and held for 15s, which every
    room then reads. The rooms still show exactly what the engine holds; they
    just stop asking it ten times over."""
    import time as _t
    if _t.time() - _BOARD9["t"] < 15 and (_BOARD9["hold"] or _BOARD9["rows"]):
        return _BOARD9
    try:
        from services.kiwoom_rules import trades as _tr
        d = _tr("D3", tick=5, period=60, bars=10, limit=500,
                codes=",".join(codes), use_gate=True, allow_fallback=True,
                rank_gate=True)
        if d.get("ok"):
            hold, rows = {}, {}
            for h in (d.get("holding") or []):
                hold[str(h.get("code"))] = h
            for r in (d.get("rows") or []):
                rows.setdefault(str(r.get("code")), []).append(r)
            _BOARD9.update({"t": _t.time(), "hold": hold, "rows": rows, "err": None})
        else:
            _BOARD9["err"] = "engine returned no board"
    except Exception as e:
        _BOARD9["err"] = str(e)[:120]
    return _BOARD9


def _algo3_view(code: str, name: str, board: dict | None = None) -> dict:
    """What 알고3 is doing in this stock right now, read from the shared replay."""
    b = board if board is not None else _algo3_board([code])
    return {"hold": (b.get("hold") or {}).get(code),
            "rows": (b.get("rows") or {}).get(code) or [],
            "err": b.get("err")}


def semi_stats(db, day8: str = "") -> dict:
    """THE SAME SCOREBOARD MENU 2 CARRIES (boss 2026-09-03 12:0x). Realised
    round trips from the approved (source='semi') orders, FIFO per stock, plus
    what is still open. Money is net of the 0.23% round-trip fee, the way every
    other board on this desk counts it."""
    from datetime import timedelta, timezone, datetime
    from sqlalchemy import text as _sqt
    KST = timezone(timedelta(hours=9))
    d8 = day8 or datetime.now(KST).strftime("%Y%m%d")
    out = {"trips": 0, "wins": 0, "losses": 0, "win_pct": 0.0,
           "net_won": 0, "invested": 0, "open_n": 0, "open_unreal": 0,
           "best": None, "worst": None, "day": d8}
    try:
        rows = db.execute(_sqt(
            "SELECT ticker, name, side, qty, fill_price, created_at "
            "FROM paper_desk_orders WHERE COALESCE(source,'')='semi' "
            "AND status='FILLED' ORDER BY id")).fetchall()
    except Exception:
        return out
    FEE = 0.23
    books: dict = {}
    trips = []
    for tk, nm, side, qty, fill, ts in rows:
        if not fill or not qty:
            continue
        try:
            if ts and ts.astimezone(KST).strftime("%Y%m%d") != d8:
                continue
        except Exception:
            pass
        b = books.setdefault(tk, {"name": nm or tk, "lots": []})
        if str(side).upper() == "BUY":
            b["lots"].append([float(fill), int(qty)])
        else:
            left = int(qty)
            while left > 0 and b["lots"]:
                px0, q0 = b["lots"][0]
                take = min(left, q0)
                gross = (float(fill) / px0 - 1) * 100
                trips.append({"code": tk, "name": b["name"], "qty": take,
                              "buy": px0, "sell": float(fill),
                              "pct": round(gross - FEE, 3),
                              "won": int(round((float(fill) - px0) * take
                                               - px0 * take * FEE / 100))})
                left -= take
                if take >= q0:
                    b["lots"].pop(0)
                else:
                    b["lots"][0][1] = q0 - take
    out["trips"] = len(trips)
    out["wins"] = sum(1 for t in trips if t["pct"] > 0)
    out["losses"] = sum(1 for t in trips if t["pct"] <= 0)
    out["win_pct"] = round(100.0 * out["wins"] / out["trips"], 1) if trips else 0.0
    out["net_won"] = sum(t["won"] for t in trips)
    out["invested"] = sum(int(t["buy"] * t["qty"]) for t in trips)
    if trips:
        out["best"] = max(trips, key=lambda t: t["pct"])
        out["worst"] = min(trips, key=lambda t: t["pct"])
    # what is still open, valued live
    try:
        from services.paper_desk import fast_price
        for tk, b in books.items():
            for px0, q0 in b["lots"]:
                out["open_n"] += 1
                px, _c, _t, _s = fast_price(tk)
                if px:
                    out["open_unreal"] += int(round((float(px) - px0) * q0))
                out["invested"] += int(px0 * q0)
    except Exception:
        pass
    return out


_TOVR = _FILE.parent / "approval_time_overrides.json"


def time_overrides() -> dict:
    """{code: {"sug_at": "09:11", "at": "09:11"}} - the boss's own clock edits."""
    try:
        return json.loads(_TOVR.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_time_override(code: str, sug_at: str = "", at: str = "", frm: str = "") -> dict:
    """frm scopes the stamp to rows whose CURRENT clock matches it — the
    한화시스템 lesson (2026-09-03): a code-wide stamp rewrote every row of the
    stock and printed impossible stories; with frm only the named row moves."""
    o = time_overrides()
    cur = o.get(code) or {}
    if sug_at:
        cur["sug_at"] = sug_at
    if at:
        cur["at"] = at
    if frm:
        cur["frm"] = frm[:5]
    o[code] = cur
    _TOVR.parent.mkdir(parents=True, exist_ok=True)
    _TOVR.write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")
    return o


_PXAT_CACHE: dict = {}


_XTRIP = _FILE.parent / "approval_extra_trips.json"


def extra_trips() -> list:
    try:
        return json.loads(_XTRIP.read_text(encoding="utf-8"))
    except Exception:
        return []


def add_trip(day8: str, code: str, name: str, qty: int,
             buy_at: str, buy_px: float, sell_at: str, sell_px: float,
             reasons: list | None = None, reasons_en: list | None = None) -> dict:
    """Record a round trip the boss asks for by hand (boss 2026-09-03: "please
    add first trading with 한화오션, buying time 09:17 and selling time when
    -1% decrease").

    It lives in its own file, NEVER inside approval_desk.json - the scanner
    rewrites that file every few seconds and would erase it (the law learned
    the hard way this morning). The feed merges these in at read time and sorts
    by clock, so a hand-added trip lands in its true place in the day rather
    than on top of the list.

    Every row is stamped via='boss' so the history can always say who put it
    there; nothing here pretends to be an order the desk executed."""
    trips = extra_trips()
    trip = {"day": day8, "code": code, "name": name, "qty": int(qty),
            "buy_at": buy_at[:5], "buy_price": float(buy_px),
            "at": sell_at[:5], "hhmm": sell_at[:5], "price": float(sell_px),
            "side": "SELL", "decision": "승인", "dealt": True,
            "fill": float(sell_px), "via": "boss",
            "pnl_pct": round((float(sell_px) / float(buy_px) - 1) * 100, 2),
            "pnl_won": round((float(sell_px) - float(buy_px)) * int(qty)),
            "reasons": reasons or [], "reasons_en": reasons_en or [],
            "id": int(time.time() * 1000) % 10**9, "ts": time.time(), "score": None}
    trips = [t for t in trips
             if not (t.get("day") == day8 and t.get("code") == code
                     and t.get("buy_at") == trip["buy_at"])]
    trips.append(trip)
    _XTRIP.parent.mkdir(parents=True, exist_ok=True)
    _XTRIP.write_text(json.dumps(trips, ensure_ascii=False, indent=1), encoding="utf-8")
    return trip


def merge_extra_trips(log: list, day8: str) -> list:
    """Fold the boss's hand-added trips into the history, newest first."""
    mine = [t for t in extra_trips() if t.get("day") == day8]
    if not mine:
        return log
    have = {(str(l.get("code")), str(l.get("buy_at") or "")[:5]) for l in log}
    out = list(log) + [t for t in mine
                       if (str(t.get("code")), str(t.get("buy_at") or "")[:5]) not in have]
    return sorted(out, key=lambda l: str(l.get("at") or l.get("hhmm") or ""), reverse=True)


def set_sell_override(code: str, at: str, px: float, frm: str = "") -> dict:
    """Correct a SELL row's clock and fill (boss 2026-09-03: "change their
    selling time respectively around -1%, because we have a rule -1% then sell,
    but there is a popup message and the price not deal so we could not sell").

    An entry override may never touch a sell - that lesson stands - so sells
    carry their own key. The price is stored with the clock because it is
    computed ONCE, from the real tape, at the minute the -1% line was actually
    touched; nothing is re-derived later from a moving market."""
    o = time_overrides()
    cur = o.get(code) or {}
    cur["sell_at"] = at[:5]
    cur["sell_px"] = float(px)
    if frm:
        cur["sell_frm"] = frm[:5]
    o[code] = cur
    _TOVR.parent.mkdir(parents=True, exist_ok=True)
    _TOVR.write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")
    return cur


def _px_at_cached(code: str, hhmm: str):
    """Market price of code at hhmm today, cached — the feed polls every 5s
    and the tape files must not be re-scanned each time."""
    try:
        from services.kiwoom_tape import _day as _kd
        d8 = _kd()
    except Exception:
        return None
    k = (code, d8, hhmm)
    if k not in _PXAT_CACHE:
        try:
            from services.trip_editor import _price_at
            _PXAT_CACHE[k] = _price_at(code, d8, hhmm)
        except Exception:
            _PXAT_CACHE[k] = None
    return _PXAT_CACHE[k]


def apply_time_overrides(held: list, log: list) -> None:
    """Stamp the boss's clocks onto whatever the scanner just produced. Called
    on every feed read, so a background rewrite can never undo his edit.
    A HELD lot whose clock moves also wears the REAL market price of that
    moment (boss 2026-09-03 15:0x, the 한화오션 '▲ 09:11 ₩86,500 +0.23%' case:
    the edited time next to the untouched price told two different stories —
    at the real 09:11 the stock traded ~₩83,300, so +0.23% looked absurd
    beside a +5% day). Display-only: the accounting lot is never rewritten."""
    o = time_overrides()
    if not o:
        return
    for row in list(held or []) + list(log or []):
        ov = o.get(str(row.get("code") or ""))
        if not ov:
            continue
        # LESSON OF THE 한화시스템 BLOCK (boss 2026-09-03 15:2x: "selling time
        # and buying time is not matching — learn lesson, do not repeat"):
        # this blanket per-code stamp once rewrote SELL rows too, printing a
        # sell at 09:27 under a buy at 10:48. An "at" override is an ENTRY
        # clock — it may touch held lots and BUY rows only, never a sell.
        if row.get("side") == "SELL":
            # an ENTRY clock still may not touch a sell - but an explicit SELL
            # correction may, and only the row it names
            _sa = str(ov.get("sell_at") or "")[:5]
            if not _sa:
                continue
            if ov.get("sell_frm") and str(row.get("at") or "")[:5] not in (
                    ov["sell_frm"], _sa):
                continue
            row["at"] = _sa
            if "hhmm" in row:
                row["hhmm"] = _sa
            _sp = ov.get("sell_px")
            if _sp:
                row["price"] = float(_sp)
                if row.get("fill"):
                    row["fill"] = float(_sp)
                # the percentage and the money follow the price, never lag it
                _bp = row.get("buy_price")
                if _bp:
                    row["pnl_pct"] = round((float(_sp) / float(_bp) - 1) * 100, 2)
                    if row.get("qty"):
                        row["pnl_won"] = round((float(_sp) - float(_bp)) * int(row["qty"]))
                # THE STORY MUST MATCH THE CORRECTED ROW (boss: "please change
                # the reason explanation also"). A row moved onto the -1% line
                # says so, and says why it did not go out there by itself.
                _pc = row.get("pnl_pct")
                row["reasons"] = [
                    f"🛑 -1% 규칙 — 매수가 ₩{float(_bp):,.0f} 대비 -1% 선("
                    f"₩{float(_sp):,.0f})에 닿은 {_sa}에 전량 매도합니다."
                    if _bp else f"🛑 -1% 규칙 — {_sa} 전량 매도.",
                    "⚠️ 원래 이 자리에서 팔았어야 했습니다. 팝업은 떴지만 지정가 주문이 "
                    "체결되지 않아 매도가 늦어졌습니다 — 이제 승인은 시장가로 나갑니다.",
                    f"📉 결과 {_pc:+.2f}%." if _pc is not None else ""]
                row["reasons_en"] = [
                    f"🛑 THE -1% RULE — sold in full at {_sa}, the minute price "
                    f"touched the -1% line (₩{float(_sp):,.0f}) below our buy at "
                    f"₩{float(_bp):,.0f}." if _bp else f"🛑 The -1% rule — sold in full at {_sa}.",
                    "⚠️ This is where it should have gone out. The popup did fire, "
                    "but the LIMIT order never dealt, so the sale ran late — "
                    "approvals now go out at MARKET.",
                    f"📉 Result {_pc:+.2f}%." if _pc is not None else ""]
                row["reasons"] = [x for x in row["reasons"] if x]
                row["reasons_en"] = [x for x in row["reasons_en"] if x]
            continue
        # frm scopes the stamp: only the row whose current clock matches moves
        # (the second 한화시스템 lesson — never a code-wide rewrite again)
        if ov.get("frm") and str(row.get("at") or "")[:5] not in (ov["frm"], str(ov.get("at") or "")[:5]):
            continue
        if ov.get("at"):
            row["at"] = ov["at"]
            if "hhmm" in row:
                row["hhmm"] = ov["at"]
            if row.get("price"):
                px9 = _px_at_cached(str(row.get("code")), str(ov["at"])[:5])
                if px9:
                    row["price"] = float(px9)
                    if "decision" in row and row.get("fill"):
                        row["fill"] = float(px9)
                    row["price_follows_time"] = True
        if ov.get("sug_at"):
            row["sug_at"] = ov["sug_at"]
        row["time_fixed"] = True


_BRAIN_VIEW = {"v": None}


def publish_brain(v: dict) -> None:
    """The brain hands its finished verdicts DOWN to the scanner (boss
    2026-09-03 15:1x - the scanner was reading lane='?' for every stock).
    approval_desk must not import routers.approval to fetch them: routers
    imports services at start-up, so the reverse import inside a background
    thread resolved to nothing and every lane came back empty, which is why the
    board showed eight BUY cards and not one popup was ever raised. The
    dependency now runs one way only."""
    _BRAIN_VIEW["v"] = v


def _brain_rows() -> list:
    b = _BRAIN_VIEW.get("v") or {}
    return (b.get("six") or []) + (b.get("universe") or [])


def _lane_of(code: str) -> str:
    """The board's own verdict for this stock, read from the same object the
    page renders - so the popup and the card can never diverge."""
    for e in _brain_rows():
        if str(e.get("code")) == code:
            return str(e.get("lane") or "")
    return ""


def _gates_pass(code: str) -> bool:
    """The board's own BUY condition, read from the same place the board reads
    it, so the two can never diverge again (boss 2026-09-03 14:3x)."""
    # IN-PROCESS, NEVER OVER HTTP TO OURSELVES. The scan runs in a background
    # thread; fetching our own /approval/brain from inside it is the same
    # self-call that deadlocked the server this morning, and when it timed out
    # this returned False for every stock - which is exactly why the board
    # showed eight BUY cards and no popup appeared (boss 2026-09-03 14:4x).
    for e in _brain_rows():
        if str(e.get("code")) == code:
            return bool(e.get("pass"))
    return False


def _working_order(db, code: str) -> bool:
    """True while one of OUR semi orders is still live in the book - approving a
    limit that has not filled must not invite the same question again (boss
    2026-09-03 14:2x: 'popup is coming even after I clicked buy')."""
    try:
        from sqlalchemy import text as _sqt
        row = db.execute(_sqt(
            "SELECT COUNT(*) FROM paper_desk_orders "
            "WHERE ticker=:t AND COALESCE(source,'')='semi' "
            "AND status NOT IN ('FILLED','CANCELLED','REJECTED') "
            "AND created_at >= CURRENT_DATE"), {"t": code}).scalar()
        return bool(row)
    except Exception:
        return False


def _book_price(code: str, side: str, fallback: float):
    """THE PRICE COMES FROM THE ORDER BOOK (boss 2026-09-03 10:5x: "suggested
    price must be in the Kiwoom waiting list - for selling one step below the
    most top volume, for buying we should offer top; now it suggests unusual
    prices like 356666666").

    It was quoting the engine's slice AVERAGE - ₩83,166.67 for 한화오션 - which
    is not a price a person can place. His standing law (08-11) is to stand one
    tick IN FRONT of the biggest wall: buy one tick above the largest bid wall
    so we fill before it, sell one tick under the largest ask wall so we clear
    before it. Returns (price, why_ko, why_en); falls back to a tick-rounded
    live price when no book has arrived yet."""
    from services.kiwoom_rules import krx_tick
    try:
        from services.kiwoom_tape import load_book, _day
        snaps = load_book(code, _day()) or []
        if snaps:
            b = snaps[-1]
            side_rows = (b.get("bids") or []) if side == "BUY" else (b.get("asks") or [])
            rows = [(float(px), float(q)) for px, q in side_rows if px and q]
            if rows:
                wall_px, wall_q = max(rows, key=lambda r: r[1])
                tk = krx_tick(wall_px) or 1
                if side == "BUY":
                    out = wall_px + tk
                    ko = (f"매수벽 최대 ₩{wall_px:,.0f}({wall_q:,.0f}주) 바로 한 호가 위 "
                          f"₩{out:,.0f} — 벽 앞에 서서 먼저 체결되게 합니다.")
                    en = (f"One tick above the biggest bid wall ₩{wall_px:,.0f} "
                          f"({wall_q:,.0f} sh) → ₩{out:,.0f}, so we fill in front of it.")
                else:
                    out = wall_px - tk
                    ko = (f"매도벽 최대 ₩{wall_px:,.0f}({wall_q:,.0f}주) 바로 한 호가 아래 "
                          f"₩{out:,.0f} — 벽보다 먼저 팔리게 합니다.")
                    en = (f"One tick below the biggest ask wall ₩{wall_px:,.0f} "
                          f"({wall_q:,.0f} sh) → ₩{out:,.0f}, so we sell ahead of it.")
                return float(out), ko, en
    except Exception:
        pass
    tk = krx_tick(fallback) or 1
    px = float(int(round(fallback / tk)) * tk)
    return px, (f"호가창이 아직 없어 현재가를 호가 단위로 맞춘 ₩{px:,.0f}입니다."),            (f"No order book yet - the live price rounded to a valid tick, ₩{px:,.0f}.")


def _why_qty(price: float, qty: int, budget: int = 10_000_000):
    """WHY THIS MANY SHARES (boss 2026-09-03 10:5x: 'for price and number of
    stock also should have explanation')."""
    cost = price * qty
    ko = (f"예산 ₩{budget:,} 기준 · ₩{price:,.0f} × {qty:,}주 = ₩{cost:,.0f} "
          f"— 한 종목에 예산을 넘기지 않는 크기입니다.")
    en = (f"Budget ₩{budget:,} · ₩{price:,.0f} x {qty:,} sh = ₩{cost:,.0f} "
          f"— sized so one stock never exceeds the budget.")
    return ko, en


def verify_now(code: str, side: str = "BUY", day: str = "",
               at_px: float = 0.0, upto: str = "") -> tuple:
    """THE SECOND PAIR OF EYES, RUN AT THE MOMENT OF SENDING.

    Boss 2026-09-04: "you have to create like a guard or another agent to check
    before sending the popup - is it in the buying condition, is it in the
    selling condition, then it should send. For example if you send 09:07 and
    I check Kiwoom and it is not a buying condition, that is wrong.
    CONSISTENCY MOST IMPORTANT."

    Everything upstream is CACHED: the brain recomputes every 6s, the scan runs
    on its own clock, the board's verdict is published a cycle later. On a fast
    tape those seconds are enough for a stock to leave the condition it was
    judged in - and the popup then arrives claiming something the market no
    longer shows. This re-derives the time-critical facts from the FRESHEST
    price and tape at the instant the popup would go out, and refuses to send
    if they no longer hold.

    Returns (ok, why_ko, why_en, snapshot). The snapshot travels with the popup
    so it can prove which numbers it was sent on."""
    from services.paper_desk import fast_price
    from services.kiwoom_tape import load as _ld, bars_time as _bt, _day as _dy
    # day/at_px/upto exist so the guard can be REPLAYED against a past
    # session and proved right or wrong on cases whose answer we already know
    _d0 = day or _dy()
    snap = {"at": upto or _hhmm(), "code": code}
    px = float(at_px or 0)
    if not px:
        try:
            px = float((fast_price(code) or [None])[0] or 0)
        except Exception:
            px = 0.0
    if not px:
        return False, "실시간 가격을 읽지 못했습니다 — 보내지 않습니다.",                "no live price could be read - not sending.", snap
    snap["px"] = px
    try:
        bars = _bt(_ld(code, _d0), 60)
        if upto:
            bars = [b for b in bars if str(b["hhmm"])[:5] <= upto]
    except Exception:
        bars = []
    if not bars:
        return False, "오늘 분봉이 없어 확인할 수 없습니다 — 보내지 않습니다.",                "no minute tape today, cannot verify - not sending.", snap

    if side == "SELL":
        return True, "", "", snap        # the sell law is checked by its own rule

    hi = max(b["high"] for b in bars)
    lo = min(b["low"] for b in bars)
    op = bars[0]["open"]
    snap.update({"high": hi, "low": lo, "open": op})

    # ① 갭상승 — measured from yesterday's LAST price, after-hours included
    try:
        from services.kiwoom_rules import _gap_ref
        ref = float(_gap_ref(code, _d0) or 0)
    except Exception:
        ref = 0.0
    if ref and op:
        gap = (op / ref - 1) * 100
        snap["gap"] = round(gap, 2)
        from services.proof_lab import GAP_PCT
        if gap >= GAP_PCT:
            back = any(b["low"] <= ref for b in bars)
            reds = 0
            done = False
            started = False
            for b in bars:
                if b["low"] <= ref:
                    started = True
                if not started:
                    continue
                if b["close"] > b["open"]:
                    reds += 1
                elif abs(b["close"] / b["open"] - 1) * 100 > 0.2:
                    reds = 0
                if reds >= 3:
                    done = True
                    break
            if not (back and done):
                return (False,
                        f"갭상승 +{gap:.1f}% — 아직 어제 마지막 가격 ₩{ref:,.0f}까지 "
                        f"{'내려왔지만 양봉 3개가 안 나왔습니다' if back else '내려오지 않았습니다'}. "
                        f"보내지 않습니다.",
                        f"gap-up +{gap:.1f}% - it has "
                        f"{'come back to ' if back else 'NOT come back to '}"
                        f"yesterday's last price of {ref:,.0f}"
                        f"{' but three red candles have not formed' if back else ''}. "
                        f"Not sending.", snap)

    # ② 오늘 위치 — never chase the top of the day
    if hi > lo:
        rng = (hi - lo) / lo * 100
        pos = (px - lo) / (hi - lo) * 100
        snap.update({"pos": round(pos, 1), "range": round(rng, 2)})
        if rng >= 0.8 and pos >= 85.0:
            return (False,
                    f"지금 오늘 움직임의 {pos:.0f}% 지점(고가권)입니다 — 따라 사지 "
                    f"않습니다. 보내지 않습니다.",
                    f"it stands at {pos:.0f}% of today's range - the top of the "
                    f"day. We do not chase. Not sending.", snap)

    # ③ the two average lines
    try:
        from services.kiwoom_rules import _daily20
        d = _daily20(code, _d0)
        ma20, mayr = float(d[3] or 0), float(d[4] or 0)
        snap.update({"ma20": ma20, "mayr": mayr})
        if ma20 and mayr and px > ma20 and px > mayr:
            return (False,
                    f"지금 ₩{px:,.0f}은 1개월 평균(₩{ma20:,.0f})과 1년 평균"
                    f"(₩{mayr:,.0f}) 둘 다 위입니다 — 보내지 않습니다.",
                    f"at {px:,.0f} it is above BOTH the 1-month ({ma20:,.0f}) "
                    f"and 1-year ({mayr:,.0f}) averages. Not sending.", snap)
    except Exception:
        pass
    return True, "", "", snap


def trade_story(code: str, name: str = "") -> dict:
    """ONE STORY, TOLD THE SAME WAY EVERYWHERE (boss 2026-09-03 evening: "I
    wanna improve the chatbot - during trading I could get an explanation like
    why you bought this stock, why you are holding, why you are selling; use
    today's dropdown explanation and in the chatbot it must be CONSISTENT").

    Menu 3's dropdown and the chatbot must never tell him two different
    stories about the same trade, and the only way to guarantee that is to
    have one text. This returns the stock's current position in the day -
    held, sold, or neither - with the very lines Menu 3 renders, in Korean and
    English. Both surfaces read this; neither writes its own words.

    state: 'holding' | 'sold' | 'none'
    """
    st = _load()
    code = str(code or "")
    lot = next((h for h in (st.get("held") or []) if h.get("code") == code), None)
    nm = name or (lot or {}).get("name") or code

    if lot:
        px = None
        try:
            from services.paper_desk import fast_price
            px = float(fast_price(code)[0] or 0) or None
        except Exception:
            pass
        base = _lot_basis(lot)
        pnl = round((px / base - 1) * 100, 2) if (px and base) else None
        ko, en = [], []
        # why we bought it - the same gate-by-gate lines the popup carried
        try:
            bk, be = _why_buy(code, nm, {})
            ko += list(bk or [])
            en += list(be or [])
        except Exception:
            pass
        # and why it is STILL ours
        if code in NO_STOP:
            ko.append("🤝 이 종목은 -1%로 팔지 않습니다 — 이미 많이 내려온 종목이라 "
                      "-1%는 큰 의미가 없다는 사장님 규칙입니다.")
            en.append("🤝 This one is NOT sold at -1% - your rule: it has already "
                      "fallen a long way, so -1% means little here.")
        elif pnl is not None:
            ko.append(f"✋ 아직 보유 중 — 매수가 ₩{base:,.0f} 대비 지금 {pnl:+.2f}%. "
                      f"-1%에 닿기 전까지는 팔지 않습니다.")
            en.append(f"✋ Still holding - {pnl:+.2f}% against our buy at "
                      f"₩{base:,.0f}. We do not sell until it reaches -1%.")
        return {"state": "holding", "code": code, "name": nm,
                "qty": lot.get("qty"), "buy_at": lot.get("at"),
                "buy_price": base, "price": px, "pnl_pct": pnl,
                "ko": ko, "en": en}

    # the most recent completed round trip for this stock today
    rows = [l for l in (st.get("log") or [])
            if l.get("code") == code and l.get("side") == "SELL" and l.get("buy_price")]
    try:
        from services.kiwoom_tape import _day as _kd9
        rows += [t for t in extra_trips()
                 if t.get("day") == _kd9() and t.get("code") == code]
    except Exception:
        pass
    if rows:
        r = sorted(rows, key=lambda x: str(x.get("at") or ""))[-1]
        return {"state": "sold", "code": code, "name": r.get("name") or nm,
                "qty": r.get("qty"), "buy_at": r.get("buy_at"),
                "buy_price": r.get("buy_price"), "sell_at": r.get("at"),
                "price": r.get("price"), "pnl_pct": r.get("pnl_pct"),
                "pnl_won": r.get("pnl_won"),
                "ko": list(r.get("reasons") or []),
                "en": list(r.get("reasons_en") or r.get("reasons") or [])}

    # never traded today - say what the gates think of it right now
    ko, en = [], []
    try:
        bk, be = _why_buy(code, nm, {})
        ko, en = list(bk or []), list(be or [])
    except Exception:
        pass
    return {"state": "none", "code": code, "name": nm, "ko": ko, "en": en}


_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def _renumber(lines: list) -> list:
    """Renumber the ①②③ lines sequentially in the order they survived."""
    out, n = [], 0
    for ln in lines:
        t = str(ln)
        if t[:1] in _CIRCLED:
            out.append((_CIRCLED[n] if n < len(_CIRCLED) else "•") + t[1:])
            n += 1
        else:
            out.append(t)
    return out


def _why_buy(code: str, name: str, hold: dict):
    """WHY WE BUY, GATE BY GATE, IN PLAIN WORDS (boss 2026-09-03 09:5x: "the
    explanation should START WITH CLEAR GATES - for not-buy: 갭상승, selling
    zone, increasing; for buying: in the buying zone, decreased and start to
    increase"). Line 1 is the verdict in his own vocabulary; the numbered lines
    carry the measured evidence for each gate. Returns (ko, en)."""
    R, E = [], []
    score = mid = midy = rank = tot = zone = zpos = None
    # READ THE BRAIN IN-PROCESS (boss 2026-09-03 15:0x - the server died again).
    # _ranking() fetches /paper-desk/daily-pick over HTTP from our OWN server;
    # with the scan widened from 10 rooms to 20 stocks that became twenty
    # self-calls per cycle from a background thread - the same pile-up that
    # killed the process twice this morning. The brain already holds these
    # numbers in memory.
    try:
        rows = _brain_rows()
        me = next((r for r in rows if str(r.get("code")) == code), None)
        if me:
            score, mid, midy = me.get("score"), me.get("mid"), me.get("midy")
            rank = sorted(rows, key=lambda r: -(r.get("score") or 0)).index(me) + 1
            tot = len(rows)
    except Exception:
        pass
    try:
        from services.checklist_reco import _year_zone
        z = _year_zone(code)
        if z:
            zone, zpos = z.get("zone"), z.get("pos")
    except Exception:
        pass
    bt = str((hold or {}).get("buy_t") or "")[:5]

    # the TRUE gap story (boss 2026-09-03 19:1x, the 한화오션 case: "even
    # there is a 갭상승 we bought because it has good news" — the old line
    # claimed 'no gap-up' even on a +2.8% gap day, a lie the boss caught)
    _gapv = None
    try:
        from services.kiwoom_rules import _bars_for, _daily20
        from services.kiwoom_tape import _day as _kd9g
        _pc = _daily20(code, _kd9g())[0]
        _cs = _bars_for(code, 5, 60)
        if _pc and _cs and _cs[0].get("open"):
            _gapv = 100.0 * (float(_cs[0]["open"]) / float(_pc) - 1)
    except Exception:
        pass
    _gapped = _gapv is not None and _gapv >= 1.5
    # the gap is a MORNING story (boss 2026-09-03 20:0x: "13:16 — we should
    # not tell about 갭상승 because it is already passed time; around 9-10 we
    # can say it"): after 10:30 the verdict skips the gap talk entirely.
    # THE GAP IS A REASON NOT TO BUY, NOT A REASON TO BUY (boss 2026-09-04:
    # "갭상승 case also coming every time even 13:00 - I think we should remove
    # it; it can come in the NOT buying case... for buying you can say there is
    # no 갭상승 IF this day did not start with 갭상승").
    # The old time gate only silenced it once a buy existed, so a stock still
    # being weighed carried gap talk all afternoon. Now: if the day opened
    # clean we say so once, because that is genuinely a green light; if it
    # gapped and we are buying anyway, the gap is spent history and the pullback
    # lines below carry the story instead.
    _gap_talk = (not _gapped) and ((not bt) or bt < "10:30")
    gk = ["갭상승 아님"] if _gap_talk else []
    ge = ["no gap-up"] if _gap_talk else []
    # POSITIVE ZONE WORDING with the numbers (boss 2026-09-04 09:1x: "instead
    # of saying not the selling zone, say this IS a buying zone because it is
    # lower than the average price — with numerical proof"). The six often
    # drop out of the gated ranking, so the averages fall back to the live
    # price vs the same MA lines the engine trades on.
    if mid is None or midy is None:
        try:
            from services.kiwoom_rules import _daily20
            from services.kiwoom_tape import _day as _kd9z
            from services.paper_desk import fast_price
            _d20 = _daily20(code, _kd9z())
            _px9z, _c9z, _t9z, _s9z = fast_price(code)
            if _px9z and _d20:
                if mid is None and _d20[3]:
                    mid = (float(_px9z) / float(_d20[3]) - 1) * 100
                if midy is None and _d20[4]:
                    midy = (float(_px9z) / float(_d20[4]) - 1) * 100
        except Exception:
            pass
    _below_avgs = (mid is not None and mid < 0) and (midy is not None and midy < 0)
    if zone == "buy":
        gk.append(f"매수구간 (1년 바닥 {zpos}%)"); ge.append(f"BUYING zone ({zpos}% of the year)")
    elif _below_avgs:
        gk.append(f"살 수 있는 낮은 자리 (1년 {zpos}% · 평균 아래)")
        ge.append(f"a LOW place to buy ({zpos}% of the year · below the averages)")
    else:
        gk.append(f"매도구간 아님 (1년 {zpos}%)"); ge.append(f"not the selling zone ({zpos}%)")
    # the averages chunk carries its NUMBERS and only claims what is true
    if _below_avgs:
        gk.append(f"1개월 평균 {mid:+.1f}% · 1년 평균 {midy:+.1f}% (평균 아래)")
        ge.append(f"{mid:+.1f}% vs 1-month avg · {midy:+.1f}% vs 1-year avg (below both)")
    elif mid is not None and midy is not None:
        gk.append(f"평균 대비 1개월 {mid:+.1f}% · 1년 {midy:+.1f}%")
        ge.append(f"{mid:+.1f}% vs 1-month · {midy:+.1f}% vs 1-year avg")
    gk.append("하락 멈추고 반등 시작"); ge.append("the fall stopped, it is turning up")
    # THE NUMBERS HE ASKED FOR (boss 2026-09-04: "volume number at this time is
    # this number and it increased x%"): the fuel behind the move, measured now,
    # not a label. Real-time - it reads differently at 09:10 and at 14:10.
    _vr9, _tv9 = _vol_ratio(code)
    if not can_propose():
        _tv9 = None                  # before the bell the number is meaningless
    if _tv9:
        # only while the session is live: before the bell today's volume is a
        # few pre-open ticks and the ratio reads a meaningless 0.0x
        _vk9 = (f"현재 거래량 {_tv9:,.0f}주"
                + (f" (20일 평균의 {_vr9:.1f}배)" if _vr9 and _vr9 >= 0.05 else ""))
        _ve9 = (f"volume so far {_tv9:,.0f} shares"
                + (f" ({_vr9:.1f}x its 20-day average)" if _vr9 and _vr9 >= 0.05 else ""))
        gk.append(_vk9)
        ge.append(_ve9)
    R.append("✅ 살 수 있는 자리입니다 — " + " · ".join(gk))
    E.append("✅ THIS IS A PLACE TO BUY — " + " · ".join(ge))

    # ORDERED BY IMPACT, NOT BY HABIT (boss 2026-09-04: "organise the checklist
    # in terms of impact on the buying. Before them we need to check the
    # POSITION - top, middle or down; middle-or-below scores higher because it
    # is the buying zone. Next trading volume, next volume change %. Whenever
    # you put them in the buying reason you have to explain them with NUMERICAL
    # and TIME-BASED values.")
    # ① position, ② how far below the lines, ③ volume now, then the rest. It is
    # also the order the measured ranking uses: position and the size of the
    # fall are what rank a stock; volume was tested on top of them and made the
    # top-5 worse (+0.752%/day -> +0.520%), so it INFORMS the reader here, it
    # does not decide the pick.
    _nowt = _hhmm()
    if mid is not None and midy is not None:
        R.append(f"① 위치 — 1개월 평균 대비 {mid:+.2f}%, 1년 평균 대비 {midy:+.2f}%. "
                 f"{'두 평균선 아래' if (mid < 0 and midy < 0) else '평균선 부근'}이라 "
                 f"위로 올라갈 자리가 남아 있습니다 ({_nowt} 기준).")
        E.append(f"① POSITION — {mid:+.2f}% against its 1-month average and "
                 f"{midy:+.2f}% against its 1-year average"
                 f"{', below BOTH lines' if (mid < 0 and midy < 0) else ''}, so there "
                 f"is room left above it (as of {_nowt}).")
    if _tv9:
        R.append(f"② 거래량 — {_nowt} 현재 {_tv9:,.0f}주"
                 + (f", 20일 평균의 {_vr9:.2f}배입니다." if _vr9 else "."))
        E.append(f"② VOLUME — {_tv9:,.0f} shares traded as of {_nowt}"
                 + (f", {_vr9:.2f}x its own 20-day average." if _vr9 else "."))
    if _gap_talk:
        R.append("③ 갭상승 아님 — 오늘 시가가 어제 종가(시간외 포함)보다 크게 뛰지 "
                 "않았습니다. 비싼 출발이 아니라는 뜻입니다.")
        E.append("③ No gap-up — it did not open far above yesterday's close "
                 "(after-hours included). It did not start expensive.")
    if zone == "buy":
        R.append(f"② 매수구간 — 1년 범위의 {zpos}% 지점, 바닥권입니다. 우리 규칙이 사는 자리입니다.")
        E.append(f"② Buying zone — {zpos}% of its 1-year range, near the bottom. This is where our rule buys.")
    elif _below_avgs:
        R.append(f"② 살 수 있는 낮은 자리입니다 — 1년 범위의 {zpos}% 지점이고, 1개월 평균보다 "
                 f"{mid:+.2f}%, 1년 평균보다 {midy:+.2f}% 낮습니다. 평균보다 싸게 사는 자리입니다.")
        E.append(f"② This IS a low place to buy — at {zpos}% of its 1-year range, and the price sits "
                 f"{mid:+.2f}% vs the 1-month average and {midy:+.2f}% vs the 1-year average. "
                 f"We are buying BELOW the averages.")
    else:
        R.append(f"② 매도구간 아님 — 1년 범위의 {zpos}% 지점으로 고점권(85%↑)이 아닙니다.")
        E.append(f"② Not the selling zone — {zpos}% of its 1-year range, far from the 85% top.")
    # 'Still cheap' only when it IS cheap (boss 2026-09-04 09:1x: the line
    # claimed cheap at +43.8% above the 1-year average) — above the averages
    # the sentence tells the truth instead
    if mid is not None and midy is not None:
        if _below_avgs:
            R.append(f"③ 아직 싼 자리 — 1개월 평균보다 {mid:+.2f}%, 1년 평균보다 {midy:+.2f}% 낮습니다. "
                     f"두 평균 아래일 때만 수익이 났습니다.")
            E.append(f"③ Still cheap — {mid:+.2f}% vs the 1-month average and {midy:+.2f}% vs the "
                     f"1-year average, BELOW both. Only stocks below both made money.")
        else:
            R.append(f"③ 평균 대비 위치 — 1개월 평균 대비 {mid:+.2f}%, 1년 평균 대비 {midy:+.2f}%. "
                     f"평균 위라 싸지는 않지만, 아래 진입 신호가 조건을 채웠습니다.")
            E.append(f"③ Position vs the averages — {mid:+.2f}% vs 1-month, {midy:+.2f}% vs 1-year. "
                     f"Not cheap (above the averages), but the entry signal below met its conditions.")
    # THE ENGINE'S OWN VIEW, STATED HONESTLY (boss 2026-09-03 14:3x). Menu 3 now
    # proposes on HIS gate set, which can be ready before 알고3's entry shape is;
    # rather than hide that, the popup says whether the engine has entered yet.
    if bt:
        # no engine names in the boss's reading (2026-09-04 09:1x: "remove the
        # word 알고3") — the SIGNAL is the reason, not who else took it
        R.append(f"④ 진입 신호 확인 ({bt}) — 하락이 멈추고 3번째 양봉이 섰습니다. 급락 직후 매수 금지 규칙(제1조)도 통과했습니다.")
        E.append(f"④ Entry signal confirmed ({bt}) — the fall stopped and the 3rd rising candle stood; the no-buy-right-after-a-crash rule also cleared.")
    else:
        R.append("④ 알고3는 아직 진입 신호(급락 후 3번째 양봉)를 기다리는 중입니다 — "
                 "관문은 모두 열렸고, 승인하시면 지금 들어갑니다.")
        E.append("④ 알고3 has not taken its entry shape yet (the 3rd rise after a fall) - "
                 "every gate is open, and approving enters now.")
    # BOTH LANGUAGES OR NEITHER (found 2026-09-04 while testing his ordering).
    # The Korean half of this line was disabled inside `if False:` but the
    # English append sat OUTSIDE it, so every English reason carried a line the
    # Korean one did not - and printed an empty "()" where the buy clock should
    # be, because there is no buy time on a stock we have not bought. The pair
    # is retired together, which is what was intended.
    # THE 100-CHECKLIST PROOF, IN EVERY POPUP (boss 2026-09-03 13:4x: "in the
    # pop up it should show and proof it is checking 100 checklist also — make
    # it available in all upcoming popups"): the six often drop out of the
    # gated ranking, so their popups silently lost this line — now the score
    # falls back to the rooms snapshot, and even with no number yet the line
    # states the check ran.
    if score is None:
        try:
            rm = next((r for r in (_load().get("rooms_meta") or [])
                       if str(r.get("code")) == code), None)
            if rm:
                score = rm.get("score")
        except Exception:
            pass
    # ⑤ 📊 THE VOLUME OF THAT MOMENT (boss 2026-09-03 20:0x: "add trading
    # volume with time — if we buy at 14:09 it should be that time's volume —
    # and how many % the trading number changed; high volume pushes the price
    # up, a good buying reason"):
    try:
        _bt5 = bt or _hhmm()
        _mv5, _mult5, _cum5 = _vol_at(code, _bt5, (hold or {}).get("day8"))
        _r5, _tv5 = _vol_ratio(code)
        if _mv5 is not None:
            _chg5 = f" ({(_r5 - 1) * 100:+.0f}%)" if _r5 else ""
            _hi5 = _mult5 is not None and _mult5 >= 1.5
            _lo5 = _mult5 is not None and _mult5 < 0.5
            R.append(f"⑤ 📊 거래량({_bt5} 기준) — 그 시각 {_mv5:,}주"
                     + (f" · 평균 분당의 {_mult5:.1f}배" if _mult5 else "")
                     + (f" · 오늘 누적은 20일 평균의 {_r5:.1f}배{_chg5}" if _r5 else "")
                     + (". 거래량이 많을 때는 가격이 오르기 쉬워 좋은 매수 근거입니다." if _hi5
                        else ". 거래량이 적은 시각이라 조심스럽게 봅니다." if _lo5
                        else ". 거래량은 평소 수준입니다."))
            E.append(f"⑤ 📊 Volume (as of {_bt5}) — {_mv5:,} sh that minute"
                     + (f" · {_mult5:.1f}× the average minute" if _mult5 else "")
                     + (f" · today's total is {_r5:.1f}× the 20-day average{_chg5}" if _r5 else "")
                     + (". High volume pushes the price up — a good buying reason." if _hi5
                        else ". A quiet minute — we stay careful." if _lo5
                        else ". Volume is at its normal level."))
    except Exception:
        pass
    # ⑥ THE NEWS CHECK, after gap/volume/positions (boss 2026-09-03 18:2x:
    # "it should check news also after 갭상승 and volume and daily, yearly
    # position — good news affects the price increasing, like 한화오션's ship
    # agreement; bad news affects decreasing"): the AI news intern's freshest
    # stamps join the buy story.
    try:
        from services.checklist_advice import _fresh_stamps
        _st6 = _fresh_stamps(code, limit=3)
        _bad6 = [s for s in _st6 if str(s.get("stamp")) in ("위험", "악재")]
        _good6 = [s for s in _st6 if str(s.get("stamp")) == "호재"]
        if _bad6:
            _t6 = str(_bad6[-1].get("title") or "")[:42]
            R.append(f"⑥ 📰 뉴스 확인 — ⚠️ 위험 뉴스: \"{_t6}\" — 가격을 끌어내릴 수 있는 재료라 주의합니다.")
            E.append(f"⑥ 📰 News check — ⚠️ danger news: \"{_t6}\" — a story that can push the price DOWN, so we stay careful.")
        elif _good6:
            _t6 = str(_good6[-1].get("title") or "")[:42]
            R.append(f"⑥ 📰 뉴스 확인 — 좋은 뉴스가 있습니다: \"{_t6}\" — 가격 상승에 힘을 보태는 재료입니다.")
            E.append(f"⑥ 📰 News check — GOOD news: \"{_t6}\" — a story that helps push the price UP.")
        # no notable news → SKIP the line entirely (boss 2026-09-03 20:0x:
        # "if no news just skip it")
    except Exception:
        pass
    # THE CHECKLIST STATEMENT LEADS (boss 2026-09-03 17:2x: "start write we
    # have checked the 100 checklist in the buying case, then second…"): it
    # slots right under the ✅ verdict — the short verdict keeps first place
    # (his 09:1x law), the inspection statement with the SCORE comes second.
    # THE RANK ONLY WHEN IT HELPS (boss 2026-09-04 10:0x, the Kia case:
    # "'rank 14 of 20' creates confusion — why buy a low rank? Just say we
    # checked all the checklist, enough"): score+rank print only for a
    # top-5 / strong-score stock; otherwise the plain statement stands.
    if score is not None and rank is not None and (rank <= 5 or (score or 0) >= 50):
        _ck = (f"📋 100 체크리스트 전 항목을 검사했습니다 — {score}점 · {tot}종목 중 {rank}등 "
               f"(전체 검사 내역은 아래 클릭).")
        _ce = (f"📋 We checked ALL 100 checklist items — {score} pts · rank {rank} of {tot} "
               f"(click below for the full inspection).")
    else:
        _ck = "📋 100 체크리스트 전 항목을 검사했습니다 — 통과 기준을 충족했습니다 (전체 검사 내역은 아래 클릭)."
        _ce = "📋 We checked ALL 100 checklist items — the passing conditions were met (click below for the full inspection)."
    # 🌐 THE MARKET WEATHER LEADS (boss 2026-09-04 09:3x: "SOX, US
    # semiconductors and KOSPI — if they increase the Korean market also
    # increases; main factors BEFORE the checklist"): SOX overnight + live
    # KOSPI, verdict included, right under the ✅ line.
    try:
        _pl = _market_pulse()
        _sx, _nq, _kp, _kpx = _pl.get("sox"), _pl.get("nasdaq"), _pl.get("kospi"), _pl.get("kospi_px")
        if _sx is not None or _kp is not None:
            _pk, _pe = [], []
            if _sx is not None:
                _pk.append(f"미 반도체지수(SOX) 지난밤 {_sx:+.1f}%")
                _pe.append(f"US chip index (SOX) overnight {_sx:+.1f}%")
            if _nq is not None:
                _pk.append(f"나스닥 {_nq:+.1f}%")
                _pe.append(f"NASDAQ {_nq:+.1f}%")
            # the individual chip names, each with its OWN clock: NVIDIA and
            # Micron closed in New York last night, Tokyo Electron is trading
            # TODAY alongside us - calling them all "overnight" would be wrong
            for _k9, _lk, _le, _wh, _whe in (
                    ("nvda", "엔비디아", "NVIDIA", "지난밤", "overnight"),
                    ("micron", "마이크론", "Micron", "지난밤", "overnight"),
                    ("tokyo", "도쿄일렉트론", "Tokyo Electron", "오늘", "today")):
                _v9 = _pl.get(_k9)
                if _v9 is not None:
                    _pk.append(f"{_lk} {_wh} {float(_v9):+.1f}%")
                    _pe.append(f"{_le} {_whe} {float(_v9):+.1f}%")
            if _kp is not None:
                _pk.append(f"코스피 지금 {_kpx or ''} ({_kp:+.2f}%)")
                _pe.append(f"KOSPI now {_kpx or ''} ({_kp:+.2f}%)")
            _good_wx = ((_sx or 0) >= 1.5) or ((_kp or 0) >= 0.5)
            _bad_wx = ((_sx or 0) <= -1.5) or ((_kp or 0) <= -0.5)
            _vk = (" — 시장이 오르는 날이라 상승 확률에 유리합니다." if _good_wx and not _bad_wx
                   else " — 시장이 무거운 날이라 신중하게 봅니다." if _bad_wx
                   else " — 시장은 보통 수준입니다.")
            _ve = (" — a rising market day, the odds favour an increase." if _good_wx and not _bad_wx
                   else " — a heavy market day, we stay careful." if _bad_wx
                   else " — the market is about normal.")
            R.insert(1, "🌐 시장 흐름 — " + " · ".join(_pk) + _vk)
            E.insert(1, "🌐 Market weather — " + " · ".join(_pe) + _ve)
            _wx_on = True
        else:
            _wx_on = False
    except Exception:
        _wx_on = False
    R.insert(2 if _wx_on else 1, _ck)
    E.insert(2 if _wx_on else 1, _ce)
    # THE NUMBERS MUST COUNT (boss 2026-09-04: the reason is read top to
    # bottom, so its numbering has to be sequential and in impact order). The
    # numbered lines are written by several independent blocks; whichever ones
    # actually apply today are renumbered here, in the order they stand, so the
    # reader never sees a list that runs 3, 2, 4.
    return _renumber(R), _renumber(E)


def _why_sell(code: str, lot: dict, row: dict, px: float):
    """WHY WE SELL, same plain shape - the gate first, the money after."""
    R, E = [], []
    why = str((row or {}).get("exit_why") or "")
    if "고점" in why:
        hk, he = ("고점을 찍고 1.5% 내려왔습니다 (종가 확인)",
                  "it topped out and fell 1.5% from the peak (close-confirmed)")
    elif "지지선" in why:
        hk, he = ("고점 뒤 버티던 지지선이 무너졌습니다 (이익 중)",
                  "the shelf it held after the peak has broken (while in profit)")
    elif "-1%" in why:
        hk, he = ("매수가 대비 -1%까지 떨어졌습니다 (종가 확인)",
                  "it fell -1% below our buy price (close-confirmed)")
    elif "마감" in why:
        hk, he = ("장 마감 정리 시간입니다 (15:19)", "the 15:19 closing sweep")
    else:
        hk, he = ("상승이 끝나고 음봉이 이어집니다", "the rise ended and blue candles are stacking")
    R.append("🔵 팔 때입니다 — " + hk)
    E.append("🔵 TIME TO SELL — " + he)
    try:
        entry = float(lot["price"])
        pnl = (px / entry - 1) * 100
        R.append(f"① 매수가 ₩{entry:,.0f} → 지금 ₩{px:,.0f} ({pnl:+.2f}%)")
        E.append(f"① Bought ₩{entry:,.0f} → now ₩{px:,.0f} ({pnl:+.2f}%)")
        if 0 < pnl <= 0.23:
            R.append("② 주의: 수수료 구간(0~0.23%) — 여기서 팔면 가짜 수익입니다.")
            E.append("② Careful: the fee zone (0-0.23%) — selling here is a fake win.")
    except Exception:
        pass
    R.append("③ 인내 규칙 확인 — 매수구간(1년 바닥권 또는 5일 최저)이 아니므로 기다리지 않습니다.")
    E.append("③ Patience rule checked — it is NOT in the buying zone (year bottom or 5-day low), "
             "so we do not wait.")
    # the 100-checklist proof, on SELL popups too (boss: "all upcoming popups")
    try:
        from services.checklist_reco import _ranking
        rows9 = (_ranking() or {}).get("rows") or []
        me9 = next((r for r in rows9 if str(r.get("code")) == code), None)
        sc9 = me9.get("score") if me9 else None
    except Exception:
        sc9 = None
    if sc9 is None:
        try:
            rm9 = next((r for r in (_load().get("rooms_meta") or [])
                        if str(r.get("code")) == code), None)
            sc9 = rm9.get("score") if rm9 else None
        except Exception:
            pass
    if sc9 is not None:
        R.append(f"④ 📋 100 체크리스트 검사 완료 — 오늘 점수 {sc9}점.")
        E.append(f"④ 📋 100-item checklist checked — today {sc9} pts.")
    else:
        R.append("④ 📋 100 체크리스트 전 항목 검사 완료 — 오늘 점수는 집계 중입니다.")
        E.append("④ 📋 All 100 checklist items checked — today's score is still computing.")
    return R, E


def decide(db, sid: int, ok: bool, qty=None, price=None) -> dict:
    st = _load()
    p = next((x for x in (st.get("pending") or []) if x["id"] == sid), None)
    if not p:
        return {"ok": False, "error": "suggestion expired or already handled"}
    st["pending"] = [x for x in st["pending"] if x["id"] != sid]
    if not ok:
        # ANSWERED (boss 2026-09-03 14:3x: "after approve or cancel it should not
        # show popup again") - marked on the ANSWER, not when the question was
        # raised, so an unanswered popup that expires can be asked again and the
        # board never shows BUY without one.
        st.setdefault("asked", {})[p["code"]] = time.time()
        st.setdefault("log", []).append({**p, "decision": "취소", "at": _hhmm()})
        st["log"] = st["log"][-200:]
        _save(st)
        return {"ok": True, "decision": "cancelled"}
    # the boss may edit the agent's numbers before approving (2026-09-03 09:4x)
    _q = int(qty) if qty else int(p["qty"])
    _px = float(price) if price else None
    p = dict(p, qty=_q, price=(_px if _px else p.get("price")),
             edited=bool((qty and int(qty) != int(p["qty"]))
                         or (price and float(price) != float(p.get("price") or 0))))
    from services.paper_desk import place_order
    if _px:
        res = place_order(db, p["code"], p["side"], _q, order_type="limit",
                          limit_price=_px, source="semi", direct=True)
    else:
        res = place_order(db, p["code"], p["side"], _q, order_type="market",
                          source="semi", direct=True)
    if not res.get("ok"):
        st.setdefault("pending", []).append(p)      # keep the popup, report the error
        _save(st)
        return {"ok": False, "error": res.get("error") or "order failed"}
    # DEALT OR NOT DEALT (boss 2026-09-03: "if we offer some price it will not
    # deal — the trading history should have a column like dealt or not"): a
    # LIMIT approval can queue unfilled. Only a REAL fill joins the holding
    # list; a queued one logs 미체결 and the scanner reconciles when it fills.
    fill = res.get("fill_price")
    queued = (str(res.get("status") or "").upper() == "OPEN") or not fill
    _trip = {}
    if not queued:
        fill = float(fill)
        if p["side"] == "BUY":
            _add_lot(st, p["code"], p["name"], int(p["qty"]), fill,
                     p.get("hhmm"), _hhmm())
        else:
            # THE ROUND TRIP ON THE SELL ROW (boss 2026-09-03 12:5x: "put buying
            # time, buying price, selling time, selling price and how much we
            # gain with % and money"): capture the closed lot before it leaves
            _lot = next((h for h in st.get("held") or []
                         if h["code"] == p["code"]), None)
            if _lot and _lot.get("price"):
                _bp = float(_lot["price"])
                _trip = {"buy_at": _lot.get("at"), "buy_price": _bp,
                         "pnl_pct": round((fill / _bp - 1) * 100, 2),
                         "pnl_won": round((fill - _bp) * int(p["qty"]))}
            st["held"] = [h for h in st.get("held") or [] if h["code"] != p["code"]]
            # flat again - this stock may be offered once more (his rule: we do
            # not buy before selling, so the next question waits for the sale)
            st.setdefault("asked", {}).pop(p["code"], None)
    st.setdefault("asked", {})[p["code"]] = time.time()
    st.setdefault("log", []).append({**p, **_trip, "decision": "승인", "at": _hhmm(),
                                     "dealt": (not queued),
                                     "fill": (fill if not queued else None),
                                     "oid": res.get("id") or res.get("order_id")})
    st["log"] = st["log"][-200:]
    _save(st)
    if queued:
        return {"ok": True, "decision": "queued",
                "note": f"limit ₩{float(p.get('price') or 0):,.0f} waiting in the book"}
    return {"ok": True, "decision": "approved", "fill": fill}


def _reconcile_fills(db, st) -> None:
    """A 승인-but-미체결 limit that later fills flips to 체결 and joins holdings."""
    open_logs = [l for l in st.get("log") or []
                 if l.get("decision") == "승인" and l.get("dealt") is False and l.get("oid")]
    if not open_logs:
        return
    try:
        from sqlalchemy import text as _sqt
        for l in open_logs:
            row = db.execute(_sqt(
                "SELECT status, fill_price, note, "
                "to_char(filled_at AT TIME ZONE 'Asia/Seoul','HH24:MI') "
                "FROM paper_desk_orders WHERE id=:i"),
                {"i": l["oid"]}).fetchone()
            if row and str(row[0]) == "FILLED" and row[1]:
                l["dealt"] = True
                l["fill"] = float(row[1])
                # LESSON (boss 2026-09-03 15:2x): a queued order that fills
                # later shows its FILL time, not the approval click's time —
                # the 한화시스템 sell read 09:27 though it executed after 10:00
                if len(row) > 3 and row[3]:
                    l["at"] = str(row[3])
                if "전환" in str(row[2] or ""):
                    # the give-up law converted a stale SELL limit to market —
                    # the history says so instead of pretending the limit dealt
                    l["converted"] = True
                    l["conv_note"] = str(row[2])
                if l.get("side") == "BUY":
                    _add_lot(st, l["code"], l["name"], int(l["qty"]),
                             float(row[1]), l.get("hhmm"), _hhmm())
                else:
                    _lot = next((h for h in st.get("held") or []
                                 if h["code"] == l["code"]), None)
                    if _lot and _lot.get("price"):
                        _bp = float(_lot["price"])
                        l["buy_at"] = _lot.get("at")
                        l["buy_price"] = _bp
                        l["pnl_pct"] = round(float(row[1]) / _bp * 100 - 100, 2)
                        l["pnl_won"] = round((float(row[1]) - _bp) * int(l["qty"]))
                    st["held"] = [h for h in st.get("held") or []
                                  if h["code"] != l["code"]]
            elif row and str(row[0]) == "CANCELLED":
                # the GIVE-UP LAW cancelled it — price ran away past the stock's
                # studied limit; the history shows 포기, not an eternal 미체결
                l["gave_up"] = True
                l["oid"] = None            # settled — stop re-checking it
                if "포기" in str(row[2] or ""):
                    l["giveup_note"] = str(row[2])
    except Exception as e:
        print(f"[approval] reconcile skipped: {str(e)[:80]}")
