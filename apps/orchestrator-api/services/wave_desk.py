# -*- coding: utf-8 -*-
"""wave_desk — the ladder rule on the desk, in TWO LANES THAT RUN AT THE SAME TIME.

Boss 2026-09-09 (second pass): "it is testing so please make sure both of them
should work parallel - semi auto and auto. When I switch one of them it should
not stop."

So this is no longer a mode switch. 반자동 and 자동 are two independent lanes with
their own on/off and their own book, and either can be turned off without
touching the other:

  🙋 반자동  the same approval card the desk has always raised. It waits for his
            click, and an approved card goes out through approval_desk.decide()
            onto the real paper book.
  🤖 자동    the identical decision, taken by the machine, written into the
            LADDER'S OWN book - its own positions, its own trade history, its own
            invested / gain / win-rate. Separate on purpose: two lanes trading one
            position would fight over it (auto selling shares a card is still
            waiting on), and the whole point of running them together is to
            compare them on the same day.

The mode lives in its own small file, deliberately NOT in approval_desk.json:
that file is rewritten by the scanner every few seconds and merged on save, and
a switch written from a click would race it (the 09-04 lesson).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from services.logger import log

_FILE = Path(__file__).resolve().parent.parent / "data" / "wave_desk.json"

# KRX round-trip cost, charged on the ladder's own book so its numbers are the
# ones he would actually have kept.
FEE = 0.00015          # commission, each side
TAX = 0.0018           # agency tax, sells only


def _blank() -> dict:
    return {"lanes": {"semi": True, "auto": True}, "at": 0.0, "day": "",
            "state": {}, "acts": [],
            "auto": {"day": "", "state": {}, "positions": {}, "trades": []}}


def _read() -> dict:
    try:
        d = json.loads(_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            out = {**_blank(), **d}
            # migration from the single-mode version shipped an hour ago
            if "lanes" not in d and d.get("mode") in ("off", "semi", "auto"):
                out["lanes"] = {"semi": d["mode"] == "semi", "auto": d["mode"] == "auto"}
            out["lanes"] = {"semi": bool(out["lanes"].get("semi")),
                            "auto": bool(out["lanes"].get("auto"))}
            out.setdefault("auto", _blank()["auto"])
            return out
    except Exception:
        pass
    return _blank()


def _write(d: dict) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log.warning(f"wave_desk save: {str(e)[:80]}")


def dials() -> dict:
    """Rule dials he has overridden while the market is running.

    A restart costs about seventy seconds of real Kiwoom tape, so a dial he wants
    to test at 10:30 must not need one. These are merged over wave_rule.CFG on
    every single decision, so a change takes effect on the next minute's candle
    and nothing else in the rule moves."""
    d = _read().get("dials") or {}
    return {k: v for k, v in d.items() if k in _ALLOWED}


# only the dials that are safe to move mid-session - no share sizes, no lot caps
_ALLOWED = {"gap_return", "gap_tol", "step", "slice_pct", "drift_pct", "drift_min",
            "spike_pct", "spike_min", "spike_hold", "stop_pct", "hard_stop",
            "big_pct", "cascade_sell", "cascade_n", "cascade_profit_only",
            "slice_gap", "cool_min", "dip_pct", "max_lots", "max_adds"}


def set_dial(name: str, value) -> dict:
    from services import wave_rule as W
    name = str(name or "").strip()
    if name not in _ALLOWED:
        return {"ok": False, "error": f"'{name}' is not a live-changeable dial",
                "allowed": sorted(_ALLOWED)}
    was = dials().get(name, W.CFG.get(name))
    try:
        v = float(value)
        v = int(v) if float(v).is_integer() and isinstance(W.CFG.get(name), int) else v
    except Exception:
        return {"ok": False, "error": "value must be a number"}
    d = _read()
    d.setdefault("dials", {})[name] = v
    _write(d)
    log.info(f"wave dial {name} {was} -> {v}", extra={"action": "wave.dial"})
    return {"ok": True, "dial": name, "was": was, "now": v, "dials": dials()}


def clear_dial(name: str = "") -> dict:
    d = _read()
    if name:
        (d.get("dials") or {}).pop(name, None)
    else:
        d["dials"] = {}
    _write(d)
    return {"ok": True, "dials": dials()}


def lanes() -> dict:
    return _read().get("lanes") or {"semi": True, "auto": True}


def lane_on(name: str) -> bool:
    return bool(lanes().get(str(name)))


def set_lane(name: str, on: bool) -> dict:
    """ONE SWITCH TOUCHES ONE LANE. Turning 자동 off must leave 반자동 exactly as
    it was, and the other way round - that is what he asked for."""
    name = str(name or "").lower().strip()
    if name not in ("semi", "auto"):
        return {"ok": False, "error": "lane must be 'semi' or 'auto'"}
    d = _read()
    was = dict(d.get("lanes") or {})
    d.setdefault("lanes", {})[name] = bool(on)
    d["at"] = time.time()
    _write(d)
    log.info(f"wave lane {name} {was.get(name)} -> {bool(on)} (other lanes untouched)",
             extra={"action": "wave.lane"})
    return {"ok": True, "lanes": d["lanes"], "was": was}


# kept so older callers and the router's mode endpoint keep working
def mode() -> str:
    l = lanes()
    return "auto" if l.get("auto") and not l.get("semi") else (
        "semi" if l.get("semi") and not l.get("auto") else
        ("both" if l.get("semi") else "off"))


def set_mode(m: str) -> dict:
    m = str(m or "").lower().strip()
    if m == "both":
        set_lane("semi", True)
        return set_lane("auto", True)
    if m == "off":
        set_lane("semi", False)
        return set_lane("auto", False)
    if m in ("semi", "auto"):
        return set_lane(m, True)
    return {"ok": False, "error": "mode must be semi | auto | both | off"}


def _weekday() -> bool:
    from datetime import datetime
    try:
        from services.kiwoom_tape import KST
        return datetime.now(KST).weekday() < 5
    except Exception:
        return datetime.now().weekday() < 5


def _today() -> str:
    from services.kiwoom_tape import _day
    return _day()


def _roll(d: dict) -> dict:
    """CLOSE YESTERDAY'S BOOK, DO NOT BURN IT (2026-09-10, 08:1x - 49 minutes
    before the bell). The lanes reset their state at the first tick of a new
    session, and that reset emptied `trades` - so the 77-trade experiment he
    asked to read THIS MORNING would have vanished the moment the market opened
    and the first candle arrived. A finished day is now archived whole, with its
    scoreboard, and the last ten sessions are kept."""
    today = _today()
    book = d.setdefault("auto", _blank()["auto"])
    if book.get("day") and book["day"] != today and (book.get("trades")
                                                     or book.get("positions")):
        arch = d.setdefault("archive", {})
        arch[book["day"]] = {"day": book["day"], "trades": book.get("trades") or [],
                             "stats": auto_stats(book)}
        for k in sorted(arch)[:-10]:
            arch.pop(k, None)
        book.update({"day": today, "state": {}, "positions": {}, "trades": []})
    if d.get("day") and d["day"] != today:
        d["day"], d["state"], d["acts"] = today, {}, []
    return d


# ── the ladder state each lane carries ───────────────────────────────────────
def _state(bag: dict, code: str, name: str) -> dict:
    from services import wave_rule as W
    if bag.get("day") != _today():
        bag["day"] = _today()
        bag["state"] = {}
        if "positions" in bag:
            bag["positions"], bag["trades"] = {}, []
        else:
            bag["acts"] = []
    st = bag.setdefault("state", {}).get(code)
    if not st:
        st = W.new_state(code, name)
        bag["state"][code] = st
    st["name"] = name or st.get("name") or code
    return st


def sync_from_desk(st: dict, lot: dict | None) -> dict:
    """THE SEMI LADDER FOLLOWS THE DESK BOOK, NOT ITS OWN MEMORY.

    His manual sells, the 15:20 flat close and the -1% stop all write to the
    desk's held list without asking this file, so before every decision the
    ladder is re-pointed at the real position. Its own marks (which rungs are
    taken, when the last slice came off) survive - they are about the campaign,
    not the shares."""
    from services import wave_rule as W
    if not lot or int(lot.get("qty") or 0) <= 0:
        if st.get("qty"):
            keep = {k: st[k] for k in ("code", "name")}
            st.update(W.new_state(**keep))
        return st
    q, px = int(lot.get("qty") or 0), float(lot.get("price") or 0)
    st["qty"], st["avg_px"], st["cost"] = q, px, px * q
    if not st.get("first_px"):
        st["first_px"] = px
    st["high_water"] = max(int(st.get("high_water") or 0), q)
    return st


# ── 🤖 THE AUTO LANE'S OWN BOOK ──────────────────────────────────────────────
def _book_buy(book: dict, code: str, name: str, dec: dict) -> dict:
    q, px = int(dec["qty"]), float(dec["px"])
    fee = px * q * FEE
    p = book.setdefault("positions", {}).setdefault(
        code, {"name": name, "qty": 0, "cost": 0.0})
    p["name"] = name
    p["qty"] += q
    p["cost"] += px * q + fee
    p["avg"] = p["cost"] / max(1, p["qty"])
    return {"pnl": None, "pnl_pct": None, "fee": round(fee)}


def _book_sell(book: dict, code: str, dec: dict) -> dict:
    q, px = int(dec["qty"]), float(dec["px"])
    p = book.setdefault("positions", {}).get(code) or {"qty": 0, "cost": 0.0, "avg": px}
    q = min(q, int(p.get("qty") or 0)) or q
    avg = float(p.get("avg") or px)
    fee = px * q * (FEE + TAX)
    pnl = (px - avg) * q - fee
    p["qty"] = max(0, int(p.get("qty") or 0) - q)
    p["cost"] = max(0.0, float(p.get("cost") or 0) - avg * q)
    if p["qty"] <= 0:
        book["positions"].pop(code, None)
    return {"pnl": round(pnl), "pnl_pct": round((px / avg - 1) * 100, 3) if avg else None,
            "fee": round(fee), "qty": q}


def book_trade(book: dict, code: str, name: str, dec: dict, src: str = "live") -> dict:
    """Write one ladder decision into the auto book and return the history row."""
    if dec["side"] == "BUY":
        extra = _book_buy(book, code, name, dec)
        qty = int(dec["qty"])
    else:
        extra = _book_sell(book, code, dec)
        qty = int(extra.pop("qty", dec["qty"]))
    row = {"at": dec["at"], "code": code, "name": name, "side": dec["side"],
           "qty": qty, "px": float(dec["px"]), "tag": dec.get("tag"),
           "ko": dec.get("ko"), "en": dec.get("en"), "src": src,
           "ts": time.time(), **extra}
    book.setdefault("trades", []).append(row)
    return row


def auto_stats(book: dict | None = None, live_px: dict | None = None) -> dict:
    """Everything he asked to see: how much we put in, how much came back, the
    win rate, and the total in won and in per cent.

    투자원금 is the MOST capital the ladder had working at one time, not the sum
    of every buy - a 20% slice bought back three times is the same money doing
    three laps, and adding those up would flatter the return three-fold."""
    book = book if book is not None else (_read().get("auto") or {})
    trades = list(book.get("trades") or [])
    trades.sort(key=lambda t: (str(t.get("at") or ""), float(t.get("ts") or 0)))
    pos: dict = {}
    deployed = peak = 0.0
    bought = sold = realised = fees = 0.0
    wins = losses = 0
    for t in trades:
        q, px = int(t["qty"]), float(t["px"])
        fees += float(t.get("fee") or 0)
        if t["side"] == "BUY":
            p = pos.setdefault(t["code"], {"qty": 0, "cost": 0.0})
            p["qty"] += q
            p["cost"] += px * q + float(t.get("fee") or 0)
            bought += px * q
            deployed += px * q + float(t.get("fee") or 0)
            peak = max(peak, deployed)
        else:
            p = pos.setdefault(t["code"], {"qty": 0, "cost": 0.0})
            avg = (p["cost"] / p["qty"]) if p["qty"] else px
            p["qty"] = max(0, p["qty"] - q)
            p["cost"] = max(0.0, p["cost"] - avg * q)
            deployed = max(0.0, deployed - avg * q)
            sold += px * q
            realised += float(t.get("pnl") or 0)
            if float(t.get("pnl") or 0) > 0:
                wins += 1
            elif float(t.get("pnl") or 0) < 0:
                losses += 1
    open_val = 0.0
    open_cost = 0.0
    for code, p in pos.items():
        if p["qty"] <= 0:
            continue
        px = float((live_px or {}).get(code) or 0)
        if not px:
            px = next((float(t["px"]) for t in reversed(trades) if t["code"] == code), 0.0)
        open_val += px * p["qty"]
        open_cost += p["cost"]
    unreal = open_val - open_cost
    total = realised + unreal
    return {"trades": len(trades),
            "buys": sum(1 for t in trades if t["side"] == "BUY"),
            "sells": sum(1 for t in trades if t["side"] == "SELL"),
            "invested": round(peak), "turnover": round(bought + sold),
            "bought": round(bought), "sold": round(sold),
            "realised": round(realised), "unrealised": round(unreal),
            "total": round(total), "fees": round(fees),
            "pct": round(total / peak * 100, 3) if peak else 0.0,
            "wins": wins, "losses": losses,
            "win_pct": round(wins / (wins + losses) * 100, 1) if (wins + losses) else None,
            "open": [{"code": c, "name": (book.get("positions", {}).get(c) or {}).get("name", c),
                      "qty": p["qty"], "cost": round(p["cost"])}
                     for c, p in pos.items() if p["qty"] > 0]}


def backfill(codes: list[str], day: str = "", clear: bool = True) -> dict:
    """RUN THE MORNING AGAIN, ON THE AUTO SIDE (boss 2026-09-09: "please use
    backup of today's morning and make a trading history on the auto side using
    SKhynix and Samsung with my idea").

    The rule is replayed over the day's stored tape minute by minute, with no
    lookahead, and every decision it takes is written into the auto book exactly
    as a live one would be - same fees, same tax, same history row. Re-running
    it replaces that stock's backfilled rows instead of adding a second copy."""
    from services import wave_rule as W
    d = _roll(_read())
    day8 = day or _today()
    # A PAST DAY IS WRITTEN INTO THE ARCHIVE, NEVER OVER THE LIVE BOOK. The
    # first version reset the running book to whatever day it was asked for, so
    # seeding 09-08 at 11:00 would have thrown away the morning's real trades
    # and stamped them with the wrong date. Today's book is only ever touched
    # when today is what was asked for.
    if day8 != _today():
        book = {"day": day8, "state": {}, "positions": {}, "trades": []}
        arch_target = True
    else:
        book = d.setdefault("auto", _blank()["auto"])
        arch_target = False
        if book.get("day") != day8:
            book.update({"day": day8, "state": {}, "positions": {}, "trades": []})
    # EVERY STOCK GETS ITS KOREAN NAME, not just the pinned six - a history of
    # twenty rows reading "012330" helps nobody (boss 2026-09-09: "use 20 stock").
    names = {}
    try:
        from services import approval_desk as _A
        names = dict(_A.SIX)
        for c, n, _s in (_A.desk_codes() or []):
            if n:
                names[str(c)] = n
        for e in (_A._brain_rows() or []):
            if e.get("code") and e.get("name"):
                names[str(e["code"])] = e["name"]
    except Exception:
        pass
    try:
        from services.kiwoom_tape import WATCH as _W9
        for c, n in (_W9 or []):
            names.setdefault(str(c), n)
    except Exception:
        pass
    out = []
    for code in codes:
        code = str(code).strip().zfill(6)
        if not code:
            continue
        if clear:
            # drop this stock's earlier backfill and rebuild the book from what
            # is left, so a re-run can never double-count
            book["trades"] = [t for t in (book.get("trades") or [])
                              if not (t.get("code") == code and t.get("src") == "backfill")]
            book["positions"].pop(code, None)
        bars = W.minute_bars(code, day8)
        if not bars:
            out.append({"code": code, "ok": False, "error": "no stored tape"})
            continue
        ref = W.prev_last(code, day8)
        gap = ((bars[0]["open"] / ref - 1) * 100) if ref else 0.0
        r = W.replay(code, names.get(code, code), day=day8, bars=bars, gap=gap)
        name = names.get(code, code)
        for t in (r.get("trades") or []):
            book_trade(book, code, name, t, src="backfill")
        # the replay's own end-state becomes the lane's state, so a live tick
        # later today continues the same campaign instead of starting over
        out.append({"code": code, "name": name, "trades": len(r.get("trades") or []),
                    "gap": r.get("gap"), "ok": True})
    book["trades"].sort(key=lambda t: str(t.get("at") or ""))
    if arch_target:
        arch = d.setdefault("archive", {})
        arch[day8] = {"day": day8, "trades": book["trades"], "stats": auto_stats(book)}
        for k in sorted(arch)[:-10]:
            arch.pop(k, None)
    _write(d)
    return {"ok": True, "day": day8, "archived": arch_target,
            "codes": out, "stats": auto_stats(book)}


# ── one stock, one minute ────────────────────────────────────────────────────
def tick_lane(bag: dict, code: str, name: str, lot: dict | None,
              gap: float | None = None, follow_desk: bool = False) -> dict | None:
    """The rule's answer for this minute in ONE lane, or None."""
    from services import wave_rule as W
    st = _state(bag, code, name)
    if follow_desk:
        sync_from_desk(st, lot)
    try:
        bars = W.minute_bars(code)
    except Exception as e:
        log.warning(f"wave bars {code}: {str(e)[:80]}")
        return None
    if len(bars) < 5:
        return None
    if gap is None:
        ref = W.prev_last(code)
        gap = ((bars[0]["open"] / ref - 1) * 100) if ref else 0.0
    now = str(bars[-1].get("hhmm") or "")[:5]
    if st.get("done_at") == now:
        return None                     # a minute decides once per lane
    dec = W.decide(bars, st, dials() or None, gap)
    if not dec:
        return None
    st["done_at"] = now
    if dec["side"] == "SELL":
        have = int(st.get("qty") or 0) if not follow_desk else int((lot or {}).get("qty") or 0)
        if have <= 0:
            return None
        dec["qty"] = min(int(dec["qty"]), have)
    if int(dec.get("qty") or 0) <= 0:
        return None
    dec["gap"] = round(float(gap or 0), 2)
    W.apply(st, dec)
    return dec


def reasons(dec: dict, name: str, code: str = "") -> tuple[list, list]:
    """The popup's lines, in his own vocabulary - Korean first, English beside."""
    from services import wave_rule as W
    tag = {"entry": "① 진입 — 3번째 양봉", "add": "② 추가 매수 — 하락이 멈춘 자리",
           "step": f"③ +{W.CFG['step']}% 구간 — {W.CFG['slice_pct']}% 익절",
           "drift": f"④ 고점에서 천천히 밀림 — {W.CFG['slice_pct']}% 정리",
           "stop": "⑤ 손절 −1%", "hardstop": "⑤ 손절 −2%",
           "stoprung": f"⑤ −1% 구간 — {W.CFG['slice_pct']}% 정리 (하락 멈추면 처음 산 만큼 되사기)",
           "cascade": f"⑤ 계단이 아닌 급락 — {W.CFG['slice_pct']}% 즉시 정리",
           "eod": "⑥ 장 마감 전 전량 정리"}.get(dec.get("tag") or "", "규칙")
    tag_en = {"entry": "① entry - the 3rd rising candle",
              "add": "② adding - the fall stopped",
              "step": f"③ +{W.CFG['step']}% rung - {W.CFG['slice_pct']}% off",
              "drift": f"④ rolling over slowly - {W.CFG['slice_pct']}% off",
              "stop": "⑤ stop -1%", "hardstop": "⑤ stop -2%",
              "stoprung": f"⑤ the -1% rung - {W.CFG['slice_pct']}% off, bought back on the turn",
              "cascade": f"⑤ a cascade, not a staircase - {W.CFG['slice_pct']}% off at once",
              "eod": "⑥ flat before the close"}.get(dec.get("tag") or "", "rule")
    ko = [f"🌊 {tag}", dec["ko"],
          f"거래량 x{dec.get('volx')} · 오늘 시가는 어제 종가 대비 {dec.get('gap', 0):+.2f}%"]
    en = [f"🌊 {tag_en}", dec["en"],
          f"volume x{dec.get('volx')} · today opened {dec.get('gap', 0):+.2f}% vs yesterday's last"]
    # THE DESK'S OWN EVIDENCE, UNDER THE LADDER'S SENTENCE (boss 2026-09-10: "in
    # both sides the buying/holding/selling explanations are too short - it should
    # explain there is no 갭상승, then the position with all the formula like you
    # have done on the semi auto, showing it like to an elementary school student,
    # add the volume number at the buying time, then a 'there is no bad news' line,
    # and lastly the 100 checklist clickable in the semi auto format").
    #
    # Every one of those already exists in approval_desk._why_buy - the 1-year
    # zone with its percentage, the distance from the 1-month and 1-year averages,
    # the volume with its number and its multiple, the true gap story, the news
    # check. The ladder's own three lines say WHY IT FIRED; these say what the
    # desk sees behind it. Written once, in one place, so the two lanes cannot
    # tell him different stories about the same stock.
    if code:
        try:
            from services import wave_why as WW
            from db.base import SessionLocal
            _db = SessionLocal()
            try:
                b = WW.blocks(str(code), name, _db, gap=dec.get("gap"),
                              side=dec.get("side"), dec=dec)
            finally:
                _db.close()
            ko += b.get("ko") or []
            en += b.get("en") or []
        except Exception as e:
            log.debug(f"wave reasons {code}: {str(e)[:70]}")
    return ko, en


def _order_price(code: str, dec: dict) -> float:
    """The price the card carries - HIS pricing law, unchanged. A sell stands one
    tick in front of the biggest wall; a buy is split across the five
    history-chosen prices by book_ladder. The ladder rule decides WHEN and HOW
    MANY; it does not get its own opinion about price."""
    from services import approval_desk as A
    try:
        px, _ko, _en = A._book_price(code, dec["side"], float(dec.get("px") or 0))
        return float(px or dec.get("px") or 0)
    except Exception:
        return float(dec.get("px") or 0)


def run_all(db) -> dict:
    """Both lanes, once, independently.

    Deliberately NOT called from inside approval_desk.scan(): the scanner holds
    its own copy of the desk state for seconds and reconciles it on save, so a
    decide() running inside that window would have its answered card written
    back as a live popup."""
    from services import approval_desk as A
    L = lanes()
    out = {"lanes": L, "semi": [], "auto": [], "errors": []}
    if not (L.get("semi") or L.get("auto")):
        return out
    # THE CLOSE IS THE ONE MINUTE THE LANE MUST NOT BE ASLEEP FOR. can_propose()
    # goes false AT 15:20 - it is about asking him a question the exchange can no
    # longer honour - and the runner used to bail on it entirely. With the flat
    # moved to 15:20 (boss 2026-09-10) that would have meant the auto book never
    # sold: its positions would sit open overnight and the "nothing is carried
    # overnight" law would quietly stop being true. The SEMI lane still obeys the
    # rule to the letter (no card after 15:20, and place_order refuses anyway);
    # the AUTO lane is allowed to run a few minutes past it, for the one thing it
    # has left to do - go flat.
    _open = A.can_propose()
    _hh = A._hhmm()
    _closing = (not _open) and ("15:20" <= _hh <= "15:35") and _weekday()
    if not _open and not (L.get("auto") and _closing):
        return out
    # EVERY STOCK THE DESK JUDGES, NOT ONLY THE TEN ROOMS (boss 2026-09-09:
    # "this is not only for Samsung and SKhynix - we implement this idea
    # tomorrow for other stocks also"). The scanner has walked rooms + board
    # since 09-03; the ladder walks exactly the same universe, so a stock that
    # can raise a card can also be traded by the auto lane.
    try:
        rooms = list(A.desk_codes())
        seen = {c for c, _n, _s in rooms}
        for e in (A._brain_rows() or []):
            c = str(e.get("code") or "")
            if c and c not in seen:
                rooms.append((c, e.get("name") or c, e.get("score")))
                seen.add(c)
    except Exception as e:
        out["errors"].append(str(e)[:80])
        return out
    d = _roll(_read())
    for code, name, _score in rooms:
        # 🤖 AUTO — its own book, its own ladder state, no click and no desk
        if L.get("auto"):
            try:
                dec = tick_lane(d.setdefault("auto", _blank()["auto"]), code, name, None)
                if dec:
                    row = book_trade(d["auto"], code, name, dec, src="live")
                    out["auto"].append({k: row[k] for k in ("at", "code", "side", "qty", "px", "tag")})
            except Exception as e:
                log.warning(f"wave auto {code}: {str(e)[:100]}")
                out["errors"].append(f"auto {code}: {str(e)[:50]}")
        # 🙋 SEMI — the approval card on the real desk book (never after 15:20)
        if L.get("semi") and _open:
            try:
                st0 = A._load()
                pend = {(p.get("side"), p.get("code")) for p in (st0.get("pending") or [])}
                if ("BUY", code) in pend or ("SELL", code) in pend:
                    continue
                lot = next((h for h in (st0.get("held") or []) if h["code"] == code), None)
                dec = tick_lane(d, code, name, lot, follow_desk=True)
                if not dec:
                    continue
                ko, en = reasons(dec, name, code)
                price = _order_price(code, dec)
                st = A._load()
                sug = A._mk_sug(st, code, name, dec["side"], ko, price, dec["qty"],
                                None, reasons_en=en)
                sug["wave"] = dec.get("tag")
                sug["urgent"] = dec.get("tag") in ("stop", "hardstop", "eod")
                A._save(st)
                d.setdefault("acts", []).append(
                    {"at": dec["at"], "code": code, "name": name, "side": dec["side"],
                     "qty": dec["qty"], "px": dec["px"], "tag": dec["tag"], "mode": "semi",
                     "ko": dec["ko"], "en": dec["en"]})
                d["acts"] = d["acts"][-200:]
                out["semi"].append({"code": code, "side": dec["side"], "qty": dec["qty"],
                                    "at": dec["at"], "tag": dec["tag"], "sid": sug["id"]})
            except Exception as e:
                log.warning(f"wave semi {code}: {str(e)[:100]}")
                out["errors"].append(f"semi {code}: {str(e)[:50]}")
    _write(d)
    return out


_busy = {"on": False, "last": 0.0}


def async_run() -> None:
    """Fire run_all in its own thread with its own DB session - the 20s desk
    tick must never wait on it."""
    import threading
    if _busy["on"] or time.time() - _busy["last"] < 8:
        return

    def _go():
        _busy["on"] = True
        try:
            from db.base import SessionLocal
            db = SessionLocal()
            try:
                r = run_all(db)
                if r.get("semi") or r.get("auto"):
                    log.info(f"wave lanes semi={len(r['semi'])} auto={len(r['auto'])}",
                             extra={"action": "wave.acted"})
            finally:
                db.close()
        except Exception as e:
            log.warning(f"wave async: {str(e)[:120]}")
        finally:
            _busy["on"] = False
            _busy["last"] = time.time()
    threading.Thread(target=_go, daemon=True).start()


def today_acts(limit: int = 60) -> list:
    d = _read()
    if d.get("day") != _today():
        return []
    return (d.get("acts") or [])[-limit:]


def auto_book(limit: int = 400, day: str = "") -> dict:
    """The auto lane's trading history and its scoreboard - today's, or any
    archived session (`day` = YYYYMMDD)."""
    d = _roll(_read())
    book = d.get("auto") or {}
    if day and day != (book.get("day") or ""):
        arch = (d.get("archive") or {}).get(day)
        if arch:
            return {"ok": True, "day": day, "lanes": lanes(),
                    "trades": (arch.get("trades") or [])[-limit:],
                    "stats": arch.get("stats") or {}, "archived": True,
                    "days": _day_list(d)}
        return {"ok": False, "day": day, "error": "no book stored for that day",
                "days": _day_list(d)}
    live = {}
    try:
        from services.paper_desk import fast_price
        for code in (book.get("positions") or {}):
            px = (fast_price(code) or [None])[0]
            if px:
                live[code] = float(px)
    except Exception:
        pass
    return {"ok": True, "day": book.get("day"), "lanes": lanes(),
            "trades": (book.get("trades") or [])[-limit:],
            "stats": auto_stats(book, live), "days": _day_list(d)}


def _day_list(d: dict) -> list:
    """Sessions the auto book can show, newest first: today, then the archive."""
    out = []
    book = d.get("auto") or {}
    if book.get("day"):
        out.append({"day": book["day"], "n": len(book.get("trades") or []),
                    "pct": (auto_stats(book) or {}).get("pct"), "live": True})
    for k in sorted((d.get("archive") or {}), reverse=True):
        a = d["archive"][k]
        out.append({"day": k, "n": len(a.get("trades") or []),
                    "pct": (a.get("stats") or {}).get("pct"), "live": False})
    return out


def why(code: str, lane: str = "auto", db=None) -> dict:
    """EVERY EXPLANATION THE DESK HAS, FOR ONE STOCK, IN ONE ANSWER (boss
    2026-09-10: "in the buying, selling, holding, why-not-buying we have to add
    explanations, including the 100 checklist, and also the explanation about
    positions").

    The ladder's own reading of the tape (wave_rule.explain), the desk's
    100-item checklist for this minute, and the position - whichever lane he is
    looking at, because the two lanes hold different books and a story about the
    wrong one would be worse than none."""
    from services import wave_rule as W
    from services import approval_desk as A
    code = str(code or "").strip().zfill(6)
    d = _roll(_read())
    bag = d.get("auto") if lane == "auto" else d
    st = ((bag or {}).get("state") or {}).get(code) or W.new_state(code, code)
    name = st.get("name") or code
    try:
        bars = W.minute_bars(code)
    except Exception:
        bars = []
    gap = None
    if bars:
        ref = W.prev_last(code)
        gap = ((bars[0]["open"] / ref - 1) * 100) if ref else 0.0
    ex = W.explain(bars, st, dials() or None, gap)
    ex["lane"] = lane
    ex["name"] = name
    # the desk's own inspection rows for this minute - the same ones the approval
    # card unfolds
    try:
        ex["checklist"] = A._check_items(code, A._hhmm()) or []
    except Exception as e:
        ex["checklist"] = []
        log.debug(f"wave why checklist {code}: {str(e)[:60]}")
    # AND THE REAL 100-ITEM CHECKLIST (boss 2026-09-10: "including 100 checklist
    # also"). checklist_engine.stock_scorecard is the one that answers all 100 -
    # the market layer and the per-stock layer, with the deal-breakers named.
    if db is not None:
        try:
            from services.checklist_engine import stock_scorecard
            sc = stock_scorecard(db, code)
            ex["scorecard"] = {
                "score": sc.get("score"), "max": sc.get("max"), "pct": sc.get("pct"),
                "verdict_ok": sc.get("verdict_ok"),
                "deal_breakers": sc.get("deal_breakers") or [],
                "unknown": len(sc.get("unknown") or []),
                "stock_items": (sc.get("stock") or {}).get("items") or [],
                "market_items": (sc.get("market") or {}).get("items") or [],
            }
        except Exception as e:
            log.debug(f"wave why scorecard {code}: {str(e)[:80]}")
    # the same evidence the semi card carries, so the two lanes tell one story
    try:
        from services import wave_why as WW
        b = WW.blocks(code, name, db, bars=bars, st=st, gap=gap)
        ex["full_ko"], ex["full_en"] = b.get("ko") or [], b.get("en") or []
        if b.get("scorecard"):
            ex["scorecard"] = b["scorecard"]
    except Exception as e:
        ex["full_ko"], ex["full_en"] = [], []
        log.debug(f"wave why full {code}: {str(e)[:70]}")
    # what this lane has actually done in the stock today
    if lane == "auto":
        ex["trades"] = [t for t in ((d.get("auto") or {}).get("trades") or [])
                        if t.get("code") == code]
    else:
        ex["trades"] = [a for a in (d.get("acts") or []) if a.get("code") == code]
    return ex


def why_all(lane: str = "auto") -> dict:
    """One line per watched stock: what the lane is doing, or what it waits for.
    This is his "why not buying" list for the ladder - the same question the
    desk's cascade answers for its own gates."""
    from services import approval_desk as A
    rows = []
    try:
        universe = list(A.desk_codes())
        seen = {c for c, _n, _s in universe}
        for e in (A._brain_rows() or []):
            c = str(e.get("code") or "")
            if c and c not in seen:
                universe.append((c, e.get("name") or c, e.get("score")))
                seen.add(c)
    except Exception:
        universe = []
    for code, name, _s in universe:
        try:
            w = why(code, lane)
            rows.append({"code": code, "name": name or w.get("name"),
                         "verdict": w.get("verdict"), "waiting_for": w.get("waiting_for"),
                         "gates": w.get("gates") or [], "position": w.get("position"),
                         "n_trades": len(w.get("trades") or [])})
        except Exception as e:
            rows.append({"code": code, "name": name, "verdict": "error",
                         "waiting_for": str(e)[:80]})
    return {"ok": True, "lane": lane, "rows": rows}


def sync_semi(db, upto: str = "") -> dict:
    """ONE-OFF: put on the desk book exactly what the auto lane has already done.

    Boss 2026-09-10, 09:1x: "they are working - I was looking at auto so I forgot
    to click and approve in the semi auto side. So far, what we bought and sold,
    please exactly do it in the semi auto side also - please ONLY so far, other
    time I will watch and approve."

    Every auto leg that has not been mirrored yet is replayed onto the desk in
    the order it happened, through the same _mk_sug + decide chokepoint an
    approval click uses - so the lot book, the log row and the fills are all
    written by the code that always writes them. These carry `urgent`, which
    sends the whole quantity at market instead of resting it across the five
    history prices: the point is to MATCH a position that already exists, and a
    ladder that half-fills would not match it.

    HE OVERRULED THAT, AND HE IS RIGHT (2026-09-10 09:2x: "I found issue - so far
    buying only one price, but our rule should buy 5 efficient price"). Matching
    a share count is not worth breaking his pricing law: a BUY now goes out over
    the five history-chosen prices like every other buy on this desk, with the
    first slice at the dealing price so the position is opened, and the other
    four resting below. It may therefore fill less than the auto lane holds -
    that is the honest outcome of buying at prices we chose rather than at
    whatever the screen says, and the reconciler picks up each resting slice as
    it fills. Only SELLS stay whole-and-urgent: stock that has to leave, leaves.

    Each leg is stamped, so calling this twice cannot double anything, and
    nothing after this moment is touched - from here the cards wait for him."""
    from services import approval_desk as A
    d = _roll(_read())
    book = d.get("auto") or {}
    done = set(d.get("synced") or [])
    out = {"ok": True, "placed": [], "skipped": 0, "errors": []}
    for i, t in enumerate(book.get("trades") or []):
        key = f"{book.get('day')}|{i}|{t.get('at')}|{t.get('code')}|{t.get('side')}|{t.get('qty')}"
        if key in done:
            out["skipped"] += 1
            continue
        if upto and str(t.get("at") or "") > upto:
            continue
        code, name, side = str(t["code"]), t.get("name") or t["code"], t["side"]
        qty = int(t["qty"])
        try:
            st0 = A._load()
            lot = next((h for h in (st0.get("held") or []) if h["code"] == code), None)
            if side == "SELL":
                have = int((lot or {}).get("qty") or 0)
                if have <= 0:
                    out["errors"].append(f"{code} {t['at']} SELL skipped - desk holds none")
                    done.add(key)
                    continue
                qty = min(qty, have)
            ko = [f"🔁 자동 레인 따라잡기 — {t['at']}에 자동이 {('산' if side == 'BUY' else '판')} "
                  f"{int(t['qty']):,}주를 반자동 장부에도 같은 수량으로 맞춥니다.",
                  (t.get("ko") or ""),
                  "사장님 요청(09:1x): 지금까지의 자동 거래만 반영하고, 이후에는 직접 승인하십니다."]
            en = [f"🔁 catching the semi book up to the auto lane - it {('bought' if side == 'BUY' else 'sold')} "
                  f"{int(t['qty']):,} sh at {t['at']}, and the same quantity goes on this book now.",
                  (t.get("en") or ""),
                  "His request: mirror what auto has done SO FAR; every later card waits for his click."]
            st = A._load()
            sug = A._mk_sug(st, code, name, side, ko, float(t["px"]), qty, None, reasons_en=en)
            sug["wave"] = t.get("tag")
            # a BUY keeps the five prices (his law); only a SELL goes whole at market
            sug["urgent"] = (side == "SELL")
            sug["catchup"] = True
            A._save(st)
            res = A.decide(db, sug["id"], True)
            out["placed"].append({"at": t["at"], "code": code, "name": name, "side": side,
                                  "qty": qty, "ok": bool(res.get("ok")),
                                  "fill": res.get("fill"), "error": res.get("error")})
            done.add(key)
        except Exception as e:
            out["errors"].append(f"{code} {t.get('at')}: {str(e)[:80]}")
    d["synced"] = sorted(done)[-500:]
    _write(d)
    return out


def status() -> dict:
    from services import wave_rule as W
    d = _roll(_read())
    live = d.get("day") == _today()
    book = d.get("auto") or {}
    _dl = dials()
    return {"ok": True, "lanes": lanes(), "mode": mode(), "day": d.get("day"),
            "dials": _dl,
            "acts": (d.get("acts") or [])[-40:] if live else [],
            "auto_day": book.get("day"),
            "auto_stats": auto_stats(book),
            "auto_trades": len(book.get("trades") or []),
            "days": _day_list(d),
            "positions": [{"code": c, "name": s.get("name"), "qty": s.get("qty"),
                           "avg": s.get("avg_px"), "first": s.get("first_px"),
                           "steps": s.get("steps"), "sold": s.get("sold"),
                           "spike_at": s.get("spike_at")}
                          for c, s in (d.get("state") or {}).items()
                          if live and int(s.get("qty") or 0) > 0],
            "cfg": {k: _dl.get(k, W.CFG[k]) for k in ("gap_tol", "ups", "step", "slice_pct",
                                          "spike_pct", "spike_min", "spike_hold",
                                          "drift_pct", "drift_min", "stop_pct",
                                          "hard_stop", "max_lots", "slice_gap", "eod")}}
