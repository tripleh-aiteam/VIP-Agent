"""THE NEWS INTERN (boss 2026-08-21: the Layer idea's third judge, hired in
observe-mode). Every minute during market days it reads fresh headlines for
the six stocks - Google News RSS (broad press) plus DART's disclosure feed
when reachable - and has the local Qwen3:32b on the RTX 5090 stamp each new
item 위험/중립/호재 with a one-line reason, at temperature 0.

LAW: this process touches NO money and NO trading state. It appends stamps to
data/news_intern/{day}.jsonl and nothing else reads that file yet. Only after
the log's record is graded against reality does the boss decide whether the
stamp earns a vote on entry size (a ctx dial, like the other judges).

Run standalone, detached:
  .venv/Scripts/python.exe -m services.news_intern          # loop forever
  .venv/Scripts/python.exe -m services.news_intern --once   # one cycle, verbose

Kiwoom REST news TR: not found in the documented catalog (prices/charts/
rankings/shsa only) - re-check their api guide before wiring; the generic
transport in kiwoom_rest._request() takes any api-id the day one appears.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

STOCKS = [
    ("000660", "SK하이닉스"),
    ("005930", "삼성전자"),
    ("035420", "NAVER"),
    ("017670", "SK텔레콤"),
    ("042660", "한화오션"),
    ("034020", "두산에너빌리티"),
]
OLLAMA = "http://localhost:11434/api/chat"
MODEL = "qwen3:32b"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "news_intern"
SEEN_PATH = OUT_DIR / "seen.json"
POLL_SEC = 60
# awake 08:30-16:00 KST Mon-Fri: pre-open news matters, evening news keeps
# until the next morning's first cycle
AWAKE = ("08:30", "16:00")

SYSTEM = ("너는 한국 주식 뉴스 분류기다. 헤드라인을 보고 해당 종목에 대해 "
          'JSON 한 줄로만 답하라: {"stamp":"위험|중립|호재","why":"한 문장"}. '
          "다른 말 금지.")


def _fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def google_news(name: str) -> list[dict]:
    q = urllib.parse.quote(f'"{name}" when:1d')
    url = (f"https://news.google.com/rss/search?q={q}"
           "&hl=ko&gl=KR&ceid=KR:ko")
    items = []
    try:
        root = ElementTree.fromstring(_fetch(url))
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = (it.findtext("pubDate") or "").strip()
            if title:
                items.append({"src": "google", "title": title, "link": link,
                              "pub": pub})
    except Exception as e:
        print(f"[warn] google rss {name}: {e}", flush=True)
    return items[:20]


def dart_feed() -> list[dict]:
    """DART's public disclosure RSS - the origin of hard news. Soft-fail:
    if the feed shape moved, log once per cycle and carry on with Google."""
    try:
        root = ElementTree.fromstring(
            _fetch("https://dart.fss.or.kr/api/todayRSS.xml"))
        out = []
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            if title:
                out.append({"src": "dart", "title": title, "link": link,
                            "pub": (it.findtext("pubDate") or "").strip()})
        return out
    except Exception as e:
        print(f"[warn] dart rss: {e}", flush=True)
        return []


def stamp(name: str, title: str) -> tuple[dict, float]:
    body = {"model": MODEL, "stream": False, "think": False,
            "options": {"temperature": 0, "num_predict": 120},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user",
                          "content": f"종목: {name}. 헤드라인: {title}"}]}
    t0 = time.time()
    req = urllib.request.Request(OLLAMA, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=180))
    txt = (r.get("message") or {}).get("content", "").strip()
    m = re.search(r"\{.*\}", txt, re.S)
    try:
        parsed = json.loads(m.group(0)) if m else {}
    except Exception:
        parsed = {}
    if parsed.get("stamp") not in ("위험", "중립", "호재"):
        parsed = {"stamp": "중립", "why": f"분류 실패: {txt[:80]}"}
    return parsed, time.time() - t0


def _load_seen() -> set:
    try:
        return set(json.loads(SEEN_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_seen(seen: set) -> None:
    SEEN_PATH.write_text(json.dumps(sorted(seen)[-5000:]), encoding="utf-8")


def cycle(seen: set, verbose: bool = False) -> int:
    day = dt.datetime.now().strftime("%Y%m%d")
    out = OUT_DIR / f"{day}.jsonl"
    fresh = 0
    dart = dart_feed()
    for code, name in STOCKS:
        pool = google_news(name)
        pool += [d for d in dart if name in d["title"]]
        for it in pool:
            key = hashlib.sha1(
                f"{code}|{it['title']}".encode()).hexdigest()[:16]
            if key in seen:
                continue
            seen.add(key)
            try:
                s, lat = stamp(name, it["title"])
            except Exception as e:
                print(f"[warn] ollama: {e}", flush=True)
                continue
            row = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
                   "code": code, "name": name, "src": it["src"],
                   "title": it["title"], "link": it.get("link", ""),
                   "stamp": s.get("stamp"), "why": s.get("why", ""),
                   "sec": round(lat, 1)}
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            _save_seen(seen)   # per stamp: a crash mid-cycle must not
                               # re-stamp (and re-log) what was already done
            fresh += 1
            if verbose:
                print(f"  {name} [{s.get('stamp')}] {it['title'][:60]} "
                      f"({lat:.1f}s)", flush=True)
    _save_seen(seen)
    return fresh


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    seen = _load_seen()
    if args.once:
        n = cycle(seen, verbose=True)
        print(f"one cycle: {n} fresh stamps -> {OUT_DIR}", flush=True)
        return
    print(f"news intern on duty: {len(seen)} known items, poll {POLL_SEC}s, "
          f"awake {AWAKE[0]}-{AWAKE[1]} Mon-Fri", flush=True)
    while True:
        now = dt.datetime.now()
        hm = now.strftime("%H:%M")
        if now.weekday() < 5 and AWAKE[0] <= hm <= AWAKE[1]:
            try:
                n = cycle(seen)
                if n:
                    print(f"{hm} +{n} stamps", flush=True)
            except Exception as e:
                print(f"[warn] cycle: {e}", flush=True)
            time.sleep(POLL_SEC)
        else:
            time.sleep(300)


if __name__ == "__main__":
    main()
