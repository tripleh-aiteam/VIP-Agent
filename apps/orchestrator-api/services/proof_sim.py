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
_PLAN = ([-1] * 2 + [+1] * 3 + [-1] * 3            # trade 1: clean 3-up → 3-down
         + [+1] * 2 + [-1] * 2                     # 2-up fakeout — no buy
         + [+1] * 5 + [0] + [-1] * 4               # trade 2: 5-up (buy on 3rd) → flat → 4-down (sell on 3rd)
         + [+1] + [0] + [+1] * 2 + [-1] * 3        # flat breaks the count — no buy (and no position → no sell)
         + [+1] * 3 + [-1] + [+1] + [-1] * 3       # trade 3: 3-up → 1-blue chop (no sell) → 3-down sell
         + [+1] * 2 + [-1] + [+1] * 3 + [-1] * 3   # trade 4: noise then clean 3-up → 3-down
         + [+1] * 3)                               # final 3-up: BUY still HELD at the end — shows the
                                                   # 📌 open-positions table + gold arrow in artificial mode too

_SYMBOLS = [("PRF1", "프루프전자", 205_000), ("PRF2", "시뮬중공업", 19_500), ("PRF3", "테스트화학", 78_000)]


def _synthetic_candles(seed: int, base_px: float) -> list[dict]:
    r = random.Random(seed)
    # end the artificial day at the CURRENT minute — never draw candles from the future
    # (boss 2026-07-30: at 09:47 the chart must not show 09:50)
    n_kst = datetime.now(KST)
    open_t = n_kst.replace(hour=9, minute=0, second=0, microsecond=0)
    day0 = n_kst.replace(second=0, microsecond=0) - timedelta(minutes=len(_PLAN))
    if day0 < open_t:
        day0 = open_t
    out, px = [], float(base_px)
    for i, step in enumerate(_PLAN):
        t = _tick(px)
        delta = 0 if step == 0 else step * t * r.choice([1, 1, 2])
        o, c = px, px + delta
        hi = max(o, c) + t * r.choice([0, 1])
        lo = min(o, c) - t * r.choice([0, 1])
        ts = day0 + timedelta(minutes=i)
        out.append({"time": int(ts.timestamp()) + 9 * 3600,   # +9h so the chart displays KST
                    "hhmm": ts.strftime("%H:%M"),
                    "open": o, "high": hi, "low": lo, "close": c})
        px = c
    return out


def _book(seed: int, ref: float, side: str) -> dict:
    """Synthetic 5-level order book around the decision price. Buys pay the BEST ASK
    (cheapest seller = ref + 1 tick); sells hit the BEST BID (highest buyer = ref)."""
    r = random.Random(seed)
    t = _tick(ref)
    asks = [[ref + t * (i + 1), r.randint(300, 9_500)] for i in range(5)]
    bids = [[ref - t * i, r.randint(300, 9_500)] for i in range(5)]
    return {"asks": asks, "bids": bids, "best_ask": asks[0][0], "best_bid": bids[0][0],
            "fill": asks[0][0] if side == "BUY" else bids[0][0]}


# --------------------------------------------------------------------------- #
#  the minute replay — proof the price is NOT picked randomly from 60 seconds  #
# --------------------------------------------------------------------------- #
def _second_tape(cd: dict, seed: int) -> list[dict]:
    """ALL 60 seconds of the signal minute — a plausible per-second price walk that is
    exactly consistent with the candle (starts at open, touches high & low, ends at close).
    Demonstrates what 'many prices in one minute' really looks like, tick by tick."""
    r = random.Random(seed)
    o, h, l, c = cd["open"], cd["high"], cd["low"], cd["close"]
    t = _tick((o + c) / 2) or 1
    hi_s, lo_s = r.sample(range(8, 52), 2)
    rows = []
    for s in range(60):
        px = o + (c - o) * s / 59 + r.choice([-1, 0, 0, 1]) * t
        px = round(px / t) * t
        if s == 0: px = o
        if s == hi_s: px = h
        if s == lo_s: px = l
        if s == 59: px = c
        px = min(max(px, l), h)
        rows.append({"t": f"{cd['hhmm']}:{s:02d}", "px": px, "qty": r.randint(1, 80) * 10})
    return rows


