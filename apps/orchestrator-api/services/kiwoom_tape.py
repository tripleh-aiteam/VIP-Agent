"""📼 kiwoom_tape — accumulate the REAL execution tape, because the exchange will not.

THE PROBLEM THIS EXISTS TO SOLVE. Kiwoom's tick chart (ka10079) returns the most recent
900 executions and nothing else. For 삼성전자 that is FORTY SECONDS of history at ~22
ticks/second, and asking for a continuation page returns the same 900 rows. So there is
no way to fetch an hour of real tick data — it has to be collected as it happens, or it
is gone. That is the whole reason this module exists, and it is the one piece of
infrastructure the artificial market never needed: a generated tape can be recomputed
from its seed forever, a real one cannot.

WHAT IT DOES. Polls each watched stock every few seconds, works out which ticks are new,
and appends them to one file per stock per day. From that file the same two aggregations
the artificial side uses — N executions per bar, or N seconds per bar — produce charts
that are directly comparable with the Proof Lab and the Strategy Lab.

DE-DUPLICATION IS THE WHOLE CORRECTNESS PROBLEM. Consecutive polls overlap heavily and
Kiwoom gives no sequence number: many ticks share a second, a price and a quantity, so a
naive key collapses genuine repeated trades into one. Instead the overlap is found by
matching a long RUN of recent ticks (see `_append_new`), which cannot align by accident
the way a single row can.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("vip.kiwoom_tape")

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent / "data" / "kiwoom_tape"

# The stocks the live desk follows. Deliberately few: every one is another poll against a
# rate-limited API, and the point is depth of tape, not breadth of coverage.
WATCH: list[tuple[str, str]] = [
    ("005930", "삼성전자"),
    # THE SCREENER'S PICKS (boss 2026-08-10, advisor's point 1: "100 checkpoints ->
    # about 5 companies"). All 39 stocks with a year of data were scored on cost,
    # liquidity, movement, continuation behaviour, flows and how our own rules did -
    # scored ONLY on 2025-07..2026-03, then the chosen five were traded on the four
    # months the screener never saw: 97% win vs 93%, half the loss of the old five.
    # NAVER ranked 38th of 39 (its tick is 0.24% of its price - one tick eats a whole
    # target) and 삼성SDI 16th; both give way to the two below. Their stored tape and
    # history stay on disk - only the collector's attention moves.
    ("012450", "한화에어로스페이스"),
    ("247540", "에코프로비엠"),
    # THE ADVISOR'S CHECKLIST PICKS (boss 2026-08-10, option B). His real 100-item list
    # replaced my first screener: 20 of its stock-selection items plus the supply-demand
    # items, scored on data before 2026-04. It named SK스퀘어 #1 and 한미반도체 #3, so
    # 현대로템 and SK하이닉스 give way. Recorded honestly: on the holdout months these
    # five earned +4.15M against the previous five's +4.64M - the boss chose the
    # advisor's method over the higher number, and the board will keep score.
    ("402340", "SK스퀘어"),
    ("042700", "한미반도체"),
]

_watch_day = ""         # the day WATCH was last chosen for


def refresh_watch(force: bool = False) -> list[tuple[str, str]]:
    """Point the collector at TODAY's five (boss 2026-08-10: the checklist chooses the
    stocks every morning, not once). Runs before the open; never mid-session, because
    swapping a stock at 11:00 would abandon half a day of its tape. Falls back to the
    current list if the picker cannot run - the desk must never be left with no stocks."""
    global _watch_day
    today = _day()
    if not force and _watch_day == today:
        return WATCH
    try:
        from services.daily_pick import load_picks, save_picks
        picks = load_picks()
        if not picks:
            res = save_picks(today)
            if res.get("ok"):
                sel = {c for c in res["picks"]}
                picks = [(r["code"], r["name"]) for r in res["rows"] if r["code"] in sel]
            else:
                picks = []
        if picks:
            WATCH[:] = picks
            _watch_day = today
            logger.info("kiwoom_tape: today's five = %s", [n for _c, n in picks])
            # the investor flows ride the same once-a-day moment: the pykrx job that
            # used to fill them died 2026-07-02 and nobody noticed for five weeks
            # (boss caught it 2026-08-11). ~40 Kiwoom calls in the quiet window.
            try:
                from services.flow_sync import sync_flows
                r = sync_flows()
                logger.info("flow_sync: +%s rows", r.get("added"))
            except Exception as e:
                logger.warning("flow_sync failed: %s", str(e)[:120])
    except Exception as e:
        logger.warning("kiwoom_tape: daily pick failed (%s) - keeping %s",
                       str(e)[:80], [n for _c, n in WATCH])
    return WATCH


POLL_SEC = 3.0          # comfortably inside the ~40s the API remembers
OVERLAP = 250           # how many recent ticks to match on when finding the new part

_lock = threading.Lock()
_mem: dict[str, list[dict]] = {}        # code -> today's ticks, chronological
# WHICH DAY the memory belongs to. Without this stamp, ticks cached yesterday evening
# were served as "today" the next morning (boss 2026-08-07: "if I click today it is
# showing old data"), and the first poll of the new day would have APPENDED today's
# ticks onto yesterday's - the day-boundary contamination bug, again, via the cache.
_mem_day: dict[str, str] = {}
_state: dict[str, Any] = {"running": False, "last": {}, "errors": {}, "polls": 0,
                          "gaps": {}}          # code -> [(from, to, seconds), ...]


def _day() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


def _path(code: str, day: str | None = None) -> Path:
    return ROOT / f"{code}_{day or _day()}.jsonl"


def market_open(now: datetime | None = None) -> bool:
    """KRX continuous session, 09:00-15:30 KST on a weekday. Outside it the tape does not
    grow, and polling would only burn rate limit for rows that never change."""
    n = now or datetime.now(KST)
    if n.weekday() >= 5:
        return False
    return (n.hour, n.minute) >= (9, 0) and (n.hour, n.minute) <= (15, 30)


def _fetch(code: str) -> list[dict]:
    """The newest ~900 executions, oldest first. Kiwoom returns newest first."""
    from services.kiwoom_rest import _request
    d = _request("ka10079", {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1"},
                 path="/api/dostk/chart")
    rows = (d or {}).get("stk_tic_chart_qry") or []
    out = []
    for r in rows:
        try:
            t = str(r.get("cntr_tm") or "")
            if len(t) != 14:
                continue
            out.append({
                "t": f"{t[8:10]}:{t[10:12]}:{t[12:14]}",
                "ts": t,
                "px": abs(float(r.get("cur_prc") or 0)),
                "qty": int(float(r.get("trde_qty") or 0)),
            })
        except Exception:
            continue
    out.reverse()                        # chronological
    # ONLY TODAY'S TICKS. ka10079 always returns the most recent ~900 executions, so the
    # first poll of a new day - before the market opens, or in the first seconds after -
    # returns YESTERDAY's closing tape. The day file was empty, so all 900 were accepted
    # as new and written into today's file (found 2026-08-05: 삼성전자's file began at
    # 15:19 the previous afternoon).
    #
    # That is not a cosmetic problem. The bar series then runs ...15:30:16 ₩240,000 ->
    # 09:00:05 ₩254,000, and a +5.8% overnight gap becomes an ordinary bar-to-bar move
    # that rules trade: 2 up / +0.5% "bought" at 15:19 and "sold" at 09:00 for +7.292%.
    # Every one of those wins is fictional - you cannot hold through the close and the
    # rule never intended to.
    today = _day()
    return [x for x in out if x["px"] > 0 and x["ts"][:8] == today]


def _append_new(have: list[dict], page: list[dict]) -> list[dict]:
    """The ticks in `page` that are not already in `have`.

    Both are chronological. The pages overlap heavily, and there is no sequence number to
    join on, so the overlap is located by matching a RUN: find the longest suffix of
    `have` that is also a prefix of `page`. A run of 250 identical ticks cannot line up by
    chance, whereas a single (time, price, qty) row very easily can — several genuine
    trades a second share all three.
    """
    if not have:
        return page
    key = lambda x: (x["ts"], x["px"], x["qty"])            # noqa: E731
    tail = [key(x) for x in have[-OVERLAP:]]
    head = [key(x) for x in page]
    # try the longest overlap first so a short accidental match cannot win
    for n in range(min(len(tail), len(head)), 0, -1):
        if tail[-n:] == head[:n]:
            return page[n:]
    # No overlap at all: either the poll was too slow and ticks were missed, or this is a
    # fresh start. Say so rather than silently guessing — a gap in a tape used for
    # backtesting is worse than a loud complaint.
    if have and page and page[0]["ts"] > have[-1]["ts"]:
        logger.warning("kiwoom_tape: gap - stored ends %s, page starts %s",
                       have[-1]["ts"], page[0]["ts"])
        return page
    return [x for x in page if x["ts"] > have[-1]["ts"]]


def gaps(code: str, min_sec: int = 60) -> list[dict]:
    """Holes in the stored tape.

    Every backend restart during market hours punches one: the collector stops, Kiwoom
    remembers only ~40 seconds, and whatever traded in between is gone for good — it
    cannot be backfilled from anywhere. A spliced tape drawn as one continuous line would
    quietly imply prices that were never observed, so the holes are recorded and shown
    (found 2026-08-04: a 419s hole at 09:42-09:49, caused by my own deploy).
    """
    tk = load(code)
    out = []
    for a_, b_ in zip(tk, tk[1:]):
        def sec(x):
            t = x["ts"]
            return int(t[8:10]) * 3600 + int(t[10:12]) * 60 + int(t[12:14])
        d = sec(b_) - sec(a_)
        if d >= min_sec:
            out.append({"from": a_["t"], "to": b_["t"], "seconds": d})
    return out


def load(code: str, day: str | None = None) -> list[dict]:
    """Today's stored ticks for one stock, chronological."""
    with _lock:
        if day in (None, _day()) and code in _mem and _mem_day.get(code) == _day():
            return list(_mem[code])
    p = _path(code, day)
    if not p.exists():
        return []
    out = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    # cache ONLY the actual today - a stored past day must never impersonate it
    if day in (None, _day()):
        with _lock:
            _mem[code] = list(out)
            _mem_day[code] = _day()
    return out


