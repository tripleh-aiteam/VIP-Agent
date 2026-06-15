"""
kiwoom_report — daily Kiwoom market-analysis report, generated after the US
market close (~6:30 AM KST). Pulls real daily OHLCV from the Stock Advisor
backend (Kiwoom for KR tickers, Yahoo for US) for a fixed watchlist, builds a
data table, and has the LLM compose a structured bilingual (EN/KO) report:
  1. General Overview
  2. Market Data (real table)
  3. Detailed Analysis
  4. Risks & Watch-items
  5. Opportunities
  6. Recommended Actions (buy/sell table with reasons)

Saved as report_type='kiwoom_report' (period='daily'); also sent to Telegram.
"""

from __future__ import annotations

import html as _html
import os
import re
import unicodedata
from datetime import datetime
from typing import Any

import httpx

from services.logger import log

_BACKEND = (os.environ.get("STOCK_BACKEND_URL")
            or "https://stock-advisor-agent-9qwi.onrender.com").rstrip("/")

# (ticker, KO name, EN name, market, unit, etf-tracking note)
KIWOOM_TICKERS: list[dict[str, Any]] = [
    # Korean stocks FIRST (한국 종목 우선), then US/overseas.
    {"t": "000660", "ko": "SK하이닉스", "en": "SK Hynix", "mkt": "KR", "etf": "KODEX 200"},
    {"t": "005930", "ko": "삼성전자", "en": "Samsung Electronics", "mkt": "KR", "etf": "KODEX 200"},
    {"t": "017670", "ko": "SK텔레콤", "en": "SK Telecom", "mkt": "KR", "etf": "KODEX 200"},
    {"t": "018260", "ko": "삼성SDS", "en": "Samsung SDS", "mkt": "KR", "etf": "KODEX 200"},
    {"t": "035420", "ko": "NAVER", "en": "Naver", "mkt": "KR", "etf": "KODEX 200"},
    {"t": "069500", "ko": "KODEX 200", "en": "Kodex 200 ETF", "mkt": "KR", "etf": "(ETF)"},
    {"t": "AMD", "ko": "AMD", "en": "AMD", "mkt": "US", "etf": "SOXX / SMH"},
    {"t": "MU", "ko": "마이크론", "en": "Micron Technology", "mkt": "US", "etf": "SOXX / SMH"},
    {"t": "SOXX", "ko": "필라델피아 반도체(SOX)", "en": "Philadelphia Semi (SOX→SOXX)", "mkt": "US", "etf": "(ETF)"},
    {"t": "SNDK", "ko": "샌디스크", "en": "SanDisk", "mkt": "US", "etf": "SOXX / SMH"},
    {"t": "AVGO", "ko": "브로드컴", "en": "Broadcom", "mkt": "US", "etf": "SOXX / SMH"},
]


def _won(v: float | None) -> str:
    """Format a value as Korean Won (all instruments shown in KRW)."""
    if v is None:
        return "—"
    return f"{v:,.0f}원"


def _usdkrw_rate() -> float:
    """Current USD/KRW from the live market snapshot (fallback 1,530)."""
    try:
        with httpx.Client(timeout=12) as c:
            r = c.get(f"{_BACKEND}/market/snapshots", params={"limit": 1})
        data = r.json() if r.status_code == 200 else None
        snap = None
        if isinstance(data, list) and data:
            snap = data[0]
        elif isinstance(data, dict):
            snap = data if "prices" in data else (data.get("items") or [{}])[0]
        rate = float((snap or {}).get("prices", {}).get("usdkrw") or 0)
        return rate if rate > 500 else 1530.0
    except Exception:
        return 1530.0


def _fmt_chg(c: float | None) -> str:
    if c is None:
        return "—"
    arrow = "▲" if c > 0 else ("▼" if c < 0 else "—")
    return f"{arrow}{abs(c):.2f}%"


def _kr_live_price(code: str) -> tuple[float, str] | None:
    """Real-time KR current price (mid of best bid/ask) from the live orderbook,
    with the capture time as KST 'HH:MM'. This is the TRUE generate-time price
    during trading hours — the daily-chart candle is intermittently null/stale
    mid-session. None if no fresh quote (older than ~20 min → not 'now')."""
    try:
        with httpx.Client(timeout=12) as c:
            r = c.get(f"{_BACKEND}/intraday/orderbook", params={"ticker": code})
        if r.status_code != 200:
            return None
        items = (r.json() or {}).get("items") or []
        if not items:
            return None
        it = items[0]
        levels = it.get("levels") or []

        def best(side: str):
            ls = [l for l in levels if l.get("side") == side]
            if not ls:
                return None
            l1 = min(ls, key=lambda x: x.get("level", 99))
            return abs(int(l1.get("price") or 0)) or None

        bid, ask = best("buy"), best("sell")
        mid = (bid + ask) / 2 if (bid and ask) else (bid or ask)
        if not mid:
            return None
        # Freshness: only treat as 'now' if captured within ~20 min.
        from datetime import datetime, timezone, timedelta
        from services.kst import kst_now
        cap = it.get("captured_at") or ""
        hhmm = kst_now().strftime("%H:%M")
        try:
            dt = datetime.fromisoformat(str(cap).replace("Z", "+00:00"))
            kst = dt.astimezone(timezone(timedelta(hours=9)))
            age_min = (kst_now() - kst.replace(tzinfo=None)).total_seconds() / 60
            if age_min > 20:
                return None
            hhmm = kst.strftime("%H:%M")
        except Exception:
            pass
        return round(mid), hhmm
    except Exception as e:
        log.warning(f"kr live price {code}: {str(e)[:80]}")
        return None


