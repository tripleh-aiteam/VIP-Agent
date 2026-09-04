# -*- coding: utf-8 -*-
"""/approval — the semi-auto approval desk API (boss 2026-09-02). See
services/approval_desk.py for the philosophy: the agent proposes, the human
clicks 승인 or 취소, nothing trades on its own."""
from fastapi import Query, APIRouter, Depends
from sqlalchemy.orm import Session

from db.base import get_db

router = APIRouter(prefix="/approval", tags=["approval-desk"])


_NOTE9 = {"t": 0.0, "v": None}

_NEWS9 = {"t": 0.0, "by_code": {}}


def _stamps_by_code(max_age_h: float = 3.0) -> dict:
    """The news intern's stamps for EVERY stock, one file read, cached 120s
    (boss 2026-09-03 18:2x: news joins the agent's judgement — per stock and
    across the whole 20). {code: [rows newest-last]} limited to fresh rows."""
    import time as _t, json as _j
    from datetime import datetime as _dt
    if _t.time() - _NEWS9["t"] < 120 and _NEWS9["by_code"]:
        return _NEWS9["by_code"]
    out: dict = {}
    try:
        from pathlib import Path as _P
        nd = _P(__file__).resolve().parent.parent / "data" / "news_intern"
        files = sorted(nd.glob("2*.jsonl"))
        if files:
            now = _dt.now()
            for ln in files[-1].read_text(encoding="utf-8").splitlines():
                try:
                    r = _j.loads(ln)
                    ts = _dt.fromisoformat(str(r.get("ts"))[:19])
                    if (now - ts).total_seconds() > max_age_h * 3600:
                        continue
                    out.setdefault(str(r.get("code")), []).append(r)
                except Exception:
                    continue
    except Exception:
        pass
    _NEWS9["t"], _NEWS9["by_code"] = _t.time(), out
    return out


def _news_of(code: str):
    """Latest non-neutral fresh stamp for one stock: {'stamp','title'} or None."""
    rows = _stamps_by_code().get(str(code)) or []
    for r in reversed(rows):
        if str(r.get("stamp")) in ("호재", "위험", "악재"):
            return {"stamp": str(r.get("stamp")), "title": str(r.get("title") or "")[:60],
                    "link": r.get("link")}
    return None


def _market_move_lines(rooms: list):
    """MARKET-WIDE MOVE → CHECK THE NEWS (boss 2026-09-03 18:2x: 'today after
    14:00 ALL stocks decreased — it should check the news what happened,
    considering the 20 stocks, and according to the news suggest
    buy/sell/hold'). Returns (ko_lines, en_lines) or None when the board is
    mixed/normal."""
    chgs = [float(r.get("chg")) for r in rooms or [] if r.get("chg") is not None]
    if len(chgs) < 8:
        return None
    n_dn = sum(1 for c in chgs if c < 0)
    n_up = sum(1 for c in chgs if c > 0)
    avg = sum(chgs) / len(chgs)
    broad_dn = n_dn >= len(chgs) * 0.75 and avg <= -0.5
    broad_up = n_up >= len(chgs) * 0.75 and avg >= 0.5
    if not (broad_dn or broad_up):
        return None
    by = _stamps_by_code()
    bads, goods = [], []
    for r in rooms:
        for s in by.get(str(r.get("code"))) or []:
            st9 = str(s.get("stamp"))
            if st9 in ("위험", "악재"):
                bads.append(s)
            elif st9 == "호재":
                goods.append(s)
    # the boss's own Naver API adds the FRESHEST market headline (minutes old)
    live_ko = live_en = None
    try:
        from services.naver_news import search_news
        arts = search_news("코스피 증시", display=2)
        if arts:
            live_ko = f"🗞 방금 나온 시장 뉴스(네이버): \"{arts[0]['title'][:52]}\""
            live_en = f"🗞 Freshest market news (Naver API): \"{arts[0]['title'][:52]}\""
    except Exception:
        pass
    if broad_dn:
        head_ko = f"🚨 시장 전체가 내리고 있습니다 — {len(chgs)}종목 중 {n_dn}개 하락 (평균 {avg:+.2f}%). 뉴스를 확인했습니다."
        head_en = f"🚨 The whole board is falling — {n_dn} of {len(chgs)} stocks down (avg {avg:+.2f}%). We checked the news."
        if bads:
            t9 = str(bads[-1].get("title") or "")[:46]
            return ([head_ko,
                     f"📰 위험 뉴스 {len(bads)}건 발견 — 최근: \"{t9}\"",
                     "→ 판단: 나쁜 뉴스가 시장을 누르고 있습니다 — 신규 매수는 보류(HOLD), 보유 종목은 -1% 규칙 그대로 지킵니다."] + ([live_ko] if live_ko else []),
                    [head_en,
                     f"📰 {len(bads)} danger stories found — latest: \"{t9}\"",
                     "→ Verdict: bad news is pressing the market — new buys on HOLD; held stocks keep the -1% rule."] + ([live_en] if live_en else []))
        return ([head_ko,
                 "📰 20종목 뉴스를 모두 확인했지만 큰 나쁜 뉴스는 없습니다.",
                 "→ 판단: 뉴스 없는 수급 하락입니다 — 서두르지 않습니다. 매수는 관문 통과를 기다리고, 보유는 -1% 규칙대로."] + ([live_ko] if live_ko else []),
                [head_en,
                 "📰 We checked the news across all 20 stocks — no big bad story.",
                 "→ Verdict: a flow-driven dip, not a news event — no hurry. Buys wait for the gates; holds keep the -1% rule."] + ([live_en] if live_en else []))
    head_ko = f"📈 시장 전체가 오르고 있습니다 — {len(chgs)}종목 중 {n_up}개 상승 (평균 {avg:+.2f}%). 뉴스를 확인했습니다."
    head_en = f"📈 The whole board is rising — {n_up} of {len(chgs)} stocks up (avg {avg:+.2f}%). We checked the news."
    if goods:
        t9 = str(goods[-1].get("title") or "")[:46]
        return ([head_ko, f"📰 호재 뉴스 {len(goods)}건 — 최근: \"{t9}\"",
                 "→ 판단: 좋은 뉴스가 가격을 밀어올리고 있습니다 — 관문을 통과하는 종목은 바로 매수 제안이 나옵니다."] + ([live_ko] if live_ko else []),
                [head_en, f"📰 {len(goods)} good-news stories — latest: \"{t9}\"",
                 "→ Verdict: good news is pushing prices up — any stock that clears the gates gets an instant BUY proposal."] + ([live_en] if live_en else []))
    return ([head_ko, "📰 특별한 호재 뉴스는 없습니다 — 수급 상승으로 판단, 규칙대로만 삽니다."] + ([live_ko] if live_ko else []),
            [head_en, "📰 No standout good news — a flow-driven rise; we buy only by the rules."] + ([live_en] if live_en else []))


