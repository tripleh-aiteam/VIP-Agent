"""ws_hot_feed.py — PC-side Kiwoom WebSocket TICK feed for the Live Order-Book Monitor.

Kiwoom PUSHES every book change (0D) and every fill (0B) over one WebSocket — no
polling. This feed keeps a `kiwoom_hot` row ~0.5s fresh for EVERY ticker any monitor
is watching (`hot_watch_multi`, row per ticker) — the VIP dashboard, the AI Advisor
and any probe can all watch different stocks at once. (v1 used a single-row watch:
two viewers made it resubscribe in a loop and fills froze — 2026-07-07.)

Runs on the PC because Kiwoom's 지정단말기 IP allowlist rejects most Render instances
(8050). REST relay in rt_snapshot_collector covers any ticker whose row goes stale
(socket drop) — zero-risk fallback. Token: services.kiwoom_rest shared pool only.

Run standalone (test):  python ml/realtime/ws_hot_feed.py
In-process: rt_snapshot_collector starts run_forever() in a daemon thread.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # apps/orchestrator-api
for p in (str(ROOT), str(ROOT / "ml")):
    if p not in sys.path:
        sys.path.insert(0, p)

import websockets                                          # noqa: E402
from websockets.exceptions import ConnectionClosed         # noqa: E402

from services.ws_orderbook_collector import WS_URL, _num, parse_0d   # noqa: E402

_KST = timezone(timedelta(hours=9))
_WRITE_MIN_INTERVAL = 0.4      # per-ticker write throttle
_HEARTBEAT_SEC = 3.0           # re-touch a quiet ticker's row so it never reads stale
_WATCH_STALE_SEC = 60          # a viewer gone this long stops its ticker's stream
_QUOTE_REFRESH_SEC = 15        # slow REST pass for OHLC/VWAP baseline
_MAX_TICKERS = 6               # sane cap on simultaneous subscriptions


class _TickerState:
    __slots__ = ("levels", "tot_bid", "tot_ask", "trades", "quote",
                 "dirty", "last_write", "last_quote_refresh")

    def __init__(self) -> None:
        self.levels: list[dict] = []
        self.tot_bid = self.tot_ask = None
        self.trades: deque = deque(maxlen=30)   # newest FIRST (matches REST payload)
        self.quote: dict = {}
        self.dirty = False
        self.last_write = 0.0
        self.last_quote_refresh = 0.0


def _market_open_now() -> bool:
    n = datetime.now(_KST)
    if n.weekday() >= 5:
        return False
    mins = n.hour * 60 + n.minute
    return (9 * 60 - 5) <= mins <= (15 * 60 + 35)


class HotFeed:
    def __init__(self) -> None:
        self._conn = None
        self.state: dict[str, _TickerState] = {}

    # ------------------------------------------------------------------ db --
    def _db(self):
        if self._conn is None or getattr(self._conn, "closed", 1):
            from _db import get_conn
            self._conn = get_conn()
        return self._conn

    def _watched(self) -> set[str]:
        """All tickers any viewer touched in the last _WATCH_STALE_SEC (multi-table,
        plus the legacy single-row table during the deploy transition)."""
        out: set[str] = set()
        try:
            conn = self._db()
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS hot_watch_multi (ticker TEXT PRIMARY KEY, "
                    "requested_at TIMESTAMPTZ DEFAULT now())")
                cur.execute(
                    "SELECT ticker FROM hot_watch_multi "
                    "WHERE requested_at > now() - interval '%s seconds'" % _WATCH_STALE_SEC)
                out |= {str(r[0]) for r in cur.fetchall() if r[0]}
                try:    # legacy row (old Render build during rollout)
                    cur.execute(
                        "SELECT ticker FROM hot_watch WHERE id=1 "
                        "AND requested_at > now() - interval '%s seconds'" % _WATCH_STALE_SEC)
                    r = cur.fetchone()
                    if r and r[0]:
                        out.add(str(r[0]))
                except Exception:
                    conn.rollback()
            conn.commit()
        except Exception:
            try:
                self._conn and self._conn.rollback()
            except Exception:
                self._conn = None
        return set(sorted(out)[:_MAX_TICKERS])

    def _write_hot(self, tk: str, force: bool = False) -> None:
        st = self.state.get(tk)
        if st is None:
            return
        now = time.time()
        heartbeat = now - st.last_write >= _HEARTBEAT_SEC
        if (not st.dirty and not heartbeat and not force) or now - st.last_write < _WRITE_MIN_INTERVAL:
            return
        payload = {"levels": st.levels, "imbalance": None,
                   "tot_bid": st.tot_bid, "tot_ask": st.tot_ask,
                   "trades": list(st.trades), "quote": st.quote}
        try:
            conn = self._db()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO kiwoom_hot (ticker, payload, updated_at) "
                    "VALUES (%s, %s, now()) "
                    "ON CONFLICT (ticker) DO UPDATE SET payload=EXCLUDED.payload, "
                    "updated_at=now()", (tk, json.dumps(payload)))
            conn.commit()
            st.last_write, st.dirty = now, False
        except Exception as e:
            print(f"[ws-feed] hot write failed ({tk}): {str(e)[:80]}", flush=True)
            try:
                self._conn and self._conn.rollback()
            except Exception:
                pass
            self._conn = None

    def _refresh_quote_rest(self, tk: str) -> None:
        """Slow REST pass per ticker: OHLC baseline + VWAP (0B FID-14's unit differs)."""
        st = self.state.get(tk)
        now = time.time()
        if st is None or now - st.last_quote_refresh < _QUOTE_REFRESH_SEC:
            return
        st.last_quote_refresh = now
        try:
            from services import kiwoom_rest as kr
            q = kr.current_price(tk) or {}
            if q.get("price"):
                keep = st.quote.get("price") or q["price"]     # WS price is fresher
                st.quote = {**q, "price": keep}
            tr = kr.executions(tk, ttl=0.0) or []
            if tr and tr[0].get("acc_amount") and tr[0].get("acc_volume"):
                st.quote["vwap"] = round(tr[0]["acc_amount"] / tr[0]["acc_volume"])
            st.dirty = True
        except Exception:
            pass

    # -------------------------------------------------------------- frames --
    def on_0d(self, tk: str, values: dict) -> None:
        st = self.state.setdefault(tk, _TickerState())
        lv = parse_0d(values)
        if lv:
            st.levels = lv
            ta, tb = _num(values.get("121")), _num(values.get("125"))
            if ta is not None:
                st.tot_ask = abs(ta)
            if tb is not None:
                st.tot_bid = abs(tb)
            st.dirty = True

    def on_0b(self, tk: str, values: dict) -> None:
        """주식체결: 20=time 10=price 12=chg% 15=±qty 13=acc_vol 16/17/18=O/H/L."""
        st = self.state.setdefault(tk, _TickerState())
        px = _num(values.get("10"))
        vol = _num(values.get("15"))
        if px is None or vol is None:
            return
        t = str(values.get("20") or "").strip()
        hhmmss = f"{t[0:2]}:{t[2:4]}:{t[4:6]}" if len(t) >= 6 else datetime.now(_KST).strftime("%H:%M:%S")
        acc = _num(values.get("13"))
        st.trades.appendleft({
            "time": hhmmss, "price": int(abs(px)),
            "qty": int(abs(vol)), "dir": 1 if vol > 0 else -1,
            "acc_volume": int(acc) if acc is not None else None,
            "acc_amount": None,
        })
        chg = _num(values.get("12"))
        st.quote["price"] = abs(px)
        if chg is not None:
            st.quote["change_pct"] = chg
        for fid, key in (("16", "open"), ("17", "high"), ("18", "low")):
            v = _num(values.get(fid))
            if v is not None:
                st.quote[key] = abs(v)
        if acc is not None:
            st.quote["volume"] = abs(acc)
        st.dirty = True

    # ------------------------------------------------------------- session --
    async def _session(self, tickers: set[str]) -> None:
        from services import kiwoom_rest as kr
        token = await asyncio.to_thread(kr._token)
        if not token:
            print("[ws-feed] no token", flush=True)
            await asyncio.sleep(20)
            return
        codes = sorted(tickers)
        async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
            login = json.loads(await ws.recv())
            if login.get("trnm") == "LOGIN" and str(login.get("return_code")) not in ("0", "None"):
                kr._token_cache = (None, 0.0)   # re-adopt shared / mint-and-publish next try
                raise RuntimeError(f"LOGIN rejected: {login.get('return_msg')}")
            await ws.send(json.dumps({
                "trnm": "REG", "grp_no": "1", "refresh": "1",
                "data": [{"item": codes, "type": ["0D", "0B"]}],
            }))
            print(f"[ws-feed] subscribed 0D+0B for {codes}", flush=True)
            self.state = {tk: _TickerState() for tk in codes}
            while _market_open_now():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    raw = None
                if raw:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        msg = {}
                    trnm = msg.get("trnm")
                    if trnm == "PING":
                        await ws.send(raw)
                    elif trnm == "REAL":
                        for el in msg.get("data", []) or []:
                            tk = str(el.get("item") or "").lstrip("A")
                            if tk not in self.state:
                                continue
                            if el.get("type") == "0D":
                                self.on_0d(tk, el.get("values") or {})
                            elif el.get("type") == "0B":
                                self.on_0b(tk, el.get("values") or {})
                for tk in codes:
                    await asyncio.to_thread(self._refresh_quote_rest, tk)
                    await asyncio.to_thread(self._write_hot, tk)
                w = await asyncio.to_thread(self._watched)
                if w != tickers:
                    print(f"[ws-feed] watch set changed {codes} -> {sorted(w)}", flush=True)
                    return

    async def run(self) -> None:
        print(f"[ws-feed] starting (multi-ticker) — {WS_URL}", flush=True)
        backoff = 1.0
        while True:
            if not _market_open_now():
                await asyncio.sleep(60)
                continue
            w = await asyncio.to_thread(self._watched)
            if not w:
                await asyncio.sleep(3)          # nobody watching -> stay off the socket
                continue
            try:
                await self._session(w)
                backoff = 1.0
            except (ConnectionClosed, OSError, asyncio.TimeoutError, RuntimeError) as e:
                print(f"[ws-feed] session ended ({str(e)[:90]}); retry in {backoff:.0f}s", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            except Exception as e:  # noqa: BLE001 — never kill the host thread
                print(f"[ws-feed] error ({str(e)[:90]}); retry in {backoff:.0f}s", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


def run_forever() -> None:
    """Thread entry point (rt_snapshot_collector) — own event loop, never returns."""
    asyncio.run(HotFeed().run())


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    try:
        from _db import load_env
        load_env()
    except Exception as _e:
        print(f"[ws-feed] env load warning: {_e!r}")
    try:
        run_forever()
    except KeyboardInterrupt:
        print("\n[ws-feed] interrupted.")
