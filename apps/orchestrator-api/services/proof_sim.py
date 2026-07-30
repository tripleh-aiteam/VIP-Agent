"""🧪 PROOF LAB (증명 시뮬레이션) — boss 2026-07-29.

Purpose: PROVE, with evidence the boss can click through, that Algorithm 3's engine
  1) BUYS exactly on the 3rd rising (red) candle — never the 2nd, 4th or 5th,
  2) SELLS exactly on the 3rd falling (blue) candle,
  3) picks its fill PRICE from the order book (best ask on a buy / best bid on a sell),
  4) decides on RAW minute data — the chart (TradingView lightweight-charts) only DRAWS
     the same raw numbers.

Two samples, same verifier:
  • source="synthetic" — an artificial trading day where we PLANT known patterns:
      clean 3-ups, a 5-up (must still buy on the 3rd), 2-up fakeouts (must NOT buy),
      flat candles (must break the count), 1-blue chops (must NOT sell), a 4-down
      (must sell on the 3rd). Order book is generated per fill, so the price
      explanation is exact.
  • source="kiwoom"  — TODAY's real minute bars from Kiwoom for a real ticker; the
      same engine comparison replayed candle-by-candle, arrows on the real chart.
      (Historical order books don't exist, so fills use the decision close and the
      book checks are skipped — the synthetic sample carries the book proof.)

The signal logic is NOT a copy: it calls candle_trader.run_steps — the exact pure
function live trading uses. An INDEPENDENT verifier then re-derives every claim
from the raw candles alone and reports pass/fail per check. No DB writes."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from services.candle_trader import run_steps   # ← the REAL engine comparison

KST = timezone(timedelta(hours=9))
FEE_PCT = 0.23                                  # round-trip fee+tax, same as the desk
NEED = 3                                        # 3 red candles buy · 3 blue candles sell


# --------------------------------------------------------------------------- #
#  tick size (KRX bands, 2023 rules) — used for realistic synthetic prices     #
# --------------------------------------------------------------------------- #
def _tick(px: float) -> int:
    if px < 2_000: return 1
    if px < 5_000: return 5
    if px < 20_000: return 10
    if px < 50_000: return 50
    if px < 200_000: return 100
    if px < 500_000: return 500
    return 1_000


# --------------------------------------------------------------------------- #
#  synthetic day — planted patterns                                            #
# --------------------------------------------------------------------------- #
#  step plan per symbol: +1 = one rising candle, -1 = one falling, 0 = flat.
#  Every trap the boss asked about is planted:
#    up3            → BUY exactly on 3rd red
#    up5            → BUY on 3rd red, 4th & 5th keep rising AFTER the entry
#    up2 / up1+flat → fakeouts: must NOT buy
#    dn3 / dn4      → SELL on 3rd blue (4th falls AFTER the exit)
#    dn1 chop       → must NOT sell on 1 blue
# One planted step per MINUTE. A trade's P&L = (rises AFTER the buy) − (3 falls to the sell),
# so run lengths decide the outcome — designed for an HONEST mix of wins and losses
# (boss 2026-07-30: the old pattern was symmetric, which made 0% wins mathematically certain).
# ups == downs == 40 per cycle → the day oscillates instead of drifting.
_PLAN = ([+1] * 8 + [-1] * 8                       # WIN  — bought on the 3rd, price rose 5 more
         + [+1] * 2 + [-1] * 2                     # trap — 2-up fakeout, no buy
         + [+1] * 3 + [-1] * 3                     # LOSS — bought the top, fell straight back
         + [+1] * 1 + [0] + [+1] * 2 + [-1] * 3    # trap — a FLAT close breaks the count, no buy
         + [+1] * 7 + [-1] * 1 + [+1] * 1 + [-1] * 7   # WIN — a single blue candle does NOT sell
         + [+1] * 4 + [-1] * 4                     # LOSS
         + [+1] * 9 + [-1] * 9                     # WIN (big)
         + [+1] * 3 + [-1] * 3)                    # LOSS

_SYMBOLS = [("PRF1", "프루프전자", 205_000), ("PRF2", "시뮬중공업", 19_500), ("PRF3", "테스트화학", 78_000)]

# Which artificial companies the Proof Lab actually SHOWS. Three at once was too much to
# read, so the boss now checks one stock at a time (2026-07-31: "hide all info related to
# these 2 companies, I will check only with one stock").
# The full list above is kept untouched on purpose: each symbol's prices come from
# `seed + index * 101`, so removing a row would renumber the others and silently change
# 프루프전자's tape. Filtering by code here keeps every price and time exactly as it was.
# To bring one back, just add its code to this set.
_SHOWN = {"PRF1"}


def _label(epoch9: int, period: int) -> str:
    dt = datetime.fromtimestamp(epoch9 - 9 * 3600, KST)
    return dt.strftime("%H:%M") if period == 60 else dt.strftime("%H:%M:%S")


def _sec_label(epoch9: int, off: int) -> str:
    return datetime.fromtimestamp(epoch9 - 9 * 3600 + off, KST).strftime("%H:%M:%S")


PERIODS = (1, 15, 30, 40, 60)    # selectable candle sizes — ALL aggregated from the same seconds
S1_WINDOW = 1800                 # 1초 chart shows a 30-minute window (Kiwoom limits tick charts too)
DEMO_MINUTES = 840               # the artificial proof day: 09:00 → 23:00, always complete


def _norm_period(p) -> int:
    try:
        p = int(p)
    except Exception:
        return 60
    return p if p in PERIODS else 60


def _seconds(seed: int, base_px: float, start: int = 0) -> tuple[int, list[dict]]:
    """THE market — ONE deterministic per-SECOND price series (frozen per minute-seed).
    EVERY timeframe (1/15/30/40/60s) is a pure aggregation of these same seconds, so
    prices/times/ups-downs are identical in every view
    (boss 2026-07-30: 'all data must be same').

    Two shapes, chosen by `start`:
      start == 0  → 전체 하루: a COMPLETE recorded proof day, 09:00→23:00, ALWAYS. It must
        never wait for the real market. This was once "09:00 until now", which meant that at
        06:52 there were 3 candles and zero trades — the rule needs 4 candles to see 3 rising
        steps, so the Proof Lab looked broken every morning until ~09:10.
      start == epoch seconds → 지금부터: the tape BEGINS at that moment and grows one candle
        per real minute, so the boss can watch candles form and arrows appear live
        (boss 2026-07-31: "I wanna start trading from now and lets see how will work").
        Minute m is seeded by its offset from `start`, so once a minute has closed its prices
        never change again — closed trades stay immutable exactly as in the full-day tape.

    Returns (tape-open epoch+9h, [{off, px, qty}, ...])."""
    n_kst = datetime.now(KST)
    if start:
        open_t = datetime.fromtimestamp(start, KST).replace(second=0, microsecond=0)
        total_sec = min(max(0, int((n_kst - open_t).total_seconds())), DEMO_MINUTES * 60)
    else:
        open_t = n_kst.replace(hour=9, minute=0, second=0, microsecond=0)
        total_sec = DEMO_MINUTES * 60
    day0 = int(open_t.timestamp()) + 9 * 3600            # +9h so charts display KST
    t = _tick(base_px) or 1
    secs: list[dict] = []
    px = float(base_px)
    for m in range((total_sec + 59) // 60):
        rm = random.Random(f"{seed}:min:{m}")            # ← per-minute seed = frozen history
        step = _PLAN[m % len(_PLAN)]                     # one planted step per MINUTE
        # LIVING minute (boss 2026-07-30): the price moves EVERY SECOND, up AND down, while
        # drifting toward the minute's target close. Real fluctuation means the finer charts
        # (15/30/40s and even 1s) carry genuine information instead of a smooth staircase —
        # that is what makes the multi-timeframe cross-check actually prove something.
        o = px
        ticks = 0 if step == 0 else step * rm.choice([2, 3, 4])   # the minute's NET move, in ticks
        target = o + ticks * t
        cur = o
        for s in range(60):
            off = m * 60 + s
            if off >= total_sec:
                break
            path = o + (target - o) * s / 59                      # drift toward the close
            wig = rm.choice([-2, -1, -1, 0, 0, 0, 1, 1, 2]) * t   # real second-to-second noise
            cur = round((path + wig) / t) * t
            if s == 0: cur = o                                    # :00 = the minute's open (= prev close)
            if s == 59: cur = target                              # :59 = the CLOSE the engine reads
            secs.append({"off": off, "px": cur, "qty": rm.randint(1, 80) * 10})
        px = target
    return day0, secs


def _candles_from(day0: int, secs: list[dict], period: int) -> list[dict]:
    """Aggregate the 1-second truth into candles of `period` seconds (open = first second,
    close = last, high/low = extremes) — exactly how any real chart builds a timeframe.
    `dir` = the ENGINE's own comparison for this bar: +1 if its close is HIGHER than the
    previous bar's close (red), -1 if lower (blue), 0 if equal. The chart is coloured by
    `dir` so what you see is literally what the engine counts (boss 2026-07-30)."""
    out = []
    prev_close = None
    # bars are CLIPPED AT EACH MINUTE, so a period that doesn't divide 60 (40s) ends the
    # minute with a short "half" bar (40 + 20). Every 3-minute decision run then finishes
    # exactly on a bar boundary in every timeframe (boss 2026-07-30: the 4.5-candle idea).
    for m in range(len(secs) // 60):
        s = 0
        while s < 60:
            ln = min(period, 60 - s)
            chunk = secs[m * 60 + s: m * 60 + s + ln]
            pxs = [x["px"] for x in chunk]
            ep9 = day0 + chunk[0]["off"]
            close = pxs[-1]
            # CONTINUOUS bars (boss 2026-07-30): a bar OPENS at the previous bar's close, the
            # way an intraday tape has no gaps. Then "close > open" (the eye) and "close >
            # previous close" (the engine) are the SAME statement — at every timeframe. So a
            # red bar can never mean anything but 'the engine counted a rise here'.
            op = prev_close if prev_close is not None else pxs[0]
            d = 1 if close > op else (-1 if close < op else 0)
            out.append({"time": ep9, "hhmm": _label(ep9, period),
                        "open": op, "high": max(max(pxs), op), "low": min(min(pxs), op),
                        "close": close, "dir": d,
                        "off0": chunk[0]["off"], "n": ln, "half": ln != period})
            prev_close = close
            s += ln
    return out


def _tape_from(day0: int, secs: list[dict], start_off: int, count: int) -> list[dict]:
    """The per-second tape of one candle — straight from the truth array (no re-generation),
    so the tape ALWAYS matches the candle and every other timeframe."""
    return [{"t": _sec_label(day0, x["off"]), "px": x["px"], "qty": x["qty"]}
            for x in secs[start_off:start_off + count]]


def _synthetic_candles(seed: int, base_px: float, period: int = 60) -> list[dict]:
    day0, secs = _seconds(seed, base_px)
    return _candles_from(day0, secs, _norm_period(period))


def _forming_from(day0: int, secs: list[dict], period: int) -> dict | None:
    """The still-forming candle = the REAL leftover seconds of the current (incomplete)
    minute, clipped the same way as closed bars — no synthesis, same truth array."""
    rem = len(secs) % 60
    if rem == 0:
        return None
    base = (len(secs) // 60) * 60
    last, s = None, 0
    while s < rem:
        ln = min(period, 60 - s, rem - s)
        chunk = secs[base + s: base + s + ln]
        pxs = [x["px"] for x in chunk]
        ep9 = day0 + chunk[0]["off"]
        last = {"time": ep9, "hhmm": _label(ep9, period), "open": pxs[0],
                "high": max(pxs), "low": min(pxs), "close": pxs[-1]}
        s += ln
    return last


def _book(seed: int, ref: float, side: str, tick: int | None = None) -> dict:
    """Synthetic 5-level order book around the decision price. Buys pay the BEST ASK
    (cheapest seller = ref + 1 tick); sells hit the BEST BID (highest buyer = ref).
    tick: pass the symbol's BASE tick so book/tape/candles always share one tick size
    even if the price drifts across a KRX tick band (boss 2026-07-30 consistency)."""
    r = random.Random(seed)
    t = tick or _tick(ref)
    # boss 2026-07-30: BOTH sides fill at the NEXT candle's :00 at its OPEN (= the signal
    # close). The book is anchored so the fill side touches ref: a buy meets a seller
    # waiting AT the last price; a sell meets a buyer AT the last price.
    if side == "BUY":
        asks = [[ref + t * i, r.randint(300, 9_500)] for i in range(5)]        # best ask == ref
        bids = [[ref - t * (i + 1), r.randint(300, 9_500)] for i in range(5)]
    else:
        asks = [[ref + t * (i + 1), r.randint(300, 9_500)] for i in range(5)]
        bids = [[ref - t * i, r.randint(300, 9_500)] for i in range(5)]        # best bid == ref
    return {"asks": asks, "bids": bids, "best_ask": asks[0][0], "best_bid": bids[0][0],
            "fill": asks[0][0] if side == "BUY" else bids[0][0]}


# --------------------------------------------------------------------------- #
#  the minute replay — proof the price is NOT picked randomly from 60 seconds  #
# --------------------------------------------------------------------------- #
def _second_tape(cd: dict, seed: int, period: int = 60, tick: int | None = None) -> list[dict]:
    """ALL seconds of the signal candle (60 or 30) — a plausible per-second price walk that
    is exactly consistent with the candle (starts at open, touches high & low, ends at close).
    Demonstrates what 'many prices in one candle' really looks like, tick by tick."""
    r = random.Random(seed)
    o, h, l, c = cd["open"], cd["high"], cd["low"], cd["close"]
    t = tick or _tick((o + c) / 2) or 1
    hi_s, lo_s = r.sample(range(4, period - 4), 2)
    rows = []
    for s in range(period):
        px = o + (c - o) * s / (period - 1) + r.choice([-1, 0, 0, 1]) * t
        px = round(px / t) * t
        if s == 0: px = o          # :00 = the candle's OPEN = prev close → where BUYS and SELLS fill
        if s == hi_s: px = h
        if s == lo_s: px = l
        if s == period - 1: px = c
        px = min(max(px, l), h)
        rows.append({"t": _sec_label(cd["time"], s), "px": px, "qty": r.randint(1, 80) * 10})
    return rows


def _next_sec(hhmm: str, sec: int = 1) -> str:
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    m += 1
    if m == 60:
        h, m = h + 1, 0
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _minute_timeline(cd: dict, fill_px: float, seed: int, synthetic: bool, fill_sec: int = 1, period: int = 60) -> list[dict]:
    """Second-by-second replay of the SIGNAL minute. One minute = 60 seconds of moving
    prices; the engine reads exactly ONE of them — the CLOSE at :59 — and the fill then
    comes from the order book at the next second. kinds: open / watch (synthetic wiggles)
    / high / low (kiwoom real anchors) / close / fill."""
    rows: list[dict] = []
    hh = cd["hhmm"]
    if synthetic:
        r = random.Random(seed)
        n = cd.get("n", period)
        rows.append({"t": _sec_label(cd["time"], 0), "px": cd["open"], "kind": "open"})
        if n >= 10:                              # short bars (1s) have no room for 'watch' rows
            secs = sorted(r.sample(range(2, n - 2), min(3, n - 4)))
            vals = [cd["high"], cd["low"], round((cd["high"] + cd["low"]) / 2)]
            r.shuffle(vals)
            rows += [{"t": _sec_label(cd["time"], s), "px": v, "kind": "watch"} for s, v in zip(secs, vals)]
        rows.append({"t": _sec_label(cd["time"], n - 1), "px": cd["close"], "kind": "close"})
        rows.append({"t": _sec_label(cd["time"], n - 1), "px": fill_px, "kind": "fill"})
    else:                                   # real bars: the 4 anchors Kiwoom actually stores
        rows.append({"t": f"{hh}:00", "px": cd["open"], "kind": "open"})
        rows.append({"t": f"{hh}:??", "px": cd["high"], "kind": "high"})
        rows.append({"t": f"{hh}:??", "px": cd["low"], "kind": "low"})
        rows.append({"t": f"{hh}:59", "px": cd["close"], "kind": "close"})
        rows.append({"t": _next_sec(hh, fill_sec), "px": fill_px, "kind": "fill"})
    return rows


# --------------------------------------------------------------------------- #
#  the simulation — calls the REAL engine function candle-by-candle            #
# --------------------------------------------------------------------------- #
def _simulate(candles: list[dict], seed: int, with_book: bool, period: int = 60,
              tick: int | None = None, tape_fn=None) -> tuple[list, list, list, list]:
    """Replay: after each candle CLOSES, feed all closes so far into the live engine
    comparison (run_steps). 3 rising steps & flat → BUY; holding & 3 falling → SELL.
    Returns (completed trades, still-open positions, hold-skips —
    3-ups that could NOT buy because the stock was already held: 1 position per stock)."""
    if tape_fn is None:
        def tape_fn(j):  # noqa: E731 — default: one canonical tape per candle
            return _second_tape(candles[j], seed * 11 + j, period, tick)
    closes: list[float] = []
    trades: list[dict] = []
    skips: list[dict] = []        # 3-up while already holding → no double-buy (visible on chart)
    pos: dict | None = None
    for i, cd in enumerate(candles):
        closes.append(cd["close"])
        up, dn = run_steps(closes)                       # ← REAL engine code
        if pos is None and up == NEED:                   # fires the moment the 3rd red closes
            bk = _book(seed * 1_000 + i, cd["close"], "BUY", tick) if with_book else None
            entry_px = bk["fill"] if bk else cd["close"]
            _bn = cd.get("n", period)                                     # bar length (40s bars end in a 20s half)
            pos = {"buy_idx": i, "buy_time": cd["time"], "buy_hhmm": cd["hhmm"],
                   # fill AT the closing second of the 3rd candle: that second's traded price
                   # IS the close, and (continuous bars) also the next bar's open
                   "buy_sig_t": _sec_label(cd["time"], _bn - 1),
                   "buy_fill_t": _sec_label(cd["time"], _bn - 1),
                   "buy_closes": closes[-4:], "entry": entry_px, "buy_book": bk,
                   "buy_timeline": _minute_timeline(cd, entry_px, seed * 31 + i, with_book, fill_sec=0, period=period),
                   # per-second tapes for ALL 3 signal candles (1st/2nd/3rd — click to inspect each)
                   "buy_tapes": ([tape_fn(j) for j in (i - 2, i - 1, i) if j >= 0]
                                 if with_book else None)}
        elif pos is not None and dn == NEED:             # fires the moment the 3rd blue closes
            bk = _book(seed * 2_000 + i, cd["close"], "SELL", tick) if with_book else None
            exit_px = bk["fill"] if bk else cd["close"]
            gross = (exit_px / pos["entry"] - 1) * 100
            net = gross - FEE_PCT
            _sn = cd.get("n", period)
            trades.append({**pos, "sell_idx": i, "sell_time": cd["time"], "sell_hhmm": cd["hhmm"],
                           "sell_sig_t": _sec_label(cd["time"], _sn - 1),
                           "sell_fill_t": _sec_label(cd["time"], _sn - 1),
                           "gross_pct": round(gross, 3), "fee_pct": FEE_PCT,
                           "sell_closes": closes[-4:], "exit": exit_px, "sell_book": bk,
                           "sell_timeline": _minute_timeline(cd, exit_px, seed * 37 + i, with_book, fill_sec=0, period=period),
                           "sell_tapes": ([tape_fn(j) for j in (i - 2, i - 1, i) if j >= 0]
                                          if with_book else None),   # same tape source as buys → ONE canonical tape per candle
                           "net_pct": round(net, 3)})
            pos = None
        elif pos is not None and up == NEED and i > pos["buy_idx"]:
            skips.append({"idx": i, "hhmm": cd["hhmm"]})   # a 3rd red we could NOT buy — already holding
    open_pos: list[dict] = []
    if pos is not None:                                  # bought, still waiting for the 3rd blue
        last = closes[-1] if closes else pos["entry"]
        open_pos.append({**pos, "last_px": last,
                         "unreal_pct": round((last / pos["entry"] - 1) * 100 - FEE_PCT, 3)})
    return trades, open_pos, skips


# --------------------------------------------------------------------------- #
#  the INDEPENDENT verifier — re-derives every claim from raw candles alone    #
# --------------------------------------------------------------------------- #
def _verify(candles: list[dict], trades: list[dict], with_book: bool) -> dict:
    closes = [c["close"] for c in candles]
    per_trade, passed, total = [], 0, 0
    for t in trades:
        b, s = t["buy_idx"], t["sell_idx"]
        cks: dict[str, bool] = {}
        # BUY: the 3 candles at b-2, b-1, b each closed above the previous close
        cks["buy_3_rising"] = b >= 3 and closes[b - 3] < closes[b - 2] < closes[b - 1] < closes[b]
        # exactly the 3rd — the candle before the run was NOT rising (else it'd be the 4th)
        cks["buy_exactly_3rd"] = b < 4 or not (closes[b - 4] < closes[b - 3])
        # SELL mirror
        cks["sell_3_falling"] = s >= 3 and closes[s - 3] > closes[s - 2] > closes[s - 1] > closes[s]
        cks["sell_exactly_3rd"] = s < 4 or not (closes[s - 4] > closes[s - 3])
        # the live engine function agrees candle-by-candle
        cks["engine_says_3up_at_buy"] = run_steps(closes[: b + 1])[0] == NEED
        cks["engine_says_3dn_at_sell"] = run_steps(closes[: s + 1])[1] == NEED
        if with_book:
            cks["buy_fill_is_best_ask"] = t["buy_book"] is not None and t["entry"] == t["buy_book"]["best_ask"]
            cks["sell_fill_is_best_bid"] = t["sell_book"] is not None and t["exit"] == t["sell_book"]["best_bid"]
        ok = sum(1 for v in cks.values() if v)
        passed += ok
        total += len(cks)
        per_trade.append({"buy_hhmm": t["buy_hhmm"], "sell_hhmm": t["sell_hhmm"],
                          "checks": cks, "passed": ok, "total": len(cks)})
    return {"trades": len(trades), "passed": passed, "total": total,
            "pct": round(passed / total * 100, 1) if total else 100.0,
            # per-trade detail is not rendered — keep it out of the payload (it was ~1/3 of it)
            "per_trade": [{"passed": pt["passed"], "total": pt["total"]} for pt in per_trade]}


# --------------------------------------------------------------------------- #
#  public entry points                                                         #
# --------------------------------------------------------------------------- #
def run_synthetic(seed: int = 7, period: int = 60, mode: str = "min1",
                  around: str = "", start: int = 0) -> dict[str, Any]:
    """mode='min1'  → the engine decides on 1-MINUTE candles (like the live desk): every
                      timeframe shows the SAME trades — this is the consistency proof.
       mode='chart' → the engine decides on the DISPLAYED candles: proof the rule works at
                      1초/15초/30초/40초/1분, each chart on its own 3rd candle.
       around='HH:MM' → 1초 charts hold only 30 minutes of bars, so a trade from earlier in
                      the day would have no arrow to show. Pass the trade's minute and the
                      1초 window CENTRES on it instead of on 'now' (Kiwoom scroll-back)."""
    period = _norm_period(period)
    per_chart = (mode == "chart")
    symbols = []
    agg_pass = agg_total = agg_trades = 0
    for k, (code, name, base) in enumerate(_SYMBOLS):
        if code not in _SHOWN:                            # hidden company — skip, keep k stable
            continue
        t_base = _tick(base) or 1                         # ONE tick per symbol, everywhere
        sseed = seed + k * 101
        day0, secs = _seconds(sseed, base, start)         # THE market (per-second truth)
        c60 = _candles_from(day0, secs, 60)               # 1-minute candles
        disp_all = c60 if period == 60 else _candles_from(day0, secs, period)
        dec = disp_all if per_chart else c60               # what the ENGINE reads
        def _mk_tape(j, _d=day0, _s=secs, _dec=dec):       # a candle's tape = its own seconds
            cd = _dec[j]
            return _tape_from(_d, _s, cd["off0"], cd["n"])
        trades, open_pos, skips = _simulate(dec, sseed, with_book=True,
                                                    period=(period if per_chart else 60),
                                                    tick=t_base, tape_fn=_mk_tape)
        for tr in trades[:-6]:                            # keep 60s tapes only on recent trades (payload size)
            tr["buy_tapes"] = None
            tr["sell_tapes"] = None
        ver = _verify(dec, trades, with_book=True)
        # the 3 DECISION candles (+ the baseline before them) travel with each trade, so the
        # evidence panel shows the same numbers whatever chart you are on
        for tr in trades + open_pos:
            i = tr["buy_idx"]
            tr["buy_cands"] = [dec[j] for j in (i - 3, i - 2, i - 1, i) if j >= 0]
            if "sell_idx" in tr:
                s2 = tr["sell_idx"]
                tr["sell_cands"] = [dec[j] for j in (s2 - 3, s2 - 2, s2 - 1, s2) if j >= 0]
        candles = disp_all
        if not per_chart and period != 60:
            # 1분-고정 mode: the arrow goes on the LAST bar of the 3rd decision minute — the
            # 3rd candle on 1분봉, 6th on 30초봉, 12th on 15초봉, 60th on 1초봉, and the closing
            # 20s half-bar on 40초봉 (boss's own rule). Bars are minute-clipped, so the count
            # per minute is constant and this lands exactly on the bar holding second :59.
            #
            # ⭐ COLOUR: the engine judges 1-MINUTE candles here, so a sub-minute bar is
            # painted by the direction of the MINUTE it belongs to — not by its own few
            # seconds of wiggle. Two things follow, and both are what the boss asked for:
            #   1. the arrow is ALWAYS on a correctly-coloured bar (buy=red, sell=blue) and
            #      still sits at the TRUE fill second — no more trading time vs. colour off
            #      against each other;
            #   2. a 3-minute rise reads as one solid red run: 3 bars on 1분봉, 6 on 30초봉,
            #      12 on 15초봉, 4.5 (4 full + the 20s half) on 40초봉, 180 on 1초봉.
            # Each bar's own direction is kept in "sdir" for anyone who wants it.
            bpm = -(-60 // period)
            for _j, _c in enumerate(candles):
                _c["sdir"] = _c["dir"]
                _m = _j // bpm
                if _m < len(dec):
                    _c["dir"] = dec[_m]["dir"]
            def _disp(i, up=True, _b=bpm, _cs=candles):
                last = i * _b + _b - 1
                return last if 0 <= last < len(_cs) else -1
            for tr in trades + open_pos:
                tr["buy_idx"] = _disp(tr["buy_idx"], True)
                if "sell_idx" in tr:
                    tr["sell_idx"] = _disp(tr["sell_idx"], False)
            for sk in skips:
                sk["idx"] = _disp(sk["idx"], True)
        # 1초 charts hold a 30-MINUTE window — a whole day is ~33,000 one-second bars, which
        # is neither drawable nor sendable. By default the window is the most recent 30 min;
        # pass around='HH:MM' (the trade the boss clicked) and it slides onto that trade so
        # the arrows are visible instead of silently falling off the left edge.
        # Indices are remapped ONCE here: anything outside [lo, hi) becomes -1 (no arrow).
        if period == 1 and len(candles) > S1_WINDOW:
            lo = len(candles) - S1_WINDOW
            if around and len(around) >= 5:
                hit = next((j for j, c in enumerate(candles) if c["hhmm"][:5] == around[:5]), None)
                if hit is not None:
                    lo = min(max(0, hit - S1_WINDOW // 3), len(candles) - S1_WINDOW)
            hi = lo + S1_WINDOW
            def _win(i, _lo=lo, _hi=hi):
                return i - _lo if _lo <= i < _hi else -1
            for tr in trades + open_pos:
                tr["buy_idx"] = _win(tr["buy_idx"])
                if "sell_idx" in tr:
                    tr["sell_idx"] = _win(tr["sell_idx"])
            for sk in skips:
                sk["idx"] = _win(sk["idx"])
            candles = candles[lo:hi]
        agg_pass += ver["passed"]; agg_total += ver["total"]; agg_trades += ver["trades"]
        # a CURRENT order book per fake stock (anchored to the last close, changes each
        # minute) — powers the '📗 price table' view: the program trades from THIS table
        ref_px = secs[-1]["px"] if secs else float(base)   # same anchor in every view
        lb = _book(seed * 17 + (secs[-1]["off"] if secs else 0), ref_px, "BUY", t_base)
        live_book = {"asks": lb["asks"], "bids": lb["bids"], "best_ask": lb["best_ask"],
                     "best_bid": lb["best_bid"], "time": datetime.now(KST).strftime("%H:%M:%S")}
        symbols.append({"code": code, "name": name, "candles": candles,
                        # decision-timeframe candles (1-min) so the counter/chips always
                        # reflect what actually drives the trades, in every view
                        "dec_candles": (None if dec is disp_all else dec[-120:]),
                        "forming": _forming_from(day0, secs, period), "trades": trades,
                        "open_positions": open_pos, "hold_skips": skips, "live_book": live_book,
                        "verification": ver})
    return {"source": "synthetic", "seed": seed, "need": NEED, "period": period, "mode": mode,
            "start": start,   # echoed so the page can discard a stale response from a previous session
            "rule_ko": f"양봉 {NEED}개 연속(전봉 대비 {NEED}회 상승) → 정확히 {NEED}번째 양봉에 매수 · 음봉 {NEED}개 연속 → 정확히 {NEED}번째 음봉에 매도",
            "rule_en": f"{NEED} rising candles → BUY exactly on the {NEED}rd red · {NEED} falling → SELL exactly on the {NEED}rd blue",
            "engine_fn": "services/candle_trader.py::run_steps (the live engine's own comparison)",
            "symbols": symbols,
            "verification": {"trades": agg_trades, "passed": agg_pass, "total": agg_total,
                             "pct": round(agg_pass / agg_total * 100, 1) if agg_total else 100.0}}


def _kiwoom_symbol(code: str) -> dict[str, Any] | None:
    """One real ticker: TODAY's Kiwoom 1-min bars replayed through the engine fn."""
    from services.kiwoom_rest import minute_bars
    from services.scalp_trader import _name as stock_name
    try:
        raw = minute_bars(code, tic="1", count=400) or []
    except Exception:
        return None
    bars = raw[:-1] if len(raw) >= 2 else raw            # drop the forming candle (engine behavior)
    today = datetime.now(KST).strftime("%Y-%m-%d")

    def _cd(b) -> dict | None:
        ts = str(b.get("ts") or "")
        if not ts.startswith(today) or b.get("close") is None:
            return None
        hh = ts[-5:]
        dt = datetime.strptime(f"{today} {hh}", "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        return {"time": int(dt.timestamp()) + 9 * 3600, "hhmm": hh,
                "open": b.get("open"), "high": b.get("high"),
                "low": b.get("low"), "close": b.get("close")}

    candles = [c for c in (_cd(b) for b in bars) if c]
    if not candles:
        return None
    # the still-FORMING current candle — shown on the chart for real-time feel, but the
    # engine never judges it (it can flip before it closes), so the replay excludes it
    forming = _cd(raw[-1]) if len(raw) >= 2 else None
    if forming and candles and forming["hhmm"] == candles[-1]["hhmm"]:
        forming = None
    trades, open_pos, skips = _simulate(candles, seed=1, with_book=False)
    ver = _verify(candles, trades, with_book=False)
    try:
        name = stock_name(code) or code
    except Exception:
        name = code
    # 📡 LIVE Kiwoom order book snapshot — proof the system reads Kiwoom's real 호가창
    # (historical books don't exist, so per-trade fills use the decision close; this
    # live book shows the exact mechanism real fills use: best ask / best bid).
    live_book = None
    try:
        from services.kiwoom_rest import order_book
        ob = order_book(code, ttl=20) or None
        if ob and ob.get("best_ask"):
            asks = sorted([[l["price"], l["qty"]] for l in ob.get("levels", []) if l["side"] == "ask"])[:5]
            bids = sorted([[l["price"], l["qty"]] for l in ob.get("levels", []) if l["side"] == "bid"], reverse=True)[:5]
            live_book = {"asks": asks or [[ob["best_ask"], ob.get("ask_qty") or 0]],
                         "bids": bids or ([[ob["best_bid"], ob.get("bid_qty") or 0]] if ob.get("best_bid") else []),
                         "best_ask": ob.get("best_ask"), "best_bid": ob.get("best_bid"),
                         "time": datetime.now(KST).strftime("%H:%M:%S")}
    except Exception:
        live_book = None
    # 🎬 LIVE tick stream — the REAL second-by-second executed deals Kiwoom reports right
    # now (exchanges don't archive past minutes' ticks, so this demonstrates the exact
    # reading mechanism live). Chronological, last ~40 deals.
    tick_tape = None
    try:
        from services.kiwoom_rest import executions
        ex = executions(code, ttl=5) or []
        tick_tape = [{"t": e.get("time"), "px": e.get("price"), "qty": e.get("qty")}
                     for e in reversed(ex[:40]) if e.get("price") is not None]
        if not tick_tape:
            tick_tape = None
    except Exception:
        tick_tape = None
    return {"code": code, "name": name, "candles": candles, "forming": forming,
            "trades": trades, "tick_tape": tick_tape,
            "open_positions": open_pos, "hold_skips": skips, "live_book": live_book,
            "verification": ver}