def _news_offers(rooms: list | None) -> list:
    """RETIRED THE DAY IT SHIPPED (boss 2026-09-04: "in case of news the popup
    is coming - please remove this, because now news looks like the MAIN
    priority").

    He is right and it is the more important principle. The desk buys and sells
    on measured rules - the gates, the zone, the -1% line - and news is context
    that colours them, never a reason of its own. A button that says "good news,
    would you like to buy?" quietly promotes a headline above every rule we
    measured, and on a screen it looks like the loudest thing there. The news
    still gets read and still gets REPORTED; it no longer asks for money.

    Kept as an empty function rather than deleted so the shape of what was
    tried, and why it was withdrawn, stays on the record."""
    return []


def _watch_note(pending: list, n_held: int = 0, rooms: list | None = None) -> dict | None:
    """THE AGENT SAYS SOMETHING EVERY 3 MINUTES, EVEN WHEN IT HAS NOTHING TO
    PROPOSE (boss 2026-09-03: "from 13:00 there is no popup message so I am
    worrying. If it is because the condition is not matching it should give
    another popup every 3 minutes like: agent is analysing 20 stocks and there
    is no buying or selling time").

    Silence and a dead screen look identical. A real BUY/SELL proposal always
    wins - this note only exists while there is nothing pending - and it never
    asks for a decision: it reports how many stocks were judged, how many are
    blocked and by WHICH gate, how many are already answered or held, so the
    silence carries its own reason."""
    import time as _t
    from services.approval_desk import can_propose
    if not can_propose():
        # the desk has nothing to say once it can no longer act (boss
        # 2026-09-03 16:4x: the note was still talking at 16:40)
        _NOTE9["v"] = None
        return None
    if pending:
        _NOTE9["v"] = None            # a real proposal is on screen - hush
        return None
    if _NOTE9["v"] and _t.time() - _NOTE9["t"] < 180:
        return _NOTE9["v"]            # the same note stands for 3 minutes
    b = _BRAIN_CACHE.get("data") or {}
    rows = (b.get("six") or []) + (b.get("universe") or [])
    if not rows:
        return _NOTE9["v"]
    lanes = b.get("lanes") or {}
    import collections as _c
    gates, gates_en = _c.Counter(), {}
    for r in rows:
        if r.get("lane") != "NOBUY":
            continue
        for g in (r.get("gates") or []):
            if g.get("bad"):
                _k9 = str(g.get("k"))
                gates[_k9] += 1
                # the gate carries its own English name - the note must not
                # print Korean gate names inside an English sentence
                gates_en.setdefault(_k9, str(g.get("en") or _k9))
    top = gates.most_common(3)
    n_no = len(lanes.get("NOBUY") or [])
    n_done = len(lanes.get("DONE") or [])
    n_hold = int(n_held)          # what we ACTUALLY hold, not a lane count -
                                  # a held stock also sits in DONE, so the lane
                                  # read 0 while the desk held nine positions
    why_ko = ", ".join(f"{k} {v}종목" for k, v in top) or "조건 미충족"
    why_en = ", ".join(f"{gates_en.get(k, k)} {v}" for k, v in top) or "no condition met"
    # THE RULES SPEAK FIRST (boss 2026-09-04: "now news looks like main
    # priority"). The market/news paragraph used to open the card, so the first
    # thing he read every three minutes was a headline. What decides a trade is
    # the gate count; the news is context and now sits under it, in one line.
    lines_ko = [
        f"🔍 {len(rows)}개 종목을 동시에 검사했습니다 — 지금은 매수·매도 자리가 없습니다.",
        f"🚫 매수 금지 {n_no}종목 — 막은 관문: {why_ko}.",
        f"✅ 이미 결정하신 종목 {n_done}개 · 보유 중 {n_hold}개 (매도 후 다시 제안합니다).",
        "👀 계속 지켜봅니다 — 조건이 맞는 순간 바로 매수/매도 팝업을 띄웁니다.",
    ]
    lines_en = [
        f"🔍 Checked {len(rows)} stocks together — right now there is no place to buy or sell.",
        f"🚫 {n_no} blocked — the gates that stopped them: {why_en}.",
        f"✅ {n_done} already decided by you · {n_hold} held (offered again after we sell).",
        "👀 Still watching — the moment a condition matches, a BUY/SELL popup appears.",
    ]
    # THE NEWS GOES UNDER THE RULES, NOT OVER THEM (boss 2026-09-04: "now news
    # looks like main priority"). A broad market move used to be prepended, so
    # the first thing he read every three minutes was a headline and the gate
    # count came last. What decides a trade is the gates; the market/news
    # paragraph is context and now follows them.
    try:
        _mm = _market_move_lines(rooms or [])
        if _mm:
            lines_ko = lines_ko + [""] + _mm[0]
            lines_en = lines_en + [""] + _mm[1]
    except Exception:
        pass
    import datetime as _dt
    _NOTE9["t"] = _t.time()
    _NOTE9["v"] = {"offers": [],
                   "id": int(_t.time()), "hhmm": _dt.datetime.now().strftime("%H:%M"),
                   "kind": "watch", "n": len(rows),
                   "lines": lines_ko, "lines_en": lines_en}
    return _NOTE9["v"]


