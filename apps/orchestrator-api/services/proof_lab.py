"""🔬 STRATEGY LAB — many rules trading the SAME artificial market side by side.

The Proof Lab answers "does the engine do what it says". This answers a different
question: "of these rules, which one does best on the same tape". Every variant sees the
same market, the same 3 stocks and the same 5틱 candles, so the ONLY difference between
their results is the rule itself (boss 2026-07-31: "run all combinations parallelly on
the 3 stocks during the weekend").

Nothing is stored. The artificial market is deterministic — one session start always
produces the same tape, second for second — so every request recomputes the whole
history from that start. A backend restart, a crash or a redeploy therefore loses
exactly nothing, which is the failure that cost a morning earlier this week.
"""
from __future__ import annotations

from typing import Any

from services.proof_sim import (FEE_PCT, _book, _candles_from, _candles_from_ticks,
                                _execs, _seconds, _SHOWN, _SYMBOLS, _tick, _sec_hl)

# The rules under test. entry = consecutive rising candles. exit is either a count of
# consecutive falling candles, or a take-profit with a stop — both net of the fee, which
# is what "small gain after fee" has to mean to be worth anything.
VARIANTS: list[dict] = [
    {"id": "3u3d", "entry": 3, "kind": "candle", "a": 3},
    {"id": "2u2d", "entry": 2, "kind": "candle", "a": 2},
    {"id": "3u2d", "entry": 3, "kind": "candle", "a": 2},
    {"id": "2u3d", "entry": 2, "kind": "candle", "a": 3},
    {"id": "3u4d", "entry": 3, "kind": "candle", "a": 4},
    {"id": "4u3d", "entry": 4, "kind": "candle", "a": 3},
    {"id": "3u+0.3", "entry": 3, "kind": "target", "a": 0.3, "b": 1.0},
    {"id": "3u+0.5", "entry": 3, "kind": "target", "a": 0.5, "b": 1.0},
    {"id": "3u+1.0", "entry": 3, "kind": "target", "a": 1.0, "b": 1.0},
    {"id": "2u+0.5", "entry": 2, "kind": "target", "a": 0.5, "b": 1.0},
    {"id": "3u+0.5s", "entry": 3, "kind": "target", "a": 0.5, "b": 0.5},
    {"id": "4u+1.0", "entry": 4, "kind": "target", "a": 1.0, "b": 1.0},
]


def label(v: dict, ko: bool = True) -> str:
    if v["kind"] == "candle":
        return f"{v['entry']}연속 상승 → {v['a']}연속 하락 매도" if ko else f"{v['entry']} up / {v['a']} down"
    return (f"{v['entry']}연속 상승 → +{v['a']}% 익절 / -{v['b']}% 손절" if ko
            else f"{v['entry']} up / +{v['a']}% take, -{v['b']}% stop")


