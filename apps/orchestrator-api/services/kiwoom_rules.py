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
from services.proof_lab import FEE_PCT, VARIANTS, label, run_variant
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


def _bars_for(code: str, tick: int, period: int) -> list[dict]:
    ticks = load(code)
    if not ticks:
        return []
    return bars_time(ticks, period) if period else bars_ticks(ticks, max(1, tick))


# THE TWELVE. Exactly the rules the boss has been testing since the start — entries on a
# run of RISES. The six reversal rules (buy after FALLS) live on the artificial side only:
# they were mine, they turned this desk into a different experiment from yesterday's, and
# he asked twice for them gone rather than filtered (2026-08-04). They are not hidden here,
# they are not ranked here, and nothing on this desk computes them.
#
# They still exist in the Strategy Lab, where they were introduced and where the
# comparison they belong to is being run.
PLAIN = [v for v in VARIANTS if not v.get("ml") and v.get("dir", 1) > 0]
ORIGINAL_12 = [v["id"] for v in PLAIN]


def rank(tick: int = 5, period: int = 0) -> dict[str, Any]:
    """Every plain rule over the real tape of every watched stock, ranked."""
    tapes = {}
    for code, name in WATCH:
        cs = _bars_for(code, tick, period)
        if len(cs) < 10:
            continue
        tapes[code] = {"name": name, "cs": cs, "tk": krx_tick(cs[-1]["close"]) or 1}

    rows = []
    for v in PLAIN:
        trades = []
        for code, tp in tapes.items():
            got = run_variant([c["close"] for c in tp["cs"]], tp["tk"], v, 1,
                              fill_fn=_fill)
            trades += got
        w = sum(1 for t in trades if t["gross_pct"] > 0)
        l = sum(1 for t in trades if t["gross_pct"] < 0)
        rows.append({
            "id": v["id"], "ko": label(v, True), "en": label(v, False),
            # +1 = the original twelve (buy after RISES), -1 = the six reversal rules
            "dir": v.get("dir", 1),
            "trips": len(trades), "wins": w, "losses": l,
            "flats": len(trades) - w - l,
            "win_pct": round(w / (w + l) * 100) if (w + l) else 0,
            "net": round(sum(t["net_pct"] for t in trades), 2),
            "per_trade": (round(sum(t["net_pct"] for t in trades) / len(trades), 3)
                          if trades else 0.0),
            # fewer than this many DECIDED trades and a win rate is a coin that landed a
            # few times. Counted on wins+losses, not trips: eight flats and two wins is a
            # "100%" carried by two trades.
            "decided": w + l, "thin": (w + l) < 10,
        })
    rows.sort(key=lambda r: (r["thin"], -r["win_pct"], -r["trips"]))
    return {"ok": True, "original_12": ORIGINAL_12, "clock": f"{period}초" if period else f"{tick}틱",
            "tick": tick, "period": period, "fee_pct": FEE_PCT,
            "stocks": [{"code": c, "name": t["name"], "bars": len(t["cs"]),
                        "from": t["cs"][0]["hhmm"], "to": t["cs"][-1]["hhmm"],
                        "tick_size": t["tk"]} for c, t in tapes.items()],
            "variants": rows}