def poll_once(code: str) -> int:
    """Fetch, work out what is new, append it. Returns how many ticks were added."""
    page = _fetch(code)
    if not page:
        return 0
    with _lock:
        have = _mem.get(code) if _mem_day.get(code) == _day() else None
    if have is None:
        have = load(code)
    new = _append_new(have, page)
    if not new:
        return 0
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(_path(code), "a", encoding="utf-8") as f:
        for x in new:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    with _lock:
        _mem[code] = have + new
        _mem_day[code] = _day()
        _state["last"][code] = new[-1]["t"]
    return len(new)


# ── THE ORDER-BOOK TAPE (boss 2026-08-11, feedback 4). His idea: stand one tick in
# front of the biggest bid wall to buy, sell one tick before the biggest ask wall. The
# execution ticks say nothing about the resting book, and Kiwoom keeps no history - so
# from today the collector snapshots the 10-level book alongside the ticks. One stock
# per cycle, round-robin: each of six stocks every ~18s, gentle on the rate limit, and
# walls live for minutes. book_{code}_{day}.jsonl, one snapshot per line.
_book_rr = {"i": 0}


def _snap_book_next() -> None:
    if not WATCH:
        return
    code, _name = WATCH[_book_rr["i"] % len(WATCH)]
    _book_rr["i"] += 1
    try:
        from services.kiwoom_rest import order_book
        ob = order_book(code)
        if not ob or not ob.get("levels"):
            return
        import json as _json
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _Z
        now = _dt.now(_Z("Asia/Seoul"))
        row = {"ts": now.strftime("%H%M%S"),
               "bids": [[lv["price"], lv["qty"]] for lv in ob["levels"] if lv["side"] == "bid"],
               "asks": [[lv["price"], lv["qty"]] for lv in ob["levels"] if lv["side"] == "ask"]}
        p = ROOT / f"book_{code}_{_day()}.jsonl"
        with p.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(row, ensure_ascii=False) + chr(10))
    except Exception as e:
        logger.debug("book snap %s: %s", code, str(e)[:80])