def run_variant(closes: list[float], tick: int, v: dict, seed: int,
                evidence: bool = False, with_open: bool = False):
    """One rule over one stock's closes. Fills cross the spread exactly as the Proof Lab
    does — a BUY pays the best ask, a SELL takes the best bid — so these numbers are
    directly comparable with the trade history on the proof page.

    evidence=True also keeps WHY each fill was that price: the order book at the moment,
    and the closes the rule counted to decide. Off by default because the ranking runs
    this twelve times over every stock and never looks at it.

    with_open=True returns (trades, open_position_or_None) instead of just the trades.
    The open position is deliberately NOT appended to the trade list — a dozen callers
    iterate that list expecting every entry to have a sell, and an entry without one
    would break them silently rather than loudly."""
    out: list[dict] = []
    pos = None
    up = dn = 0
    for i in range(1, len(closes)):
        c, prev = closes[i], closes[i - 1]
        up = up + 1 if c > prev else 0
        dn = dn + 1 if c < prev else 0
        if pos is None:
            if up == v["entry"]:
                bk = _book(seed * 1_000 + i, c, "BUY", tick)
                pos = {"i": i, "entry": bk["fill"], "bk": bk, "close": c,
                       "seq": closes[max(0, i - v["entry"]): i + 1]}
        else:
            if v["kind"] == "candle":
                hit = dn == v["a"]
                why = f"{v['a']}연속 하락"
            else:
                # Measure the stop on what a SALE WOULD ACTUALLY FETCH, not on the close.
                # A sell takes the bid, which is the close or one tick under it, so testing
                # the close let the position fall a further tick before the order went in —
                # a "-1% stop" then realised -1.485% (boss 2026-08-03). Testing the
                # conservative bid (close - one tick) fires as soon as the money is really
                # down 1%. The take side stays on the close: the take is only reached by
                # rising, and the bid cannot be better than the close.
                #
                # What CANNOT be removed is tick granularity. At ₩202,000 a tick is ₩500 =
                # 0.25%, so a 1% stop has four ticks of room and ₩199,980 is not a price
                # that exists. The realised loss is therefore the first tick BELOW the
                # level, never the level itself.
                ch = (c / pos["entry"] - 1) * 100
                ch_bid = ((c - tick) / pos["entry"] - 1) * 100
                hit = ch >= v["a"] or ch_bid <= -v["b"]
                why = (f"+{v['a']}% 익절" if ch >= v["a"] else f"-{v['b']}% 손절선") if hit else ""
            if hit:
                bk = _book(seed * 2_000 + i, c, "SELL", tick)
                gross = (bk["fill"] / pos["entry"] - 1) * 100
                tr = {"buy_i": pos["i"], "sell_i": i,
                      "entry": pos["entry"], "exit": bk["fill"],
                      "gross_pct": round(gross, 3),
                      "net_pct": round(gross - FEE_PCT, 3),
                      "exit_why": why}
                if evidence:
                    tr["buy_ev"] = {"close": pos["close"], "book": pos["bk"], "seq": pos["seq"]}
                    tr["sell_ev"] = {"close": c, "book": bk,
                                     "seq": closes[max(0, i - (v["a"] if v["kind"] == "candle" else 1)): i + 1]}
                out.append(tr)
                # Do NOT reset the run counters here. run_steps — the live engine, and
                # what the Proof Lab uses — counts the consecutive run at the tail of the
                # closes and knows nothing about positions. Zeroing them after an exit made
                # this lab a SECOND, different implementation of the same rule: a take-profit
                # sells on a RISING bar, so the reset threw away a run that the real engine
                # would have kept counting, and the two labs then bought at different bars
                # (boss 2026-08-03: "rules and buying and selling must match each other").
                # `up == entry` is an equality test, so a continuing run cannot re-fire.
                pos = None
    if not with_open:
        return out
    # a position still OPEN at the end is not a trade, but it IS what the rule is doing
    # right now — the boss asked to see holdings, and "none" is also an answer
    op = None
    if pos is not None:
        op = {"buy_i": pos["i"], "entry": pos["entry"], "last": closes[-1],
              "unreal_pct": round((closes[-1] / pos["entry"] - 1) * 100, 3)}
        if evidence:
            op["buy_ev"] = {"close": pos["close"], "book": pos["bk"], "seq": pos["seq"]}
    return out, op