def trades(vid: str, tick: int = 5, period: int = 0, code: str = "",
           bars: int = 2500, limit: int = 300, around: int = -1) -> dict[str, Any]:
    """One rule's trades on the real tape, with the chart and the evidence per trade."""
    v = next((x for x in PLAIN if x["id"] == vid), None)
    if v is None:
        return {"ok": False, "error": f"unknown rule {vid}"}

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
    for c_code, name in WATCH:
        cs = _bars_for(c_code, tick, period)
        if len(cs) < 10:
            continue
        tk = krx_tick(cs[-1]["close"]) or 1
        got, op = run_variant([c["close"] for c in cs], tk, v, 1,
                              evidence=True, with_open=True, fill_fn=_fill)
        for g in got:
            b_c, s_c = cs[g["buy_i"]], cs[g["sell_i"]]
            rows.append({
                "code": c_code, "name": name, "buy_i": g["buy_i"], "sell_i": g["sell_i"],
                "buy_t": b_c["hhmm"], "entry": g["entry"],
                "sell_t": s_c["hhmm"], "exit": g["exit"],
                "gross_pct": g["gross_pct"], "net_pct": g["net_pct"],
                "exit_why": g.get("exit_why", ""),
                "result": ("win" if g["gross_pct"] > 0 else
                           "loss" if g["gross_pct"] < 0 else "flat"),
                "bars_held": g["sell_i"] - g["buy_i"],
                "tick_size": tk,
                "buy_ev": g.get("buy_ev"), "sell_ev": g.get("sell_ev"),
            })
        if op:
            b_c = cs[op["buy_i"]]
            holding.append({"code": c_code, "name": name, "buy_t": b_c["hhmm"],
                            "entry": op["entry"], "last": op["last"],
                            "unreal_pct": op["unreal_pct"]})
    rows.sort(key=lambda r: r["sell_t"], reverse=True)

    # a clicked trade decides the company; only when nothing is clicked does the stock
    # button decide it
    focus = rows[around] if 0 <= around < len(rows) else None
    want_code = focus["code"] if focus else code

    # ---- second pass: the chart, now that `rows` is in the order the table shows ----
    for c_code, name in WATCH:
        cs = _bars_for(c_code, tick, period)
        if len(cs) < 10:
            continue
        got = [{"buy_i": r["buy_i"], "sell_i": r["sell_i"], "gross_pct": r["gross_pct"],
                "net_pct": r["net_pct"]} for r in rows if r["code"] == c_code]
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
            anchor = focus["sell_i"] if (focus and focus["code"] == c_code) else None
            if anchor is None:
                anchor = got[-1]["sell_i"] if got else len(cs) - 1
            hi = min(len(cs), anchor + max(20, bars // 8))
            off = max(0, hi - bars)
            chart = {"code": c_code, "name": name, "off": off,
                     # where the clicked trade sits in THIS window, so the page can put it
                     # on screen instead of trusting the chart's own remembered view
                     "focus": ({"b": focus["buy_i"] - off, "s": focus["sell_i"] - off}
                               if focus and focus["code"] == c_code
                               and off <= focus["buy_i"] < hi else None),
                     "candles": cs[off:hi],
                     "marks": [{"b": g["buy_i"] - off, "s": g["sell_i"] - off,
                                "g": g["gross_pct"], "net": g["net_pct"]}
                               for g in got if off <= g["buy_i"] < hi and off <= g["sell_i"] < hi]}

    w = sum(1 for r in rows if r["result"] == "win")
    l = sum(1 for r in rows if r["result"] == "loss")
    return {"ok": True, "id": vid, "ko": label(v, True), "en": label(v, False),
            "clock": f"{period}초" if period else f"{tick}틱",
            "entry_n": v["entry"], "kind": v["kind"], "a": v["a"], "b": v.get("b"),
            "dir": v.get("dir", 1),
            "trips": len(rows), "wins": w, "losses": l, "flats": len(rows) - w - l,
            "win_pct": round(w / (w + l) * 100) if (w + l) else 0,
            # the same honesty the Strategy Lab now carries: a flat is neither a win nor a
            # loss and is NOT in the percentage, so the denominator has to be on screen or
            # "2 trips ... 100%" reads as two wins (boss 2026-08-04)
            "decided": w + l, "thin": (w + l) < 10,
            # THE MONEY. Summed over EVERY trade, not the page's slice - `trades` is
            # cut to `limit`, so a total added up on screen would quietly under-report a
            # rule with more trades than fit. Net is after the round-trip fee.
            "net_total": round(sum(r["net_pct"] for r in rows), 2),
            "gross_total": round(sum(r["gross_pct"] for r in rows), 2),
            "per_trade": round(sum(r["net_pct"] for r in rows) / len(rows), 3) if rows else 0.0,
            "trades": rows[:limit], "shown": min(len(rows), limit),
            "holding": holding, "chart": chart, "fee_pct": FEE_PCT}