def load_book(code: str, day: str = "") -> list[dict]:
    """The day's book snapshots, oldest first. [] when none were recorded."""
    import json as _json
    p = ROOT / f"book_{code}_{day or _day()}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(_json.loads(line))
        except Exception:
            continue
    return out


def _loop():
    _state["running"] = True
    # A RESTART DURING MARKET HOURS came back on the hardcoded default list, because the
    # daily re-point only ran in the quiet window - the desk collected the wrong five
    # for 21 seconds on 2026-08-11 before it was caught. On startup the saved picks are
    # from before the bell, so honouring them mid-session abandons nothing.
    refresh_watch(force=True)
    while True:
        try:
            if not market_open():
                # the quiet window before the bell is when the checklist chooses
                refresh_watch()
            if market_open():
                for code, _name in WATCH:
                    try:
                        n = poll_once(code)
                        _state["polls"] += 1
                        if n:
                            _state["errors"].pop(code, None)
                    except Exception as e:                       # one bad stock must not
                        _state["errors"][code] = str(e)[:200]    # stop the others
                _snap_book_next()
                time.sleep(POLL_SEC)
            else:
                time.sleep(30)
        except Exception as e:                                   # the loop itself must live
            logger.warning("kiwoom_tape loop: %s", e)
            time.sleep(10)


