# -*- coding: utf-8 -*-
"""wave_rule — THE ONE STANDARD RULE FOR ALL TWENTY STOCKS (boss 2026-09-09).

His words, with the SK하이닉스 tape of today open in front of him:

  "we have 20 stock and we have rules. It looks like our rule is so hard so today
   we have only made 5 tradings. I wanna make a little change. I will explain by
   the example of SKhynix so you have to make a standard rule for other stocks
   also.  ...  if there is no 갭상승 - means equal or lower than yesterday price -
   at 09:04 we should buy 1000 shares. Then if there is a sharp decrease - you see
   from 09:24 to 09:26 within 3 minutes it decreased a lot - we should NOT sell,
   because mostly SKhynix and Samsung cases it jumps up again. So we should wait
   the decrease to stop, and 09:38 we should buy again another 1000. Then if our
   first buying case gains 1.5% we should sell 20% around maybe 09:49, and after
   each 1.5% we sell like 20%. Then if it started to decrease not rapidly, slowly,
   then we should sell again 20% from example 10:38, then wait again stop
   decreasing and start increasing, on the 3 red again buy 20% for example 10:58
   ... we should sell again 11:51 and maybe 12:03 and 12:23 like this, so 12:56
   again we should buy 20%. You see in 13:10 and 13:12 there was a sharp decrease
   so do NOT sell, wait stop decreasing, and if it starts increasing again buy
   ... 14:15, 14:17, 14:19, 14:21 sharply decreased within 4 minutes, then it
   again started to increase, then again buy at 14:24 and wait if we have 1.5%
   then sell. So before market closing sell everything."

WHAT THE RULE IS, IN ONE PARAGRAPH. A stock that did not open above yesterday is
bought when its fall stops and three rises stand (his "3 red"). From then on the
position is worked in 20% slices: a slice is sold at every +1.5% step, and a
slice is sold when a rise rolls over into a SLOW slide while we are in profit; a
slice is bought back every time the fall stops and three rises stand again. The
one thing that is never done is selling into a FAST fall - his 09:24-09:26,
13:10-13:12 and 14:15-14:21 are all the same shape, and all three turned back up
within minutes. A fast fall is not a sell signal, it is the next buy.

WHY A FALL'S SPEED DECIDES AND NOT ITS DEPTH. Measured on his own marks (today,
000660, 1-minute tape rebuilt in time order):

    his "sharp, do not sell"        his "slow, sell a slice"
    09:24-26  -0.98% / 3 min        10:33-10:38  -0.48% / 5 min
    13:10-12  -0.43% / 3 min        11:44-11:51  -0.53% / 7 min
    14:15-21  -0.55% / 6 min        12:00-12:03  -0.48% / 3 min
                                    12:18-12:23  -0.46% / 5 min

The depths overlap almost exactly - it is the RATE that separates them, and the
position: a slice is only ever sold out of profit, which is why 13:10 (bought
12:56, still -0.27%) is a hold under the same shape that sells at 12:03 (+2.81%).

Every number below is a dial, and every decision this module makes carries the
sentence that produced it - in Korean and in English - so a person reading the
log can check the rule against the tape without reading any code.
"""
from __future__ import annotations

from typing import Optional

