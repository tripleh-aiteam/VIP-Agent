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
    ("000660", "SK하이닉스"),
    ("042660", "한화오션"),
]

POLL_SEC = 3.0          # comfortably inside the ~40s the API remembers
OVERLAP = 250           # how many recent ticks to match on when finding the new part

_lock = threading.Lock()
_mem: dict[str, list[dict]] = {}        # code -> today's ticks, chronological
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
    return [x for x in out if x["px"] > 0]


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
        if day in (None, _day()) and code in _mem:
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
    if day in (None, _day()):
        with _lock:
            _mem[code] = list(out)
    return out


def poll_once(code: str) -> int:
    """Fetch, work out what is new, append it. Returns how many ticks were added."""
    page = _fetch(code)
    if not page:
        return 0
    with _lock:
        have = _mem.get(code)
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
        _state["last"][code] = new[-1]["t"]
    return len(new)


def _loop():
    _state["running"] = True
    while True:
        try:
            if market_open():
                for code, _name in WATCH:
                    try:
                        n = poll_once(code)
                        _state["polls"] += 1
                        if n:
                            _state["errors"].pop(code, None)
                    except Exception as e:                       # one bad stock must not
                        _state["errors"][code] = str(e)[:200]    # stop the others
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
