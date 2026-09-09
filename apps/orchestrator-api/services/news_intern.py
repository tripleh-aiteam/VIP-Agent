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

Kiwoom REST news TR: CONFIRMED ABSENT 2026-08-22 - the full REST catalog
(~186 endpoints, 17 modules: account/stkinfo/market/orders/charts/ranking/
sector/frgn/shsa/lending/themes/condition/ELW/ETF/websocket) carries no
news or 공시 endpoint at all. Google News RSS + DART stay the sources; the
generic transport in kiwoom_rest._request() takes any api-id the day
Kiwoom ships one.
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

# The body reader searches Naver with the boss's own keys. main.py loads
# .env for the orchestrator; this process is started on its own and would
# otherwise see no keys at all — and search_news() fails SILENTLY without
# them, leaving every article unread with nothing on screen to say so.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
except Exception:
    pass

STOCKS = [
    ("000660", "SK하이닉스"),
    ("005930", "삼성전자"),
    ("035420", "NAVER"),
    ("017670", "SK텔레콤"),
    ("042660", "한화오션"),
    ("034020", "두산에너빌리티"),
]


def _universe() -> list[tuple[str, str]]:
    """The whole 20-stock desk, not just the six (boss's deep audit
    2026-08-25: the news judge was BLIND on every checklist extra - the
    intern only read headlines for the six). Reads the live watch list from
    the server; falls back to the six if the server is down."""
    import urllib.request as _u
    try:
        st = json.loads(_u.urlopen(
            "http://127.0.0.1:8000/paper-desk/live/status", timeout=15).read())
        rows = [(s.get("code"), s.get("name")) for s in st.get("stocks") or []
                if s.get("code") and s.get("name")]
        if len(rows) >= 6:
            return rows
    except Exception:
        pass
    return STOCKS
OLLAMA = "http://localhost:11434/api/chat"
# ONE local model for the whole desk (2026-08-28, boss: "20 sec ... all LLM must
# answer faster"): the dense qwen3:32b judge and the chatbot's qwen3-vl 30b star
# could not share the 5090's 32GB — every 60s judging cycle evicted the chat
# model and every chat answer evicted the judge back, so BOTH paid a ~20s VRAM
# reload all market day. The star is also the better judge per our own bench
# (llm_client: 6.8s warm vs 28.9s for dense 32b, near-32B quality).
MODEL = "qwen3-vl:30b-a3b-instruct-q4_K_M"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "news_intern"
SEEN_PATH = OUT_DIR / "seen.json"
POLL_SEC = 60
# awake 08:30-16:00 KST Mon-Fri: pre-open news matters, evening news keeps
# until the next morning's first cycle
AWAKE = ("08:30", "16:00")

SYSTEM = ("너는 한국 주식 뉴스 분류기다. 헤드라인을 보고 해당 종목에 대해 "
          'JSON 한 줄로만 답하라: {"stamp":"위험|중립|호재|무관","why":"한 문장"}. '
          "헤드라인의 주체가 그 종목이 아니면 — 다른 회사 이야기이거나 그 종목이 "
          '헤드라인에 등장하지 않으면 — 반드시 "무관"으로 답하라. 억지로 연결짓지 '
          "말고, 헤드라인에 없는 사실을 지어내지 마라. 다른 말 금지.")


# ── WHOSE STORY IS IT? ────────────────────────────────────────────────
# boss 2026-09-08: "한화시스템을 판단하는데 '덩치 키우면 경쟁력?'…KAI 노조,
# 한화 인수 움직임에 제동 같은 뉴스를 왜 들어가니. 이게 맞아?"
#
# He is right, and it cost money. google_news() searches the article BODY, so
# any story that merely MENTIONS a stock came back as that stock's news. The
# stamper, whose only choices were 위험/중립/호재, then had to invent a link:
# it wrote "한화시스템의 KAI 인수" — false, the acquirer is 한화에어로스페이스 —
# and that invented 위험 vetoed 한화시스템 buys for three hours
# (approval_desk: "danger news still vetoes").
#
# MEASURED over 09-03 / 09-04 / 09-08 (3,610 stamps): 195 carried a headline
# whose subject was ANOTHER desk stock, 35 of them 위험. Worst hit: 한화시스템,
# 30 stamps.
#
# THE LAW — only a stock's OWN story may veto it:
#   own    the company is named in the HEADLINE      → the model's stamp stands
#   other  the headline names a DIFFERENT desk stock → not our story, DROPPED
#   group  only the group name is there (한화, 현대…) → context, forced 중립
#   sector no company in the headline at all         → context, forced 중립
# Nothing is lost: dropped items are written to dropped_{day}.jsonl (a name
# that deliberately does NOT match checklist_advice._fresh_stamps' "2*.jsonl"
# glob, so the live desk never reads them) and the model's literal word always
# survives in the row as stamp_raw.