# ── THE DIALS ────────────────────────────────────────────────────────────────
CFG: dict = {
    # ① the day gate
    "gap_tol": 0.30,     # an open more than this % above yesterday's last is a 갭상승
    # GATE 1 - HIS LAW, WITH THE MEASUREMENT ON THE RECORD BESIDE IT. 2026-09-10,
    # after being shown the numbers: "make sure if there is a 갭상승 and price come
    # back to yesterday 19:59 price or down then start to buy. This is rule."
    # So a gap-up stock is no longer dead for the session - it waits until the
    # price has traded at or below yesterday's LAST price (after-hours included,
    # which is what his 19:59 means), and from that moment the ordinary entry
    # applies. Measured over the 26 stored days this opens 72 extra stock-days
    # worth -16.8% between them (-0.233% a day) and it is his desk and his call;
    # gap_return=0 puts the strict gate back in one word.
    "gap_return": 1,
    # ② the entry shape - his "3 red"
    "ups": 3,            # rises that must stand
    "soft": 0.20,        # one blue candle this small inside the run is forgiven
    "dip_pct": 0.25,     # ...and the fall into the turn must be at least this deep
    "turn_win": 30,      # minutes the shape is read over
    # ③ the ladder
    "step": 1.5,         # sell a slice at every +1.5% (his number)
    "slice_pct": 20,     # ...and a slice is 20% of the base position (his number)
    "max_lots": 2,       # the position never grows past 2 base lots (his 1,000 + 1,000)
    "max_adds": 6,       # and never more than this many decisions to build it
    # ④ fast fall vs slow slide
    "spike_pct": 0.40,   # a fall this deep inside spike_min minutes is FAST -> never sell
    "spike_min": 3,
    "spike_bar": 0.35,   # ...or any single candle this red
    "spike_hold": 12,    # a fast fall protects the position for this many minutes
    "drift_pct": 0.25,   # a slide this far off a local peak...
    "drift_min": 3,      # ...taking at least this many minutes = a SLOW slide -> sell a slice
    # ⑦ 계단 (staircase) vs 계단이 아닌 것 (a cascade of real drops) - boss 2026-09-09
    "big_pct": 0.50,     # a candle must fall at least this much to count as a BIG drop
                         # (his idea measured at 0.30 / 0.35 / 0.50: only 0.50 pays -
                         # +12.0% against +10.6% without it, 23 slices in 26 days)
    "big_x": 2.0,        # ...AND be this many times the tape's own typical minute
    "cascade_win": 8,    # BIG drops inside this many minutes...
    "cascade_n": 2,      # ...this many of them, with a pause or a bounce between
    "cascade_sell": 1,   # 1 = sell a slice on a cascade, 0 = only ever hold (measured)
    "cascade_profit_only": 0,   # 1 = a cascade slice only comes off a profit
    # ⑤ the floors (his standing -1% law, and the one that overrides the hold)
    "stop_pct": -1.0,
    "hard_stop": -2.0,   # a fast fall is forgiven, but never past this
    # ⑥ housekeeping
    "cool_min": 3,       # minutes between two decisions in one stock
    "add_gap": 10,       # ...and between two pullback buys inside one holding
    "slice_gap": 10,     # ...and this many between two slices coming off the same rise
    # EVERYTHING IS FLAT AT THE CLOSE (boss 2026-09-10: "it should be 15:20, like
    # market closing time") - the same minute the desk's own flat close fires.
    # `eod` is the DEADLINE, `eod_from` is the minute the selling starts, and the
    # two differ for a reason found while making this change: continuous trading
    # ends at 15:20 and the next print is the 15:30 closing auction, so today's
    # tape runs …15:18, 15:19, 15:30. A flat triggered at "15:20" therefore fired
    # at 15:30 - inside the auction, where place_order refuses and no order of
    # ours can deal. It starts at 15:19 instead: the last price the desk can
    # actually trade before the bell.
    "eod": "15:20",
    "eod_from": "15:19",
    "vol_win": 20,       # bars in the trailing volume average
    # ⑧ how big an order this stock can actually deal (boss 2026-09-10)
    "liq_adv_pct": 0.5,  # at most this % of the 20-day average daily volume
    "liq_min_mult": 3,   # ...and at most this many times the median minute now
}

# HIS TWO NAMES. The rule is the same for all twenty; these two are the ones he
# taught it on, and the ones whose fast falls are known to come back.
TAUGHT_ON = ("000660", "005930")


# ── the tape ─────────────────────────────────────────────────────────────────
def minute_bars(code: str, day: str = "") -> list[dict]:
    """Today's 1-minute candles for one stock, IN TIME ORDER.

    The sort is not decoration. Kiwoom's execution pages arrive slightly out of
    order (532 backwards ticks in 000660 today, the first at 09:21:37), and
    bars_time() starts a new bar whenever the minute key changes - so an
    out-of-order tick SPLITS a minute and the day comes back with 45 duplicated
    and backwards candles. Every rule that counts candles was counting those.
    """
    from services.kiwoom_tape import load as _load, bars_time as _bt, _day as _dy
    tk = sorted(_load(str(code), day or None), key=lambda x: x.get("ts") or "")
    return _bt(tk, 60)


def prev_last(code: str, day: str = "") -> float:
    """Yesterday's last traded price - the line the 갭상승 test is drawn from."""
    try:
        from services.kiwoom_rules import _gap_ref
        from services.kiwoom_tape import _day as _dy
        return float(_gap_ref(str(code), day or _dy()) or 0)
    except Exception:
        return 0.0


# ── the shapes ───────────────────────────────────────────────────────────────
def _turn(bars: list[dict], cfg: dict) -> tuple[bool, Optional[str]]:
    """HIS "3 RED": a fall, then three rises, and we are standing on the third.

    Counted exactly as the approval desk counts it (approval_desk._turn_shape,
    the exempt-pair branch he dictated this morning): a blue candle of any size
    opens the shape, flat minutes neither count nor break the run, one small
    blue (<= soft%) inside the run is forgiven, and a slide of more than soft%
    off the run's own high cancels it. Returns (fires now?, the minute that
    completed the count).

    AND THE FALL MUST BE A FALL. "Price stop decreasing and start increasing" -
    a rising market makes a small blue candle every few minutes, and without a
    depth test the shape fired on every one of them: replayed on today's tape
    the un-tested version bought six times in the hour after 09:44, none of
    which he asked for. The dip into the turn must be at least dip_pct deep,
    measured from the highest close of the window down to where the rise began.
    All five of his own buy marks clear it (-0.39 / -1.09 / -0.48 / -1.12 / -0.86%).
    """
    if len(bars) < 4:
        return False, None
    w = bars[-int(cfg.get("turn_win", 30)):]
    lows = [b["close"] for b in w]
    trough_i = lows.index(min(lows))
    peak_before = max(b["close"] for b in w[:trough_i + 1]) if trough_i >= 0 else 0.0
    dip = ((peak_before - w[trough_i]["close"]) / peak_before * 100) if peak_before else 0.0
    if dip < cfg["dip_pct"]:
        return False, None
    ups, seen_fall, third = 0, False, None
    prev = bars[0]["close"]
    peak = prev
    for b in bars[1:]:
        c, o = b["close"], b["open"]
        if c < o:
            seen_fall = True
        elif c > prev:
            ups += 1
            if ups == cfg["ups"] and third is None:
                third = str(b.get("hhmm") or "")[:5]
        if peak is None or c > peak:
            peak = c
        if peak and (peak - c) / peak * 100 > cfg["soft"]:
            ups, third, peak = 0, None, c
        prev = c
    # the third rise must be THIS minute or the one just before it - the door is
    # the turn itself, not a memory of one (the 09-09 한화오션 lesson: a signal
    # stamped at 10:49 was still being called alive at 10:53 while price walked
    # down)
    if seen_fall and ups >= cfg["ups"] and third:
        if third in (str(bars[-1].get("hhmm") or "")[:5],
                     str(bars[-2].get("hhmm") or "")[:5]):
            return True, third
    return False, None