def _fetch_daily(spec: dict) -> dict:
    """Fetch the latest FULLY-SETTLED daily candle + previous close for one ticker.
    Retries a few times — the backend's Yahoo/Kiwoom fetch is intermittently
    slow/empty, which was leaving blank rows.

    Time-aware price selection (so the number always matches WHEN the report is
    made, and is NEVER yesterday's data mislabelled as today's):
      • KR trading hours (09:00–15:30 KST) and a candle dated today exists →
        use today's RUNNING candle = the CURRENT/LIVE price. price_kind="live".
      • After the close (≥15:30 KST) with today's candle → today's FINALIZED
        close. price_kind="close".
      • Pre-open (before 09:00, e.g. the 6:50 AM run) or when the freshest
        candle is older than today (source lag, common for US) → the latest
        SETTLED prior session, honestly labelled. price_kind="prev_close".
    Every row carries `price_kind` + `data_date` so the table can label it."""
    import time as _t
    from services.kst import kst_date as _kst_date, kst_now
    _now = kst_now()
    today = _kst_date()
    kst_hour = _now.hour
    kst_minute = _now.minute
    # KR regular session closes 15:30; treat ≥15:30 as "after close".
    after_close = (kst_hour > 15) or (kst_hour == 15 and kst_minute >= 30)
    pre_open = kst_hour < 9
    # KR equities trade 09:00–15:30 KST → during that window the live orderbook,
    # not the daily candle, is the real generate-time price.
    kr_trading = (spec.get("mkt") == "KR") and (not pre_open) and (not after_close)
    row = {**spec, "open": None, "close": None, "high": None, "low": None,
           "volume": None, "change_pct": None, "ok": False,
           "price_kind": None, "data_date": None, "data_time": None}
    daily_done = False
    for attempt in range(3):
        try:
            with httpx.Client(timeout=30) as c:
                r = c.get(f"{_BACKEND}/intraday/daily-chart",
                          params={"ticker": spec["t"], "days": 20})
            candles = (r.json() or {}).get("candles") or [] if r.status_code == 200 else []
            valid = [c for c in candles if c.get("close") is not None]
            # Pre-open: today's candle (if any) is meaningless → use prior session.
            if pre_open:
                pre = [c for c in valid if (c.get("date") or "0000-00-00") < today]
                valid = pre or valid
            candles = valid or candles
            if candles:
                last = candles[-1]
                prev_close = candles[-2].get("close") if len(candles) >= 2 else None
                close = last.get("close")
                chg = ((close - prev_close) / prev_close * 100) if (prev_close and close) else None
                d = last.get("date")
                # Weekly stats (≈5 trading days) for the recommendation rationale.
                recent5 = candles[-5:]
                weekly_volume = sum((c.get("volume") or 0) for c in recent5) or None
                wk_base = candles[-6].get("close") if len(candles) >= 6 else (candles[0].get("close") if candles else None)
                weekly_change = ((close - wk_base) / wk_base * 100) if (wk_base and close) else None
                # Honest label based on what the freshest candle actually IS.
                if d == today and not pre_open:
                    price_kind = "close" if after_close else "live"
                else:
                    price_kind = "prev_close"   # freshest available is an earlier session
                row.update({
                    "open": last.get("open"), "close": close, "high": last.get("high"),
                    "low": last.get("low"), "volume": last.get("volume"),
                    "prev_close": prev_close, "change_pct": chg, "date": d,
                    "ma5": last.get("ma5"), "ma20": last.get("ma20"), "ma60": last.get("ma60"),
                    "weekly_volume": weekly_volume, "weekly_change_pct": weekly_change,
                    "weekly_base": wk_base,
                    "price_kind": price_kind, "data_date": d,
                    "data_time": _now.strftime("%H:%M") if price_kind == "live" else None,
                    "ok": True,
                })
                daily_done = True
                break
        except Exception as e:
            log.warning(f"kiwoom fetch {spec['t']} attempt {attempt+1} failed: {e}")
        if attempt < 2:
            _t.sleep(1.5)
    if not daily_done:
        log.warning(f"kiwoom fetch {spec['t']}: no daily data after retries")

    # During KR trading hours, OVERRIDE with the real-time orderbook price so the
    # report reflects the price AT GENERATION TIME (e.g. a 10:40 run = 10:40 price),
    # not a stale/null daily candle. Keep the daily-chart open/prev_close/volume.
    if kr_trading:
        live = _kr_live_price(spec["t"])
        if live:
            price, hhmm = live
            prev_close = row.get("prev_close")
            wk_base = row.get("weekly_base")
            row.update({
                "close": price,
                "open": row.get("open") if row.get("open") is not None else None,
                "change_pct": ((price - prev_close) / prev_close * 100) if prev_close else row.get("change_pct"),
                "weekly_change_pct": ((price - wk_base) / wk_base * 100) if wk_base else row.get("weekly_change_pct"),
                "price_kind": "live", "data_date": today, "data_time": hhmm,
                "ok": True,
            })
    return row


