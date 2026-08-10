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
WEIGHTS = {"trend": 25, "liquidity": 20, "flexibility": 20,
           "levels": 15, "momentum": 10, "flows": 10}
N_PICKS = 5


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
    cur.execute("SELECT code, name FROM krx_stocks")
    names = dict(cur.fetchall())
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


def pick(day: str, n: int = N_PICKS, refresh_character: bool = False) -> dict[str, Any]:
    """TODAY's ranking. Character (year) x Condition (recent days), checklist weights."""
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
            "liquidity": 0.6 * _pct(A["turnover"], a["turnover"]) +
                         0.4 * _pct(A["surge_rate"], a["surge_rate"]),
            # 48 - is one tick cheap enough to profit?
            "flexibility": _pct(A["tick_pct"], a["tick_pct"], hi=False),
            # 50,51,52,58 - is it trending and aligned right now?
            "trend": (0.35 * (b["aligned"] / 2 * 100) +
                      0.25 * _pct(A["trendiness"], a["trendiness"]) +
                      0.20 * (b["new_high"] * 100) +
                      0.20 * _pct(B["above_s20"], b["above_s20"])),
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
        rows.append({"code": c, "name": a["name"], "score": round(score, 1),
                     "groups": {k: round(v) for k, v in g.items()},
                     "tick_pct": round(a["tick_pct"], 3), "rsi": round(b["rsi"]),
                     "aligned": b["aligned"], "new_high": b["new_high"],
                     "foreign3": b["foreign3"], "why": why})
    rows.sort(key=lambda r: -r["score"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"ok": True, "day": day, "picks": [r["code"] for r in rows[:n]],
            "weights": WEIGHTS, "rows": rows}


def save_picks(day: str, n: int = N_PICKS) -> dict[str, Any]:
    res = pick(day, n)
    if res.get("ok"):
        _DATA.mkdir(exist_ok=True)
        _PICK_FILE.write_text(json.dumps(
            {"day": day, "picks": [[r["code"], r["name"]] for r in res["rows"][:n]],
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