def _fall_speed(bars: list[dict], cfg: dict) -> tuple[str, float, int, float, str]:
    """Is the price falling, and FAST or SLOW? -> (kind, % off the peak, minutes, peak, peak clock).

    kind is 'fast' (his "sharp decrease" - never sell into it), 'slow' (his
    "slowly decrease" - sell a slice after the peak), or '' (not falling).
    """
    if len(bars) < 2:
        return "", 0.0, 0, 0.0, ""
    px = bars[-1]["close"]
    # the local peak: walk back while the closes are not higher than the one before
    i = len(bars) - 1
    peak_i, peak = i, bars[i]["close"]
    j = i
    while j > 0 and (len(bars) - j) <= 30:
        if bars[j]["close"] > peak:
            peak, peak_i = bars[j]["close"], j
        j -= 1
        if bars[j]["close"] < peak * (1 - 0.9 / 100):   # far enough back
            break
    off = (peak - px) / peak * 100 if peak else 0.0
    mins = len(bars) - 1 - peak_i
    peak_at = str(bars[peak_i].get("hhmm") or "")[:5]
    if off <= 0.01:
        return "", 0.0, 0, peak, peak_at
    # FAST: the drop inside the last spike_min minutes, or one very red candle
    w = bars[-(cfg["spike_min"] + 1):]
    quick = (w[0]["close"] - px) / w[0]["close"] * 100 if w and w[0]["close"] else 0.0
    worst = min(((b["close"] / b["open"] - 1) * 100) for b in bars[-cfg["spike_min"]:]
                if b["open"])
    if quick >= cfg["spike_pct"] or worst <= -cfg["spike_bar"]:
        return "fast", off, mins, peak, peak_at
    if off >= cfg["drift_pct"] and mins >= cfg["drift_min"]:
        return "slow", off, mins, peak, peak_at
    return "", off, mins, peak, peak_at


def _drop_shape(bars: list[dict], cfg: dict) -> tuple[str, int, float]:
    """계단인가, 아닌가 — IS THIS FALL A STAIRCASE OR A CASCADE OF REAL DROPS?

    Boss 2026-09-09: "in the SKhynix case if there is a 계단 3 drop then we do not
    sell, because within 3 minutes it decreases and it jumps again... but if it is
    NOT 계단 - like one big drop, then small, then one red, then again one big -
    then it is a selling case, we do not wait and sell 20%."

    Two shapes, and the difference is not the depth of the fall but how it is
    BUILT. A staircase walks down in steps of the same size and snaps back; a
    cascade drops hard, pauses or bounces feebly, and then drops hard AGAIN -
    that second big candle is somebody still selling, and it is worth taking a
    slice off rather than waiting.

    WHAT "BIG" HAS TO MEAN. Read only against the tape's own typical minute, his
    OWN hold-cases would flip to sells: 000660's 14:15-14:21 - which he pointed
    at as "wait, then buy at 14:24" - is four drops of 0.22-0.32% that look 4-6x
    typical simply because the early-afternoon tape was almost still (0.054% a
    minute). So a BIG drop must clear an absolute floor as well as the relative
    one. At 0.35% his 09:24-09:26 staircase holds one big candle and his
    14:15-14:21 holds none, so both stay holds - exactly as he described them -
    while a genuine two-punch cascade still registers.

    Returns (shape, how many BIG drops, the fall off the window's high).
    """
    import statistics
    w = bars[-(cfg["cascade_win"] + 1):]
    if len(w) < 4:
        return "", 0, 0.0
    ref = bars[-(cfg["vol_win"] + 1):-1] or bars[:-1]
    moves = [abs(ref[k]["close"] - ref[k - 1]["close"]) / ref[k - 1]["close"] * 100
             for k in range(1, len(ref))]
    typ = statistics.median(moves) if moves else 0.0
    hi = max(b["close"] for b in w)
    px = w[-1]["close"]
    off = (hi - px) / hi * 100 if hi else 0.0
    steps = [((w[k]["close"] - w[k - 1]["close"]) / w[k - 1]["close"] * 100)
             for k in range(1, len(w))]
    big = [i for i, ch in enumerate(steps)
           if ch <= -cfg["big_pct"] and (not typ or abs(ch) >= typ * cfg["big_x"])]
    if len(big) >= cfg["cascade_n"]:
        # they must be SEPARATED - a run of big candles back to back is still a
        # staircase, just a steep one; a cascade pauses and comes again
        if any(big[i + 1] - big[i] >= 2 for i in range(len(big) - 1)):
            return "cascade", len(big), off
    downs = 0
    for ch in reversed(steps):
        if ch < 0:
            downs += 1
        elif abs(ch) > cfg["soft"]:
            break
    if downs >= 3 or off >= cfg["spike_pct"]:
        return "stair", len(big), off
    return "", len(big), off


