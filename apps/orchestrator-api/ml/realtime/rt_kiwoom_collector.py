"""rt_kiwoom_collector — real-time KR 1-minute bars -> Supabase raw_intraday_bars.

Ported from the user's jarvis rt_kiwoom.py (REST OAuth -> WebSocket 0B real-time
ticks -> aggregate to 1-min OHLCV), but writes to Postgres instead of parquet so the
ML pipeline + breaking monitor can read fresh intraday data.

Needs env: KIWOOM_APPKEY, KIWOOM_SECRET, KIWOOM_MOCK (1=mock, 0=live).
Run during KR market hours (long-running). NOT on Render (keep the live app light):
    python ml/realtime/rt_kiwoom_collector.py

Honest note: this is the "before retail" speed feed. It is a persistent process — start
it on the PC/worker during 09:00-15:30 KST. Gated on real Kiwoom credentials.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/ on path
from _db import get_conn          # noqa: E402
from universe import STARTER_KR   # noqa: E402

KST = timezone(timedelta(hours=9))
FID_TIME, FID_PRICE, FID_VOL = "20", "10", "15"


def _bases(mock: bool):
    if mock:
        return "https://mockapi.kiwoom.com", "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"
    return "https://api.kiwoom.com", "wss://api.kiwoom.com:10000/api/dostk/websocket"


def get_token(rest_base: str, appkey: str, secret: str) -> str:
    import requests
    r = requests.post(rest_base + "/oauth2/token",
                      headers={"content-type": "application/json;charset=UTF-8"},
                      data=json.dumps({"grant_type": "client_credentials",
                                       "appkey": appkey, "secretkey": secret}), timeout=20)
    j = r.json()
    if j.get("return_code") != 0 or not j.get("token"):
        raise RuntimeError(f"Kiwoom token failed: {j.get('return_msg')}")
    return j["token"]


class KiwoomRealtime:
    def __init__(self, symbols, ws_url, token_provider):
        import websocket  # noqa: F401  (ensure dep present)
        self.symbols = symbols
        self.ws_url = ws_url
        self._token_provider = token_provider
        self.token = ""
        self._token_at = 0.0
        self._lock = threading.Lock()
        self._cur: dict[str, dict] = {}
        self.bars_written = 0
        self._conn = get_conn()

    def _flush(self, code: str, bar: dict) -> None:
        # minute is 'YYYYMMDDHHMM' -> ts
        m = bar["minute"]
        ts = datetime(int(m[0:4]), int(m[4:6]), int(m[6:8]), int(m[8:10]), int(m[10:12]), tzinfo=KST)
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO raw_intraday_bars (ticker, ts, open, high, low, close, volume) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ticker, ts) DO UPDATE SET "
                    "high=GREATEST(raw_intraday_bars.high, EXCLUDED.high), "
                    "low=LEAST(raw_intraday_bars.low, EXCLUDED.low), "
                    "close=EXCLUDED.close, volume=EXCLUDED.volume",
                    (code, ts, bar["o"], bar["h"], bar["l"], bar["c"], int(bar["v"])))
            self._conn.commit()
            self.bars_written += 1
        except Exception as e:
            print(f"[rt_kiwoom] flush err {code}: {str(e)[:70]}")
            self._conn.rollback()

    def _on_tick(self, code: str, price: float, vol: float, hhmmss: str) -> None:
        now = datetime.now(KST)
        minute = now.strftime("%Y%m%d") + (hhmmss[:4] if len(hhmmss) >= 4 else now.strftime("%H%M"))
        with self._lock:
            cur = self._cur.get(code)
            if cur and cur["minute"] != minute:
                self._flush(code, cur); cur = None
            if cur is None:
                self._cur[code] = {"minute": minute, "o": price, "h": price,
                                   "l": price, "c": price, "v": vol}
            else:
                cur["h"] = max(cur["h"], price); cur["l"] = min(cur["l"], price)
                cur["c"] = price; cur["v"] += vol

    def _handle_real(self, msg: dict) -> None:
        for d in msg.get("data", []):
            if d.get("type") != "0B":
                continue
            code = (d.get("item") or "").lstrip("A")
            v = d.get("values", {})
            try:
                price = abs(float(v.get(FID_PRICE, "0") or 0))
                vol = abs(float(v.get(FID_VOL, "0") or 0))
                if code and price:
                    self._on_tick(code, price, vol, str(v.get(FID_TIME, "") or ""))
            except Exception:
                continue

    def _refresh_token(self) -> bool:
        if self.token and (time.time() - self._token_at) < 30:
            return True
        try:
            self.token = self._token_provider(); self._token_at = time.time()
            return True
        except Exception as e:
            print(f"[rt_kiwoom] token err: {e}"); self.token = ""; return False

    def run(self) -> None:
        import websocket

        def on_open(ws):
            if not self._refresh_token():
                ws.close(); return
            ws.send(json.dumps({"trnm": "LOGIN", "token": self.token}))

        def on_message(ws, message):
            try:
                m = json.loads(message)
            except Exception:
                return
            t = m.get("trnm")
            if t == "LOGIN":
                if m.get("return_code") == 0:
                    ws.send(json.dumps({"trnm": "REG", "grp_no": "1", "refresh": "1",
                                        "data": [{"item": self.symbols, "type": ["0B"]}]}))
                else:
                    self.token = ""; self._token_at = 0.0; ws.close()
            elif t == "PING":
                ws.send(message)
            elif t == "REAL":
                self._handle_real(m)

        print(f"[rt_kiwoom] subscribing {len(self.symbols)} symbols -> raw_intraday_bars")
        while True:
            try:
                ws = websocket.WebSocketApp(self.ws_url, on_open=on_open, on_message=on_message,
                                            on_error=lambda w, e: print(f"[rt_kiwoom] ws error: {e}"),
                                            on_close=lambda w, c, m: print("[rt_kiwoom] ws closed"))
                ws.run_forever(ping_interval=60, ping_timeout=10, reconnect=5)
            except Exception as e:
                print(f"[rt_kiwoom] run_forever: {e}")
            time.sleep(5)


def main() -> int:
    # Accept either naming convention (KIWOOM_APP_KEY is the orchestrator standard;
    # KIWOOM_APPKEY is the legacy jarvis name) so one set of creds works everywhere.
    appkey = os.getenv("KIWOOM_APP_KEY") or os.getenv("KIWOOM_APPKEY")
    secret = os.getenv("KIWOOM_APP_SECRET") or os.getenv("KIWOOM_SECRET")
    if not appkey or not secret:
        print("ERROR: set KIWOOM_APP_KEY / KIWOOM_APP_SECRET (and KIWOOM_MOCK=0 for live).")
        return 2
    # Live unless explicitly mocked. Honor KIWOOM_API_BASE (kiwoom_rest's switch) too.
    _base = os.getenv("KIWOOM_API_BASE", "")
    if "mockapi" in _base:
        mock = True
    else:
        mock = os.getenv("KIWOOM_MOCK", "0") not in ("0", "false", "False", "")
    rest_base, ws_url = _bases(mock)
    print(f"[rt_kiwoom] env: {'MOCK' if mock else 'LIVE'} ({rest_base})")
    syms = [c for c, _ in STARTER_KR]
    KiwoomRealtime(syms, ws_url, lambda: get_token(rest_base, appkey, secret)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
