"""
Server-side Kiwoom WebSocket order-book collector (real-time feed for /monitoring).

Subscribes to 0D (주식호가잔량 / order book) over Kiwoom's WebSocket and folds every
tick into the rolling order-book memory (services.orderbook_memory.record), so the
Monitoring page shows a LIVE 10 + REMEMBERED top-30 ladder that updates and
color-changes in real time — no PC required (our Render IP is Kiwoom-whitelisted).

Why WebSocket (not the 30s REST poll): Kiwoom pushes a frame on every book change,
so the remembered book fills to 30 fast and qty changes are visible tick-by-tick.

KRX still publishes only 10 live levels — the deeper rows are genuinely-observed
memory (orderbook_memory marks them stale with an age). This is read-only market
data; it never places orders.

Ported from the user's stock_test/collector/websocket_collector.py, adapted to
write to Supabase (obm.record) and reuse our existing Kiwoom token.

Run standalone (e.g. on a PC):  python -m services.ws_orderbook_collector
In-process: started from main.py lifespan unless WS_ORDERBOOK_COLLECTOR=false.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta

import websockets
from websockets.exceptions import ConnectionClosed

_KST = timezone(timedelta(hours=9))

WS_URL = os.getenv("KIWOOM_WS_URL", "wss://api.kiwoom.com:10000/api/dostk/websocket")
CODES = [c.strip() for c in os.getenv(
    "WS_ORDERBOOK_CODES",
    "005930,000660,035420,009150,402340",  # 삼성전자 · SK하이닉스 · NAVER · 삼성전기 · SK스퀘어
).split(",") if c.strip()]


# --------------------------------------------------------------------------- #
# 0D (주식호가잔량) FID map — Kiwoom realtime order book.
#   ask price 41..50 · bid price 51..60 · ask qty 61..70 · bid qty 71..80
# Prices arrive sign-prefixed (direction vs prev close); the sign is NOT part of
# the price, so take the magnitude.
# --------------------------------------------------------------------------- #
def _num(raw):
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").lstrip("+")
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_0d(values: dict) -> list[dict]:
    """FID-keyed values -> [{side, level, price, qty}] (≤10 per side)."""
    levels: list[dict] = []
    for i in range(1, 11):
        ap, aq = _num(values.get(str(40 + i))), _num(values.get(str(60 + i)))
        if ap:
            levels.append({"side": "ask", "level": i, "price": int(abs(ap)),
                           "qty": int(abs(aq)) if aq is not None else 0})
        bp, bq = _num(values.get(str(50 + i))), _num(values.get(str(70 + i)))
        if bp:
            levels.append({"side": "bid", "level": i, "price": int(abs(bp)),
                           "qty": int(abs(bq)) if bq is not None else 0})
    return levels


def _market_open_now() -> bool:
    """KRX regular session: Mon-Fri 09:00-15:30 KST (+5min buffer each side)."""
    n = datetime.now(_KST)
    if n.weekday() >= 5:
        return False
    mins = n.hour * 60 + n.minute
    return (9 * 60 - 5) <= mins <= (15 * 60 + 35)


async def _get_token(force: bool = False) -> str | None:
    from services import kiwoom_rest
    return await asyncio.to_thread(kiwoom_rest._token, force)


def _get_conn():
    from ml._db import get_conn
    return get_conn()


class WsOrderbookCollector:
    def __init__(self) -> None:
        self.stop = asyncio.Event()
        self._conn = None
        self.msgs = 0
        # diagnostics surfaced via status() / the ws-debug endpoint
        self.state = "init"           # init|idle(closed)|connecting|logged_in|subscribed|recv|error
        self.connected = False
        self.last_error = None
        self.last_write_ts = None
        self.last_raw = None          # last raw 0D frame (to verify FIDs against live data)
        self.last_login = None        # raw LOGIN response
        self.writes = 0
        self.force_token = False      # force a fresh token after a token/LOGIN failure

    def _conn_ok(self):
        if self._conn is None or getattr(self._conn, "closed", 1):
            self._conn = _get_conn()
        return self._conn

    async def _write(self, ticker: str, levels: list[dict]) -> None:
        if not levels:
            return
        from services import orderbook_memory as obm

        def _do():
            conn = self._conn_ok()
            try:
                obm.record(conn, ticker, levels, datetime.now(timezone.utc))
                self.writes += 1
                self.last_write_ts = datetime.now(timezone.utc).isoformat()
            except Exception:
                try:
                    if self._conn:
                        self._conn.rollback()
                except Exception:
                    pass
                # drop the bad connection so the next write reconnects
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
                raise
        await asyncio.to_thread(_do)

    async def _session(self) -> None:
        self.state = "connecting"
        token = await _get_token(self.force_token)   # force-refresh if a prior login failed
        self.force_token = False
        if not token:
            self.last_error = "no Kiwoom token (KIWOOM_APP_KEY/SECRET or whitelisted IP)"
            print("[ws-ob] " + self.last_error)
            await self._sleep(30)
            return
        async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
            self.connected = True
            await ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
            login = json.loads(await ws.recv())
            self.last_login = login
            if login.get("trnm") == "LOGIN" and str(login.get("return_code")) not in ("0", "None"):
                raise RuntimeError(f"LOGIN rejected: {login.get('return_msg')}")
            self.state = "logged_in"
            await ws.send(json.dumps({
                "trnm": "REG", "grp_no": "1", "refresh": "1",
                "data": [{"item": CODES, "type": ["0D"]}],
            }))
            self.state = "subscribed"
            print(f"[ws-ob] subscribed 0D for {CODES}")
            while not self.stop.is_set() and _market_open_now():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                except asyncio.TimeoutError:
                    continue
                await self._handle(ws, raw)

    async def _handle(self, ws, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        trnm = msg.get("trnm")
        if trnm == "PING":
            await ws.send(raw if isinstance(raw, str) else json.dumps(msg))
            return
        if trnm == "REAL":
            self.state = "recv"
            for el in msg.get("data", []) or []:
                if el.get("type") != "0D":
                    continue
                self.last_raw = el            # keep a real 0D frame to verify FIDs
                levels = parse_0d(el.get("values") or {})
                if levels:
                    await self._write(el.get("item"), levels)
                    self.msgs += 1
                    if self.msgs % 50 == 0:
                        print(f"[ws-ob] stored {self.msgs} order-book ticks")

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def run(self) -> None:
        backoff = 1.0
        print(f"[ws-ob] collector starting - {WS_URL} codes={CODES}")
        while not self.stop.is_set():
            if not _market_open_now():
                self.state, self.connected = "idle(closed)", False
                await self._sleep(60)           # idle outside market hours
                continue
            try:
                await self._session()
                backoff = 1.0
            except (ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                if self.stop.is_set():
                    break
                self.connected = False
                self.state, self.last_error = "error", f"{type(e).__name__}: {e}"
                print(f"[ws-ob] disconnected ({e!r}); retry in {backoff:.0f}s")
                await self._sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            except Exception as e:  # noqa: BLE001 — log & retry, never crash the app
                if self.stop.is_set():
                    break
                self.connected = False
                self.state, self.last_error = "error", f"{type(e).__name__}: {e}"
                # stale/invalid token (e.g. 8005) -> mint a fresh one on the next attempt
                if any(k in str(e).lower() for k in ("token", "login", "8005", "유효")):
                    self.force_token = True
                print(f"[ws-ob] error ({e!r}); retry in {backoff:.0f}s")
                await self._sleep(backoff)
                backoff = min(backoff * 2, 30.0)


_singleton: WsOrderbookCollector | None = None


def status() -> dict:
    """Live diagnostics for the ws-debug endpoint."""
    s = _singleton
    out = {
        "enabled": should_run(),
        "has_key": bool(os.getenv("KIWOOM_APP_KEY")),
        "has_secret": bool(os.getenv("KIWOOM_APP_SECRET")),
        "ws_url": WS_URL, "codes": CODES,
        "market_open_now": _market_open_now(),
        "running": s is not None,
    }
    if s is not None:
        # sanitize the LOGIN frame to non-sensitive status fields only (never echo
        # back tokens / session identifiers from the third-party response).
        login = s.last_login if isinstance(s.last_login, dict) else {}
        login_safe = {k: login.get(k) for k in ("trnm", "return_code", "return_msg") if k in login}
        out.update({
            "state": s.state, "connected": s.connected,
            "msgs": s.msgs, "writes": s.writes,
            "last_write_ts": s.last_write_ts,
            "last_error": s.last_error,
            "login": login_safe,
            "last_raw_0d": s.last_raw,   # public market data (order book) — verify FIDs
        })
    return out


def should_run() -> bool:
    """Enabled when Kiwoom creds exist and not explicitly disabled."""
    if os.getenv("WS_ORDERBOOK_COLLECTOR", "").lower() == "false":
        return False
    return bool(os.getenv("KIWOOM_APP_KEY") and os.getenv("KIWOOM_APP_SECRET"))


async def start_in_process() -> None:
    """Entry point for main.py lifespan — runs forever, gated to market hours."""
    global _singleton
    if _singleton is not None:
        return
    _singleton = WsOrderbookCollector()
    await _singleton.run()


def main() -> None:
    # Korean Windows consoles default to cp949 and crash on non-Latin output —
    # force UTF-8 like the user's config.py does.
    import sys as _sys
    for _s in (_sys.stdout, _sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    # Standalone (PC) run: load .env / .env.supabase so KIWOOM keys + the Supabase
    # DATABASE_URL are present (the server loads these at startup; a bare `python -m`
    # does not). Reuses the ML pipeline's loader (.env.supabase overrides .env).
    try:
        from ml._db import load_env
        load_env()
    except Exception as _e:
        print(f"[ws-ob] env load warning: {_e!r}")
    print(f"[ws-ob] standalone start - enabled={should_run()} market_open={_market_open_now()} codes={CODES}")
    try:
        asyncio.run(WsOrderbookCollector().run())
    except KeyboardInterrupt:
        print("\n[ws-ob] interrupted.")


if __name__ == "__main__":
    main()
