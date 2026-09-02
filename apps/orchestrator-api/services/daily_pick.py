"""🎯 daily_pick — every morning, choose TODAY's five stocks from the advisor's checklist.

The boss's instruction (2026-08-10): the five must be chosen fresh each morning, judged on
BOTH the stock's long-run character AND its current state, using the 90 automatable items
of the 100-item checklist (1-10 are the trader's own condition and belong to a human).
ML plays no part in this choice.

TWO HALVES, both required:

  A. CHARACTER  - measured over a year, changes slowly. Is this a stock our style CAN
                  trade at all? (trading value, tick cost, trending nature, whether
                  pullbacks continue, how often volume surges.)   items 46,47,48,52,64,69
  B. CONDITION  - measured from the days right before today, changes daily. Is this
                  stock in a buyable state RIGHT NOW? (moving-average alignment, new
                  highs, RSI zone, MACD cross, Bollinger position, distance from
                  yesterday's close, three-day flows, short-selling heat.)
                                                     items 31,32,34,43,50,51,58,60,61,62,67

A stock needs both: good character with bad condition waits for another day; bad
character never qualifies however pretty today looks.

Everything is computed from data STRICTLY BEFORE the day being picked, so an 08:40
selection never peeks at the session it is choosing for.
"""
from __future__ import annotations

import json
from datetime import date as _date
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent.parent / "data"
_PICK_FILE = _DATA / "today_picks.json"
_CHAR_FILE = _DATA / "stock_character.json"      # the slow half, refreshed weekly

# the checklist's own sections, weighted as they weigh on "which stock today"
# REWEIGHED BY MEASUREMENT (boss 2026-08-25: "out of 90 each item's effect is
# different - find which matters"). Two independent 250-day backtests over the
# 51-stock history: turnover IC +0.13/+0.09 (king), MA-alignment +0.08/+0.08,
# trendiness +0.05/+0.03, tick cost +0.03/+0.02; RSI/MACD ~0 both periods,
# and #67 vs-prev-close NEGATIVE both periods (-0.04/-0.08). The winning
# scheme (top-5 next-day rise +0.55%p/+0.46%p vs +0.39/+0.25 for the old
# weights) puts volume and trend in charge; levels/momentum leave the
# ranking (the engine still enforces zone laws at buy time). Flows kept at
# 10 - no per-day history to test it, industry practice says keep it.
OLD_WEIGHTS = {"trend": 25, "liquidity": 20, "flexibility": 20,
               "levels": 15, "momentum": 10, "flows": 10}
WEIGHTS = {"trend": 35, "liquidity": 45, "flexibility": 10,
           "levels": 0, "momentum": 0, "flows": 10}
N_PICKS = 5

# THE BOSS'S DESK (2026-08-10, his call). He named the six companies he wants traded
# every day, so the desk is fixed and the checklist no longer chooses who trades - it
# scores them. The 100-item score still runs every morning over every candidate and is
# shown in full on the board, so he can see where his six rank and who would have been
# picked on merit; it just does not decide the desk any more. Change this list and the
# collector follows it the next morning (or immediately with daily-pick?refresh=1&force=1).
DESK = [
    "000660",   # SK하이닉스
    "005930",   # 삼성전자
    "035420",   # NAVER
    "017670",   # SK텔레콤
    "042660",   # 한화오션
    "034020",   # 두산에너빌리티
]
PINNED = DESK           # kept for callers that still ask which names are fixed

# WHICH DESK IS LIVE (boss 2026-08-11). "fixed" = his six, traded every day. "score" =
# the day's top five from the 100-item checklist, chosen fresh each morning. He wanted to
# be able to switch and have the other desk turn OFF, so this is one setting, remembered
# on disk, read by pick() and by the collector - never two desks trading at once.
_MODE_FILE = _DATA / "desk_mode.json"


def _today() -> str:
    from services.kiwoom_tape import _day
    return _day()


