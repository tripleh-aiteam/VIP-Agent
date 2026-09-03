# -*- coding: utf-8 -*-
"""/approval — the semi-auto approval desk API (boss 2026-09-02). See
services/approval_desk.py for the philosophy: the agent proposes, the human
clicks 승인 or 취소, nothing trades on its own."""
from fastapi import APIRouter, Depends
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
    return {"ok": True, "market_open": mkt, "rooms": rooms,
            "pending": st.get("pending") or [],
            "held": st.get("held") or [],
            "log": list(reversed((st.get("log") or [])[-40:]))}


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
def approve(sid: int, db: Session = Depends(get_db)):
    from services import approval_desk as ad
    return ad.decide(db, sid, True)


@router.post("/reject/{sid}")
def reject(sid: int, db: Session = Depends(get_db)):
    from services import approval_desk as ad
    return ad.decide(db, sid, False)


@router.get("/brain")
def brain():
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
    try:
        d = _j.load(_ur.urlopen(
            "http://127.0.0.1:8000/paper-desk/daily-pick", timeout=120))
        rows = d.get("rows") or []
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
    SIX = ["000660", "005930", "035420", "017670", "042660", "034020"]
    watch_codes = {c for c, _n in WATCH}
    # live gap check: today's open vs yesterday's close, once bars exist
    def _gap(code):
        try:
            from services.kiwoom_rules import _daily20
            pc = _daily20(code, _kd())[0]
            t = _j.load(_ur.urlopen(
                f"http://127.0.0.1:8000/paper-desk/live/tape?code={code}"
                f"&period=60&bars=1", timeout=8))
            b = (t.get("bars") or [None])[0]
            if not (pc and b and b.get("open")):
                return None, None
            g = 100.0 * (float(b["open"]) / float(pc) - 1)
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
        gates.append({
            "k": "갭상승", "en": "gap-up open",
            "v": (f"{gv:+.1f}%" if gv is not None else "대기/wait"),
            "bad": bool(gbad),
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
            "why": ("📰 최근 1시간 안에 위험·악재 뉴스가 떴습니다. "
                    "뉴스가 아직 가격을 흔드는 중입니다 → 뉴스가 정리될 때까지 "
                    "기다립니다." if news_bad else ""),
            "why_en": ("📰 A danger / bad-news stamp landed within the last "
                       "hour - it is still moving the price → WAIT until it "
                       "settles." if news_bad else "")})
        blocked = [g for g in gates if g["bad"]]
        entry = {"code": code, "name": r.get("name"),
                 "score": r.get("score"), "score_100": r.get("score_100"),
                 "gates": gates, "pass": not blocked,
                 "no_buy": (blocked[0].get("why") or
                            (blocked[0]["k"] + " — " + blocked[0]["v"]))
                           if blocked else None,
                 "no_buy_en": (blocked[0].get("why_en") or
                               (blocked[0]["en"] + " — " + blocked[0]["v"]))
                              if blocked else None,
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
    return out