def _display_stats(held: list, log: list, rooms: list) -> dict:
    """THE CARD COUNTS WHAT THE BOARD SHOWS (boss 2026-09-03 16:5x: 'out of 3
    we are winning 3 so it should be 100%' — the old card read the raw DB,
    every semi order ever, hidden/deleted/test rows included, so it said 57.1%
    with 7 trips while the visible history showed 3 clean wins). Computed from
    the SAME filtered held/log rows the page renders, live prices from rooms."""
    # TODAY ONLY (boss 2026-09-04 09:5x: "today we have not sold yet" while
    # the card showed 17 trips — it was counting every stored day). The card
    # is today's scoreboard; past days live in the history's day dropdown.
    import time as _tm0
    _today0 = _tm0.strftime("%Y-%m-%d", _tm0.gmtime(_tm0.time() + 9 * 3600))
    done = [l for l in log if l.get("side") == "SELL" and l.get("fill")
            and l.get("decision") == "승인" and l.get("pnl_won") is not None
            and (l.get("day") or "") == _today0]
    wins = sum(1 for l in done if (l.get("pnl_won") or 0) > 0)
    losses = sum(1 for l in done if (l.get("pnl_won") or 0) < 0)
    net = round(sum(float(l.get("pnl_won") or 0) for l in done))
    px_of = {r.get("code"): r.get("price") for r in rooms or []}
    inv = open_unreal = 0.0
    for h in held:
        inv += float(h.get("price") or 0) * int(h.get("qty") or 0)
        _px = px_of.get(h.get("code"))
        if _px:
            open_unreal += ((float(_px) - float(h.get("price") or 0))
                            * int(h.get("qty") or 0))
    for l in done:
        if l.get("buy_price"):
            inv += float(l["buy_price"]) * int(l.get("qty") or 0)
    best = worst = None
    if done:
        _b = max(done, key=lambda l: l.get("pnl_pct") or 0)
        _w = min(done, key=lambda l: l.get("pnl_pct") or 0)
        best = {"name": _b.get("name"), "pct": _b.get("pnl_pct")}
        worst = {"name": _w.get("name"), "pct": _w.get("pnl_pct")}
    return {"trips": len(done), "wins": wins, "losses": losses,
            "win_pct": round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0,
            "net_won": net, "invested": round(inv),
            "open_n": len(held), "open_unreal": round(open_unreal),
            "best": best, "worst": worst}


@router.get("/feed")
def feed(db: Session = Depends(get_db)):
    """INSTANT: stored meta + live prices; the heavy scan runs in background."""
    from services import approval_desk as ad
    from services.paper_desk import fast_price
    ad.scan_async()
    st = ad._load()
    meta = st.get("rooms_meta")
    if not meta:                # very first boot — cheap names only, scan fills in
        meta = [{"code": c, "name": n, "score": s, "zone": None}
                for c, n, s in [(c, n, None) for c, n in ad.SIX]]
    rooms = []
    # time (and its price) overrides run BEFORE the P&L math, so an edited lot's
    # displayed entry and its % tell ONE story (boss 2026-09-03 15:0x)
    try:
        ad.apply_time_overrides(st.get("held") or [], st.get("log") or [])
    except Exception:
        pass
    held = {h["code"]: h for h in ad.held(st)}
    for m in meta:
        code = m["code"]
        px = chg = None
        try:
            px, chg, _t, _s = fast_price(code)
        except Exception:
            pass
        lot = held.get(code)
        pnl = None
        if lot and px:
            pnl = round((float(px) / float(lot["price"]) - 1) * 100, 2)
        _nw = None
        try:
            _nw = _news_of(code)     # freshest 호재/위험 stamp rides with the room
        except Exception:
            pass
        rooms.append({"code": code, "name": m["name"], "score": m.get("score"),
                      "price": px, "chg": chg, "zone": m.get("zone"),
                      "held": lot, "pnl": pnl, "news": _nw})
    try:
        from services.kiwoom_tape import market_open
        mkt = market_open()
    except Exception:
        mkt = False
    _st9 = None
    _held9 = st.get("held") or []
    # the WHOLE day's log, not a 40-row window (boss 2026-09-03 16:2x: "before
    # 12:00 there were 3 or 4 completed cases, now only 1" — the log grew past
    # 40 rows and the morning's completed sells fell out of the window)
    _log9 = [l for l in reversed((st.get("log") or [])[-200:])
             if not l.get("hidden")]
    # THE CHAT ERASER REACHES MENU 3 TOO (boss 2026-09-03 13:5x: "he said I
    # deleted and when I check it is not deleting" — the chat lane registered
    # the hide in hidden_trips.json but only menus 1/2 read that registry).
    # Display filter only, records stay; "복원해줘" brings rows back.
    try:
        from services.kiwoom_tape import _day as _kd
        from services.trip_eraser import filter_m3_held, filter_m3_log
        _d8 = _kd()
        _held9 = filter_m3_held(_held9, _d8)
        _log9 = filter_m3_log(_log9, _d8)
        # the boss's hand-added round trips land in their true place in the day
        from services.approval_desk import merge_extra_trips
        _log9 = merge_extra_trips(_log9, _d8)
    except Exception:
        pass
    # each row wears its calendar day (KST) so the history can offer a
    # day dropdown for comparing previous days (boss 2026-09-03 19:3x)
    import time as _tm9
    _log9 = [{**l, "day": (_tm9.strftime("%Y-%m-%d", _tm9.gmtime(float(l.get("ts") or 0) + 9 * 3600))
                           if l.get("ts") else "")} for l in _log9]
    # the stats card judges the SAME rows the boards render (post-filter)
    try:
        _st9 = _display_stats(_held9, _log9, rooms)
    except Exception:
        try:
            from services.approval_desk import semi_stats
            _st9 = semi_stats(db)
        except Exception:
            _st9 = None
    _pend9 = st.get("pending") or []
    _pulse9 = None
    try:
        _pulse9 = ad._market_pulse()      # 🌐 SOX + KOSPI weather (cached 5min)
    except Exception:
        pass
    # every held lot carries its LIVE price even when its stock has no room —
    # without it the holding reason lost its ①②③ lines (boss 2026-09-04 10:1x:
    # the Kia case showed only ④⑤)
    try:
        _pxmap9 = {r.get("code"): r.get("price") for r in rooms}
        for h in _held9:
            _pv9 = _pxmap9.get(h.get("code"))
            if _pv9 is None:
                try:
                    _pv9, _c9x, _t9x, _s9x = fast_price(h.get("code"))
                except Exception:
                    _pv9 = None
            h["live"] = _pv9
    except Exception:
        pass
    return {"ok": True, "market_open": mkt, "rooms": rooms, "pulse": _pulse9,
            "pending": _pend9,
            # the agent speaks every 3 minutes even with nothing to propose
            "note": _watch_note(_pend9, len(_held9), rooms),
            "held": _held9,
            # rows the boss struck stay in the record but leave the board
            # (2026-09-03 12:2x, the 현대모비스 09:50 entry: "remove this, it is
            # not a good condition to buy") - never a deletion, only a display
            # filter, the same law every other board here follows
            "log": _log9, "stats": _st9,
            "why_skip": st.get("why_skip") or {}}