def self_check(seed: int = 7) -> dict[str, Any]:
    """🔬 THE CONSISTENCY MATRIX (boss 2026-07-30). Runs every check across ALL timeframes
    and both decision modes and returns pass/fail counts, so the proof verifies itself:
      A) every trade is identical in all 5 charts (1분-고정 mode): times, prices, net%
      B) the trade price EXISTS at that exact second, and inside the bar covering it
      C) the rule held: 3 strictly rising closes → BUY · 3 strictly falling → SELL
      D) the arrow sits on the right bar and is the right colour (red buy / blue sell)
      E) every candle equals the aggregation of its own seconds
      F) every bar opens at the previous bar's close (continuous tape)
      G) the arrow's bar actually CONTAINS the exact fill second — so the time written in
         the history is the time the arrow points at, on every chart"""
    checks: dict[str, dict] = {k: {"ok": 0, "bad": 0} for k in ("A", "B", "C", "D", "E", "F", "G")}
    fails: list[str] = []

    def _hit(key, good, msg=""):
        checks[key]["ok" if good else "bad"] += 1
        if not good and len(fails) < 12:
            fails.append(f"[{key}] {msg}")

    def off_of(t: str) -> int:
        h, m, s = (int(x) for x in t.split(":"))
        return h * 3600 + m * 60 + s - 9 * 3600

    per_tf: list[dict] = []
    ref: dict[str, list] = {}
    for mode in ("min1", "chart"):
        for p in (60,) + tuple(x for x in PERIODS if x != 60):   # 1분 first: it is the reference
            r = run_synthetic(seed=seed, period=p, mode=mode)
            wins = gross = net = 0.0
            n_tr = 0
            for s in r["symbols"]:
                cs, day0 = s["candles"], None
                # index by CODE, never by position in the response: hidden companies are
                # filtered out of the payload, so position 0 is not necessarily _SYMBOLS[0]
                # and a positional lookup would rebuild the wrong tape and cry false failures.
                k = next(i for i, (c, _n, _b) in enumerate(_SYMBOLS) if c == s["code"])
                _d0, secs = _seconds(seed + k * 101, _SYMBOLS[k][2])
                # E) candles == their own seconds · F) continuous opens
                prev_close = None
                for c in cs:
                    chunk = [x["px"] for x in secs[c["off0"]: c["off0"] + c["n"]]]
                    if chunk:
                        _hit("E", c["close"] == chunk[-1] and c["high"] >= max(chunk) and c["low"] <= min(chunk),
                             f"{p}s {c['hhmm']} candle≠seconds")
                    if prev_close is not None:
                        _hit("F", c["open"] == prev_close, f"{p}s {c['hhmm']} open≠prev close")
                    prev_close = c["close"]
                for t in s["trades"]:
                    n_tr += 1
                    net += t["net_pct"]; gross += t.get("gross_pct", t["net_pct"])
                    wins += 1 if t["net_pct"] > 0 else 0
                    # C) the rule
                    for ck, want in (("buy_cands", 1), ("sell_cands", -1)):
                        cl = [c["close"] for c in t[ck]]
                        st = [cl[i + 1] - cl[i] for i in range(len(cl) - 1)]
                        _hit("C", all(x > 0 for x in st) if want == 1 else all(x < 0 for x in st),
                             f"{mode} {p}s {t['buy_fill_t']} steps={st}")
                    # B) price exists at that exact second, and inside the bar covering it
                    #    (the 1초 chart is windowed to 30 min, so older bars aren't sent —
                    #     the second-level check still applies to every trade)
                    for ft, px in ((t["buy_fill_t"], t["entry"]), (t["sell_fill_t"], t["exit"])):
                        o = off_of(ft)
                        _hit("B", o < len(secs) and secs[o]["px"] == px, f"{mode} {p}s {ft} px={px} not at that second")
                        bar = next((c for c in cs if c["off0"] <= o < c["off0"] + c["n"]), None)
                        if bar is not None:
                            _hit("B", bar["low"] <= px <= bar["high"], f"{mode} {p}s {ft} px outside bar {bar['hhmm']}")
                    # D) arrow colour · G) the arrow's bar CONTAINS the exact fill second, so
                    #    the history's time and the arrow's position can never disagree
                    for ik, ft, want in (("buy_idx", "buy_fill_t", 1), ("sell_idx", "sell_fill_t", -1)):
                        i = t[ik]
                        if i < 0:              # outside the 1초 30-min window → no arrow drawn
                            continue
                        _hit("D", cs[i]["dir"] == want, f"{mode} {p}s {t[ft]} arrow colour")
                        d = off_of(t[ft]) - cs[i]["off0"]
                        _hit("G", 0 <= d < cs[i]["n"],
                             f"{mode} {p}s fill {t[ft]} is {d}s outside its arrow bar {cs[i]['hhmm']}")
                    # A) identical across charts (1분-고정 mode only — 'chart' mode differs by design)
                    if mode == "min1":
                        key = f"{k}|{t['buy_fill_t']}|{t['sell_fill_t']}"
                        sig = (t["entry"], t["exit"], t["net_pct"])
                        if p == 60:                       # the 1분 run is the reference
                            ref[key] = sig
                        else:
                            _hit("A", ref.get(key) == sig, f"{p}s {key} differs from 1분")
            per_tf.append({"mode": mode, "period": p, "candles": len(r["symbols"][0]["candles"]),
                           "trades": n_tr, "wins": int(wins),
                           "win_pct": round(wins / n_tr * 100) if n_tr else 0,
                           "gross_pct": round(gross, 1), "net_pct": round(net, 1),
                           "rule_pct": r["verification"]["pct"]})
    tot_ok = sum(v["ok"] for v in checks.values())
    tot_bad = sum(v["bad"] for v in checks.values())
    return {"ok": tot_bad == 0, "passed": tot_ok, "total": tot_ok + tot_bad,
            "checks": checks, "failures": fails, "per_tf": per_tf,
            "labels": {
                "A": "모든 차트에서 같은 매매 (시각·가격·손익) / same trade in every chart",
                "B": "체결가가 그 '초'에 실제로 존재하고 캔들 범위 안 / fill price exists at that second & inside its bar",
                "C": "규칙: 3연속 상승 매수 · 3연속 하락 매도 / rule: 3 rising → buy, 3 falling → sell",
                "D": "화살표 색 = 판단 캔들 방향 (매수=빨강, 매도=파랑) / arrow colour = decision candle's direction",
                "E": "캔들 = 자기 초들의 집계 / candle = aggregation of its own seconds",
                "F": "바의 시가 = 앞 바의 종가 (연속 테이프) / bar opens at previous close",
                "G": "화살표가 가리키는 캔들이 그 체결 '초'를 실제로 포함 / the arrow's bar contains the exact fill second"},
            # ⚠️ the honest reading of the loss/profit columns above. The pattern is drawn for
            # TEACHING (clean 3-candle runs + traps), and it pairs every up-leg with an equal
            # down-leg. With a 3-candle entry lag and a 3-candle exit lag, a paired leg of
            # length L returns exactly (L-3) - 3 = L-6 ticks — so short legs (3, 4) must lose
            # and long legs (8, 9) must win, and the total is decided by which lengths I drew.
            # That is arithmetic of the DRAWING, not a property of the algorithm. Positive
            # numbers here are no more evidence than negative ones.
            "note_ko": ("이 표의 손익은 '작동 증명'용이며 수익 예측이 아닙니다. 인공 패턴은 상승 구간마다 "
                        "같은 길이의 하락 구간을 붙여 만든 교육용 데이터이고, 3봉 진입·3봉 청산이므로 길이 L 구간은 "
                        "항상 (L-3)-3 = L-6 만큼만 남습니다 — 짧은 구간(3·4봉)은 반드시 손실, 긴 구간(8·9봉)은 반드시 이익. "
                        "즉 이 손익은 '제가 그린 패턴의 산수'이지 알고리즘의 실력이 아닙니다. 플러스 숫자도 증거가 아닙니다. "
                        "실제 수익성은 키움 실데이터에서만 판단할 수 있습니다."),
            "note_en": ("the P&L here proves MECHANICS, not profit. The artificial pattern pairs every "
                        "up-leg with an equal down-leg for teaching, and with a 3-candle entry lag and a "
                        "3-candle exit lag a leg of length L returns exactly (L-3)-3 = L-6 ticks — short "
                        "legs (3, 4) must lose and long legs (8, 9) must win, so the total is decided by "
                        "which leg lengths were drawn. That is arithmetic of the drawing, not the algorithm's "
                        "skill; the positive numbers are no more evidence than the negative ones. Real "
                        "profitability can only be judged on real Kiwoom data.")}


