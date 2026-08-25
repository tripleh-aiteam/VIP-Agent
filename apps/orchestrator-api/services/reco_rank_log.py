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
TOP_N = 3
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
                    "avg": r.get("avg"), "top": rows[:TOP_N],
                    "in_top": i <= TOP_N}
    return {"t": best["t"], "rank": None, "of": len(rows),
            "avg": None, "top": rows[:TOP_N], "in_top": False}


def windows_for(code: str, day: str | None = None) -> list | None:
    """[(from,to)] spans when the code stood in the top-N. None = no log yet
    that day (the engine grants historical grace before the first snapshot)."""
    snaps = snapshots(day)
    if not snaps:
        return None
    out: list = []
    open_from = None
    for s in snaps:
        tops = {r.get("code") for r in (s.get("rows") or [])[:TOP_N]}
        if code in tops and open_from is None:
            open_from = s.get("t")
        elif code not in tops and open_from is not None:
            out.append((open_from, s.get("t")))
            open_from = None
    if open_from is not None:
        out.append((open_from, "15:30:00"))
    return out


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
    tops_new = [x["code"] for x in slim[:TOP_N]]
    tops_old = [x.get("code") for x in (last.get("rows") or [])[:TOP_N]] \
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
                    _cycle()
                    time.sleep(20)
                else:
                    time.sleep(120)
            except Exception:
                time.sleep(30)

    threading.Thread(target=_loop, name="reco-rank-log", daemon=True).start()
