# -*- coding: utf-8 -*-
"""/approval — the semi-auto approval desk API (boss 2026-09-02). See
services/approval_desk.py for the philosophy: the agent proposes, the human
clicks 승인 or 취소, nothing trades on its own."""
from fastapi import Query, APIRouter, Depends
from sqlalchemy.orm import Session

from db.base import get_db

router = APIRouter(prefix="/approval", tags=["approval-desk"])


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
        rooms.append({"code": code, "name": m["name"], "score": m.get("score"),
                      "price": px, "chg": chg, "zone": m.get("zone"),
                      "held": lot, "pnl": pnl})
    try:
        from services.kiwoom_tape import market_open
        mkt = market_open()
    except Exception:
        mkt = False
    try:
        ad.apply_time_overrides(st.get("held") or [], st.get("log") or [])
    except Exception:
        pass
    try:
        from services.approval_desk import semi_stats
        _st9 = semi_stats(db)
    except Exception:
        _st9 = None
    _held9 = st.get("held") or []
    _log9 = [l for l in reversed((st.get("log") or [])[-40:])
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
    except Exception:
        pass
    return {"ok": True, "market_open": mkt, "rooms": rooms,
            "pending": st.get("pending") or [],
            "held": _held9,
            # rows the boss struck stay in the record but leave the board
            # (2026-09-03 12:2x, the 현대모비스 09:50 entry: "remove this, it is
            # not a good condition to buy") - never a deletion, only a display
            # filter, the same law every other board here follows
            "log": _log9, "stats": _st9}
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


@router.post("/reject/{sid}")
def reject(sid: int, db: Session = Depends(get_db)):
    from services import approval_desk as ad
    return ad.decide(db, sid, False)


_BRAIN9 = {"t": 0.0, "v": None, "busy": False}


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
    if not c["busy"] and _t.time() - c["ts"] > 6:
        c["busy"] = True

        def _run():
            try:
                d = _brain_compute()
                if d.get("ok"):
                    c["data"] = d
                    c["ts"] = _t.time()
            except Exception:
                pass
            finally:
                c["busy"] = False
        threading.Thread(target=_run, daemon=True).start()
    return c["data"] or {"ok": False, "computing": True}


def _brain_compute():
    """NEVER BLOCKS THE SERVER (boss 2026-09-03 10:1x: the brain took 170s and
    then killed the process twice - it walked 38 stocks, each touching the tape
    and the database, INLINE on the request thread, so Approve and the feed
    queued behind it). It now serves the last computed answer instantly and
    refreshes in a background thread, the same pattern the room meta already
    uses. The very first call returns an empty shell; the panel fills a moment
    later and animates client-side regardless."""
    import threading as _th, time as _tm
    if _BRAIN9["v"] is not None and _tm.time() - _BRAIN9["t"] < 6:
        return _BRAIN9["v"]
    if not _BRAIN9["busy"]:
        _BRAIN9["busy"] = True

        def _bg():
            try:
                v = _brain_compute()
                _BRAIN9["t"], _BRAIN9["v"] = _tm.time(), v
            except Exception:
                pass
            finally:
                _BRAIN9["busy"] = False
        _th.Thread(target=_bg, daemon=True).start()
    return _BRAIN9["v"] or {"ok": True, "universe": [], "six": [], "five": [],
                            "computing": True}


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
            pc = _daily20(code, _kd())[0]
            cs = _bars_for(code, 5, 60)
            if not (pc and cs and cs[0].get("open")):
                return None, None
            g = 100.0 * (float(cs[0]["open"]) / float(pc) - 1)
            return g, g >= 1.5
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
        gv, gbad = (_gap(code) if code in watch_codes else (None, None))
        # (stocks outside the collector are scored but never tape-read here)
        gates.append({
            "k": "갭상승", "en": "gap-up open",
            "v": (f"{gv:+.1f}%" if gv is not None else "대기/wait"),
            "bad": bool(gbad),
            "short": "갭상승 출발 → 대기", "short_en": "Gap-up open → WAIT",
            "why": (f"⚡ 갭상승입니다! 오늘 시가가 어제 종가보다 {gv:+.1f}% 높게 "
                    f"출발했습니다. 지금은 비싼 자리입니다 → 가격이 오늘 시가 "
                    f"아래로 내려올 때까지 기다립니다." if gbad else ""),
            "why_en": (f"⚡ GAP-UP! It opened {gv:+.1f}% above yesterday's close - "
                       f"an expensive place to buy. WAIT until the price comes back "
                       f"below today's opening price." if gbad else "")})
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
        blocked = [g for g in gates if g["bad"]]
        # ALL THE MACHINE-CHECKABLE CHECKLIST ITEMS (boss 2026-09-03 13:4x:
        # "inside each stock our agent is checking, but you did not include all
        # 100 checklist - some of them related to human so remove them - start
        # with 갭상승 and list them one by one"). The 100 items were always part
        # human judgement; 15 of them are measured, and each already carries its
        # original checklist number. They ride alongside the six gates so a card
        # can list every check the desk actually performs.
        _items = []
        for _gk, _lst in (r.get("detail") or {}).items():
            for _it in (_lst or []):
                _items.append({"k": _it.get("k"), "v": str(_it.get("v")),
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
    for e in out["six"] + out["universe"]:
        o = _own.get(e["code"])
        if o:
            e["lane"] = "SELL" if o["verdict"] == "SELL" else "HOLD"
            e["lane_why"] = o.get("why")
            e["lane_why_en"] = o.get("why_en")
            e["pnl"] = o.get("pnl")
        else:
            e["lane"] = "BUY" if e.get("pass") else "NOBUY"
            e["lane_why"] = e.get("no_buy")
            e["lane_why_en"] = e.get("no_buy_en")
    out["lanes"] = {k: [e["name"] for e in out["six"] + out["universe"]
                        if e.get("lane") == k]
                    for k in ("BUY", "NOBUY", "HOLD", "SELL")}
    out["conditions"] = len(out["six"] + out["universe"]) * 6 + len(sell_rows) * 6
    return out


@router.get("/giveup")
def giveup_table():
    """THE GIVE-UP LAW table (boss 2026-09-03): per-stock price-runaway limits
    from the year study; other stocks default to 4 ticks of their price band."""
    from services.giveup_rule import GIVEUP_WON, DEFAULT_TICKS, table
    return {"ok": True, "rows": table(), "default_ticks": DEFAULT_TICKS}
