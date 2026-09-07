"""뉴스 영향 분석기 — WHICH NEWS CAN MOVE THE PRICE, AND WHICH CANNOT.

Boss 2026-09-07: "we have to analyze news also — any keyword based news can not
effect, we should analyze which news can effect or not effect, so please first
build this thing, then I will check, then you will implement it to our case."

The desk already stamps every headline 위험/중립/호재. That label answers "is
this good or bad", which is not the same question as "will this move the
price". A 호재 that is a market recap, an opinion column, or the fourth outlet
running the same story an hour later moves nothing at all.

This module answers the second question in two independent ways and then puts
them side by side:

  1. WHAT THE TAPE DID (ground truth, no opinion). Duplicate headlines are
     folded into one STORY, dated at the minute it first broke. From the
     1-minute tape we then read the stock's move at +15 / +30 / +60 minutes and
     to the close, and subtract the market's own move over the same minutes
     (the median of the desk names), because a stock that rises with everything
     else did not rise because of its headline. What is left is the ABNORMAL
     move - the part the story has to answer for. Volume is read the same way:
     the ten minutes after the story against the thirty before it.

  2. WHAT A READER WOULD SAY (judgement, not keywords). Each story is read by
     the model with a rubric that asks the questions a person asks: is there a
     NEW fact here, how big is it against this company's size, is it already
     known, and would a desk act on it within the hour. Keyword lists are
     deliberately absent - "수주" in a headline about someone else's order, or
     about an order announced last week, must not score.

Then the two are compared, so the judgement can be scored against the tape
instead of believed. Nothing here touches money or trading state; it reads the
news files and the stored tape, and writes its own report.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

NEWS_DIR = Path(__file__).resolve().parent.parent / "data" / "news_intern"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "news_impact"

# the desk's own universe - the names we hold tape for, so a reaction can be
# measured at all
DESK_CODES = ["000660", "005930", "035420", "017670", "042660", "034020",
              "064350", "015760", "329180", "012330", "000270", "009540",
              "005380", "010140", "272210", "012450", "079550", "042700",
              "402340", "207940"]

_OUTLET = re.compile(r"\s*[-–—]\s*[^-–—]{1,20}$")        # "… - 머니투데이"
_BRACKET = re.compile(r"[\[\(【][^\]\)】]{0,30}[\]\)】]")   # "[속보]", "(종합)"
_PUNCT = re.compile(r"[^0-9A-Za-z가-힣%]+")


def _norm(title: str) -> str:
    """A headline reduced to what it is actually saying."""
    t = _BRACKET.sub(" ", str(title or ""))
    t = _OUTLET.sub("", t)
    t = _OUTLET.sub("", t)          # some titles carry the outlet twice
    return _PUNCT.sub(" ", t).strip()


def _tokens(title: str) -> set:
    return {w for w in _norm(title).split() if len(w) > 1}


def _nums(tokens: set) -> set:
    """The numbers in a headline - 6척, 2조, 305억. Two headlines carrying the
    same figures are almost always the same event in different words."""
    return {w for w in tokens if any(ch.isdigit() for ch in w)}


def _same_story(a: set, b: set) -> bool:
    """Two headlines telling the same story - measured, not keyword-matched.

    Wording varies wildly between outlets ("컨테이너선 6척 수주" / "2조원 규모
    선박 수주" / "이틀 새 2조 305억 수주" are one event), so word overlap alone
    splits a story into three. The figures rarely vary: when the numbers match
    and the words agree at all, it is one event."""
    if not a or not b:
        return False
    share = len(a & b) / min(len(a), len(b))
    na, nb = _nums(a), _nums(b)
    if na and nb and (na & nb) and share >= 0.28:
        return True
    return share >= 0.55


def read_day(day: str, codes: list[str] | None = None) -> list[dict]:
    """Every stamped item of one day for the codes we can measure."""
    f = NEWS_DIR / f"{day}.jsonl"
    if not f.exists():
        return []
    keep = set(codes or DESK_CODES)
    out = []
    for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if str(r.get("code")) in keep and r.get("title"):
            out.append(r)
    out.sort(key=lambda r: str(r.get("ts")))
    return out


def stories(day: str, codes: list[str] | None = None) -> list[dict]:
    """Fold the day's headlines into stories, dated when each first broke.

    The same story reaches us from ten outlets over two hours; the price reacts
    once, at the first one. Counting them separately would credit the story
    with ten reactions and measure the last nine against a move that had
    already happened."""
    rows = read_day(day, codes)
    by_code: dict[str, list[dict]] = {}
    for r in rows:
        by_code.setdefault(str(r.get("code")), []).append(r)
    out = []
    for code, items in by_code.items():
        clusters: list[dict] = []
        for it in items:
            tk = _tokens(it.get("title"))
            for c in clusters:
                if _same_story(tk, c["tokens"]):
                    c["titles"].append(it.get("title"))
                    c["outlets"] += 1
                    c["last_ts"] = it.get("ts")
                    c["tokens"] |= tk
                    if it.get("stamp") and not c.get("stamp"):
                        c["stamp"], c["why"] = it.get("stamp"), it.get("why")
                    break
            else:
                clusters.append({"code": code, "name": it.get("name"),
                                 "ts": it.get("ts"), "last_ts": it.get("ts"),
                                 "title": it.get("title"),
                                 "titles": [it.get("title")], "outlets": 1,
                                 "tokens": tk, "stamp": it.get("stamp"),
                                 "why": it.get("why"), "link": it.get("link")})
        for c in clusters:
            c.pop("tokens", None)
        out.extend(clusters)
    out.sort(key=lambda c: str(c.get("ts")))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 1. WHAT THE TAPE DID
# ─────────────────────────────────────────────────────────────────────────────
_TAPE: dict = {}


def _bars(code: str, day: str) -> list:
    key = (code, day)
    if key not in _TAPE:
        try:
            from services.kiwoom_tape import load, bars_time
            _TAPE[key] = bars_time(load(code, day), 60) or []
        except Exception:
            _TAPE[key] = []
    return _TAPE[key]


def _at(bars: list, hhmm: str):
    """The last bar at or before a clock time, and its index."""
    idx = None
    for i, b in enumerate(bars):
        if str(b.get("hhmm") or "")[:5] <= hhmm:
            idx = i
        else:
            break
    return (bars[idx], idx) if idx is not None else (None, None)


def _plus(t: str, minutes: int) -> str:
    h, m = int(t[:2]), int(t[3:5])
    d = datetime(2000, 1, 1, h, m) + timedelta(minutes=minutes)
    return d.strftime("%H:%M")


def _market_move(day: str, t0: str, t1: str, skip: str) -> float:
    """The market's own move over the same minutes - the median of the desk
    names, so one stock's story cannot define the market it is measured
    against."""
    rets = []
    for c in DESK_CODES:
        if c == skip:
            continue
        bs = _bars(c, day)
        if len(bs) < 10:
            continue
        a, _ = _at(bs, t0)
        b, _ = _at(bs, t1)
        if a and b and a.get("close"):
            rets.append((b["close"] / a["close"] - 1) * 100)
    if not rets:
        return 0.0
    rets.sort()
    return rets[len(rets) // 2]


def _open_baseline(code: str, day: str) -> float:
    """The stock's usual first-ten-minutes volume, from its recent sessions."""
    try:
        from services.kiwoom_rules import stored_days
        past = [d for d in stored_days(code) if d < day][-5:]
    except Exception:
        past = []
    vals = []
    for d in past:
        bs = _bars(code, d)
        if len(bs) >= 10:
            vals.append(sum(b.get("vol") or 0 for b in bs[:10]) / 10)
    if not vals:
        return 0.0
    vals.sort()
    return vals[len(vals) // 2]


def reaction(code: str, day: str, ts: str) -> dict:
    """What the tape did after this story broke, market move removed."""
    bars = _bars(code, day)
    if len(bars) < 10:
        return {"ok": False, "note": "no tape for this stock today"}
    t = str(ts)[11:16] or "09:00"
    # A STORY THAT BREAKS BEFORE THE BELL IS ANSWERED BY THE OPEN (found while
    # testing 한화오션's 09-04 order win: six different stories all broke at
    # 08:32 and every one of them was handed the same 09:00-09:15 move, so the
    # measurement credited one move to six stories - and to the market recap
    # sitting among them). Pre-open stories are measured from yesterday's close
    # THROUGH the opening auction, and the row says how many other stories
    # share that same open, because attribution between them is impossible.
    pre_open = t < "09:00"
    t0 = "09:00" if pre_open else t
    base, i0 = _at(bars, t0)
    if base is None:                      # story broke before the first bar
        base, i0 = bars[0], 0
        t0 = str(bars[0].get("hhmm") or "09:00")[:5]
    out = {"ok": True, "at": t, "measured_from": t0, "pre_open": pre_open,
           "base": base["close"]}
    if pre_open:
        # the gap itself is the answer to overnight news
        try:
            from services.kiwoom_rules import _gap_ref
            _pc = _gap_ref(code, day)
            if _pc:
                _g = (base["close"] / float(_pc) - 1) * 100
                out["gap"] = round(_g, 2)
                # ... and against what everything else did overnight, so a
                # morning when the whole market gaps up does not credit one
                # company's headline with the tide
                _mg = []
                for _c in DESK_CODES:
                    if _c == code:
                        continue
                    _bs = _bars(_c, day)
                    if not _bs:
                        continue
                    try:
                        _p2 = _gap_ref(_c, day)
                    except Exception:
                        _p2 = None
                    if _p2:
                        _mg.append((_bs[0]["close"] / float(_p2) - 1) * 100)
                if _mg:
                    _mg.sort()
                    out["gap_ab"] = round(_g - _mg[len(_mg) // 2], 2)
        except Exception:
            pass
    for mins in (15, 30, 60):
        t1 = _plus(t0, mins)
        nxt, i1 = _at(bars, t1)
        if not nxt or i1 == i0:
            out[f"m{mins}"] = None
            out[f"ab{mins}"] = None
            continue
        raw = (nxt["close"] / base["close"] - 1) * 100
        mkt = _market_move(day, t0, t1, code)
        out[f"m{mins}"] = round(raw, 2)
        out[f"ab{mins}"] = round(raw - mkt, 2)
    close = bars[-1]["close"]
    raw_c = (close / base["close"] - 1) * 100
    out["to_close"] = round(raw_c, 2)
    out["ab_close"] = round(raw_c - _market_move(day, t0, str(bars[-1]["hhmm"])[:5], code), 2)
    # volume: the ten minutes after the story against the thirty before it
    after = [b.get("vol") or 0 for b in bars[i0:i0 + 10]]
    before = [b.get("vol") or 0 for b in bars[max(0, i0 - 30):i0]]
    av_b = (sum(before) / len(before)) if before else 0
    if not av_b:
        # BEFORE THE BELL, THE BASELINE IS OTHER OPENINGS (found while testing
        # 한화오션 09-04: measured against the day's median minute, the opening
        # ten minutes read 14x for every pre-open story - but the first ten
        # minutes are the heaviest of any day, story or no story. The honest
        # comparison is this opening against the same stock's recent openings.)
        av_b = _open_baseline(code, day)
    out["vol_x"] = round((sum(after) / len(after)) / av_b, 2) if av_b and after else None
    return out


def moved(react: dict, min_ab: float = 0.30, min_vol: float = 1.5) -> bool:
    """Did the price actually answer this story?

    Intraday: an abnormal move inside the hour AND a volume reaction - either
    alone is noise on a thin tape. Before the bell there are no minutes to
    measure and the auction answers instead, so the OPENING GAP is the reply,
    measured against the market's own gap (한화오션 09-03: +3.54% while the
    market opened flat - the disclosure was answered before a single minute of
    trading, and reading only the first 15 minutes called it 'no move')."""
    if not react.get("ok"):
        return False
    if react.get("pre_open"):
        g = react.get("gap_ab")
        if g is None:
            g = react.get("gap")
        return abs(g or 0) >= max(min_ab, 0.5)
    ab = max([abs(react.get(k) or 0) for k in ("ab15", "ab30", "ab60")] or [0])
    return ab >= min_ab and (react.get("vol_x") or 0) >= min_vol


def mark_repeats(day: str, rows: list[dict], back: int = 3) -> None:
    """THE SAME STORY ON THE SECOND DAY IS NOT NEWS (한화오션, measured: the
    order win opened the stock +3.54% on 09-03; the identical story running in
    23 outlets on 09-04 opened it flat and it then lagged the market by 1.44%).
    Nothing about the WORDS separates those two mornings - only whether the
    market has already heard it. Each story is therefore checked against the
    previous days' headlines for the same stock."""
    days = [d for d in days_available() if d < day][-back:]
    seen: dict[str, list[set]] = {}
    for d in days:
        for st in stories(d):
            seen.setdefault(str(st["code"]), []).append(_tokens(st.get("title")))
    for r in rows:
        tk = _tokens(r.get("title"))
        old = seen.get(str(r.get("code"))) or []
        r["repeat"] = any(_same_story(tk, o) for o in old)


# ─────────────────────────────────────────────────────────────────────────────
# 2. WHAT A READER WOULD SAY
# ─────────────────────────────────────────────────────────────────────────────
_RUBRIC = """당신은 한국 주식 트레이딩 데스크의 뉴스 애널리스트입니다.
아래 헤드라인이 "지금 이 종목의 가격을 움직일 수 있는 뉴스인가"를 판단하세요.
좋은 소식인지 나쁜 소식인지가 아니라, 가격을 움직일 힘이 있는지가 질문입니다.

판단 기준 (키워드 금지 - 문장이 실제로 무엇을 말하는지 읽으십시오):
1. 새로운 사실이 있는가? 이미 알려진 내용의 반복·요약·시황 정리는 힘이 없습니다.
2. 이 회사 규모에 비해 큰가? 매출 대비 작은 계약은 헤드라인이 화려해도 힘이 없습니다.
3. 이 회사의 이야기인가? 업종 기사·경쟁사 기사에 이름만 언급된 것은 힘이 없습니다.
4. 언제 작용하는가? 오늘 몇 분 안에 작용할 일인지, 몇 달 뒤의 일인지.
5. 사실인가 의견인가? 애널리스트 전망·칼럼·주가 등락 보도 자체는 힘이 없습니다.

중요 - 실제 측정된 사실: 이 데스크에 하루 500건이 들어오지만 실제로 가격을
움직이는 것은 약 5%뿐입니다. 대부분의 뉴스는 아무 일도 일으키지 않습니다.
확신이 없으면 "없음"이라고 답하십시오. "강함"은 공시급 새 사실에만 쓰십시오.

JSON 한 줄로만 답하십시오:
{"move":"강함|보통|없음","dir":"상승|하락|중립","kind":"수주|실적|증자|소송|규제|제품|인사|시황|의견|기타","new":true|false,"why":"한 문장 근거"}"""


def judge(story: dict, model: str | None = None) -> dict:
    """One story, read with the rubric. No keyword lists anywhere."""
    from services.llm_client import chat_completion_sync
    head = str(story.get("title") or "")[:300]
    name = story.get("name") or story.get("code")
    msg = (f"종목: {name}\n헤드라인: {head}\n"
           f"같은 내용 보도 매체 수: {story.get('outlets')}개")
    try:
        txt = chat_completion_sync(_RUBRIC, [{"role": "user", "content": msg}],
                                   model=model) or ""
    except Exception as e:
        return {"move": "?", "why": f"판단 실패: {str(e)[:80]}"}
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return {"move": "?", "why": f"형식 오류: {txt[:80]}"}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {"move": "?", "why": f"형식 오류: {txt[:80]}"}
    if d.get("move") not in ("강함", "보통", "없음"):
        d["move"] = "?"
    return d


# ─────────────────────────────────────────────────────────────────────────────
# the report
# ─────────────────────────────────────────────────────────────────────────────
def _score(rows: list[dict]) -> dict:
    """Grade the reading against the tape - the only honest way to know whether
    the analysis is worth anything. A story the reader called 강함/보통 should
    be one the price answered; a story called 없음 should be one it ignored."""
    said_move = [r for r in rows if (r.get("judge") or {}).get("move") in ("강함", "보통")]
    said_none = [r for r in rows if (r.get("judge") or {}).get("move") == "없음"]
    hit = sum(1 for r in said_move if r.get("moved"))
    quiet = sum(1 for r in said_none if not r.get("moved"))
    return {"judged": len(said_move) + len(said_none),
            "said_move": len(said_move), "of_those_moved": hit,
            "said_none": len(said_none), "of_those_quiet": quiet,
            "precision": round(hit / len(said_move), 3) if said_move else None,
            "quiet_rate": round(quiet / len(said_none), 3) if said_none else None}


def analyze(day: str, codes: list[str] | None = None, with_judge: bool = False,
            limit: int = 0, model: str | None = None, judge_top: int = 60) -> dict:
    """Every story of one day, what the tape did, and (optionally) the reading."""
    rows = []
    for st in stories(day, codes):
        r = reaction(str(st["code"]), day, str(st.get("ts")))
        row = {k: st.get(k) for k in ("code", "name", "ts", "title", "outlets",
                                      "stamp", "why", "link")}
        row["n_titles"] = len(st.get("titles") or [])
        row["react"] = r
        row["moved"] = moved(r)
        # WHICH WAY IT MOVED, AGAINST WHAT THE HEADLINE PROMISED. A 호재 whose
        # stock then lags the market by 1.4% did move - it just moved against
        # the story, which is the most useful row on the page: the news that
        # everyone called good and the market ignored.
        _mv = (r.get("gap_ab") if r.get("pre_open") else
               max([r.get(k) or 0 for k in ("ab15", "ab30", "ab60")],
                   key=lambda x: abs(x)))
        row["move_pct"] = _mv
        if row["moved"] and st.get("stamp") in ("호재", "위험") and _mv is not None:
            row["dir_ok"] = (_mv > 0) if st["stamp"] == "호재" else (_mv < 0)
        rows.append(row)
    # ONE OPEN, ONE STORY (found while testing 한화오션 09-04: six stories broke
    # at 08:32 - the order win, two rewrites of it, and a market recap listing
    # eight unrelated tickers - and every one of them was handed the SAME
    # opening move, so the recap scored as market-moving. The open belongs to
    # the story the market was actually reading: the one the outlets piled
    # onto. If no story dominates, the morning is marked shared and scored by
    # nobody, because attribution is genuinely impossible.)
    # THE SAME PROBLEM EXISTS INSIDE THE DAY. Three stories about one stock
    # inside the same quarter hour are all handed that quarter hour's move, and
    # the reaction gets credited to each - including to "삼성중공업 주가 상승 중",
    # a story whose whole content IS the move it is being credited with. One
    # window, one owner: the story the outlets piled onto, or nobody.
    _buckets: dict = {}
    for r in rows:
        rc = r["react"] or {}
        if rc.get("ok") and not rc.get("pre_open"):
            _b = (r["code"], str(rc.get("measured_from") or "")[:4])
            _buckets.setdefault(_b, []).append(r)
    for _b, grp in _buckets.items():
        if len(grp) < 2:
            continue
        grp.sort(key=lambda r: -(r.get("outlets") or 0))
        top = grp[0].get("outlets") or 0
        second = grp[1].get("outlets") or 0
        dom = grp[0] if top >= max(3, second * 3) else None
        for r in grp:
            r["shares_window_with"] = len(grp) - 1
            if dom is None:
                r["attribution"] = "shared"
                r["moved"] = False
            elif r is dom:
                r["attribution"] = "owns the window"
            else:
                r["attribution"] = "rode the window"
                r["moved"] = False
    for code in {r["code"] for r in rows}:
        pre = [r for r in rows if r["code"] == code
               and (r["react"] or {}).get("pre_open")]
        if not pre:
            continue
        pre.sort(key=lambda r: -(r.get("outlets") or 0))
        top = pre[0].get("outlets") or 0
        second = (pre[1].get("outlets") or 0) if len(pre) > 1 else 0
        dominant = pre[0] if (len(pre) == 1 or top >= max(3, second * 3)) else None
        for r in pre:
            r["shares_open_with"] = len(pre) - 1
            if dominant is None:
                r["attribution"] = "shared"
                r["moved"] = False
            elif r is dominant:
                r["attribution"] = "owns the open"
            else:
                r["attribution"] = "rode the open"
                r["moved"] = False
    # the stories the price ANSWERED lead the page; a big move whose owner is
    # unclear is still shown, but below them - it teaches nothing about which
    # news moves a price
    # A REAL EVENT IS CARRIED BY MORE THAN ONE OUTLET (seen on the finished
    # page: "삼성중공업 주가 상승 중" - a story whose entire content is the move
    # it was being credited with - and "SNT에너지 수주", another company's news
    # filed under 현대로템, both owned their window because they happened to be
    # the only story in it. One newsroom noticing something is not the market
    # hearing it, so a lone single-outlet story explains nothing; the move is
    # real, its cause is simply not on this page.)
    for r in rows:
        if r.get("moved") and (r.get("outlets") or 0) < 3:
            r["moved"] = False
            r["attribution"] = "single outlet - cause unclear"
    rows.sort(key=lambda r: (0 if r.get("moved") else 1,
                             -abs(r.get("move_pct") or 0)))
    if limit:
        rows = rows[:limit]
    mark_repeats(day, rows)
    if with_judge:
        # THE STORIES A PERSON WOULD EVER READ. Five hundred headlines a day
        # cross this desk; the ones that carry a story are the ones the outlets
        # piled onto. Judging the long tail of single-outlet rewrites costs
        # minutes and teaches nothing, so the reading goes to the top of the
        # pile and everything else keeps its measurement only.
        _rank = sorted(rows, key=lambda r: -(r.get("outlets") or 0))
        for row in _rank[:(judge_top or len(rows))]:
            row["judge"] = judge(row, model=model)
        out_hit = _score(rows)
    else:
        out_hit = None
    out = {"ok": True, "day": day, "stories": len(rows),
           "moved": sum(1 for r in rows if r["moved"]),
           "score": out_hit if with_judge else None, "rows": rows}
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"{day}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log.warning(f"news_impact save {day}: {str(e)[:80]}")
    return out


def days_available() -> list[str]:
    return sorted(p.stem for p in NEWS_DIR.glob("2*.jsonl"))
