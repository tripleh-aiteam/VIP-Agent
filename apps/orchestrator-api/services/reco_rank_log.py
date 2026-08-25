"""RECO RANK TIMELINE (boss 2026-08-25 12:3x: "check every 3-5 seconds and
buy using the checklist - and clicking a buy time must explain WHY THIS
STOCK, WHY THIS TIME").

A daemon thread records the checklist ranking's living top-N through the
session: every ~20 seconds it asks our own /paper-desk/daily-pick (which
carries the live-adjusted scores; its internal TTLs make this cheap and add
ZERO new Kiwoom load), and appends a snapshot whenever the top set or the
scores move. The reco desk's engine replays entries against this timeline -
a stock may only ENTER while it stood in the top-N at that moment - and the
board's explanation panel reads the same file to answer "why this stock,
why this time" with the recorded rank and score.

Tonight's planned upgrade tightens the loop to 3-5s for the collected hot
pool; this logger's file format stays the same either way.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
DIR = Path(__file__).resolve().parent.parent / "data" / "reco_rank"
TOP_N = 3       # fallback; the live value follows the board's N picker


def top_n() -> int:
    """The boss's chosen N (the top-3/top-5 picker) - the gate, the panel and
    the snapshots all follow it (2026-08-25: 'I cannot see changes in the
    top 5' - the machinery was pinned to 3 while his board showed 5)."""
    try:
        from services.daily_pick import reco_n
        return max(1, min(int(reco_n()), 10))
    except Exception:
        return TOP_N
_state = {"on": False}


def _day() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


def log_path(day: str | None = None) -> Path:
    return DIR / f"{day or _day()}.jsonl"


def snapshots(day: str | None = None) -> list[dict]:
    try:
        return [json.loads(x) for x in
                log_path(day).read_text(encoding="utf-8").splitlines()]
    except Exception:
        return []


def rank_at(code: str, hhmmss: str, day: str | None = None) -> dict | None:
    """The latest snapshot at/before hhmmss - the record a buy is judged by."""
    best = None
    for s in snapshots(day):
        if (s.get("t") or "") <= hhmmss:
            best = s
        else:
            break
    if not best:
        return None
    rows = best.get("rows") or []
    for i, r in enumerate(rows, 1):
        if r.get("code") == code:
            return {"t": best["t"], "rank": i, "of": len(rows),
                    "avg": r.get("avg"), "top": rows[:top_n()],
                    "in_top": i <= top_n()}
    return {"t": best["t"], "rank": None, "of": len(rows),
            "avg": None, "top": rows[:top_n()], "in_top": False}


def windows_for(code: str, day: str | None = None) -> list | None:
    """[(from,to)] spans when the code stood in the top-N. None = no log yet
    that day (the engine grants historical grace before the first snapshot)."""
    snaps = snapshots(day)
    if not snaps:
        return None
    out: list = []
    open_from = None
    for s in snaps:
        tops = {r.get("code") for r in (s.get("rows") or [])[:top_n()]}
        if code in tops and open_from is None:
            open_from = s.get("t")
        elif code not in tops and open_from is not None:
            out.append((open_from, s.get("t")))
            open_from = None
    if open_from is not None:
        out.append((open_from, "15:30:00"))
    return out


_news9 = {"mtime": 0.0, "rows": []}


def _news_counts(code: str, minutes: int = 60) -> tuple[int, int]:
    """(위험, 호재) stamps for one stock in the last N minutes, from the
    intern's live log (mtime-cached parse, so the 4s loop stays free)."""
    f = (Path(__file__).resolve().parent.parent / "data" / "news_intern"
         / f"{_day()}.jsonl")
    try:
        mt = f.stat().st_mtime
        if mt != _news9["mtime"]:
            rows = []
            for ln in f.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    continue
            _news9["mtime"] = mt
            _news9["rows"] = rows
        cut = (datetime.now(KST) - timedelta(minutes=minutes)
               ).isoformat(timespec="seconds")
        risk = sum(1 for r in _news9["rows"] if r.get("code") == code
                   and r.get("stamp") == "위험" and (r.get("ts") or "") >= cut)
        good = sum(1 for r in _news9["rows"] if r.get("code") == code
                   and r.get("stamp") == "호재" and (r.get("ts") or "") >= cut)
        return risk, good
    except Exception:
        return 0, 0


# THE TRUE PULSE (boss 2026-08-25 13:4x: the panel showed the last RECORD
# time - written only on rank changes/60s - so the 4s checking looked slow):
# every single check stamps here, even when nothing changed.
_live = {"t": "", "checks": 0, "top": []}


def live_pulse() -> dict:
    return dict(_live)


_base = {"t": 0.0, "rows": [], "vol20": {}}   # morning scores + 20d volume baselines


def _refresh_base() -> None:
    r = json.load(urllib.request.urlopen(
        "http://127.0.0.1:8000/paper-desk/daily-pick", timeout=60))
    _base["rows"] = r.get("rows") or []
    _base["t"] = time.time()
    # 20-day average volume per stock (for the live volume-surge term) -
    # one cheap DB read per 5-minute refresh, never in the 4s loop
    try:
        from services.daily_pick import _conn
        conn = _conn()
        cur = conn.cursor()
        cur.execute("""SELECT ticker, AVG(volume) FROM raw_daily_prices
                       WHERE date >= CURRENT_DATE - 35 GROUP BY ticker""")
        _base["vol20"] = {t: float(a or 0) for t, a in cur.fetchall()}
        conn.close()
    except Exception:
        pass


def _fast_cycle() -> None:
    """THE 3-5 SECOND RE-CHECK (boss 2026-08-25: "make it 3-5 sec recheck",
    the 20-universe): every watched stock's score = its morning checklist
    base + a live adjustment computed ENTIRELY from our own recorded tape
    (last tick price vs prev close, year zone) - zero API calls, so this
    loop can run every few seconds forever. The adjustment mirrors the
    chatbot's live layer (price up to ±4, zone +2/−3, cap ±9; the order-book
    term rides the slower base refresh)."""
    import services.kiwoom_tape as kt
    import services.kiwoom_rules as kr
    if time.time() - _base["t"] > 300:
        try:
            _refresh_base()
        except Exception:
            pass
    watch = {c: n for c, n in kt.WATCH}
    rows = []
    for b in _base["rows"]:
        c = b.get("code")
        if c not in watch:
            continue
        base = ((b.get("cats") or {}).get("avg")
                or b.get("live_total") or b.get("score") or 0)
        px = kt.last_price(c)
        adj = 0.0
        if px:
            try:
                prev = kr._daily20(c, kt._day())[0]
                if prev:
                    chg = (px / prev - 1) * 100
                    adj += max(-4.0, min(4.0, chg * 1.33))
            except Exception:
                pass
            try:
                dp = kr._daily_pos(c, px)
                if dp is not None:
                    adj += 2.0 if dp <= 0.20 else (-3.0 if dp >= 0.85 else 0.0)
            except Exception:
                pass
        # NEWS IN THE SELECTION (boss 2026-08-25 13:5x: "news also must be
        # implemented BEFORE selection to buy"): the intern's last-hour stamps
        # move the rank itself - each 위험 -2 (cap -4), each 호재 +1 (cap +2).
        # A stock drowning in danger headlines can no longer hold a top seat
        # on price alone; the engine's half-size law still applies on top.
        try:
            risk9, good9 = _news_counts(c)
            adj += min(2, good9) * 1.0 - min(2, risk9) * 2.0
        except Exception:
            pass
        # THE VOLUME MUSCLE (boss 2026-08-25: "volume changes but it will not
        # affect the top 5 - solve this"): today's pace vs the stock's own
        # 20-day average, time-of-day adjusted. Measured 2026-08-25: turnover
        # is the single strongest next-day predictor (IC +0.13/+0.09 across
        # two independent 250-day periods) - so a real surge now carries up
        # to +4, enough to flip a 2-4 point seat gap. No baseline = term 0.
        try:
            av20 = (_base.get("vol20") or {}).get(c) or 0
            tv = kt.today_volume(c)
            if av20 and tv:
                nk = datetime.now(KST)
                frac = max(0.05, min(1.0, ((nk.hour - 9) * 60 + nk.minute) / 390))
                ratio = tv / (av20 * frac)
                adj += (4.0 if ratio >= 3 else 3.0 if ratio >= 2
                        else 1.5 if ratio >= 1.5 else (-1.0 if ratio < 0.5 else 0.0))
        except Exception:
            pass
        adj = max(-12.0, min(12.0, adj))
        rows.append({"code": c, "name": b.get("name") or watch.get(c) or c,
                     "avg": round(float(base) + adj, 1)})
    if not rows:
        return
    rows.sort(key=lambda x: -(x["avg"] or 0))
    _live["t"] = datetime.now(KST).strftime("%H:%M:%S")
    _live["checks"] += 1
    _live["top"] = rows[:10]
    _write_snapshot(rows[:10])


def _write_snapshot(slim: list) -> None:
    now = datetime.now(KST).strftime("%H:%M:%S")
    p = log_path()
    last = None
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        if lines:
            last = json.loads(lines[-1])
    except Exception:
        pass
    tops_new = [x["code"] for x in slim[:top_n()]]
    tops_old = [x.get("code") for x in (last.get("rows") or [])[:top_n()]] \
        if last else None
    aged = (not last) or (last.get("t", "") < (
        datetime.now(KST) - timedelta(seconds=60)).strftime("%H:%M:%S"))
    if tops_new != tops_old or aged:
        DIR.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": now, "rows": slim},
                               ensure_ascii=False) + "\n")


