"""📡 kiwoom_rules — the SAME twelve rules, on the real Kiwoom tape.

The rule engine is not reimplemented here. `proof_lab.run_variant` is called with a
different FILL MODEL, so "3 consecutive rises" means exactly one thing across both
markets. Copying the engine would have been quicker and would eventually have produced
two subtly different definitions — which is the bug that cost a day when the Strategy Lab
briefly kept its own counters.

WHAT IS DIFFERENT, AND WHY IT MATTERS

The artificial market has a synthetic order book, so a fill can be priced from it. The
real market has a real book, but only RIGHT NOW — nobody recorded the spread at 09:14:22,
and it cannot be recovered. So a fill on the real tape is modelled, and the model is
stated plainly rather than hidden:

    BUY  pays close + one KRX tick   (you lift the ask)
    SELL receives close              (you hit the bid)

That charges exactly one tick per round trip, plus the 0.23% fee. It is the honest
conservative reading: on a real account you cross the spread, and one tick is the
tightest a KRX spread can be. If anything it FLATTERS the result on a wide-spread stock,
which is worth remembering before believing a win rate from here.

NO ML HERE. The boss asked for the twelve plain rules on real data first, and that is
the right order: a model belongs on data whose behaviour is understood, not before.
"""
from __future__ import annotations

from typing import Any

from services.kiwoom_tape import WATCH, bars_ticks, bars_time, load
from services.proof_lab import FEE_PCT, VARIANTS, label, run_desk, run_variant
from services.proof_sim import _tick as krx_tick


def _fill(_seed_i: int, px: float, side: str, tk: int) -> dict[str, Any]:
    """One tick of spread, charged to the taker — see the module note.

    Deliberately deterministic: no coin flip, no randomness. On the artificial side the
    book decides where the last print sat; here there is nothing to decide from, so the
    cost is stated as a constant instead of invented.
    """
    ask = px + tk
    bid = px
    return {"asks": [[ask, 0]], "bids": [[bid, 0]],
            "best_ask": ask, "best_bid": bid,
            "fill": ask if side == "BUY" else bid,
            "last": px, "spread": tk,
            "slip": tk if side == "BUY" else 0}


def _hole_bars(code: str, tick: int, period: int) -> set[int]:
    """Bar indices where the tape has a HOLE immediately before them.

    A hole is time the collector was down. The bars either side are stitched together as
    if consecutive, so a position open across one has an unobserved price path: a stop
    that should have fired during the gap did not, and the trade survived to exit later.
    Those trades are real but not judgeable, and this is how the desk can say so instead
    of quietly counting them (boss 2026-08-05: "what is the solution?").
    """
    ticks = load(code)
    if not ticks:
        return set()

    def sec(x):
        t = x["ts"]
        return int(t[8:10]) * 3600 + int(t[10:12]) * 60 + int(t[12:14])

    out: set[int] = set()
    for i in range(1, len(ticks)):
        if sec(ticks[i]) - sec(ticks[i - 1]) >= 60:
            # which bar does tick i land in? tick bars are fixed-size groups; time bars
            # are found by the second, so ask the aggregator rather than assume
            out.add(i // max(1, tick) if not period else -1)
    if period:
        cs = _bars_for(code, tick, period)
        out = set()
        for j in range(1, len(cs)):
            # a time bar whose clock jumps more than its own width has a hole before it
            def s2(h):
                p = h.split(":")
                return int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2] if len(p) > 2 else 0)
            if s2(cs[j]["hhmm"]) - s2(cs[j - 1]["hhmm"]) >= max(60, period * 2):
                out.add(j)
    return out


_FINISHED_BARS: dict = {}


def _bars_for(code: str, tick: int, period: int, day: str = "",
              frm: str = "", to: str = "") -> list[dict]:
    """Bars for one stock — today's live tape by default, or any STORED day, optionally
    cut to an hour window. The boss lost sight of yesterday twice at dawn because the
    desk only ever read yesterday's (empty) file (2026-08-06): now any collected day is
    one click away, and an hour of it can be isolated.

    The window cuts TICKS, not bars, so a 5틱 bar never straddles the boundary — the
    first bar of the window is built purely from executions inside it.

    A FINISHED day's bars are cached: its file never changes, and without the cache the
    cumulative view re-read and re-parsed three 16MB tape files on every 3-second poll
    (boss 2026-08-06: "switching to all days is very slow").
    """
    from services.kiwoom_tape import _day as _kd
    finished = bool(day) and day < _kd()
    key = (code, tick, period, day, frm, to)
    if finished and key in _FINISHED_BARS:
        return _FINISHED_BARS[key]
    ticks = load(code, day or None)
    if not ticks:
        return []
    if frm or to:
        f = (frm or "00:00").replace(":", "")[:4].ljust(6, "0")
        t2 = (to or "23:59").replace(":", "")[:4].ljust(6, "9")
        ticks = [x for x in ticks if f <= x["ts"][8:14] <= t2]
        if not ticks:
            return []
    out = bars_time(ticks, period) if period else bars_ticks(ticks, max(1, tick))
    if finished:
        _FINISHED_BARS[key] = out
    return out


def stored_days(code: str = "005930") -> list[str]:
    """Every day the collector has a file for, oldest first."""
    import re as _re
    from services.kiwoom_tape import ROOT
    return sorted({m.group(1) for p in ROOT.glob(f"{code}_*.jsonl")
                   if (m := _re.match(rf"{code}_(\d{{8}})\.jsonl$", p.name))})


# THE TWELVE. Exactly the rules the boss has been testing since the start — entries on a
# run of RISES. The six reversal rules (buy after FALLS) live on the artificial side only:
# they were mine, they turned this desk into a different experiment from yesterday's, and
# he asked twice for them gone rather than filtered (2026-08-04). They are not hidden here,
# they are not ranked here, and nothing on this desk computes them.
#
# They still exist in the Strategy Lab, where they were introduced and where the
# comparison they belong to is being run.
# THE TAKE-PROFIT EXPERIMENT, carried over from the Strategy Lab (boss 2026-08-05).
# These four buy after FALLS, so the dir > 0 filter below would drop them - they are named
# explicitly instead. He had me remove the six reversal rules from this desk because two
# experiments in one table was confusing; these four are a DIFFERENT experiment he asked
# for by name, testing whether a bigger profit target beats the fee.
# The 1분 group joined 2026-08-05: 2d+1.0 (the first setting positive on real data,
# on the 1분 clock), 3d3u (the only rule ever positive on the artificial side — no
# profit cap, exits on the market's own signal), and 2d+0.5 as the tight-target control
# so the comparison "same entry, wider exit" is visible on one screen.
# ALL down-entry rules removed at the boss's instruction (2026-08-05) - the desk buys
# only after RISES now. The tuple stays as the one place to re-admit an experiment.
EXPERIMENT: tuple[str, ...] = ()

# THE SIMPLE UP/DOWNS ARE OFF THIS DESK (boss 2026-08-06 evening: "waiting until
# 4 down is not good... remove simple up/downs and remain others like up/some %").
# A pure candle rule waits for N falls with no % anywhere; every rule that stays has a
# % in its exit - his six 2% hybrids (falls = the stop, 2% = the take) and the six
# %-target rules. Their ML twins go with them: the exit is what he rejected. Removed,
# not filtered, as always - and re-admitting is deleting one condition.
_PURE_CANDLE = {"3u3d", "2u2d", "3u2d", "2u3d", "3u4d", "4u3d"}
# LIMIT ONLY (boss 2026-08-10: "just keep limit based and remove old version"). Every
# rule on the desk now offers its price, never pays above its cap, and sells no lower
# than its floor. The market-order variants remain in VARIANTS for stored-day lookups.
PLAIN = [v for v in VARIANTS if v.get("exec") == "limit" and not v.get("ml")]

# ── ML ON THE REAL DESK (boss 2026-08-06, before the open) ─────────────────────────
# The same six "+ ML" rules the Strategy Lab runs, now trading the real tape in parallel
# with their plain twins - so "with ML" and "without ML" sit side by side on one board.
#
# THE ONE HONEST DIFFERENCE FROM THE LAB: these models train ONLY on prior days' stored
# real tape (2026-08-04, 08-05 and whatever accumulates), never on the day being traded.
# Same features, same trainer, same labels as the lab (proof_ml) - only the tape is real.
# RETIRED FROM THE LIVE DESK (boss 2026-08-13: "our app is heavy, remove ML -
# if we need later we can recreate"; re-confirmed 2026-08-19: "you still
# implementing machine learning?"). The two ML twins trained their models for
# minutes inside every /live/warm and replayed on every poll, while the boss's
# algorithms use none of it. The rules stay in VARIANTS and the trainer stays
# in services/proof_ml.py - re-admitting is restoring one line, see
# REMOVED_FEATURES.md for the standing recreation promise.
ML_RULES: list = []
DESK = PLAIN

_KML_CACHE: dict = {}
_CTX_CACHE: dict = {}


_NEWS_T9: dict = {"mtime": 0.0, "by_code": {}}


def _news_times(code: str) -> list:
    """Today's 호재 stamp times for one stock, 'HH:MM:SS' sorted (mtime-cached
    parse of the intern's log). Feeds the gap guard's BIG-NEWS exception (boss
    2026-08-27 night: 'if we have big news during market time we can join')."""
    from pathlib import Path
    from services.kiwoom_tape import _day as _kd
    f = (Path(__file__).resolve().parent.parent / "data" / "news_intern"
         / f"{_kd()}.jsonl")
    try:
        mt = f.stat().st_mtime
        if mt != _NEWS_T9["mtime"]:
            import json as _j
            by: dict = {}
            for ln in f.read_text(encoding="utf-8").splitlines():
                try:
                    r = _j.loads(ln)
                except Exception:
                    continue
                if r.get("stamp") != "호재":
                    continue
                ts = str(r.get("ts") or "")
                hh = ts[11:19] if len(ts) >= 19 else ""
                if hh >= "09:00:00":
                    # WHICH OUTLET (boss 2026-08-27: "we should search
                    # different sources - one source can continuously publish
                    # one-sided news"): the outlet rides the title's tail
                    # ("... - 주간동아"); the aggregator name is the fallback
                    _ttl = str(r.get("title") or "")
                    _out = (_ttl.rsplit(" - ", 1)[-1].strip()
                            if " - " in _ttl else str(r.get("src") or "?"))
                    by.setdefault(r.get("code"), []).append([hh, _out])
            for k in by:
                by[k].sort(key=lambda x: x[0])
            _NEWS_T9["mtime"] = mt
            _NEWS_T9["by_code"] = by
        return _NEWS_T9["by_code"].get(code, [])
    except Exception:
        return []


def _gate_ok(code: str, day: str) -> bool:
    """Was this stock cleared to trade on this day? (services/daily_gate)"""
    try:
        from services.daily_gate import gate
        return bool(gate(code, day).get("go", True))
    except Exception:
        return True                      # fail open - never stop the desk on an error


_US_MODE_CACHE: list = [0.0, "calm"]


def _us_mode_today() -> str:
    """This morning's American night, classified for the storm habit (SOX = the
    chip index that predicts SK하이닉스/삼성전자 mornings, corr 0.64 over 250
    days). Goes through overnight.fetch(), which refetches when its file belongs
    to a PAST day - found in the 08-12 pre-flight audit: reading the file raw
    would have traded tomorrow on yesterday's American night if nobody opened
    the 🌙 strip before the bell. 10-minute TTL keeps the hot path fast."""
    import time as _t
    if _t.time() - _US_MODE_CACHE[0] < 600.0:
        return _US_MODE_CACHE[1]
    mode = "calm"
    try:
        from services.overnight import fetch as _ofetch
        d = _ofetch()
        chg = next((r.get("chg_pct") for r in d.get("rows", [])
                    if r.get("sym") == "^SOX"), None)
        if chg is not None:
            mode = ("storm_down" if chg <= -1.5 else
                    "storm_up" if chg >= 1.5 else "calm")
    except Exception:
        mode = "calm"                    # no data -> no habit, never block the desk
    _US_MODE_CACHE[0] = _t.time()
    _US_MODE_CACHE[1] = mode
    return mode


_D20_CACHE: dict = {}


_VOL5_CACHE: dict = {}


_WK_CACHE: dict = {}


_HZ_CACHE: dict = {}
HORIZONS = {"w": 5, "m": 20, "q": 60, "h": 120}      # week, month, 3 months, 6 months