def _price_mode() -> str:
    """Which price columns the report shows, by KST time of generation:
      • 'market'    09:00–15:30 → 시가 + 현재가(실시간)
      • 'afterclose' 15:30–24:00 → 시가 + 시간외(실시간, Naver after-market)
      • 'premarket'  00:00–09:00 (the 6:50 run) → 시가 + 종가(키움) + 종가(Naver)"""
    from services.kst import kst_now
    now = kst_now()
    mins = now.hour * 60 + now.minute
    if 9 * 60 <= mins < 15 * 60 + 30:
        return "market"
    if 15 * 60 + 30 <= mins <= 23 * 60 + 59:
        return "afterclose"
    return "premarket"


def _enrich_kr_rows(rows: list[dict]) -> None:
    """For KR tickers, set the displayed price by the time-of-day MODE, using
    Kiwoom (daily-chart, regular session) + Naver (real-time / after-market):
      market    → close = Naver live price (현재가)
      afterclose → close = Naver after-market price (시간외 실시간)
      premarket  → close = Kiwoom regular close; naver_close = Naver 시간외 close
    Also attaches investor flows. Weekly stats / MAs / volume stay daily-chart."""
    mode = _price_mode()
    for r in rows:
        if r.get("mkt") != "KR":
            continue
        try:
            from services import naver_stock
            e = naver_stock.enrich_kr(r["t"])
        except Exception:
            continue
        kiwoom_close = r.get("close")        # daily-chart regular-session close
        live = e.get("price")                # Naver live (regular) price
        nxt = e.get("nxt_price")             # Naver after-market (시간외)
        prev = r.get("prev_close")
        wk = r.get("weekly_base")
        r["kiwoom_close"] = kiwoom_close
        r["naver_close"] = nxt
        if mode == "market" and live:
            r["close"] = live
            r["price_kind"] = "live"
            if e.get("change_pct") is not None:
                r["change_pct"] = e["change_pct"]
            if e.get("as_of"):
                r["data_time"] = e["as_of"]
        elif mode == "afterclose" and (nxt or live):
            p = nxt or live
            r["close"] = p
            r["price_kind"] = "afterhours"
            r["change_pct"] = ((p - prev) / prev * 100) if prev else r.get("change_pct")
            if e.get("as_of"):
                r["data_time"] = e["as_of"]
        else:  # premarket → Kiwoom regular close (already r["close"]); show both closes
            r["price_kind"] = "close"
        # Weekly change vs the displayed price.
        if r.get("close") and wk:
            r["weekly_change_pct"] = (r["close"] - wk) / wk * 100
        fl = e.get("flow") or {}
        r["foreign_net"] = fl.get("foreign")
        r["organ_net"] = fl.get("organ")
        r["individual_net"] = fl.get("individual")
        r["foreign_hold"] = fl.get("foreign_hold")


def _gather() -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=5) as ex:
        rows = list(ex.map(_fetch_daily, KIWOOM_TICKERS))
    try:
        _enrich_kr_rows(rows)
    except Exception as e:
        log.warning(f"kiwoom KR enrich skipped: {str(e)[:80]}")
    return rows


def _expected_us_session() -> str:
    """The most recent COMPLETED US trading session date (ISO), in ET terms.
    Used to detect when the backend's US data is more than the timezone-expected
    one session behind. June = EDT (UTC-4) → KST(UTC+9) is 13h ahead."""
    from datetime import timedelta
    from services.kst import kst_now
    et = kst_now() - timedelta(hours=13)
    d = et.date()
    if et.hour < 16:          # today's US close hasn't happened yet
        d = d - timedelta(days=1)
    while d.weekday() >= 5:    # skip Sat/Sun
        d = d - timedelta(days=1)
    return d.isoformat()


def _backfill_us(rows: list[dict]) -> int:
    """Replace stale/empty US rows with Google's latest price — best-effort."""
    try:
        from services.price_validate import backfill_stale_us
        return backfill_stale_us(rows, _expected_us_session())
    except Exception as e:
        log.warning(f"kiwoom US backfill skipped: {e}")
        return 0


def _run_cross_check(rows: list[dict]) -> dict:
    """Independent Google (Serper) price cross-check — best-effort. Never raises;
    if Serper is unavailable the rows just carry g_flag='—'/'확인불가'."""
    try:
        from services.price_validate import cross_check_rows
        return cross_check_rows(rows)
    except Exception as e:
        log.warning(f"kiwoom cross-check skipped: {e}")
        for r in rows:
            r.setdefault("g_flag", "—")
        return {"checked": 0, "matched": 0, "flagged": 0, "unverified": 0}


def _basis(r: dict, ko: bool) -> str:
    """Honest label for WHAT the price is: live intraday / final close / prior
    session — so a stale number can never masquerade as today's."""
    kind = r.get("price_kind")
    d = (r.get("data_date") or "")[5:]  # MM-DD
    t = r.get("data_time")
    if kind == "google":
        # Sourced from Google when the backend lagged — but the REPORT stays clean:
        # show it as the plain session close, never expose the provider.
        return (f"종가 {d}" if d else "종가") if ko else (f"Close {d}" if d else "Close")
    if kind == "live":
        return (f"현재가 {t}" if t else "현재가") if ko else (f"Live {t}" if t else "Live")
    if kind == "nxt":
        return "종가(시간외/NXT)" if ko else "NXT close"
    if kind == "close":
        return f"종가 {d}" if ko else f"Close {d}"
    if kind == "prev_close":
        return f"전일 {d}" if ko else f"Prev {d}"
    return "—"