def _cycle() -> None:
    r = json.load(urllib.request.urlopen(
        "http://127.0.0.1:8000/paper-desk/daily-pick", timeout=60))
    rows = r.get("rows") or []
    rows = sorted(rows, key=lambda x: -(((x.get("cats") or {}).get("avg"))
                                        or x.get("live_total")
                                        or x.get("score") or 0))
    slim = [{"code": x.get("code"), "name": x.get("name"),
             "avg": ((x.get("cats") or {}).get("avg")
                     or x.get("live_total") or x.get("score"))}
            for x in rows[:10]]
    now = datetime.now(KST).strftime("%H:%M:%S")
    p = log_path()
    last = None
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        if lines:
            last = json.loads(lines[-1])
    except Exception:
        pass
    tops_new = [x["code"] for x in slim[:top_n()]]
    tops_old = [x.get("code") for x in (last.get("rows") or [])[:top_n()]] \
        if last else None
    aged = (not last) or (last.get("t", "") < (
        datetime.now(KST) - timedelta(seconds=60)).strftime("%H:%M:%S"))
    if tops_new != tops_old or aged:
        DIR.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": now, "rows": slim},
                               ensure_ascii=False) + "\n")


def start_logger() -> None:
    """Idempotent; the thread sleeps outside market hours."""
    if _state["on"]:
        return
    _state["on"] = True

    def _loop():
        time.sleep(20)          # let the server finish booting first
        while True:
            try:
                from services.kiwoom_tape import market_open
                if market_open():
                    _fast_cycle()          # the 3-5s check (own tape, no API)
                    time.sleep(4)
                else:
                    time.sleep(120)
            except Exception:
                time.sleep(30)

    threading.Thread(target=_loop, name="reco-rank-log", daemon=True).start()
