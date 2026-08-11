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
ML_RULES = [v for v in VARIANTS if v.get("ml") and v.get("exec") == "limit"]
DESK = PLAIN + ML_RULES

_KML_CACHE: dict = {}
_CTX_CACHE: dict = {}


def _gate_ok(code: str, day: str) -> bool:
    """Was this stock cleared to trade on this day? (services/daily_gate)"""
    try:
        from services.daily_gate import gate
        return bool(gate(code, day).get("go", True))
    except Exception:
        return True                      # fail open - never stop the desk on an error


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
         allow_fallback: bool = True) -> dict[str, Any]:
    """Every plain rule over the real tape of every watched stock, ranked.

    day="all" is the CUMULATIVE board (boss 2026-08-06: "total result up to today"):
    every stored day is run separately - each day is its own session, positions never
    span the overnight gap, and each day's ML models are the ones that day actually had
    (trained only on the days before it) - then the trades are added up."""
    import time as _t
    _rk = (tick, period, day, frm, to, use_gate, allow_fallback)
    _hit = _RANK_TTL.get(_rk)
    if _hit and _t.time() - _hit[0] < 20.0:
        return _hit[1]         # the page polls every 3s; identical answers are reused
    day, auto_day = _auto_day(day) if allow_fallback else (day, False)
    day_list = stored_days() if day == "all" else [day]
    tapes_by_day = []
    for d in day_list:
        tapes = {}
        for code, name in WATCH:
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
                         "tick": tp["tk"], "seed": 1,
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


def trades(vid: str, tick: int = 5, period: int = 0, code: str = "",
           bars: int = 2500, limit: int = 300, around: int = -1,
           budget: int = 0, day: str = "", frm: str = "", to: str = "",
           use_gate: bool = True, allow_fallback: bool = True) -> dict[str, Any]:
    """One rule's trades on the real tape, with the chart and the evidence per trade."""
    v = next((x for x in DESK if x["id"] == vid), None)
    if v is None:
        return {"ok": False, "error": f"unknown rule {vid}"}
    import time as _t
    day, _auto = _auto_day(day) if allow_fallback else (day, False)
    _tk2 = (vid, tick, period, code, bars, limit, around, budget, day, frm, to, use_gate)
    _hit2 = _TRADES_TTL.get(_tk2)
    if _hit2 and _t.time() - _hit2[0] < 20.0:
        return _hit2[1]

    # Two passes: collect every trade first, sort them, THEN build the chart — the chart
    # needs to look up `around` in the SAME order the table displays, and that order is
    # not known until every stock has been walked.
    rows, holding, chart = [], [], None
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
    for d in day_list:
        stks = []
        for c_code, name in WATCH:
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
                         "tick": krx_tick(cs[-1]["close"]) or 1, "seed": 1,
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
        got, op = run_desk(stks, v, evidence=True, with_open=True, fill_fn=_fill)
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
                "sig": g.get("sig"), "wall": g.get("wall"),
                "result": ("win" if g["gross_pct"] > 0 else
                           "loss" if g["gross_pct"] < 0 else "flat"),
                "bars_held": g["sell_i"] - g["buy_i"],
                "tick_size": sk["tick"],
                # shares this trade would buy for the chosen budget (1 when none is set)
                "qty": shares_for(g["entry"], budget),
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
        if op and d == day_list[-1]:
            sk = stks[op["si"]]
            b_c = sk["cs"][op["buy_i"]]
            holding.append({"code": sk["code"], "name": sk["name"], "buy_t": b_c["hhmm"],
                            "entry": op["entry"], "last": op["last"],
                            "sig": op.get("sig"), "wall": op.get("wall"),
                            "chop": op.get("chop"),
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
                               + ([{"b": op["buy_i"] - off, "s": len(cs) - 1 - off,
                                    "g": op["unreal_pct"], "net": op["unreal_pct"],
                                    "open": True}]
                                  if op is not None and stks[op["si"]]["code"] == c_code
                                  and off <= op["buy_i"] < hi else []))}

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
            "holding": holding, "chart": chart, "fee_pct": FEE_PCT}
    _TRADES_TTL[_tk2] = (_t.time(), _res2)
    return _res2
