"""뉴스 영향 분석 — the menu the boss checks before this ever touches a trade.

Boss 2026-09-07: "we have to analyze news also - any keyword based news can not
effect, we should analyze which news can effect or not effect, so please first
build this thing, then I will check, then you will implement it to our case."

Read-only. It reads the stored news files and the stored tape, and it never
looks at, or writes, any trading state.
"""
import logging
import threading
import time

from fastapi import APIRouter, Query

log = logging.getLogger(__name__)
router = APIRouter(prefix="/news-impact", tags=["news-impact"])

# one day's analysis is seconds of measurement plus a minute of reading; the
# page must never wait for it twice
_CACHE: dict = {}
_BUSY: set = set()


def _key(day, judge, model, top):
    return (str(day), bool(judge), str(model), int(top))


@router.get("/days")
def days():
    """Which days we hold news for at all."""
    from services.news_impact import days_available
    ds = days_available()
    return {"ok": True, "days": ds, "first": ds[0] if ds else None,
            "last": ds[-1] if ds else None}


@router.get("")
def impact(day: str = Query(""), judge: int = Query(0),
           model: str = Query("claude-haiku-4-5"), top: int = Query(40),
           moved_only: int = Query(0), code: str = Query(""),
           limit: int = Query(150)):
    """Every story of one day: what it said, and what the price did about it.

    `judge=1` also has the model read the top stories with the rubric, which
    takes about a second each - so the answer is cached and, on a cold cache,
    the measurement comes back immediately with `reading: computing` while the
    reading is prepared behind it."""
    from services.news_impact import analyze, days_available
    ds = days_available()
    day = day or (ds[-1] if ds else "")
    if not day:
        return {"ok": False, "error": "no news files stored yet"}
    codes = [c.strip() for c in code.split(",") if c.strip()] or None
    k = _key(day, judge, model, top)
    hit = _CACHE.get(k)
    if hit is None and judge:
        # serve the measurement now, read behind it
        plain = _CACHE.get(_key(day, False, model, top))
        if plain is None:
            plain = analyze(day, codes=codes)
            _CACHE[_key(day, False, model, top)] = plain
        if k not in _BUSY:
            _BUSY.add(k)

            def _run():
                try:
                    _CACHE[k] = analyze(day, codes=codes, with_judge=True,
                                        model=model, judge_top=top)
                except Exception as e:
                    log.warning(f"news impact {day}: {str(e)[:100]}")
                finally:
                    _BUSY.discard(k)
            threading.Thread(target=_run, daemon=True).start()
        out = dict(plain)
        out["reading"] = "computing"
        hit = out
    elif hit is None:
        hit = analyze(day, codes=codes)
        _CACHE[k] = hit
    rows = hit.get("rows") or []
    if moved_only:
        rows = [r for r in rows if r.get("moved")]
    # the rows arrive sorted by the size of the reaction, so a cap keeps the
    # page light without hiding anything that reacted (500+ stories a day is
    # half a megabyte of mostly-quiet headlines)
    total = len(rows)
    if limit and total > limit:
        rows = rows[:limit]
    return {**hit, "rows": rows, "shown": len(rows), "matched": total,
            "days": ds, "day": day, "generated": time.strftime("%H:%M:%S")}
