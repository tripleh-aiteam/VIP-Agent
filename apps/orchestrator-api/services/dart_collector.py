"""dart_collector — official DART disclosures → raw_disclosures (the fastest signal).

DART (전자공시) is where Korean companies file 실적·수주·유상증자·자사주 etc. The
filing hits DART's open API within ~1 minute of publication — well before most news
articles digest it. Reacting to a 공시 early is a genuine, *legal*, public-data edge
for short-term trades, so this is the highest-value free collector.

Needs a free key from https://opendart.fss.or.kr  (env DART_API_KEY). Without it the
collector no-ops gracefully (the rest of the brief still works).

    list endpoint: https://opendart.fss.or.kr/api/list.json
      ?crtfc_key=KEY&corp_code=...&bgn_de=YYYYMMDD&end_de=...&page_count=100

corp_code is DART's own 8-digit id (NOT the 6-digit ticker), mapped via the one-time
corpCode.xml download. We cache that mapping in a module dict on first use.
"""
from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

try:                                  # XXE-safe parser; falls back to stdlib if absent
    from defusedxml import ElementTree as ET
except Exception:                     # pragma: no cover
    from xml.etree import ElementTree as ET   # noqa: S405 (input is trusted DART gov zip)

import httpx
from sqlalchemy import text

from services.prediction_service import NAMES   # our 37-ticker universe (code->name)

DART_KEY = lambda: os.getenv("DART_API_KEY", "").strip()
KST = timezone(timedelta(hours=9))
_CORP_MAP: dict[str, str] = {}    # 6-digit ticker -> 8-digit DART corp_code

# disclosure title -> our coarse type (reuses news_impact taxonomy loosely)
def _dart_type(title: str) -> str:
    from services.news_impact import classify
    return classify(title)[0]


def _load_corp_map() -> dict[str, str]:
    """Download DART's corpCode.xml once and map our universe tickers -> corp_code."""
    global _CORP_MAP
    if _CORP_MAP or not DART_KEY():
        return _CORP_MAP
    try:
        r = httpx.get("https://opendart.fss.or.kr/api/corpCode.xml",
                      params={"crtfc_key": DART_KEY()}, timeout=30)
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        xml = zf.read(zf.namelist()[0])
        want = set(NAMES.keys())
        for el in ET.fromstring(xml).iter("list"):
            stock = (el.findtext("stock_code") or "").strip()
            corp = (el.findtext("corp_code") or "").strip()
            if stock in want and corp:
                _CORP_MAP[stock] = corp
    except Exception as e:
        print(f"[dart] corp map load failed: {str(e)[:80]}")
    return _CORP_MAP


def collect(db, days: int = 1, tickers: Optional[list[str]] = None) -> dict[str, Any]:
    """Pull recent disclosures for the universe -> raw_disclosures (idempotent on rcept_no)."""
    if not DART_KEY():
        return {"ok": False, "reason": "DART_API_KEY not set", "inserted": 0}
    cmap = _load_corp_map()
    if not cmap:
        return {"ok": False, "reason": "corp map empty", "inserted": 0}
    codes = tickers or list(NAMES.keys())
    bgn = (datetime.now(KST) - timedelta(days=days)).strftime("%Y%m%d")
    end = datetime.now(KST).strftime("%Y%m%d")
    inserted = 0
    with httpx.Client(timeout=15) as cli:
        for tk in codes:
            corp = cmap.get(tk)
            if not corp:
                continue
            try:
                resp = cli.get("https://opendart.fss.or.kr/api/list.json", params={
                    "crtfc_key": DART_KEY(), "corp_code": corp,
                    "bgn_de": bgn, "end_de": end, "page_count": 100})
                items = resp.json().get("list", []) or []
            except Exception as e:
                print(f"[dart] {tk} list failed: {str(e)[:60]}")
                continue
            for it in items:
                rcept = it.get("rcept_no")
                title = (it.get("report_nm") or "").strip()
                rdt = it.get("rcept_dt") or end           # YYYYMMDD
                if not rcept:
                    continue
                ts = datetime.strptime(rdt, "%Y%m%d").replace(tzinfo=KST)
                try:
                    db.execute(text(
                        "INSERT INTO raw_disclosures (ts, ticker, rcept_no, type, title, detail) "
                        "VALUES (:ts,:t,:r,:ty,:ti,:d) ON CONFLICT (rcept_no) DO NOTHING"),
                        {"ts": ts, "t": tk, "r": rcept, "ty": _dart_type(title),
                         "ti": title, "d": '{"flr":"%s"}' % (it.get("flr_nm") or "")})
                    inserted += 1
                except Exception:
                    db.rollback()
    db.commit()
    return {"ok": True, "inserted": inserted, "universe": len(codes)}


def recent(db, ticker: Optional[str] = None, limit: int = 8, days: int = 3) -> list[dict]:
    """Recent disclosures for the brief's news panel (with impact tag + minutes-ago)."""
    from services.news_impact import classify
    q = ("SELECT ts, ticker, rcept_no, type, title FROM raw_disclosures "
         "WHERE ts > now() - (:days || ' days')::interval ")
    params: dict[str, Any] = {"days": days}
    if ticker:
        q += "AND ticker=:t "
        params["t"] = ticker
    q += "ORDER BY ts DESC LIMIT :lim"
    params["lim"] = limit
    out = []
    for r in db.execute(text(q), params):
        _, impact, ddir = classify(r.title or "")
        out.append({
            "ts": str(r.ts), "ticker": r.ticker,
            "name": NAMES.get(r.ticker, r.ticker), "type": r.type,
            "title": r.title, "impact": impact, "direction": ddir,
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.rcept_no}",
            "source": "DART",
        })
    return out
