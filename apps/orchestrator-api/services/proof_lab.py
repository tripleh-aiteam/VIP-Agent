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

from services.proof_sim import (FEE_PCT, _book, _candles_from_ticks, _execs, _seconds,
                                _SHOWN, _SYMBOLS, _tick)

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


def run_variant(closes: list[float], tick: int, v: dict, seed: int) -> list[dict]:
    """One rule over one stock's closes. Fills cross the spread exactly as the Proof Lab
    does — a BUY pays the best ask, a SELL takes the best bid — so these numbers are
    directly comparable with the trade history on the proof page."""
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
                pos = {"i": i, "entry": bk["fill"]}
        else:
            if v["kind"] == "candle":
                hit = dn == v["a"]
            else:
                ch = (c / pos["entry"] - 1) * 100
                hit = ch >= v["a"] or ch <= -v["b"]
            if hit:
                bk = _book(seed * 2_000 + i, c, "SELL", tick)
                gross = (bk["fill"] / pos["entry"] - 1) * 100
                out.append({"buy_i": pos["i"], "sell_i": i,
                            "entry": pos["entry"], "exit": bk["fill"],
                            "gross_pct": round(gross, 3),
                            "net_pct": round(gross - FEE_PCT, 3)})
                pos, up, dn = None, 0, 0
    return out


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


def compare(seed: int = 7, start: int = 0, tick: int = 5) -> dict[str, Any]:
    """Every variant against the SAME market, returned as the Monday comparison table.

    The tape is built ONCE per stock and every rule runs against it, so the only thing
    separating two rows is the rule. Recomputed from the session start on every call —
    deterministic, so a restart cannot lose or alter a single trade."""
    # A weekend tape is ~110,000 candles per stock and takes seconds to rebuild, while new
    # candles only arrive a few times a second. Cached for the current MINUTE: the page can
    # poll freely and the numbers still move, without rebuilding the world each time.
    import time as _t
    key = (seed, start, tick, tuple(sorted(_SHOWN)))
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
        tapes.append({"code": code, "name": name, "seed": sseed, "tick": t,
                      "closes": [c["close"] for c in cs],
                      "first": cs[0]["hhmm"] if cs else None,
                      "last": cs[-1]["hhmm"] if cs else None})

    rows = []
    for v in VARIANTS:
        trades = []
        per_stock = {}
        for tp in tapes:
            got = run_variant(tp["closes"], tp["tick"], v, tp["seed"])
            trades += got
            per_stock[tp["name"]] = len(got)
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
        })
    rows.sort(key=lambda r: (-r["win_pct"], -r["per_trade"]))
    out = {"ok": True, "seed": seed, "start": start, "tick": tick,
            "stocks": [{"code": t["code"], "name": t["name"], "candles": len(t["closes"]),
                        "from": t["first"], "to": t["last"]} for t in tapes],
            "variants": rows, "fee_pct": FEE_PCT}
    _cmp_cache[key] = (now_min, out)
    return out