def _net(v) -> str:
    """Investor net-buy quantity with sign (외국인/기관 순매수)."""
    if v is None:
        return "—"
    try:
        n = int(v)
    except Exception:
        return "—"
    return f"+{n:,}" if n > 0 else (f"−{abs(n):,}" if n < 0 else "0")


def _verify_cell(r: dict, ko: bool) -> str:
    """Google cross-check result for this row."""
    flag = r.get("g_flag") or "—"
    if flag == "⚠":
        gp = r.get("g_price")
        gtxt = f"{gp:,.0f}" if (gp and r.get("mkt") == "KR") else (f"{gp:,.2f}" if gp else "?")
        return f"⚠ G:{gtxt}"
    return flag  # ✓ / 확인불가 / —


def _build_table(rows: list[dict], ko: bool) -> str:
    """Price table whose PRICE COLUMNS depend on the time-of-day mode:
      • market    → 시가 + 현재가(실시간)
      • afterclose → 시가 + 시간외(실시간)
      • premarket  → 시가 + 종가(키움) + 종가(시간외)
    plus daily & weekly change% / volume. No duplicate prices, no 기준 column."""
    def vol(v):
        return f"{int(v):,}" if v is not None else "—"
    def naver_c(r):
        return _won(r.get("naver_close")) if (r.get("mkt") == "KR" and r.get("naver_close")) else "—"

    mode = _price_mode()
    if mode == "market":
        price_cols = [("현재가(실시간)" if ko else "Price (live)", lambda r: _won(r.get("close_krw")))]
    elif mode == "afterclose":
        price_cols = [("시간외(실시간)" if ko else "After-mkt (live)", lambda r: _won(r.get("close_krw")))]
    else:  # premarket (the 6:50 run) — show both closes
        price_cols = [("종가(키움)" if ko else "Close (KRX)", lambda r: _won(r.get("close_krw"))),
                      ("종가(시간외)" if ko else "Close (NXT)", naver_c)]

    name_h, open_h = ("종목", "시가") if ko else ("Stock", "Open")
    tail_h = (["일일 등락", "일일 거래량", "주간 등락", "주간 거래량"] if ko
              else ["Daily Chg", "Daily Vol", "Weekly Chg", "Weekly Vol"])
    cols = [name_h, open_h] + [h for h, _ in price_cols] + tail_h
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols)
    lines = [head]
    for r in rows:
        name = r["ko"] if ko else r["en"]
        cells = ([name, _won(r.get("open_krw"))]
                 + [fn(r) for _, fn in price_cols]
                 + [_fmt_chg(r.get("change_pct")), vol(r.get("volume")),
                    _fmt_chg(r.get("weekly_change_pct")), vol(r.get("weekly_volume"))])
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _flows_table(rows: list[dict], ko: bool) -> str:
    """투자자별 순매수 (foreign / institutional / individual) — KR stocks only."""
    kr = [r for r in rows if r.get("mkt") == "KR" and (r.get("foreign_net") is not None
          or r.get("organ_net") is not None)]
    if not kr:
        return ""
    if ko:
        head = ("| 종목 | 외국인 순매수 | 기관 순매수 | 개인 순매수 | 외국인 보유율 |\n"
                "|---|---|---|---|---|")
    else:
        head = ("| Stock | Foreign net | Institution net | Individual net | Foreign hold |\n"
                "|---|---|---|---|---|")
    lines = [head]
    for r in kr:
        name = r["ko"] if ko else r["en"]
        lines.append(f"| {name} | {_net(r.get('foreign_net'))} | {_net(r.get('organ_net'))} | "
                     f"{_net(r.get('individual_net'))} | {r.get('foreign_hold') or '—'} |")
    return "\n".join(lines)