# The tokens a real Korean headline uses for THAT company — the group name on
# its own (한화 / 현대 / 삼성 / SK) is NEVER an own-alias: that is precisely the
# token the KAI story rode in on.
ALIASES: dict[str, list[str]] = {
    "한화시스템": ["한화시스템", "한화 시스템", "에어로·시스템", "한화시스"],
    "한화에어로스페이스": ["한화에어로", "한화 에어로"],
    "한화오션": ["한화오션", "한화 오션"],
    "한국항공우주": ["한국항공우주", "KAI"],
    "LIG디펜스앤에어로스페이스": ["LIG", "넥스원"],
    "현대로템": ["현대로템"],
    "현대모비스": ["현대모비스", "모비스"],
    "현대차": ["현대차", "현대자동차"],
    "기아": ["기아"],
    "HD현대중공업": ["HD현대중", "현대중공업", "HD현대重", "현대重"],
    "HD한국조선해양": ["HD한국조선", "한국조선해양"],
    "삼성중공업": ["삼성중공업", "삼성重"],
    "삼성전자": ["삼성전자", "삼성電", "갤럭시"],
    "SK하이닉스": ["SK하이닉스", "하이닉스"],
    "SK텔레콤": ["SK텔레콤", "SKT"],
    "SK스퀘어": ["SK스퀘어"],
    "NAVER": ["NAVER", "네이버"],
    "카카오": ["카카오"],
    "POSCO홀딩스": ["POSCO", "포스코"],
    "한국전력": ["한국전력", "한전"],
    "한미반도체": ["한미반도체"],
    "두산에너빌리티": ["두산에너빌리티", "두산에너"],
}

# The parent whose name a headline may carry instead of the subsidiary's.
GROUP: dict[str, str] = {
    "한화시스템": "한화", "한화에어로스페이스": "한화", "한화오션": "한화",
    "현대로템": "현대", "현대모비스": "현대", "현대차": "현대",
    "HD현대중공업": "현대", "HD한국조선해양": "현대",
    "삼성전자": "삼성", "삼성중공업": "삼성",
    "SK하이닉스": "SK", "SK텔레콤": "SK", "SK스퀘어": "SK",
    "POSCO홀딩스": "포스코", "두산에너빌리티": "두산",
}

# SEO junk Google News RSS hands back — today it put "강원랜드 바카라 …
# Histoire pour tous" and "조이카지노" into 한화시스템's feed. Gambling spam
# and personal blogs are not news about anybody.
_SPAM = ("카지노", "바카라", "슬롯", "토토", "먹튀", "배팅사이트", "온라인 도박",
         "blog.naver.com", "Histoire pour tous", "tistory.com")


def _aliases(name: str) -> list[str]:
    return ALIASES.get(name) or [name]


def is_spam(title: str) -> bool:
    return any(k in title for k in _SPAM)


def relevance(name: str, title: str, universe: list[str]) -> str:
    """own / other / group / sector — see THE LAW above."""
    if any(a in title for a in _aliases(name)):
        return "own"
    for other in universe:
        if other != name and any(a in title for a in _aliases(other)):
            return "other"          # the headline belongs to that company
    g = GROUP.get(name) or ""
    return "group" if g and g in title else "sector"


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
            "keep_alive": "24h",       # same model as the chat star — never unload
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
    if parsed.get("stamp") not in ("위험", "중립", "호재", "무관"):
        parsed = {"stamp": "중립", "why": f"분류 실패: {txt[:80]}"}
    return parsed, time.time() - t0


