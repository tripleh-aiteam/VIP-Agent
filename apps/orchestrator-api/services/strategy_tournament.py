"""🏁 3-strategy shadow tournament (boss 2026-07-20): run Algorithm 1 (daily),
Algorithm 2 · Ripple, and Algorithm 2 · Candle 3-2 SIDE BY SIDE on the same
stocks, same live prices, same day — with three separate VIRTUAL books so they
never fight over one paper account (Algo 1 + Algo 2 share the real desk, and
Algo 2 can only run one strategy at a time, so a live 3-way race is impossible
without shadowing).

Each strategy trades a fixed ₩10M notional per position so the honest score is
avg-%-per-trade, not who had more cash. Positions + closed trades are DB-persisted
so a Render restart mid-day doesn't lose the race. Graded/reported after 15:20.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text

from services.scalp_trader import (KST, EOD_FLAT_HHMM, _px, _name,
                                   _market_open_now, _streaks_1m)

logger = logging.getLogger(__name__)

NOTIONAL = 10_000_000          # ₩10M per virtual position — equal footing
TAKE_PCT = 0.4                 # ripple take (same as the live ripple default)
STOP_PCT = 1.0                 # hard stop, all strategies
RISES = 2                      # ripple: consecutive up ticks to confirm the turn
STRATS = ("algo1", "ripple", "candle")
# same basket for all three — liquid names the boss watches
BASKET = ["005930", "000660", "005490", "035420", "000270", "005380"]

_tbuf: dict[str, deque] = {}   # code -> recent (ts, px) for ripple's turn detection
_algo1_done: set[str] = set()  # algo1 decides ONCE per day per stock (daily horizon)


def _ensure(db) -> None:
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS tournament_pos ("
        " strategy TEXT, ticker TEXT, name TEXT, entry DOUBLE PRECISION, qty BIGINT,"
        " opened_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (strategy, ticker))"))
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS tournament_trades ("
        " id SERIAL PRIMARY KEY, strategy TEXT, ticker TEXT, name TEXT,"
        " entry DOUBLE PRECISION, exit_price DOUBLE PRECISION, net_pct DOUBLE PRECISION,"
        " reason TEXT, opened_at TIMESTAMPTZ, closed_at TIMESTAMPTZ DEFAULT now())"))
    db.commit()


def _open(db, strat: str, code: str, px: float) -> None:
    qty = max(1, int(NOTIONAL / px))
    db.execute(text(
        "INSERT INTO tournament_pos (strategy, ticker, name, entry, qty) "
        "VALUES (:s,:t,:n,:e,:q) ON CONFLICT (strategy, ticker) DO NOTHING"),
        {"s": strat, "t": code, "n": _name(code), "e": px, "q": qty})
    db.commit()


def _close(db, strat: str, code: str, entry: float, opened_at, px: float, reason: str) -> None:
    net = (px / entry - 1) * 100 - 0.23      # net of round-trip fees, like the live desks
    db.execute(text(
        "INSERT INTO tournament_trades (strategy, ticker, name, entry, exit_price, "
        "net_pct, reason, opened_at) VALUES (:s,:t,:n,:e,:x,:p,:r,:o)"),
        {"s": strat, "t": code, "n": _name(code), "e": entry, "x": px,
         "p": round(net, 3), "r": reason, "o": opened_at})
    db.execute(text("DELETE FROM tournament_pos WHERE strategy=:s AND ticker=:t"),
               {"s": strat, "t": code})
    db.commit()


def _ripple_entry(code: str, px: float) -> bool:
    buf = _tbuf.get(code)
    if not buf or len(buf) < RISES + 2:
        return False
    prices = [p for _, p in buf]
    lo = min(prices)
    if lo <= 0:
        return False
    bounce = (px / lo - 1) * 100
    rising = all(prices[i] < prices[i + 1] for i in range(len(prices) - RISES - 1, len(prices) - 1))
    return rising and 0.10 <= bounce <= 0.45


def tick(db) -> dict[str, Any]:
    """One heartbeat: update prices, open/close each strategy's virtual positions."""
    _ensure(db)
    if not _market_open_now():
        return {"skipped": "closed"}
    now = datetime.now(KST)
    eod_scalp = (now.hour * 60 + now.minute) >= (EOD_FLAT_HHMM[0] * 60 + EOD_FLAT_HHMM[1])
    eod_algo1 = (now.hour * 60 + now.minute) >= (15 * 60 + 20)

    live: dict[str, float] = {}
    for code in BASKET:
        p = _px(code)
        if p is None:
            continue
        live[code] = float(p)
        _tbuf.setdefault(code, deque(maxlen=20)).append((now.timestamp(), float(p)))

    open_rows = db.execute(text(
        "SELECT strategy, ticker, entry, qty, opened_at FROM tournament_pos")).fetchall()
    open_map = {(r[0], r[1]): (float(r[2]), r[4]) for r in open_rows}
    out = {"opened": [], "closed": []}

    # ---- EXITS ---- #
    for (strat, code), (entry, oat) in open_map.items():
        px = live.get(code)
        if px is None:
            continue
        reason = None
        if px <= entry * (1 - STOP_PCT / 100):
            reason = "STOP"
        elif strat == "ripple":
            if px >= entry * (1 + TAKE_PCT / 100):
                reason = "TAKE"
            elif eod_scalp:
                reason = "EOD"
        elif strat == "candle":
            _u, _d, _n = _streaks_1m(code)
            if _n >= 2 and _d >= 2:
                reason = "CANDLE2"
            elif eod_scalp:
                reason = "EOD"
        elif strat == "algo1":
            if eod_algo1:
                reason = "EOD"      # daily strategy: entered in the morning, marked out at close
        if reason:
            _close(db, strat, code, entry, oat, px, reason)
            out["closed"].append({"strategy": strat, "code": code, "reason": reason})

    # ---- ENTRIES (one open position per strategy+stock) ---- #
    held = {(r[0], r[1]) for r in db.execute(
        text("SELECT strategy, ticker FROM tournament_pos")).fetchall()}
    # algo1 = daily 3-method → decides ONCE per stock per day. DB-backed (survives
    # restarts): a code is "done" if algo1 holds it OR already traded it today.
    algo1_today = {r[0] for r in db.execute(text(
        "SELECT ticker FROM tournament_trades WHERE strategy='algo1' "
        "AND closed_at::date=(now() AT TIME ZONE 'Asia/Seoul')::date")).fetchall()}
    algo1_today |= {c for (s, c) in held if s == "algo1"}
    if not eod_scalp:
        for code in BASKET:
            px = live.get(code)
            if px is None:
                continue
            # ripple
            if ("ripple", code) not in held and _ripple_entry(code, px):
                _open(db, "ripple", code, px); out["opened"].append(["ripple", code])
            # candle 3-2
            if ("candle", code) not in held:
                _u, _d, _n = _streaks_1m(code)
                if _n >= 3 and _u >= 3:
                    _open(db, "candle", code, px); out["opened"].append(["candle", code])
            # algo1 — daily 3-method, decide ONCE per stock per day (long horizon)
            if code not in algo1_today and not eod_algo1:
                algo1_today.add(code)
                try:
                    from services.decision_agent import decide
                    d = decide(db, code) or {}
                    if (d.get("decision") or "").upper() == "BUY":
                        _open(db, "algo1", code, px); out["opened"].append(["algo1", code])
                except Exception:
                    db.rollback()
    return out


