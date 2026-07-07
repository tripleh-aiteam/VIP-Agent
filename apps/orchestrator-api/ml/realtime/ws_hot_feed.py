"""ws_hot_feed.py — PC-side Kiwoom WebSocket TICK feed for the Live Order-Book Monitor.

Kiwoom PUSHES every book change (0D) and every fill (0B) over one WebSocket — no
polling. This feed keeps the `kiwoom_hot` relay row ~0.5s fresh for whichever ticker
the monitor is watching (`hot_watch`), cutting the monitor's data age from ~2-4s
(REST relay) to ~1s, HTS-grade. Runs on the PC because Kiwoom's 지정단말기 IP
allowlist rejects most Render instances (8050) — see orderbook_memory hot-relay notes.

Coexistence: the REST hot relay (rt_snapshot_collector._hot_relay_loop) checks the
row's age and yields while this feed keeps it <2s fresh; if the socket drops, the
REST relay takes over automatically within a couple of seconds. Zero-risk fallback.

Token: reuses services.kiwoom_rest._token() — the SHARED pool token. Never mints
its own except through that path (which publishes to the shared row). No token war.

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
_WRITE_MIN_INTERVAL = 0.4      # max ~2.5 hot-row writes/sec
_WATCH_STALE_SEC = 60          # nobody viewed the monitor for this long -> idle
_QUOTE_REFRESH_SEC = 15        # slow REST pass for open/high/low/vwap baseline


def _market_open_now() -> bool:
    n = datetime.now(_KST)
    if n.weekday() >= 5:
        return False
    mins = n.hour * 60 + n.minute
    return (9 * 60 - 5) <= mins <= (15 * 60 + 35)


class HotFeed:
    """One watched ticker at a time; reconnects when the watch changes."""

    def __init__(self) -> None:
        self._conn = None
        self.ticker: str | None = None
        self.levels: list[dict] = []          # last 0D book (10+10)
        self.tot_bid: float | None = None
        self.tot_ask: float | None = None
        self.trades: deque = deque(maxlen=30)  # newest FIRST (matches REST payload)
        self.quote: dict = {}
        self._last_write = 0.0
        self._last_quote_refresh = 0.0
        self._dirty = False
        self.ticks = 0

    # ------------------------------------------------------------------ db --
    def _db(self):
        if self._conn is None or getattr(self._conn, "closed", 1):
            from _db import get_conn
            self._conn = get_conn()
        return self._conn

    def _watched(self) -> str | None:
        try:
            conn = self._db()
            with conn.cursor() as cur:
                cur.execute("SELECT ticker, EXTRACT(EPOCH FROM (now()-requested_at)) "
                            "FROM hot_watch WHERE id=1")
                row = cur.fetchone()
            conn.commit()
            if row and row[0] and float(row[1] or 9e9) <= _WATCH_STALE_SEC:
                return str(row[0])
        except Exception:
            try:
                self._conn and self._conn.rollback()
            except Exception:
                self._conn = None
        return None

    def _write_hot(self) -> None:
        now = time.time()
        if not self._dirty or now - self._last_write < _WRITE_MIN_INTERVAL:
            return
        payload = {"levels": self.levels, "imbalance": None,
                   "tot_bid": self.tot_bid, "tot_ask": self.tot_ask,
                   "trades": list(self.trades), "quote": self.quote}
        try:
            conn = self._db()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO kiwoom_hot (ticker, payload, updated_at) "
                    "VALUES (%s, %s, now()) "
                    "ON CONFLICT (ticker) DO UPDATE SET payload=EXCLUDED.payload, "
                    "updated_at=now()", (self.ticker, json.dumps(payload)))
            conn.commit()
            self._last_write, self._dirty = now, False
        except Exception as e:
            print(f"[ws-feed] hot write failed: {str(e)[:80]}", flush=True)
            try:
                self._conn and self._conn.rollback()
            except Exception:
                pass
            self._conn = None

    def _refresh_quote_rest(self) -> None:
        """Slow REST pass: OHLC baseline + VWAP (acc amount unit is only trustworthy
        from ka10003/ka10001 — the 0B FID-14 unit differs); WS keeps price live."""
        now = time.time()
        if now - self._last_quote_refresh < _QUOTE_REFRESH_SEC or not self.ticker:
            return
        self._last_quote_refresh = now
        try:
            from services import kiwoom_rest as kr
            q = kr.current_price(self.ticker) or {}
            if q.get("price"):
                keep_price = self.quote.get("price") or q["price"]   # WS price is fresher
                self.quote = {**q, "price": keep_price}
            tr = kr.executions(self.ticker, ttl=0.0) or []
            if tr and tr[0].get("acc_amount") and tr[0].get("acc_volume"):
                self.quote["vwap"] = round(tr[0]["acc_amount"] / tr[0]["acc_volume"])
            self._dirty = True
        except Exception:
            pass

    # -------------------------------------------------------------- frames --
    def on_0d(self, values: dict) -> None:
        lv = parse_0d(values)
        if lv:
            self.levels = lv
            # 121/125 = 매도/매수 호가 총잔량 (present on most 0D frames)
            ta, tb = _num(values.get("121")), _num(values.get("125"))
            if ta is not None:
                self.tot_ask = abs(ta)
            if tb is not None:
                self.tot_bid = abs(tb)
            self._dirty = True
            self.ticks += 1

    def on_0b(self, values: dict) -> None:
        """주식체결: 20=체결시간(HHMMSS) 10=현재가 12=등락율 15=체결량(+매수/−매도)
        13=누적거래량 16=시가 17=고가 18=저가 — same FID map as the boss's stock_test."""
        px = _num(values.get("10"))
        vol = _num(values.get("15"))
        if px is None or vol is None:
            return
        t = str(values.get("20") or "").strip()
        hhmmss = f"{t[0:2]}:{t[2:4]}:{t[4:6]}" if len(t) >= 6 else datetime.now(_KST).strftime("%H:%M:%S")
        acc = _num(values.get("13"))
        self.trades.appendleft({
            "time": hhmmss, "price": int(abs(px)),
            "qty": int(abs(vol)), "dir": 1 if vol > 0 else -1,
            "acc_volume": int(acc) if acc is not None else None,
            "acc_amount": None,
        })
        chg = _num(values.get("12"))
        self.quote["price"] = abs(px)
        if chg is not None:
            self.quote["change_pct"] = chg
        for fid, key in (("16", "open"), ("17", "high"), ("18", "low")):
            v = _num(values.get(fid))
            if v is not None:
                self.quote[key] = abs(v)
        if acc is not None:
            self.quote["volume"] = abs(acc)
        self._dirty = True
        self.ticks += 1

    # ------------------------------------------------------------- session --
    async def _session(self, ticker: str) -> None:
        from services import kiwoom_rest as kr
        token = await asyncio.to_thread(kr._token)
        if not token:
            print("[ws-feed] no token", flush=True)
            await asyncio.sleep(20)
            return
        async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
            login = json.loads(await ws.recv())
            if login.get("trnm") == "LOGIN" and str(login.get("return_code")) not in ("0", "None"):
                # revoked/stale token: drop the process cache and re-adopt the SHARED
                # row (or mint-and-publish) via kiwoom_rest — never a private mint.
                kr._token_cache = (None, 0.0)
                raise RuntimeError(f"LOGIN rejected: {login.get('return_msg')}")
            await ws.send(json.dumps({
                "trnm": "REG", "grp_no": "1", "refresh": "1",
                "data": [{"item": [ticker], "type": ["0D", "0B"]}],
            }))
            print(f"[ws-feed] subscribed 0D+0B for {ticker}", flush=True)
            self.ticker = ticker
            self.levels, self.trades, self.quote = [], deque(maxlen=30), {}
            self._last_quote_refresh = 0.0
            while _market_open_now():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
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
                            if str(el.get("item") or "").lstrip("A") != ticker:
                                continue
                            if el.get("type") == "0D":
                                self.on_0d(el.get("values") or {})
                            elif el.get("type") == "0B":
                                self.on_0b(el.get("values") or {})
                await asyncio.to_thread(self._refresh_quote_rest)
                await asyncio.to_thread(self._write_hot)
                # watch changed or expired -> end session (outer loop re-checks)
                w = await asyncio.to_thread(self._watched)
                if w != ticker:
                    print(f"[ws-feed] watch changed {ticker} -> {w}", flush=True)
                    return

    async def run(self) -> None:
        print(f"[ws-feed] starting — {WS_URL}", flush=True)
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