# ── IS IT NEWS, OR IS IT JUST THE PRICE? ──────────────────────────────
# 2026-09-09. Sixteen of the day's 79 danger stamps were auto-written price
# recaps: "LG에너지솔루션 주가, 9월 8일 348,500원 3.86% 하락 마감", "현대모비스,
# 41만원선 하회하며 3%대 하락 마감 : 기업주식정보". That is not news, it is the
# price with a byline — and it runs the desk backwards: the price falls, a robot
# writes that it fell, and the news gate then refuses to buy the dip. On a book
# whose whole thesis is buying at the week's low, this gate fired hardest exactly
# where it was meant to stay quiet.
#
# Both halves must be present — a PRICE context word AND a MOVE word — so that a
# real story carrying only one of them survives: "K배터리 점유율 10.4% 하락" is
# market share, not a quote, and stays news.
_PX_CTX = re.compile(r"(주가|장중|마감|종가|시황|증시|코스피|코스닥|네이버증권|"
                     r"기업주식정보|특징주|주식정보)")
_PX_MOVE = re.compile(r"(상승|하락|약세|강세|급등|급락|출렁|하회|상회|반등|"
                      r"부진|폭락|폭등|오름세|내림세)")


def is_price_recap(title: str) -> bool:
    """A headline that only reports what the quote did. It may still be 호재 or
    중립 — it may never be the reason a buy is refused."""
    t = str(title or "")
    return bool(_PX_CTX.search(t) and _PX_MOVE.search(t))


# ── THE BODY, NOT ONLY THE TITLE ──────────────────────────────────────
# Boss 2026-09-09: "we have to find how our agent check news body not only
# title to find is it meaningful? is it effectful?"
#
# The headline decides WHOSE story it is — that law stays, it is what stopped a
# KAI headline vetoing 한화시스템. The body decides whether the story has any
# force. His own example: HD현대중공업 was blocked by a court dismissing an
# appeal over KDDX penalty points. True, and about them — but the 7.8조 contract
# was lost to 한화오션 in JULY. The body says so in its third paragraph; the
# headline does not. A legal postscript to a two-month-old loss is not a reason
# to refuse a buy this morning.
#
# Google News RSS links cannot be read — they are JS-only redirect stubs, and a
# plain fetch returns Google's own shell (measured 09-09: 586KB, not one article
# URL anywhere in it). So the body comes from Naver's news API searched on the
# headline text, which answers with the outlet's real URL that trafilatura reads.
_MEDIA_TAIL = re.compile(r"\s*[-–—]\s*[^\s-]{2,20}$")


def article_body(title: str, max_chars: int = 1800) -> str:
    """The real article text behind a Google headline, or '' if unreachable."""
    try:
        from services.naver_news import search_news
        from services.news_fetch import fetch_fulltext
    except Exception:
        return ""
    head = _MEDIA_TAIL.sub("", str(title or "")).strip()   # drop " - 매체명"
    if len(head) < 8:
        return ""
    # A HANGING NEWSPAPER MAY NOT STOP THE DESK. fetch_fulltext waits up to 20s
    # per URL and the cycle polls every 60s, so three dead links on one article
    # would stall every other stock's news behind it. Budget the whole read.
    deadline = time.time() + 25
    try:
        for a in search_news(head, display=3):
            if time.time() > deadline:
                break
            body = fetch_fulltext(a.get("link") or "", max_chars=max_chars)
            if body:
                return body
    except Exception:
        pass
    return ""


# The rubric is services.news_impact._RUBRIC's question — "can this move the
# price" — asked of the BODY instead of the headline. Kept here rather than
# imported so the intern stays a standalone process with no orchestrator deps.
IMPACT = "\n".join([
    "너는 한국 주식 트레이딩 데스크의 뉴스 애널리스트다. 아래 기사 본문을 읽고,",
    "이 기사가 오늘 이 종목의 가격을 움직일 힘이 있는지 판단하라.",
    "좋은 소식인지 나쁜 소식인지가 아니라, 움직일 힘이 있는지가 질문이다.",
    "",
    "① 새로운 사실인가 — 가장 중요한 질문이다.",
    "   본문에서 '실제로 무슨 일이 언제 일어났는지'를 먼저 찾아라.",
    "   시장이 이미 아는 결과를 확인·반복하는 후속 보도는 새로운 사실이 아니다:",
    "   항소·항고 기각, 판결 확정, 이미 발표된 계약·수주·탈락의 후일담,",
    "   몇 주·몇 달 전 사건의 법적 절차, 다른 매체의 재보도.",
    "   → 이런 기사는 new=false, move=없음 이다. 기사가 오늘 나왔다는 사실은",
    "     '새로운 사실'이 아니다 — 사건이 언제 일어났는지가 기준이다.",
    "② 이 회사 규모에 비해 큰가? 매출 대비 작은 건은 헤드라인이 세도 힘이 없다.",
    "③ 언제 작용하는가? 오늘 생산·주문·실적에 닿는 일인가, 몇 달 뒤의 일인가?",
    "④ 사실인가 의견인가? 시황·칼럼·전망·주가 등락 보도 자체는 힘이 없다.",
    "",
    "확신이 없으면 '없음'이라고 답하라. '강함'은 오늘 처음 알려진 공시급 사실에만.",
    'JSON 한 줄로만: {"move":"강함|보통|없음","kind":"수주|실적|증자|소송|규제|제품|'
    '사고|노사|인사|시황|의견|기타","new":true|false,"when":"사건이 일어난 시점",'
    '"why":"한 문장"}',
])


