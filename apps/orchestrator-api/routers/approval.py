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
    five = []
    for r in sorted(rows, key=lambda x: -(x.get("score") or 0)):
        code = str(r.get("code"))
        gates = []
        gv, gbad = (_gap(code) if code in watch_codes else (None, None))
        gates.append({"k": "갭상승", "en": "gap-up open",
                      "v": (f"{gv:+.1f}%" if gv is not None else "대기/wait"),
                      "bad": bool(gbad)})
        gates.append({"k": "1개월 평균", "en": "vs 1-month avg",
                      "v": f"{r.get('mid'):+.2f}%" if r.get("mid") is not None else "-",
                      "bad": (r.get("mid") or 0) > 0})
        gates.append({"k": "1년 평균", "en": "vs 1-year avg",
                      "v": f"{r.get('midy'):+.2f}%" if r.get("midy") is not None else "-",
                      "bad": (r.get("midy") or 0) > 0})
        gates.append({"k": "연속 상승", "en": "already rising",
                      "v": f"{r.get('up3')}일↑/{r.get('upm')}월↑",
                      "bad": (r.get("up3") or 0) >= 3 or (r.get("upm") or 0) >= 2})
        z = (r.get("zone") or "-")
        gates.append({"k": "1년 구간", "en": "year zone",
                      "v": f"{z} {r.get('zone_pos')}%",
                      "bad": z == "sell"})
        news_bad = False
        try:
            from services.checklist_advice import _fresh_stamps
            news_bad = any(str(x.get("stamp")) in ("위험", "악재")
                           for x in _fresh_stamps(code, limit=2))
        except Exception:
            pass
        gates.append({"k": "위험 뉴스", "en": "danger news",
                      "v": "있음/yes" if news_bad else "없음/no", "bad": news_bad})
        blocked = [g for g in gates if g["bad"]]
        entry = {"code": code, "name": r.get("name"),
                 "score": r.get("score"), "score_100": r.get("score_100"),
                 "gates": gates, "pass": not blocked,
                 "no_buy": (blocked[0]["k"] + " — " + blocked[0]["v"]) if blocked else None}
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