def _reset_daily() -> None:
    """Called at open — algo1 gets to decide fresh each day."""
    _algo1_done.clear()


def report(db, lang: str = "ko") -> str:
    """Per-strategy scorecard for TODAY — trips, win%, avg-%-per-trade (the honest
    metric), net ₩ on the ₩10M notional. Includes still-open positions marked live."""
    _ensure(db)
    en = str(lang or "").lower().startswith("en")
    rows = db.execute(text(
        "SELECT strategy, net_pct FROM tournament_trades "
        "WHERE closed_at::date = (now() AT TIME ZONE 'Asia/Seoul')::date")).fetchall()
    by: dict[str, list[float]] = {s: [] for s in STRATS}
    for s, p in rows:
        by.setdefault(s, []).append(float(p or 0))

    labels = {"algo1": ("알고리즘 1 (일봉 3-method)", "Algorithm 1 (daily 3-method)"),
              "ripple": ("알고리즘 2 · 잔물결", "Algorithm 2 · Ripple"),
              "candle": ("알고리즘 2 · 캔들 3-2", "Algorithm 2 · Candle 3-2")}
    scored = []
    for s in STRATS:
        pcts = by.get(s, [])
        n = len(pcts)
        wins = sum(1 for p in pcts if p > 0)
        avg = (sum(pcts) / n) if n else None
        won = sum(p / 100 * NOTIONAL for p in pcts)
        scored.append((s, n, wins, avg, won))
    ranked = sorted([x for x in scored if x[1] > 0], key=lambda x: x[3], reverse=True)

    L = []
    day = datetime.now(KST).strftime("%Y-%m-%d")
    L.append(f"🏁 {'Strategy tournament' if en else '전략 대결'} — {day}")
    for s, n, wins, avg, won in scored:
        lab = labels[s][1 if en else 0]
        if n == 0:
            L.append(f"• {lab}: " + ("no completed trades yet" if en else "완료된 거래 없음"))
            continue
        wr = round(wins / n * 100)
        if en:
            L.append(f"• {lab}: {n} trades · {wr}% win · avg {avg:+.3f}%/trade · "
                     f"net {'+' if won>=0 else ''}₩{round(won):,} (on ₩10M each)")
        else:
            L.append(f"• {lab}: {n}회 · 승률 {wr}% · 평균 {avg:+.3f}%/거래 · "
                     f"순손익 {'+' if won>=0 else ''}₩{round(won):,} (건당 ₩1천만 기준)")
    if ranked:
        win_lab = labels[ranked[0][0]][1 if en else 0]
        if en:
            L.append(f"🏆 Best today by avg-per-trade: {win_lab} ({ranked[0][3]:+.3f}%). "
                     "Win RATE alone is misleading — a −1% stop erases ~6 small wins, so "
                     "average-per-trade is the honest score.")
        else:
            L.append(f"🏆 오늘 최고 (거래당 평균 기준): {win_lab} ({ranked[0][3]:+.3f}%). "
                     "승률만 보면 오해입니다 — −1% 손절 1번이 작은 승리 ~6번을 지웁니다. "
                     "그래서 '거래당 평균'이 정직한 점수입니다.")
    else:
        L.append("아직 완료된 거래가 없습니다 — 장중 신호가 나오면 쌓입니다."
                 if not en else "No completed trades yet — fills accumulate as signals fire.")
    return "\n".join(L)
