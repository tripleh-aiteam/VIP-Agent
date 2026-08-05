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
                                _sec_label, _date_label,
                                _execs, _seconds, _SHOWN, _SYMBOLS, _tick, _sec_hl,
                                _sec_label)

# Below this many DECIDED trades (wins + losses) a win rate is not a measurement — it is
# a coin that happened to land. Rules under it are still shown, but ranked last and marked.
MIN_DECIDED = 10

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
    # ── REVERSAL entries (2026-08-03). Measured on 60h of this tape: after 3 RISES the
    # next 5틱 bar rises 18% of the time against a 27% base, but after 2 FALLS it rises
    # 55%. Every rule above therefore buys at the worst moment available and the mirror
    # is twice as good. These are the same exits, entered the other way round, so the
    # comparison isolates exactly one thing: which direction the entry faces.
    # ALL down-entry rules (2-down AND 3-down families) were REMOVED at the boss's
    # instruction, 2026-08-05: "too risky, I wanna trade only buy when up not down".
    # Buying after falls is catching a falling knife, and that is his risk call to make.
    # For the record, the removed set included the day's only positive rules; re-adding
    # any of them is one line here plus the id lists in kiwoom_rules and the live page.
    # The 2-DOWN family (2d+0.3/0.5/1.0/0.8/1.2/1.0s, 2d3u) was REMOVED at the boss's
    # instruction on 2026-08-05, from both desks. Recorded for honesty: 2d+1.0 was that
    # day's only rule positive on BOTH clocks, and the neighbourhood test was built
    # around it - re-adding is a one-line change if the decision is revisited.

    # ── THE TAKE-PROFIT EXPERIMENT (boss 2026-08-05) ────────────────────────────────
    # Every rule above aims for +0.3% or +0.5%, and the round trip costs 0.23% — so the
    # fee eats most of every win and it takes eighteen of them to pay for one -1% loss.
    # These four hold out for a bigger move instead. The BUY is identical to 3d+0.3
    # (three falls in a row); only the EXIT differs, so this group isolates exactly one
    # variable: how much profit is worth waiting for.
    #
    # The trade-off is real and points the other way: a bigger target is reached less
    # often, so the win rate falls as the target rises. Which effect wins is a question
    # about this tape, not about arithmetic, and running them side by side is the answer.
    # ── THE 1분 CANDIDATE (boss 2026-08-05). Measured on today's real tape: the same
    # fall-entry rules lose less at every step from 5틱 to 30초 to 1분, and this shape —
    # two falls, +1.0% take, -1.5% stop — was the first thing POSITIVE on real data
    # (+0.157%/trade, 73% win, 11 trades) when read on the 1분 clock. A 1분 bar moves
    # further than a 5틱 bar while the fee stays 0.23%, so the move-to-cost ratio
    # improves purely by waiting. Small sample; that is what running it is for.

    # ── THE NEIGHBOURHOOD TEST (boss approved 2026-08-05). 2d+1.0 is the only rule with
    # a real sample that is positive on BOTH clocks today. Before trusting it, nudge each
    # of its numbers and see whether the ZONE is good or only the point: a rule that
    # captured something true about the market must have decent neighbours, and a rule
    # that merely fit one day's wiggles will stand alone. Four directions:  # stricter entry
    # ── the boss's top six, each with a per-company model filtering its entries
    # (2026-08-03). Same rule, same exits; the model only decides whether to TAKE a
    # signal the rule already produced, so a "+ML" row can be read against its twin.
    {"id": "3u+0.3ML", "entry": 3, "kind": "target", "a": 0.3, "b": 1.0, "ml": True},
    {"id": "3u+0.5ML", "entry": 3, "kind": "target", "a": 0.5, "b": 1.0, "ml": True},
    {"id": "2u+0.5ML", "entry": 2, "kind": "target", "a": 0.5, "b": 1.0, "ml": True},
    {"id": "4u3dML", "entry": 4, "kind": "candle", "a": 3, "ml": True},
    {"id": "3u+1.0ML", "entry": 3, "kind": "target", "a": 1.0, "b": 1.0, "ml": True},
    {"id": "4u+1.0ML", "entry": 4, "kind": "target", "a": 1.0, "b": 1.0, "ml": True},
]


