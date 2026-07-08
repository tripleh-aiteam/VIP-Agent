"""backfill_minute_deep.py — DEEP 5-min history backfill via Kiwoom ka10080 PAGINATION.

The single-call fetch only returns ~900 bars (~12 days), which starved the hourly
model (13 days, one regime → failed validation 2026-07-08). Kiwoom's cont-yn/next-key
response HEADERS allow paging much deeper — probe reached March in 6 pages/stock.
This pulls N pages per ticker (default 14 ≈ ~6 months) for every collected ticker and
upserts into minute_bars_hist (PK ticker+ts, idempotent).

Run on the PC (registered IP):  python ml/backfill_minute_deep.py [pages]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx  # noqa: E402


def fetch_pages(code: str, pages: int, tic: str = "5") -> list[dict]:
    from services import kiwoom_rest as kr
    tok = kr._token()
    if not tok:
        raise RuntimeError("no Kiwoom token")
    base = kr._active_base or "https://api.kiwoom.com"
    hdr = {"authorization": f"Bearer {tok}", "api-id": "ka10080",
           "Content-Type": "application/json;charset=UTF-8", "cont-yn": "N", "next-key": ""}
    body = {"stk_cd": str(code).zfill(6), "tic_scope": tic, "upd_stkpc_tp": "1"}
    out: list[dict] = []
    for _ in range(pages):
        for attempt in range(4):
            r = httpx.post(f"{base}/api/dostk/chart", headers=hdr, json=body, timeout=20)
            d = r.json()
            if d.get("return_code") == 5:            # rate limit → back off
                time.sleep(1.0 + attempt)
                continue
            break
        rows = d.get("stk_min_pole_chart_qry") or []
        if not rows:
            break
        for x in rows:
            tm = str(x.get("cntr_tm") or "")
            if len(tm) < 12:
                continue
            def _i(v):
                try:
                    return abs(int(str(v).replace(",", "").lstrip("+-") or 0)) or None
                except Exception:
                    return None
            c = _i(x.get("cur_prc"))
            if c is None:
                continue
            out.append({"ts": f"{tm[0:4]}-{tm[4:6]}-{tm[6:8]} {tm[8:10]}:{tm[10:12]}",
                        "open": _i(x.get("open_pric")), "high": _i(x.get("high_pric")),
                        "low": _i(x.get("low_pric")), "close": c,
                        "volume": _i(x.get("trde_qty")) or 0})
        if r.headers.get("cont-yn") != "Y" or not r.headers.get("next-key"):
            break
        hdr["cont-yn"], hdr["next-key"] = "Y", r.headers.get("next-key")
        time.sleep(0.25)                             # be gentle on the rate limit
    return out


def main() -> int:
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    from sqlalchemy import text

    from db.base import SessionLocal
    db = SessionLocal()
    tickers = [r[0] for r in db.execute(text(
        "SELECT DISTINCT ticker FROM minute_bars_hist")).fetchall()]
    print(f"deep backfill: {len(tickers)} tickers × up to {pages} pages")
    total = 0
    for i, code in enumerate(sorted(tickers), 1):
        try:
            bars = fetch_pages(code, pages)
        except Exception as e:
            print(f"  {code}: FETCH ERR {str(e)[:60]}")
            continue
        n = 0
        for b in bars:
            db.execute(text(
                "INSERT INTO minute_bars_hist (ticker, ts, open, high, low, close, volume) "
                "VALUES (:t, (:ts)::timestamptz, :o, :h, :l, :c, :v) "
                "ON CONFLICT (ticker, ts) DO NOTHING"),
                {"t": code, "ts": b["ts"] + ":00+09", "o": b.get("open"), "h": b.get("high"),
                 "l": b.get("low"), "c": b["close"], "v": b.get("volume")})
            n += 1
        db.commit()
        total += n
        oldest = min((b["ts"] for b in bars), default="-")
        print(f"  [{i}/{len(tickers)}] {code}: {len(bars)} bars (oldest {oldest[:10]}) upserted")
        time.sleep(0.3)
    print(f"\nDONE — {total:,} bars processed")
    r = db.execute(text("SELECT count(*), min(ts)::date, max(ts)::date FROM minute_bars_hist")).first()
    print(f"minute_bars_hist now: {r[0]:,} rows · {r[1]} → {r[2]}")
    db.close()
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        from _db import load_env
        load_env()
    except Exception:
        pass
    sys.exit(main())