def _facts(rows: list[dict]) -> str:
    """Rich per-ticker facts (KRW prices + intraday range + MA trend structure)
    so the LLM can write a deep, name-by-name Detailed Analysis."""
    out = []
    for r in rows:
        if not r.get("ok"):
            out.append(f"- {r['en']} ({r['ko']}, {r['t']}, {r['mkt']}): NO DATA this session.")
            continue

        def pa(v):  # close vs a moving average, in %
            try:
                return f"{(r['close'] - v) / v * 100:+.1f}%" if (v and r.get('close')) else "n/a"
            except Exception:
                return "n/a"

        rng = "n/a"
        try:
            if r.get("high") and r.get("low") and r.get("open"):
                rng = f"{(r['high'] - r['low']) / r['open'] * 100:.1f}%"
        except Exception:
            pass
        chg = r.get("change_pct")
        chg_s = f"{chg:+.2f}%" if chg is not None else "n/a"

        # Trend structure from the moving-average stack.
        ma5, ma20, ma60, cl = r.get("ma5"), r.get("ma20"), r.get("ma60"), r.get("close")
        trend = "n/a"
        if cl and ma5 and ma20 and ma60:
            if cl > ma5 > ma20 > ma60:
                trend = "full uptrend (close>MA5>MA20>MA60, bullish stack)"
            elif cl < ma5 < ma20 < ma60:
                trend = "full downtrend (close<MA5<MA20<MA60, bearish stack)"
            elif cl > ma60 > ma20 and cl < ma5:
                trend = "pullback within an uptrend (above MA60, below MA5)"
            elif cl < ma60 and cl > ma5 > ma20:
                trend = "early bottoming (reclaiming MA5/MA20, still under MA60)"
            elif cl > ma60:
                trend = "above long-term MA60 (longer-term bullish, short-term mixed)"
            else:
                trend = "below long-term MA60 (longer-term bearish, short-term mixed)"

        vol = f"{int(r['volume']):,}" if r.get("volume") is not None else "n/a"
        wchg = r.get("weekly_change_pct")
        wchg_s = f"{wchg:+.2f}%" if wchg is not None else "n/a"
        wvol = f"{int(r['weekly_volume']):,}" if r.get("weekly_volume") is not None else "n/a"
        # Investor flows (KR only) — for the recommendation analysis.
        flow_s = ""
        if r.get("foreign_net") is not None or r.get("organ_net") is not None:
            flow_s = (f" 수급: 외국인순매수={_net(r.get('foreign_net'))}, "
                      f"기관순매수={_net(r.get('organ_net'))}, "
                      f"개인순매수={_net(r.get('individual_net'))}, "
                      f"외국인보유율={r.get('foreign_hold') or 'n/a'};")
        out.append(
            f"- {r['en']} ({r['ko']}, {r['t']}, {r['mkt']}, ETF:{r['etf']}): "
            f"open={_won(r.get('open_krw'))}, close={_won(r.get('close_krw'))}, "
            f"high={_won(r.get('high_krw'))}, low={_won(r.get('low_krw'))}, "
            f"intraday_range={rng}, change_vs_prev_close={chg_s}, volume={vol}, "
            f"weekly_change={wchg_s}, weekly_volume={wvol};{flow_s} "
            f"close_vs_MA5={pa(ma5)}, close_vs_MA20={pa(ma20)}, close_vs_MA60={pa(ma60)}; "
            f"trend={trend}."
        )
    return "\n".join(out)


def gather_priced_rows() -> tuple[list[dict], str, str, float]:
    """Shared price layer for any report: fetch the 11 tickers, convert all
    prices to KRW, and build the EN/KO Markdown tables. Returns
    (rows, table_en, table_ko, usdkrw_rate)."""
    rows = _gather()
    _backfill_us(rows)          # fix backend US lag before pricing
    rate = _usdkrw_rate()
    for r in rows:
        mult = rate if r["mkt"] == "US" else 1.0
        for k in ("open", "close", "high", "low"):
            r[f"{k}_krw"] = (r[k] * mult) if r.get(k) is not None else None
    _run_cross_check(rows)
    return rows, _build_table(rows, ko=False), _build_table(rows, ko=True), rate


def get_quote(ticker: str, ko: str = "", en: str = "", mkt: str | None = None) -> dict | None:
    """SINGLE SOURCE OF TRUTH for one stock's current price — the same daily-chart
    layer (KST-aware live/close + US Google fallback) the reports use. Chatbots /
    assistants across ALL agents call this so every surface quotes the SAME price.
    Returns a clean quote dict, or None if no price could be fetched."""
    code = (ticker or "").strip()
    if not code:
        return None
    if mkt is None:  # 6-digit numeric → KR (KRX), otherwise treat as US
        mkt = "KR" if (code.isdigit() and len(code) == 6) else "US"
    spec = {"t": code, "ko": ko or code, "en": en or code, "mkt": mkt, "etf": ""}
    row = _fetch_daily(spec)
    if mkt == "US":
        try:
            _backfill_us([row])   # replace stale/empty US with the latest real close
        except Exception:
            pass
    if not row.get("ok") or row.get("close") is None:
        return None
    return {
        "ticker": code, "name_ko": ko or code, "name_en": en or code,
        "market": mkt, "currency": "KRW" if mkt == "KR" else "USD",
        "price": row.get("close"), "open": row.get("open"),
        "high": row.get("high"), "low": row.get("low"),
        "change_pct": row.get("change_pct"), "volume": row.get("volume"),
        "price_kind": row.get("price_kind"), "as_of": row.get("data_date"),
        "data_time": row.get("data_time"),
        "source": "Kiwoom / Stock-Advisor (daily-chart)",
    }