def label(v: dict, ko: bool = True) -> str:
    if v.get("ml"):
        base = dict(v)
        base.pop("ml")
        return label(base, ko) + (" + ML" if not ko else " + ML")
    dn = v.get("dir", 1) < 0
    ent = (f"{v['entry']}연속 하락" if dn else f"{v['entry']}연속 상승") if ko else           (f"{v['entry']} down" if dn else f"{v['entry']} up")
    if v["kind"] == "candle":
        exi = f"{v['a']}연속 {'상승' if dn else '하락'} 매도" if ko else               f"{v['a']} {'up' if dn else 'down'}"
        return f"{ent} → {exi}" if ko else f"{ent} / {exi}"
    return (f"{ent} → +{v['a']}% 익절 / -{v['b']}% 손절" if ko
            else f"{ent} / +{v['a']}% take, -{v['b']}% stop")


def _outcome(cl, i, entry, tick, v):
    """Did the signal at bar i eventually win, and on WHICH BAR was that decided?

    The resolving bar matters as much as the answer. A label that is only settled inside
    the trading window has read the future, so those samples must be embargoed from the
    fit — otherwise the model is scored on bars it was partly trained on and reports
    skill it does not have. Used only to label PAST signals; never to decide a live trade."""
    for j in range(i + 1, min(i + 600, len(cl))):
        if v["kind"] == "candle":
            # the mirror of the live exit, counted the same way
            run = 0
            for q in range(j, max(0, j - v["a"]) - 1, -1):
                if q < 1:
                    break
                rise = cl[q] > cl[q - 1]
                if (rise if v.get("dir", 1) < 0 else not rise):
                    run += 1
                else:
                    break
            if run >= v["a"]:
                return (1 if cl[j] > entry else 0), j
        else:
            if (cl[j] / entry - 1) * 100 - FEE_PCT >= v["a"]:
                return 1, j
            if ((cl[j] - tick) / entry - 1) * 100 <= -v["b"]:
                return 0, j
    return None, None