# ── ONE EVENT, ONE VERDICT ────────────────────────────────────────────
# The KDDX ruling reached the desk six times on 09-09 under six headlines, and
# the readings disagreed: "기술 우위도 뒤집혔다" was judged a fresh danger while
# "[단독] …항고도 재차 기각" — the same court, the same day — was correctly read
# as the follow-up to a loss the market has known since July. Whichever landed
# in the freshest-two window decided whether the stock could be bought.
#
# So a story is weighed ONCE per stock per day, and every retelling inherits
# that verdict — unless the retelling brings MORE article text, in which case
# it is re-read and the better-informed verdict replaces the earlier one. The
# fullest account of an event wins, which is also what a person would do.
_STORY: dict[str, list] = {}          # code -> [(tokens, verdict), ...]
_STORY_DAY = ""


def _event_tokens(title: str) -> set:
    """CHARACTER BIGRAMS, not words. Korean glues its particles onto the noun —
    '보안감점에' and '보안감점' are different words to a whitespace splitter, so
    word overlap rated two reports of one court ruling as unrelated (measured
    09-09 on the six KDDX retellings). Bigrams see straight through the
    particles and need no stemmer."""
    t = re.sub(r"[\[\(].*?[\]\)]", " ", str(title or ""))     # [단독], [특징주]
    t = _MEDIA_TAIL.sub("", t)                                 # " - 매체명"
    t = re.sub(r"[^\w가-힣]+", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _same_event(a: set, b: set) -> bool:
    """Two headlines about the same happening.

    The cut is MEASURED, not guessed: every same-stock headline pair in
    09-09's 1,196 rows was scored, and the band from 0.30 up is genuine
    retelling — 한화오션's Thai frigate award across three outlets (0.45),
    KB증권's note (0.45), 현대차그룹's early supplier payment (0.44), the KDDX
    ruling (0.32-1.00). HD현대중공업's KDDX story against its own strike story,
    two different events at one company on one day, scores 0.12-0.27. So 0.30
    folds the retellings and still keeps the events apart."""
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= 0.30


def weigh(name: str, title: str, code: str = "") -> dict:
    """Read the article and answer whether it can move the price today."""
    global _STORY_DAY
    day = dt.datetime.now().strftime("%Y%m%d")
    if day != _STORY_DAY:
        _STORY.clear()
        _STORY_DAY = day
    toks = _event_tokens(title)
    # the BEST-INFORMED reading of this event so far, not merely the first —
    # outlets arrive in no useful order, and the one that happened to land
    # first is often the thinnest account
    prior = None
    for tk, v in _STORY.get(code or name, []):
        if _same_event(toks, tk) and (prior is None
                                      or int(v.get("chars") or 0) > int(prior.get("chars") or 0)):
            prior = v

    body = article_body(title)
    basis = "body" if body else "headline"
    # a retelling that adds nothing new to read inherits the standing verdict,
    # and costs no model call at all
    if prior is not None and len(body) <= int(prior.get("chars") or 0):
        return {**prior, "repeat": True}
    msg = "종목: " + name + "\n헤드라인: " + title + "\n"
    msg += ("기사 본문:\n" + body) if body else "(본문을 가져오지 못함 — 헤드라인만으로 판단)"
    req = {"model": MODEL, "stream": False, "think": False, "keep_alive": "24h",
           "options": {"temperature": 0, "num_predict": 160},
           "messages": [{"role": "system", "content": IMPACT},
                        {"role": "user", "content": msg}]}
    out = {"move": "?", "basis": basis, "chars": len(body)}
    try:
        r = urllib.request.Request(OLLAMA, json.dumps(req).encode(),
                                   {"Content-Type": "application/json"})
        txt = (json.load(urllib.request.urlopen(r, timeout=180))
               .get("message") or {}).get("content", "")
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            d = json.loads(m.group(0))
            if d.get("move") in ("강함", "보통", "없음"):
                out.update({k: d.get(k)
                            for k in ("move", "kind", "new", "when", "why")})
    except Exception as e:
        out["why"] = "본문 판단 실패: " + str(e)[:60]
    # remember it, so the next outlet running this story says the same thing
    if out.get("move") != "?":
        _STORY.setdefault(code or name, []).append((toks, out))
    return out


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
    # NOT "2*.jsonl" — _fresh_stamps globs that and takes the LAST file, so a
    # sibling named 20260908_dropped.jsonl would silently become the desk's
    # live news feed. The audit trail must sort nowhere near the real log.
    junk = OUT_DIR / f"dropped_{day}.jsonl"
    fresh = 0
    dart = dart_feed()
    universe = _universe()
    names = [n for _, n in universe]
    for code, name in universe:
        pool = google_news(name)
        pool += [d for d in dart if name in d["title"]]
        for it in pool:
            key = hashlib.sha1(
                f"{code}|{it['title']}".encode()).hexdigest()[:16]
            if key in seen:
                continue
            seen.add(key)
            # WHOSE STORY IS IT — decided before we spend a second of the
            # 5090 on it. Someone else's headline never reaches the model, so
            # it can no longer be asked to invent a link it does not have.
            rel = "spam" if is_spam(it["title"]) else relevance(name, it["title"], names)
            if rel in ("spam", "other"):
                with junk.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(
                        {"ts": dt.datetime.now().isoformat(timespec="seconds"),
                         "code": code, "name": name, "src": it["src"],
                         "title": it["title"], "link": it.get("link", ""),
                         "rel": rel}, ensure_ascii=False) + "\n")
                _save_seen(seen)
                if verbose:
                    print(f"  {name} [{rel} — dropped] {it['title'][:60]}",
                          flush=True)
                continue
            try:
                s, lat = stamp(name, it["title"])
            except Exception as e:
                print(f"[warn] ollama: {e}", flush=True)
                continue
            raw = s.get("stamp")
            # ONLY THE STOCK'S OWN STORY MAY VETO IT. A group / sector story is
            # logged and shown, but rides in as 중립 so the running desk — which
            # reads `stamp` and knows nothing of `rel` until its next restart —
            # cannot veto a buy on somebody else's headline. The model's literal
            # word is never lost: it stays in stamp_raw.
            eff = raw if (rel == "own" and raw != "무관") else "중립"
            # A VETO MUST EARN ITSELF. Everything above judged the headline;
            # only a stamp that is about to REFUSE A BUY is worth the cost of
            # opening the article, so the two checks below run on ~9% of rows.
            recap = imp = None
            if eff == "위험":
                if is_price_recap(it["title"]):
                    recap = True
                    eff = "중립"          # the price is not news about itself
                else:
                    imp = weigh(name, it["title"], code)
                    # TWO ANSWERS, TWO STANDARDS OF PROOF.
                    #   new=false is a POSITIVE claim — "this already happened
                    #     and the market knows" — so it stands a stamp down
                    #     whatever the reader had in front of it.
                    #   move=없음 is also the rubric's answer when unsure, so it
                    #     only counts with the article actually read; otherwise
                    #     an unfetchable page would quietly clear every veto.
                    if imp.get("new") is False:
                        eff = "중립"
                    elif imp.get("basis") == "body" and imp.get("move") == "없음":
                        eff = "중립"
                    elif imp.get("kind") in ("시황", "의견"):
                        eff = "중립"
            row = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
                   "code": code, "name": name, "src": it["src"],
                   "title": it["title"], "link": it.get("link", ""),
                   "stamp": eff, "stamp_raw": raw, "rel": rel,
                   "why": s.get("why", ""), "sec": round(lat, 1)}
            if recap:
                row["recap"] = True
            if imp:
                row["impact"] = imp
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            _save_seen(seen)   # per stamp: a crash mid-cycle must not
                               # re-stamp (and re-log) what was already done
            fresh += 1
            if verbose:
                _why9 = ("recap" if recap else
                         f"{imp.get('move')}/{imp.get('basis')}" if imp else rel)
                _mark = f"{eff}" if eff == raw else f"{eff}←{raw}/{_why9}"
                print(f"  {name} [{_mark}] {it['title'][:60]} "
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