def live_book_fast(source: str, code: str, seed: int = 7, period: int = 60,
                   start: int = 0) -> dict[str, Any]:
    """⚡ Kiwoom-speed ladder feed — 10 price levels per side, changing every call.
    Polled ~1/sec by the 📗 price-table view (kept separate from the heavy proof payload).
    synthetic: anchored to the fake stock's current last close; quantities & a ±1-tick
    mid drift re-seeded per second so it moves like the real 호가창.
    kiwoom: the real live order book (10-deep both sides)."""
    import time as _t
    now_s = int(_t.time())
    if source == "synthetic":
        k = next((i for i, (c, _n, _b) in enumerate(_SYMBOLS) if c == code), 0)
        base = _SYMBOLS[k][2]
        _d0, _sc = _seconds(seed + k * 101, base, start)  # ONE market — ladder identical in every view
        ref = _sc[-1]["px"] if _sc else float(base)
        r = random.Random(f"{code}:{seed}:{now_s}")
        t = _tick(base) or 1                              # base tick — same as candles/tapes/books
        mid = ref + t * r.choice([-1, 0, 0, 0, 1])
        asks = [[mid + t * (i + 1), r.randint(200, 9_900)] for i in range(10)]
        bids = [[mid - t * i, r.randint(200, 9_900)] for i in range(10)]
        # Kiwoom-style 체결 tape: like the real market, EACH second prints a BURST of 1-4
        # deals (all stamped with that same second), then the next second's burst. Deterministic
        # per second so overlapping rows never change between polls — only new seconds append.
        prev_close = round((ref * 0.985) / t) * t          # fake yesterday's close (~-1.5%)
        tape = []
        for s in range(now_s - 14, now_s + 1):
            rs = random.Random(f"{code}:{seed}:tape:{s}")
            ts_s = datetime.fromtimestamp(s, KST).strftime("%H:%M:%S")
            for _d in range(rs.randint(8, 15)):            # Kiwoom-like burst: ~8-15 deals within the SAME second
                tape.append({"t": ts_s,
                             "px": ref + t * rs.choice([-2, -1, -1, 0, 0, 0, 1, 1, 2]),
                             "qty": rs.randint(1, 120) * 10,
                             "strength": rs.randint(78, 138)})   # 체결강도 %
        return {"ok": True, "asks": asks, "bids": bids,
                "best_ask": asks[0][0], "best_bid": bids[0][0],
                "tape": tape, "prev_close": prev_close,
                "time": datetime.now(KST).strftime("%H:%M:%S")}
    from services.kiwoom_rest import order_book, executions
    ob = order_book(code, ttl=0.8) or {}
    lv = ob.get("levels") or []
    asks = sorted([[l["price"], l["qty"]] for l in lv if l["side"] == "ask"])[:10]
    bids = sorted([[l["price"], l["qty"]] for l in lv if l["side"] == "bid"], reverse=True)[:10]
    if not asks and ob.get("best_ask"):
        asks = [[ob["best_ask"], ob.get("ask_qty") or 0]]
    if not bids and ob.get("best_bid"):
        bids = [[ob["best_bid"], ob.get("bid_qty") or 0]]
    # real 체결 tape + running 체결강도 (buy vol / sell vol × 100, oldest→newest)
    tape = []
    prev_close = None
    try:
        ex = executions(code, ttl=0.8) or []
        buyv = sellv = 0
        rows = []
        for e in reversed(ex[:30]):                        # chronological
            q = int(e.get("qty") or 0)
            d = e.get("dir") or 0
            if d > 0: buyv += q
            elif d < 0: sellv += q
            strength = round(buyv / sellv * 100) if sellv else None
            rows.append({"t": e.get("time"), "px": e.get("price"), "qty": q, "strength": strength})
        tape = rows[-20:]
        try:
            from services.paper_desk import _chg_cache
            chg = _chg_cache.get(code)
            last = tape[-1]["px"] if tape else (ob.get("best_bid") or None)
            if chg is not None and last:
                prev_close = round(float(last) / (1 + float(chg) / 100))
        except Exception:
            prev_close = None
    except Exception:
        tape = []
    return {"ok": bool(asks or bids), "asks": asks, "bids": bids,
            "best_ask": (asks[0][0] if asks else None), "best_bid": (bids[0][0] if bids else None),
            "tape": tape or None, "prev_close": prev_close,
            "time": datetime.now(KST).strftime("%H:%M:%S")}