def run_variant(closes: list[float], tick: int, v: dict, seed: int,
                evidence: bool = False, with_open: bool = False,
                vols: list[float] | None = None, ml_key: tuple | None = None,
                ml_bundle: dict | None = None, fill_fn=None):
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
    # HOW A FILL IS PRICED, injected. The artificial market has a synthetic order book;
    # the real one has a real spread that was never recorded historically. Keeping the
    # RULE in one place and swapping only the fill model means the two markets can never
    # drift into two different definitions of "3 consecutive rises" — which is exactly the
    # bug that cost a day when this lab briefly had its own copy of the engine.
    book = fill_fn or (lambda seed_i, px, side, tk: _book(seed_i, px, side, tk))

    # The model is trained on history that ENDS where this session begins (see
    # _ml_for). Nothing is fitted in here, so there is no split to honour and no way for
    # a label to be settled by a bar the model is later scored on. `ml_bundle` is either
    # a finished model or None, and None means this variant simply does not trade.
    bundle = ml_bundle

    last_sig_live = -1
    for i in range(1, len(closes)):
        c, prev = closes[i], closes[i - 1]
        up = up + 1 if c > prev else 0
        dn = dn + 1 if c < prev else 0
        if pos is None:
            # dir=-1 buys after a run of FALLS instead of rises. The tape is mean-reverting
            # at 5틱, so this is the same rule pointed the other way — nothing else changes.
            if (dn if v.get("dir", 1) < 0 else up) == v["entry"]:
                if v.get("ml"):
                    from services.proof_ml import features_at, score, MARGIN
                    vv = vols or [0.0] * len(closes)
                    fa = features_at(closes, vv, i, last_sig_live)
                    last_sig_live = i
                    # no model, no trading — a variant that cannot be scored honestly
                    # takes nothing rather than falling back to the plain rule
                    if bundle is None:
                        continue
                    sc = score(bundle, fa)
                    # "better than this rule's average signal", not an absolute 0.5
                    if sc["p"] < bundle["base_rate"] + MARGIN:
                        continue                      # the model declined this signal
                    from services.proof_ml import quantity as _qty
                    _bar = bundle["base_rate"] + MARGIN
                    ml_meta = {"p": round(sc["p"], 4), "why": sc["why"],
                               "bar": round(_bar, 4),
                               "base_rate": round(bundle["base_rate"], 4),
                               # HOW MANY SHARES the model wants on this signal. One share
                               # is the floor; the edge over its own bar buys more. Sizing
                               # amplifies whatever edge exists — including a negative one.
                               # priced off the BAR'S CLOSE, so the cap band is decided
                               # by what the share actually costs at that moment
                               "qty": _qty(sc["p"], _bar, c),
                               "auc": bundle["auc"], "n_train": bundle["n_train"]}
                else:
                    ml_meta = None
                bk = book(seed * 1_000 + i, c, "BUY", tick)
                # EVERY rule gets a real position, not just the ML ones. A plain rule has
                # no model to ask, so it takes the whole cap for its price band — which is
                # still an explainable number ("the most this band allows"), and it is what
                # the boss meant by "increase number of stock" (2026-08-04). An ML rule
                # takes 5-100% of that same cap, so the model can only ever ask for LESS
                # than the plain rule, never more.
                from services.proof_ml import cap_for as _cap
                _q = (ml_meta or {}).get("qty") or _cap(c)
                pos = {"i": i, "entry": bk["fill"], "bk": bk, "close": c, "qty": _q,
                       "ml": ml_meta,
                       "seq": closes[max(0, i - v["entry"]): i + 1]}
        else:
            if v["kind"] == "candle":
                # a reversal entry exits on a run of RISES — the mirror of the exit above
                if v.get("dir", 1) < 0:
                    hit = up == v["a"]
                    why = f"{v['a']}연속 상승"
                else:
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
                bk = book(seed * 2_000 + i, c, "SELL", tick)
                gross = (bk["fill"] / pos["entry"] - 1) * 100
                tr = {"buy_i": pos["i"], "sell_i": i,
                      # 1 for every plain rule; the model's answer for an ML one
                      "qty": pos.get("qty", 1),
                      "entry": pos["entry"], "exit": bk["fill"],
                      "gross_pct": round(gross, 3),
                      "net_pct": round(gross - FEE_PCT, 3),
                      "exit_why": why, "ml": pos.get("ml")}
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
    if v.get("ml"):
        for tr in out:
            tr["ml_model"] = ({"auc": bundle["auc"], "n_train": bundle["n_train"],
                               "n_test": bundle["n_test"], "base_rate": bundle["base_rate"],
                               "trained_to": bundle.get("trained_to"),
                               "n_signals": bundle.get("n_signals", 0)} if bundle else None)
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


