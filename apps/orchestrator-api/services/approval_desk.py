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
# _EXPIRE is GONE, and the name is kept only to say so: popups used to die ten
# minutes after they were raised, and a proposal made while he was away from the
# desk was over before he saw it. Nothing expires by time now - a card leaves
# only by his answer or by the closing bell (boss 2026-09-07, re-confirmed
# 2026-09-09: "the popup should stay until we close or exit or approve or
# cancel").
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
        st.pop("_desk_closed", None)
        _save(st)
        return
    # 1. asked marks merge, and a mark made during the scan wins — BUT ONLY
    #    TODAY'S MARKS (boss 2026-09-07: "현대로템 and 한국전력 pass every gate
    #    and the popup is not coming"). The daily reset above clears yesterday's
    #    answers; this merge then read them straight back off disk and put them
    #    all back, so 09-03's twenty answers had been silencing the desk ever
    #    since. Every one of the twenty board stocks counted as "already
    #    answered": the scanner skipped them all, and the board's five seats
    #    had nobody eligible to fill them, which is why gate-passing stocks
    #    were told they lost a competition among 0 candidates. An answer is
    #    only an answer for the day it was given.
    merged = dict(st.get("asked") or {})
    _cur9 = dict(cur.get("asked") or {})
    _today9 = None
    try:
        from services.kiwoom_tape import _day as _kd9
        _today9 = str(_kd9())
    except Exception:
        pass
    if _today9 and str(cur.get("asked_day") or "") != _today9:
        _cur9 = {}                      # disk still holds another day's answers
    merged.update(_cur9)
    if _today9:
        import datetime as _dt9
        def _mark_today(_ts):
            try:
                return _dt9.datetime.fromtimestamp(float(_ts)).strftime("%Y%m%d") == _today9
            except Exception:
                return False
        merged = {c: t for c, t in merged.items() if _mark_today(t)}
    st["asked"] = merged
    if _today9:
        st["asked_day"] = _today9
    # 2. a popup that vanished from disk during the scan was ANSWERED - drop it
    live = {p.get("id") for p in (cur.get("pending") or [])}
    st["pending"] = [p for p in (st.get("pending") or [])
                     if (p.get("id") not in seen_ids) or (p.get("id") in live)]
    # AND A POPUP THAT APPEARED WHILE THE SCAN WAS THINKING MUST SURVIVE IT.
    # The filter above only preserves cards the scan itself remembered, so any
    # card written to disk during the scan - decide() putting one back when an
    # order fails, or any other path - was silently erased on the next write.
    # Caught 2026-09-07 by a lifecycle test: a card was gone 12 seconds after
    # it appeared, on the very day the rule became "it stays until he answers".
    # Same fault as the holdings race, in the third and last place it lived.
    _have9 = {p.get("id") for p in st["pending"]}
    for _p9 in (cur.get("pending") or []):
        if _p9.get("id") not in _have9 and _p9.get("id") not in seen_ids:
            st["pending"].append(_p9)
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
    # a lot the scanner itself closed this pass (desk went flat — see
    # _reconcile_positions) must NOT come back from the disk copy
    closed9 = set(st.pop("_desk_closed", None) or [])
    for h in (cur.get("held") or []):
        if h.get("code") not in have and h.get("code") not in closed9:
            st.setdefault("held", []).append(h)
    # 4. keep every log row either side wrote
    # A ROW THE FOLD REMOVED MUST STAY REMOVED (boss 2026-09-09 read a card
    # saying "×16696987183346145720095693343744"). _fold_notes collapses the
    # guard's repeated refusals into one row and drops the copies - and then
    # THIS merge saw those copies still sitting on disk, did not find their ids
    # in memory, and put them all back. The next fold added their counts into
    # the survivor again, so the count DOUBLED every cycle: 7 → 328 → 23,850 →
    # 5×10^8 → 10^31. The refusals were real; the arithmetic was ours.
    seen_log = {l.get("id") for l in (st.get("log") or [])}
    _gone = set(st.get("_folded") or [])
    extra = [l for l in (cur.get("log") or [])
             if l.get("id") not in seen_log and l.get("id") not in _gone]
    if extra:
        st["log"] = ((st.get("log") or []) + extra)[-200:]
    _save(st)


LOG_MAX = 200


def _row_day(l: dict) -> str:
    """The KST day a log row belongs to, read from its own clock. Empty when
    the row carries no clock at all.

    A ROW WITH NO TIMESTAMP IS NOT A ROW FROM 1970 (boss 2026-09-09 11:3x:
    "another bad thing is happened why theri buying time has changed" - the
    trading history had gone back to 10:03/10:08/10:24 while the holding list
    showed his edits). A HELD lot carries no `ts` - it is a position we are in
    right now - so this returned "19700101", and the day-guard added to
    apply_time_overrides at 11:20 compared that against today and skipped
    EVERY held lot. All five of his clock corrections died silently at once.
    The guard's own fallback ("if not _rd: it is today") was written for
    exactly this row and could never fire, because 19700101 is truthy."""
    try:
        ts = float(l.get("ts") or 0)
    except Exception:
        ts = 0.0
    if ts <= 0:
        return str(l.get("day") or "").replace("-", "")
    try:
        return time.strftime("%Y%m%d", time.gmtime(ts + 9 * 3600))
    except Exception:
        return str(l.get("day") or "").replace("-", "")


def _fold_notes(st: dict) -> bool:
    """ONE STANDING REFUSAL IS ONE ROW, NOT SIXTY-SEVEN.

    The send-time guard writes a 보류 row every time it refuses a stock, and it
    refuses on EVERY scan cycle for as long as the reason holds. A stock the
    guard blocked all morning therefore left 67 identical rows (한화오션,
    2026-09-08), 54 more for 한화시스템, 46 for 한화에어로. The refusal is one
    fact that persisted, not sixty-seven events.

    Rows are folded per (day, code, reason): the NEWEST keeps its place and its
    clock, the older copies collapse into `repeat` so nothing about how long
    the refusal stood is lost."""
    log = st.get("log") or []
    first, keep, changed, _dropped = {}, [], False, []
    for l in reversed(log):                       # newest first
        if l.get("decision") != "보류":
            keep.append(l)
            continue
        key = (_row_day(l), str(l.get("code")), str(l.get("why_gone") or "")[:80])
        p9 = first.get(key)
        if p9 is None:
            first[key] = l
            keep.append(l)
        else:
            p9["repeat"] = int(p9.get("repeat") or 1) + int(l.get("repeat") or 1)
            _dropped.append(l.get("id"))
            changed = True
    if changed:
        st["log"] = list(reversed(keep))
        # the ids just folded away, so the disk merge cannot bring them back
        st["_folded"] = ([i for i in (st.get("_folded") or []) if i is not None]
                         + [i for i in _dropped if i is not None])[-4000:]
    # AND A COUNT CANNOT EXCEED THE NUMBER OF TIMES WE COULD HAVE LOOKED. The
    # desk checks at most a few times a second between 09:00 and now; anything
    # past that is arithmetic, not history, so it is clamped and marked rather
    # than shown as a number nobody can believe.
    try:
        _mins = max(1, (int(_hhmm()[:2]) - 9) * 60 + int(_hhmm()[3:5]))
        _cap = _mins * 20
        for l in st.get("log") or []:
            if int(l.get("repeat") or 1) > _cap:
                l["repeat"], l["repeat_capped"] = _cap, True
                changed = True
    except Exception:
        pass
    return changed


def _trim_log(st: dict) -> None:
    """THE LOG IS THE RECORD OF MONEY MOVING. NOISE MAY NEVER EVICT IT.

    Boss 2026-09-08 13:1x: "오늘 매수를 한 다음에 매도한 기록이 있어서 보여야
    하는데 안 보이는거 같다." He was right and the rows were not lost - they
    were pushed out. Two Menu 3 round trips closed today (LIG넥스원 3주
    09:51→11:10 +1.43%, HD현대중공업 22주 10:44→12:00 +0.11%) and both had
    been written to this log. Then 199 보류 rows for stocks we never traded
    filled the 200-row window, and a plain `log[-200:]` threw the trades away
    oldest-first. A record of a decision he made must outlive a note about a
    decision the machine declined to make.

    So: fold the repeats first, then trim - and trim the INFORMATIONAL rows.
    Executed and cancelled rows (승인/취소) are dropped only when they alone
    overflow the cap, which is the trim this was always meant to be."""
    _fold_notes(st)
    log = st.get("log") or []
    if len(log) <= LOG_MAX:
        return
    kept = {i for i, l in enumerate(log) if l.get("decision") in ("승인", "취소")}
    if len(kept) >= LOG_MAX:
        st["log"] = [l for i, l in enumerate(log) if i in kept][-LOG_MAX:]
        return
    room = LOG_MAX - len(kept)
    noise = [i for i in range(len(log)) if i not in kept]
    kept |= set(noise[-room:])
    st["log"] = [l for i, l in enumerate(log) if i in kept]


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
    # EVERY chat fill reaches the board (boss 2026-09-07: "when we bought
    # using the chatbot it should automatically go to our trading list; if
    # we sold it should go to the trading history") — the old desk-codes
    # filter silently dropped off-board experiments, so it is gone.
    st = _load()
    _trip: dict = {}
    # 🚦 the gate verdict AT THIS MOMENT rides with the record (boss
    # 2026-09-07: "in the reason it should explain this trade was done using
    # the chatbot even though the conditions were not met, including the
    # 갭상승 and the others")
    _gko: list[str] = []
    _gen: list[str] = []
    if side == "BUY":
        try:
            from routers.approval import whynot_at
            _wr9 = whynot_at(str(code), _hhmm(), name)
            if _wr9 and _wr9.get("stopped_at"):
                _gko.append("⚠️ 이 시점에 매수 관문이 막혀 있었지만, 사장님이 챗봇으로 "
                            "직접 승인하신 실험 매매입니다. 그 시각의 관문:")
                _gen.append("⚠️ The buy gates were BLOCKED at this moment, but the boss "
                            "approved it directly in chat — an experiment trade. The "
                            "gates at that minute:")
                for _g9 in _wr9.get("gates") or []:
                    _mk9 = "✅" if _g9.get("passed") else "⛔"
                    _gko.append(f"{_mk9} {_g9['n']}. {_g9['ko']}")
                    _gen.append(f"{_mk9} {_g9['n']}. {_g9['en']}")
            elif _wr9:
                _gko.append("✅ 매매 시점에 관문이 모두 열려 있었습니다 — 규칙과 같은 방향의 매수입니다.")
                _gen.append("✅ All gates were open at the moment of the trade — a buy in "
                            "the same direction as the rule.")
        except Exception:
            pass
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
        # A PARTIAL SELL LEAVES WHAT IT DID NOT SELL (boss 2026-09-09: he told
        # me to get rid of the duplicate 468 삼성중공업 shares, the sale went out
        # correctly - and Menu 3 then dropped the WHOLE position, hiding the 96
        # shares we still own. Same fault the desk's own ladder sell had; this
        # is its twin on the chat's side.)
        _lot9 = next((h for h in st.get("held") or [] if h["code"] == code), None)
        _left9 = int((_lot9 or {}).get("qty") or 0) - int(qty)
        if _lot9 and _left9 > 0:
            _lot9["qty"] = _left9
        else:
            st["held"] = [h for h in st.get("held") or [] if h["code"] != code]
    if side == "SELL" and not _gko:
        _gko.append("데스크의 -1% 매도 규칙과 무관하게, 사장님의 지시로 실행된 매도입니다.")
        _gen.append("Sold on the boss's own order, independent of the desk's -1% sell rule.")
    st.setdefault("log", []).append(
        {"id": int(time.time() * 1000) % 10**9, "ts": time.time(),
         "hhmm": _hhmm(), "code": code, "name": name, "side": side,
         "reasons": ["💬 챗봇 주문 — 사장님이 채팅으로 직접 지시하셨습니다."] + _gko,
         "reasons_en": ["💬 Chatbot order — the boss ordered it in chat."] + _gen,
         "price": float(fill), "qty": int(qty), "score": None, **_trip,
         "decision": "승인", "at": _hhmm(), "dealt": True, "fill": float(fill),
         "via": "chat"})
    _trim_log(st)
    _save(st)
    return True