@router.get("/process/{code}")
def process(code: str, db: Session = Depends(get_db)):
    from services import approval_desk as ad
    name = next((n for c, n, _s in ad.desk_codes() if c == code), code)
    return {"ok": True, "code": code, "name": name,
            "steps": ad.process_steps(db, code, name)}


@router.get("/chart/{code}")
def chart(code: str, mode: str = "min"):
    """Room charts (boss 2026-09-02: 'if we click any stock room we can load
    chart minute and monthly and yearly'). min = today's 1-minute tape bars;
    month = ~22 daily candles; year = ~250 daily candles."""
    bars = []
    if mode == "min":
        try:
            from routers.paper_desk import live_tape
            d = live_tape(code=code, period=60, tick=5, bars=400)
            for b in (d.get("bars") or []):
                bars.append({"t": (b.get("hhmm") or "")[:5], "o": b.get("open"),
                             "h": b.get("high"), "l": b.get("low"), "c": b.get("close")})
        except Exception:
            bars = []
    else:
        try:
            from services.naver_stock import daily_history
            n = 22 if mode == "month" else 250
            rows = daily_history(code, days=n)
            for r in reversed(rows):            # oldest → newest for drawing
                ds = str(r.get("date") or "")
                bars.append({"t": ds[2:] if mode == "year" else ds[5:],
                             "o": r.get("open"), "h": r.get("high"),
                             "l": r.get("low"), "c": r.get("close")})
        except Exception:
            bars = []
    return {"ok": bool(bars), "mode": mode, "code": code, "bars": bars}


@router.post("/approve/{sid}")
def approve(sid: int, qty: int = Query(0), price: float = Query(0.0),
            db: Session = Depends(get_db)):
    """THE SUGGESTION IS EDITABLE (boss 2026-09-03 09:4x: "it is a suggestion -
    if we do not like it we can edit"). qty/price default to 0 = accept the
    agent's own numbers; anything else is the boss overriding them, and the
    decision log records which."""
    from services import approval_desk as ad
    return ad.decide(db, sid, True, qty=qty or None, price=price or None)


# THE NEWS ORDER ENDPOINT IS GONE (boss 2026-09-04: "news looks like main
# priority - please remove this"). It shipped and was withdrawn the same
# morning, deliberately: a one-click "good news, would you like to buy?" path
# puts a headline above every rule we measured, and the desk's whole claim is
# that it trades on measured rules. The news is still read, still stamped, and
# still REPORTED in the watch note - it simply no longer asks for money, and
# there is no longer a path by which it can.


@router.post("/reject/{sid}")
def reject(sid: int, db: Session = Depends(get_db)):
    from services import approval_desk as ad
    return ad.decide(db, sid, False)




_BRAIN_CACHE = {"ts": 0.0, "data": None, "busy": False}


@router.get("/brain")
def brain():
    """Stale-serve wrapper (2026-09-03: the 7s browser poll piled heavy
    self-calls onto a cold server and KILLED the process — twice): answer
    instantly from the last computed payload; recompute in a background
    thread at most every 6s."""
    import threading
    import time as _t
    c = _BRAIN_CACHE
    # AFTER THE BELL THE AGENT RESTS (boss 2026-09-03 19:3x: "after 15:20 the
    # agent should not work — it should say market is closed and not check"):
    # serve the last picture with a closed flag, never spawn a recompute.
    try:
        from services.kiwoom_tape import market_open
        if not market_open():
            return {**(c["data"] or {"universe": [], "six": [], "five": []}),
                    "ok": True, "closed": True}
    except Exception:
        pass
    if not c["busy"] and _t.time() - c["ts"] > 6:
        c["busy"] = True

        def _run():
            try:
                d = _brain_compute()
                if d.get("ok"):
                    c["data"] = d
                    c["ts"] = _t.time()
                    # HAND THE VERDICTS DOWN TO THE SCANNER (boss 2026-09-03
                    # 15:2x: the board showed eight BUY cards and not one popup
                    # ever appeared). The scanner asks the brain which lane a
                    # stock is in before raising a popup and was getting an
                    # empty answer for every stock: a SECOND function of the
                    # same name further down this file shadowed the cached
                    # wrapper holding those verdicts, so the store the scanner
                    # read was never once written. The live path now publishes
                    # directly and the dead twin is gone.
                    try:
                        from services.approval_desk import publish_brain
                        publish_brain(d)
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                c["busy"] = False
        threading.Thread(target=_run, daemon=True).start()
    return c["data"] or {"ok": False, "computing": True}


