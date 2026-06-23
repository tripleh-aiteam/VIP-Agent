"""backfill_flows.py — daily 외국인/기관 net buying (수급) → raw_investor_flows.

This is the Korean day-trader's core signal (the YouTube method): WHO — foreign vs
institution "smart money" — is accumulating or distributing a stock. The KRX investor
endpoints are currently blocked (whole data.krx.co.kr investor path returns empty), so
we read Naver Finance's foreign/institution page, which is reachable and historical and
needs no credentials.

    https://finance.naver.com/item/frgn.naver?code=005930&page=N   (~20 trading days/page)

Idempotent upsert on (ticker, date). Re-running refreshes the recent window and resumes.
When Kiwoom creds land, an intraday/금투/program source can be added the same way — the
feature layer (flow_features.py) reads raw_investor_flows regardless of source.

Usage:
    python ml/data/backfill_flows.py                  # STARTER_KR, ~24 pages (~2yr)
    python ml/data/backfill_flows.py --pages 40       # deeper history
    python ml/data/backfill_flows.py --pages 3        # quick daily refresh
    python ml/data/backfill_flows.py --tickers 005930 000660
"""
from __future__ import annotations

import argparse
import io
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/ on path
from _db import get_conn, upsert            # noqa: E402
from universe import get_universe           # noqa: E402

COLS = ["ticker", "date", "inst_net", "foreign_net", "foreign_hold_pct",
        "close", "volume", "source"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
URL = "https://finance.naver.com/item/frgn.naver?code={code}&page={page}"


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS raw_investor_flows (
                ticker TEXT NOT NULL, date DATE NOT NULL,
                inst_net BIGINT, foreign_net BIGINT, foreign_hold_pct NUMERIC,
                close NUMERIC, volume BIGINT, source TEXT DEFAULT 'naver',
                ingested_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (ticker, date))""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_flows_date "
                    "ON raw_investor_flows (date)")
    conn.commit()


def _to_int(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    try:
        return int(float(str(v).replace(",", "")))
    except (ValueError, TypeError):
        return None


def _to_float(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_page(html: str, code: str) -> list[tuple]:
    """Naver frgn table -> rows. Columns by position (multiindex header):
    0 날짜 | 1 종가 | 2 전일비 | 3 등락률 | 4 거래량 | 5 기관순매매 | 6 외국인순매매 |
    7 외국인보유주수 | 8 외국인보유율."""
    try:
        tabs = pd.read_html(io.StringIO(html))
    except ValueError:
        return []
    target = None
    for t in tabs:
        if t.shape[1] >= 9:
            target = t
            break
    if target is None:
        return []
    rows = []
    for _, r in target.iterrows():
        vals = list(r.values)
        d = str(vals[0]).strip()
        if not d or d in ("날짜", "nan") or "." not in d:
            continue                                  # skip header/spacer rows
        try:
            date = pd.to_datetime(d, format="%Y.%m.%d").date()
        except (ValueError, TypeError):
            continue
        rows.append((
            code, date,
            _to_int(vals[5]),                          # 기관 순매매량
            _to_int(vals[6]),                          # 외국인 순매매량
            _to_float(vals[8]),                        # 외국인 보유율 %
            _to_float(vals[1]),                        # 종가
            _to_int(vals[4]),                          # 거래량
            "naver",
        ))
    return rows


def _fetch(code: str, pages: int, pause: float) -> list[tuple]:
    out, seen = [], set()
    for p in range(1, pages + 1):
        try:
            req = urllib.request.Request(URL.format(code=code, page=p), headers=UA)
            html = urllib.request.urlopen(req, timeout=12).read().decode("euc-kr", "replace")
        except Exception as e:
            print(f"  {code} page {p} fetch err: {str(e)[:60]}")
            break
        rows = _parse_page(html, code)
        fresh = [r for r in rows if r[1] not in seen]
        if not fresh:                                  # empty page = end of history
            break
        for r in fresh:
            seen.add(r[1])
        out.extend(fresh)
        time.sleep(pause)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=24, help="Naver pages back (~20 days each)")
    ap.add_argument("--tickers", nargs="+")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pause", type=float, default=0.25)
    args = ap.parse_args()

    if args.tickers:
        uni = [(t, "") for t in args.tickers]
    else:
        uni = get_universe("all" if args.all else "starter")
    print(f"[flows] {len(uni)} tickers x up to {args.pages} pages -> raw_investor_flows")

    conn = get_conn()
    _ensure_table(conn)
    ok = empty = fail = total = 0
    try:
        for i, (code, name) in enumerate(uni, 1):
            try:
                rows = _fetch(code, args.pages, args.pause)
                if not rows:
                    empty += 1
                    continue
                n = upsert(conn, "raw_investor_flows", COLS, rows,
                           conflict_cols=["ticker", "date"])
                total += n
                ok += 1
            except Exception as e:
                fail += 1
                print(f"[flows] {code} {name} FAILED: {str(e)[:80]}")
            if i % 10 == 0:
                print(f"[flows] {i}/{len(uni)} ok={ok} empty={empty} fail={fail} rows={total}")
    finally:
        conn.close()
    print(f"[flows] DONE: ok={ok} empty={empty} fail={fail} total_rows={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
