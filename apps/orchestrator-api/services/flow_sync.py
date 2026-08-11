"""Investor flows (수급) from the Kiwoom API into korean_investor_flows.

The table was filled by a pykrx job that died on 2026-07-02, and everything downstream
- the checklist's 수급 column, the daily gate's flow check, the 과거 기록 panel - has
been reading five-week-old rows since. The boss asked the obvious question: we hold a
Kiwoom key, why is this empty? (2026-08-11)

ka10059 (종목별 투자자·기관) returns ~100 daily rows per call in MILLIONS of won,
verified against the last pykrx row (기관 -616,213 vs stored -616,213,221,250 for
삼성전자 2026-07-02). Volumes are not provided and stay NULL; source marks the rows.
"""
from __future__ import annotations

import datetime as _dt
import logging

logger = logging.getLogger("flow_sync")

_M = 1_000_000            # ka10059 amounts are millions of won


def _rows_for(code: str) -> list[dict]:
    from services.kiwoom_rest import _request, _to_int
    d = _request("ka10059", {"stk_cd": code,
                             "dt": _dt.date.today().strftime("%Y%m%d"),
                             "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1000"},
                 path="/api/dostk/stkinfo")
    out = []
    for r in (d or {}).get("stk_invsr_orgn") or []:
        dt = str(r.get("dt") or "")
        if len(dt) != 8:
            continue
        f = _to_int(r.get("frgnr_invsr"))
        o = _to_int(r.get("orgn"))
        i = _to_int(r.get("ind_invsr"))
        if f is None and o is None and i is None:
            continue
        out.append({"date": f"{dt[:4]}-{dt[4:6]}-{dt[6:]}",
                    "foreign": (f or 0) * _M, "inst": (o or 0) * _M,
                    "individual": (i or 0) * _M})
    return out


def sync_flows(codes: list[str] | None = None, upto_yesterday: bool = True) -> dict:
    """Fill every missing (ticker, date) after what the table already has. A settled
    day never changes, so existing rows are left untouched; today is skipped while the
    session is still adding to it."""
    from ml._db import get_conn
    conn = get_conn(); cur = conn.cursor()
    if codes is None:
        cur.execute("SELECT DISTINCT ticker FROM minute_bars_hist")
        codes = sorted({r[0] for r in cur.fetchall()})
        from services.daily_pick import DESK
        codes = sorted(set(codes) | set(DESK))
    cur.execute("SELECT code, name FROM krx_stocks")
    names = dict(cur.fetchall())
    today = _dt.date.today().isoformat()
    added = 0; failed = []
    for code in codes:
        try:
            rows = _rows_for(code)
        except Exception as e:
            failed.append(code); logger.warning("ka10059 %s: %s", code, str(e)[:80])
            continue
        if not rows:
            failed.append(code)
            continue
        cur.execute("SELECT date FROM korean_investor_flows WHERE ticker=%s", (code,))
        have = {r[0].isoformat() for r in cur.fetchall()}
        for r in rows:
            if r["date"] in have:
                continue
            if upto_yesterday and r["date"] >= today:
                continue
            cur.execute("""INSERT INTO korean_investor_flows
                           (ticker, name, date, foreign_net_value, inst_net_value,
                            individual_net_value, source)
                           VALUES (%s, %s, %s, %s, %s, %s, 'kiwoom_ka10059')""",
                        (code, names.get(code, code), r["date"], r["foreign"],
                         r["inst"], r["individual"]))
            added += 1
    conn.commit(); conn.close()
    logger.info("flow_sync: +%d rows, %d stocks failed", added, len(failed))
    return {"ok": True, "added": added, "stocks": len(codes), "failed": failed}