def start() -> None:
    """Begin collecting in the background. Idempotent."""
    if _state.get("thread"):
        return
    th = threading.Thread(target=_loop, name="kiwoom-tape", daemon=True)
    _state["thread"] = th
    th.start()
    logger.info("kiwoom_tape: collector started for %s", [c for c, _ in WATCH])


def status() -> dict[str, Any]:
    out = {"running": bool(_state.get("thread")), "market_open": market_open(),
           "polls": _state["polls"], "errors": dict(_state["errors"]),
           "poll_sec": POLL_SEC, "stocks": []}
    for code, name in WATCH:
        t = load(code)
        gp = gaps(code)
        out["stocks"].append({
            "code": code, "name": name, "ticks": len(t),
            "first": t[0]["t"] if t else None, "last": t[-1]["t"] if t else None,
            "gaps": gp, "gap_sec": sum(g["seconds"] for g in gp),
            "file": str(_path(code)),
        })
    return out


# ── bars, aggregated exactly as the artificial side does ────────────────────────────
def bars_ticks(ticks: list[dict], n: int) -> list[dict]:
    """One bar per n EXECUTIONS. Only complete groups become bars, so a bar never changes
    once drawn — the same rule the artificial tick charts follow."""
    out: list[dict] = []
    prev_close = None
    for b in range(len(ticks) // n):
        grp = ticks[b * n:(b + 1) * n]
        pxs = [x["px"] for x in grp]
        close = pxs[-1]
        op = prev_close if prev_close is not None else pxs[0]
        out.append({"time": b, "hhmm": grp[-1]["t"], "open": op,
                    "high": max(max(pxs), op), "low": min(min(pxs), op), "close": close,
                    "dir": 1 if close > op else (-1 if close < op else 0),
                    "vol": sum(x["qty"] for x in grp), "n": n})
        prev_close = close
    return out


def bars_time(ticks: list[dict], seconds: int) -> list[dict]:
    """One bar per `seconds` of clock. Bars are CONTINUOUS — each opens at the previous
    close — so "close > open" and "close > previous close" are the same statement, which
    is what makes a red bar mean "the engine counted a rise" at every timeframe."""
    if not ticks:
        return []
    def sec_of(x):
        t = x["ts"]
        return int(t[8:10]) * 3600 + int(t[10:12]) * 60 + int(t[12:14])
    out: list[dict] = []
    prev_close = None
    cur: list[dict] = []
    cur_key = None
    for x in ticks:
        k = sec_of(x) // seconds
        if cur_key is None:
            cur_key = k
        if k != cur_key and cur:
            pxs = [y["px"] for y in cur]
            close = pxs[-1]
            op = prev_close if prev_close is not None else pxs[0]
            out.append({"time": cur_key, "hhmm": cur[-1]["t"], "open": op,
                        "high": max(max(pxs), op), "low": min(min(pxs), op), "close": close,
                        "dir": 1 if close > op else (-1 if close < op else 0),
                        "vol": sum(y["qty"] for y in cur), "n": len(cur)})
            prev_close = close
            cur, cur_key = [], k
        cur.append(x)
    return out                      # the bar still forming is deliberately not emitted


# ── the Data File: the minute-by-minute record of what REALLY traded ────────────────
def data_file(code: str, mins: int = 12, frm: str = "", to: str = "",
              hhmm: str = "") -> dict[str, Any]:
    """🕰️ The Data File for one real stock — the same reconciliation surface the
    artificial Strategy Lab has, built from actual Kiwoom executions.

    The boss reconciles a trade against this: a fill at 10:32 has to be findable in
    10:32, at a price that really printed (2026-08-04, asking for the Kiwoom side to
    have what the artificial side has).

    Minutes are built with the SAME rule as `bars_time(ticks, 60)` — each opens at the
    previous close — so the Data File and the 1분 chart cannot disagree about a minute.
    The minute STILL RUNNING is included and flagged `forming`, because a trade that just
    executed must be reconcilable immediately; on the artificial side its absence looked
    exactly like a wrong price.

    hhmm="10:32" returns EVERY execution in that minute grouped by second, not one price
    per second. That distinction is what makes the reconciliation work at all: a tick bar
    closes on a DEAL, and one second holds several.
    """
    ticks = load(code)
    name = next((n for c, n in WATCH if c == code), code)
    if not ticks:
        return {"ok": True, "code": code, "name": name, "rows": [], "total_minutes": 0,
                "empty": "no executions collected yet"}

    def minute_of(x):
        return x["ts"][8:12]                       # HHMM

    # group, preserving order
    order: list[str] = []
    by: dict[str, list[dict]] = {}
    for x in ticks:
        k = minute_of(x)
        if k not in by:
            by[k] = []
            order.append(k)
        by[k].append(x)

    last_key = order[-1]
    rows, prev_close = [], None
    for k in order:
        grp = by[k]
        pxs = [y["px"] for y in grp]
        close = pxs[-1]
        op = prev_close if prev_close is not None else pxs[0]
        rows.append({
            "hhmm": f"{k[:2]}:{k[2:]}", "key": k,
            "date": f"{grp[0]['ts'][4:6]}-{grp[0]['ts'][6:8]}",
            "open": op, "high": max(max(pxs), op), "low": min(min(pxs), op), "close": close,
            "diff": round(close - op, 2),
            "dir": 1 if close > op else (-1 if close < op else 0),
            "deal_count": len(grp), "vol": sum(y["qty"] for y in grp),
            # the running minute is not finished; say so rather than let it read as closed
            "forming": k == last_key,
        })
        prev_close = close

    # ---- drilling into ONE minute: every deal in it, grouped by second --------------
    if hhmm:
        want = hhmm.replace(":", "")[:4]
        row = next((r for r in rows if r["key"] == want), None)
        if row is None:
            return {"ok": False, "code": code, "name": name,
                    "error": f"{hhmm} is not in the collected tape"}
        secs: list[dict] = []
        for x in by[want]:
            t = x["t"]
            if not secs or secs[-1]["t"] != t:
                secs.append({"t": t, "deals": []})
            secs[-1]["deals"].append({"px": x["px"], "qty": x["qty"]})
        return {"ok": True, "code": code, "name": name, **row,
                "seconds": secs, "traded": sorted({x["px"] for x in by[want]})}

    # ---- the list, newest last, windowed the same way the lab's is -----------------
    sel = rows
    if frm or to:
        f = (frm or "00:00").replace(":", "")[:4]
        t2 = (to or "23:59").replace(":", "")[:4]
        sel = [r for r in rows if f <= r["key"] <= t2]
    elif mins:
        sel = rows[-mins:]
    return {"ok": True, "code": code, "name": name, "rows": sel,
            "total_minutes": len(rows),
            "first": rows[0]["hhmm"], "last": rows[-1]["hhmm"]}