def _disp_at(l: dict) -> str:
    """THE CLOCK ON THE SCREEN, NOT THE ONE WE WROTE (boss 2026-09-09: "please
    check and change all other parts also accordingly like buying, holding
    reasons also").

    A time edit moves a row to its signal minute at RENDER time only, so the
    stored `at` is still the minute the desk happened to act. Every explanation
    built from the stored clock therefore printed 10:24 prices and 10:24 volume
    under a 09:03 heading - the very inconsistency he keeps catching. The
    override's `frm` scope is honoured exactly as apply_time_overrides honours
    it, so only the row the edit names is moved."""
    at = str(l.get("at") or l.get("hhmm") or "")[:5]
    try:
        ov = (time_overrides() or {}).get(str(l.get("code") or "")) or {}
        if ov.get("at") and (not ov.get("frm") or str(ov["frm"])[:5] == at):
            return str(ov["at"])[:5]
    except Exception:
        pass
    return at


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
                    # v5: SOX/chip weather only on semiconductor names (09-04 10:3x)
                    or l.get("_rv") != 5
                    # the clock moved under it (a time edit) - the numbers must
                    # be re-read at the minute now on the screen
                    or l.get("_ci_at") != _disp_at(l)):
                # time-stamped at the row's own clock (volume of THAT minute)
                _d8r = None
                try:
                    _d8r = time.strftime("%Y%m%d", time.gmtime(float(l.get("ts")) + 9 * 3600)) if l.get("ts") else None
                except Exception:
                    pass
                l["check_items"] = _check_items(code, _disp_at(l) or None, _d8r)
                l["_ci_at"] = _disp_at(l)
            if l.get("score") is None:
                l["score"] = _score(code)
            sc = l.get("score")
            sc_ko = f" — 오늘 {sc}점." if sc is not None else "."
            sc_en = f" — today {sc} pts." if sc is not None else "."
            l["_rv"] = 5
            # THE SNAPSHOT MUST BE TAKEN AT THE MINUTE OF THE TRADE (boss
            # 2026-09-09: "Suggested and ordered time is not logically true so
            # make it true"). chat_mirror stamps the gates at the minute the
            # order is MIRRORED, so the 09:04 SK하이닉스 row explained itself
            # with 10:08 prices. whynot_at already replays any past minute -
            # the row re-reads itself once, at its own clock, and is marked so
            # it never pays for the replay again.
            if l.get("side") == "BUY" and l.get("via") == "chat":
                try:
                    from services.kiwoom_tape import _day as _kd8x
                    _at8 = _disp_at(l)
                    if (_row_day(l) == _kd8x() and len(_at8) == 5
                            and l.get("_gsnap_at") != _at8 + "|v6"):
                        from routers.approval import whynot_at as _wna8
                        _wr8 = _wna8(code, _at8, name) or {}
                        _gs8 = _wr8.get("gates") or []
                        if _gs8:
                            # NO PREAMBLE — THE GATES ARE THE EXPLANATION (boss
                            # 2026-09-09: "please remove the first line of the
                            # explanation and directly start explaining gates —
                            # it is better in all stock"). That line also
                            # printed the minute the ORDER went out ("as of
                            # 12:34") beside a buy clock he had corrected to
                            # 09:04 on the very same card - one trade wearing
                            # two times.
                            _rk8, _re8 = [], []
                            for _g8 in _gs8:
                                _m8 = "✅" if _g8.get("passed") else "⛔"
                                _rk8.append(f"{_m8} {_g8['n']}. {_g8['ko']}")
                                _re8.append(f"{_m8} {_g8['n']}. {_g8['en']}")
                            # and nothing kept in front of them either - not
                            # the 💬 chatbot header (he had that removed for his
                            # two names on 09-09) and not the 🏷 why-this-stock
                            # line. The explanation opens at gate 1.
                            _keepk, _keepe = [], []
                            l["reasons"] = _keepk + _rk8
                            l["reasons_en"] = _keepe + _re8
                            l["_gsnap_at"] = _at8 + "|v6"
                except Exception:
                    pass
            # AND THE PAIR LEADS WITH THE GAP (boss 2026-09-09: "in the buying
            # case it should explain there is not 갭상승, or that the market
            # opened with a 갭상승 of X% but the price came back to yesterday's
            # price") — only on today's own row, because exempt_gap reads the
            # live tape.
            if l.get("side") == "BUY":
                try:
                    from services.kiwoom_tape import _day as _kd8y
                    if _row_day(l) == _kd8y() and not any(
                            "갭상승이 없습니다" in str(x) or "갭상승이 있습니다" in str(x)
                            or "갭상승 " in str(x)[:14]
                            for x in (l.get("reasons") or [])[:3]):
                        from services.kiwoom_rules import exempt_gap as _xgf8
                        _xg8 = _xgf8(code)
                        if _xg8 and _xg8.get("buy_ko"):
                            _rs8 = list(l.get("reasons") or [""])
                            _es8 = list(l.get("reasons_en") or [""])
                            l["reasons"] = _rs8[:1] + [_xg8["buy_ko"]] + _rs8[1:]
                            l["reasons_en"] = _es8[:1] + [_xg8["buy_en"]] + _es8[1:]
                except Exception:
                    pass
            if l.get("side") == "BUY" and l.get("via") != "chat" and (
                    # a 💬 chat row keeps its OWN story — "bought by the boss's
                    # order though the gates were blocked" must never be
                    # repainted into the standard buy narrative (boss 2026-09-07)
                    len(l.get("reasons") or []) <= 2
                    or sum(1 for x in l.get("reasons") or [] if "📋" in str(x)) > 1
                    # rows saved before the ⑥ news line / true-gap story rebuild once
                    or not any("⑥" in str(x) for x in l.get("reasons") or [])
                    # rows still naming 알고3 rebuild with the engine-free wording
                    or any("알고3" in str(x) for x in l.get("reasons") or [])
                    # non-semi rows that still quote SOX/chip names rebuild with
                    # the scoped market weather (boss 09-04 10:3x)
                    or (not _is_semi(code, name)
                        and any("SOX" in str(x) for x in l.get("reasons") or []))
                    # rows with the rejected 'not the selling zone' phrasing
                    # rebuild into the positive low-place wording (09:1x)
                    or (any("매도구간 아님" in str(x) for x in l.get("reasons") or [])
                        and not l.get("_zone_reworded"))
                    # a time edit moved the clock under this row - the story
                    # must be retold at the minute now on the screen
                    or l.get("_why_at") != _disp_at(l)):
                l["_zone_reworded"] = True
                l["_why_at"] = _disp_at(l)
                head_ko = (l.get("reasons") or [""])[0]
                head_en = (l.get("reasons_en") or [head_ko])[0]
                try:
                    _d8w = None
                    try:
                        _d8w = time.strftime("%Y%m%d", time.gmtime(float(l.get("ts")) + 9 * 3600)) if l.get("ts") else None
                    except Exception:
                        pass
                    R, E = _why_buy(code, name, {"buy_t": _disp_at(l), "day8": _d8w})
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
        # HIS TWO NAMES ARE HELD UNTIL HE SELLS THEM (boss 2026-09-09, twice in
        # one hour: "Please do not sell this skhynix because it is not selling
        # postion so make it holding" / "Skhynix should be hold").
        # 알고2 trades the SAME position pool - today it round-tripped SK하이닉스
        # fifteen times, 50,000 shares at a go - and every time it flattened,
        # this reconcile read position 0 and closed his 5-share lot with it.
        # That is the machine deleting his standing order, not a ghost being
        # cleaned up. NOTHING in Menu 3 sells these two (NO_STOP), so nothing
        # here may close them either.
        if code in POS_GATE_EXEMPT:
            keep.append(h)
            continue
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
        # the close must SURVIVE the disk merge (caught 2026-09-04 11:24: the
        # merge's re-add loop brought every closed lot straight back from disk,
        # so this ran again each scan — the same 4 sells were appended 50 times
        # each and flooded the whole history out of the 200-row log)
        st.setdefault("_desk_closed", []).append(code)
        base = _lot_basis(h)
        if any(str(l.get("code")) == code and l.get("side") == "SELL"
               and str(l.get("at")) == when and l.get("fill") == fill
               and l.get("via") == "desk" for l in st.get("log") or []):
            changed = True          # drop the lot, the row already exists
            continue
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
        _trim_log(st)
    return changed


# THE PATIENT PAIR (boss 2026-09-03 evening: "another rule related to
# 삼성전자 and SK하이닉스 - exceptional case: even if they decreased -1% do not
# sell, keep holding, because they are already decreased many %, so -1 is not a
# big deal"). These two never trigger the -1% sale on any surface.
NO_STOP = ("005930", "000660")

# ── HOW BIG A BUY IS (boss 2026-09-09: "it is buiyng very small number of
# stock so please choose minimum sstock 1000 and others can be 10.000 also.
# For expensive one minimum buiying is 1000 like skhynix") ────────────────────
# The old ₩10M-per-name budget was written when this desk was a demo. On a
# ₩1.15조 book it bought FIVE shares of SK하이닉스 while 알고2 sat on 40,596 of
# them - a position too small to matter and too small to read. His rule sizes
# by SHARES, not by won: never fewer than 1,000 however expensive the stock,
# never more than 10,000 however cheap, and the budget picks the number in
# between so a ₩70,000 name and a ₩1,850,000 name still cost the same order of
# money.
BUY_BUDGET = 2_000_000_000      # ₩2B per name - lands SK하이닉스 just over 1,000
MIN_QTY = 1_000                 # the floor he named, for the expensive ones
MAX_QTY = 10_000                # the ceiling he named, for the cheap ones