def _next_sec(hhmm: str) -> str:
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    m += 1
    if m == 60:
        h, m = h + 1, 0
    return f"{h:02d}:{m:02d}:01"


def _minute_timeline(cd: dict, fill_px: float, seed: int, synthetic: bool) -> list[dict]:
    """Second-by-second replay of the SIGNAL minute. One minute = 60 seconds of moving
    prices; the engine reads exactly ONE of them — the CLOSE at :59 — and the fill then
    comes from the order book at the next second. kinds: open / watch (synthetic wiggles)
    / high / low (kiwoom real anchors) / close / fill."""
    rows: list[dict] = []
    hh = cd["hhmm"]
    if synthetic:
        r = random.Random(seed)
        secs = sorted(r.sample(range(6, 54), 3))
        vals = [cd["high"], cd["low"], round((cd["high"] + cd["low"]) / 2)]
        r.shuffle(vals)
        rows.append({"t": f"{hh}:00", "px": cd["open"], "kind": "open"})
        rows += [{"t": f"{hh}:{s:02d}", "px": v, "kind": "watch"} for s, v in zip(secs, vals)]
    else:                                   # real bars: the 4 anchors Kiwoom actually stores
        rows.append({"t": f"{hh}:00", "px": cd["open"], "kind": "open"})
        rows.append({"t": f"{hh}:??", "px": cd["high"], "kind": "high"})
        rows.append({"t": f"{hh}:??", "px": cd["low"], "kind": "low"})
    rows.append({"t": f"{hh}:59", "px": cd["close"], "kind": "close"})
    rows.append({"t": _next_sec(hh), "px": fill_px, "kind": "fill"})
    return rows