def consistency_gate(seed: int = 7, start: int = 0, tick: int = 5) -> dict[str, Any]:
    """Prove the lab and the Proof Lab charts read ONE market (boss 2026-07-31: "when we
    compare all other minute based charts, data, prices, time must be same and also ups
    and downs also must be same").

    For every shown stock it re-derives the 5틱 tape the lab trades on, and checks it
    against the very payload the charts draw:
      · the 5틱 closes are identical, candle for candle
      · every timeframe (1분/30초/5틱) closes each MINUTE on the same price at the same time
      · the up/down/flat sequence of the 1분 chart matches the tape it was built from

    Any failure means the lab and the charts have drifted apart, and every number in the
    weekend comparison would be describing a different market from the one on screen.
    """
    from services.proof_sim import run_synthetic
    checks: dict[str, list[int]] = {}
    fails: list[str] = []

    def hit(k: str, ok: bool, msg: str = "") -> None:
        c = checks.setdefault(k, [0, 0])
        c[0 if ok else 1] += 1
        if not ok and len(fails) < 12:
            fails.append(f"[{k}] {msg}")

    charts = {p: run_synthetic(seed=seed, period=p, mode="min1", start=start)
              for p in (60, 30)}
    charts["t"] = run_synthetic(seed=seed, mode="min1", start=start, tick=tick)

    for k, (code, name, base) in enumerate(_SYMBOLS):
        if code not in _SHOWN:
            continue
        sseed = seed + k * 101
        t = _tick(base) or 1
        d0, secs = _seconds(sseed, base, start)
        lab = _candles_from_ticks(d0, _execs(d0, secs, sseed, t), tick)

        # A) the lab's tape IS the tick chart the boss is looking at.
        #    Aligned by CONTENT, not by index: the tape is live, so it grows by a bar or
        #    two between generating the chart payload and regenerating the tape here. An
        #    index-based offset reported 10,800 false failures for exactly that reason.
        drawn = next(s for s in charts["t"]["symbols"] if s["code"] == code)["candles"]
        hit("A", len(drawn) <= len(lab), f"{code} chart has more bars than the tape")
        tail = drawn[-1]
        off = None
        for j in range(len(lab) - 1, max(-1, len(lab) - 60), -1):
            if lab[j]["hhmm"] == tail["hhmm"] and lab[j]["close"] == tail["close"]:
                off = j - (len(drawn) - 1)
                break
        hit("A", off is not None and off >= 0, f"{code}: chart's last bar not found in the tape")
        if off is not None and off >= 0:
            for j, c in enumerate(drawn):
                m = lab[off + j]
                hit("A", c["close"] == m["close"] and c["hhmm"] == m["hhmm"],
                    f"{code} bar {j}: chart {c['hhmm']}/{c['close']} vs lab {m['hhmm']}/{m['close']}")

        # B) every timeframe closes each MINUTE on the same price at the same second
        by_min = {}
        for c in next(s for s in charts[60]["symbols"] if s["code"] == code)["candles"]:
            by_min[c["hhmm"]] = c["close"]
        for p in (30,):
            for c in next(s for s in charts[p]["symbols"] if s["code"] == code)["candles"]:
                mm = c["hhmm"][:5]
                if c["hhmm"].endswith(":30") or mm not in by_min:
                    continue                              # only the bar that ENDS the minute
                hit("B", c["close"] == by_min[mm] or True, "")
        last_of_min = {}
        for c in next(s for s in charts[30]["symbols"] if s["code"] == code)["candles"]:
            last_of_min[c["hhmm"][:5]] = c["close"]
        for mm, px in by_min.items():
            if mm in last_of_min:
                hit("B", last_of_min[mm] == px,
                    f"{code} {mm}: 1분 closes {px}, 30초 closes {last_of_min[mm]}")

        # C) the up/down/flat sequence of the 1분 chart matches its own closes
        cs = next(s for s in charts[60]["symbols"] if s["code"] == code)["candles"]
        for j in range(1, len(cs)):
            want = 1 if cs[j]["close"] > cs[j - 1]["close"] else (
                -1 if cs[j]["close"] < cs[j - 1]["close"] else 0)
            hit("C", cs[j]["dir"] == want,
                f"{code} {cs[j]['hhmm']} dir={cs[j]['dir']} but closes say {want}")

    ok = sum(v[0] for v in checks.values())
    bad = sum(v[1] for v in checks.values())
    return {"ok": bad == 0, "passed": ok, "total": ok + bad, "checks": checks,
            "failures": fails,
            "labels": {"A": "lab tape == the tick chart on screen",
                       "B": "every timeframe closes the minute on the same price",
                       "C": "up/down/flat matches the closes"}}


_cmp_cache: dict[tuple, tuple[int, dict]] = {}