def buy_qty(price: float, budget: int = 0) -> int:
    """Shares to buy at `price`, under his floor/ceiling law."""
    try:
        px = float(price or 0)
    except Exception:
        px = 0.0
    if px <= 0:
        return 0
    q = int((int(budget) or BUY_BUDGET) // px)
    return max(MIN_QTY, min(MAX_QTY, q))



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
        # SOX/NVIDIA/Micron/Tokyo Electron are CHIP weather — only
        # semiconductor-related stocks list them (boss 2026-09-04 10:3x)
        if _is_semi(code):
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
        elif _pl0.get("nasdaq") is not None:
            _n0 = float(_pl0["nasdaq"])
            out0.append({"k": "🌐 나스닥 밤사이", "en": "NASDAQ overnight",
                         "v": f"{_n0:+.1f}%", "ven": f"{_n0:+.1f}%",
                         "s": max(0, min(100, round(50 + _n0 * 15))), "g": "market",
                         "bad": _n0 <= -1.0})
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
            # REAL-TIME ONLY (boss 2026-09-04 12:3x): stamps older than an
            # hour are dropped by _fresh_stamps, and the shown line carries
            # the news' own clock so its freshness is visible.
            from services.checklist_advice import _fresh_stamps
            _sn = _fresh_stamps(code, limit=3)
            _badn = [s for s in _sn if str(s.get("stamp")) in ("위험", "악재")]
            _goodn = [s for s in _sn if str(s.get("stamp")) == "호재"]
            def _nhm(s9):
                return str(s9.get("ts") or "")[11:16]
            if _badn:
                out.append({"k": "📰 뉴스 검사 (AI 인턴)",
                            "v": f"위험({_nhm(_badn[-1])}): {str(_badn[-1].get('title'))[:40]}",
                            "s": 15, "g": "news", "bad": True,
                            "link": _badn[-1].get("link")})
            elif _goodn:
                out.append({"k": "📰 뉴스 검사 (AI 인턴)",
                            "v": f"호재({_nhm(_goodn[-1])}): {str(_goodn[-1].get('title'))[:40]}",
                            "s": 85, "g": "news", "bad": False,
                            "link": _goodn[-1].get("link")})
            else:
                # Naver fallback obeys the same law: a headline older than
                # 2 hours is NOT shown at all — better no news than old news
                _nm9 = (me or {}).get("name")
                _arts9 = []
                if _nm9:
                    from services.naver_news import fresh_news
                    _arts9 = fresh_news(str(_nm9), display=3, max_age_min=120)
                out.append({"k": "📰 뉴스 검사",
                            "v": (f"특이 없음 · {_arts9[0]['age_min']}분 전: {_arts9[0]['title'][:34]}" if _arts9
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
    _trim_log(st)
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
    # A QUESTION WAITS FOR ITS ANSWER (boss 2026-09-07: "if we are outside so we
    # do not see the popup, it must keep stay until we approve or cancel").
    # It used to die silently after ten minutes, so a proposal raised while he
    # was away from the desk was gone before he ever saw it - and he could not
    # tell that from the desk never having asked. Nothing expires now; only his
    # answer, or the closing bell, removes a popup.
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
            _trim_log(st)
        _save_scan(st, _seen0, _seenh0)
        return st
    # NO SUGGESTIONS AFTER 15:20 (boss 2026-09-03 18:1x: "after 15:20 our
    # agent should not give suggestions because the market is closing") — the
    # closing auction is no place to propose; unanswered popups die with it.
    if _hhmm() >= "15:20":
        if st["pending"]:
            # ...AND IT LEAVES A ROW WHEN IT GOES (boss 2026-09-09: "make sure
            # the popup stays until we close or exit or approve or cancel").
            # The closing bell is his "close", so the sweep is right - but this
            # one used to empty the list in silence, so a card he never got to
            # answer simply vanished with no trace of having existed. The
            # market-closed sweep twenty lines up has always logged its
            # withdrawals; this one now does too.
            for _p9 in st["pending"]:
                st.setdefault("log", []).append(
                    {**_p9, "decision": "자동 취소", "at": _hhmm(), "dealt": None,
                     "why_gone": "15:20 마감 정리 — 답을 못 받은 채 거둡니다 / "
                                 "swept unanswered at the 15:20 close"})
            st["pending"] = []
            _trim_log(st)
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
        # AND A LAPSED CONDITION MARKS THE CARD, IT NO LONGER TAKES IT AWAY.
        # Withdrawing was the honest thing while popups also expired; now that
        # a question waits for him, silently removing one is the very thing he
        # asked us to stop. The card stays and says the condition has passed,
        # so the choice is still his - and approving sends a MARKET order, so
        # the fill is at today's price, never the stale one on the card.
        if _sd9 == "BUY" and int(_mk9.get(_c9) or 0) >= _HOLD_N:
            _p9["stale"] = True
            _p9["stale_ko"] = "⚠️ 처음 제안한 조건은 지나갔습니다 — 그래도 결정은 "
            _p9["stale_ko"] += "사장님 몫이라 카드를 남겨둡니다. 승인하시면 지금 나온 값에 바로 나갑니다."
            _p9["stale_en"] = ("⚠️ The condition this was raised on has passed. "
                               "The card stays because the decision is yours; "
                               "approving sends a MARKET order at today's price.")
            _keep9.append(_p9)          # ... and KEEP it. Marking a card and
            # then forgetting to keep it is how "it stays until you answer"
            # quietly became "it disappears after three checks" - the lifecycle
            # test caught it 44 seconds in.
        elif _sd9 == "SELL" and _c9 not in _ourc9:
            # the ONE case a card is still taken away: a sell for a stock we no
            # longer hold cannot be acted on at all - approving it would only
            # be refused. That is an impossible action, not a lapsed condition.
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
        _trim_log(st)
    # 🚦 POPUPS ONLY WHEN THE FULL CASCADE PASSES (boss 2026-09-07: "it keeps
    # asking buy popups even though I did not try — the popup must come only
    # when ALL gates pass"). The brain's lane test predates the weekly-
    # position gate, so a stock the proof menu itself marks BLOCKED could
    # still raise a popup. The scanner now also asks the whynot cascade —
    # the very verdicts the proof menu shows — and proposes nothing blocked.
    _wn_pass9 = None
    try:
        from routers.approval import whynot as _wnf9
        _wn_pass9 = {str(x.get("code")) for x in (_wnf9(db).get("rows") or [])
                     if not x.get("stopped_at")}
    except Exception:
        _wn_pass9 = None
    # 🌊 IS THE LADDER LANE ON? (boss 2026-09-09: "please make it consistent").
    # When his 20% ladder rule is running - in either mode - it is the ONE voice
    # for every room: this scanner stops raising its own BUY cards and its own
    # -1% SELL cards, because the ladder already carries a stop, a profit rung,
    # a slow-roll-over exit and a closing flat. Two lanes proposing on one stock
    # is two agents arguing on his screen. 'off' hands the desk straight back.
    try:
        from services.wave_desk import mode as _wmode9
        _wave_on9 = _wmode9() != "off"
    except Exception:
        _wave_on9 = False
    for code, name, score in _rooms9:
        try:
            px, chg, _t, _s = fast_price(code)
            if not px:
                continue
            px = float(px)
            if _wave_on9:
                st.setdefault("why_skip", {})[code] = (
                    "🌊 ladder lane owns this room - see wave_desk")
                continue
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
            # THE CASCADE IS THE ONE GATE LAW (boss 2026-09-07, the Samsung
            # Biologics case: the proof menu said ALL gates passed but no
            # popup came — the old brain gates, 1-year average and already-
            # rising, were still required on top although the 09-04 three-
            # gate order superseded them). The cascade decides WHERE we may
            # buy; the lane is only recorded for the note. Falls back to the
            # lane when the cascade cache is cold.
            _ln9 = _lane_of(code)
            if _wn_pass9 is not None:
                if str(code) not in _wn_pass9:
                    st.setdefault("why_skip", {})[code] = "whynot cascade blocked — no popup"
                    st.setdefault("streak", {})[code] = 0
                    continue
                st.setdefault("why_skip", {})[code] = "cascade passed"
            else:
                st.setdefault("why_skip", {})[code] = "lane=" + (_ln9 or "?")
                if _ln9 != "BUY":
                    st.setdefault("streak", {})[code] = 0
                    continue
            # AND THE TURN MUST HAVE HAPPENED (boss 2026-09-07 10:4x - the
            # three holdings above). Gates say WHERE we may buy; the turn says
            # WHEN. Both, or no popup.
            _tok9, _tk9, _te9, _tt9 = turn_now(code, _board9)
            if not _tok9:
                st.setdefault("why_skip", {})[code] = "no turn yet: " + _te9[:60]
                st.setdefault("streak", {})[code] = 0
                continue
            try:
                from services.checklist_advice import _fresh_stamps, danger_stamps
                # the RISK veto keeps a wider 3h net — missing a danger story
                # costs money, so the real-time display law does not thin it.
                # But it must be THIS stock's danger: an industry headline that
                # happens to name two of our companies ("中 자동차 수출 급증…
                # 현대차·기아 긴장") is a comparison, not a threat, and it cost
                # seven of today's best signals (boss 2026-09-09).
                if (code not in POS_GATE_EXEMPT
                        and danger_stamps(code,
                                          _fresh_stamps(code, limit=2, max_age_min=180),
                                          name)):
                    st.setdefault("why_skip", {})[code] = "danger news on this stock"
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
            # NO HEADER IN FRONT OF THE GATES (boss 2026-09-09: "PLEASE REMOVE
            # FIRST LINE OF EXPLANATION AND DIRECTLY START EXPLAINING GATES ...
            # IT IS BETTER IN ALL STOCK"). The 🏷 "why this company" line that
            # stood here since 09-03 said in a sentence what the gates below
            # say with their own numbers; he wants the numbers first. Which
            # basket a stock came from is still on its board card.
            # the price a person can actually place, off the live order book
            _bp, _pko, _pen = _book_price(code, "BUY", px)
            _bq = buy_qty(_bp)
            if not _bq:
                _bq = buy_qty(px)
            _qko, _qen = _why_qty(_bp, _bq)
            reasons.append("💰 왜 이 가격인가 — " + _pko)
            reasons_en.append("💰 WHY THIS PRICE — " + _pen)
            # THE LADDER IS SHOWN BEFORE HE CLICKS (boss 2026-09-07: "20% with
            # this price and another 20% another like this"), so the popup he
            # approves is the order that actually goes out.
            try:
                _lad9 = book_ladder(code, "BUY", _bp, int(_bq))
                if len(_lad9) > 1:
                    _lk9, _le9 = ladder_words(_lad9, "BUY")
                    reasons.append("🪜 주문을 나눠서 — " + _lk9)
                    reasons_en.append("🪜 SPLIT INTO SLICES — " + _le9)
                    _sg_lad9 = _lad9
                else:
                    _sg_lad9 = None
            except Exception:
                _sg_lad9 = None
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
                _trim_log(st)
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
            if _sg_lad9:
                _sg9["ladder"] = [{k: r[k] for k in ("px", "qty", "kind")}
                                  for r in _sg_lad9]
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


# ─────────────────────────────────────────────────────────────────────────────
# THE TURN ITSELF (boss 2026-09-07 10:4x, holding 현대로템 10:28, 한국전력 10:30 and
# HD현대중공업 10:32 on his screen: "even though they passed the gates we have to
# wait for their decrease, and once they stopped decreasing and in the 3 red -
# I mean it started to increase - then we should buy").
#
# He was right and the tape says so. 현대로템 was bought at 10:28 into two blue
# minutes (125,900 → 125,700 → 125,600) and kept falling to 125,300; 알고3's own
# door did not open until 10:35:54. 한국전력 was bought at 10:30 inside a
# one-tick chop (32,150↔32,200 all morning) - there was no fall to stop and no
# rise to confirm; the engine's entry there was 09:04:59, an hour and a half
# earlier, so buying at 10:30 was a late chase of a turn that had long passed.
#
# Since 09-03 the board deliberately did NOT require the entry shape - "whether
# 알고3 has taken its own entry shape yet is shown INSIDE the popup, not used to
# gate the question" - because he was then asking why a card said BUY with no
# popup. That trade-off is now reversed by his own instruction: the shape is a
# REQUIREMENT, and a card without it says WAITING instead of BUY.
#
# No new law is written here either - every number below is 알고3's own dip door.
# What changed after his 11:0x correction is only WHERE it is counted: on the
# 1-minute candles, not on the engine's 5-tick bars. (The first version of this
# asked the engine whether it held the stock; measured against his three cases
# it agreed on two and missed HD현대중공업's 10:27 turn entirely, which is the
# case he could see with his own eyes.)
# THE COUNT IS DONE ON THE CHART HE READS (boss 2026-09-07 11:0x, correcting me
# on HD현대중공업: "it should be 10:27, is it not?"). He was right and I was wrong -
# I had measured the shallow 10:30 dip and missed the real one: the fall ran
# 444,000 (09:55) → 435,000 (10:22), −2.03%, and the rises after that bottom are
# 10:23 · 10:24 · [10:25, 10:26 flat] · 10:27 — the 3rd red stands at 10:27 and
# THAT is where we buy, not 10:32 where the desk actually bought.
#
# So the turn is counted here, minute by minute, on the same 1-minute candles he
# looks at - the engine's own door runs on 5-tick bars and answers a different
# question at a different moment (it never entered HD현대중공업 at all today).
# Every number below is 알고3's, unchanged: a 0.7% fall measured to NOW, a real
# range (not a flat tape), sharp against the bar's own typical move, no more
# than half the fall given back, the price within 1.5% of the bottom, and the
# 3rd rising candle. A flat minute neither counts nor breaks the run; a blue
# deeper than 0.2% resets it, exactly as the blues law does everywhere else.
_TURN = {"win": 30, "drop": 0.7, "ups": 3, "soft": 0.2,
         "chase": 1.5, "chop": 1.0, "sharp": 3.0, "recov": 0.5}
_TURNC: dict = {}       # (code, minute) -> answer; the tape only moves once a minute


POS_GATE_EXEMPT = ("000660", "005930")   # boss 2026-09-09


def _turn_shape(code: str, bars: list | None = None) -> tuple:
    """(ok, ko, en, at) — does the 3rd rise stand on the 1-minute tape right now?

    `bars` lets the same shipped code be REPLAYED against any past minute - pass
    the tape up to that minute and it answers as it would have answered then.
    The send-time guard has been replayable this way since 09-04; a rule that
    decides when we buy must be checkable against the day it decided."""
    import statistics, time as _t
    _live = bars is None                 # a replay must never read or write the memo
    _key = (str(code), int(_t.time() // 20))
    if _live and _key in _TURNC:
        return _TURNC[_key]
    if len(_TURNC) > 200:
        _TURNC.clear()
    d = _TURN
    if bars is None:
        try:
            from services.kiwoom_tape import load as _ld, bars_time as _bt, _day as _dy
            bars = _bt(_ld(str(code), _dy()), 60)
        except Exception:
            bars = []
    # ── HIS TWO NAMES USE HIS OWN SHAPE (boss 2026-09-09, pointing at the
    # SK하이닉스 tape: "in the skhynix case it should be at 09:04 - price
    # decreased and stopped decreasing and started to increase and the 3rd
    # (small blue ignore, like before)"). He is right and our detector missed
    # it twice over: it refuses to answer before 8 bars exist (so nothing can
    # fire before 09:08) and it demands a 0.7% fall, while his 09:01 dip was
    # 0.39%. For the two exempt names the shape is exactly as he describes it:
    # a blue candle of ANY size, then 3 consecutive rises, a small blue
    # (<=0.2%) ignored as the soft-up law already does elsewhere. Everything
    # else - gate 1, volume, news - still guards the buy.
    if str(code) in POS_GATE_EXEMPT and bars and len(bars) >= 4:
        w2 = bars[-d["win"]:]
        ups, seen_fall, third = 0, False, None
        prev = w2[0]["close"]
        peak = prev
        for b in w2[1:]:
            c, o = b["close"], b["open"]
            if c < o:                                   # a blue candle
                seen_fall = True
            elif c > prev:
                ups += 1
                if ups == d["ups"] and third is None:
                    third = str(b.get("hhmm") or "")[:5]
            # the same slide test as the day-scale counter above: forgiveness
            # is measured from the run's own peak, never step to step
            if peak is None or c > peak:
                peak = c
            if peak and (peak - c) / peak * 100 > d["soft"]:
                ups, third, peak = 0, None, c
            prev = c
        if seen_fall and ups >= d["ups"]:
            px2 = w2[-1]["close"]
            out = (True,
                   f"진입 신호 확인 (예외 2종목 규칙) — 하락이 멈추고 {third}에 3번째 "
                   f"양봉이 섰습니다 (현재 ₩{px2:,.0f}). 작은 음봉은 무시합니다.",
                   f"entry signal confirmed (exempt-pair rule) - the fall stopped and the "
                   f"3rd rising candle stood at {third} (now W{px2:,.0f}); small blue "
                   f"candles are ignored",
                   third)
            if _live:
                _TURNC[_key] = out
            return out
    w = bars[-d["win"]:] if bars else []
    if len(w) < 8:
        out = (False, "1분봉이 아직 충분하지 않습니다 — 신호를 셀 수 없습니다",
               "not enough 1-minute tape yet to count the signal", None)
        if _live:
            _TURNC[_key] = out
        return out
    px = w[-1]["close"]
    whi, wlo = max(b["high"] for b in w), min(b["low"] for b in w)
    lows = [b["low"] for b in w]
    ti = lows.index(min(lows))
    trough, hi = w[ti]["low"], max(b["high"] for b in w[:ti + 1])
    at = str(w[ti].get("hhmm") or "")[:5]
    fall = (hi - px) / hi * 100 if hi else 0.0
    diffs = [abs(w[k]["close"] - w[k - 1]["close"]) for k in range(1, len(w))]
    typ = statistics.median(diffs) if diffs else 0.0

    def _no(ko, en):
        out = (False, ko, en, at)
        if _live:
            _TURNC[_key] = out
        return out

    def _rises(seq, first_prev):
        """HIS OWN COUNT (boss 2026-09-07 10:2x, HD현대중공업: 10:23 ▲ · 10:24 ▲ ·
        10:25 flat · 10:26 flat · 10:27 ▲ = "the 3rd red"). A flat minute
        neither counts nor breaks the run; a blue deeper than 0.2% resets it.

        A SLIDE IS A FALL EVEN WHEN EVERY STEP IS SMALL (boss 2026-09-09, the
        한화오션 10:53 popup: "it now decreasing not signal 3 red"). The 0.2%
        forgiveness was measured from the PREVIOUS candle, so a staircase -
        10:51 -0.11%, 10:52 -0.11%, 10:53 -0.11% - slid 0.34% off the top
        without any single step tripping the test, and a signal stamped at
        10:49 was still counted as alive four minutes later while price walked
        steadily down. The forgiveness is meant for ONE wobble inside a rise,
        so it is measured from the highest close the run has made: one small
        blue is still ignored, a slide past the same 0.2% is a real fall and
        resets the count. Returns (rises, the clock that completed the count).
        """
        _u, _prev, _third = 0, first_prev, None
        _peak = first_prev
        for _b in seq:
            _c = _b["close"]
            if _c > _prev:
                _u += 1
                if _u == d["ups"] and _third is None:
                    _third = str(_b.get("hhmm") or "")[:5]
            if _peak is None or _c > _peak:
                _peak = _c
            if _peak and (_peak - _c) / _peak * 100 > d["soft"]:
                _u, _third, _peak = 0, None, _c
            _prev = _c
        return _u, _third
    if wlo and (whi - wlo) / wlo * 100 < d["chop"]:
        # THE DAY-SCALE DOOR (boss 2026-09-07 15:1x, six all-passed stocks
        # asking "why still no popup"): his law reads across the DAY — these
        # stocks fell earlier today (that is exactly why the gates opened),
        # the fall has STOPPED (this flat base), and when the rise begins he
        # wants the popup. The 30-minute window had forgotten the morning
        # fall, so a flat base blocked forever. If the DAY shows a real fall
        # into this base and 3 consecutive 1-minute rises now stand at it,
        # the turn is confirmed — 내렸고, 멈췄고, 오르기 시작했다.
        try:
            _dhi = max(b["high"] for b in bars)
            _dfall = (_dhi - px) / _dhi * 100 if _dhi else 0.0
        except Exception:
            _dfall = 0.0
        # ONE COUNT FOR BOTH DOORS (boss 2026-09-07 11:5x, pressing again on
        # 한화오션 and five others: "all gates passed - why is the popup not
        # coming"). This door demanded three CONSECUTIVE rising closes, and on
        # the flat base it is built for, most minutes are flat - so the run was
        # broken by a minute in which nothing happened. Measured over today's
        # tape across fourteen names: the consecutive test opened ONCE all day
        # (1 of 14 names), his own count opens for 13 of 14. It was not the
        # market that was silent, it was the counting.
        _u3, _hm3 = _rises(w[ti + 1:], w[ti]["close"])
        if (_dfall >= d["drop"] and _u3 >= d["ups"]
                and px <= wlo * (1 + d["chase"] / 100)):
            _hm3 = _hm3 or str(w[-1].get("hhmm") or "")[:5]
            out = (True,
                   f"진입 신호 확인 (하루 흐름) — 오늘 고점 대비 {_dfall:.2f}% 내린 뒤 "
                   f"하락이 멈춰 횡보했고(30분 폭 {(whi - wlo) / wlo * 100:.2f}%), "
                   f"{_hm3}에 3번째 상승이 섰고 지금도 그 자리를 지키고 있습니다 "
                   f"(₩{px:,.0f}). 사이의 보합은 세지 않고, 작은 음봉(0.2% 이하) "
                   f"하나는 무시하지만, 고점에서 0.2% 넘게 밀리면 신호는 사라집니다.",
                   f"entry signal confirmed (day-scale) — fell {_dfall:.2f}% from today's "
                   f"high, the fall stopped into a flat base "
                   f"({(whi - wlo) / wlo * 100:.2f}% over 30 min), and the 3rd rise "
                   f"stood at {_hm3} and still holds (₩{px:,.0f}). Flat minutes in "
                   f"between are not counted and one small blue candle (<=0.2%) is "
                   f"forgiven, but a slide of more than 0.2% off the run's high "
                   f"cancels the signal.",
                   _hm3)
            if _live:
                _TURNC[_key] = out
            return out
        return _no(f"오늘 고점 대비 {_dfall:.2f}% 내린 뒤 멈춘 자리입니다 "
                   f"(30분 폭 {(whi - wlo) / wlo * 100:.2f}%). 지금까지 상승 {_u3}개 — "
                   f"{d['ups']}개가 서면 그 순간 제안드립니다",
                   f"it fell {_dfall:.2f}% from today's high and has stopped here "
                   f"({(whi - wlo) / wlo * 100:.2f}% of range over 30 min). {_u3} rise(s) "
                   f"so far - the moment the {d['ups']}rd one stands, we ask")
    if fall < d["drop"]:
        # A NEGATIVE "FALL" IS NOT A SENTENCE (boss 2026-09-09 read the line
        # "the fall has already healed - only -1.41% below the high"). Below a
        # high by a negative amount means the price is ABOVE where it fell
        # from - there is no dip at all, and the row should say that plainly
        # instead of printing a minus sign at him.
        if fall <= 0:
            return _no("지금은 눌림이 없습니다 — 오늘 흐름의 위쪽에 있어 "
                       "살 자리가 아닙니다 (내렸다 돌아서는 모양을 기다립니다)",
                       "there is no dip at all right now - the price sits above where "
                       "it last fell from, so there is nothing to buy into yet")
        return _no(f"하락이 이미 회복됐습니다 — 고점 대비 {fall:.2f}%뿐이라 "
                   f"살 만한 눌림이 아닙니다 (최소 {d['drop']}% 필요)",
                   f"the fall has already healed - only {fall:.2f}% below the high, "
                   f"and {d['drop']}% is the least we buy into")
    if typ and (hi - px) < d["sharp"] * typ:
        return _no("천천히 흘러내린 것이지 급락이 아닙니다 — 기다립니다",
                   "a slow drift, not a sharp fall - we wait")
    if trough and hi > trough and (px - trough) / (hi - trough) > d["recov"]:
        return _no(f"반등이 이미 하락폭의 {(px - trough) / (hi - trough) * 100:.0f}%를 "
                   f"되돌렸습니다 — 돌아서는 자리는 지났습니다",
                   f"the bounce already took back {(px - trough) / (hi - trough) * 100:.0f}% of the fall - "
                   f"the turn happened without us")
    if trough and px > trough * (1 + d["chase"] / 100):
        return _no(f"바닥({at} ₩{trough:,.0f})보다 {(px - trough) / trough * 100:+.2f}% 위입니다 — "
                   f"추격 매수는 하지 않습니다 (제1조)",
                   f"{(px - trough) / trough * 100:+.2f}% above the bottom (₩{trough:,.0f} at {at}) - "
                   f"we do not chase (제1조)")
    ups, third = _rises(w[ti + 1:], w[ti]["close"])
    if ups < d["ups"]:
        return _no(f"하락 {fall:.2f}%는 충분하지만 {at} 바닥 이후 양봉이 {ups}개뿐입니다 — "
                   f"3번째 양봉이 서면 그때 삽니다",
                   f"the fall of {fall:.2f}% is real, but only {ups} rising candle(s) since the "
                   f"bottom at {at} - we buy when the 3rd one stands")
    out = (True,
           f"진입 신호 확인 — {at} 바닥 ₩{trough:,.0f}까지 {fall:.2f}% 하락한 뒤 하락이 멈췄고, "
           f"{third}에 3번째 양봉이 섰습니다 (바닥 대비 {(px - trough) / trough * 100:+.2f}%)",
           f"entry signal confirmed - a {fall:.2f}% fall into the bottom of ₩{trough:,.0f} at {at}, "
           f"the fall stopped, and the 3rd rising candle stood at {third} "
           f"({(px - trough) / trough * 100:+.2f}% off the bottom)",
           third or at)
    if _live:
        _TURNC[_key] = out
    return out


def turn_now(code: str, board: dict | None = None) -> tuple:
    """Kept for its callers - the answer now comes from the 1-minute chart."""
    return _turn_shape(code)


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
    # AN EDIT BELONGS TO ITS DAY (boss 2026-09-09: today's five holdings were
    # all wearing clocks he corrected on an earlier day - 현대차 09:01,
    # 삼성중공업 09:05, SK하이닉스 09:04 - because this file is keyed by stock
    # code alone and never expires. A held lot whose clock moves also takes the
    # price of that moment, so his P&L was being shown against prices we never
    # paid: 삼성중공업 read -0.69% off ₩21,600 when we actually paid ₩21,367.
    # It is the same lesson as the 09-08 gap waiver: a one-day correction has
    # to be a DATE, never a switch nobody remembers to flip back.)
    try:
        from services.kiwoom_tape import _day as _kd_ov
        cur["day"] = _kd_ov()
    except Exception:
        pass
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


_PREAMBLE = ("🧾", "🏷")   # 🧾 ledger/bulk headers, 🏷 why-this-company


def open_at_the_gates(rows: list) -> list:
    """THE EXPLANATION STARTS AT THE GATES (boss 2026-09-09).

    Rows written before that word - the two the 09-08 repair tool rebuilt from
    the ledger, the bulk-buy batch, his own hand-added trips whose header was
    once blanked and left an empty line - still carry a sentence in front of
    the first gate. Their text lives in approval_desk.json, which is never
    edited while the desk is running, so the line is dropped here, on the way
    to the screen. Nothing is deleted: the record keeps every word it had.

    Only a BUY is touched, and only leading lines: a blank, or one opening
    with a header mark. The moment a real gate line is reached it stops.
    """
    out = []
    for l in rows or []:
        if str(l.get("side") or "BUY").upper() != "BUY":
            out.append(l)
            continue
        n = dict(l)
        for k in ("reasons", "reasons_en"):
            rs = list(n.get(k) or [])
            while rs and (not str(rs[0]).strip()
                          or str(rs[0]).lstrip().startswith(_PREAMBLE)):
                rs.pop(0)
            if rs != (n.get(k) or []):
                n[k] = rs
        out.append(n)
    return out


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
    # the day this file was last written - the only day a legacy entry (one
    # saved before corrections carried a date) can honestly be said to describe
    _legacy_day = ""
    try:
        import datetime as _dt_ov
        _legacy_day = _dt_ov.datetime.fromtimestamp(
            _TOVR.stat().st_mtime).strftime("%Y%m%d")
    except Exception:
        pass
    for row in list(held or []) + list(log or []):
        ov = o.get(str(row.get("code") or ""))
        if not ov:
            continue
        # A CORRECTION MAY ONLY TOUCH THE DAY IT WAS MADE FOR (boss 2026-09-09).
        # A HELD lot carries no clock of its own - it is a position we are in
        # right now, and this desk never holds one overnight, so its day is
        # today. Without this the guard would read an empty day and wave every
        # stale correction straight through onto the very rows he is looking at.
        _rd = str(_row_day(row) or "").replace("-", "")
        if not _rd:
            try:
                from services.kiwoom_tape import _day as _kd_ov2
                _rd = _kd_ov2()
            except Exception:
                _rd = ""
        _od = str(ov.get("day") or _legacy_day)
        if _rd and _od and _rd != _od:
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
                    "체결되지 않아 매도가 늦어졌습니다 — 이제 승인은 지금 값에 바로 나갑니다.",
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


LADDER_N = 5            # five slices of 20% (boss 2026-09-07)
LADDER_STEP = 1         # one tick better per slice
MIN_LOT = 100           # boss 2026-09-09: "we should not buy 5 shares, it is
                        # too low - the minimum buying stock is 100"


def _touch_price(code: str, side: str):
    """The price a market order actually deals at right now: the cheapest ask
    when we buy, the highest bid when we sell. Written on the first slice so a
    person reads a number instead of the words 'market order'."""
    try:
        from services.kiwoom_tape import load_book, _day
        snaps = load_book(code, _day()) or []
        if snaps:
            b = snaps[-1]
            rows = (b.get("asks") or []) if side == "BUY" else (b.get("bids") or [])
            px = [float(p) for p, q in rows if p and q]
            if px:
                return min(px) if side == "BUY" else max(px)
    except Exception:
        pass
    try:
        from services.paper_desk import fast_price
        from services.kiwoom_rules import krx_tick
        p0 = float((fast_price(code) or [None])[0] or 0)
        if p0:
            tk = krx_tick(p0) or 1
            return float(int(round(p0 / tk)) * tk)
    except Exception:
        pass
    return None


def _daily3(code: str, days: int = 60) -> list[dict]:
    """This stock's last ~3 months of daily open/high/low - the habit the five
    buy prices are read from. Same rows the popup's price plan draws."""
    try:
        from ml._db import get_conn
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("""SELECT open,high,low FROM raw_daily_prices
                           WHERE ticker=%s ORDER BY date DESC LIMIT %s""",
                        (str(code), int(days)))
            rows = cur.fetchall() or []
        finally:
            cur.close()
            conn.close()
        return [{"o": float(o), "h": float(h), "l": float(l)}
                for o, h, l in rows if o and h and l]
    except Exception as e:
        log.debug(f"daily3 {code}: {str(e)[:60]}")
        return []


def history_buy_plan(code: str, qty: int, now_px: float) -> list[dict]:
    """FIVE EFFICIENT PRICES, FROM THIS STOCK'S OWN THREE-MONTH HABIT.

    Boss 2026-09-09: "in case of the buying you just put market price; we should
    buy efficient price, so you have to choose 5 different efficient prices" -
    and, pressing again while the ladder rule was being built, "our agent needs
    to find by analyzing historical data and prices and offer the best efficient
    prices, so please make sure to this."

    THE POPUP ALREADY DID THIS AND THE ORDER DID NOT. The five history-chosen
    prices were computed in the browser (PricePlan.tsx) purely to be READ, while
    the order that actually went out was still five slices a single tick apart
    around the order book - so the screen and the money disagreed about what a
    buy is. This is that same calculation, in the backend, on the path the order
    takes.

    The levels are the depths this stock genuinely reaches: three months of "how
    far did it fall from its own open that day", sorted, read at the 85 / 70 /
    50 / 30 / 15% marks. Every price stands BELOW the market - an unfilled buy
    costs nothing, an expensive one costs every time - and the size on each is
    (how often we get there) x (how many sessions truly traded through it), so
    the likeliest, best-supported level carries the most shares.
    """
    from services.kiwoom_rules import krx_tick
    bars = _daily3(code)
    if not bars or len(bars) < 20 or not now_px or qty <= 0:
        return []
    dips = sorted(((b["o"] - b["l"]) / b["o"]) for b in bars if b["o"] > 0)
    if not dips:
        return []

    def q(p: float) -> float:
        i = int(round((len(dips) - 1) * p))
        return dips[max(0, min(len(dips) - 1, i))]

    def snap(px: float) -> float:
        tk = krx_tick(px) or 1
        return float(int(px / tk) * tk)

    def support(px: float) -> int:
        return sum(1 for b in bars if b["l"] <= px <= b["h"])

    picked: list[dict] = []
    for p in (0.15, 0.30, 0.50, 0.70, 0.85):
        px = snap(now_px * (1 - q(p)))
        # two marks can snap onto the same KRX tick on a quiet stock - step down
        # until five real, distinct, placeable prices stand (bounded, never a
        # loop that cannot end)
        for _ in range(40):
            if px < now_px and all(abs(px - x["px"]) > 1e-9 for x in picked):
                break
            tk = krx_tick(px) or 1
            nx = snap(px - tk)
            px = nx if nx < px else px - tk
        if px > 0 and px < now_px and all(abs(px - x["px"]) > 1e-9 for x in picked):
            picked.append({"px": px, "reach": 1 - p, "days": support(px)})
    if len(picked) < 5:
        return []
    picked.sort(key=lambda x: -x["px"])                 # dearest first
    # NO ODD LOTS, EVER (his standing complaint about "96 and 93"). Weights
    # decide the shape; the KRX lot decides the numbers. Five real lots need
    # 500 shares - below that the plan takes as many whole lots as it can, and
    # under two it hands the order back to the book ladder.
    n_sl = max(0, min(len(picked), int(qty) // MIN_LOT))
    if n_sl < 2:
        return []
    picked = picked[:n_sl]
    wsum = sum(x["reach"] * max(x["days"], 1) for x in picked) or 1.0
    lots = int(qty) // MIN_LOT
    alloc = [max(1, int(round(lots * (x["reach"] * max(x["days"], 1)) / wsum)))
             for x in picked]
    while sum(alloc) > lots:                 # trim from the smallest weight up
        i = max(range(len(alloc)), key=lambda k: (alloc[k], -k))
        if alloc[i] <= 1:
            break
        alloc[i] -= 1
    alloc[0] += lots - sum(alloc)            # the remainder rides the likeliest price
    out, left = [], int(qty)
    for i, x in enumerate(picked):
        n = alloc[i] * MIN_LOT
        if i == len(picked) - 1:
            n = left                          # the last slice carries any odd tail
        n = min(n, left)
        if n <= 0:
            continue
        left -= n
        off = (x["px"] - now_px) / now_px * 100
        out.append({
            "px": float(x["px"]), "qty": int(n), "kind": "limit",
            "ko": (f"{i + 1}번째 {n:,}주 — ₩{x['px']:,.0f} ({off:+.2f}%). "
                   f"최근 {len(bars)}일 중 {round(x['reach'] * 100)}%의 날이 시가에서 "
                   f"이 깊이까지 밀렸고, 실제로 이 가격대에서 거래된 날은 {x['days']}일입니다."),
            "en": (f"slice {i + 1}, {n:,} sh at ₩{x['px']:,.0f} ({off:+.2f}%) - "
                   f"{round(x['reach'] * 100)}% of the last {len(bars)} sessions fell this "
                   f"far from their own open, and {x['days']} of them actually traded here."),
        })
    return out


def book_ladder(code: str, side: str, fallback: float, qty: int,
                slices: int = LADDER_N, touch_first: bool = False) -> list[dict]:
    """ONE ORDER BECOMES A LADDER (boss 2026-09-07: "we are selling all with one
    price. How about 20% with this price and another 20% another like this").

    The first slice keeps his standing law - one tick in front of the biggest
    wall - and goes out at MARKET, so a decision always executes: a sell that
    must leave gets out, a buy that must get in gets in. The remaining slices
    rest one tick better each, so a move in our favour is harvested instead of
    given away at a single price.

        SELL 100 sh, wall price ₩86,300, tick ₩100
          20 sh market (fills now)         ← the guaranteed leg
          20 sh limit ₩86,400
          20 sh limit ₩86,500
          20 sh limit ₩86,600
          20 sh limit ₩86,700

    A BUY steps the other way (cheaper each slice). Rounding rides on the first
    slice, so the guaranteed leg is never the short one. Below `slices` shares,
    or with no book, there is no ladder - one order, as before."""
    qty = int(qty or 0)
    # A BUY IS A HISTORY QUESTION (boss 2026-09-09) — the five prices the popup
    # shows are now the five the order uses. Only a buy: a sell that must leave
    # still goes as one price in front of the biggest wall.
    if side == "BUY" and qty > 0:
        try:
            _now9 = _touch_price(code, "BUY") or float(fallback or 0)
            _hist9 = history_buy_plan(code, qty, float(_now9 or 0))
            if len(_hist9) >= 2 and all(r["qty"] >= MIN_LOT for r in _hist9):
                # WHERE HIS TWO LAWS MEET (2026-09-09). All five prices stand
                # below the market, which is right for a patient buy - "an
                # unfilled buy costs nothing". But the ladder rule's entry is a
                # TIMING decision: "at 09:04 we should buy 1000 shares" means
                # owning them at 09:04. Measured over the last ten stored days
                # on six stocks, only 54% of its buys ever traded down to even
                # the dearest of the five prices that day, and 21% inside half
                # an hour - so a purely patient entry misses half the signals
                # it just spent the morning finding. When the caller says this
                # buy is a timing decision, the first slice takes the price
                # that deals now and the other four keep their history marks.
                if touch_first and _now9:
                    _hist9[0] = {**_hist9[0], "px": float(_now9), "kind": "market",
                                 "ko": (f"1번째 {_hist9[0]['qty']:,}주 — 지금 바로 체결되는 "
                                        f"₩{_now9:,.0f}. 신호가 선 자리를 놓치지 않기 위한 "
                                        f"물량이고, 나머지 네 조각은 아래 가격에 걸어둡니다."),
                                 "en": (f"slice 1, {_hist9[0]['qty']:,} sh at ₩{_now9:,.0f} - the "
                                        f"price it deals at right now, so the signal is not "
                                        f"missed; the other four rest at the history prices below.")}
                return _hist9
        except Exception as e:
            log.debug(f"history ladder {code}: {str(e)[:60]}")
    base, ko, en = _book_price(code, side, fallback)
    # A SELL IS ONE PRICE AT THE WALL (boss 2026-09-09, twice: "in case of the
    # selling just put price one tick below highest volume", and again with the
    # ladder rule: "our price offer the highest volume numbers down side").
    # The popup has said this since commit 1ced7044 - the ORDER had not caught
    # up and was still going out as five slices a tick apart, so the screen and
    # the money disagreed on the sell side exactly as they did on the buy side.
    # Stock that has to leave does not get spread above the wall it was meant
    # to beat; it goes at one price, in front of the queue.
    if side == "SELL" and base:
        return [{"px": float(base), "qty": qty, "kind": "limit", "ko": ko, "en": en}]
    # NO SLICE BELOW THE MINIMUM LOT (boss 2026-09-09). Five slices of an order
    # of 468 gave 96 and 93 - odd lots he does not want sent. The ladder now
    # takes as many slices as it can while every one of them is a real order,
    # and sends one order when it cannot make even two.
    if qty and slices > 1:
        slices = max(1, min(int(slices), int(qty) // MIN_LOT))
    if qty < max(2, slices) or slices < 2 or not base:
        return [{"px": base, "qty": qty, "kind": "market", "ko": ko, "en": en}]
    from services.kiwoom_rules import krx_tick
    tk = krx_tick(base) or 1
    each = qty // slices
    first = qty - each * (slices - 1)          # the remainder rides the sure leg
    # THE FIRST SLICE SHOWS ITS PRICE, NOT THE WORD "MARKET" (boss 2026-09-07:
    # "just normal price I mean give number, because people may not understand
    # what is market price"). It still goes out as a market order - that is what
    # guarantees the fill - but the number written on it is the price it will
    # actually deal at: the cheapest ask for a buy, the highest bid for a sell.
    _now = _touch_price(code, side) or base
    out = [{"px": float(_now), "qty": first, "kind": "market",
            "ko": f"1번째 {first:,}주 — 지금 바로 체결되는 가격 ₩{_now:,.0f}. " + ko,
            "en": f"slice 1, {first:,} sh at ₩{_now:,.0f} - the price it deals at right now. " + en}]
    for i in range(1, slices):
        px = base + (tk * LADDER_STEP * i if side == "SELL" else -tk * LADDER_STEP * i)
        if px <= 0:
            break
        out.append({"px": float(px), "qty": each, "kind": "limit",
                    "ko": (f"{i + 1}번째 {each:,}주 — {'한 호가 위' if side == 'SELL' else '한 호가 아래'}"
                           f" ₩{px:,.0f}에 걸어둡니다."),
                    "en": (f"slice {i + 1}, {each:,} sh resting at ₩{px:,.0f} "
                           f"({'one tick higher' if side == 'SELL' else 'one tick lower'})")})
    return out


def ladder_words(rows: list[dict], side: str) -> tuple:
    """The ladder as one sentence a person can check against the book."""
    if len(rows) < 2:
        return rows[0].get("ko", ""), rows[0].get("en", "")
    _k = " · ".join(f"{r['qty']:,}주 ₩{r['px']:,.0f}"
                    + ("(지금 체결)" if r["kind"] == "market" else "") for r in rows)
    _e = " · ".join(f"{r['qty']:,}sh ₩{r['px']:,.0f}"
                    + (" (deals now)" if r["kind"] == "market" else "") for r in rows)
    return (f"{'매도' if side == 'SELL' else '매수'}를 {len(rows)}조각으로 나눕니다 — {_k}. "
            f"첫 조각은 지금 나온 값에 바로 체결되고, 나머지는 한 호가씩 "
            f"{'비싼' if side == 'SELL' else '싼'} 자리에서 기다립니다.",
            f"the {'sell' if side == 'SELL' else 'buy'} goes out in {len(rows)} slices - {_e}. "
            f"The first deals immediately at the price shown; the rest wait one tick "
            f"{'higher' if side == 'SELL' else 'lower'} each.")


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


def _why_qty(price: float, qty: int, budget: int = 0):
    """WHY THIS MANY SHARES (boss 2026-09-03 10:5x: 'for price and number of
    stock also should have explanation')."""
    bud = int(budget) or BUY_BUDGET
    cost = price * qty
    # SAY WHICH RULE PICKED THE NUMBER (boss 2026-09-09: minimum 1,000 shares,
    # 10,000 for the cheap ones) - the floor, the ceiling, or the budget
    if qty <= MIN_QTY:
        why_k = (f"비싼 종목이라 예산으로는 {int(bud // price):,}주밖에 안 되지만, "
                 f"최소 {MIN_QTY:,}주 규칙을 적용했습니다.")
        why_e = (f"the budget alone would buy only {int(bud // price):,} sh at this "
                 f"price, so the {MIN_QTY:,}-share floor applies — an expensive "
                 f"stock still gets a position worth reading.")
    elif qty >= MAX_QTY:
        why_k = (f"싼 종목이라 예산으로는 {int(bud // price):,}주까지 가능하지만, "
                 f"한 종목 최대 {MAX_QTY:,}주에서 멈췄습니다.")
        why_e = (f"the budget would allow {int(bud // price):,} sh at this price, but "
                 f"we stop at the {MAX_QTY:,}-share ceiling for any one stock.")
    else:
        why_k = f"예산 ₩{bud:,} 안에서 {MIN_QTY:,}~{MAX_QTY:,}주 사이로 정해졌습니다."
        why_e = (f"sized inside the ₩{bud:,} budget, between the {MIN_QTY:,} and "
                 f"{MAX_QTY:,} share limits.")
    ko = f"₩{price:,.0f} × {qty:,}주 = ₩{cost:,.0f} — {why_k}"
    en = f"₩{price:,.0f} x {qty:,} sh = ₩{cost:,.0f} — {why_e}"
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
    try:
        from services.kiwoom_rules import _open_official
        op = float(_open_official(code, _d0, bars[0]["open"]))
    except Exception:
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
        # THE SEND-TIME GUARD HONOURS THE SAME ONE-DAY WAIVER as the board and
        # the cascade (boss 2026-09-08). It must, or the card would say BUY and
        # the popup would still refuse to go out - the exact divergence between
        # board and popup he caught on 09-03. The snapshot the popup carries
        # records that the gate was lifted, so a replay of today can never be
        # mistaken for a day the gap simply was not there.
        from services.kiwoom_rules import gap_gate_waived as _gwv9
        if gap >= GAP_PCT and _gwv9(_d0):
            snap["gap_waived"] = True
        elif gap >= GAP_PCT:
            _tlo9 = min(b["low"] for b in bars)
            try:
                from services.kiwoom_rules import _low_official
                _tlo9 = float(_low_official(code, _d0, _tlo9) or _tlo9)
            except Exception:
                pass
            back = _tlo9 <= ref
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

    # ① THE TURN MUST STILL BE STANDING AT THE MOMENT WE SEND
    #
    # boss 2026-09-09, the 한화오션 10:53 popup: "why pop up is coming now?
    # becuase it now decreasing not singnal 3 red please check this case". He
    # is right, and this guard was the piece that should have caught it. The
    # shape is judged inside the scan and then CACHED - the popup carried a
    # signal stamped 10:49 into a minute in which price had fallen three
    # times in a row. Every other time-critical fact here is re-derived from
    # the freshest tape; the entry signal, the one fact the buy actually
    # rests on, was not. It is now, on the same bars this guard already holds,
    # so a signal that has died between the scan and the send cannot go out.
    try:
        _tk9, _tko9, _ten9, _t3 = _turn_shape(code, bars)
    except Exception:
        _tk9, _tko9, _ten9, _t3 = True, "", "", None
    snap["turn"] = bool(_tk9)
    snap["turn_at"] = _t3
    if not _tk9:
        return (False,
                f"보내는 순간 진입 신호가 살아 있지 않습니다 — {_tko9} 보내지 않습니다.",
                f"the entry signal is no longer standing at the moment of "
                f"sending - {_ten9}. Not sending.", snap)

    # ② 오늘 위치 — never chase the top of the day
    #
    # HIS TWO NAMES ARE JUDGED BY THE SHAPE, NOT BY THIS RULER (boss
    # 2026-09-09, making it a standing law: "from today, if there is no
    # 갭상승, or there is a 갭상승 and the price came back to the original
    # price or lower, then SK하이닉스 and 삼성전자 - after 3 red we should
    # buy"). His entry already contains its own anti-chase test: it needs a
    # FALL first and only then the 3 rises, so a runaway top never fires it
    # (that is why SK하이닉스 was refused at 10:00 - "no dip at all right
    # now"). Leaving this cruder day-range veto on top of it would forbid
    # the very buy he just defined, so for these two the shape decides.
    if hi > lo and str(code) not in POS_GATE_EXEMPT:
        rng = (hi - lo) / lo * 100
        pos = (px - lo) / (hi - lo) * 100
        snap.update({"pos": round(pos, 1), "range": round(rng, 2)})
        if rng >= 0.8 and pos >= 85.0:
            return (False,
                    f"지금 오늘 움직임의 {pos:.0f}% 지점(고가권)입니다 — 따라 사지 "
                    f"않습니다. 보내지 않습니다.",
                    f"it stands at {pos:.0f}% of today's range - the top of the "
                    f"day. We do not chase. Not sending.", snap)

    # ② GATE 2 - THE WEEKLY POSITION. Buy only at or under the lowest close of
    # the past week (boss 2026-09-04: "we will check the weekly position; if it
    # is lower or equal to the minimum price within the last week then we can
    # buy, otherwise do not buy").
    try:
        from services.kiwoom_rules import _daily20 as _d20g, _vol5 as _v5g
        _dd = _d20g(code, _d0)
        _low5 = float(_dd[2] or 0)
        snap["low5"] = _low5
        # MENU 3 FOLLOWS 알고3'S RULER (boss 2026-09-04). Gate 2 is no longer
        # one window: the position is averaged across week / month / 3-month /
        # 6-month and must sit in the bottom 35% of that blend. A stock cheap
        # on ONE window is not cheap. Measured on 알고3, 22 sessions:
        # +8.89% / 22 trades / 77% win, against +4.95% / 14 / 79% for the
        # single weekly low it replaces.
        from services.kiwoom_rules import _hz_stats as _hzs
        _hz = _hzs(code, _d0) or {}
        _ps, _det = [], []
        for _h, _lab in (("w", "주"), ("m", "월"), ("q", "3개월"), ("h", "6개월")):
            _lo, _hi = _hz.get(_h + "_low"), _hz.get(_h + "_hi")
            if _lo and _hi and _hi > _lo:
                _v = max(0.0, min(100.0, (px - _lo) / (_hi - _lo) * 100))
                _ps.append(_v)
                _det.append(f"{_lab} {_v:.0f}%")
        if _ps:
            _blend = sum(_ps) / len(_ps)
            snap["pos_blend"] = round(_blend, 1)
            snap["pos_detail"] = " · ".join(_det)
            # THE GUARD READS THE SAME GATE 2 AS THE BOARD (caught 2026-09-09:
            # the board said "gate 2 open" while this guard still refused on
            # the retired 35% blend - the exact board-vs-popup divergence the
            # 09-03 lesson forbids). Current law: the score averages the range
            # read with the all-days read and refuses only the TOP zone (>65),
            # and the boss's two exempt names (SK하이닉스·삼성전자, 09-09)
            # skip the position gate altogether.
            _sc9 = _blend
            try:
                from services.kiwoom_rules import pos_score as _psc9
                _sc9 = _psc9(code, px, _d0) or _blend
            except Exception:
                pass
            snap["pos_score"] = round(_sc9, 1)
            if str(code) in POS_GATE_EXEMPT:
                pass                    # his two names: position never refuses
            elif _sc9 > 65.0:
                return (False,
                        f"위치가 고점권입니다 — 관문 2 점수 {_sc9:.0f}% "
                        f"({' · '.join(_det)}). 65% 초과는 사지 않습니다. 보내지 않습니다.",
                        f"its position is in the TOP zone - gate 2 score {_sc9:.0f}% "
                        f"({' · '.join(_det)}). We do not buy above 65%. "
                        f"Not sending.", snap)
        elif _low5 and px > _low5 and str(code) not in POS_GATE_EXEMPT:
            return (False,
                    f"이번 주 최저가 ₩{_low5:,.0f}보다 위입니다 (지금 ₩{px:,.0f}) — "
                    f"보내지 않습니다.",
                    f"it stands ABOVE the week's lowest close of {_low5:,.0f} "
                    f"(now {px:,.0f}). Not sending.", snap)
    except Exception:
        pass

    # ③ GATE 3 - THE VOLUME. Today's flow must be running at least at a normal
    # week's pace by this hour (boss 2026-09-04: "if trading volume is higher
    # than average within the week, or at least a normal number, then buy").
    try:
        _avg5 = _v5g(code, _d0)
        if _avg5:
            _cum = sum(float(b.get("vol") or 0) for b in bars)
            try:
                _cum = float(_vol_scale(code, _d0, int(_cum)) * _cum)
            except Exception:
                pass
            _frac = len(bars) / 381.0
            _exp = float(_avg5) * max(_frac, 0.02)
            _pace = _cum / _exp if _exp else None
            snap["vol_pace"] = round(_pace, 2) if _pace else None
            # THE MINUTE WE ARE ABOUT TO BUY IN LEADS (boss 2026-09-07: "main
            # priority should be that minute volume"). The day can read normal
            # on a session that was busy at 09:00 and dead now, so the CURRENT
            # bar must also be trading at 1.2x its own recent average. Same
            # test 알고3 runs, so the board and the engine cannot disagree.
            _now9 = None
            if len(bars) >= 6:
                _w9 = [float(b.get("vol") or 0) for b in bars[-31:-1]]
                _a9 = (sum(_w9) / len(_w9)) if _w9 else 0.0
                if _a9 > 0:
                    _now9 = float(bars[-1].get("vol") or 0) / _a9
                    snap["vol_now"] = round(_now9, 2)
            # HIS TWO NAMES BUY ON THE SHAPE, NOT ON A VOLUME SPIKE (boss
            # 2026-09-09: "after 3 red we should buy"). Demanding 1.2x the
            # 30-minute average in the very minute of the 3rd red would veto
            # most of the moments his rule names - it refused SK하이닉스 at
            # 0.48x and 삼성전자 at 0.26x this morning. The DAY-level pace
            # check below still applies to them, so a genuinely dead tape is
            # still refused; only the per-minute spike requirement steps aside.
            if _now9 is not None and _now9 < 1.2 and str(code) not in POS_GATE_EXEMPT:
                return (False,
                        f"지금 이 분봉의 거래량이 약합니다 — 최근 30분 평균의 "
                        f"{_now9:.2f}배입니다 (1.20배 이상 필요). 사는 그 순간에 "
                        f"거래가 붙어야 합니다. 보내지 않습니다.",
                        f"the volume in THIS minute is weak - {_now9:.2f}x its own "
                        f"30-minute average (1.20x required). The moment we buy "
                        f"must have real flow behind it. Not sending.", snap)
            if _pace is not None and _pace < 1.0:
                return (False,
                        f"거래량이 평소보다 적습니다 — 지금까지 {_cum:,.0f}주로 "
                        f"주간 평균 페이스의 {_pace:.2f}배입니다 (1.0배 이상 필요). "
                        f"보내지 않습니다.",
                        f"volume is running THIN - {_cum:,.0f} shares so far, "
                        f"{_pace:.2f}x the pace a normal week-average day would "
                        f"set by now (1.0x required). Not sending.", snap)
    except Exception:
        pass

    # ④ the two average lines
    try:
        from services.kiwoom_rules import _daily20
        d = _daily20(code, _d0)
        ma20, mayr = float(d[3] or 0), float(d[4] or 0)
        snap.update({"ma20": ma20, "mayr": mayr})
        # the averages are a POSITION test by another name, and his two
        # names are exempt from position (boss 2026-09-09) - otherwise the
        # law he just wrote could never fire on a stock that has risen
        if ma20 and mayr and px > ma20 and px > mayr and str(code) not in POS_GATE_EXEMPT:
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
            # SAID THE RIGHT WAY ROUND (boss 2026-09-09: "for selling case need
            # to explain skhynix and samsungchonja INCREASED a lot so there is
            # a -1% decrease, not big decrease"). The old line claimed they had
            # FALLEN a long way - the opposite of what is true and of what he
            # asked for. Now it carries the size of the rise behind it.
            _hk9, _he9 = "", ""
            try:
                from services.kiwoom_rules import exempt_hold_line as _xhl9
                _hk9, _he9 = _xhl9(code, float(px or 0), float(base or 0))
            except Exception:
                pass
            if _hk9:
                ko.append(_hk9)
                en.append(_he9)
            else:
                ko.append("🤝 이 종목은 -1%로 팔지 않습니다 — 많이 오른 종목이라 "
                          "-1% 하락은 큰 하락이 아니라는 사장님 규칙입니다.")
                en.append("🤝 This one is NOT sold at -1% - your rule: it has "
                          "risen a lot, so a -1% dip is not a big fall.")
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
    # HIS TWO NAMES SAY THE GAP OUT LOUD, ALWAYS (boss 2026-09-09: "in the
    # buying case it should explain there is not 갭상승, or if there was a
    # 갭상승 then it should explain the market opened with 갭상승 of X% but the
    # price decreased back to yesterday's price"). Gate 2 no longer refuses
    # them, so the gap is the only gate that had anything to say - it is not a
    # three-word chip in a list, it is the first line of the story, and it
    # keeps saying it after 10:30 because for these two it is the whole reason.
    _xg9 = None
    try:
        from services.kiwoom_rules import exempt_gap as _xgf9
        _xg9 = _xgf9(code)
    except Exception:
        _xg9 = None
    if _xg9 and _xg9.get("buy_ko"):
        gk, ge = [], []                 # the full sentence replaces the chip
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
    if _xg9 and _xg9.get("buy_ko"):
        R.append(_xg9["buy_ko"])
        E.append(_xg9["buy_en"])
        # then the shape, in his own words, with the minutes and the prices
        try:
            from services.kiwoom_rules import exempt_turn as _xtf8
            _xt8 = _xtf8(code, upto=bt or "")
            if _xt8:
                R.append(_xt8["ko"])
                E.append(_xt8["en"])
        except Exception:
            pass
    # THE ONE-LINE WHY, FIRST (boss 2026-09-09: "In the why buying explaination
    # add there is not kepsangsing and volume is too high then bought it like
    # this meaning"). The two facts a person checks before anything else - did
    # it open expensive, and was there real money behind the move - with their
    # numbers, above the longer proof.
    try:
        from services.kiwoom_rules import buy_headline as _bh9
        _hk9, _he9 = _bh9(code, at=bt or "")
        if _hk9:
            R.append(_hk9)
            E.append(_he9)
    except Exception:
        pass
    if gk:
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
    # ① GATE 2 IN THE BUYING STORY TOO (boss 2026-09-04: "add these
    # explanations as gate 2... I want to see how you implement it to the
    # buying / non-buying case"). The NOT-buying verdict already prints the
    # blend; a buy must show the SAME arithmetic, or the two halves of the desk
    # would explain the same decision differently.
    try:
        from services.kiwoom_rules import _hz_stats as _hzb
        from services.paper_desk import fast_price as _fpb
        from services.kiwoom_tape import _day as _kdb
        _pxb = float((_fpb(code) or [None])[0] or 0)
        _hzb9 = _hzb(code, _kdb()) or {}
        _ppb, _rowsk, _rowse = [], [], []
        for _kb, _nb, _nbe in (("w", "1주", "1 week"), ("m", "1개월", "1 month"),
                               ("q", "3개월", "3 months"), ("h", "6개월", "6 months")):
            _lob, _hib = _hzb9.get(_kb + "_low"), _hzb9.get(_kb + "_hi")
            if _pxb and _lob and _hib and _hib > _lob:
                _vb = max(0.0, min(100.0, (_pxb - _lob) / (_hib - _lob) * 100))
                _ppb.append(_vb)
                # THE THREE PRICES, NOT JUST THE PERCENT (boss 2026-09-04:
                # "when you show gate 2 you should show the lowest, highest and
                # current price for 6 month, 3 month, 1 month, 1 week")
                _rowsk.append(f"     · {_nb} 최저 ₩{_lob:,.0f} ~ 최고 ₩{_hib:,.0f} "
                              f"→ 지금 ₩{_pxb:,.0f} = {_vb:.0f}%")
                _rowse.append(f"     · {_nbe}: low ₩{_lob:,.0f} ~ high ₩{_hib:,.0f} "
                              f"→ now ₩{_pxb:,.0f} = {_vb:.0f}%")
        # ONE STORYTELLER FOR EVERY SURFACE (boss 2026-09-07: "the formula is
        # not easily understandable — extend it and make it understandable,
        # and implement it to all other cases, buying and holding also"):
        # the same pos_story the whynot gate and the chatbot read.
        from services.kiwoom_rules import pos_story as _psb
        _stb = _psb(code, _pxb, _kdb()) if _pxb else None
        if _stb:
            _lk = _stb["ko"].split("\n")
            _le = _stb["en"].split("\n")
            R.append("① " + _lk[0])
            R.extend("     " + x for x in _lk[1:])
            E.append("① " + _le[0])
            E.extend("     " + x for x in _le[1:])
        elif _ppb:
            _blb = sum(_ppb) / len(_ppb)
            R.append(f"① 위치 — 네 구간 평균 {_blb:.0f}% (35% 이하가 매수 자리).")
            R.extend(_rowsk)
            E.append(f"① POSITION — {_blb:.0f}% averaged across four windows "
                     f"(35% or less is where we buy).")
            E.extend(_rowse)
    except Exception:
        pass
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
    # THE TURN, WITH ITS OWN NUMBERS (boss 2026-09-07 11:0x). The popup used to
    # print the engine's entry clock here; it now prints the shape counted on
    # the same 1-minute chart he checks - how far it fell, where the bottom
    # was, and at which minute the 3rd rising candle stood.
    _tok8, _tk8, _te8, _tt8 = turn_now(code)
    if _tok8:
        # no engine names in the boss's reading (2026-09-04 09:1x: "remove the
        # word 알고3") — the SIGNAL is the reason, not who else took it
        R.append("④ " + _tk8 + ". 급락 직후 매수 금지 규칙(제1조)도 통과했습니다.")
        E.append("④ " + _te8 + ". The no-buy-right-after-a-crash rule also cleared.")
    elif bt:
        R.append(f"④ 진입 신호 확인 ({bt}) — 하락이 멈추고 3번째 양봉이 섰습니다. 급락 직후 매수 금지 규칙(제1조)도 통과했습니다.")
        E.append(f"④ Entry signal confirmed ({bt}) — the fall stopped and the 3rd rising candle stood; the no-buy-right-after-a-crash rule also cleared.")
    else:
        # THIS BRANCH NO LONGER RAISES A POPUP (boss 2026-09-07 10:4x: "we have
        # to wait for their decrease, and once they stopped decreasing and in
        # the 3 red then we should buy"). The scanner refuses to ask without
        # the turn, so a card can only reach here through a board view; it says
        # what is still missing instead of offering to enter now.
        R.append("④ 진입 신호 대기 중 — 아직 하락이 멈추고 3번째 양봉이 서지 "
                 "않았습니다. 관문은 모두 열렸고, 신호가 서는 순간 제안드립니다.")
        E.append("④ Waiting for the entry signal — the fall has not stopped with "
                 "a 3rd rising candle yet. Every gate is open; the moment the "
                 "signal stands, we ask.")
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
        # real-time only: _fresh_stamps drops rows older than an hour, and
        # the line shows the news' own clock (boss 2026-09-04 12:3x: "remove
        # old days or old time news — if news is old, better do not add")
        if _bad6:
            _t6 = str(_bad6[-1].get("title") or "")[:42]
            _h6 = str(_bad6[-1].get("ts") or "")[11:16]
            R.append(f"⑥ 📰 뉴스 확인 — ⚠️ 위험 뉴스({_h6}): \"{_t6}\" — 가격을 끌어내릴 수 있는 재료라 주의합니다.")
            E.append(f"⑥ 📰 News check — ⚠️ danger news ({_h6}): \"{_t6}\" — a story that can push the price DOWN, so we stay careful.")
        elif _good6:
            _t6 = str(_good6[-1].get("title") or "")[:42]
            _h6 = str(_good6[-1].get("ts") or "")[11:16]
            R.append(f"⑥ 📰 뉴스 확인 — 좋은 뉴스가 있습니다({_h6}): \"{_t6}\" — 가격 상승에 힘을 보태는 재료입니다.")
            E.append(f"⑥ 📰 News check — GOOD news ({_h6}): \"{_t6}\" — a story that helps push the price UP.")
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
        # SOX and the chip names speak ONLY to semiconductor-related stocks
        # (boss 2026-09-04 10:3x: "SOX should be only semiconductor-related
        # stocks like SK하이닉스, 삼성전자, 삼성전기 — remove it from
        # unrelated things"). Everyone else reads NASDAQ + KOSPI.
        _semi9 = _is_semi(code, name)
        if not _semi9:
            _sx = None
        if _sx is not None or _nq is not None or _kp is not None:
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
            if _semi9:
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
            _good_wx = ((_sx or 0) >= 1.5) or ((_nq or 0) >= 1.0) or ((_kp or 0) >= 0.5)
            _bad_wx = ((_sx or 0) <= -1.5) or ((_nq or 0) <= -1.0) or ((_kp or 0) <= -0.5)
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


# ONE CARD, ONE ORDER (boss 2026-09-09, auditing the holdings: 삼성중공업 shows
# 96 shares bought at 10:30:13 by the ladder - and a SECOND order for the full
# 468 at 10:30:20, ₩9,991,800 of exposure he never approved twice. Two decide()
# calls landed inside seven seconds; each read the pending list before the other
# had written it, so both found the card and both sent orders. The page's two
# approve buttons (시장가 / 효율가) make that one mis-click away, and the state
# is a JSON file with no lock, so the guard has to live here.)
_DECIDED: dict = {}          # sid -> the moment it was answered


def decide(db, sid: int, ok: bool, qty=None, price=None) -> dict:
    import time as _t9
    _n9 = _t9.time()
    for _k9 in [k for k, v in _DECIDED.items() if _n9 - v > 7200]:
        _DECIDED.pop(_k9, None)
    if sid in _DECIDED:
        return {"ok": False, "decision": "duplicate",
                "error": "이미 처리된 제안입니다 (중복 클릭) / already handled - "
                         "this suggestion was answered a moment ago"}
    _DECIDED[sid] = _n9
    st = _load()
    p = next((x for x in (st.get("pending") or []) if x["id"] == sid), None)
    if not p:
        _DECIDED.pop(sid, None)      # nothing was sent; a real retry may come
        return {"ok": False, "error": "suggestion expired or already handled"}
    st["pending"] = [x for x in st["pending"] if x["id"] != sid]
    if not ok:
        # ANSWERED (boss 2026-09-03 14:3x: "after approve or cancel it should not
        # show popup again") - marked on the ANSWER, not when the question was
        # raised, so an unanswered popup that expires can be asked again and the
        # board never shows BUY without one.
        st.setdefault("asked", {})[p["code"]] = time.time()
        st.setdefault("log", []).append({**p, "decision": "취소", "at": _hhmm()})
        _trim_log(st)
        _save(st)
        return {"ok": True, "decision": "cancelled"}
    # the boss may edit the agent's numbers before approving (2026-09-03 09:4x)
    _q = int(qty) if qty else int(p["qty"])
    _px = float(price) if price else None
    p = dict(p, qty=_q, price=(_px if _px else p.get("price")),
             edited=bool((qty and int(qty) != int(p["qty"]))
                         or (price and float(price) != float(p.get("price") or 0))))
    from services.paper_desk import place_order
    # ── THE LADDER (boss 2026-09-07) ────────────────────────────────────────
    # His own price is never overridden: if he edited the number, that single
    # limit is what goes out. A STOP is never laddered either - an exit that
    # must complete leaves in one market order, whole. Everything else goes out
    # as five slices, the first at market so the decision always executes.
    _urgent = bool(p.get("urgent") or p.get("via") == "stop"
                   or any("손절" in str(x) or "-1%" in str(x)
                          for x in (p.get("reasons") or [])[:3]))
    _rows = None
    if not _px and not _urgent and _q >= LADDER_N:
        try:
            # a ladder-rule entry or dip-buy is a timing decision - its first
            # slice deals now (see book_ladder). Everything else stays patient.
            _tf9 = str(p.get("wave") or "") in ("entry", "add")
            _rows = book_ladder(p["code"], p["side"], float(p.get("price") or 0), _q,
                                touch_first=_tf9)
        except Exception:
            _rows = None
    # ...and a ONE-row plan is still a plan: the sell's single price at the wall
    # must go out as that limit, not fall through to a market order (which is
    # what "one price at the wall" was meant to stop).
    if _rows and (len(_rows) > 1 or str(_rows[0].get("kind")) == "limit"):
        _placed, _filled, _cost, _oids = [], 0, 0.0, []
        for _r in _rows:
            if _r["kind"] == "market":
                _o = place_order(db, p["code"], p["side"], int(_r["qty"]),
                                 order_type="market", source="semi", direct=True)
            else:
                _o = place_order(db, p["code"], p["side"], int(_r["qty"]),
                                 order_type="limit", limit_price=float(_r["px"]),
                                 source="semi", direct=True)
            _ok9 = bool(_o.get("ok"))
            _f9 = _o.get("fill_price")
            if _ok9 and _f9:
                _filled += int(_r["qty"])
                _cost += float(_f9) * int(_r["qty"])
            if _ok9:
                _oids.append(_o.get("id") or _o.get("order_id"))
            _placed.append({**{k: _r[k] for k in ("px", "qty", "kind")},
                            "ok": _ok9, "fill": _f9,
                            "oid": _o.get("id") or _o.get("order_id"),
                            "error": None if _ok9 else (_o.get("error") or "")[:60]})
        if not any(x["ok"] for x in _placed):
            st.setdefault("pending", []).append(p)
            _save(st)
            _DECIDED.pop(sid, None)          # nothing went out - he may try again
            return {"ok": False,
                    "error": (_placed[0].get("error") if _placed else "order failed")}
        p["slices"] = _placed
        p["oids"] = [o for o in _oids if o]
        # the decision's own numbers are what actually filled right now; the
        # resting slices join later through _reconcile_fills
        res = {"ok": True, "fill_price": (_cost / _filled) if _filled else None,
               "status": ("FILLED" if _filled else "OPEN"),
               "id": p["oids"][0] if p["oids"] else None}
        _q = _filled or _q
        p = dict(p, qty=_q)
    elif _px:
        res = place_order(db, p["code"], p["side"], _q, order_type="limit",
                          limit_price=_px, source="semi", direct=True)
    else:
        res = place_order(db, p["code"], p["side"], _q, order_type="market",
                          source="semi", direct=True)
    if not res.get("ok"):
        st.setdefault("pending", []).append(p)      # keep the popup, report the error
        _save(st)
        _DECIDED.pop(sid, None)                     # the order never went out
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
            # A LADDER SELLS A PIECE AT A TIME (boss 2026-09-07). Removing the
            # whole holding because the first slice filled would erase shares we
            # still own - the position shrinks by what actually sold, and only
            # an empty one leaves the book.
            # ...AND A PIECE IS A PIECE WHETHER OR NOT IT WENT AS A LADDER
            # (2026-09-09, wiring his 20% ladder): the test used to be "did this
            # decision have slices", so a plain 400-share sell out of 2,000 threw
            # away the other 1,600 - the book would have shown a flat position
            # while we still owned most of it. The test is now the only one that
            # matters: did this sale take everything?
            if _lot and int(p["qty"]) < int(_lot.get("qty") or 0):
                _left9 = int(_lot.get("qty") or 0) - int(p["qty"])
                if _left9 > 0:
                    _lot["qty"] = _left9
                else:
                    st["held"] = [h for h in st.get("held") or []
                                  if h["code"] != p["code"]]
                    st.setdefault("asked", {}).pop(p["code"], None)
            else:
                st["held"] = [h for h in st.get("held") or [] if h["code"] != p["code"]]
                # flat again - this stock may be offered once more (his rule: we
                # do not buy before selling, so the next question waits for the sale)
                st.setdefault("asked", {}).pop(p["code"], None)
    st.setdefault("asked", {})[p["code"]] = time.time()
    st.setdefault("log", []).append({**p, **_trip, "decision": "승인", "at": _hhmm(),
                                     "dealt": (not queued),
                                     "fill": (fill if not queued else None),
                                     "oid": res.get("id") or res.get("order_id")})
    _trim_log(st)
    _save(st)
    if queued:
        return {"ok": True, "decision": "queued",
                "note": f"limit ₩{float(p.get('price') or 0):,.0f} waiting in the book"}
    return {"ok": True, "decision": "approved", "fill": fill}


def _reconcile_slices(db, st, rows: list[dict]) -> None:
    """Every resting slice of a laddered decision, checked one by one.

    A slice that fills adds its own shares to the position (BUY) or closes that
    much of it (SELL); the row's price becomes the weighted average of what has
    actually filled, so the history shows the price we really got rather than
    the first leg's."""
    from sqlalchemy import text as _sqt
    for l in rows:
        _sl = l.get("slices") or []
        _changed = False
        for _s in _sl:
            if _s.get("settled") or not _s.get("oid") or _s.get("fill"):
                continue
            row = db.execute(_sqt(
                "SELECT status, fill_price, "
                "to_char(filled_at AT TIME ZONE 'Asia/Seoul','HH24:MI') "
                "FROM paper_desk_orders WHERE id=:i"), {"i": _s["oid"]}).fetchone()
            if not row:
                continue
            if str(row[0]) == "FILLED" and row[1]:
                _s["fill"], _s["settled"], _s["at"] = float(row[1]), True, (row[2] or _hhmm())
                _changed = True
                if l.get("side") == "BUY":
                    _add_lot(st, l["code"], l["name"], int(_s["qty"]),
                             float(row[1]), l.get("hhmm"), _s["at"])
                else:
                    _lot = next((h for h in st.get("held") or []
                                 if h["code"] == l["code"]), None)
                    if _lot:
                        _left = int(_lot.get("qty") or 0) - int(_s["qty"])
                        if _left > 0:
                            _lot["qty"] = _left
                        else:
                            st["held"] = [h for h in st.get("held") or []
                                          if h["code"] != l["code"]]
            elif str(row[0]) in ("CANCELLED", "REJECTED"):
                _s["settled"], _s["gave_up"] = True, True
                _changed = True
        if not _changed:
            continue
        _got = [(int(x["qty"]), float(x["fill"])) for x in _sl if x.get("fill")]
        if _got:
            _q = sum(q for q, _p in _got)
            l["fill"] = round(sum(q * p for q, p in _got) / _q, 2)
            l["qty"] = _q
            l["dealt"] = True
        l["slices_left"] = sum(1 for x in _sl if not x.get("settled") and not x.get("fill"))


def _reconcile_fills(db, st) -> None:
    """A 승인-but-미체결 limit that later fills flips to 체결 and joins holdings."""
    open_logs = [l for l in st.get("log") or []
                 if l.get("decision") == "승인" and l.get("dealt") is False and l.get("oid")]
    # A LADDERED DECISION HAS FIVE ORDERS, NOT ONE (boss 2026-09-07). Each slice
    # that later fills has to reach the holdings on its own; a row is only
    # finished when every slice of it is settled.
    _lad = [l for l in st.get("log") or []
            if l.get("decision") == "승인" and (l.get("slices") or [])]
    if _lad:
        try:
            _reconcile_slices(db, st, _lad)
        except Exception as e:
            print(f"[approval] slice reconcile skipped: {str(e)[:80]}")
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