def desk_mode() -> str:
    """BOTH DESKS TRADE BY DEFAULT (boss 2026-08-24: "if I do not turn off one of them,
    both must continue trading") — his six AND the checklist's top five together, every
    day, unless he switches one off. A switch to a single desk ("fixed" or "score")
    holds for the rest of that day and no longer: the stored mode is stamped with the
    day it was chosen, and a stamp from any earlier day reads back as "both".
    (History: 2026-08-11→08-24 the default was "fixed" — his six only.)"""
    try:
        d = json.loads(_MODE_FILE.read_text(encoding="utf-8"))
        m = d.get("mode")
        if m not in ("fixed", "score", "both"):
            return "both"
        if m != "both" and d.get("day") != _today():
            return "both"           # yesterday's single-desk choice does not carry into today
        return m
    except Exception:
        return "both"


def set_desk_mode(mode: str) -> str:
    mode = mode if mode in ("fixed", "score", "both") else "both"
    _DATA.mkdir(exist_ok=True)
    _MODE_FILE.write_text(json.dumps({"mode": mode, "day": _today()}),
                          encoding="utf-8")
    return mode


_N_FILE = _DATA / "reco_n.json"


def reco_n() -> int:
    """How many top-scored stocks the reco desk trades (boss 2026-08-24: 'we can
    choose number of stock also'). Default 5; persisted on disk; clamped 1..10."""
    try:
        n = int(json.loads(_N_FILE.read_text(encoding="utf-8")).get("n", N_PICKS))
        return max(1, min(n, 10))
    except Exception:
        return N_PICKS


def set_reco_n(n: int) -> int:
    n = max(1, min(int(n), 10))
    _DATA.mkdir(exist_ok=True)
    _N_FILE.write_text(json.dumps({"n": n}), encoding="utf-8")
    return n


def score_five(n: int | None = None) -> list[tuple[str, str]]:
    """Today's top-n by the 100-item score — saved morning file first, fresh compute
    fallback, [] when the picker cannot run (callers must then fall back to DESK,
    never to an empty desk — the 2026-08-24 blind-desk lesson)."""
    if n is None:
        n = reco_n()
    def _nm(r):
        # some rows carry the code as their name (e.g. 069500) — resolve to a real name
        if r.get("name") and r["name"] != r["code"]:
            return r["name"]
        try:
            from services.stock_resolver import display_name
            return display_name(r["code"]) or r["code"]
        except Exception:
            return r["code"]
    try:
        d = json.loads(_PICK_FILE.read_text(encoding="utf-8"))
        if d.get("day") == _today() and d.get("rows"):
            rows = sorted(d["rows"], key=lambda r: -(r.get("score") or 0))
            return [(r["code"], _nm(r)) for r in rows[:n]]
    except Exception:
        pass
    try:
        res = pick(_today(), n)
        if res.get("ok"):
            return [(r["code"], _nm(r)) for r in res["rows"][:n]]
    except Exception:
        pass
    return []


def _conn():
    from ml._db import get_conn
    return get_conn()