def _vol_x(bars: list[dict], cfg: dict) -> float:
    """This minute's volume against its own trailing average - the number he asked
    to be checked ("you should check volume also. In most case volume will be high
    before price increase"). It is REPORTED on every decision and it sizes nothing:
    measured on his five buy marks today it was 0.29 / 0.67 / 0.48 / 0.74 / 0.82 of
    the trailing average, so a volume floor would have refused every buy he named.
    Where it does decide is the fall - a fall still accelerating on rising volume
    is not a bottom yet."""
    if len(bars) < 2:
        return 0.0
    w = bars[-(cfg["vol_win"] + 1):-1]
    avg = sum(b.get("vol") or 0 for b in w) / max(1, len(w))
    return round((bars[-1].get("vol") or 0) / avg, 2) if avg else 0.0


# ── size ─────────────────────────────────────────────────────────────────────
_ADVC: dict = {}          # (code, day) -> average daily volume


def _adv_day() -> str:
    try:
        from services.kiwoom_tape import _day
        return _day()
    except Exception:
        return ""


def liquidity_cap(code: str, bars: list[dict] | None, cfg: dict) -> int:
    """THE MOST SHARES THIS STOCK CAN ACTUALLY DEAL (boss 2026-09-10: "for other
    the maximum number should be based on the market, because if we fix then if
    there is not this much we cannot deal").

    A flat ceiling of 10,000 shares is nothing in 삼성전자 and impossible in a
    thin name. Measured on yesterday's tape, the fixed size was 0.1x a minute's
    volume in SK하이닉스 and 0.3x in 삼성전자 - but 39.9x a minute in 한화시스템,
    38.6x in 한국항공우주 and 35.5x in HD한국조선해양, which is not an order, it is
    a wish. Two ceilings, whichever is lower:

      · a share of the 20-day AVERAGE DAILY VOLUME (liq_adv_pct, default 0.5%)
      · a multiple of the MEDIAN MINUTE traded right now (liq_min_mult, default
        3x) - the live one, so a stock that has gone quiet today is sized for
        today and not for its history

    Returns 0 when neither can be read, and the caller then falls back to the
    budget and the flat ceiling as before."""
    import statistics
    caps = []
    # THE DAILY TABLE IS ASKED ONCE A DAY PER STOCK, NOT ONCE A MINUTE. This runs
    # inside every decision, for every stock, on a 20-second clock - a database
    # round trip there would be twenty queries a minute all session for a number
    # that changes once a day.
    key = (str(code), _adv_day())
    if key in _ADVC:
        adv = _ADVC[key]
    else:
        adv = 0.0
        try:
            from services.approval_desk import _daily3
            vols = [b["v"] for b in (_daily3(str(code), 20) or []) if b.get("v")]
            adv = (sum(vols) / len(vols)) if vols else 0.0
        except Exception:
            adv = 0.0
        if len(_ADVC) > 400:
            _ADVC.clear()
        _ADVC[key] = adv
    if adv:
        caps.append(int(adv * cfg["liq_adv_pct"] / 100))
    try:
        if bars and len(bars) >= 10:
            med = statistics.median([b.get("vol") or 0 for b in bars[-30:]])
            if med > 0:
                caps.append(int(med * cfg["liq_min_mult"]))
    except Exception:
        pass
    caps = [c for c in caps if c > 0]
    return min(caps) if caps else 0