def consistency_gate(seed: int = 7, start: int = 0, tick: int = 5,
                     period: int = 0) -> dict[str, Any]:
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

    # The minute STILL RUNNING belongs here too. _candles_from only emits whole minutes, so
    # a trade that just executed could not be reconciled against the Data File until its
    # minute ended — and "the minute is not in the Data File" is exactly what a wrong price
    # would look like (found by the audit 2026-08-03, 3 trades in the live minute).
    # It is appended and flagged `forming`, never silently mixed in with the closed ones.
    rem = len(secs) % 60
    if rem:
        base = (len(secs) // 60) * 60
        chunk = secs[base: base + rem]
        ep9 = d0 + chunk[0]["off"]
        pxs = [x["px"] for x in chunk]
        mrows = mrows + [{"time": ep9, "hhmm": _sec_label(d0, chunk[0]["off"])[:5],
                          "open": mrows[-1]["close"] if mrows else pxs[0],
                          "high": max(pxs), "low": min(pxs), "close": pxs[-1],
                          "off0": chunk[0]["off"], "n": rem, "forming": True}]

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
                "low": cd["low"], "tick": t, "forming": bool(cd.get("forming")),
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
                     # the DAY, because the standing session runs 07:21 -> 00:01 and after
                     # midnight two rows can both read "08:30" with nothing to separate
                     # them. The trades have carried a date since 2026-08-03; the Data File
                     # they are reconciled against did not, which is half a fix.
                     "date": _date_label(d0, cd["off0"]),
                     "diff": diff, "forming": bool(cd.get("forming")),
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


def _bars(d0, secs, sseed, t, tick: int, period: int):
    """The bars the rules run on AND the chart draws — deliberately the same object.

    period=0 → N-execution (틱) bars.  period>0 → N-second bars.
    In this lab the clock IS the chart: the rule decides on exactly the candles you are
    looking at, so counting them on screen always gives the number the rule counted. That
    is the opposite of the Proof Lab, where the clock is pinned and the chart is free —
    and it is why this page needs no "these are not the candles the rule counted" warning.
    """
    if period:
        return _candles_from(d0, secs, period, _sec_hl(sseed, secs, t))
    return _candles_from_ticks(d0, _execs(d0, secs, sseed, t), tick)


def clock_label(tick: int, period: int) -> str:
    return f"{period}초" if period else f"{tick}틱"


_ML_CACHE: dict[tuple, Any] = {}
TRAIN_HOURS = 72          # how much history before the session the model may learn from


def _ml_for(c_code: str, base: float, sseed: int, t: int, tick: int, period: int,
            v: dict, start: int) -> dict | None:
    """Train this company's model on the tape BEFORE the traded session, and stop there.

    Training on the same bars the rule then trades is the mistake that makes a model look
    clever: even with a split, a label that resolves after the split has read the future.
    Ending the training tape at the session open removes the question entirely — every bar
    the model learned from is finished before the first trade is scored. It is also what
    you would do with real data: fit on last week, trade today.
    """
    from services.proof_ml import features_at, train, MIN_TRAIN
    open_ep = _session_open_epoch(start)
    key = (c_code, v["id"], tick, period, open_ep // 3600)
    if key in _ML_CACHE:
        return _ML_CACHE[key]
    d0, secs = _seconds(sseed, base, open_ep - TRAIN_HOURS * 3600, span=0)
    keep = max(0, open_ep - (d0 - 9 * 3600))        # only seconds before the session open
    secs = secs[:keep]
    bundle = None
    if len(secs) > 3600:
        cs = _bars(d0, secs, sseed, t, tick, period)
        cl = [c["close"] for c in cs]
        vv = [float(c.get("vol") or 0) for c in cs]
        samples, u, dn_, last = [], 0, 0, -1
        for i in range(1, len(cl)):
            u = u + 1 if cl[i] > cl[i - 1] else 0
            dn_ = dn_ + 1 if cl[i] < cl[i - 1] else 0
            if (dn_ if v.get("dir", 1) < 0 else u) != v["entry"]:
                continue
            y, _res = _outcome(cl, i, cl[i] + t, t, v)
            if y is None:
                continue
            samples.append((features_at(cl, vv, i, last), y))
            last = i
        bundle = train(samples, key)
        if bundle is not None:
            bundle["n_signals"] = len(samples)
            bundle["trained_to"] = _sec_label(d0, len(secs) - 1)
    _ML_CACHE[key] = bundle
    if len(_ML_CACHE) > 128:
        _ML_CACHE.pop(next(iter(_ML_CACHE)))
    return bundle


def sessions() -> dict[str, Any]:
    """The 07:21 open of today and of the preceding days, as epochs.

    Computed here rather than in the browser: the market opens at 07:21 KST and a browser
    in another timezone would compute a different second, which would quietly load a
    different market. The artificial tape is deterministic, so asking for an earlier open
    REGENERATES those days exactly — yesterday's trading was never lost, the lab simply
    never asked for it (boss 2026-08-04)."""
    from datetime import datetime, timedelta
    from services.proof_sim import KST, DEMO_OPEN
    n = datetime.now(KST).replace(hour=DEMO_OPEN[0], minute=DEMO_OPEN[1],
                                  second=0, microsecond=0)
    out = [{"days": 1, "label_ko": "오늘", "label_en": "today", "start": 0}]
    for d in (1, 2, 6):
        ep = int((n - timedelta(days=d)).timestamp())
        out.append({"days": d + 1, "start": ep,
                    "label_ko": f"{d + 1}일", "label_en": f"{d + 1} days",
                    "opened": (n - timedelta(days=d)).strftime("%m-%d %H:%M")})
    return {"ok": True, "sessions": out}


def _session_open_epoch(start: int) -> int:
    """The epoch second this session opened — an explicit start, or today's 07:21 open."""
    if start:
        return int(start)
    from datetime import datetime
    from services.proof_sim import _default_start, KST
    return int(_default_start(datetime.now(KST)).timestamp())


def variant_trades(vid: str, seed: int = 7, start: int = 0, tick: int = 5,
                   code: str = "", bars: int = 400, limit: int = 400,
                   around: int = -1, period: int = 0, at: str = "") -> dict[str, Any]:
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
    pair_all: list[dict] = []          # the same rule WITHOUT the model, same window
    pair_model: dict | None = None
    no_model: list[str] = []           # companies with too little history to fit
    for k, (c_code, name, base) in enumerate(_SYMBOLS):
        if c_code not in _SHOWN:
            continue
        sseed = seed + k * 101
        t = _tick(base) or 1
        d0, secs = _seconds(sseed, base, start, span=0)
        cs = _bars(d0, secs, sseed, t, tick, period)
        vv = [float(c.get("vol") or 0) for c in cs]
        mlb = _ml_for(c_code, base, sseed, t, tick, period, v, start) if v.get("ml") else None
        got, op = run_variant([c["close"] for c in cs], t, v, sseed,
                              evidence=True, with_open=True,
                              vols=vv, ml_bundle=mlb)
        # THE PAIRED BASELINE: the same rule without the model, over the SAME window.
        # "+ML 60%" against the rule's all-day figure would compare two different sets of
        # bars; the only honest comparison is the one the model actually faced.
        # computed whenever this is an ML variant — NOT only when the model traded.
        # A model that declined everything still needs its baseline on screen, or the row
        # reads "0 trips" with nothing to compare it against and looks broken.
        base_pair = None
        if v.get("ml"):
            plain = dict(v); plain.pop("ml")
            # the model trades the WHOLE session (it was trained before it), so the plain
            # rule over the whole session is the like-for-like comparison
            base_pair = run_variant([c["close"] for c in cs], t, plain, sseed)
            for _g in base_pair:
                _g["code"] = c_code          # so the overlap below can be counted per stock
        tapes[c_code] = {"cs": cs, "name": name, "trades": got}
        if base_pair is not None:
            pair_all.extend(base_pair)
            if pair_model is None:
                pair_model = (got[0].get("ml_model") if got else None) or (
                    {"auc": mlb.get("auc"), "n_train": mlb.get("n_train"),
                     "n_test": mlb.get("n_test"), "n_signals": mlb.get("n_signals"),
                     "trained_to": mlb.get("trained_to")} if mlb else None)
            if mlb is None:
                no_model.append(name)
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
                # how many shares the model asked for (1 for every plain rule)
                "qty": g.get("qty", 1),
                "buy_ev": g.get("buy_ev"), "sell_ev": g.get("sell_ev"),
                "ml": g.get("ml"),
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
    at_found = False
    # An explicitly requested stock WINS. It used to be the other way round, so the focused
    # trade's stock overrode the caller and this chart ignored `code` entirely — which is
    # how the page ended up showing two charts of two different companies at once
    # (boss 2026-08-03: "we have 2 charts open, are they the same or different?").
    # With no explicit code, follow the trade being looked at.
    pick = code or (focus or {}).get("code")
    tp = tapes.get(pick) or (tapes.get(code) or (next(iter(tapes.values())) if tapes else None))
    if tp:
        cs = tp["cs"]
        # `at` is a minute clicked in the Data File — the chart jumps there so the boss can
        # see the place the row is describing (2026-08-03). It beats the focused trade,
        # because he asked for that minute explicitly.
        anchor = None
        at_found = False
        if at:
            hit = next((j for j, c in enumerate(cs) if c["hhmm"][:5] == at[:5]), None)
            if hit is not None:
                anchor, at_found = hit, True
            # A minute still RUNNING has no completed 30초/1분 bar yet — _candles_from only
            # emits whole minutes — so the jump would silently land nowhere. Anchor on the
            # live edge instead and SAY the bar does not exist yet, because the forming row
            # is the top one in the Data File and therefore the first thing anyone clicks.
        if anchor is None:
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
                 # where in the RETURNED window the requested minute sits, so the page can
                 # actually scroll to it. Sliding the window is not enough: the chart keeps
                 # its own view, so a 1,500-bar payload looks unchanged (boss 2026-08-03:
                 # "if I click any time it is not opening exact time").
                 "at_idx": (next((j for j, c in enumerate(cs[off:hi])
                                  if c["hhmm"][:5] == at[:5]), None) if at else None),
                 "focus": ({"b": focus["buy_i"] - off, "s": focus["sell_i"] - off}
                           if focus and focus.get("code") == pick
                           and off <= focus["buy_i"] < hi else None)}

    w = sum(1 for r in rows if r["result"] == "win")
    l = sum(1 for r in rows if r["result"] == "loss")
    # trades the two versions took at the very same bar of the very same stock
    _pair_keys = {(g.get("code"), g["buy_i"]) for g in pair_all}
    _same_bar = sum(1 for r in rows if (r["code"], r["buy_i"]) in _pair_keys)
    return {"ok": True, "id": vid, "ko": label(v, True), "en": label(v, False),
            "tick": tick, "period": period, "clock": clock_label(tick, period),
            "at": at, "at_found": bool(at) and at_found,
            "entry_n": v["entry"], "kind": v["kind"], "a": v["a"], "b": v.get("b"),
            # the SAME arithmetic the ranking row uses — one source, so they cannot drift
            "trips": len(rows), "wins": w, "losses": l, "flats": len(rows) - w - l,
            "win_pct": round(w / (w + l) * 100) if (w + l) else 0,
            # 승률 is W/(W+L) — a flat is neither, as agreed on 2026-07-31. But a header
            # reading "2 trips ... 100%" while one of those two was FLAT looks like two
            # wins, which is exactly how the boss found this (2026-08-04, 4up/3down + ML).
            # `decided` is the real denominator; `thin` says when the whole percentage is
            # a coin that has landed once or twice.
            "decided": w + l, "thin": (w + l) < MIN_DECIDED,
            # THE MONEY. Summed over EVERY trade, not the page's slice - `trades` is
            # cut to `limit`, so a total added up on screen would quietly under-report a
            # rule with more trades than fit. Net is after the round-trip fee.
            # THE MONEY IN WON - see the note in kiwoom_rules. One share per signal.
            "net_won_total": round(sum(r["entry"] * r["net_pct"] / 100 for r in rows)),
            # the SAME trades with the model's share count. Shown beside the one-share
            # figure rather than replacing it: sizing multiplies whatever edge is there,
            # so the two numbers together are the only honest way to see what it did.
            "net_won_sized": round(sum(r.get("qty", 1) * r["entry"] * r["net_pct"] / 100
                                       for r in rows)),
            "shares_total": sum(r.get("qty", 1) for r in rows),
            # what the model actually committed - the number that says whether a share
            # count is sane, and the one a cap exists to bound
            "capital_used": round(sum(r.get("qty", 1) * r["entry"] for r in rows)),
            # SCALE and ALLOCATION are two different ideas and they give opposite answers.
            # "buy more when confident" (net_won_sized) buys ~3x more stock, and a rule
            # that loses on average loses ~2x more when it holds 3x the stock. Spreading
            # the SAME money toward the trades the model likes is the version that helps:
            # it is the only one where the model's judgement is being used rather than
            # simply amplified. Both are reported; neither is hidden (boss 2026-08-04).
            "net_won_balanced": (round(sum(r.get("qty", 1) * (len(rows) / max(1, sum(
                x.get("qty", 1) for x in rows))) * r["entry"] * r["net_pct"] / 100
                for r in rows)) if rows else 0),
            "per_trade_won": (round(sum(r["entry"] * r["net_pct"] / 100 for r in rows) / len(rows))
                              if rows else 0),
            "net_total": round(sum(r["net_pct"] for r in rows), 2),
            "gross_total": round(sum(r["gross_pct"] for r in rows), 2),
            "per_trade": round(sum(r["net_pct"] for r in rows) / len(rows), 3) if rows else 0.0,
            "trades": rows[:limit], "shown": min(len(rows), limit),
            # what the model is, and what the SAME rule did on the SAME bars without it
            # HOW THE TWO ACTUALLY RELATE. The model never invents a signal — audited
            # 2026-08-04, 0 invented across every rule and stock. But it is NOT true that
            # "+ML" is the plain rule minus some trades, which is what the page used to
            # say. Declining a signal leaves the rule FLAT, and a flat rule can take the
            # next signal that the plain rule had to ignore because it was still holding.
            # So the two follow different paths through the same market, and the honest
            # figure is how many trades they actually share.
            "ml": ({"same_bar": _same_bar, "only_ml": len(rows) - _same_bar,
                    "only_plain": len(pair_all) - _same_bar,
                    "no_model": no_model,
                    "auc": (pair_model or {}).get("auc"),
                    "n_train": (pair_model or {}).get("n_train"),
                    "n_test": (pair_model or {}).get("n_test"),
                    "base": {
                        "trips": len(pair_all),
                        "wins": sum(1 for g in pair_all if g["gross_pct"] > 0),
                        "losses": sum(1 for g in pair_all if g["gross_pct"] < 0),
                        "win_pct": round(sum(1 for g in pair_all if g["gross_pct"] > 0)
                                         / max(1, sum(1 for g in pair_all if g["gross_pct"] != 0)) * 100),
                        "per_trade": (round(sum(g["net_pct"] for g in pair_all) / len(pair_all), 3)
                                      if pair_all else 0.0)}}
                   if v.get("ml") else None),
            "holding": holding, "chart": chart, "fee_pct": FEE_PCT}


def compare(seed: int = 7, start: int = 0, tick: int = 5,
            code: str = "", bars: int = 500, hist: int = 40,
            period: int = 0) -> dict[str, Any]:
    """Every variant against the SAME market, returned as the Monday comparison table.

    The tape is built ONCE per stock and every rule runs against it, so the only thing
    separating two rows is the rule. Recomputed from the session start on every call —
    deterministic, so a restart cannot lose or alter a single trade."""
    # A weekend tape is ~110,000 candles per stock and takes seconds to rebuild, while new
    # candles only arrive a few times a second. Cached for the current MINUTE: the page can
    # poll freely and the numbers still move, without rebuilding the world each time.
    import time as _t
    key = (seed, start, tick, period, code, bars, hist, tuple(sorted(_SHOWN)))
    now_min = int(_t.time()) // 60
    hit = _cmp_cache.get(key)
    if hit and hit[0] == now_min:
        return hit[1]

    tapes = []
    # ⚠️ NOT `code` — that is the caller's chosen stock. Reusing the name here left it
    # holding the LAST symbol of the loop, so chart_tape below always resolved to that one
    # and the stock buttons under the market chart did nothing (found 2026-08-03 when the
    # two charts on screen showed two different companies).
    for k, (c_code, name, base) in enumerate(_SYMBOLS):
        if c_code not in _SHOWN:
            continue
        sseed = seed + k * 101
        t = _tick(base) or 1
        d0, secs = _seconds(sseed, base, start, span=0)   # span=0 → no 14h cap
        cs = _bars(d0, secs, sseed, t, tick, period)
        tapes.append({"code": c_code, "name": name, "seed": sseed, "tick": t, "cs": cs,
                      "base": base,
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
            got = run_variant(tp["closes"], tp["tick"], v, tp["seed"],
                              vols=[float(c.get("vol") or 0) for c in tp["cs"]],
                              ml_bundle=(_ml_for(tp["code"], tp["base"], tp["seed"], tp["tick"],
                                                 tick, period, v, start) if v.get("ml") else None))
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
            "kind": v["kind"],
            "trips": len(trades), "wins": len(w), "losses": len(l), "flats": flat,
            "win_pct": round(len(w) / decided * 100) if decided else 0,
            "gross": round(sum(t["gross_pct"] for t in trades), 2),
            "net": round(sum(t["net_pct"] for t in trades), 2),
            # AT THE SIZE ACTUALLY TRADED. This summed one share per trade while the
            # drill-down underneath it showed 100,000, so the ranking said -₩29,387 for a
            # rule whose own rows added to millions (boss 2026-08-04: "I can not see big
            # money because nothing changed, you have changed only per trade").
            "net_won": round(sum(t.get("qty", 1) * t["entry"] * t["net_pct"] / 100
                                 for t in trades)),
            "per_trade_won": (round(sum(t.get("qty", 1) * t["entry"] * t["net_pct"] / 100
                                        for t in trades) / len(trades)) if trades else 0),
            "shares_total": sum(t.get("qty", 1) for t in trades),
            "capital_used": round(sum(t.get("qty", 1) * t["entry"] for t in trades)),
            "avg_win": round(aw, 3), "avg_loss": round(al, 3),
            "rr": round(aw / al, 2) if al else 0.0,
            "per_trade": round(sum(t["net_pct"] for t in trades) / len(trades), 3) if trades else 0.0,
            "per_stock": per_stock,
            "recent": recent[:hist],
            # arrows for the charted stock only — index into the candles sent below
            "marks": [{"b": g["buy_i"], "s": g["sell_i"], "g": g["gross_pct"], "net": g["net_pct"]}
                      for g in (run_variant(chart_tape["closes"], chart_tape["tick"], v, chart_tape["seed"],
                                            vols=[float(c.get("vol") or 0) for c in chart_tape["cs"]],
                                            ml_key=(chart_tape["code"], v["id"], tick, period,
                                                    len(chart_tape["cs"])))[-60:]
                                if chart_tape else [])],
        })
    # A rule with one trade at 100% is not the leader, it is a coin that landed once.
    # Rules below MIN_RANKED trips are still SHOWN — hiding them would be worse — but
    # they sort beneath everything with a real sample, and carry `thin` so the page can
    # say why (boss 2026-08-04 saw "4 up / +1.0% + ML  100%  1 trip" at the top).
    MIN_RANKED = 10
    for r in rows:
        r["thin"] = r["trips"] < MIN_RANKED
    # Every "+ ML" row carries the win rate of its OWN plain twin. The boss asked for every
    # view sorted by win rate, which scatters a rule and its ML version to opposite ends of
    # the table — so the comparison that pairing used to make travels inside the row instead.
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        if r["id"].endswith("ML"):
            twin = by_id.get(r["id"][:-2])
            r["vs"] = twin["win_pct"] if twin else None
            r["vs_trips"] = twin["trips"] if twin else None
    # GROUPED as the boss reads them (2026-08-05): first every up/down rule (exit by
    # candle count), then every %-target rule - and inside each group, highest win rate
    # first. `kind` travels with the row so the page cannot need to guess the group.
    rows.sort(key=lambda r: (0 if r.get("kind") == "candle" else 1,
                             -r["win_pct"], -r["trips"]))
    off = max(0, len(chart_tape["cs"]) - bars) if chart_tape else 0
    if chart_tape and off:
        for r in rows:
            r["marks"] = [{"b": m["b"] - off, "s": m["s"] - off, "g": m["g"], "net": m["net"]}
                          for m in r["marks"] if m["b"] >= off]
    out = {"ok": True, "seed": seed, "start": start, "tick": tick, "period": period,
           "clock": clock_label(tick, period),
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