# --------------------------------------------------------------------------- #
#  the simulation — calls the REAL engine function candle-by-candle            #
# --------------------------------------------------------------------------- #
def _simulate(candles: list[dict], seed: int, with_book: bool) -> tuple[list, list, list, list]:
    """Replay: after each candle CLOSES, feed all closes so far into the live engine
    comparison (run_steps). 3 rising steps & flat → BUY; holding & 3 falling → SELL.
    Returns (completed trades, no-trade proofs, still-open positions, hold-skips —
    3-ups that could NOT buy because the stock was already held: 1 position per stock)."""
    closes: list[float] = []
    trades: list[dict] = []
    proofs: list[dict] = []       # no-trade proofs (fakeouts the engine correctly ignored)
    skips: list[dict] = []        # 3-up while already holding → no double-buy (visible on chart)
    pos: dict | None = None
    prev_up = prev_dn = 0
    for i, cd in enumerate(candles):
        closes.append(cd["close"])
        up, dn = run_steps(closes)                       # ← REAL engine code
        if pos is None and up == NEED:                   # fires the moment the 3rd red closes
            bk = _book(seed * 1_000 + i, cd["close"], "BUY") if with_book else None
            entry_px = bk["fill"] if bk else cd["close"]
            pos = {"buy_idx": i, "buy_time": cd["time"], "buy_hhmm": cd["hhmm"],
                   "buy_closes": closes[-4:], "entry": entry_px, "buy_book": bk,
                   "buy_timeline": _minute_timeline(cd, entry_px, seed * 31 + i, with_book),
                   "buy_tape": (_second_tape(cd, seed * 11 + i) if with_book else None)}
        elif pos is not None and dn == NEED:             # fires the moment the 3rd blue closes
            bk = _book(seed * 2_000 + i, cd["close"], "SELL") if with_book else None
            exit_px = bk["fill"] if bk else cd["close"]
            net = (exit_px / pos["entry"] - 1) * 100 - FEE_PCT
            trades.append({**pos, "sell_idx": i, "sell_time": cd["time"], "sell_hhmm": cd["hhmm"],
                           "sell_closes": closes[-4:], "exit": exit_px, "sell_book": bk,
                           "sell_timeline": _minute_timeline(cd, exit_px, seed * 37 + i, with_book),
                           "sell_tape": (_second_tape(cd, seed * 13 + i) if with_book else None),
                           "net_pct": round(net, 3)})
            pos = None
        elif pos is not None and up == NEED and i > pos["buy_idx"]:
            skips.append({"idx": i, "hhmm": cd["hhmm"]})   # a 3rd red we could NOT buy — already holding
            if len(proofs) < 8:
                proofs.append({"hhmm": cd["hhmm"], "kind": "already-holding",
                               "note_ko": "3연속 상승이지만 이미 보유 중 → 추가매수 없음 (1종목 1포지션) ✓",
                               "note_en": "3-up but ALREADY HOLDING this stock → no double-buy (one position per stock) ✓"})
        # ---- no-trade proofs: a 2-up that died / a flat that broke the count ----
        if prev_up == 2 and up == 0 and len(proofs) < 6:
            proofs.append({"hhmm": cd["hhmm"], "kind": "fakeout-2up",
                           "note_ko": "양봉 2개뿐 → 3번째가 없어서 매수 안 함 ✓",
                           "note_en": "only 2 red candles → no 3rd, engine did NOT buy ✓"})
        if len(closes) >= 2 and cd["close"] == closes[-2] and prev_up >= 1 and len(proofs) < 6:
            proofs.append({"hhmm": cd["hhmm"], "kind": "flat-break",
                           "note_ko": "보합(같은 종가) → 연속 카운트 리셋, 매수 안 함 ✓",
                           "note_en": "flat close → streak reset, engine did NOT buy ✓"})
        if pos is not None and prev_dn in (1, 2) and dn == 0 and len(proofs) < 8:
            proofs.append({"hhmm": cd["hhmm"], "kind": f"chop-{prev_dn}dn",
                           "note_ko": f"음봉 {prev_dn}개에서 반등 → 3번째가 없어서 매도 안 함 ✓",
                           "note_en": f"only {prev_dn} blue then bounce → no 3rd, engine did NOT sell ✓"})
        prev_up, prev_dn = up, dn
    open_pos: list[dict] = []
    if pos is not None:                                  # bought, still waiting for the 3rd blue
        last = closes[-1] if closes else pos["entry"]
        open_pos.append({**pos, "last_px": last,
                         "unreal_pct": round((last / pos["entry"] - 1) * 100 - FEE_PCT, 3)})
    return trades, proofs, open_pos, skips


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
            "pct": round(passed / total * 100, 1) if total else 100.0, "per_trade": per_trade}


# --------------------------------------------------------------------------- #
#  public entry points                                                         #
# --------------------------------------------------------------------------- #
def run_synthetic(seed: int = 7) -> dict[str, Any]:
    symbols = []
    agg_pass = agg_total = agg_trades = 0
    for k, (code, name, base) in enumerate(_SYMBOLS):
        candles = _synthetic_candles(seed + k * 101, base)
        trades, proofs, open_pos, skips = _simulate(candles, seed + k * 101, with_book=True)
        ver = _verify(candles, trades, with_book=True)
        agg_pass += ver["passed"]; agg_total += ver["total"]; agg_trades += ver["trades"]
        # a CURRENT order book per fake stock (anchored to the last close, changes each
        # minute) — powers the '📗 price table' view: the program trades from THIS table
        lb = _book(seed * 17 + (candles[-1]["time"] if candles else 0), candles[-1]["close"], "BUY")
        live_book = {"asks": lb["asks"], "bids": lb["bids"], "best_ask": lb["best_ask"],
                     "best_bid": lb["best_bid"], "time": datetime.now(KST).strftime("%H:%M:%S")}
        symbols.append({"code": code, "name": name, "candles": candles, "trades": trades,
                        "open_positions": open_pos, "hold_skips": skips, "live_book": live_book,
                        "no_trade_proofs": proofs, "verification": ver})
    return {"source": "synthetic", "seed": seed, "need": NEED,
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
    trades, proofs, open_pos, skips = _simulate(candles, seed=1, with_book=False)
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
            "no_trade_proofs": proofs, "verification": ver}


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