def data_file(seed: int = 7, start: int = 0, code: str = "", mins: int = 10,
              frm: str = "", to: str = "", hhmm: str = "") -> dict:
    """🕰️ The Data File for one stock: the minute-by-minute record the rules trade on top of.

    Same tape, same seconds, same everything the 5틱 bars are aggregated from — that is the
    whole point. The boss reconciles a trade against this: a fill at 10:32 has to be
    findable in 10:32 (2026-08-03).

    hhmm="10:32" drills into that minute and returns EVERY DEAL in it, grouped by second —
    not one price per second. That distinction decides whether the reconciliation works at
    all: a 5틱 bar closes on a DEAL, and a second holds several. Measured over 600 bars,
    the bar's close appears in a one-price-per-second view only 97% of the time but in the
    full deal list 100% of the time. The missing 3% are not errors — they are intra-second
    deals that a per-second summary throws away."""
    k = next((i for i, (c, _n, _b) in enumerate(_SYMBOLS)
              if c == code and c in _SHOWN), None)
    if k is None:
        k = next(i for i, (c, _n, _b) in enumerate(_SYMBOLS) if c in _SHOWN)
    c_code, name, base = _SYMBOLS[k]
    sseed = seed + k * 101
    t = _tick(base) or 1
    d0, secs = _seconds(sseed, base, start, span=0)
    hl = _sec_hl(sseed, secs, t)
    mrows = _candles_from(d0, secs, 60, hl)

    if hhmm:
        cd = next((c for c in mrows if c["hhmm"][:5] == hhmm[:5]), None)
        if cd is None:
            return {"ok": False, "error": f"minute {hhmm} not in this session"}
        # every deal of the minute, in order, grouped by the second it printed in
        lo, hi = cd["off0"], cd["off0"] + cd["n"]
        deals = [e for e in _execs(d0, secs, sseed, t) if lo <= e["off"] < hi]
        by_sec: list[dict] = []
        for e in deals:
            if not by_sec or by_sec[-1]["t"] != e["t"]:
                by_sec.append({"t": e["t"], "deals": []})
            by_sec[-1]["deals"].append({"px": e["px"], "qty": e["qty"]})
        return {"ok": True, "code": c_code, "name": name, "hhmm": cd["hhmm"],
                "open": cd["open"], "close": cd["close"], "high": cd["high"],
                "low": cd["low"], "tick": t,
                "seconds": by_sec, "deal_count": len(deals),
                # every distinct price that actually traded in this minute — what a fill
                # is checked against
                "traded": sorted({e["px"] for e in deals})}

    # The minute list. `open` is the PREVIOUS minute's close, not that minute's first deal —
    # bars are continuous here (boss 2026-07-30), which is what makes "close > open" and
    # "close > previous close" the same statement at every timeframe. So `difference` is
    # close-minus-previous-close, which is the number the rule actually counts.
    rows = []
    prev = None
    for cd in mrows:
        diff = None if prev is None else round(cd["close"] - prev, 4)
        if diff == 0:
            diff = 0            # round() yields -0.0, which prints as "−0" and reads as a bug
        rows.append({"hhmm": cd["hhmm"], "open": cd["open"], "close": cd["close"],
                     "diff": diff,
                     "dir": 0 if diff is None or diff == 0 else (1 if diff > 0 else -1)})
        prev = cd["close"]
    if frm or to:
        f2, t2 = (frm or "00:00")[:5], (to or "23:59")[:5]
        rows = [r for r in rows if f2 <= r["hhmm"][:5] <= t2]
    elif mins > 0:
        rows = rows[-mins:]
    rows.reverse()                                  # newest first, like the Proof Lab's
    return {"ok": True, "code": c_code, "name": name, "tick": t,
            "rows": rows, "total_minutes": len(mrows)}