# ─────────────────────────── A. CHARACTER (slow, from the year) ────────────────────
def build_character(before: str = "") -> dict[str, dict]:
    """Score every candidate's long-run character. Cached to disk; refresh weekly."""
    import statistics
    out: dict[str, dict] = {}
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM minute_bars_hist ORDER BY ticker")
    tickers = [r[0] for r in cur.fetchall()]
    # a desk stock is scored whether or not we happen to hold minute history for it -
    # SK텔레콤 has none and would otherwise be missing from its own board
    tickers = sorted(set(tickers) | set(DESK))
    cur.execute("SELECT code, name FROM krx_stocks")
    names = dict(cur.fetchall())
    # KOSPI ONLY (boss 2026-08-25 evening: "remove any KOSDAQ and ETF"):
    # candidates must be KOSPI-listed companies. ETFs (KODEX 200) are not in
    # krx_stocks with a KOSPI market tag and fall out with the same net.
    # The desk six are KOSPI and stay unconditionally.
    cur.execute("SELECT code, market FROM krx_stocks")
    _mkt9 = dict(cur.fetchall())
    tickers = [tk for tk in tickers
               if tk in DESK or (_mkt9.get(tk) or "").upper() == "KOSPI"]
    for tk in tickers:
        cur.execute("""SELECT date, close, volume FROM raw_daily_prices
                       WHERE ticker=%s ORDER BY date""", (tk,))
        rows = [(d, float(c or 0), float(v or 0)) for d, c, v in cur.fetchall()]
        if before:
            dd = _date(int(before[:4]), int(before[4:6]), int(before[6:]))
            rows = [r for r in rows if r[0] < dd]
        if len(rows) < 120:
            continue
        cl = [r[1] for r in rows]; vol = [r[2] for r in rows]
        px = cl[-1]
        from services.proof_sim import _tick as krx_tick
        t = krx_tick(px) or 1
        turn = sorted(cl[i] * vol[i] for i in range(len(cl) - 120, len(cl)))
        # 46/21 trading value · 47 volume surges · 48 tick cost · 52 trending
        surge = sum(1 for i in range(1, len(vol)) if vol[i - 1] and vol[i] > 1.5 * vol[i - 1])
        trend_q = []
        for i in range(20, len(cl)):
            travel = sum(abs(cl[j] - cl[j - 1]) for j in range(i - 19, i + 1))
            trend_q.append(abs(cl[i] - cl[i - 20]) / travel * 100 if travel else 0)
        out[tk] = {"name": names.get(tk, tk), "price": px, "tick": t,
                   "turnover": turn[len(turn) // 2],
                   "tick_pct": t / px * 100 if px else 9.9,
                   "surge_rate": surge / max(len(vol) - 1, 1) * 100,
                   "trendiness": statistics.mean(trend_q) if trend_q else 0}
    conn.close()
    _DATA.mkdir(exist_ok=True)
    _PICK_FILE.parent.mkdir(exist_ok=True)
    _CHAR_FILE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def character(refresh: bool = False, before: str = "") -> dict[str, dict]:
    if not refresh and _CHAR_FILE.exists():
        try:
            return json.loads(_CHAR_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return build_character(before)


# ─────────────────────────── B. CONDITION (fast, from recent days) ─────────────────
def _rsi(cl, n=14):
    if len(cl) < n + 1:
        return 50.0
    g = l = 0.0
    for i in range(len(cl) - n, len(cl)):
        d = cl[i] - cl[i - 1]
        g += max(d, 0.0); l += max(-d, 0.0)
    return 100.0 if l == 0 else 100 - 100 / (1 + (g / n) / (l / n))


def _ema(v, n):
    k = 2 / (n + 1); e = v[0]
    for x in v[1:]:
        e = x * k + e * (1 - k)
    return e


def condition(day: str, codes: list[str]) -> dict[str, dict]:
    """Each stock's state as of the morning of `day` - only earlier data is read."""
    dd = _date(int(day[:4]), int(day[4:6]), int(day[6:]))
    out: dict[str, dict] = {}
    conn = _conn(); cur = conn.cursor()
    for tk in codes:
        cur.execute("""SELECT date, open, high, low, close, volume FROM raw_daily_prices
                       WHERE ticker=%s AND date < %s ORDER BY date DESC LIMIT 90""",
                    (tk, dd))
        rows = list(reversed([(d, float(o or 0), float(h or 0), float(l or 0),
                               float(c or 0), float(v or 0)) for d, o, h, l, c, v in cur.fetchall()]))
        if len(rows) < 61:
            continue
        cl = [r[4] for r in rows]; vol = [r[5] for r in rows]
        # ALREADY-RISING CHECK (boss 2026-09-02 16:0x: "if it is increasing do
        # not recommend stock - if stock is increasing from last days according
        # to monthly and daily chart then it should not be recommended").
        # up3 = how many of the last 3 sessions closed higher; upm = how many of
        # the last 2 months finished higher. Measured on our OWN 알고3 trades
        # over 20 sessions: a stock up 3 days straight averaged -0.354%/trip
        # against -0.087% for one falling three days - four times worse, over
        # 77 trades. (The monthly half did not discriminate: -0.178% either
        # way; it is carried because he asked for both charts.)
        _up3 = sum(1 for _k in (1, 2, 3) if len(cl) > _k and cl[-_k] > cl[-_k - 1])
        # THE MIDDLE LINE (boss 2026-09-02 16:2x: "if average monthly price is
        # higher than middle then we should not buy - give 0 score and even
        # directly cancel; we should buy stock below the middle line or in the
        # middle"). _mid is the price's distance from its own 20-day average.
        # MEASURED on our OWN 417 알고3 trades over 20 sessions - the strongest
        # single filter found so far: entries made 2%+ ABOVE the middle line
        # were 279 trips at -0.194%/trip, -54.22% in total - 85% of everything
        # the desk lost. Entries BELOW the middle were the only bucket that did
        # not lose (+0.001%/trip).
        _ma20 = sum(cl[-20:]) / 20 if len(cl) >= 20 else (cl[-1] if cl else 0)
        _mid = (100.0 * (cl[-1] / _ma20 - 1)) if _ma20 else 0.0
        # TWO GATES, NOT ONE (boss 2026-09-02 17:1x: "so stock should pass 2
        # gates right - it should be below the average in the last 20 days AND
        # last year also"). Measured over 12 years, 103,873 stock-days:
        #   below BOTH month and year  30,410 days  45.6% up  +0.009%  <- only
        #                                                     positive bucket
        #   below the month only       20,804 days  45.8% up  -0.065%
        #   below the year only        17,709 days  43.9% up  -0.054%
        #   above both                 34,950 days  44.6% up  -0.063%
        # The month test ALONE is worth no more than failing both; it is the
        # pair that earns the edge.
        _ylen = min(len(cl), 246)
        _mayr = sum(cl[-_ylen:]) / _ylen if _ylen >= 60 else _ma20
        _midy = (100.0 * (cl[-1] / _mayr - 1)) if _mayr else 0.0
        _mo: dict = {}
        for _r in rows:
            _mo.setdefault(_r[0].strftime("%Y-%m"), []).append(_r[4])
        _mm = [v for v in _mo.values() if len(v) >= 5][-2:]
        _upm = sum(1 for v in _mm if v[-1] > v[0])
        s5 = sum(cl[-5:]) / 5; s20 = sum(cl[-20:]) / 20; s60 = sum(cl[-60:]) / 60
        w20 = cl[-20:]; m = sum(w20) / 20
        sd = (sum((x - m) ** 2 for x in w20) / 20) ** 0.5
        macd_now = _ema(cl[-26:], 12) - _ema(cl[-26:], 26)
        macd_prev = _ema(cl[-27:-1], 12) - _ema(cl[-27:-1], 26)
        cur.execute("""SELECT foreign_net_value, inst_net_value, individual_net_value
                       FROM korean_investor_flows WHERE ticker=%s AND date < %s
                       ORDER BY date DESC LIMIT 3""", (tk, dd))
        fl = [(float(a or 0), float(b or 0), float(c or 0)) for a, b, c in cur.fetchall()]
        sr = 0.0
        try:
            cur.execute("""SELECT short_ratio FROM korean_short_selling
                           WHERE ticker=%s AND date < %s ORDER BY date DESC LIMIT 5""", (tk, dd))
            v = [float(x[0] or 0) for x in cur.fetchall()]
            sr = sum(v) / len(v) if v else 0.0
        except Exception:
            conn.rollback()
        out[tk] = {
            # 51/58 alignment · 50 new high · 60 RSI · 61 MACD · 62 Bollinger · 67 vs close
            "aligned": 2 if s5 > s20 > s60 else (1 if s5 > s20 else 0),
            "new_high": 1 if cl[-1] >= max(cl[-20:]) else 0,
            "above_s20": (cl[-1] / s20 - 1) * 100 if s20 else 0,
            "rsi": _rsi(cl),
            "macd_cross": 1 if macd_prev <= 0 < macd_now else (0.5 if macd_now > 0 else 0),
            "bb_pos": (cl[-1] - m) / (2 * sd) if sd else 0,
            "vs_close": (cl[-1] / cl[-2] - 1) * 100 if cl[-2] else 0,
            "up3": _up3, "upm": _upm, "mid": round(_mid, 2), "midy": round(_midy, 2),
            "vol_surge": (vol[-1] / (sum(vol[-21:-1]) / 20)) if sum(vol[-21:-1]) else 1,
            "foreign3": sum(x[0] for x in fl),
            "inst3": sum(x[1] for x in fl),
            "retail_crowd": 1 if (fl and fl[0][2] > 0 and fl[0][0] < 0) else 0,
            "short_ratio": sr,
        }
    conn.close()
    return out


# ─────────────────────────── the daily pick ────────────────────────────────────────
def _pct(vals: list[float], v: float, hi: bool = True) -> float:
    if not vals:
        return 50.0
    s = sorted(vals)
    p = sum(1 for x in s if x < v) / max(len(s) - 1, 1) * 100
    return p if hi else 100 - p


def pick(day: str, n: int | None = None, refresh_character: bool = False) -> dict[str, Any]:
    """TODAY's ranking. Character (year) x Condition (recent days), checklist weights."""
    if n is None:
        n = reco_n()
    ch = character(refresh=refresh_character, before=day)
    codes = list(ch)
    co = condition(day, codes)
    codes = [c for c in codes if c in co]
    if not codes:
        return {"ok": False, "error": "no candidates with enough history"}

    A = {k: [ch[c][k] for c in codes] for k in ("turnover", "tick_pct", "surge_rate", "trendiness")}
    B = {k: [co[c][k] for c in codes] for k in
         ("aligned", "new_high", "above_s20", "rsi", "macd_cross", "bb_pos",
          "vs_close", "vol_surge", "foreign3", "inst3", "short_ratio")}
    rows = []
    for c in codes:
        a, b = ch[c], co[c]
        g = {
            # 46,47,69,21 - can we get in and out?
            "liquidity": 0.7 * _pct(A["turnover"], a["turnover"]) +
                         0.3 * _pct(A["surge_rate"], a["surge_rate"]),
            # 48 - is one tick cheap enough to profit?
            "flexibility": _pct(A["tick_pct"], a["tick_pct"], hi=False),
            # 50,51,52,58 - is it trending and aligned right now?
            "trend": (0.40 * (b["aligned"] / 2 * 100) +
                      0.25 * _pct(A["trendiness"], a["trendiness"]) +
                      0.20 * (b["new_high"] * 100) +
                      0.15 * _pct(B["above_s20"], b["above_s20"])),
            # 62,63,67,74 - where is it against its own levels?
            "levels": (0.5 * max(0.0, min(100.0, (b["bb_pos"] + 1) * 50)) +
                       0.5 * _pct(B["vs_close"], b["vs_close"])),
            # 60,61 - momentum with room left
            "momentum": (0.5 * (100 - abs(b["rsi"] - 55) * 2.2) +
                         0.5 * (b["macd_cross"] * 100)),
            # 31,32,34,43 - is big money with us?
            "flows": (0.45 * _pct(B["foreign3"], b["foreign3"]) +
                      0.30 * _pct(B["inst3"], b["inst3"]) +
                      0.15 * (0 if b["retail_crowd"] else 100) +
                      0.10 * _pct(B["short_ratio"], b["short_ratio"], hi=False)),
        }
        g = {k: max(0.0, min(100.0, v)) for k, v in g.items()}
        score = sum(g[k] * WEIGHTS[k] for k in g) / 100
        why = []
        if b["aligned"] == 2: why.append("5>20>60 정배열" )
        if b["new_high"]: why.append("20일 신고가")
        if b["macd_cross"] == 1: why.append("MACD 골든크로스")
        if b["foreign3"] > 0: why.append("외국인 순매수")
        if b["vol_surge"] > 1.5: why.append("거래량 급증")
        if a["tick_pct"] < 0.10: why.append("호가 비용 낮음")
        nm = a.get("name") or c
        if nm == c:                     # some rows carry the code as their name (069500)
            try:
                from services.stock_resolver import display_name
                nm = display_name(c) or c
            except Exception:
                pass
        # THE OPEN CALCULATION (boss 2026-08-25: "when I click a column it
        # should show all sub-checks with the actual calculation and score"):
        # every group carries its checklist items, the measured raw value,
        # and the 0-100 sub-score the weighted sum was built from.
        detail = {
            "liquidity": [
                {"k": "거래대금 회전 (46·47)", "v": f"{a['turnover']:,.0f}",
                 "s": round(_pct(A["turnover"], a["turnover"])), "w": 70},
                {"k": "거래량 급증 빈도 (69)", "v": f"{a['surge_rate']:.2f}",
                 "s": round(_pct(A["surge_rate"], a["surge_rate"])), "w": 30}],
            "flexibility": [
                {"k": "호가 1틱 비용 (48)", "v": f"{a['tick_pct']:.3f}%",
                 "s": round(_pct(A["tick_pct"], a["tick_pct"], hi=False)),
                 "w": 100}],
            "trend": [
                {"k": "이평 정배열 5>20>60 (50)",
                 "v": ("정배열" if b["aligned"] == 2
                       else "부분" if b["aligned"] == 1 else "역배열"),
                 "s": round(b["aligned"] / 2 * 100), "w": 40},
                {"k": "추세성 · 1년 (52)", "v": f"{a['trendiness']:.2f}",
                 "s": round(_pct(A["trendiness"], a["trendiness"])), "w": 25},
                {"k": "20일 신고가 (51)", "v": "예" if b["new_high"] else "아니오",
                 "s": 100 if b["new_high"] else 0, "w": 20},
                {"k": "20일선 위 거리 (58)", "v": f"{b['above_s20']:+.2f}%",
                 "s": round(_pct(B["above_s20"], b["above_s20"])), "w": 15}],
            "levels": [
                {"k": "볼린저 위치 (62·63)", "v": f"{b['bb_pos']:+.2f}",
                 "s": round(max(0.0, min(100.0, (b["bb_pos"] + 1) * 50))),
                 "w": 50},
                {"k": "전일 종가 대비 (67)", "v": f"{b['vs_close']:+.2f}%",
                 "s": round(_pct(B["vs_close"], b["vs_close"])), "w": 50}],
            "momentum": [
                {"k": "RSI 55 근접 (60)", "v": f"{b['rsi']:.0f}",
                 "s": round(max(0.0, 100 - abs(b["rsi"] - 55) * 2.2)), "w": 50},
                {"k": "MACD 골든크로스 (61)",
                 "v": "예" if b["macd_cross"] == 1 else "아니오",
                 "s": 100 if b["macd_cross"] == 1 else 0, "w": 50}],
            "flows": [
                {"k": "외국인 3일 순매수 (31)", "v": f"{b['foreign3']:+,.0f}",
                 "s": round(_pct(B["foreign3"], b["foreign3"])), "w": 45},
                {"k": "기관 3일 순매수 (32)", "v": f"{b['inst3']:+,.0f}",
                 "s": round(_pct(B["inst3"], b["inst3"])), "w": 30},
                {"k": "개인 과열 여부 (34)",
                 "v": "과열" if b["retail_crowd"] else "정상",
                 "s": 0 if b["retail_crowd"] else 100, "w": 15},
                {"k": "공매도 비중 (43)", "v": f"{b['short_ratio']:.1f}%",
                 "s": round(_pct(B["short_ratio"], b["short_ratio"], hi=False)),
                 "w": 10}],
        }
        # ALREADY-RISING (boss 09-02 16:0x) - a stock up 3 sessions running, or
        # up both of the last 2 months, may not be RECOMMENDED. It still scores
        # and still shows in the table, marked, so the room can see why.
        # ABOVE THE MIDDLE LINE = NOT RECOMMENDED (his 16:2x rule), alongside
        # the already-rising test from 16:0x
        _ris = ((b.get("up3", 0) >= 3) or (b.get("upm", 0) >= 2)
                or (b.get("mid", 0) > 0) or (b.get("midy", 0) > 0))
        rows.append({"code": c, "name": nm, "score": round(score, 1),
                     "rising": _ris, "up3": b.get("up3"), "upm": b.get("upm"),
                     "mid": b.get("mid"), "midy": b.get("midy"),
                     "groups": {k: round(v) for k, v in g.items()},
                     "detail": detail,
                     "tick_pct": round(a["tick_pct"], 3), "rsi": round(b["rsi"]),
                     "aligned": b["aligned"], "new_high": b["new_high"],
                     "foreign3": b["foreign3"], "why": why})
    rows.sort(key=lambda r: -r["score"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r["pinned"] = r["code"] in DESK
    # WHICH DESK TRADES. "fixed" = his six in his order; "score" = the day's top n from
    # the checklist. Only one is ever live, and `by_score` always marks the checklist's
    # own answer so the board can show what the other desk would have done.
    # THE RISING ARE NOT RECOMMENDED (boss 09-02 16:0x). They keep their rank
    # and stay visible; they simply cannot take one of the checklist's seats.
    _elig = [r for r in rows if not r.get("rising")]
    earned = _elig[:n]
    mode = desk_mode()
    if mode == "score":
        chosen = list(earned)
    elif mode == "both":
        # THE ONE DESK (boss 2026-09-02 09:1x: "I do not wanna go and check both
        # menus - only one menu and all stock what I wanna... total 11"): his six
        # in his order, ALWAYS, then the checklist's best n that are not already
        # among them. Taking `earned[:n]` and dropping the duplicates gave FEWER
        # than 6+n whenever the checklist crowned one of his own six (today it
        # crowned 두산 #2 and 하이닉스 #5, so the desk would have been 9, not 11);
        # the desk now fills to 6+n every day.
        chosen = [r for r in rows if r["code"] in DESK]
        chosen.sort(key=lambda r: DESK.index(r["code"]))
        chosen += [r for r in _elig if r["code"] not in DESK][:n]
    else:
        chosen = [r for r in rows if r["code"] in DESK]
        chosen.sort(key=lambda r: DESK.index(r["code"]))   # his order, not the score's
    live = {r["code"] for r in chosen}
    for r in rows:
        r["on_desk"] = r["code"] in live
        r["by_score"] = r in earned                     # the checklist's own five
        r["pinned"] = r["code"] in DESK                 # one of his six, live or not
        r["added"] = r["on_desk"] and r not in earned   # trading by his choice alone
    missing = [c for c in DESK if c not in {r["code"] for r in rows}]
    return {"ok": True, "day": day, "picks": [r["code"] for r in chosen],
            "mode": mode, "pinned": DESK, "desk": DESK,
            "fixed_desk": mode == "fixed",
            "n_earned": len([r for r in chosen if r["by_score"]]),
            "n_added": len([r for r in chosen if r["added"]]),
            "missing": missing, "weights": WEIGHTS, "rows": rows}


def save_picks(day: str, n: int | None = None) -> dict[str, Any]:
    res = pick(day, n)
    if res.get("ok"):
        chosen = {c for c in res["picks"]}
        res["rows_desk"] = [r for r in res["rows"] if r["code"] in chosen]
        _DATA.mkdir(exist_ok=True)
        _PICK_FILE.write_text(json.dumps(
            {"day": day,
             "picks": [[r["code"], r["name"]] for r in res.get("rows_desk", [])],
             "rows": res["rows"]}, ensure_ascii=False), encoding="utf-8")
    return res


def load_picks() -> list[tuple[str, str]]:
    """Today's chosen five, or [] if the morning job has not run."""
    try:
        d = json.loads(_PICK_FILE.read_text(encoding="utf-8"))
        from services.kiwoom_tape import _day
        if d.get("day") == _day():
            return [(c, n) for c, n in d.get("picks", [])]
    except Exception:
        pass
    return []


def effective_picks(n: int | None = None) -> list[tuple[str, str]]:
    """THE BENCH LAW's morning form (boss 2026-08-27: 'if one of them inside
    top 7 is not on the buying case then 8th can join'): walk today's ranking
    in score order, skip any stock the peak law forbids buying RIGHT NOW
    (dp >= 0.85), fill n seats. Bench capped at rank 10 - quality measured
    equal through seat 9, diluting at 10. Falls back to the plain top-n when
    the tape or ranking is unavailable (never an empty desk)."""
    if n is None:
        n = reco_n()
    try:
        d = json.loads(_PICK_FILE.read_text(encoding="utf-8"))
        from services.kiwoom_tape import _day
        if d.get("day") != _day() or not d.get("rows"):
            return load_picks()[:n]
        rows = sorted(d["rows"], key=lambda r: -(r.get("score") or 0))[:10]
        import services.kiwoom_tape as kt
        import services.kiwoom_rules as kr
        out: list[tuple[str, str]] = []
        for r in rows:
            dp = None
            try:
                px = kt.last_price(r["code"])
                if px:
                    dp = kr._daily_pos(r["code"], px)
            except Exception:
                pass
            if dp is not None and dp >= 0.85:
                continue
            out.append((r["code"], r.get("name") or r["code"]))
            if len(out) >= n:
                break
        return out or load_picks()[:n]
    except Exception:
        return load_picks()[:n]