def _intraday_journey(db) -> str:
    """Per-ticker intraday path from the day's hourly price snapshots (the '24
    parts'): hourly close sequence + high/low through the day. '' if none."""
    try:
        from services import hourly_capture
        snaps = hourly_capture.accumulated(db, "kiwoom", hours=26)
    except Exception:
        return ""
    if not snaps:
        return ""
    # accumulated() dedupes by ticker (one entry per ticker = latest); for a real
    # journey we need the time series, so read raw snapshots instead.
    try:
        from datetime import datetime, timedelta
        from db.models import OrchReport
        since = datetime.utcnow() - timedelta(hours=26)
        recs = (db.query(OrchReport).filter(OrchReport.report_type == "kiwoom_snapshot")
                .filter(OrchReport.created_at >= since)
                .order_by(OrchReport.created_at.asc()).limit(30).all())
    except Exception:
        return ""
    series: dict[str, list] = {}
    names: dict[str, str] = {}
    for rec in recs:
        c = rec.content_json or {}
        hour = (c.get("hour") or "")[-9:-4]  # HH:00
        for it in (c.get("items") or []):
            t = it.get("t")
            if t and it.get("close") is not None:
                series.setdefault(t, []).append((hour, it["close"]))
                names[t] = it.get("ko", t)
    if not series:
        return ""
    lines = []
    for t, pts in series.items():
        if len(pts) < 2:
            continue
        closes = [p[1] for p in pts]
        path = " → ".join(f"{h} {c:,.0f}" for h, c in pts[-8:])
        lines.append(f"- {names.get(t, t)}: 고가 {max(closes):,.0f} / 저가 {min(closes):,.0f} | {path}")
    return "\n".join(lines)


def _futures_sentiment() -> str:
    """Market-level futures positioning (외국인 선물 순매수) from the Kiwoom backend
    — a real-time derivatives/direction signal. '' if unavailable."""
    try:
        with httpx.Client(timeout=12) as c:
            r = c.get(f"{_BACKEND}/market/futures-positions")
        if r.status_code != 200:
            return ""
        d = r.json() or {}
        desc = d.get("description") or ""
        return desc.strip()
    except Exception as e:
        log.warning(f"kiwoom futures sentiment: {str(e)[:80]}")
        return ""