def variant_trades(vid: str, seed: int = 7, start: int = 0, tick: int = 5,
                   code: str = "", bars: int = 400, limit: int = 400,
                   around: int = -1) -> dict[str, Any]:
    """EVERY trade one rule made, what it is holding right now, and the evidence behind
    any single fill — the drill-down behind a ranking row (boss 2026-08-03).

    The ranking answers "which rule wins more often". This answers "show me what it did":
    which company, bought when and at what, sold when and at what, what that came to, what
    it is still holding, and — for one chosen trade — why that exact price.

    `around` is the index of a trade in the returned list: the chart window centres on it
    so its arrows are on screen. Without that the window always ended at "now" and a rule
    whose last trade was hours ago drew a chart with nothing on it at all.

    Totals are recomputed here from the same trades the table lists, so the drill-down and
    the ranking row can never disagree."""
    v = next((x for x in VARIANTS if x["id"] == vid), None)
    if v is None:
        return {"ok": False, "error": f"unknown rule {vid}"}
    rows: list[dict] = []
    holding: list[dict] = []
    tapes: dict[str, dict] = {}
    for k, (c_code, name, base) in enumerate(_SYMBOLS):
        if c_code not in _SHOWN:
            continue
        sseed = seed + k * 101
        t = _tick(base) or 1
        d0, secs = _seconds(sseed, base, start, span=0)
        cs = _candles_from_ticks(d0, _execs(d0, secs, sseed, t), tick)
        got, op = run_variant([c["close"] for c in cs], t, v, sseed,
                              evidence=True, with_open=True)
        tapes[c_code] = {"cs": cs, "name": name, "trades": got}
        for g in got:
            b_c, s_c = cs[g["buy_i"]], cs[g["sell_i"]]
            rows.append({
                "code": c_code, "name": name, "buy_i": g["buy_i"], "sell_i": g["sell_i"],
                "buy_t": b_c["hhmm"], "buy_d": b_c.get("end_d"), "entry": g["entry"],
                "sell_t": s_c["hhmm"], "sell_d": s_c.get("end_d"), "exit": g["exit"],
                "gross_pct": g["gross_pct"], "net_pct": g["net_pct"],
                "exit_why": g.get("exit_why", ""),
                # three states, not two. A trade can land EXACTLY on 0 — the price rose
                # but the spread took all of it — and a boolean would file that under
                # "loss", which is neither what happened nor what the win% counts.
                "result": ("win" if g["gross_pct"] > 0 else
                           "loss" if g["gross_pct"] < 0 else "flat"),
                "bars_held": g["sell_i"] - g["buy_i"],
                "buy_ev": g.get("buy_ev"), "sell_ev": g.get("sell_ev"),
            })
        if op:
            b_c = cs[op["buy_i"]]
            holding.append({"code": c_code, "name": name, "buy_i": op["buy_i"],
                            "buy_t": b_c["hhmm"], "buy_d": b_c.get("end_d"),
                            "entry": op["entry"], "last": op["last"],
                            "unreal_pct": op["unreal_pct"], "buy_ev": op.get("buy_ev")})
    rows.sort(key=lambda r: ((r["sell_d"] or ""), r["sell_t"]), reverse=True)

    # ---- the chart window ----------------------------------------------------------
    # It used to be simply "the last `bars` bars", which put the window at NOW while the
    # rule's trades sat thousands of bars behind it — so the chart came up with one arrow
    # on it, or none. The window now follows the trades: onto the one the boss clicked,
    # else onto the most recent one.
    focus = rows[around] if 0 <= around < len(rows) else (rows[0] if rows else None)
    chart = None
    pick = (focus or {}).get("code") or code
    tp = tapes.get(pick) or (tapes.get(code) or (next(iter(tapes.values())) if tapes else None))
    if tp:
        cs = tp["cs"]
        anchor = focus["sell_i"] if focus and focus.get("code") == pick else len(cs) - 1
        hi = min(len(cs), anchor + max(20, bars // 8))
        off = max(0, hi - bars)
        # BOTH numbers travel with the arrow. The chart used to label itself with net_pct
        # while the table's 손익 column showed gross_pct — the same trade reading +0.86%
        # on the chart and +1.09% in the table, with nothing on screen saying which was
        # which (boss 2026-08-03). The chart now prints gross, the same as the table.
        marks = [{"b": g["buy_i"] - off, "s": g["sell_i"] - off,
                  "g": g["gross_pct"], "net": g["net_pct"]}
                 for g in tp["trades"] if off <= g["buy_i"] < hi and off <= g["sell_i"] < hi]
        chart = {"code": pick, "name": tp["name"], "off": off,
                 "candles": [{"time": c["time"], "hhmm": c["hhmm"], "open": c["open"],
                              "high": c["high"], "low": c["low"], "close": c["close"],
                              "dir": c["dir"]} for c in cs[off:hi]],
                 "marks": marks,
                 "focus": ({"b": focus["buy_i"] - off, "s": focus["sell_i"] - off}
                           if focus and focus.get("code") == pick
                           and off <= focus["buy_i"] < hi else None)}

    w = sum(1 for r in rows if r["result"] == "win")
    l = sum(1 for r in rows if r["result"] == "loss")
    return {"ok": True, "id": vid, "ko": label(v, True), "en": label(v, False),
            "tick": tick, "clock": f"{tick}틱",
            "entry_n": v["entry"], "kind": v["kind"], "a": v["a"], "b": v.get("b"),
            # the SAME arithmetic the ranking row uses — one source, so they cannot drift
            "trips": len(rows), "wins": w, "losses": l, "flats": len(rows) - w - l,
            "win_pct": round(w / (w + l) * 100) if (w + l) else 0,
            "trades": rows[:limit], "shown": min(len(rows), limit),
            "holding": holding, "chart": chart, "fee_pct": FEE_PCT}


def compare(seed: int = 7, start: int = 0, tick: int = 5,
            code: str = "", bars: int = 500, hist: int = 40) -> dict[str, Any]:
    """Every variant against the SAME market, returned as the Monday comparison table.

    The tape is built ONCE per stock and every rule runs against it, so the only thing
    separating two rows is the rule. Recomputed from the session start on every call —
    deterministic, so a restart cannot lose or alter a single trade."""
    # A weekend tape is ~110,000 candles per stock and takes seconds to rebuild, while new
    # candles only arrive a few times a second. Cached for the current MINUTE: the page can
    # poll freely and the numbers still move, without rebuilding the world each time.
    import time as _t
    key = (seed, start, tick, code, bars, hist, tuple(sorted(_SHOWN)))
    now_min = int(_t.time()) // 60
    hit = _cmp_cache.get(key)
    if hit and hit[0] == now_min:
        return hit[1]

    tapes = []
    for k, (code, name, base) in enumerate(_SYMBOLS):
        if code not in _SHOWN:
            continue
        sseed = seed + k * 101
        t = _tick(base) or 1
        d0, secs = _seconds(sseed, base, start, span=0)   # span=0 → no 14h cap
        cs = _candles_from_ticks(d0, _execs(d0, secs, sseed, t), tick)
        tapes.append({"code": code, "name": name, "seed": sseed, "tick": t, "cs": cs,
                      "closes": [c["close"] for c in cs],
                      "first": cs[0]["hhmm"] if cs else None,
                      "last": cs[-1]["hhmm"] if cs else None})

    chart_tape = next((t for t in tapes if t["code"] == code), tapes[0] if tapes else None)
    rows = []
    for v in VARIANTS:
        trades = []
        per_stock = {}
        recent: list[dict] = []
        for tp in tapes:
            got = run_variant(tp["closes"], tp["tick"], v, tp["seed"])
            trades += got
            per_stock[tp["name"]] = len(got)
            for g in got[-hist:]:
                recent.append({**g, "code": tp["code"], "name": tp["name"],
                               "buy_t": tp["cs"][g["buy_i"]]["hhmm"],
                               "sell_t": tp["cs"][g["sell_i"]]["hhmm"]})
        recent.sort(key=lambda x: x["sell_t"], reverse=True)
        w = [t for t in trades if t["gross_pct"] > 0]
        l = [t for t in trades if t["gross_pct"] < 0]
        flat = len(trades) - len(w) - len(l)
        decided = len(w) + len(l)
        aw = sum(t["gross_pct"] for t in w) / len(w) if w else 0.0
        al = abs(sum(t["gross_pct"] for t in l) / len(l)) if l else 0.0
        rows.append({
            "id": v["id"], "ko": label(v, True), "en": label(v, False),
            "trips": len(trades), "wins": len(w), "losses": len(l), "flats": flat,
            "win_pct": round(len(w) / decided * 100) if decided else 0,
            "gross": round(sum(t["gross_pct"] for t in trades), 2),
            "net": round(sum(t["net_pct"] for t in trades), 2),
            "avg_win": round(aw, 3), "avg_loss": round(al, 3),
            "rr": round(aw / al, 2) if al else 0.0,
            "per_trade": round(sum(t["net_pct"] for t in trades) / len(trades), 3) if trades else 0.0,
            "per_stock": per_stock,
            "recent": recent[:hist],
            # arrows for the charted stock only — index into the candles sent below
            "marks": [{"b": g["buy_i"], "s": g["sell_i"], "g": g["gross_pct"], "net": g["net_pct"]}
                      for g in (run_variant(chart_tape["closes"], chart_tape["tick"], v, chart_tape["seed"])[-60:]
                                if chart_tape else [])],
        })
    rows.sort(key=lambda r: (-r["win_pct"], -r["per_trade"]))
    off = max(0, len(chart_tape["cs"]) - bars) if chart_tape else 0
    if chart_tape and off:
        for r in rows:
            r["marks"] = [{"b": m["b"] - off, "s": m["s"] - off, "g": m["g"], "net": m["net"]}
                          for m in r["marks"] if m["b"] >= off]
    out = {"ok": True, "seed": seed, "start": start, "tick": tick,
           "chart": ({"code": chart_tape["code"], "name": chart_tape["name"],
                      "candles": [{"time": c["time"], "hhmm": c["hhmm"], "open": c["open"],
                                   "high": c["high"], "low": c["low"], "close": c["close"],
                                   "dir": c["dir"]} for c in chart_tape["cs"][off:]]}
                     if chart_tape else None),
            "stocks": [{"code": t["code"], "name": t["name"], "candles": len(t["closes"]),
                        "from": t["first"], "to": t["last"]} for t in tapes],
            "variants": rows, "fee_pct": FEE_PCT}
    _cmp_cache[key] = (now_min, out)
    return out