def base_lot(price: float, code: str = "", bars: list[dict] | None = None,
             cfg: dict | None = None) -> int:
    """The first buy's share count: his floor, the budget, and what the market
    can take - whichever binds first - rounded DOWN to a clean lot.

    SK하이닉스 at ₩1,812,000 still sends exactly 1,000, the number he named
    ("for skhynix minimum stock buying in our case is 1000"): the ₩2B budget
    allows 1,103 and its own volume would allow 19,000, so the budget binds and
    rounds to 1,000. A thin name is now sized by its tape instead of by a
    constant it could never fill."""
    cfg = {**CFG, **(cfg or {})}
    from services.approval_desk import BUY_BUDGET, MAX_QTY, MIN_QTY, MIN_LOT
    try:
        px = float(price or 0)
    except Exception:
        px = 0.0
    if px <= 0:
        return 0
    q = min(int(BUY_BUDGET // px), MAX_QTY)
    if q < MIN_QTY:
        q = MIN_QTY                     # his floor: an expensive stock still gets a real position
    lq = liquidity_cap(code, bars, cfg) if code or bars else 0
    if lq:
        q = min(q, lq)                  # ...but never more than the market can deal
    if q >= 1000:
        return (q // 1000) * 1000
    if q >= MIN_LOT:
        return (q // MIN_LOT) * MIN_LOT
    return MIN_LOT


def slice_qty(high_water: int, cfg: dict) -> int:
    """A ladder slice: 20% of the biggest the position has been today, in whole
    100-share lots. Constant through the day - "sell 20%" five times means five
    equal sells, not a geometric series that never quite ends."""
    from services.approval_desk import MIN_LOT
    q = int(high_water * cfg["slice_pct"] / 100)
    q = (q // MIN_LOT) * MIN_LOT
    return max(MIN_LOT, q)


def new_state(code: str, name: str = "") -> dict:
    return {"code": str(code), "name": name or str(code), "qty": 0, "first_px": 0.0,
            "avg_px": 0.0, "high_water": 0, "steps": 0, "adds": 0, "sold": 0,
            "spike_at": None, "last_at": None, "peak": 0.0, "cost": 0.0,
            # the three marks that stop the ladder repeating itself
            "last_side": "",     # a buy-back may not follow a buy-back
            "buy_at": None,      # when we last bought - a spike after it re-opens the door
            "drift_at": "",      # the peak a drift slice was already taken from
            "sell_px": 0.0,      # the last price we sold at - a buy-back must be under it
            "sell_at": None,     # ...and when, so slices are not taken on top of each other
            "owed": 0}           # slices sold and not yet bought back


def _mins(a: str, b: str) -> int:
    """Minutes between two HH:MM stamps."""
    try:
        return (int(b[:2]) * 60 + int(b[3:5])) - (int(a[:2]) * 60 + int(a[3:5]))
    except Exception:
        return 999


# ── THE RULE ─────────────────────────────────────────────────────────────────
def decide(bars: list[dict], st: dict, cfg: dict | None = None,
           gap: float | None = None) -> Optional[dict]:
    """What the rule says at the LAST bar of `bars`. None = do nothing.

    Nothing here reads beyond bars[-1], so the same function answers live and in
    a replay of any past minute - the only way a rule about when we buy can be
    checked against the day it decided.
    """
    cfg = {**CFG, **(cfg or {})}
    if not bars:
        return None
    b = bars[-1]
    now = str(b.get("hhmm") or "")[:5]
    px = float(b.get("close") or 0)
    if px <= 0:
        return None
    volx = _vol_x(bars, cfg)
    kind, off_peak, off_min, peak_px, peak_at = _fall_speed(bars, cfg)
    shape, n_big, off_win = _drop_shape(bars, cfg)
    # A CASCADE IS NOT A SPIKE. Only a staircase earns the protection - the shape
    # that snaps back. A fall that keeps landing big candles is the one shape we
    # do NOT sit through (boss 2026-09-09).
    if kind == "fast" and shape != "cascade":
        st["spike_at"] = now
    protected = bool(st.get("spike_at")
                     and _mins(st["spike_at"], now) <= cfg["spike_hold"])

    def out(side, qty, why_ko, why_en, tag):
        return {"side": side, "qty": int(qty), "at": now, "px": px, "tag": tag,
                "volx": volx, "peak": peak_px, "peak_at": peak_at,
                "ko": why_ko, "en": why_en}

    # ① THE BELL. Everything is flat at the close - his last sentence.
    if now >= cfg.get("eod_from", cfg["eod"]):
        if st["qty"] > 0:
            pnl = (px / st["avg_px"] - 1) * 100 if st["avg_px"] else 0.0
            return out("SELL", st["qty"],
                       f"장 마감 정리 — {cfg['eod']} 마감 전 전량 매도. 남은 {st['qty']:,}주를 "
                       f"₩{px:,.0f}에 정리합니다 (평균가 대비 {pnl:+.2f}%). "
                       f"{cfg['eod']} 이후는 종가 단일가라 주문이 체결되지 않아, 그 직전 "
                       f"가격에 냅니다.",
                       f"closing flat - everything out by {cfg['eod']}: {st['qty']:,} sh at "
                       f"₩{px:,.0f} ({pnl:+.2f}% on average cost). After {cfg['eod']} the "
                       f"market is in its closing auction where our orders cannot deal, so "
                       f"this goes at the last price before it.", "eod")
        return None

    # ② NOTHING HELD: the day gate, then his 3 red.
    if st["qty"] <= 0:
        # ── GATE 1 (boss 2026-09-09: "if there is not 갭상승, or it back to normal
        # price, then buy"). A day that opened above yesterday is not closed for
        # ever - it is closed until the price comes BACK to yesterday's last. The
        # desk's own send-time guard has read it that way since 09-03; the ladder
        # now reads it the same, so the two cannot disagree about a gap day.
        if gap is not None and gap > cfg["gap_tol"]:
            if not cfg.get("gap_return"):
                return None                  # the day is simply closed for buying
            ref = bars[0]["open"] / (1 + gap / 100) if gap > -100 else 0
            if not (ref and min(b["low"] for b in bars) <= ref):
                return None                  # still above yesterday - nothing to buy yet
        ok, third = _turn(bars, cfg)
        if not ok:
            return None
        if st.get("last_at") and _mins(st["last_at"], now) < cfg["cool_min"]:
            return None
        qty = base_lot(px, st.get("code"), bars, cfg)
        return out("BUY", qty,
                   f"진입 — 하락이 멈추고 {third}에 3번째 양봉이 섰습니다. "
                   f"갭상승 없이 시작한 날이라 규칙대로 {qty:,}주를 ₩{px:,.0f}에 삽니다 "
                   f"(거래량 x{volx}).",
                   f"entry - the fall stopped and the 3rd rising candle stood at {third}. "
                   f"The day did not open above yesterday, so the rule buys {qty:,} sh at "
                   f"₩{px:,.0f} (volume x{volx}).", "entry")

    # ③ HOLDING.
    pnl = (px / st["avg_px"] - 1) * 100 if st["avg_px"] else 0.0
    gain = (px / st["first_px"] - 1) * 100 if st["first_px"] else 0.0
    st["peak"] = max(float(st.get("peak") or 0), px)

    # ③-a the floor no shape argues with
    if pnl <= cfg["hard_stop"]:
        return out("SELL", st["qty"],
                   f"손절 — 평균 매수가 ₩{st['avg_px']:,.0f} 대비 {pnl:+.2f}%. "
                   f"급락 보호도 여기까지입니다({cfg['hard_stop']}%). 전량 정리합니다.",
                   f"stop - {pnl:+.2f}% against an average cost of ₩{st['avg_px']:,.0f}; the "
                   f"fast-fall grace ends at {cfg['hard_stop']}%. Everything out.", "hardstop")
    # ③-b his -1% law - but a fast fall is given its minutes first
    if pnl <= cfg["stop_pct"]:
        if not protected:
            return out("SELL", st["qty"],
                       f"손절 — 평균 매수가 대비 {pnl:+.2f}% (기준 {cfg['stop_pct']}%). "
                       f"급락이 아니라 천천히 밀려 내려온 자리라 규칙대로 정리합니다.",
                       f"stop - {pnl:+.2f}% against average cost (the {cfg['stop_pct']}% law). "
                       f"This is a slow slide, not a fast fall, so the floor applies.", "stop")
        return None                          # protected: the fast fall gets its minutes

    # ③-b2 계단이 아니면 기다리지 않는다 — the cascade slice (boss 2026-09-09:
    # "we do not wait and sell 20%"). It comes off before the profit rung is
    # considered, because the whole point is not waiting for a number.
    if (cfg["cascade_sell"] and shape == "cascade"
            and (not cfg["cascade_profit_only"] or gain > 0)):
        if not st.get("sell_at") or _mins(st["sell_at"], now) >= cfg["cool_min"]:
            q = min(st["qty"], slice_qty(st["high_water"], cfg))
            return out("SELL", q,
                       f"계단이 아닙니다 — 큰 음봉이 {n_big}번 나왔고 사이의 반등이 약합니다 "
                       f"(최근 {cfg['cascade_win']}분 고점 대비 {off_win:.2f}%). 계단식 하락은 "
                       f"기다리지만 이 모양은 기다리지 않습니다. {q:,}주를 ₩{px:,.0f}에 덜어냅니다 "
                       f"(현재 {gain:+.2f}%).",
                       f"not a staircase - {n_big} big down candles with only a feeble bounce "
                       f"between them ({off_win:.2f}% off the {cfg['cascade_win']}-minute high). "
                       f"A staircase is waited out; this shape is not. Taking {q:,} sh off at "
                       f"₩{px:,.0f} ({gain:+.2f}%).", "cascade")

    # ③-c a slice at every +1.5% step (measured from the FIRST buy, his words)
    rung = cfg["step"] * (st["steps"] + 1)
    if gain >= rung:
        q = min(st["qty"], slice_qty(st["high_water"], cfg))
        return out("SELL", q,
                   f"+{gain:.2f}% — 첫 매수가 ₩{st['first_px']:,.0f} 기준 {rung:.1f}% "
                   f"구간을 넘었습니다. {cfg['slice_pct']}%인 {q:,}주를 ₩{px:,.0f}에 팝니다.",
                   f"+{gain:.2f}% above the first buy of ₩{st['first_px']:,.0f} - the "
                   f"{rung:.1f}% rung. Selling the {cfg['slice_pct']}% slice, {q:,} sh at "
                   f"₩{px:,.0f}.", "step")

    # ③-d a slice when a rise rolls over into a SLOW slide, while in profit.
    # ONE SLICE PER PEAK, AND A PEAK IS COUNTED BY ITS CLOCK, NOT ITS HEIGHT.
    # A slide stays a slide for as long as it lasts, so without this mark the
    # same roll-over sold six times between 10:17 and 11:05 in the replay and
    # the position was gone by lunch. Requiring a HIGHER peak was the other
    # mistake: his 11:51, 12:03 and 12:23 slices come off three successive
    # LOWER peaks (₩1,880,000 -> ₩1,872,000 -> ₩1,865,000) as the afternoon
    # rolls over step by step, and a height test refused all three.
    if (kind == "slow" and gain >= cfg["step"] and not protected
            and peak_at and peak_at > str(st.get("drift_at") or "")):
        # AND SLICES ARE NOT TAKEN ON TOP OF EACH OTHER. His own five sells are
        # 49, 73, 12 and 20 minutes apart; the replay without this spacing took
        # three inside twenty minutes (10:17 / 10:30 / 10:38) and two more at
        # 11:03 / 11:06, which emptied the position before lunch and left the
        # afternoon with nothing to work.
        if (not st.get("sell_at")
                or _mins(st["sell_at"], now) >= cfg["slice_gap"]):
            q = min(st["qty"], slice_qty(st["high_water"], cfg))
            return out("SELL", q,
                       f"고점에서 천천히 밀립니다 — {off_min}분 동안 {off_peak:.2f}% "
                       f"(급락이 아닙니다). 수익 {gain:+.2f}% 상태라 {q:,}주를 ₩{px:,.0f}에 "
                       f"덜어냅니다.",
                       f"rolling over slowly - {off_peak:.2f}% off the local peak over "
                       f"{off_min} min, which is a drift and not a fast fall. We are "
                       f"{gain:+.2f}% up, so one {q:,}-share slice comes off at ₩{px:,.0f}.",
                       "drift")

    # ③-e buy back on the next 3 red - a full lot after a fast fall, a slice after a sell.
    # TWO FENCES. The position never grows past max_lots base lots (his own
    # example tops out at 1,000 + 1,000), and a slice is only bought back BELOW
    # the price it was sold at - a ladder that buys its slices back higher is
    # just paying for the privilege of trading.
    cap = cfg["max_lots"] * base_lot(px, st.get("code"), bars, cfg)
    if st["adds"] < cfg["max_adds"] and st["qty"] < cap:
        ok, third = _turn(bars, cfg)
        if ok and (not st.get("last_at") or _mins(st["last_at"], now) >= cfg["cool_min"]):
            # ONE BUY-BACK PER SLICE SOLD. Without this the last sale kept
            # paying for buy after buy - four of them between 12:10 and 13:03 in
            # the replay, all within ₩10,000 of each other, and the day's budget
            # was spent before his 14:24 turn ever arrived.
            # A FAST FALL IS ITS OWN PERMISSION. His 13:10 and his 14:15-14:21
            # are both "do not sell, wait for it to stop, and when it starts
            # increasing buy again" - no sale comes in between, the drop itself
            # is the reason. Everything else is a ladder buy-back and must obey
            # the two fences under it.
            fresh = bool(st.get("spike_at") and (not st.get("buy_at")
                         or _mins(st["buy_at"], st["spike_at"]) > 0))
            # HIS GENERAL LAW, 2026-09-10: "during trading even if we have a
            # stock (holding), if price down and again start increasing then in
            # this case we will buy." A dip that turns is a buy whether or not a
            # slice was sold first and whether or not the fall was fast - those
            # were the only two doors before, so a plain pullback inside a
            # holding did nothing at all. The turn still has to be a real one
            # (a fall of at least dip_pct, then the 3rd rise), the position still
            # cannot pass max_lots, and two adds cannot land inside add_gap
            # minutes of each other.
            _plain = not fresh and st["sold"] <= 0
            if _plain and st.get("buy_at") and _mins(st["buy_at"], now) < cfg["add_gap"]:
                return None
            if not fresh and not _plain and st["sold"] > 0 and int(st.get("owed") or 0) <= 0:
                return None
            # AND THE LADDER ALTERNATES. Two buy-backs in a row is not a ladder,
            # it is an average-down: the replay stacked four of them between
            # 12:28 and 13:22, filled the position to its cap, and had nothing
            # left when his own 14:24 turn arrived. A slice bought back must be
            # sold again before the next one is bought.
            if not fresh and not _plain and st["sold"] > 0 and st.get("last_side") == "BUY":
                return None
            if not fresh and not _plain and st["sold"] > 0 and st.get("sell_px") and px > st["sell_px"]:
                return None
            if fresh and st["sold"] == 0:
                q = min(base_lot(px, st.get("code"), bars, cfg), cap - st["qty"])
                ko = (f"급락 뒤 회복 — {st['spike_at']}의 빠른 하락이 멈추고 {third}에 "
                      f"3번째 양봉이 섰습니다. 팔지 않고 기다린 자리에서 {q:,}주를 "
                      f"₩{px:,.0f}에 추가로 삽니다 (거래량 x{volx}).")
                en = (f"back up after the fast fall - the drop at {st['spike_at']} stopped and "
                      f"the 3rd rising candle stood at {third}. We did not sell into it; we add "
                      f"{q:,} sh at ₩{px:,.0f} (volume x{volx}).")
            elif st["sold"] > 0:
                q = min(slice_qty(st["high_water"], cfg), cap - st["qty"])
                ko = (f"되사기 — 하락이 멈추고 {third}에 3번째 양봉이 섰습니다. "
                      f"덜어낸 {cfg['slice_pct']}%인 {q:,}주를 ₩{px:,.0f}에 되삽니다 "
                      f"(거래량 x{volx}).")
                en = (f"buying the slice back - the fall stopped and the 3rd rising candle "
                      f"stood at {third}. {q:,} sh at ₩{px:,.0f}, the same {cfg['slice_pct']}% "
                      f"we sold (volume x{volx}).")
            elif _plain:
                q = min(slice_qty(st["high_water"], cfg), cap - st["qty"])
                ko = (f"보유 중 눌림 매수 — 내리다가 멈추고 {third}에 3번째 양봉이 "
                      f"섰습니다. 들고 있는 자리에서 {q:,}주를 ₩{px:,.0f}에 더 삽니다 "
                      f"(평균 ₩{st['avg_px']:,.0f} 대비 {pnl:+.2f}%, 거래량 x{volx}).")
                en = (f"adding into a pullback while holding - the fall stopped and the 3rd "
                      f"rising candle stood at {third}. {q:,} sh more at ₩{px:,.0f} "
                      f"({pnl:+.2f}% against an average of ₩{st['avg_px']:,.0f}, volume x{volx}).")
            else:
                return None
            return out("BUY", q, ko, en, "add")
    return None


def apply(st: dict, d: dict) -> dict:
    """Book a decision into the running state. Returns the state."""
    q, px = int(d["qty"]), float(d["px"])
    if d["side"] == "BUY":
        if st["qty"] <= 0:
            st["first_px"], st["cost"], st["qty"] = px, px * q, q
            st["steps"], st["sold"], st["adds"], st["spike_at"] = 0, 0, 0, None
        else:
            st["cost"] += px * q
            st["qty"] += q
            st["adds"] += 1
            if d.get("tag") == "add":
                st["spike_at"] = None        # the fast fall has been answered
            if st["sold"] > 0:
                st["owed"] = max(0, int(st.get("owed") or 0) - 1)
                if st["owed"] == 0:
                    st["sell_px"] = 0.0
        st["avg_px"] = st["cost"] / max(1, st["qty"])
        st["high_water"] = max(st["high_water"], st["qty"])
        st["peak"] = px
        st["buy_at"] = d["at"]
    else:
        q = min(q, st["qty"])
        st["cost"] -= st["avg_px"] * q
        st["qty"] -= q
        st["sold"] += q
        st["sell_px"] = px
        st["sell_at"] = d["at"]
        if d.get("tag") in ("step", "drift"):
            st["owed"] = int(st.get("owed") or 0) + 1
        if d.get("tag") == "step":
            st["steps"] += 1
        if d.get("tag") == "drift":
            st["drift_at"] = str(d.get("peak_at") or d.get("at") or "")
        if st["qty"] <= 0:
            code, name = st["code"], st["name"]
            st.update(new_state(code, name))
    st["last_at"] = d["at"]
    st["last_side"] = d["side"]
    return st


def replay(code: str, name: str = "", day: str = "", cfg: dict | None = None,
           bars: list[dict] | None = None, gap: float | None = None) -> dict:
    """Run the rule over a whole day's tape, minute by minute, with no lookahead.

    This is the proof surface: every decision it returns comes from the same call
    the live desk makes, given the same bars.
    """
    cfg = {**CFG, **(cfg or {})}
    bars = bars if bars is not None else minute_bars(code, day)
    if not bars:
        return {"ok": False, "code": code, "err": "no tape"}
    if gap is None:
        ref = prev_last(code, day)
        gap = ((bars[0]["open"] / ref - 1) * 100) if ref else 0.0
    st = new_state(code, name)
    trades: list[dict] = []
    for i in range(1, len(bars) + 1):
        d = decide(bars[:i], st, cfg, gap)
        if not d:
            continue
        apply(st, d)
        trades.append(d)
    last = bars[-1]["close"]
    realised, pos, cost = 0.0, 0, 0.0
    for t in trades:
        if t["side"] == "BUY":
            pos += t["qty"]
            cost += t["qty"] * t["px"]
        else:
            avg = cost / pos if pos else t["px"]
            realised += (t["px"] - avg) * t["qty"]
            cost -= avg * t["qty"]
            pos -= t["qty"]
    open_pnl = (last * pos - cost) if pos else 0.0
    spent = sum(t["qty"] * t["px"] for t in trades if t["side"] == "BUY") or 1.0
    return {"ok": True, "code": code, "name": name, "gap": round(gap, 3),
            "bars": len(bars), "trades": trades,
            "buys": sum(1 for t in trades if t["side"] == "BUY"),
            "sells": sum(1 for t in trades if t["side"] == "SELL"),
            "realised": round(realised), "open": round(open_pnl),
            "pnl_pct": round((realised + open_pnl) / spent * 100, 3),
            "left": pos}