def _hz_stats(code: str, day: str) -> dict:
    """Low / high / average of the CLOSES over each horizon before `day`
    (boss 2026-09-04: "we have 6 month, 3 month, 1 month and 1 week for
    choosing position - should we make an equation?"). One query, four windows.

    Returned as {'w_low','w_avg','w_hi', 'm_low',... } so any ruler can be
    tested without another database trip."""
    key = (code, day)
    hit = _HZ_CACHE.get(key)
    if hit is not None:
        return hit
    out: dict = {}
    try:
        from services.daily_pick import _conn
        cn = _conn(); cu = cn.cursor()
        cu.execute("""SELECT close FROM raw_daily_prices
                      WHERE ticker = %s AND date < %s AND close IS NOT NULL
                      ORDER BY date DESC LIMIT 120""",
                   (code, f"{day[:4]}-{day[4:6]}-{day[6:8]}"))
        cl = [float(r[0]) for r in cu.fetchall()]
        cn.close()
        for k, n in HORIZONS.items():
            w = cl[:n]
            if len(w) >= max(3, n // 4):
                out[k + "_low"] = min(w)
                out[k + "_hi"] = max(w)
                out[k + "_avg"] = sum(w) / len(w)
    except Exception:
        pass
    if not out:
        # THE OFFICIAL RECORD FILLS THE HOLE (boss 2026-09-07: 삼성중공업 read
        # "all gates passed" because raw_daily_prices had no rows for it, so
        # the position gate silently passed on missing data — while Kiwoom's
        # own numbers said BLOCKED). Naver's daily history covers every
        # listed code; today's row is excluded to keep the date<day meaning.
        try:
            from services.naver_stock import daily_history
            rows = daily_history(code, days=130) or []
            d_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
            cl = [float(r["close"]) for r in rows
                  if r.get("close") and str(r.get("date"))[:10] < d_iso]
            for k, n in HORIZONS.items():
                w = cl[:n]
                if len(w) >= max(3, n // 4):
                    out[k + "_low"] = min(w)
                    out[k + "_hi"] = max(w)
                    out[k + "_avg"] = sum(w) / len(w)
        except Exception:
            pass
    _HZ_CACHE[key] = out
    return out


_CL120_CACHE: dict = {}


def closes120(code: str, day: str) -> list[float]:
    """EVERY daily close before `day`, newest first (up to 120 sessions).

    The position gate's range formula reads only two of these - the lowest
    and the highest - so the boss asked to see the rest (2026-09-07: "a
    6-month window holds ~120 days but only 2 matter; show that we are not
    caring only 3 numbers"). This is the whole set, so the screen can count
    them and draw them."""
    key = (str(code), str(day))
    hit = _CL120_CACHE.get(key)
    if hit is not None:
        return hit
    cl: list[float] = []
    try:
        from services.daily_pick import _conn
        cn = _conn(); cu = cn.cursor()
        cu.execute("""SELECT close FROM raw_daily_prices
                      WHERE ticker = %s AND date < %s AND close IS NOT NULL
                      ORDER BY date DESC LIMIT 120""",
                   (code, f"{day[:4]}-{day[4:6]}-{day[6:8]}"))
        cl = [float(r[0]) for r in cu.fetchall()]
        cn.close()
    except Exception:
        cl = []
    if not cl:
        try:
            from services.naver_stock import daily_history
            d_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
            cl = [float(r["close"]) for r in (daily_history(code, days=130) or [])
                  if r.get("close") and str(r.get("date"))[:10] < d_iso][:120]
        except Exception:
            cl = []
    _CL120_CACHE[key] = cl
    return cl


POS_GATE_EXEMPT = ("000660", "005930")   # boss 2026-09-09


# ── 예외 2종목의 말투: 갭상승 하나로 설명한다 (boss 2026-09-09) ───────────────
# "why not buying 이면 갭상승이 있다고 하고, buying 이면 갭상승이 없다(또는
#  갭상승 몇 %로 열렸지만 어제 가격까지 내려왔다)고 설명하고, 매도 쪽은
#  SK하이닉스·삼성전자는 많이 올라서 -1% 하락은 큰 하락이 아니라고 설명해줘."
# Gate 2 no longer refuses these two, so the ONLY thing that can still refuse
# them is the gap - and that is exactly what the words must say, on all three
# surfaces, from ONE place so they can never drift apart.

def exempt_gap(code: str, day: str = "") -> dict | None:
    """Today's gap story for SK하이닉스 / 삼성전자, with real numbers.

    Returns yesterday's close, today's open, the gap %, whether the price ever
    came back to yesterday's line (and at what minute), plus the two sentences
    the desk speaks: `buy_ko/buy_en` (why we COULD buy) and `no_ko/no_en` (why
    we are NOT buying). None for any other stock, or when the tape is missing.
    """
    code = str(code or "")
    if code not in POS_GATE_EXEMPT:
        return None
    if not day:
        from services.kiwoom_tape import _day as _kd9x
        day = _kd9x()
    nm = "SK하이닉스" if code == "000660" else "삼성전자"
    nm_e = "SK hynix" if code == "000660" else "Samsung Electronics"
    try:
        yc = float(_gap_ref(code, day) or 0) or None
    except Exception:
        yc = None
    op = px = None
    back_at = None
    try:
        from routers.paper_desk import live_tape
        bars = (live_tape(code=code, period=60, tick=5, bars=400) or {}).get("bars") or []
        if bars:
            op = float(bars[0].get("open") or 0) or None
            px = float(bars[-1].get("close") or 0) or None
            if yc:
                for b in bars:
                    if float(b.get("low") or 1e18) <= yc * 1.0015:
                        back_at = str(b.get("hhmm") or "")[:5]
                        break
    except Exception:
        pass
    if px is None:
        try:
            from services.paper_desk import fast_price
            px = float((fast_price(code) or [None])[0] or 0) or None
        except Exception:
            pass
    if not (yc and op):
        return None
    gap = (op / yc - 1) * 100
    now = ((px / yc - 1) * 100) if px else None
    W = lambda v: f"₩{v:,.0f}" if v else "?"
    gapped = gap >= 0.3
    back = bool(back_at) or (now is not None and now <= 0.15)

    if not gapped:
        buy_ko = (f"🟢 갭상승이 없습니다 — 오늘 시가 {W(op)}, 어제 종가 {W(yc)} 대비 "
                  f"{gap:+.2f}%입니다. {nm}는 갭상승만 없으면 위치로는 막지 않습니다 — "
                  f"하락이 멈추고 빨간 봉 3개가 뜨는 순간 삽니다.")
        buy_en = (f"🟢 There is NO gap-up — it opened {W(op)}, {gap:+.2f}% against "
                  f"yesterday's close {W(yc)}. With no gap-up, {nm_e} is never refused "
                  f"on position — we buy the moment the fall stops and three red "
                  f"candles appear.")
    elif back:
        _when = f"{back_at}에 " if back_at else ""
        _when_e = f" at {back_at}" if back_at else ""
        buy_ko = (f"🟢 오늘 시장은 갭상승 {gap:+.2f}%로 열렸지만(시가 {W(op)}, 어제 종가 "
                  f"{W(yc)}) {_when}어제 가격까지 다시 내려왔습니다"
                  + (f" — 지금 {W(px)} ({now:+.2f}%)." if px and now is not None else ".")
                  + f" 비싸게 출발한 값을 쫓은 것이 아니라 어제 가격으로 돌아온 뒤에 "
                    f"빨간 봉 3개를 보고 샀습니다.")
        buy_en = (f"🟢 The market opened with a gap-up of {gap:+.2f}% (open {W(op)} vs "
                  f"yesterday's close {W(yc)}), but the price came back DOWN to "
                  f"yesterday's price{_when_e}"
                  + (f" — now {W(px)} ({now:+.2f}%)." if px and now is not None else ".")
                  + f" We did not chase the expensive open: we bought after it came "
                    f"back, on the three red candles.")
    else:
        buy_ko = buy_en = ""

    if gapped and not back:
        no_ko = (f"🚫 갭상승이 있습니다 — 오늘 시가 {W(op)}, 어제 종가 {W(yc)}보다 "
                 f"{gap:+.2f}% 높습니다."
                 + (f" 지금도 {W(px)} ({now:+.2f}%) — 아직 어제 가격 위에 있습니다."
                    if px and now is not None else "")
                 + f" {nm}는 위치 관문에서는 막지 않습니다 — 오늘 사지 않는 이유는 "
                   f"오직 이 갭상승 하나입니다. 어제 가격 {W(yc)}까지 내려오면 그때 "
                   f"빨간 봉 3개를 보고 삽니다.")
        no_en = (f"🚫 There IS a gap-up — it opened {W(op)}, {gap:+.2f}% above "
                 f"yesterday's close {W(yc)}"
                 + (f", and it is still {W(px)} ({now:+.2f}%) above that line."
                    if px and now is not None else ".")
                 + f" {nm_e} is not refused on position any more — this gap-up is the "
                   f"ONE and only reason it is not bought today. Once it comes back "
                   f"down to yesterday's price {W(yc)}, we buy it on the three red "
                   f"candles.")
    else:
        no_ko = no_en = ""

    return {"code": code, "name": nm, "name_en": nm_e, "yc": yc, "op": op,
            "px": px, "gap": round(gap, 2),
            "now_vs_yc": round(now, 2) if now is not None else None,
            "back": back, "back_at": back_at, "gapped": gapped,
            "buy_ko": buy_ko, "buy_en": buy_en, "no_ko": no_ko, "no_en": no_en}


def exempt_turn(code: str, day: str = "", upto: str = "") -> dict | None:
    """THE ENTRY, TOLD THE WAY HE TELLS IT (boss 2026-09-09: "there is not a
    kepsangsing, then it should say at this time stopped decrease and started
    increase and on the 3rd bought like this").

    For SK하이닉스 / 삼성전자 the position gate never refuses, so the buy rests
    on exactly two facts: no 갭상승, and the shape - it fell, it stopped, it
    rose three times. This finds those minutes on the tape and says them with
    their prices. `upto` replays any past minute. Returns the 'we bought'
    wording and the 'not yet' wording; None for any other stock.
    """
    code = str(code or "")
    if code not in POS_GATE_EXEMPT:
        return None
    if not day:
        from services.kiwoom_tape import _day as _kd7
        day = _kd7()
    try:
        from routers.paper_desk import live_tape
        bars = (live_tape(code=code, period=60, tick=5, bars=400) or {}).get("bars") or []
    except Exception:
        bars = []
    if upto:
        bars = [b for b in bars if str(b.get("hhmm") or "")[:5] <= upto]
    if len(bars) < 4:
        return None
    w = bars[-30:]
    # the low of the dip, and the minute it stopped falling
    lows = [float(b.get("close") or 0) for b in w]
    ti = lows.index(min(x for x in lows if x)) if any(lows) else 0
    stop_at = str(w[ti].get("hhmm") or "")[:5]
    stop_px = float(w[ti].get("close") or 0)
    # what it fell FROM, before that low
    from_px = max([float(b.get("close") or 0) for b in w[:ti + 1]] or [stop_px])
    fell = ((from_px - stop_px) / from_px * 100) if from_px else 0.0
    # then count the rises, forgiving one small blue but not a slide
    ups, third_at, third_px, peak = 0, None, None, stop_px
    rise_times = []
    for b in w[ti + 1:]:
        c = float(b.get("close") or 0)
        t = str(b.get("hhmm") or "")[:5]
        if c > peak or (rise_times and c > float(rise_times[-1][1])):
            pass
        if c > (float(rise_times[-1][1]) if rise_times else stop_px):
            ups += 1
            rise_times.append((t, c))
            if ups == 3 and third_at is None:
                third_at, third_px = t, c
        if c > peak:
            peak = c
        if peak and (peak - c) / peak * 100 > 0.2:
            ups, third_at, third_px, peak, rise_times = 0, None, None, c, []
    px_now = float(w[-1].get("close") or 0)
    W = lambda v: f"₩{v:,.0f}" if v else "?"
    nm = "SK하이닉스" if code == "000660" else "삼성전자"
    nm_e = "SK hynix" if code == "000660" else "Samsung Electronics"
    seq_k = " · ".join(f"{t} {W(c)}" for t, c in rise_times[:3])
    seq_e = " · ".join(f"{t} {W(c)}" for t, c in rise_times[:3])

    if third_at:
        ko = (f"📉→📈 {stop_at}에 {W(stop_px)}까지 내려온 뒤 하락이 멈췄고"
              + (f" (그 전 {W(from_px)}에서 -{fell:.2f}%)" if fell >= 0.05 else "")
              + f", 이어서 세 번 올랐습니다 — {seq_k}. "
                f"3번째 상승이 선 {third_at} {W(third_px)}이 매수 자리입니다. "
                f"작은 음봉 하나는 무시하지만 고점에서 0.2% 넘게 밀리면 신호는 사라집니다.")
        en = (f"📉→📈 It fell to {W(stop_px)} at {stop_at}"
              + (f" (-{fell:.2f}% from {W(from_px)})" if fell >= 0.05 else "")
              + f", the fall STOPPED there, and then it rose three times — {seq_e}. "
                f"The 3rd rise stood at {third_at} {W(third_px)} — that is the buy. "
                f"One small blue candle is forgiven, but a slide of more than 0.2% "
                f"off the high cancels the signal.")
    else:
        ko = (f"⏳ 아직 매수 자리가 아닙니다 — {stop_at}에 {W(stop_px)}까지 내려왔지만, "
              f"하락이 멈춘 뒤 세 번 오르는 신호가 아직 서지 않았습니다 "
              f"(지금 {ups}번, 현재 {W(px_now)}). {nm}는 이 신호가 서야 삽니다.")
        en = (f"⏳ Not the buy yet — it came down to {W(stop_px)} at {stop_at}, but the "
              f"three rises after the fall have not stood up yet "
              f"({ups} so far, now {W(px_now)}). {nm_e} is bought only when that "
              f"signal stands.")
    return {"ok": bool(third_at), "stop_at": stop_at, "stop_px": stop_px,
            "from_px": from_px, "fell": round(fell, 2), "third_at": third_at,
            "third_px": third_px, "ups": ups, "px": px_now, "ko": ko, "en": en}


def exempt_hold_line(code: str, px: float, basis: float = 0.0,
                     day: str = "") -> tuple:
    """WHY -1% DOES NOT SELL THESE TWO (boss 2026-09-09: "for selling case need
    to explain skhynix and samsungchonja increased a lot so there is a -1%
    decrease, not big decrease"). Says it with the size of the rise behind it,
    so the sentence carries a measurement and not just an opinion."""
    code = str(code or "")
    if code not in POS_GATE_EXEMPT:
        return ("", "")
    if not day:
        from services.kiwoom_tape import _day as _kd9x
        day = _kd9x()
    nm = "SK하이닉스" if code == "000660" else "삼성전자"
    nm_e = "SK hynix" if code == "000660" else "Samsung Electronics"
    rise_ko = rise_en = ""
    try:
        hz = _hz_stats(code, day) or {}
        lo = hz.get("m_low") or hz.get("q_low")
        lo_n = "1개월 저점" if hz.get("m_low") else "3개월 저점"
        lo_e = "1-month low" if hz.get("m_low") else "3-month low"
        if px and lo and lo > 0:
            up = (float(px) / float(lo) - 1) * 100
            if up >= 3:
                rise_ko = (f"{lo_n} ₩{lo:,.0f} → 지금 ₩{px:,.0f}, {up:+.1f}% 올랐습니다. ")
                rise_en = (f"{lo_e} ₩{lo:,.0f} → now ₩{px:,.0f}, a rise of {up:+.1f}%. ")
    except Exception:
        pass
    # the won value of 1% - from OUR buy price when we know it, otherwise from
    # today's price, so the sentence always carries a number and never "(1%)"
    _ref = float(basis or 0) or float(px or 0)
    won = f"약 ₩{_ref * 0.01:,.0f}" if _ref else "1%"
    won_e = f"about ₩{_ref * 0.01:,.0f}" if _ref else "1%"
    ko = (f"🤝 -1%로는 팔지 않습니다 — {nm}는 많이 오른 종목입니다. {rise_ko}"
          f"그만큼 오른 값에서 -1%({won})는 큰 하락이 아니라 작은 흔들림입니다. "
          f"이 두 종목은 -1% 매도 규칙에서 제외입니다 — 사장님이 직접 파실 때까지 "
          f"보유합니다.")
    en = (f"🤝 NOT sold at -1% — {nm_e} has risen a lot. {rise_en}"
          f"Against a rise that size, -1% ({won_e}) is a small wobble, not a big "
          f"fall. These two are exempt from the -1% sell rule - held until you "
          f"sell them yourself.")
    return (ko, en)


def whole_read(code: str, px: float, day: str) -> float | None:
    """The all-days position: what share of the last 120 daily closes were
    CHEAPER than `px`, with recent days carrying more weight (half-life 20
    sessions). Every day counted once - no window edges, no double counting."""
    cl = closes120(code, day)
    if not cl or not px:
        return None
    wsum = bsum = 0.0
    for i, x in enumerate(cl):
        wt = 0.5 ** (i / 20.0)
        wsum += wt
        if x < float(px):
            bsum += wt
    return (bsum / wsum * 100) if wsum else None


def pos_score(code: str, px: float, day: str) -> float | None:
    """GATE 2's NUMBER (boss 2026-09-07 evening: "gate 2 should only care
    about position - if it is TOP do not buy, otherwise buy - and find this
    % by analysing ALL information, not only min, max and price").

    One score from both readings: the RANGE read (where the price sits
    between each window's low and high, four windows averaged) blended with
    the ALL-DAYS read (how many of the last 120 closes were cheaper,
    recency-weighted). Neither alone; both, averaged."""
    hz = _hz_stats(code, day) or {}
    ps = []
    for h in ("w", "m", "q", "h"):
        lo, hi = hz.get(h + "_low"), hz.get(h + "_hi")
        if lo and hi and hi > lo and px:
            ps.append(max(0.0, min(100.0, (float(px) - lo) / (hi - lo) * 100)))
    rng = (sum(ps) / len(ps)) if ps else None
    wh = whole_read(code, px, day)
    vals = [x for x in (rng, wh) if x is not None]
    return (sum(vals) / len(vals)) if vals else None


def pos_story(code: str, px: float, day: str, bar: float = 65.0,
              context: str = "buy") -> dict | None:
    """THE POSITION FORMULA, TOLD SO A PERSON CAN FOLLOW IT (boss 2026-09-07:
    "we created the position formula last Friday but it is not easily
    understandable — extend it and make it understandable, and implement it
    to all other cases, buying and holding also"). One storyteller for every
    surface — the whynot gate, the buy reasons, the holding reasons, the
    chatbot — so the same numbers always wear the same words.

    Returns {'ko','en','blend','ok','parts'} or None without data. `context`:
    'buy' ends with the buy/no-buy verdict; 'hold' ends with the holding
    reading (the -1% rule still decides the sell)."""
    hz = _hz_stats(code, day) or {}
    if not px:
        return None
    parts = []
    # THE ALL-DAYS PROOF LEADS (boss 2026-09-07: "you again started with the
    # formula — if people are impatient they do not read our explanation that
    # we are not using only min/max price"). The impatient reader must meet
    # the 120-day analysis in the FIRST lines; the min/max arithmetic the
    # rule uses follows underneath as the detail.
    ko_l: list[str] = []
    en_l: list[str] = []
    rng_ko = ["📐 두 번째 읽기 (최저~최고 방식) — 관문 2의 점수는 위 전체 분석과 이 계산을 "
              "평균낸 값입니다. 각 %의 계산법: "
              "(지금 가격 − 그 기간의 최저가) ÷ (그 기간의 최고가 − 그 기간의 최저가) × 100 "
              "— 최저가·최고가는 모두 같은 기간 안의 값입니다."]
    rng_en = ["📐 The second reading (range method) — gate 2's score is this AVERAGED with the "
              "whole read above. How each % is computed: (price now − that window's LOW) ÷ (that window's HIGH "
              "− that window's LOW) × 100 — the high and the low both come from the same window."]
    for k, nk, ne in (("w", "1주일", "1 week"), ("m", "1개월", "1 month"),
                      ("q", "3개월", "3 months"), ("h", "6개월", "6 months")):
        lo, hi = hz.get(k + "_low"), hz.get(k + "_hi")
        if lo and hi and hi > lo:
            v = max(0.0, min(100.0, (float(px) - lo) / (hi - lo) * 100))
            parts.append(v)
            tag_k = "싼 자리" if v < 35 else "중간" if v < 65 else "비싼 자리"
            tag_e = "cheap" if v < 35 else "middle" if v < 65 else "expensive"
            if k == "w":
                rng_ko.append(f"· {nk}: 최저 ₩{lo:,.0f} ~ 최고 ₩{hi:,.0f} → 계산: "
                              f"(₩{px:,.0f} − ₩{lo:,.0f}) ÷ (₩{hi:,.0f} − ₩{lo:,.0f}) × 100 "
                              f"= ₩{px - lo:,.0f} ÷ ₩{hi - lo:,.0f} × 100 = {v:.0f}% ({tag_k})")
                rng_en.append(f"· {ne}: low ₩{lo:,.0f} ~ high ₩{hi:,.0f} → worked out: "
                              f"(₩{px:,.0f} − ₩{lo:,.0f}) ÷ (₩{hi:,.0f} − ₩{lo:,.0f}) × 100 "
                              f"= ₩{px - lo:,.0f} ÷ ₩{hi - lo:,.0f} × 100 = {v:.0f}% ({tag_e})")
            else:
                rng_ko.append(f"· {nk}: 최저 ₩{lo:,.0f} ~ 최고 ₩{hi:,.0f} → 같은 계산으로 "
                              f"지금 ₩{px:,.0f}는 {v:.0f}% 지점 ({tag_k})")
                rng_en.append(f"· {ne}: low ₩{lo:,.0f} ~ high ₩{hi:,.0f} → same formula: "
                              f"now ₩{px:,.0f} sits at {v:.0f}% ({tag_e})")
    if not parts:
        return None
    blend = sum(parts) / len(parts)
    ssum = " + ".join(f"{x:.0f}" for x in parts)
    rng_ko.append(f"네 기간의 평균 = ({ssum}) ÷ {len(parts)} = {blend:.1f}%")
    rng_en.append(f"The average of the four = ({ssum}) ÷ {len(parts)} = {blend:.1f}%")
    # THE GATE'S OWN NUMBER: both readings averaged (boss 2026-09-07 evening)
    _wh0 = whole_read(code, px, day)
    score = ((blend + _wh0) / 2) if _wh0 is not None else blend
    ok = score <= bar
    # HIS TWO NAMES SKIP THIS GATE (boss 2026-09-09: "gate 2 is exceptional
    # for SK하이닉스 and 삼성전자 - if there is no 갭상승, or there is one and
    # it came back to yesterday's price or lower, it should start trading").
    # The score is still SHOWN - he wants to see where they stand - it just
    # no longer refuses the buy. Gate 1, volume, news and the turn still do.
    exempt = str(code) in POS_GATE_EXEMPT and context != "hold"
    if exempt:
        ok = True
    rule_ko: list[str] = []
    rule_en: list[str] = []
    if _wh0 is not None:
        rule_ko.append(f"🧮 관문 2의 점수 = (최저~최고 방식 {blend:.1f}% + 모든 날 방식 "
                       f"{_wh0:.1f}%) ÷ 2 = {score:.1f}%")
        rule_en.append(f"🧮 Gate 2's score = (range method {blend:.1f}% + all-days method "
                       f"{_wh0:.1f}%) ÷ 2 = {score:.1f}%")
    if exempt:
        rule_ko.append(f"⚖ 규칙: 이 종목은 회장님 지정 예외 2종목(SK하이닉스·삼성전자)입니다 — "
                       f"위치 점수 {score:.1f}%는 참고로만 보고, 위치로는 막지 않습니다. "
                       f"갭상승 관문(어제 가격으로 돌아왔는가)·거래량·반등 신호는 "
                       f"그대로 지킵니다.")
        rule_en.append(f"⚖ Rule: this is one of the two EXEMPT names (SK hynix · Samsung "
                       f"Electronics) — its position score {score:.1f}% is shown for "
                       f"information only and never refuses the buy. Gate 1 (the gap and "
                       f"the return to yesterday's price), volume and the turn "
                       f"still apply.")
    elif context == "hold" and str(code) in POS_GATE_EXEMPT:
        # the -1% rule does NOT sell these two, so the holding card must not
        # promise that it will (boss 2026-09-09)
        _hk, _he = exempt_hold_line(code, px, day=day)
        rule_ko.append(f"→ 지금 {score:.1f}% 지점입니다 (낮을수록 싼 자리). " + _hk)
        rule_en.append(f"→ It sits at {score:.1f}% (lower = cheaper). " + _he)
    elif context == "hold":
        rule_ko.append(f"→ 지금 {score:.1f}% 지점입니다 (낮을수록 싼 자리). "
                       f"보유 중의 매도는 이 위치가 아니라 -1% 규칙이 결정합니다.")
        rule_en.append(f"→ It sits at {score:.1f}% (lower = cheaper). While holding, the "
                       f"SELL is decided by the -1% rule, not this position.")
    elif ok:
        rule_ko.append(f"⚖ 규칙: 고점권({bar:.0f}% 초과)만 사지 않습니다 → "
                       f"{score:.1f}% ≤ {bar:.0f}% ✔ 고점이 아니므로 살 수 있습니다.")
        rule_en.append(f"⚖ Rule: we refuse ONLY the top zone (above {bar:.0f}%) → "
                       f"{score:.1f}% ≤ {bar:.0f}% ✔ not the top, so we can buy.")
    else:
        rule_ko.append(f"⚖ 규칙: 고점권({bar:.0f}% 초과)에서는 사지 않습니다 → "
                       f"{score:.1f}% > {bar:.0f}% ✘ 지금은 고점권입니다. "
                       f"{bar:.0f}% 아래로 내려오면 삽니다.")
        rule_en.append(f"⚖ Rule: we do not buy in the TOP zone (above {bar:.0f}%) → "
                       f"{score:.1f}% > {bar:.0f}% ✘ it is in the top zone now. "
                       f"It becomes buyable once it comes under {bar:.0f}%.")
    # 📊 COUNTED AGAINST EVERY DAY (boss 2026-09-07: "show that we are not
    # caring only 3 numbers, and that our work is helping"): the range
    # formula above reads two days per window; this reads them ALL, and the
    # screen draws every one of them under the card.
    dist = None
    try:
        cl = closes120(code, day)
        if cl:
            pxf = float(px)
            n_all = len(cl)
            # ── ONE CONTINUOUS READ, not four chunks averaged (boss
            # 2026-09-07: "now it looks like separated analyses / chunk
            # based - it should be analysed based"). Every day is counted
            # exactly ONCE, weighted by how recent it is (half-life 20
            # sessions), so there are no arbitrary window edges and no
            # double counting of the days that sit in several windows.
            below_all = sum(1 for x in cl if x < pxf)
            plain = below_all / n_all * 100
            wsum = bsum = 0.0
            for i, x in enumerate(cl):            # i = 0 is yesterday
                wt = 0.5 ** (i / 20.0)
                wsum += wt
                if x < pxf:
                    bsum += wt
            weighted = (bsum / wsum * 100) if wsum else plain
            # the SHAPE: how much time this stock actually spent at this
            # price - a shelf it knows, or thin air it passed through
            near = sum(1 for x in cl if abs(x / pxf - 1) <= 0.015)
            near_pct = near / n_all * 100
            # the DIRECTION: where the same measure stood 5 sessions ago
            prev5 = cl[4] if len(cl) > 4 else None
            rank5 = (sum(1 for x in cl if x < prev5) / n_all * 100) if prev5 else None
            wins, ranks = [], []
            for k, n, nk, ne in (("w", 5, "1주일", "1 week"), ("m", 20, "1개월", "1 month"),
                                 ("q", 60, "3개월", "3 months"), ("h", 120, "6개월", "6 months")):
                w9 = cl[:n]
                if len(w9) >= max(3, n // 4):
                    below = sum(1 for x in w9 if x < pxf)
                    pct = below / len(w9) * 100
                    ranks.append(pct)
                    wins.append({"k": k, "ko": nk, "en": ne, "n": len(w9),
                                 "below": below, "pct": round(pct)})
            rank_avg = sum(ranks) / len(ranks) if ranks else plain
            dist = {"closes": cl, "px": pxf, "windows": wins,
                    "rank_avg": round(rank_avg, 1), "plain": round(plain, 1),
                    "weighted": round(weighted, 1), "near": near,
                    "near_pct": round(near_pct), "rank5": (round(rank5, 1) if rank5 else None)}
            ko_l.append(f"📊 전체 분석 — 기간을 토막내지 않고 {n_all}일 전부를 한 번에 봅니다 "
                        f"(각 날짜는 한 번만 세고, 최근일수록 크게 봅니다):")
            en_l.append(f"📊 THE WHOLE READ — all {n_all} days at once, not four separate "
                        f"chunks (each day counted once, recent days weighted more):")
            ko_l.append(f"· 오늘 가격보다 쌌던 날: {n_all}일 중 {below_all}일 = 하위 {plain:.0f}%")
            en_l.append(f"· Days cheaper than today: {below_all} of {n_all} = bottom {plain:.0f}%")
            ko_l.append(f"· 최근 가중(최근 1개월에 절반의 무게): {weighted:.0f}% "
                        f"— 6개월 전보다 지난달의 가격이 더 중요합니다")
            en_l.append(f"· Recency-weighted (half the weight on the last month): {weighted:.0f}% "
                        f"— last month's prices matter more than six-month-old ones")
            ko_l.append(f"· 이 가격대(±1.5%)에서 실제로 보낸 날: {near}일 ({near_pct:.0f}%) — "
                        + ("이 종목이 오래 머물렀던 익숙한 자리입니다."
                           if near_pct >= 8 else "거의 머문 적 없는 드문 자리입니다."))
            en_l.append(f"· Days actually spent at this price (±1.5%): {near} ({near_pct:.0f}%) — "
                        + ("a shelf this stock knows well."
                           if near_pct >= 8 else "a rare place it has hardly ever traded."))
            if rank5 is not None:
                _dir_k = ("점점 싸지고 있습니다" if plain < rank5 - 2 else
                          "점점 비싸지고 있습니다" if plain > rank5 + 2 else "제자리입니다")
                _dir_e = ("it is getting cheaper" if plain < rank5 - 2 else
                          "it is getting more expensive" if plain > rank5 + 2 else "it is flat")
                ko_l.append(f"· 방향: 5거래일 전 이 종목은 하위 {rank5:.0f}% 자리였고 지금 "
                            f"{plain:.0f}%입니다 — {_dir_k}.")
                en_l.append(f"· Direction: 5 sessions ago it stood at bottom {rank5:.0f}%, "
                            f"today {plain:.0f}% — {_dir_e}.")
            # ── HOW BIG IS A MOVE IN THIS STOCK? (boss 2026-09-07: "in the
            # historical data there are different LENGTHS of increase and
            # decrease - Friday's decrease can be smaller than today's move
            # between 12 and 13 - should we implement this?"). A percentage
            # means nothing until you know what a normal day looks like for
            # this particular stock, so the read now carries the yardstick:
            # the typical daily move, how far today sits from the middle
            # measured in THOSE units, and how big today's own move is.
            import statistics as _st9
            chg = [abs(cl[i] / cl[i + 1] - 1) * 100
                   for i in range(min(len(cl) - 1, 60)) if cl[i + 1]]
            typ = _st9.median(chg) if chg else None
            mid = _st9.median(cl)
            if typ and typ > 0:
                steps = (mid - pxf) / (pxf * typ / 100.0)
                today_mv = ((pxf / cl[0] - 1) * 100) if cl and cl[0] else None
                ko_l.append(f"· 이 종목의 '보통 하루' 움직임: ±{typ:.2f}% "
                            f"(최근 60일 중앙값) — 같은 1%도 종목마다 크기가 다릅니다.")
                en_l.append(f"· What a NORMAL day looks like for this stock: ±{typ:.2f}% "
                            f"(median of the last 60 days) — the same 1% means different "
                            f"things on different stocks.")
                _mid_k = ("아래" if steps > 0 else "위")
                _mid_e = ("below" if steps > 0 else "above")
                ko_l.append(f"· 120일 한가운데 가격은 ₩{mid:,.0f}이고, 지금은 그보다 "
                            f"{abs(steps):.1f} '보통 하루'만큼 {_mid_k}입니다 "
                            + ("— 하루 이틀 움직임이면 닿는 거리라 크게 싼 자리는 아닙니다."
                               if abs(steps) < 2 else
                               "— 보통 며칠치 움직임만큼 떨어진, 의미 있는 거리입니다."))
                en_l.append(f"· The middle price of the 120 days is ₩{mid:,.0f}; today sits "
                            f"{abs(steps):.1f} normal days' move {_mid_e} it"
                            + (" — a distance one or two ordinary days can cover, so not "
                               "deeply cheap." if abs(steps) < 2 else
                               " — a real distance, several ordinary days' worth."))
                if today_mv is not None:
                    _rt = abs(today_mv) / typ
                    ko_l.append(f"· 오늘 움직임: {today_mv:+.2f}% = 보통 하루의 {_rt:.1f}배"
                                + (" — 오늘은 평소보다 큰 날입니다." if _rt >= 1.5 else
                                   " — 오늘은 평소보다 조용한 날입니다." if _rt <= 0.6 else
                                   " — 평소와 비슷한 크기입니다."))
                    en_l.append(f"· Today's own move: {today_mv:+.2f}% = {_rt:.1f}× a normal day"
                                + (" — a big day for this stock." if _rt >= 1.5 else
                                   " — a quiet day for this stock." if _rt <= 0.6 else
                                   " — an ordinary-sized day."))
                dist["typ_move"] = round(typ, 2)
                dist["steps_from_mid"] = round(steps, 1)
                dist["mid"] = round(mid)
                dist["today_move"] = (round(today_mv, 2) if today_mv is not None else None)
            if wins:
                # WHY THE WINDOWS STILL EXIST (boss 2026-09-07: "if you read
                # all 6 months at once, why do you need 3-month, 1-month, 1
                # week?"). Fair question, and the answer must be in the
                # product: they are NOT extra data - they are the same 120
                # days cut by time, and they answer a different question.
                # The whole read says HOW cheap; the cuts say WHEN it was
                # cheaper - a stock can be cheap over months and expensive
                # this week (a bounce off a deep fall) or the reverse (fresh
                # weakness from a high place). One number cannot show that.
                _wk = next((x["pct"] for x in wins if x["k"] == "w"), None)
                _hf = next((x["pct"] for x in wins if x["k"] == "h"), None)
                _shape_k = _shape_e = ""
                if _wk is not None and _hf is not None:
                    if _wk - _hf >= 20:
                        _shape_k = (" → 길게 보면 싸지만 최근에는 비쌉니다: 크게 떨어진 뒤 "
                                    "이번 주에 되올라오는 중이라는 뜻입니다.")
                        _shape_e = (" → cheap over months but expensive lately: a deep fall "
                                    "that is bouncing back this week.")
                    elif _hf - _wk >= 20:
                        _shape_k = (" → 길게 보면 비싼 편인데 최근에 급히 싸졌습니다: "
                                    "높은 자리에서 막 무너지는 중일 수 있어 조심합니다.")
                        _shape_e = (" → expensive over months but suddenly cheap lately: it may "
                                    "be breaking down from a high place, so we stay careful.")
                    else:
                        _shape_k = " → 어느 시간대로 봐도 같은 그림입니다 (판정이 흔들리지 않습니다)."
                        _shape_e = (" → the same picture at every time scale, so the verdict "
                                    "does not depend on which window you look at.")
                ko_l.append("· 시간대별 모양 (새로운 자료가 아니라 같은 120일을 시간으로 자른 것 — "
                            "'얼마나 싼가'가 아니라 '언제 더 쌌는가'를 봅니다): "
                            + " · ".join(f"{x['ko']} {x['pct']}%" for x in wins) + _shape_k)
                en_l.append("· The time shape (not new data — the SAME 120 days cut by time; it "
                            "answers WHEN it was cheaper, not how cheap): "
                            + " · ".join(f"{x['en']} {x['pct']}%" for x in wins) + _shape_e)
            _agree = abs(weighted - blend) <= 7.0
            ko_l.append(f"→ 전체 분석 {weighted:.0f}% · 최저~최고 방식 {blend:.1f}% (관문 2는 둘의 평균으로 판정) — "
                        + ("두 방식이 같은 답을 줍니다 (판정 신뢰 ↑)." if _agree else
                           "두 방식이 다릅니다 — 최고·최저가 한두 날의 극단값에 끌려간 자리입니다."))
            en_l.append(f"→ whole-read {weighted:.0f}% vs range method {blend:.1f}% "
                        f"(gate 2 judges on the AVERAGE of the two) — "
                        + ("both agree, so the verdict is solid." if _agree else
                           "they disagree, which means the high/low is being pulled by one or "
                           "two extreme days."))
    except Exception:
        dist = None
    if not ko_l:
        # no all-days data — the range detail becomes the opening
        ko_l.append("📍 위치 — 지금 가격이 각 기간의 최저~최고 사이 어디쯤인지 봅니다 "
                    "(0% = 가장 싼 자리, 100% = 가장 비싼 자리).")
        en_l.append("📍 POSITION — where today's price sits between each window's lowest "
                    "and highest (0% = cheapest, 100% = most expensive).")
    # ORDER (boss 2026-09-07): the all-days proof above, THEN the rule's own
    # range arithmetic, then the verdict, then why the bar is 35.
    ko_l += rng_ko + rule_ko
    en_l += rng_en + rule_en
    if context != "hold" and abs(bar - 65.0) < 0.01:
        # WHY 65 (boss 2026-09-07 evening: "gate 2 is too heavy, it is
        # blocking everything - make it weaker, only refuse the top"): the
        # court over all 24 stored days, 20 stocks, 알고3's book, changing
        # ONLY this ruler. 65 is where the chances stop being free.
        ko_l.append("왜 65%인가 — 저장된 24일·20종목 전체를 다시 돌려 기준선을 쓸어봤습니다: "
                    "옛 기준 35% = 18건 · 승률 44% · 거래당 -0.34% / 45% = 23건 · 52% · -0.18% / "
                    "55% = 33건 · 52% · -0.23% / 60% = 36건 · 53% · -0.22% / "
                    "65% = 42건 · 승률 55% · 거래당 -0.17% ★ / 70% = 46건 · 52% · -0.24% / "
                    "관문 2 없음 = 69건 · 49% · 거래당 -0.29%. 65%에서 기회가 2.3배로 늘고 "
                    "승률이 가장 높으며 거래당 손실이 가장 작습니다. 70%부터는 다시 나빠지고, "
                    "관문을 아예 없애면 크게 나빠집니다 — 그래서 '고점권만 거부'의 경계는 65%입니다.")
        en_l.append("WHY 65% — we re-ran the court over all 24 stored days and 20 stocks, "
                    "changing only this ruler: old bar 35% = 18 trades · 44% win · "
                    "-0.34%/trade / 45% = 23 · 52% · -0.18% / 55% = 33 · 52% · -0.23% / "
                    "60% = 36 · 53% · -0.22% / 65% = 42 trades · 55% win · -0.17%/trade ★ / "
                    "70% = 46 · 52% · -0.24% / NO gate 2 at all = 69 · 49% · -0.29%/trade. "
                    "At 65% the chances multiply 2.3×, the win rate is the best measured and "
                    "the per-trade loss the smallest; above it the numbers turn worse again, "
                    "and removing the gate entirely is far worse — so the top-zone line is 65%.")
    # ── HIS TWO NAMES DO NOT GET THE LECTURE (boss 2026-09-09, reading the
    # SK하이닉스 card: "in case of buying case of skhynix and samsungchonja
    # should be different not like this ... we should check Gate 1, there is
    # not a kepsangsing, then it should say at this time stopped decrease and
    # started increase and on the 3rd bought").
    # Gate 2 cannot refuse these two, so printing the 120-day read, the four
    # windows, the worked arithmetic AND the 24-day court behind the 65% bar
    # buries the two facts that actually decided the trade under a page of
    # numbers that decided nothing. The score still shows - he asked to see
    # where they stand - but as ONE line; gate 1 and the turn carry the story.
    if str(code) in POS_GATE_EXEMPT:
        sk = [f"ℹ️ 위치 점수 {score:.1f}% — 참고용입니다. SK하이닉스·삼성전자는 "
              f"위치로는 막지 않습니다 (기준 {bar:.0f}%를 넘어도 삽니다). 판단은 "
              f"갭상승 · 반등 신호(하락이 멈추고 3번째 상승) · 거래량, "
              f"이 셋이 합니다. 뉴스는 이 두 종목을 막지 않습니다."]
        se = [f"ℹ️ Position score {score:.1f}% — for information only. SK hynix / "
              f"Samsung Electronics are never refused on position (they buy even "
              f"above the {bar:.0f}% bar). Three things decide instead: the gap-up, "
              f"the turn signal (the fall stops, then the 3rd rise) and volume. "
              f"News does not refuse these two."]
        if context == "hold":
            _hk2, _he2 = exempt_hold_line(code, px, day=day)
            sk.append(_hk2)
            se.append(_he2)
        return {"ko": "\n".join(sk), "en": "\n".join(se),
                "blend": round(blend, 1), "ok": ok,
                "parts": [round(p) for p in parts], "dist": dist}
    return {"ko": "\n".join(ko_l), "en": "\n".join(en_l),
            "blend": round(blend, 1), "ok": ok, "parts": [round(p) for p in parts],
            "dist": dist}


def _week_stats(code: str, day: str):
    """(low5, high5, avg5) over the five sessions before `day` — everything a
    'where is it in its week' test could want (boss 2026-09-04: "check with our
    historical data which one is more meaningful and efficient to use")."""
    key = (code, day)
    hit = _WK_CACHE.get(key)
    if hit is not None:
        return hit
    out = (None, None, None)
    try:
        from services.daily_pick import _conn
        cn = _conn(); cu = cn.cursor()
        cu.execute("""SELECT high, low, close FROM raw_daily_prices
                      WHERE ticker = %s AND date < %s AND close IS NOT NULL
                      ORDER BY date DESC LIMIT 5""",
                   (code, f"{day[:4]}-{day[4:6]}-{day[6:8]}"))
        rows = cu.fetchall(); cn.close()
        if len(rows) >= 3:
            cl = [float(r[2]) for r in rows]
            hi = [float(r[0]) for r in rows if r[0]]
            lo = [float(r[1]) for r in rows if r[1]]
            out = (min(cl), max(hi) if hi else max(cl), sum(cl) / len(cl))
    except Exception:
        pass
    _WK_CACHE[key] = out
    return out


def _vol5(code: str, day: str) -> float | None:
    """Average DAILY volume over the five sessions before `day` - the "average
    within the week" gate 3 measures against (boss 2026-09-04)."""
    key = (code, day)
    hit = _VOL5_CACHE.get(key)
    if hit is not None:
        return hit
    out = None
    try:
        from services.daily_pick import _conn
        cn = _conn(); cu = cn.cursor()
        cu.execute("""SELECT volume FROM raw_daily_prices
                      WHERE ticker = %s AND date < %s AND volume IS NOT NULL
                      ORDER BY date DESC LIMIT 5""",
                   (code, f"{day[:4]}-{day[4:6]}-{day[6:8]}"))
        vs = [float(r[0]) for r in cu.fetchall() if r[0]]
        cn.close()
        if len(vs) >= 3:
            out = sum(vs) / len(vs)
    except Exception:
        out = None
    if out is None:
        # official-record fallback (boss 2026-09-07: a code missing from
        # raw_daily_prices let the volume gate pass on no data)
        try:
            from services.naver_stock import daily_history
            rows = daily_history(code, days=8) or []
            d_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
            vs = [float(r["volume"]) for r in rows
                  if r.get("volume") and str(r.get("date"))[:10] < d_iso][:5]
            if len(vs) >= 3:
                out = sum(vs) / len(vs)
        except Exception:
            pass
    _VOL5_CACHE[key] = out
    return out


_OPEN_CACHE: dict = {}


def _low_official(code: str, day: str, fallback=None):
    """TODAY'S TRUE LOW so far - the same lesson as the open. Our tape starts
    after the opening auction, so a dip in the first seconds is invisible to
    it, and gate 1's "did it come back to yesterday's price" test would miss
    exactly the moment it is looking for. The daily row carries the real low;
    the tape is the fallback."""
    try:
        from services.naver_stock import daily_history
        h = daily_history(code, days=1)
        if h and h[0].get("low"):
            v = float(h[0]["low"])
            return min(v, float(fallback)) if fallback else v
    except Exception:
        pass
    return fallback


def _open_official(code: str, day: str, fallback=None):
    """TODAY'S REAL OPENING PRICE, not the first bar our tape happened to catch.

    Boss 2026-09-04, checking the gap table against Kiwoom. Our websocket tape
    starts a moment after the opening auction, so its first bar is the price
    AFTER the open - measured on six stocks it was high every single time, by
    0.31% to 2.15%. Gate 1 divides that number by yesterday's close, so every
    gap we computed was inflated: 한미반도체 opened DOWN 0.71% and our tape made
    it look like a 2.15% gap UP, blocking a stock that never gapped and is
    +8.1% today.

    The official open comes from the daily row; the tape is only the fallback,
    and it is now the thing we distrust rather than the thing we trust."""
    key = (code, day)
    hit = _OPEN_CACHE.get(key)
    if hit is not None:
        return hit or fallback
    out = None
    try:
        from services.naver_stock import daily_history
        h = daily_history(code, days=2)
        if h and h[0].get("open"):
            out = float(h[0]["open"])
    except Exception:
        out = None
    _OPEN_CACHE[key] = out or 0
    return out or fallback


# ── 갭상승 관문 면제, 날짜로 못박은 하루 (boss 2026-09-08) ──────────────────
# "오늘 한정으로 갭상승 관문 하나만 제외하고 판정 및 작동하도록 하고, 내일부터
#  다시 갭상승 관문 정상작동 하도록 해."
#
# A waiver that has to be REMEMBERED is a waiver that gets forgotten - and a
# forgotten one buys gapped stocks every morning after. So it is not a flag
# anyone has to switch back: it is a DATE, and the day itself ends it. Tomorrow
# this function returns False on its own, with no restart, no edit and nobody
# needing to recall that today was special.
#
# It is read at CALL time by every place the gate actually acts - the board
# gate, the whynot cascade, the send-time guard and the bulk order - so all
# four say the same thing on the same day and cannot drift apart.
#
# NOT waived: checklist item 49 (과도한 갭 시가가 아닌가) which only scores and
# never blocks, and the engines' own gap guards in proof_lab. He asked for the
# 관문 - the gate that refuses a buy - not for the scoring to be rewritten.
GAP_WAIVER_DAYS = ("20260908",)


def gap_gate_waived(day: str | None = None) -> bool:
    """True only on a day the boss lifted the 갭상승 gate by hand. Every other
    day - including tomorrow - the gate is in force exactly as before."""
    try:
        if not day:
            from services.kiwoom_tape import _day as _d9
            day = _d9()
    except Exception:
        return False
    return str(day or "") in GAP_WAIVER_DAYS


def _gap_ref(code: str, day: str) -> float:
    """The price an overnight gap is measured FROM (boss 2026-09-03 evening:
    "we have to compare with the 9am price and one day before 20:00 price").

    Yesterday's 15:30 close is not the last price the market paid - KRX trades
    on to 18:00 in the 시간외 sessions. When we recorded that evening print it
    is the reference; otherwise the official close stands, which is exactly the
    old behaviour, so a missing print can never change a decision."""
    try:
        from services.after_hours import price as _ahp
        prev = [d for d in (stored_days() or []) if d < day]
        if prev:
            ah = _ahp(prev[-1], code)
            if ah:
                return float(ah)
    except Exception:
        pass
    return _daily20(code, day)[0]


def _daily20(code: str, before_day: str) -> tuple:
    """(yesterday's close, 20-day low) as of before_day - the rebound door's
    daily context. Daily closes come from the 250-day 1-minute history plus the
    desk's own stored tapes; finished days never change, so cache for ever."""
    key = (code, before_day)
    hit = _D20_CACHE.get(key)
    if hit is not None:
        return hit
    closes = {}
    # THE DATABASE FIRST (boss 2026-09-03 12:5x: "no late, no missed chances").
    # Profiling the desk pass showed 48.5 of its 52 seconds inside THIS function:
    # to recover each past day CLOSING PRICE it replayed that day entire TICK
    # TAPE - 12.9 million json.loads per pass - for numbers raw_daily_prices
    # already holds. One query replaces all of it; the tape scan below now only
    # runs for a stock the database has never heard of. Same numbers, same laws.
    try:
        from services.daily_pick import _conn as _dbc
        _cn = _dbc(); _cu = _cn.cursor()
        _cu.execute("""SELECT date, close FROM raw_daily_prices
                       WHERE ticker = %s AND close IS NOT NULL
                       ORDER BY date""", (code,))
        for _d3, _c3 in _cu.fetchall():
            closes[_d3.strftime("%Y%m%d")] = float(_c3)
        _cn.close()
    except Exception:
        closes = {}
    _pd9 = sorted(d2 for d2 in closes if d2 < before_day)
    if len(_pd9) >= 60:
        _cl0 = [closes[d2] for d2 in _pd9]
        _yl0 = min(len(_cl0), 246)
        out = (_cl0[-1], min(_cl0[-20:]), min(_cl0[-5:]),
               sum(_cl0[-20:]) / 20, sum(_cl0[-_yl0:]) / _yl0)
        _D20_CACHE[key] = out
        return out
    closes = {}
    try:
        import json as _j
        from pathlib import Path as _P
        f = _P(__file__).resolve().parent.parent / "data" / "minute1_hist" / f"{code}.json"
        if f.exists():
            for row in _j.loads(f.read_text()):
                ts = row[0]
                t = ts[8:14]
                if "090000" <= t <= "153000":
                    closes[ts[:8]] = float(row[4])
    except Exception:
        pass
    try:
        for d2 in stored_days(code):
            if d2 >= before_day:
                continue
            cs2 = _bars_for(code, 5, 60, d2)
            if cs2:
                closes[d2] = float(cs2[-1]["close"])
    except Exception:
        pass
    # THE DATABASE IS THE THIRD SOURCE (boss 2026-09-03 09:2x, demo morning:
    # Menu 3's four freshly-picked rooms had ma20/ma1y/low5 = None, so the
    # average gates, the 5-day patience law and the rebound door were all
    # SILENTLY INERT on exactly the stocks the agent had just chosen. The two
    # sources above only cover stocks the desk has been collecting for weeks -
    # a room picked this morning has neither a minute-history file nor stored
    # tapes. raw_daily_prices already holds their full daily history and is
    # what the checklist scores from, so the laws now read it too.
    if len([d2 for d2 in closes if d2 < before_day]) < 40:
        try:
            from services.daily_pick import _conn
            _cn = _conn(); _cu = _cn.cursor()
            _cu.execute("""SELECT date, close FROM raw_daily_prices
                           WHERE ticker = %s ORDER BY date""", (code,))
            for _d3, _c3 in _cu.fetchall():
                if _c3 is None:
                    continue
                _k3 = _d3.strftime("%Y%m%d")
                closes.setdefault(_k3, float(_c3))
            _cn.close()
        except Exception:
            pass
    days = sorted(d2 for d2 in closes if d2 < before_day)
    if not days:
        out = (None, None, None, None, None)
    else:
        prev_close = closes[days[-1]]
        low20 = min(closes[d2] for d2 in days[-20:])
        # the RECENT low as well (boss 2026-09-02 11:0x, the 두산 case: he
        # reads "in the buying zone" from the last few days, and the year
        # percentile cannot see it - 두산 is 0.31 of its year but BELOW its
        # 5-day low)
        low5 = min(closes[d2] for d2 in days[-5:])
        # the two average lines the boss's gates run on (2026-09-02 17:5x:
        # "in the buying block case add today's rule - if it is higher than
        # average do not buy"): month and year means of the closes
        _cl9 = [closes[d2] for d2 in days]
        ma20 = sum(_cl9[-20:]) / 20 if len(_cl9) >= 20 else None
        _yl9 = min(len(_cl9), 246)
        mayr = sum(_cl9[-_yl9:]) / _yl9 if _yl9 >= 60 else None
        out = (prev_close, low20, low5, ma20, mayr)
    _D20_CACHE[key] = out
    return out


_YR_CACHE: dict = {}


def _daily_pos(code: str, price: float) -> float | None:
    """LAYER 1 (boss 2026-08-21): where this price sits in the stock's one-year
    range - 0.0 = the year's low, 1.0 = the year's high. The context every law
    may consult; near the top the desk gets careful (sizes halve, no averaging
    down), near the bottom it trades with both hands."""
    rng = _YR_CACHE.get(code)
    if rng is None:
        lo = hi = None
        try:
            import json as _j
            from pathlib import Path as _P
            f = (_P(__file__).resolve().parent.parent / "data" / "minute1_hist"
                 / f"{code}.json")
            if f.exists():
                closes = [float(r[4]) for r in _j.loads(f.read_text())]
                if closes:
                    lo, hi = min(closes), max(closes)
        except Exception:
            lo = hi = None
        if lo is None or hi is None:
            # THE EXTRAS' BLIND SPOT (boss's deep audit, 2026-08-25 13:3x:
            # "if it selects 5 stocks but one is in the selling zone..."):
            # minute1_hist covers only his six, so every checklist stock had
            # daily_pos None - the 85% no-buy ban, the bottom boost and the
            # caution half were silently OFF for them. The year range now
            # falls back to the daily-price DB the checklist itself scores
            # from, so the zone laws guard all 20.
            try:
                from services.daily_pick import _conn
                conn = _conn()
                cur = conn.cursor()
                cur.execute(
                    """SELECT close FROM raw_daily_prices
                       WHERE ticker = %s AND date >= CURRENT_DATE - 370
                       ORDER BY date""", (code,))
                closes = [float(r[0]) for r in cur.fetchall() if r[0]]
                conn.close()
                if len(closes) >= 40:
                    lo, hi = min(closes), max(closes)
            except Exception:
                lo = hi = None
        rng = (lo, hi)
        _YR_CACHE[code] = rng
    lo, hi = rng
    if not lo or not hi or hi <= lo or not price:
        return None
    return max(0.0, min(1.5, (price - lo) / (hi - lo)))


_NEWS_CACHE: dict = {"mtime": 0.0, "rows": []}


def _news_risk(code: str) -> int:
    """LAYER 3, SAFE MODE (boss 2026-08-24 morning, explicit order: "test and
    implement in parallel, start to use from today - news does not decide
    solely, other factors also help"): how many 위험 stamps the news intern
    put on this stock in the LAST 60 MINUTES. The engine halves NEW buys at
    >=2 - it never bans a door and never sells a position. The intern's
    week-one grading continues every evening in parallel; full power (door
    closing) only after the graded record and the boss's word."""
    import datetime as _dt
    import json as _json
    from pathlib import Path as _P
    try:
        f = (_P(__file__).resolve().parent.parent / "data" / "news_intern"
             / f"{_dt.datetime.now().strftime('%Y%m%d')}.jsonl")
        mt = f.stat().st_mtime
        if mt != _NEWS_CACHE["mtime"]:
            rows = []
            for ln in f.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(_json.loads(ln))
                except Exception:
                    continue
            _NEWS_CACHE["mtime"] = mt
            _NEWS_CACHE["rows"] = rows
        cut = (_dt.datetime.now()
               - _dt.timedelta(minutes=60)).isoformat(timespec="seconds")
        return sum(1 for r in _NEWS_CACHE["rows"]
                   if r.get("code") == code and r.get("stamp") == "위험"
                   and (r.get("ts") or "") >= cut)
    except Exception:
        return 0


_VB_CACHE: dict = {}


def _fuel(code: str, bars: list) -> float | None:
    """LAYER 2 (boss 2026-08-21): 'if volume increases, price increases; in
    quiet cases most probably decrease or oscillation.' The last 30 minutes'
    volume against this stock's OWN median for the same half-hour of the day,
    over the year. 1.0 = a normal half hour; 0.5 = half-asleep; 2.0 = loud."""
    if not bars:
        return None
    med = _VB_CACHE.get(code)
    if med is None:
        med = {}
        try:
            import json as _j
            import statistics as _st
            from pathlib import Path as _P
            f = (_P(__file__).resolve().parent.parent / "data" / "minute1_hist"
                 / f"{code}.json")
            if f.exists():
                buckets: dict = {}
                for r in _j.loads(f.read_text()):
                    t6 = str(r[0])[8:14]
                    if "090000" <= t6 <= "153000":
                        bk = (str(r[0])[:8], t6[:3])   # (day, half-hour-ish)
                        buckets.setdefault(bk, 0.0)
                        buckets[bk] += float(r[5])
                per_slot: dict = {}
                for (d8, slot), vsum in buckets.items():
                    per_slot.setdefault(slot, []).append(vsum)
                med = {slot: _st.median(v) for slot, v in per_slot.items() if v}
        except Exception:
            med = {}
        _VB_CACHE[code] = med
    if not med:
        return None
    # the history buckets are ~10-minute slots - the live window must match
    last = bars[-10:] if len(bars) >= 5 else bars
    cur = sum(float(b.get("vol") or 0) for b in last)
    slot = bars[-1]["hhmm"].replace(":", "")[:3]
    base = med.get(slot)
    if not base:
        return None
    # scale: the buckets sum ~30 minutes of the day; align window lengths
    return round(cur / base, 2) if base else None


_OV_CACHE: dict = {}


def _open_vol_med(code: str) -> float:
    """This stock's median first-five-minutes volume over the 250-day history -
    the morning door's yardstick for 'the open is unusually busy'."""
    hit = _OV_CACHE.get(code)
    if hit is not None:
        return hit
    out = 0.0
    try:
        import json as _j
        import statistics as _st
        from pathlib import Path as _P
        f = _P(__file__).resolve().parent.parent / "data" / "minute1_hist" / f"{code}.json"
        if f.exists():
            days = {}
            for row in _j.loads(f.read_text()):
                ts = row[0]
                t = ts[8:14]
                if "090000" <= t <= "153000":
                    days.setdefault(ts[:8], []).append(float(row[5]))
            sums = [sum(v[:5]) for v in days.values() if len(v) > 10]
            if sums:
                out = float(_st.median(sums))
    except Exception:
        out = 0.0
    _OV_CACHE[code] = out
    return out


def _kd0() -> str:
    from services.kiwoom_tape import _day
    return _day()


def daily_ctx(code: str, day: str) -> list[float]:
    """The 5-year tables' view of this stock on the morning of `day` - everything from
    strictly EARLIER days: yesterday's returns, gap, SMA ratio, and the foreign/
    institutional flow signs. Zeros when the DB is unreachable - the models degrade
    gracefully instead of the desk failing."""
    key = (code, day)
    if key in _CTX_CACHE:
        return _CTX_CACHE[key]
    out = [0.0] * 7
    try:
        from datetime import date as _date
        from ml._db import get_conn
        dd = _date(int(day[:4]), int(day[4:6]), int(day[6:]))
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""SELECT ret_1d, ret_5d, ret_20d, gap_open, sma_5, sma_20
                       FROM stock_features_daily WHERE ticker=%s AND date < %s
                       ORDER BY date DESC LIMIT 1""", (code, dd))
        r = cur.fetchone()
        cur.execute("""SELECT foreign_net_value, inst_net_value
                       FROM korean_investor_flows WHERE ticker=%s AND date < %s
                       ORDER BY date DESC LIMIT 1""", (code, dd))
        f = cur.fetchone() or (0, 0)
        conn.close()
        if r:
            ret1, ret5, ret20, gap, s5, s20 = [float(x or 0) for x in r]
            out = [ret1, ret5, ret20, gap,
                   (s5 / s20 - 1) * 100 if s20 else 0.0,
                   (1.0 if (f[0] or 0) > 0 else -1.0 if (f[0] or 0) < 0 else 0.0),
                   (1.0 if (f[1] or 0) > 0 else -1.0 if (f[1] or 0) < 0 else 0.0)]
    except Exception:
        pass
    _CTX_CACHE[key] = out
    return out
_RANK_DAY_CACHE: dict = {}
_RANK_TTL: dict = {}
_TRADES_TTL: dict = {}


def _prior_day_closes(code: str, tick: int, period: int, before: str = ""):
    """Bars from every stored day BEFORE today, concatenated in date order. Day files are
    independent tapes, so bars are built per day and joined - an overnight gap therefore
    lands INSIDE the training data exactly once per boundary, which mirrors reality."""
    import re as _re
    from services.kiwoom_tape import ROOT, _day, load
    days = sorted({m.group(1) for p in ROOT.glob(f"{code}_*.jsonl")
                   if (m := _re.match(rf"{code}_(\d{{8}})\.jsonl$", p.name))
                   and m.group(1) < (before or _day())})
    cl, vv = [], []
    for d in days:
        tk = load(code, d)
        if not tk:
            continue
        cs = bars_time(tk, period) if period else bars_ticks(tk, max(1, tick))
        cl += [c["close"] for c in cs]
        vv += [float(c.get("vol") or 0) for c in cs]
    return cl, vv, days


def kiwoom_ml_for(code: str, tick: int, period: int, v: dict, day: str = ""):
    """This company's model for this rule and clock, fitted on yesterday-and-earlier."""
    from services.kiwoom_tape import _day
    from services.proof_lab import _outcome
    from services.proof_ml import features_at, train
    ref = day or _day()
    key = (code, v["id"], tick, period, ref)
    if key in _KML_CACHE:
        return _KML_CACHE[key]
    import re as _re
    from services.kiwoom_tape import ROOT, load as _load
    from services.proof_ml import features_at_v2, train_v2
    prior_days = sorted({m.group(1) for p in ROOT.glob(f"{code}_*.jsonl")
                         if (m := _re.match(rf"{code}_(\d{{8}})\.jsonl$", p.name))
                         and m.group(1) < ref})
    # V2 SAMPLES (boss 2026-08-06 night): per prior day, tick features + that day's
    # 5-year context. v1 samples collected in the same pass as the honest fallback.
    s_v1, s_v2 = [], []
    for d2 in prior_days:
        tk_rows = _load(code, d2)
        if not tk_rows:
            continue
        cs2 = bars_time(tk_rows, period) if period else bars_ticks(tk_rows, max(1, tick))
        if len(cs2) < 30:
            continue
        cl = [c["close"] for c in cs2]
        vv = [float(c.get("vol") or 0) for c in cs2]
        tt = [c["hhmm"] for c in cs2]
        ctx = daily_ctx(code, d2)
        t = krx_tick(cl[-1]) or 1
        u, dn, last = 0, 0, -1
        for i in range(1, len(cl)):
            # flat = pause, same as the live engines (boss 2026-08-06)
            if cl[i] > cl[i - 1]:
                u, dn = u + 1, 0
            elif cl[i] < cl[i - 1]:
                u, dn = 0, dn + 1
            if (dn if v.get("dir", 1) < 0 else u) < v["entry"]:
                continue
            y, _res = _outcome(cl, i, cl[i] + t, t, v)
            if y is None:
                continue
            s_v1.append((features_at(cl, vv, i, last), y))
            s_v2.append((features_at_v2(cl, vv, i, last, tt, u, ctx), y))
            last = i
    bundle = train_v2(s_v2, key)
    if bundle is None:
        bundle = train(s_v1, key)      # the old recipe - never guessing
    if bundle is not None:
        bundle["n_signals"] = len(s_v1)
        bundle["trained_to"] = (f"{prior_days[-1][:4]}-{prior_days[-1][4:6]}-"
                                f"{prior_days[-1][6:]} close" if prior_days else "?")
    _KML_CACHE[key] = bundle
    return bundle
# every id this desk shows - the page uses it so the two can never drift apart.
# DESK, not PLAIN: listing only the plain 12 made the page hide the 12 ML twins
# (boss 2026-08-06 - "rules + ML is empty").
ORIGINAL_12 = [v["id"] for v in DESK]


def _auto_day(day: str) -> tuple[str, bool]:
    """WHICH DAY THE BOARD READS when nothing is chosen (boss 2026-08-11: "even when the
    market is closed I was able to see old trading history, now it is not showing").

    "" means TODAY's live tape, which is right during and after a session - but before
    the opening bell today has no tape at all, so the whole board went blank every
    morning and the previous day's work looked deleted. When today has no file yet, fall
    back to the newest day that does, and say so, rather than showing an empty desk.
    """
    if day:
        return day, False
    days = stored_days()
    today = _kd0()
    if not days or days[-1] == today:
        return day, False
    return days[-1], True


def dip_status(tick: int = 5, period: int = 0) -> dict[str, Any]:
    """WHERE EACH STOCK STANDS in the new rule's hunt, live (boss 2026-08-11: the rule
    fires a few times a day by design, and a quiet board must say "the condition is not
    met yet" per stock rather than look broken). Judged against N2, the loosest active
    dip rule (0.4% drop, 3x a typical bar, 2 ups, 0.4% chop floor); N1/N3 want 0.8%.
    """
    import statistics
    out = []
    for code, name in WATCH:
        cs = _bars_for(code, tick, period, "")
        if len(cs) < 25:
            out.append({"code": code, "name": name, "stage": "warming",
                        "ko": "봉이 아직 부족합니다 (수집 중)",
                        "en": "not enough bars yet (collecting)"})
            continue
        cl = [c["close"] for c in cs]
        i = len(cl) - 1
        diffs = [abs(cl[j] - cl[j - 1]) for j in range(max(1, i - 39), i + 1)]
        typ = statistics.median(diffs) if diffs else 0.0
        j0 = max(0, i - 20)
        win = cl[j0:i + 1]
        hi = max(win); lo = min(win)
        rng = (hi - lo) / hi * 100 if hi else 0.0
        k = j0 + win.index(hi)
        trough = min(cl[k:i + 1]) if k < i else cl[i]
        drop = (hi - trough) / hi * 100 if hi and k < i else 0.0
        sharp_x = ((hi - trough) / typ) if typ else 0.0
        ups = 0
        for j in range(i, 0, -1):
            if cl[j] > cl[j - 1]:
                ups += 1
            elif cl[j] < cl[j - 1]:
                break
        row = {"code": code, "name": name, "drop": round(drop, 2),
               "sharp_x": round(sharp_x, 1), "range": round(rng, 2), "ups": ups}
        if rng < 0.40:
            row["stage"] = "chop"
            row["ko"] = f"횡보 (최근 20봉 폭 {rng:.2f}%) — 규칙대로 매매 안 함"
            row["en"] = f"flat market ({rng:.2f}% range over 20 bars) - no trading, by the rule"
        elif drop < 0.40 or sharp_x < 3.0:
            row["stage"] = "waiting_drop"
            row["ko"] = (f"급락 대기 — 지금까지 최대 하락 {drop:.2f}% "
                         f"(기준 0.4% 이상 · 평소 봉의 3배, 현재 {sharp_x:.1f}배)")
            row["en"] = (f"waiting for a sharp drop - deepest so far {drop:.2f}% "
                         f"(needs 0.4%+ and 3x a normal bar, now {sharp_x:.1f}x)")
        elif ups < 2:
            row["stage"] = "turning"
            row["ko"] = f"급락 {drop:.2f}% 발견 — 반등 확인 중 ({ups}/2 양봉)"
            row["en"] = f"sharp drop {drop:.2f}% found - waiting for the turn ({ups}/2 up bars)"
        else:
            row["stage"] = "ready"
            row["ko"] = f"조건 충족 — 급락 {drop:.2f}% 후 {ups}연속 상승 (신호 구간)"
            row["en"] = f"condition met - {drop:.2f}% drop then {ups} rises (signal zone)"
        out.append(row)
    return {"ok": True, "clock": f"{period}초" if period else f"{tick}틱",
            "rule": "N2 (급락 0.4% · 3배 · 2양봉 · 횡보 0.4% 제외)", "rows": out}


def rank(tick: int = 5, period: int = 0, day: str = "",
         frm: str = "", to: str = "", use_gate: bool = True,
         allow_fallback: bool = True, codes: str = "") -> dict[str, Any]:
    """Every plain rule over the real tape of every watched stock, ranked.

    day="all" is the CUMULATIVE board (boss 2026-08-06: "total result up to today"):
    every stored day is run separately - each day is its own session, positions never
    span the overnight gap, and each day's ML models are the ones that day actually had
    (trained only on the days before it) - then the trades are added up."""
    import time as _t
    _rk = (tick, period, day, frm, to, use_gate, allow_fallback, codes)
    _hit = _RANK_TTL.get(_rk)
    if _hit and _t.time() - _hit[0] < 20.0:
        return _hit[1]         # the page polls every 3s; identical answers are reused
    day, auto_day = _auto_day(day) if allow_fallback else (day, False)
    day_list = stored_days() if day == "all" else [day]
    # per-desk view (boss 2026-08-24: the two desks' histories looked the same):
    # `codes` limits the replay to those stocks — the six-desk and the reco desk
    # each see only their own trades.
    _csel = {c.strip() for c in (codes or "").split(",") if c.strip()}
    _watch = [(c, n) for c, n in WATCH if not _csel or c in _csel]
    tapes_by_day = []
    for d in day_list:
        tapes = {}
        for code, name in _watch:
            cs = _bars_for(code, tick, period, d, frm, to)
            if len(cs) < 10:
                continue
            tapes[code] = {"name": name, "cs": cs, "tk": krx_tick(cs[-1]["close"]) or 1}
        if tapes:
            tapes_by_day.append((d, tapes))
    if not tapes_by_day:
        tapes_by_day = [(day, {})]

    rows = []
    # THE DESK LAW (boss 2026-08-06: "if I am holding then I can not buy another
    # stock"): every stock's bars merge onto one clock and the rule holds ONE position
    # across all of them - see proof_lab.run_desk. The per-stock loop this replaces
    # let a rule hold all three companies at once.
    # "_dipc" is the shared chop/dip cache: every rule gets a shallow copy of these
    # dicts, and seeding the inner dict here means the 20-bar walk runs once per stock
    # the day's book snapshots ride on the stk dict so the new family can offer in
    # front of the biggest bid wall; [] on days before recording began (2026-08-11)
    from services.kiwoom_tape import load_book as _lb
    base_by_day = [(d, [{"code": code, "_dipc": {}, "book": _lb(code, d or _kd0()),
                         "d8": d or _kd0(),
                         "closes": [c["close"] for c in tp["cs"]],
                         "highs": [c["high"] for c in tp["cs"]],
                         "lows": [c["low"] for c in tp["cs"]],
                         # the TRUE opening print - the gap guard compares THIS
                         # to prev_close (boss 2026-08-27: NAVER +2.05% slipped
                         # under a first-minute-close comparison)
                         "open_px": _open_official(code, d or _kd0(), tp["cs"][0].get("open")) or (tp["cs"][0].get("open")
                                     if tp["cs"] else None),
                         "news_hits": (_news_times(code)
                                       if (d or _kd0()) == _kd0() else []),
                         "tick": tp["tk"], "seed": 1,
                         # storm habit input: only TODAY has an American night on
                         # file; stored days replay calm (honest default)
                         "us_mode": (_us_mode_today() if (d or _kd0()) == _kd0()
                                     else "calm"),
                         "prev_close": _gap_ref(code, d or _kd0()),
                         "low20": _daily20(code, d or _kd0())[1],
                         "low5": _daily20(code, d or _kd0())[2],
                         "high5": _week_stats(code, d or _kd0())[1],
                         "hz": _hz_stats(code, d or _kd0()),
                         "avg5": _week_stats(code, d or _kd0())[2],
                         "vol_day_avg": _vol5(code, d or _kd0()),
                         "ma20": _daily20(code, d or _kd0())[3],
                         "mayr": _daily20(code, d or _kd0())[4],
                         "open_vol_med": _open_vol_med(code),
                         "daily_pos": _daily_pos(code,
                                                 tp["cs"][-1]["close"]
                                                 if tp["cs"] else 0),
                         "fuel": _fuel(code, tp["cs"]),
                         "news_risk": _news_risk(code),
                         "vols": [float(c.get("vol") or 0) for c in tp["cs"]],
                         "ctx": daily_ctx(code, d or _kd0()),
                         "gate_ok": (_gate_ok(code, d or _kd0()) if use_gate else True),
                         "times": [c["hhmm"] for c in tp["cs"]]}
                        for code, tp in tapes.items()])
                   for d, tapes in tapes_by_day]
    # ONE sort of the merged clock per day-tape, shared by every rule (the sort was
    # ~90% of the request at end-of-day: 29 rules x 300k events)
    events_by_day = [(d, sorted((sk["times"][i], si, i)
                                for si, sk in enumerate(base)
                                for i in range(1, len(sk["closes"]))))
                     for d, base in base_by_day]
    from services.kiwoom_tape import _day as _kday
    _today = _kday()
    for v in DESK:
        # a clock-pinned rule only computes on its own view - a 1분 strategy must not
        # be judged on 5틱 bars it was never designed for (boss 2026-08-07 gain group)
        if v.get("clock") and tuple(v["clock"]) != (tick, period):
            continue
        trades = []
        for (d, base_stks), (_d2, _events) in zip(base_by_day, events_by_day):
            # a FINISHED day's tape never changes, so its trades are computed once.
            # Without this the cumulative view re-ran three days of every rule on every
            # 3-second poll. Today is never cached - it is still being written.
            ck = (d, tick, period, frm, to, v["id"], use_gate)
            if d and d < _today and ck in _RANK_DAY_CACHE:
                trades += _RANK_DAY_CACHE[ck]
                continue
            stks = [dict(sk, ml_bundle=(kiwoom_ml_for(sk["code"], tick, period, v, d)
                                        if v.get("ml") else None)) for sk in base_stks]
            got = run_desk(stks, v, fill_fn=_fill, events=_events)
            if d and d < _today:
                _RANK_DAY_CACHE[ck] = got
            trades += got
        w = sum(1 for t in trades if t["gross_pct"] > 0)
        l = sum(1 for t in trades if t["gross_pct"] < 0)
        rows.append({
            "id": v["id"], "ko": label(v, True), "en": label(v, False),
            # +1 = the original twelve (buy after RISES), -1 = the six reversal rules
            "dir": v.get("dir", 1), "kind": v["kind"],
            # WHICH WAY (boss 2026-08-10): "new" = find a sharp drop and ride the
            # bounce, "old" = buy three rises and take a fixed number of ticks. The
            # board opens on the new family and the old one is one click away.
            "family": v.get("family", "old"),
            "trips": len(trades), "wins": w, "losses": l,
            "flats": len(trades) - w - l,
            "win_pct": round(w / (w + l) * 100) if (w + l) else 0,
            "net": round(sum(t["net_pct"] for t in trades), 2),
            "net_won": round(sum(shares_for(t["entry"], 0) * t["entry"] * t["net_pct"] / 100
                                 for t in trades)),
            "shares_total": sum(shares_for(t["entry"], 0) for t in trades),
            "capital_used": round(sum(shares_for(t["entry"], 0) * t["entry"] for t in trades)),
            "per_trade_won": (round(sum(shares_for(t["entry"], 0) * t["entry"] * t["net_pct"]
                                        / 100 for t in trades) / len(trades))
                              if trades else 0),
            "per_trade": (round(sum(t["net_pct"] for t in trades) / len(trades), 3)
                          if trades else 0.0),
            # fewer than this many DECIDED trades and a win rate is a coin that landed a
            # few times. Counted on wins+losses, not trips: eight flats and two wins is a
            # "100%" carried by two trades.
            "decided": w + l, "thin": (w + l) < 10,
        })
    # PURELY by win rate, highest first (boss 2026-08-05, same as the Strategy Lab).
    # Sorting thin rules to the bottom put a 100% rule below a 7% one and made the
    # sequence look arbitrary next to the column he is reading. The 표본 부족 badge stays
    # on the row, which is where a warning about the sample belongs.
    # GROUPED as the boss reads them (2026-08-05): first every up/down rule (exit by
    # candle count), then every %-target rule - and inside each group, highest win rate
    # first. `kind` travels with the row so the page cannot need to guess the group.
    # every "+ ML" row carries its own plain twin's rate, so the comparison the board
    # exists for survives any sorting (same as the Strategy Lab)
    _by = {r["id"]: r for r in rows}
    for r in rows:
        if r["id"].endswith("ML"):
            tw = _by.get(r["id"][:-2])
            r["vs"] = tw["win_pct"] if tw else None
            r["vs_trips"] = tw["trips"] if tw else None
    rows.sort(key=lambda r: (0 if r.get("kind") == "candle" else 1,
                             -r["win_pct"], -r["trips"]))
    _res = {"ok": True, "original_12": ORIGINAL_12, "days": stored_days(),
            "day": day, "auto_day": auto_day, "today": _kd0(),
            "frm": frm, "to": to, "gate_applied": use_gate,
            "clock": f"{period}초" if period else f"{tick}틱",
            "tick": tick, "period": period, "fee_pct": FEE_PCT,
            # for day="all" this is the LATEST day's tape summary with the bar counts
            # summed over every day - enough for the header, honest about the total
            "stocks": [{"code": c, "name": t["name"],
                        "bars": sum(len(tp2[c]["cs"]) for _d2, tp2 in tapes_by_day if c in tp2),
                        "from": t["cs"][0]["hhmm"], "to": t["cs"][-1]["hhmm"],
                        "tick_size": t["tk"]} for c, t in tapes_by_day[-1][1].items()],
            "variants": rows}
    _RANK_TTL[_rk] = (_t.time(), _res)
    return _res


def shares_for(entry: float, budget: int) -> int:
    """(see below) — kept for the explicit won-budget buttons on the desk."""
    """How many shares ₩`budget` buys of a stock priced `entry`.

    The desks have always traded ONE share, which is not equal risk: one share of
    SK하이닉스 is ₩1,562,000 of exposure and one share of 한화오션 is ₩85,250 — an 18x
    difference filed under the same word, "a trade". A fixed won budget makes the three
    companies comparable, and it is what a real account does.

    Korea has no fractional shares, so this floors — and never below one, because a budget
    smaller than one share of SK하이닉스 would silently drop that stock from the results.
    """
    if budget <= 0:
        # NO BUDGET CHOSEN -> the same price-band cap the artificial desk uses, so the
        # real desk opens tomorrow trading real sizes instead of one share (boss
        # 2026-08-04: "not 1 shares it should more then we can earn more money").
        #
        # There are no models on this desk yet, so nothing can pick a fraction of the cap
        # the way the artificial ML rules do — a plain rule takes the whole band. When the
        # models are trained on the real tape they will size WITHIN this cap, never above
        # it, so today's numbers are the ceiling rather than something to be revised past.
        from services.proof_ml import cap_for
        return cap_for(entry)
    return max(1, int(budget // max(1.0, entry)))


def _rank_win9(code: str, day: str = ""):
    try:
        from services.reco_rank_log import windows_for
        return windows_for(code, day or None)
    except Exception:
        return None


def _rank_t09(day: str = "", code: str = ""):
    """Grace boundary for the rank gate. Before the day's first snapshot the
    timeline is blind - but grace admits ONLY the morning's own picks, never
    the whole universe (boss 2026-08-25 14:0x: the six's morning trades were
    leaking into the reco desk through the pre-log window)."""
    try:
        from services.reco_rank_log import snapshots
        sn = snapshots(day or None)
        t0 = sn[0].get("t") if sn else None
        if code:
            try:
                from services.daily_pick import effective_picks, reco_n
                # ONLY the morning's actual top-N recommendation gets grace.
                # Since the 20-universe, the saved picks file carries the six
                # core stocks too (they sit in the checklist board), so the
                # full list is NOT "the selection" (boss 2026-08-25 14:2x:
                # SK하이닉스 traded in menu 2 all morning through this hole).
                # BENCH LAW (2026-08-27): the N seats skip zone-banned stocks,
                # so the grace set is the same seats the rank gate will use.
                _n9 = max(1, min(int(reco_n()), 10))
                picks = {c for c, _n in (effective_picks(_n9) or [])}
                if code not in picks:
                    return "00:00:00"       # no grace - top-N windows only
            except Exception:
                pass
        return t0
    except Exception:
        return None


def trades(vid: str, tick: int = 5, period: int = 0, code: str = "",
           bars: int = 2500, limit: int = 300, around: int = -1,
           budget: int = 0, day: str = "", frm: str = "", to: str = "",
           use_gate: bool = True, allow_fallback: bool = True,
           codes: str = "", rank_gate: bool = False) -> dict[str, Any]:
    """One rule's trades on the real tape, with the chart and the evidence per trade."""
    v = next((x for x in DESK if x["id"] == vid), None)
    if v is None:
        return {"ok": False, "error": f"unknown rule {vid}"}
    import time as _t
    day, _auto = _auto_day(day) if allow_fallback else (day, False)
    _tk2 = (vid, tick, period, code, bars, limit, around, budget, day, frm, to, use_gate, codes, rank_gate)
    _hit2 = _TRADES_TTL.get(_tk2)
    if _hit2 and _t.time() - _hit2[0] < 20.0:
        return _hit2[1]

    # Two passes: collect every trade first, sort them, THEN build the chart — the chart
    # needs to look up `around` in the SAME order the table displays, and that order is
    # not known until every stock has been walked.
    rows, holding, chart = [], [], None
    waiting: list = []          # working limit offers (boss 2026-08-26: the
                                # condition→offer→waiting→fill process, shown live)
    # WHICH STOCK THE CHART DRAWS. `code` is the button the boss pressed above the chart,
    # but a clicked TRADE wins over it: the trade table lists every company together, and
    # clicking an SK하이닉스 row while the chart was pinned to 삼성전자 left the chart
    # exactly where it was — so clicking almost any row appeared to do nothing at all
    # (boss 2026-08-04: "if click any completed trade it is not showing chart"). Resolved
    # after the rows are sorted, because `around` indexes the displayed order.
    # THE DESK LAW (boss 2026-08-06): all stocks on one clock, ONE position for the
    # rule across the whole desk - see proof_lab.run_desk. day="all" runs every stored
    # day as its own session and concatenates the trades (cumulative view).
    day_list = stored_days() if day == "all" else [day]
    _csel2 = {c.strip() for c in (codes or "").split(",") if c.strip()}
    _watch2 = [(c, n) for c, n in WATCH if not _csel2 or c in _csel2]
    for d in day_list:
        stks = []
        for c_code, name in _watch2:
            cs = _bars_for(c_code, tick, period, d, frm, to)
            if len(cs) < 10:
                continue
            from services.kiwoom_tape import load_book as _lb2
            stks.append({"code": c_code, "name": name, "cs": cs, "_dipc": {},
                         "d8": d or _kd0(),
                         "book": _lb2(c_code, d or _kd0()),
                         "closes": [c["close"] for c in cs],
                         "highs": [c["high"] for c in cs],
                         "lows": [c["low"] for c in cs],
                         "open_px": (_open_official(c_code, d or _kd0(), cs[0].get("open"))
                                     if cs else None),
                         "news_hits": (_news_times(c_code)
                                       if (d or _kd0()) == _kd0() else []),
                         "tick": krx_tick(cs[-1]["close"]) or 1, "seed": 1,
                         # today's American night gates only TODAY - a stored day
                         # replays calm, or this morning's storm-up would erase
                         # yesterday's 09:0x trades from the record (caught in the
                         # 08-13 pre-open replay: every pre-10:00 buy vanished)
                         "us_mode": (_us_mode_today()
                                     if (d or _kd0()) == _kd0() else "calm"),
                         "prev_close": _gap_ref(c_code, d or _kd0()),
                         "low20": _daily20(c_code, d or _kd0())[1],
                         "low5": _daily20(c_code, d or _kd0())[2],
                         "high5": _week_stats(c_code, d or _kd0())[1],
                         "hz": _hz_stats(c_code, d or _kd0()),
                         "avg5": _week_stats(c_code, d or _kd0())[2],
                         "vol_day_avg": _vol5(c_code, d or _kd0()),
                         "ma20": _daily20(c_code, d or _kd0())[3],
                         "mayr": _daily20(c_code, d or _kd0())[4],
                         "open_vol_med": _open_vol_med(c_code),
                         "daily_pos": _daily_pos(c_code,
                                                 cs[-1]["close"] if cs else 0),
                         "fuel": _fuel(c_code, cs),
                         "news_risk": _news_risk(c_code),
                         # THE LIVING TOP-3 (boss 2026-08-25, menu 2): the
                         # reco desk's entries replay against the recorded
                         # rank timeline; None = no log (gate off)
                         "rank_win": (_rank_win9(c_code, day) if rank_gate
                                      else None),
                         "rank_t0": (_rank_t09(day, c_code) if rank_gate
                                     else None),
                         "times": [c["hhmm"] for c in cs],
                         "vols": [float(c.get("vol") or 0) for c in cs],
                         "ctx": daily_ctx(c_code, d or _kd0()),
                         "gate_ok": (_gate_ok(c_code, d or _kd0()) if use_gate else True),
                         "holes": (_hole_bars(c_code, tick, period)
                                   if not (d or frm or to) else set()),
                         "ml_bundle": (kiwoom_ml_for(c_code, tick, period, v, d)
                                       if v.get("ml") else None)})
        if not stks:
            continue
        got, ops = run_desk(stks, v, evidence=True, with_open=True, fill_fn=_fill)
        for g in got:
            sk = stks[g["si"]]
            cs = sk["cs"]
            b_c, s_c = cs[g["buy_i"]], cs[g["sell_i"]]
            rows.append({
                "code": sk["code"], "name": sk["name"], "buy_i": g["buy_i"], "sell_i": g["sell_i"],
                "buy_t": b_c["hhmm"], "entry": g["entry"],
                "sell_t": s_c["hhmm"], "exit": g["exit"],
                # which stored day this trade belongs to - shown on the cumulative view
                "d8": d, "day": (f"{d[4:6]}-{d[6:]}" if day == "all" and d else ""),
                "gross_pct": g["gross_pct"], "net_pct": g["net_pct"],
                "exit_why": g.get("exit_why", ""),
                "sig": g.get("sig"), "wall": g.get("wall"), "scout": g.get("scout"),
                "judge": g.get("judge"),
                "parts": g.get("parts"),
                # after-fee ruler (boss 2026-08-28 16:0x - same law as the header)
                "result": ("win" if g["net_pct"] > 0 else
                           "loss" if g["net_pct"] < 0 else "flat"),
                "bars_held": g["sell_i"] - g["buy_i"],
                "tick_size": sk["tick"],
                # a drip episode carries its REAL share count (every sizing law
                # applied - storm third, quiet-bar half); only plain rules fall
                # back to the budget display (found 2026-08-19: the board showed
                # full-cap money on a storm-third day)
                "qty": (g.get("qty") if (g.get("parts") and g.get("qty"))
                        else shares_for(g["entry"], budget)),
                # held through a stretch of tape the collector missed - the entry and exit
                # are real, the path between them is unknown
                "spans_hole": any(g["buy_i"] < h <= g["sell_i"] for h in sk["holes"]),
                # the model's decision at the buy - p, the bar it had to clear, and the
                # share count it chose - so the evidence panel can tell the ML story
                # (boss 2026-08-06: "this kind of process need in the rule+ML part")
                "ml": g.get("ml"),
                "buy_ev": g.get("buy_ev"), "sell_ev": g.get("sell_ev"),
            })
        # only the LAST session can still be holding - earlier days are finished
        for op in (ops if d == day_list[-1] else []):
            sk = stks[op["si"]]
            b_c = sk["cs"][op["buy_i"]]
            if op.get("waiting"):
                waiting.append({"code": sk["code"], "name": sk["name"],
                                "t": b_c["hhmm"], "px": op.get("entry"),
                                "qty": op.get("qty_left"),
                                "wall": op.get("wall"), "last": op.get("last")})
                continue
            holding.append({"code": sk["code"], "name": sk["name"], "buy_t": b_c["hhmm"],
                            "entry": op["entry"], "last": op["last"],
                            "sig": op.get("sig"), "wall": op.get("wall"),
                            "judge": op.get("judge"),
                            "chop": op.get("chop"), "parts": op.get("parts"),
                            "base": op.get("base") or op["entry"],
                            # each sold slice with its own time and bar - the boss
                            # wants them as completed rows, provable on the chart
                            "qty_left": op.get("qty_left"),
                            "slices": [[p_, q_, w_, sk["cs"][i_]["hhmm"], i_,
                                        (r_[0] if r_ else None),
                                        (r_[1] if len(r_) > 1 else None)]
                                       for p_, q_, w_, i_, *r_ in (op.get("slices") or [])
                                       if i_ < len(sk["cs"])],
                            "buy_i": op["buy_i"],
                            "unreal_pct": op["unreal_pct"]})
    rows.sort(key=lambda r: (r.get("d8") or "", r["sell_t"]), reverse=True)

    # a clicked trade decides the company; only when nothing is clicked does the stock
    # button decide it
    focus = rows[around] if 0 <= around < len(rows) else None
    want_code = focus["code"] if focus else code

    # ---- second pass: the chart, now that `rows` is in the order the table shows ----
    # on the cumulative view the chart shows ONE day at a time: the day of the clicked
    # trade, else the latest day - a chart of three glued days would lie about time
    chart_day = (focus.get("d8") if focus else "") or (day_list[-1] if day == "all" else day)
    for c_code, name in WATCH:
        cs = _bars_for(c_code, tick, period, chart_day, frm, to)
        if len(cs) < 10:
            continue
        got = [{"buy_i": r["buy_i"], "sell_i": r["sell_i"], "gross_pct": r["gross_pct"],
                "net_pct": r["net_pct"]} for r in rows if r["code"] == c_code
               and (day != "all" or r.get("d8") == chart_day)]
        got.sort(key=lambda g: g["buy_i"])
        if (want_code and c_code == want_code) or (not want_code and chart is None):
            # The window follows the TRADES, not the clock. At 5틱 on a liquid name a bar
            # is a fraction of a second, so "the last 600 bars" is about two minutes —
            # and every trade older than that falls off the left edge, which is how this
            # first came up showing "600 bars, 0 arrows". Anchor on the most recent trade.
            # `around` is a row the boss clicked in the trade table below; it wins, because
            # he asked for that trade. Otherwise anchor on the most recent one.
            # The window is wide (2,500 bars) because a real 5틱 bar on 삼성전자 lasts a
            # fraction of a second — 600 bars was two minutes and showed no arrows at all.
            anchor = (focus["sell_i"] if (focus and focus["code"] == c_code
                      and (day != "all" or focus.get("d8") == chart_day)) else None)
            if anchor is None:
                anchor = got[-1]["sell_i"] if got else len(cs) - 1
            hi = min(len(cs), anchor + max(20, bars // 8))
            off = max(0, hi - bars)
            chart = {"code": c_code, "name": name, "off": off,
                     # where the clicked trade sits in THIS window, so the page can put it
                     # on screen instead of trusting the chart's own remembered view
                     "focus": ({"b": focus["buy_i"] - off, "s": focus["sell_i"] - off}
                               if focus and focus["code"] == c_code
                               and (day != "all" or focus.get("d8") == chart_day)
                               and off <= focus["buy_i"] < hi else None),
                     "candles": cs[off:hi],
                     "marks": ([{"b": g["buy_i"] - off, "s": g["sell_i"] - off,
                                 "g": g["gross_pct"], "net": g["net_pct"]}
                                for g in got if off <= g["buy_i"] < hi and off <= g["sell_i"] < hi]
                               + [{"b": op["buy_i"] - off, "s": len(cs) - 1 - off,
                                   "g": op["unreal_pct"], "net": op["unreal_pct"],
                                   "open": True}
                                  for op in ops
                                  if stks[op["si"]]["code"] == c_code
                                  and off <= op["buy_i"] < hi]
                               + [{"b": i_ - off, "s": i_ - off,
                                   "g": round((p_ / (op.get("base") or op["entry"]) - 1)
                                              * 100, 2),
                                   "net": 0, "part": True}
                                  for op in ops
                                  if stks[op["si"]]["code"] == c_code
                                  for p_, q_, w_, i_, *r_ in (op.get("slices") or [])
                                  if off <= i_ < hi]
                               + [{"b": i_ - off, "s": i_ - off,
                                   "g": round((p_ / r2["entry"] - 1) * 100, 2),
                                   "net": 0, "part": True}
                                  for r2 in rows
                                  if r2.get("code") == c_code
                                  and not r2.get("partial") and r2.get("entry")
                                  for p_, q_, _t2, i_, *r3_ in
                                  ((r2.get("parts") or {}).get("sells") or []
                                   if len(((r2.get("parts") or {}).get("sells")
                                           or [[0, 0]])[0]) >= 4 else [])
                                  if off <= i_ < hi])}

    w = sum(1 for r in rows if r["result"] == "win")
    l = sum(1 for r in rows if r["result"] == "loss")
    _res2 = {"ok": True, "id": vid, "ko": label(v, True), "en": label(v, False),
            "clock": f"{period}초" if period else f"{tick}틱",
            "entry_n": v["entry"], "kind": v["kind"], "a": v["a"], "b": v.get("b"),
            # the rule's full recipe, so the page can EXPLAIN it in either language
            # (boss 2026-08-07: click a rule -> buy/sell conditions + why selected)
            "vol_x": v.get("vol"), "max_run": v.get("max_run"), "take": v.get("take"),
            "is_ml": bool(v.get("ml")),
            # the new way carries its own recipe so the explanation panel can spell out
            # the sharp-drop entry and the riding exit instead of the +N호가 wording
            "family": v.get("family", "old"), "dip": v.get("dip"), "ride": v.get("ride"),
            "take_ticks": v.get("take_ticks"), "stop_pct": v.get("stop_pct"),
            "scout": v.get("scout"), "ladder": v.get("ladder"),
            "drip": v.get("drip"), "us_habit": bool(v.get("us_habit")),
            "rebound": v.get("rebound"), "morning": v.get("morning"),
            "burst": v.get("burst"),
            "wall_price": bool(v.get("wall_price") or v.get("family") == "new"),
            "exact_entry": bool(v.get("exact_entry")),
            "dir": v.get("dir", 1),
            "trips": len(rows), "wins": w, "losses": l, "flats": len(rows) - w - l,
            "win_pct": round(w / (w + l) * 100) if (w + l) else 0,
            # the same honesty the Strategy Lab now carries: a flat is neither a win nor a
            # loss and is NOT in the percentage, so the denominator has to be on screen or
            # "2 trips ... 100%" reads as two wins (boss 2026-08-04)
            "decided": w + l, "thin": (w + l) < 10,
            # how many of these cannot be judged: the collector was down while they
            # were open, so a stop that should have fired during the gap may not have
            "spanning_hole": sum(1 for r in rows if r.get("spans_hole")),
            # THE MONEY. Summed over EVERY trade, not the page's slice - `trades` is
            # cut to `limit`, so a total added up on screen would quietly under-report a
            # rule with more trades than fit. Net is after the round-trip fee.
            # THE MONEY IN WON. Percent answers "how well", won answers "how much", and
            # the boss asked for how much. One share per signal: there is no position size
            # anywhere in this system, so a share is the only honest unit - and it is the
            # same unit the `diff` column beside it already uses.
            "budget": budget,
            "net_won_total": round(sum(r["entry"] * r["net_pct"] / 100 for r in rows)),
            # THE SAME TRADES AT THE CHOSEN SIZE. P&L is linear in quantity, so this is
            # exactly the one-share figure scaled per stock - which is the point: it shows
            # that size changes the MAGNITUDE and never the sign.
            "net_won_sized": round(sum(r["qty"] * r["entry"] * r["net_pct"] / 100
                                       for r in rows)),
            "shares_total": sum(r["qty"] for r in rows),
            "capital_used": round(sum(r["qty"] * r["entry"] for r in rows)),
            "per_trade_won": (round(sum(r["entry"] * r["net_pct"] / 100 for r in rows) / len(rows))
                              if rows else 0),
            "net_total": round(sum(r["net_pct"] for r in rows), 2),
            "gross_total": round(sum(r["gross_pct"] for r in rows), 2),
            "per_trade": round(sum(r["net_pct"] for r in rows) / len(rows), 3) if rows else 0.0,
            "trades": rows[:limit], "shown": min(len(rows), limit),
            "holding": holding, "waiting": waiting, "chart": chart, "fee_pct": FEE_PCT}
    _TRADES_TTL[_tk2] = (_t.time(), _res2)
    return _res2
