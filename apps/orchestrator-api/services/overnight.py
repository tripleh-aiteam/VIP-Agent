"""What America did while Korea slept (boss 2026-08-11).

Fetched free from Yahoo Finance's public chart API (no key, daily closes): S&P 500, NASDAAQ composite, the
SOXX semiconductor ETF (the tradable proxy for the SOX index that moves SK하이닉스 and
삼성전자), and USD/KRW. Cached to disk per calendar day - one fetch each morning serves
the board, the gate panel and the morning report.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger("overnight")
_FILE = Path(__file__).resolve().parent.parent / "data" / "overnight.json"

SYMBOLS = [
    ("^GSPC", "S&P 500", "미국 대형주"),
    ("^IXIC", "NASDAQ", "미국 기술주"),
    ("^SOX", "SOX(반도체)", "반도체 - SK하이닉스·삼성전자 예고편"),
    ("KRW=X", "원/달러", "환율"),
    # THE CHIP NAMES THEMSELVES, NOT ONLY THE INDEX (boss 2026-09-04: "please
    # add other semiconductor related prices also, like NVIDIA, Micron
    # Technology, and there is a Japanese one also"). SOX is an average; these
    # are the three that actually move 하이닉스/삼성 sentiment - the AI-demand
    # bellwether, the memory maker that competes with them directly, and Japan's
    # chip-equipment giant, whose orders lead their capex.
    ("NVDA", "엔비디아", "AI 수요 - 하이닉스 HBM 예고편"),
    ("MU", "마이크론", "메모리 직접 경쟁사 - 삼성·하이닉스와 같은 사이클"),
    ("8035.T", "도쿄일렉트론", "일본 반도체 장비 - 오늘 아시아 장에서 같이 움직임"),
]


def _last_two_closes(sym: str):
    import urllib.parse
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(sym)}?range=10d&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    res = d["chart"]["result"][0]
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    pairs = [(t, c) for t, c in zip(ts, closes) if c is not None]
    if len(pairs) < 2:
        return None
    (t_prev, prev), (t_last, last) = pairs[-2], pairs[-1]
    return {"date": _dt.date.fromtimestamp(t_last).isoformat(),
            "close": float(last), "prev": float(prev)}


def fetch(force: bool = False) -> dict:
    today = _dt.date.today().isoformat()
    if not force and _FILE.exists():
        try:
            d = json.loads(_FILE.read_text(encoding="utf-8"))
            if d.get("day") == today:
                return d
        except Exception:
            pass
    out = []
    for sym, name, role in SYMBOLS:
        try:
            r = _last_two_closes(sym)
            if not r:
                continue
            chg = (r["close"] / r["prev"] - 1) * 100 if r["prev"] else 0.0
            out.append({"sym": sym, "name": name, "role": role,
                        "date": r["date"], "close": r["close"],
                        "chg_pct": round(chg, 2)})
        except Exception as e:
            logger.warning("overnight %s: %s", sym, str(e)[:80])
    soxx = next((x for x in out if x["sym"] == "^SOX"), None)
    ndq = next((x for x in out if x["sym"] == "^IXIC"), None)
    mood_ko = mood_en = ""
    ref = soxx or ndq
    if ref:
        c = ref["chg_pct"]
        if c <= -1.5:
            mood_ko = "반도체 급락 밤 — 갭하락 출발 가능성. 급락-반등(N3)의 사냥터가 될 수 있는 날."
            mood_en = "semis fell hard overnight - gap-down open likely; the kind of day N3 hunts."
        elif c >= 1.5:
            mood_ko = "반도체 급등 밤 — 갭상승 출발 가능성. 시초 추격 금지 구간 주의."
            mood_en = "semis surged overnight - gap-up open likely; beware chasing the open."
        else:
            mood_ko = "밤사이 미국 조용함 — 평소 흐름 예상."
            mood_en = "quiet US night - expect an ordinary open."
    d = {"ok": bool(out), "day": today,
         "fetched_at": _dt.datetime.now().strftime("%H:%M:%S"),
         "rows": out, "mood_ko": mood_ko, "mood_en": mood_en}
    try:
        _FILE.parent.mkdir(exist_ok=True)
        _FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return d