def build_kiwoom_report(db, trace_id: str) -> dict:
    """Build the daily Kiwoom report (real data table + LLM narrative, EN+KO)."""
    rows = _gather()
    _backfill_us(rows)          # fix backend US lag before pricing
    # Convert ALL prices to Korean Won (US tickers × USD/KRW; KR as-is).
    rate = _usdkrw_rate()
    for r in rows:
        mult = rate if r["mkt"] == "US" else 1.0
        r["open_krw"] = (r["open"] * mult) if r.get("open") is not None else None
        r["close_krw"] = (r["close"] * mult) if r.get("close") is not None else None
        r["high_krw"] = (r["high"] * mult) if r.get("high") is not None else None
        r["low_krw"] = (r["low"] * mult) if r.get("low") is not None else None
    _run_cross_check(rows)
    ok_rows = [r for r in rows if r.get("ok")]
    from services.kst import kst_date as _kst_date
    kst_date = _kst_date()
    table_en, table_ko = _build_table(rows, ko=False), _build_table(rows, ko=True)
    # Append the investor-flows (수급) table for KR stocks, when available.
    _fl_ko, _fl_en = _flows_table(rows, ko=True), _flows_table(rows, ko=False)
    if _fl_ko:
        table_ko = table_ko + "\n\n**투자자별 순매수 (수급, 단위: 주)**\n" + _fl_ko
        table_en = table_en + "\n\n**Investor net-buy (shares)**\n" + _fl_en

    # One-line summary (deterministic fallback)
    movers = sorted([r for r in ok_rows if r.get("change_pct") is not None],
                    key=lambda r: r["change_pct"])
    sum_en = (f"Kiwoom daily ({len(ok_rows)}/{len(rows)} tickers): "
              + (f"weakest {movers[0]['en']} {_fmt_chg(movers[0]['change_pct'])}, "
                 f"strongest {movers[-1]['en']} {_fmt_chg(movers[-1]['change_pct'])}."
                 if movers else "data limited."))
    sum_ko = (f"키움 일일 ({len(ok_rows)}/{len(rows)} 종목): "
              + (f"최약 {movers[0]['ko']} {_fmt_chg(movers[0]['change_pct'])}, "
                 f"최강 {movers[-1]['ko']} {_fmt_chg(movers[-1]['change_pct'])}."
                 if movers else "데이터 제한."))

    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You are Kiwoom's senior market analyst writing the DAILY report after "
            "the US market close (~6:30 AM KST). Use ONLY the data provided — NEVER "
            "invent numbers. ALL prices are in Korean Won (KRW). Produce the report "
            "in this EXACT section structure:\n"
            "## 1. General Overview\n## 2. Market Data\n## 3. Detailed Analysis\n"
            "## 4. Risks & Watch-items\n## 5. Opportunities\n## 6. Recommended Actions\n\n"
            "Rules:\n"
            "- Section 2: insert the provided data table VERBATIM.\n"
            "- Section 3 (DETAILED ANALYSIS — this is the CORE of the report and MUST "
            "be long and substantive: AT LEAST ~750 words. Structure it as:\n"
            "  (a) a sub-heading '### Memory & KR Semiconductors' with a DEDICATED "
            "paragraph for SK Hynix AND for Samsung Electronics;\n"
            "  (b) '### US Semiconductors' with a dedicated paragraph for AMD, Micron, "
            "Broadcom, SanDisk, and the SOXX (Philadelphia Semi) index;\n"
            "  (c) '### Telecom & IT' with a dedicated paragraph for SK Telecom, "
            "Samsung SDS, and Naver;\n"
            "  (d) '### Broad Market — KODEX 200' on the ETF;\n"
            "  (e) '### Cross-Market Read' tying US semis → KR memory read-through and "
            "the overall risk tone.\n"
            "For EVERY name discuss ALL of: change vs prior close, the intraday range "
            "(high/low — wide=volatile/contested, narrow=quiet), the VOLUME (heavy = "
            "conviction/institutional, light = low participation), and the TREND "
            "STRUCTURE from the MA stack (close vs MA5/MA20/MA60 — above=uptrend, "
            "below=downtrend; explicitly name pullbacks-in-uptrend, bottoming, bullish/"
            "bearish stacks, and golden/dead-cross setups). Quote the REAL KRW prices "
            "and percentages throughout — never be vague, never invent.\n"
            "- Section 6: FIRST a Markdown table | Stock | Action | Reason | where "
            "Action is BUY / SELL / HOLD. CRITICAL — each Reason must be written in a "
            "DIFFERENT STYLE and sentence STRUCTURE; do NOT use the same template/opening "
            "for every row. Vary the angle per stock: lead one with its CATALYST, another "
            "with its 수급(외국인/기관 flow), another with its TECHNICAL setup (MA stack), "
            "another with a RISK/contrarian note, another with its weekly momentum. The "
            "rows must read like they were written individually, not filled from a "
            "template. NEVER reuse phrases like '강한 상승 추세, 불리시 추세 구조' across "
            "rows. Each Reason cites THAT stock's OWN numbers (its change%, weekly%, "
            "volume, 외국인/기관/개인 순매수, MA position). THEN, AFTER the table, a "
            "'### Rationale' subsection: a DETAILED 4-6 sentence paragraph per stock, "
            "each ALSO in its own distinct style, going deeper into the catalyst, the "
            "수급 read, the trend structure, and the risk to the thesis. For KR stocks "
            "weave in the investor flows (heavy foreign buying = conviction → BUY; heavy "
            "foreign selling → caution). Every stock must read DISTINCTLY. When market "
            "futures positioning (선물) is provided, factor it into the overall direction "
            "read. (Options trading values and short-selling/공매도 are not available in "
            "the current data feed.)\n"
            "Output ONLY the finished English Markdown report — no preamble, no "
            "placeholders, no notes about a translation."
        )
        journey = _intraday_journey(db)
        futures = _futures_sentiment()
        user = (f"Date (KST): {kst_date} · USD/KRW used for US tickers: {rate:,.0f}\n\n"
                + (f"파생/선물 동향 (market futures positioning — weave into the overview "
                   f"& direction read): {futures}\n\n" if futures else "")
                + f"DATA TABLE (insert verbatim in Section 2):\n{table_en}\n\n"
                f"DATA + TECHNICALS:\n{_facts(rows)}"
                + (f"\n\nINTRADAY JOURNEY (hourly snapshots through the day — weave the "
                   f"day's price path into Section 3):\n{journey}" if journey else ""))
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": user[:9000]}],
            max_tokens=7000, temperature=0.5, model="groq-llama-3.3-70b", prefer_paid=True) or ""
        bad = (not out.strip()) or out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
        if not bad:
            detail_en = out.replace("===EN===", "").strip()
            # Korean: a DEDICATED translation call (the single-shot "write it twice"
            # approach made the model stub the KO half). Translate the FULL English
            # report and swap in the ready-made Korean data table.
            try:
                ko_sys = (
                    "You are a professional Korean financial translator. Translate the "
                    "ENTIRE English market report below into natural, professional "
                    "Korean (존댓말) for an investor audience. Rules:\n"
                    "- Translate EVERYTHING — every section, heading and paragraph. The "
                    "output must contain NO English prose or English headings. Translate "
                    "the SECTION HEADINGS too (e.g. '## 1. General Overview' → "
                    "'## 1. 시장 개요', 'Detailed Analysis' → '상세 분석', 'Risks' → '리스크', "
                    "'Opportunities' → '기회', 'Recommended Actions' → '추천 액션'). "
                    "Translate BUY/HOLD/SELL → 매수/보유/매도. NEVER abbreviate or stub; "
                    "the Korean must be as long as the English.\n"
                    "- Preserve ALL Markdown structure, heading levels, and tables.\n"
                    "- Keep every number, %, 원 amount, ticker code and MA value "
                    "IDENTICAL — translate only the words.\n"
                    "- Replace the Section 2 data table with this EXACT Korean table:\n"
                    f"{table_ko}\n"
                    "Output ONLY the Korean Markdown report."
                )
                ko_out = chat_completion_sync(
                    system_prompt=ko_sys,
                    messages=[{"role": "user", "content": detail_en[:14000]}],
                    max_tokens=7000, temperature=0.3, model="groq-llama-3.3-70b", prefer_paid=True) or ""
                ko_bad = ((not ko_out.strip())
                          or ko_out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
                          or len(ko_out.strip()) < 400)
                if not ko_bad:
                    detail_ko = ko_out.strip()
            except Exception as e:
                log.warning(f"kiwoom KO translation failed: {e}")
    except Exception as e:
        log.warning(f"kiwoom LLM compose failed: {e}")

    # Fallback: assemble a structured report from the table if the LLM is down.
    if not detail_en:
        detail_en = (f"# Kiwoom Daily Report\n*{kst_date} (after US close)*\n\n"
                     f"## 1. General Overview\n{sum_en}\n\n## 2. Market Data\n{table_en}\n\n"
                     f"## 3. Detailed Analysis\nSee the table above for open/close/change/volume.\n\n"
                     f"## 4. Risks & Watch-items\n- Review the weakest movers above.\n\n"
                     f"## 5. Opportunities\n- Review the strongest/weakest movers above.\n\n"
                     f"## 6. Recommended Actions\n| Stock | Action | Reason |\n|---|---|---|\n"
                     f"| — | HOLD | LLM unavailable — manual review recommended |")
    if not detail_ko:
        detail_ko = detail_en

    return {
        "agent_type": "kiwoom", "name": "Kiwoom Market Analysis", "emoji": "📈",
        "status": "ok" if ok_rows else "partial",
        "summary_en": sum_en, "summary_ko": sum_ko,
        "detail_en": detail_en, "detail_ko": detail_ko,
        "table_en": table_en, "table_ko": table_ko,
        "rows": rows, "source": "Kiwoom / Stock Advisor (live daily OHLCV)",
    }