def minute_tape(source: str, code: str, seed: int, hhmm: str, period: int = 60,
                start: int = 0) -> dict[str, Any]:
    """🕰️ drill-down: the 60-second tape of ONE chosen minute (e.g. 14:07) — regenerated
    deterministically for artificial stocks (same canonical tape shown everywhere).
    Real stocks: exchanges don't archive past per-second data, so this is synthetic-only."""
    if source != "synthetic":
        return {"ok": False, "reason": "no-history"}
    k = next((i for i, (c, _n, _b) in enumerate(_SYMBOLS) if c == code), 0)
    sseed = seed + k * 101
    period = _norm_period(period)
    day0, secs = _seconds(sseed, _SYMBOLS[k][2], start)
    candles = _candles_from(day0, secs, period)
    for cd in candles:
        if cd["hhmm"] == hhmm:
            return {"ok": True, "hhmm": hhmm, "candle": cd,
                    "tape": _tape_from(day0, secs, cd["off0"], cd["n"])}
    return {"ok": False, "reason": "minute-not-found"}


def run_kiwoom(code: str = "005930", codes: list[str] | None = None) -> dict[str, Any]:
    """Same proof on TODAY's REAL Kiwoom minute bars (replay). codes=[...] runs ALL
    companies (the desk watchlist) and aggregates the verification. Order-book history
    does not exist, so fills = decision close and book checks are skipped here — the
    synthetic sample carries the fill-price proof."""
    targets = codes if codes else [code]
    symbols = [s for s in (_kiwoom_symbol(c) for c in targets) if s]
    agg_pass = sum(s["verification"]["passed"] for s in symbols)
    agg_total = sum(s["verification"]["total"] for s in symbols)
    agg_trades = sum(s["verification"]["trades"] for s in symbols)
    return {"source": "kiwoom", "code": code, "need": NEED,
            "rule_ko": "실제 키움 오늘 1분봉으로 같은 엔진 함수를 재생 — 화살표가 정확히 3번째 양봉/음봉에 있는지 확인",
            "rule_en": "TODAY's real Kiwoom 1-min bars replayed through the same engine function — check the arrows sit exactly on the 3rd candles",
            "engine_fn": "services/candle_trader.py::run_steps (the live engine's own comparison)",
            "symbols": symbols,
            "verification": {"trades": agg_trades, "passed": agg_pass, "total": agg_total,
                             "pct": round(agg_pass / agg_total * 100, 1) if agg_total else 100.0}}