def _brain_compute():
    """THE AGENT, THINKING OUT LOUD (boss 2026-09-03 06:54 #9: the agent must
    sit ABOVE the rooms, visibly checking every stock in the universe gate by
    gate - 갭상승? zone? averages? falling? news? - and continuously choosing
    the 5 beside the fixed six; a gated stock, the six included, wears a bold
    NO BUY with its reason). One cheap payload the page can animate: every
    scored stock with its per-gate verdicts, the six flagged, the current five,
    all from the same numbers the engine itself trades on."""
    import json as _j, urllib.request as _ur
    from services.kiwoom_tape import WATCH, _day as _kd
    out = {"ok": True, "universe": [], "six": [], "five": [], "day": _kd()}
    # IN-PROCESS, NEVER OVER HTTP TO OURSELVES (boss 2026-09-03 10:2x: the
    # background brain never finished. It fetched /paper-desk/daily-pick from
    # our OWN server, so while the desk was busy the request queued behind the
    # very work it was waiting on - a self-deadlock. Standalone the same
    # computation finishes in 51s; inside the server it hung forever. It now
    # calls the picker directly.
    # THE CACHED PICK, NOT A FRESH ONE (boss 2026-09-03 10:3x: the background
    # brain still never finished). Calling daily_pick.pick() directly recomputes
    # every score from the database - 51s standalone and far longer while the
    # desk is replaying beside it. The /paper-desk/daily-pick endpoint holds a
    # 60s cache of exactly this, and the room scanner has been reading it that
    # way all morning without trouble, so the brain reads it the same way.
    try:
        d = _j.load(_ur.urlopen(
            "http://127.0.0.1:8000/paper-desk/daily-pick", timeout=120))
        rows = d.get("rows") or []
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
    if not rows:
        return {"ok": False, "error": "no scored rows"}
    # the zone chips come from the same helper the rooms use
    try:
        from services.checklist_reco import _year_zone as _yz9
        for _r9 in rows:
            if _r9.get("zone") is None:
                _z9 = _yz9(str(_r9.get("code")))
                if _z9:
                    _r9["zone"], _r9["zone_pos"] = _z9.get("zone"), _z9.get("pos")
    except Exception:
        pass
    SIX = ["000660", "005930", "035420", "017670", "042660", "034020"]
    watch_codes = {c for c, _n in WATCH}
    # ONLY WHAT CAN ACTUALLY BE TRADED TODAY (boss 2026-09-03 10:2x: the panel
    # still never finished under load). It was walking all 38 scored names, but
    # a stock outside the collector has no tape today - it can be scored and
    # never bought, so weighing it costs a database round trip and buys nothing.
    # Restricted to the collector's set plus the fixed six: about half the work,
    # every row genuinely tradeable, and their daily lines are already warm in
    # the desk's cache.
    # TWENTY NAMES, FIXED (boss 2026-09-03 11:3x: "lets fix number 20 - you
    # should choose 20 stock including our 6 fixed and other popular ones and
    # our agent should analyze in parallel"). His six always, then the highest
    # scorers we actually collect tape for, padded to 20 from the rest so the
    # board is a constant size the room can read at a glance.
    _keep9 = watch_codes | set(SIX)
    _six_rows = [r for r in rows if str(r.get("code")) in set(SIX)]
    _hot = [r for r in rows if str(r.get("code")) in _keep9
            and str(r.get("code")) not in set(SIX)]
    _cold = [r for r in rows if str(r.get("code")) not in _keep9]
    rows = (_six_rows + _hot + _cold)[:20]
    # live gap check: today's open vs yesterday's close, once bars exist
    def _gap(code):
        """Today's open vs yesterday's close, read IN-PROCESS (boss 2026-09-03
        10:1x: the brain took 170s because it made one HTTP call to our own
        server per stock - 38 of them - which starved every other request,
        including Approve). _bars_for reads the same tape directly."""
        try:
            from services.kiwoom_rules import _daily20, _bars_for
            # the SAME reference the engines use: yesterday's evening print
            # when we recorded one, else the official close (boss 2026-09-03)
            from services.kiwoom_rules import _gap_ref
            pc = _gap_ref(code, _kd())
            cs = _bars_for(code, 5, 60)
            if not (pc and cs and cs[0].get("open")):
                return None, None
            g = 100.0 * (float(cs[0]["open"]) / float(pc) - 1)
            from services.proof_lab import GAP_PCT
            if g < GAP_PCT:
                return g, False
            # THE GAP PAUSE CAN BE LIFTED (boss 2026-09-03 evening): "do not buy
            # if there is a 갭상승; you can buy when it decreases and is equal
            # to yesterday's price or lower, and stops decreasing, and 3 red
            # candles (like the small blue case)". The board used to ban a
            # gap-up stock for the whole day, which is stricter than his own
            # rule and stricter than the engines - so a stock that had given
            # the whole gap back and turned could still not be proposed.
            # The release line is YESTERDAY'S CLOSE, not today's open, and the
            # three rises count through a small blue exactly as the blues law
            # does everywhere else.
            back = False
            reds = 0
            for b in cs:
                lo = float(b.get("low") or b.get("close") or 0)
                if lo and lo <= float(pc):
                    back = True
                o9, c9 = float(b.get("open") or 0), float(b.get("close") or 0)
                if o9 and c9:
                    if c9 > o9:
                        reds += 1
                    elif abs(c9 / o9 - 1) * 100 > 0.2:   # a SMALL blue continues the run
                        reds = 0
                if back and reds >= 3:
                    return g, False          # the pause is lifted for good
            return g, True
        except Exception:
            return None, None
    def _lines(code):
        """The two average lines in WON, so a verdict can name the price we are
        waiting for instead of only a percentage."""
        try:
            from services.kiwoom_rules import _daily20
            d = _daily20(code, _kd())
            return d[3], d[4]
        except Exception:
            return None, None

    five = []
    for r in sorted(rows, key=lambda x: -(x.get("score") or 0)):
        code = str(r.get("code"))
        _ma1, _mayr = _lines(code)
        # EVERY VERDICT CARRIES A REASON A PERSON CAN READ (boss 2026-09-03
        # 09:1x: "after the stock name there should be a one line explanation
        # WHY not buy - currently explanations are not good and not catchable").
        # The chip keeps the short number; `why` is the sentence, and it is the
        # sentence that appears beside a NO BUY.
        gates = []
        # THE GAP GATE MUST JUDGE EVERY STOCK IT CAN (boss 2026-09-03 evening,
        # asked directly: "are you sure things from tomorrow will implement
        # right for ALL stocks?"). It did not. This ran only for the six names
        # on the live WATCH list, so fourteen of the twenty skipped the 갭상승
        # check altogether and PASSED IT BY DEFAULT - his newest rule was
        # protecting less than a third of the board. The stored tape exists for
        # almost all of them; _gap already returns (None, None) when it cannot
        # read one, so asking unconditionally is safe and covers everything we
        # have data for.
        gv, gbad = _gap(code)
        # (stocks outside the collector are scored but never tape-read here)
        gates.append({
            "k": "갭상승", "en": "gap-up open",
            "v": (f"{gv:+.1f}%" if gv is not None else "대기/wait"),
            "bad": bool(gbad),
            "short": "갭상승 출발 → 대기", "short_en": "Gap-up open → WAIT",
            "why": (f"⚡ 갭상승입니다! 오늘 시가가 어제 종가보다 {gv:+.1f}% 높게 "
                    f"출발했습니다. 아직 비싼 자리입니다 → 어제 종가까지 "
                    f"내려오고, 하락이 멈추고, 양봉 3개가 나오면 그때 삽니다."
                    if gbad else ""),
            "why_en": (f"⚡ GAP-UP! It opened {gv:+.1f}% above yesterday's close - "
                       f"still an expensive place. We buy only after it comes "
                       f"back DOWN to yesterday's close, the fall stops, and "
                       f"three red candles confirm." if gbad else "")})
        _m = r.get("mid")
        gates.append({
            "k": "1개월 평균", "en": "vs 1-month avg",
            "v": f"{_m:+.2f}%" if _m is not None else "-",
            "bad": (_m or 0) > 0,
            "short": "1개월 평균 위 → 대기", "short_en": "Above 1-month avg → WAIT",
            "why": ((f"📈 이 종목은 지금 오르고 있습니다. 최근 1개월 평균"
                     + (f"(₩{_ma1:,.0f})" if _ma1 else "")
                     + f"보다 {_m:+.2f}% 비쌉니다. 평균 위에서 산 거래가 우리 손실의 "
                       f"85%였습니다 → 평균선 아래로 내려올 때까지 기다립니다.")
                    if (_m or 0) > 0 else ""),
            "why_en": ((f"📈 This stock is RISING right now - it trades "
                        f"{_m:+.2f}% above its 1-month average"
                        + (f" (₩{_ma1:,.0f})" if _ma1 else "")
                        + ". Buying above this line caused 85% of our losses "
                          "→ WAIT until it falls back below the average.")
                       if (_m or 0) > 0 else "")})
        _my = r.get("midy")
        gates.append({
            "k": "1년 평균", "en": "vs 1-year avg",
            "v": f"{_my:+.2f}%" if _my is not None else "-",
            "bad": (_my or 0) > 0,
            "short": "1년 평균 위 → 대기", "short_en": "Above 1-year avg → WAIT",
            "why": ((f"📈 1년 데이터로 봐도 이미 오른 상태입니다. 1년 평균"
                     + (f"(₩{_mayr:,.0f})" if _mayr else "")
                     + f"보다 {_my:+.2f}% 높습니다. 1개월·1년 두 평균 아래일 때만 "
                       f"수익이 났습니다 → 1년 평균선 아래로 내려올 때까지 "
                       f"기다립니다.") if (_my or 0) > 0 else ""),
            "why_en": ((f"📈 Over a FULL YEAR it is already up - trading "
                        f"{_my:+.2f}% above its 1-year average"
                        + (f" (₩{_mayr:,.0f})" if _mayr else "")
                        + ". Only stocks below BOTH averages made money in 12 years "
                          "of data → WAIT until it comes back under the year "
                          "line.") if (_my or 0) > 0 else "")})
        _u3, _um = (r.get("up3") or 0), (r.get("upm") or 0)
        _ris9 = _u3 >= 3 or _um >= 2
        gates.append({
            "k": "연속 상승", "en": "already rising",
            "v": f"{_u3}일↑/{_um}월↑", "bad": _ris9,
            "short": "이미 오른 상태 → 대기", "short_en": "Already risen → WAIT",
            "why": (("🔺 이미 3일 연속 올랐습니다" if _u3 >= 3
                     else "🔺 최근 2개월 모두 올랐습니다")
                    + " - 이미 오른 종목이 다시 오를 확률은 45%로 평균(50%)보다 "
                      "낮습니다 → 한 번 떨어져 평균 아래로 올 때까지 기다립니다."
                    if _ris9 else ""),
            "why_en": (("🔺 It has already risen 3 days in a row" if _u3 >= 3
                        else "🔺 It rose in BOTH of the last 2 months")
                       + " - an already-risen stock climbs again only 45% of the time, "
                         "below the 50% average → WAIT for a fall back under its "
                         "average." if _ris9 else "")})
        z = (r.get("zone") or "-")
        gates.append({
            "k": "1년 구간", "en": "year zone",
            "v": f"{z} {r.get('zone_pos')}%", "bad": z == "sell",
            "short": "고점권(매도구간) → 금지", "short_en": "SELLING zone → never buy",
            "why": (f"🎯 1년 범위의 {r.get('zone_pos')}% 지점 - 고점권"
                    f"(매도구간)입니다. 여기서는 절대 사지 않습니다 → 급락해서 "
                    f"바닥권으로 내려올 때까지 기다립니다." if z == "sell" else ""),
            "why_en": (f"🎯 It sits at {r.get('zone_pos')}% of its 1-year "
                       f"range - the SELLING zone. We never buy here → WAIT for "
                       f"a sharp fall back toward the bottom." if z == "sell" else "")})
        news_bad = False
        try:
            from services.checklist_advice import _fresh_stamps
            news_bad = any(str(x.get("stamp")) in ("위험", "악재")
                           for x in _fresh_stamps(code, limit=2))
        except Exception:
            pass
        gates.append({
            "k": "위험 뉴스", "en": "danger news",
            "v": "있음/yes" if news_bad else "없음/no", "bad": news_bad,
            "short": "위험 뉴스 발생 → 대기", "short_en": "Danger news just landed → WAIT",
            "why": ("📰 최근 1시간 안에 위험·악재 뉴스가 떴습니다. "
                    "뉴스가 아직 가격을 흔드는 중입니다 → 뉴스가 정리될 때까지 "
                    "기다립니다." if news_bad else ""),
            "why_en": ("📰 A danger / bad-news stamp landed within the last "
                       "hour - it is still moving the price → WAIT until it "
                       "settles." if news_bad else "")})
        # ⑦ WHERE IT SITS IN *TODAY'S* OWN RANGE (boss 2026-09-03, the
        # 한국전력 popup: "it is recommending to me, but it is on the selling
        # zone and continuously increasing - in the selling zone it should not
        # recommend; if it is sharply decreasing then it is ok").
        #
        # He was right and so was the data, which is why this slipped through:
        # 한국전력 sat at 5% of its ONE-YEAR range - genuinely near the bottom,
        # 6.4% under its 1-month average - while sitting at 94% of TODAY'S
        # range, +3.6% off the open. Every one of the six gates looks at daily
        # or yearly history; not one asked where the price stands inside the
        # day it is actually being bought in, so a stock could be at its
        # yearly floor and at today's ceiling and pass everything.
        #
        # 알고3 already refuses to chase (no_high_chase) - the BOARD did not,
        # so it recommended what the engine itself would not take. This closes
        # that gap. His exception needs no special case: a sharp FALL puts the
        # price near today's low, which is a low position and passes freely.
        # A day with almost no range cannot block - on a flat tape the
        # percentage is noise, so a real spread is required first.
        _pos9 = _rng9 = None
        try:
            from services.kiwoom_tape import load as _ld9, bars_time as _bt9, _day as _dy9
            _bb9 = _bt9(_ld9(code, _dy9()), 60)
            if _bb9:
                _hi9 = max(x["high"] for x in _bb9)
                _lo9 = min(x["low"] for x in _bb9)
                _px9 = _bb9[-1]["close"]
                if _hi9 > _lo9 and _lo9:
                    _rng9 = (_hi9 - _lo9) / _lo9 * 100
                    _pos9 = (_px9 - _lo9) / (_hi9 - _lo9) * 100
        except Exception:
            pass
        _hb9 = bool(_pos9 is not None and _rng9 is not None
                    and _rng9 >= 0.8 and _pos9 >= 85.0)
        gates.append({
            "k": "오늘 위치", "en": "place in today's range",
            "v": (f"{_pos9:.0f}%" if _pos9 is not None else "대기/wait"),
            "bad": _hb9,
            "short": "오늘 고가권 → 대기", "short_en": "At today's high → WAIT",
            "why": (f"📍 오늘 하루 움직임의 {_pos9:.0f}% 지점, 즉 오늘 고가권입니다 "
                    f"(오늘 저가 대비 폭 {_rng9:.1f}%). 이미 오른 자리를 따라가는 "
                    f"매수입니다 → 눌림(하락)이 나올 때까지 기다립니다."
                    if _hb9 else ""),
            "why_en": (f"📍 It stands at {_pos9:.0f}% of today's own range - the "
                       f"top of the day (range {_rng9:.1f}% off today's low). "
                       f"Buying here is chasing a move that already happened → "
                       f"WAIT for a pullback." if _hb9 else "")})
        blocked = [g for g in gates if g["bad"]]
        # ALL THE MACHINE-CHECKABLE CHECKLIST ITEMS (boss 2026-09-03 13:4x:
        # "inside each stock our agent is checking, but you did not include all
        # 100 checklist - some of them related to human so remove them - start
        # with 갭상승 and list them one by one"). The 100 items were always part
        # human judgement; 15 of them are measured, and each already carries its
        # original checklist number. They ride alongside the six gates so a card
        # can list every check the desk actually performs.
        _items = []
        from services.approval_desk import _ITEM_EN, _VAL_EN, _fmt_big, _fmt_big_en
        for _gk, _lst in (r.get("detail") or {}).items():
            for _it in (_lst or []):
                _k0 = str(_it.get("k") or "")
                _b0 = _k0.split(" (")[0]
                _e0 = (next((v for p0, v in _ITEM_EN.items() if _b0.startswith(p0)), _b0)
                       + _k0[len(_b0):])
                _r0 = str(_it.get("v"))
                _v0 = _r0
                _d0 = _r0.replace(",", "").replace("-", "")
                if _d0.isdigit() and len(_d0) > 8:
                    _v0 = _fmt_big(_r0)
                    _ve0 = _fmt_big_en(_r0)
                else:
                    _ve0 = _VAL_EN.get(_v0, _v0)
                _items.append({"k": _k0, "en": _e0, "v": _v0, "ven": _ve0,
                               "s": _it.get("s"), "g": _gk,
                               "bad": (_it.get("s") or 0) < 40})
        entry = {"code": code, "name": r.get("name"), "items": _items,
                 "score": r.get("score"), "score_100": r.get("score_100"),
                 "gates": gates, "pass": not blocked,
                 "no_buy": (blocked[0].get("why") or
                            (blocked[0]["k"] + " — " + blocked[0]["v"]))
                           if blocked else None,
                 "no_buy_en": (blocked[0].get("why_en") or
                               (blocked[0]["en"] + " — " + blocked[0]["v"]))
                              if blocked else None,
                 "no_buy_short": blocked[0].get("short") if blocked else None,
                 "no_buy_short_en": blocked[0].get("short_en") if blocked else None,
                 "blocked_n": len(blocked)}
        if code in SIX:
            out["six"].append(entry)
        else:
            out["universe"].append(entry)
            if not blocked and len(five) < 5:
                five.append(entry["name"]); entry["chosen"] = True
    out["five"] = five
    # the six keep the boss's order
    out["six"].sort(key=lambda e: SIX.index(e["code"]))

    # every row ends in one word the room can read: BUY or WAIT
    for e in out["six"] + out["universe"]:
        e["verdict"] = "BUY" if e.get("pass") else "WAIT"
        e["tradeable"] = e["code"] in watch_codes
    out["universe_n"] = len(out["universe"]) + len(out["six"])

    # ── PART 2: THE SELLING SIDE (boss 2026-09-03 11:3x: "in case of selling
    # also, in the holding list we have to make like this - for selling our
    # agent needs to check conditions, we have rules for selling so you have to
    # list them"). Same agent, second half of its work: every open ride with
    # 알고3's four exits and the two patience laws evaluated live.
    sell_rows = []
    try:
        from services.approval_desk import _algo3_board, desk_codes
        from services.kiwoom_rules import _bars_for, _daily20, _daily_pos
        board = _algo3_board([c for c, _n, _s in desk_codes()])
        for code, h in (board.get("hold") or {}).items():
            cs = _bars_for(code, 5, 60)
            if not cs:
                continue
            px = float(cs[-1]["close"])
            base = float(h.get("base") or h.get("entry") or px)
            pnl = (px / base - 1) * 100
            i0 = 0
            for k, c2 in enumerate(cs):
                if str(c2.get("hhmm") or "")[:5] >= str(h.get("buy_t") or "")[:5]:
                    i0 = k
                    break
            seg = [float(c2["close"]) for c2 in cs[i0:]] or [px]
            peak = max(seg)
            from_peak = (px / peak - 1) * 100
            armed = peak >= base * 1.01
            shelf = min([float(c2["close"]) for c2 in cs[-13:-1]] or [px])
            d20 = _daily20(code, _kd())
            dp = _daily_pos(code, px)
            in_buy_zone = bool((dp is not None and dp <= 0.20)
                               or (d20[2] and px <= d20[2] * 1.005))
            checks = [
                {"k": "고점 대비 -1.5% (종가)", "en": "1.5% down from the peak",
                 "v": f"{from_peak:+.2f}% (고점 ₩{peak:,.0f})",
                 "hit": bool(armed and from_peak <= -1.5)},
                {"k": "지지선 이탈 (12봉 최저, 이익 중)", "en": "shelf break (12-bar low, in profit)",
                 "v": f"₩{px:,.0f} vs ₩{shelf:,.0f}",
                 "hit": bool(px < shelf and pnl > 0.23)},
                {"k": "-1% 손절", "en": "-1% stop",
                 "v": f"{pnl:+.2f}%", "hit": bool(pnl <= -1.0)},
                {"k": "15:19 마감 정리", "en": "15:19 closing sweep",
                 "v": str(cs[-1].get("hhmm") or "")[:5], "hit": str(cs[-1].get("hhmm") or "")[:5] >= "15:19"},
            ]
            patience = [
                {"k": "매수구간 인내 (1년 바닥 또는 5일 최저)",
                 "en": "buying-zone patience (year bottom or 5-day low)",
                 "v": ("해당 — 손절·마감 외에는 팔지 않음" if in_buy_zone else "해당 없음"),
                 "hold": in_buy_zone},
                {"k": "수수료 구간 금지 (0~0.23%)", "en": "fee-zone ban (0-0.23%)",
                 "v": f"{pnl:+.2f}%", "hold": bool(0 < pnl <= 0.23)},
            ]
            fired = [c for c in checks if c["hit"]]
            blocked_by = [q for q in patience if q["hold"]]
            # patience never blocks the stop or the bell
            hard = any(c["hit"] for c in checks if c["k"].startswith("-1%")
                       or c["k"].startswith("15:19"))
            do_sell = bool(fired) and (hard or not blocked_by)
            sell_rows.append({
                "code": code, "name": h.get("name") or code,
                "buy_t": str(h.get("buy_t") or "")[:5],
                "base": base, "px": px, "pnl": round(pnl, 2),
                "peak": peak, "from_peak": round(from_peak, 2),
                "qty": h.get("qty"),
                "checks": checks, "patience": patience,
                "verdict": "SELL" if do_sell else "HOLD",
                "why": ((fired[0]["k"] if fired else "청산 조건 미충족")
                        if do_sell else
                        (blocked_by[0]["k"] if blocked_by and fired
                         else "청산 조건 미충족 — 파도가 아직 살아 있습니다")),
                "why_en": ((fired[0]["en"] if fired else "no exit condition met")
                           if do_sell else
                           (blocked_by[0]["en"] if blocked_by and fired
                            else "no exit condition met - the ride is still alive")),
            })
    except Exception as e:
        out["sell_err"] = str(e)[:120]
    out["selling"] = sell_rows
    # ONE VERDICT PER STOCK, FOUR LANES (boss 2026-09-03 13:0x: "show the agent
    # analysing all 20 and telling us which to buy, which not to buy, which to
    # hold and which to sell - like a simulation"). A stock we own is judged by
    # the SELL rules; everything else by the BUY gates. Nothing is in two lanes.
    _own = {r["code"]: r for r in sell_rows}
    # which stocks the ENGINE is actually in right now - the board may only
    # say BUY for these, because only these produce a popup
    _eng, _answered, _ourown = set(), set(), set()
    try:
        from services.approval_desk import _algo3_board, desk_codes as _dc9, _load as _ld9
        _eng = set((_algo3_board([c for c, _n, _s in _dc9()]).get("hold") or {}).keys())
        _st9 = _ld9()
        # what the boss has already answered, and what we already own - the
        # scanner refuses to ask about either, so the board must not show BUY
        # for them or the two would disagree again (boss 2026-09-03 14:3x)
        _answered = set((_st9.get("asked") or {}).keys())
        _ourown = {h.get("code") for h in (_st9.get("held") or [])}
    except Exception:
        pass
    for e in out["six"] + out["universe"]:
        o = _own.get(e["code"])
        if o:
            e["lane"] = "SELL" if o["verdict"] == "SELL" else "HOLD"
            e["lane_why"] = o.get("why")
            e["lane_why_en"] = o.get("why_en")
            e["pnl"] = o.get("pnl")
        elif not e.get("pass"):
            e["lane"] = "NOBUY"
            e["lane_why"] = e.get("no_buy")
            e["lane_why_en"] = e.get("no_buy_en")
        else:
            # ONE CONDITION FOR BOTH (boss 2026-09-03 14:3x: "make it BUY", and
            # "삼성전자 keeps saying BUY but the popup is not coming"). The board
            # and the scanner now read the SAME test - every gate open - so a
            # card that says BUY always has its popup. Whether 알고3 has taken
            # its own entry shape yet is shown INSIDE the popup, not used to
            # gate the question.
            if e["code"] in _answered or e["code"] in _ourown:
                e["lane"] = "DONE"
                e["lane_why"] = "모든 관문 통과 · 이미 결정하셨습니다 — 매도 후 다시 제안합니다"
                e["lane_why_en"] = ("all gates passed - already decided; it will be "
                                    "offered again after we sell")
                continue
            e["lane"] = "BUY"
            e["lane_why"] = ("모든 관문 통과 — 팝업으로 승인 요청"
                             + (" · 알고3도 진입" if e["code"] in _eng
                                else " · 알고3는 진입 신호 대기 중"))
            e["lane_why_en"] = ("all gates passed - asking approval by popup"
                                + (" · 알고3 is in too" if e["code"] in _eng
                                   else " · 알고3 still waiting for its entry shape"))
    out["lanes"] = {k: [e["name"] for e in out["six"] + out["universe"]
                        if e.get("lane") == k]
                    for k in ("BUY", "DONE", "NOBUY", "HOLD", "SELL")}
    out["conditions"] = len(out["six"] + out["universe"]) * 7 + len(sell_rows) * 6
    return out


@router.get("/giveup")
def giveup_table():
    """THE GIVE-UP LAW table (boss 2026-09-03): per-stock price-runaway limits
    from the year study; other stocks default to 4 ticks of their price band."""
    from services.giveup_rule import GIVEUP_WON, DEFAULT_TICKS, table
    return {"ok": True, "rows": table(), "default_ticks": DEFAULT_TICKS}