# ---------------------------------------------------------------------------
# Telegram rendering — send the SAME full report (incl. table) as the dashboard
# ---------------------------------------------------------------------------

def _dw(s: str) -> int:
    """Display width: CJK glyphs occupy ~2 monospace cells."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad(s: str, w: int) -> str:
    return s + " " * max(0, w - _dw(s))


def _inline_tg(s: str) -> str:
    """Escape HTML, then convert inline **bold** / `code` to Telegram tags."""
    s = _html.escape(s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def _table_to_pre(block: list[str]) -> str:
    """Render a Markdown table as an aligned monospace <pre> block (CJK-aware)."""
    rows: list[list[str]] = []
    for ln in block:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if cells and all(set(c) <= set("-: ") for c in cells):
            continue  # separator row |---|---|
        rows.append(cells)
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    widths = [max(_dw(r[c]) for r in rows) for c in range(ncol)]
    out = [" │ ".join(_pad(r[c], widths[c]) for c in range(ncol)) for r in rows]
    return "<pre>" + _html.escape("\n".join(out)) + "</pre>"


def _md_to_tg_blocks(md: str) -> list[str]:
    """Convert report Markdown into a list of atomic Telegram-HTML blocks
    (headings → bold, tables → <pre>, bullets → •). Blocks are never split."""
    lines = md.split("\n")
    blocks: list[str] = []
    buf: list[str] = []

    def flush():
        if buf:
            blocks.append("\n".join(buf).strip())
            buf.clear()

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        is_tbl = (line.lstrip().startswith("|") and i + 1 < n
                  and set(lines[i + 1].replace("|", "").replace(" ", "")) <= set("-:"))
        if is_tbl:
            flush()
            tbl = []
            while i < n and lines[i].lstrip().startswith("|"):
                tbl.append(lines[i]); i += 1
            blocks.append(_table_to_pre(tbl))
            continue
        m = re.match(r"^(#{1,6})\s*(.*)$", line)
        if m:
            flush()
            blocks.append(f"<b>{_inline_tg(m.group(2).strip())}</b>")
            i += 1
            continue
        if re.match(r"^\s*[-*]\s+", line):
            buf.append("• " + _inline_tg(re.sub(r"^\s*[-*]\s+", "", line)))
            i += 1
            continue
        if line.strip() == "":
            flush()
        else:
            buf.append(_inline_tg(line))
        i += 1
    flush()
    return [b for b in blocks if b]


def format_report_telegram(rep: dict, kst: str, lang: str = "ko", limit: int = 3900,
                           title: str = "Kiwoom Daily Report", emoji: str = "📈") -> list[str]:
    """Build the FULL report (same content + table as the dashboard) as a list of
    Telegram-HTML messages, each under the 4096-char limit. Generic across report
    types — pass title/emoji to brand the header."""
    body = rep.get("detail_ko") if lang == "ko" else rep.get("detail_en")
    if not body or (lang == "ko" and (len(body.strip()) < 200
                                      or "same report in korean" in body.lower())):
        body = rep.get("detail_en") or ""
    # Drop a leading "# <title>" H1 (we add our own header).
    body = re.sub(r"^\s*#\s+.*\n", "", body, count=1)

    header = f"{emoji} <b>{_html.escape(title)}</b>\n<i>{_html.escape(kst)}</i>"
    blocks = _md_to_tg_blocks(body)

    chunks: list[str] = []
    cur = header
    for blk in blocks:
        if len(blk) > limit:  # oversized single block — hard-split on newlines
            for part in blk.split("\n"):
                if len(cur) + len(part) + 1 > limit:
                    chunks.append(cur); cur = ""
                cur += ("\n" if cur else "") + part
            continue
        if len(cur) + len(blk) + 2 > limit:
            chunks.append(cur); cur = ""
        cur += ("\n\n" if cur else "") + blk
    if cur.strip():
        chunks.append(cur)

    total = len(chunks)
    if total > 1:
        chunks = [f"{c}\n\n<i>({i + 1}/{total})</i>" for i, c in enumerate(chunks)]
    return chunks


def format_kiwoom_telegram(rep: dict, kst: str, lang: str = "ko", limit: int = 3900) -> list[str]:
    """Back-compat wrapper — Kiwoom-branded Telegram chunks."""
    return format_report_telegram(rep, kst, lang, limit, "Kiwoom Daily Report", "📈")
