# -*- coding: utf-8 -*-
"""global_quotes — Step 2 of the boss's 4-step plan (2026-08-27): the chatbot
answers US stocks and crypto with REAL numbers, never from LLM memory.

Sources, in order:
  1. The data PC (100.96.115.29:8010 — 517 US daily tickers + BTC/ETH/XRP/GOLD),
     with a SHORT timeout so an unreachable data PC never stalls the chat.
  2. US fallback — Yahoo's public chart API (no key, the same source the ADR
     lane and overnight brief already trust).
  3. Crypto fallback — Upbit's public API (no key, KRW prices — the numbers the
     boss actually thinks in).

No LLM anywhere in this module: every figure in the reply came from a quote API.
"""
from __future__ import annotations

import re
import time
from typing import Optional

import httpx

from services.logger import log

DATA_PC = "http://100.96.115.29:8010"
_PC_TIMEOUT = 2.5          # the chat must stay fast even when the data PC is off
_pc_down_until = 0.0       # after a miss, skip the data PC for a while

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# ---- name → symbol -------------------------------------------------------------
_US_ALIASES = {
    "apple": "AAPL", "애플": "AAPL", "tesla": "TSLA", "테슬라": "TSLA",
    "nvidia": "NVDA", "엔비디아": "NVDA", "microsoft": "MSFT", "마이크로소프트": "MSFT",
    "google": "GOOGL", "alphabet": "GOOGL", "구글": "GOOGL", "amazon": "AMZN",
    "아마존": "AMZN", "meta": "META", "facebook": "META", "페이스북": "META",
    "메타": "META", "netflix": "NFLX", "넷플릭스": "NFLX", "intel": "INTC",
    "인텔": "INTC", "palantir": "PLTR", "팔란티어": "PLTR", "coinbase": "COIN",
    "코인베이스": "COIN", "broadcom": "AVGO", "브로드컴": "AVGO", "qualcomm": "QCOM",
    "퀄컴": "QCOM", "micron": "MU", "마이크론": "MU", "boeing": "BA", "보잉": "BA",
    "disney": "DIS", "디즈니": "DIS", "starbucks": "SBUX", "스타벅스": "SBUX",
    "nike": "NKE", "나이키": "NKE", "mcdonalds": "MCD", "맥도날드": "MCD",
    "oracle": "ORCL", "오라클": "ORCL", "uber": "UBER", "우버": "UBER",
    "airbnb": "ABNB", "paypal": "PYPL", "페이팔": "PYPL", "berkshire": "BRK-B",
    "eli lilly": "LLY", "novo nordisk": "NVO", "tsmc": "TSM", "asml": "ASML",
    "walmart": "WMT", "월마트": "WMT", "costco": "COST", "코스트코": "COST",
    "exxon": "XOM", "chevron": "CVX", "pfizer": "PFE", "화이자": "PFE",
    "moderna": "MRNA", "모더나": "MRNA", "jpmorgan": "JPM", "salesforce": "CRM",
}
_US_NAMES = {
    "AAPL": "Apple", "TSLA": "Tesla", "NVDA": "NVIDIA", "MSFT": "Microsoft",
    "GOOGL": "Alphabet (Google)", "GOOG": "Alphabet (Google)", "AMZN": "Amazon",
    "META": "Meta", "NFLX": "Netflix", "INTC": "Intel", "AMD": "AMD",
    "PLTR": "Palantir", "COIN": "Coinbase", "AVGO": "Broadcom", "QCOM": "Qualcomm",
    "MU": "Micron", "BA": "Boeing", "DIS": "Disney", "SBUX": "Starbucks",
    "NKE": "Nike", "MCD": "McDonald's", "JPM": "JPMorgan", "ORCL": "Oracle",
    "CRM": "Salesforce", "UBER": "Uber", "ABNB": "Airbnb", "PYPL": "PayPal",
    "LLY": "Eli Lilly", "NVO": "Novo Nordisk", "ZTS": "Zoetis", "TSM": "TSMC",
    "ASML": "ASML", "MRNA": "Moderna", "PFE": "Pfizer", "XOM": "ExxonMobil",
    "CVX": "Chevron", "WMT": "Walmart", "COST": "Costco", "HD": "Home Depot",
    "GE": "GE", "GM": "GM", "RIVN": "Rivian", "SNOW": "Snowflake",
    "SHOP": "Shopify", "SPOT": "Spotify", "RBLX": "Roblox", "SOFI": "SoFi",
    "HOOD": "Robinhood", "ARM": "Arm", "SMCI": "Supermicro", "MSTR": "MicroStrategy",
    "IONQ": "IonQ", "BRK-B": "Berkshire Hathaway",
}
# bare tickers accepted ONLY from this set — never mistake AI/CEO/ETF for a ticker
_US_TICKERS = frozenset(_US_NAMES)

_CRYPTO_ALIASES = {
    "btc": "BTC", "bitcoin": "BTC", "비트코인": "BTC", "빗코": "BTC",
    "eth": "ETH", "ethereum": "ETH", "이더리움": "ETH", "이더": "ETH",
    "xrp": "XRP", "ripple": "XRP", "리플": "XRP",
    "sol": "SOL", "solana": "SOL", "솔라나": "SOL",
    "doge": "DOGE", "dogecoin": "DOGE", "도지": "DOGE", "도지코인": "DOGE",
    "ada": "ADA", "cardano": "ADA", "에이다": "ADA",
}
_CRYPTO_NAMES = {"BTC": ("Bitcoin", "비트코인"), "ETH": ("Ethereum", "이더리움"),
                 "XRP": ("XRP (Ripple)", "리플"), "SOL": ("Solana", "솔라나"),
                 "DOGE": ("Dogecoin", "도지코인"), "ADA": ("Cardano", "에이다"),
                 "GOLD": ("Gold", "금")}


def resolve(text: str) -> Optional[dict]:
    """Find ONE US stock or crypto asset mentioned in the text. Word-boundary
    matched so KR questions ('삼성전자 얼마야') pass through untouched."""
    if not text:
        return None
    low = text.lower()
    # crypto first — 'eth' inside a longer latin word must not fire
    for alias, sym in _CRYPTO_ALIASES.items():
        if re.search(r"[가-힣]", alias):
            if alias in text:
                en_n, ko_n = _CRYPTO_NAMES[sym]
                return {"kind": "crypto", "sym": sym, "name": en_n, "name_ko": ko_n}
        elif re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", low):
            en_n, ko_n = _CRYPTO_NAMES[sym]
            return {"kind": "crypto", "sym": sym, "name": en_n, "name_ko": ko_n}
    # gold needs a price-ish context in KO ('금' alone is too common a syllable)
    if re.search(r"\bgold( price| chart)?\b|금값|금 시세|골드 시세", low):
        return {"kind": "crypto", "sym": "GOLD", "name": "Gold", "name_ko": "금"}
    for alias, sym in _US_ALIASES.items():
        if re.search(r"[가-힣]", alias):
            if alias in text:
                return {"kind": "us", "sym": sym,
                        "name": _US_NAMES.get(sym, sym), "name_ko": alias}
        elif re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", low):
            return {"kind": "us", "sym": sym,
                    "name": _US_NAMES.get(sym, sym), "name_ko": None}
    # bare UPPERCASE ticker in the ORIGINAL text ('AAPL price?')
    for tok in re.findall(r"\b[A-Z]{2,5}(?:-[A-Z])?\b", text):
        if tok in _US_TICKERS:
            return {"kind": "us", "sym": tok,
                    "name": _US_NAMES.get(tok, tok), "name_ko": None}
    return None


# ---- data plumbing -------------------------------------------------------------
def _pc_get(path: str) -> Optional[dict | list]:
    global _pc_down_until
    if time.time() < _pc_down_until:
        return None
    try:
        r = httpx.get(DATA_PC + path, timeout=_PC_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    _pc_down_until = time.time() + 300      # try again in 5 min, not every message
    return None


def _yahoo_daily(sym: str, rng: str = "3mo") -> list[dict]:
    """Newest-first [{date, open, high, low, close, volume}] from Yahoo."""
    try:
        j = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                      params={"range": rng, "interval": "1d"},
                      headers=_UA, timeout=10).json()
        res = j["chart"]["result"][0]
        ts = res.get("timestamp") or []
        q = res["indicators"]["quote"][0]
        rows = []
        for i, t in enumerate(ts):
            c = q["close"][i]
            if c is None:
                continue
            rows.append({"date": time.strftime("%Y-%m-%d", time.gmtime(t - 5 * 3600)),
                         "open": q["open"][i], "high": q["high"][i],
                         "low": q["low"][i], "close": c,
                         "volume": q["volume"][i] or 0})
        rows.reverse()
        return rows
    except Exception as e:
        log.warning(f"global_quotes yahoo daily {sym}: {str(e)[:80]}")
        return []


def us_quote(sym: str) -> Optional[dict]:
    """{price, pct, exchange, src} — data PC summary first, Yahoo meta second."""
    pc = _pc_get(f"/us/daily/{sym}/summary?months=1")
    try:
        j = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                      params={"range": "5d", "interval": "1d"},
                      headers=_UA, timeout=10).json()
        m = j["chart"]["result"][0]["meta"]
        px, prev = m.get("regularMarketPrice"), m.get("chartPreviousClose")
        if px is not None:
            return {"price": float(px),
                    "pct": ((float(px) / float(prev)) - 1) * 100 if prev else None,
                    "exchange": m.get("fullExchangeName") or "US",
                    "name": m.get("longName") or m.get("shortName"),
                    "src": "Yahoo Finance"}
    except Exception as e:
        log.warning(f"global_quotes yahoo {sym}: {str(e)[:80]}")
    if isinstance(pc, dict) and pc.get("latest"):      # data PC as the backstop
        la = pc["latest"]
        return {"price": float(la.get("close") or 0), "pct": la.get("change"),
                "exchange": "US", "name": None, "src": "자체 데이터 서버"}
    return None


def us_daily(sym: str, days: int) -> tuple[list[dict], str]:
    months = max(1, min(36, (days // 28) + 1))
    pc = _pc_get(f"/us/daily/{sym}?months={months}")
    if isinstance(pc, dict) and pc.get("data"):
        rows = list(reversed(pc["data"]))[:days]        # newest-first
        return rows, "자체 데이터 서버 (data PC)"
    rng = "1mo" if days <= 22 else "3mo" if days <= 66 else "1y" if days <= 260 else "5y"
    return _yahoo_daily(sym, rng)[:days], "Yahoo Finance"


def crypto_quote(sym: str) -> Optional[dict]:
    """KRW live price via Upbit (GOLD via Yahoo futures, USD)."""
    if sym == "GOLD":
        try:
            j = httpx.get("https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF",
                          params={"range": "5d", "interval": "1d"},
                          headers=_UA, timeout=10).json()
            m = j["chart"]["result"][0]["meta"]
            px, prev = m.get("regularMarketPrice"), m.get("chartPreviousClose")
            if px is None:
                return None
            return {"price": float(px), "currency": "USD",
                    "pct": ((float(px) / float(prev)) - 1) * 100 if prev else None,
                    "src": "COMEX Gold futures (Yahoo)"}
        except Exception:
            return None
    try:
        j = httpx.get("https://api.upbit.com/v1/ticker",
                      params={"markets": f"KRW-{sym}"}, timeout=8).json()
        x = j[0]
        return {"price": float(x["trade_price"]), "currency": "KRW",
                "pct": float(x["signed_change_rate"]) * 100,
                "high": x.get("high_price"), "low": x.get("low_price"),
                "vol24": x.get("acc_trade_price_24h"), "src": "업비트 (Upbit)"}
    except Exception as e:
        log.warning(f"global_quotes upbit {sym}: {str(e)[:80]}")
        return None


def crypto_daily(sym: str, days: int) -> tuple[list[dict], str]:
    pc = _pc_get(f"/crypto/daily/{sym}?months={max(1, min(36, days // 28 + 1))}")
    if isinstance(pc, dict) and pc.get("data"):
        return list(reversed(pc["data"]))[:days], "자체 데이터 서버 (data PC)"
    if sym == "GOLD":
        return _yahoo_daily("GC%3DF", "3mo" if days <= 66 else "1y")[:days], "Yahoo (COMEX)"
    try:
        j = httpx.get("https://api.upbit.com/v1/candles/days",
                      params={"market": f"KRW-{sym}", "count": min(200, days)},
                      timeout=10).json()
        rows = [{"date": x["candle_date_time_kst"][:10],
                 "open": x["opening_price"], "high": x["high_price"],
                 "low": x["low_price"], "close": x["trade_price"],
                 "volume": x["candle_acc_trade_volume"]} for x in j]
        return rows, "업비트 (Upbit)"                     # Upbit is newest-first already
    except Exception as e:
        log.warning(f"global_quotes upbit daily {sym}: {str(e)[:80]}")
        return [], ""


# ---- the lane ------------------------------------------------------------------
_DATA_CUE = re.compile(
    r"\bprice\b|얼마|시세|현재가|\bquote\b|\bchart\b|차트|\bhistory\b|추이"
    r"|\bmin\b|\bmax\b|최고|최저|\bhigh\b|\blow\b|\bvolume\b|거래량|올랐|떨어|내렸"
    r"|\bdrop(?:ped|s)?\b|\brose\b|\bfell\b|\bup\b|\bdown\b|어때|전망"
    r"|\bdays?\b|\bweeks?\b|\bmonths?\b|일간|주간|개월|캔들|해\s*줘|보여줘|알려줘"
    r"|\bbuy\b|\bsell\b|살까|팔까|사도|매수|매도|\bnow\b|지금|\bworth\b", re.IGNORECASE)
_TRADE_CUE = re.compile(r"\b(buy|sell|invest)\b|살까|팔까|사도|매수|매도|사줘|팔아",
                        re.IGNORECASE)
_PERIOD = re.compile(r"(\d+)\s*(day|days|일|d\b)|(\d+)\s*(week|weeks|주)"
                     r"|(\d+)\s*(month|months|개월|달)", re.IGNORECASE)
_RANGE_CUE = re.compile(r"min|max|최고|최저|high|low|history|추이|days?|week|month"
                        r"|일간|주간|개월|달|last|recent|최근|지난", re.IGNORECASE)


def _fmt_usd(v: float) -> str:
    return f"${v:,.2f}"


def _fmt_krw(v: float) -> str:
    return f"₩{v:,.0f}"


def _days_asked(text: str) -> Optional[int]:
    m = _PERIOD.search(text or "")
    if not m:
        return None
    if m.group(1):
        return max(2, min(365, int(m.group(1))))
    if m.group(3):
        return max(2, min(365, int(m.group(3)) * 7))
    if m.group(5):
        return max(2, min(365, int(m.group(5)) * 30))
    return None


def reply(transcript: str, lang: str, history: list | None = None) -> Optional[dict]:
    """Full lane: returns {intent, reply} or None when this isn't a global-asset
    question. KR stocks never reach here (resolve() only knows US/crypto names)."""
    if not transcript:
        return None
    ent = resolve(transcript)
    if not ent:
        # follow-up inheritance: 'and the volume?' after a Tesla answer
        if _DATA_CUE.search(transcript) and len(transcript) <= 60:
            for h in reversed(history or []):
                ent = resolve(str(h.get("content") or h.get("text") or ""))
                if ent:
                    break
        if not ent:
            return None
    if not _DATA_CUE.search(transcript) and len(transcript.split()) > 3:
        return None                      # 'I watched a Disney movie' must not fire
    en = str(lang or "").lower().startswith("en")
    ko_name = ent.get("name_ko") or ent["name"]
    days = _days_asked(transcript)
    want_range = bool(days) or bool(_RANGE_CUE.search(transcript))
    trade_note = ""
    if _TRADE_CUE.search(transcript):
        trade_note = ("\n\n⚠️ 매매는 한국 주식 데스크 전용입니다 — 미국 주식/코인은 시세와 "
                      "데이터만 도와드릴 수 있어요." if not en else
                      "\n\n⚠️ Trading here is Korean stocks only — for US stocks and "
                      "crypto I can give quotes and data, not orders.")

    if ent["kind"] == "crypto":
        if want_range:
            rows, src = crypto_daily(ent["sym"], days or 30)
            if not rows:
                return {"intent": "global_history", "reply":
                        ("⚠️ 지금 시세 서버에 연결할 수 없습니다 — 잠시 후 다시 물어봐 주세요."
                         if not en else "⚠️ The quote servers are unreachable right now — please try again shortly.")}
            unit = _fmt_usd if ent["sym"] == "GOLD" else _fmt_krw
            hi = max(r["high"] for r in rows if r.get("high") is not None)
            lo = min(r["low"] for r in rows if r.get("low") is not None)
            newest, oldest = rows[0], rows[-1]
            n = len(rows)
            head = (f"🪙 **{ent['name']} ({ent['sym']})** — last {n} days ({src})" if en
                    else f"🪙 **{ko_name} ({ent['sym']})** — 최근 {n}일 ({src})")
            L = [head,
                 (f"· Close: {unit(oldest['close'])} → **{unit(newest['close'])}**" if en
                  else f"· 종가: {unit(oldest['close'])} → **{unit(newest['close'])}**"),
                 (f"· High **{unit(hi)}** · Low **{unit(lo)}**" if en
                  else f"· 최고 **{unit(hi)}** · 최저 **{unit(lo)}**")]
            for r in rows[:5]:
                L.append(f"  - {r['date']}: {unit(r['close'])}")
            if n > 5:
                L.append("  - …")
            return {"intent": "global_history", "reply": "\n".join(L) + trade_note}
        q = crypto_quote(ent["sym"])
        if not q:
            return {"intent": "global_price", "reply":
                    ("⚠️ 지금 시세 서버에 연결할 수 없습니다 — 잠시 후 다시 물어봐 주세요." if not en
                     else "⚠️ The quote servers are unreachable right now — please try again shortly.")}
        unit = _fmt_usd if q.get("currency") == "USD" else _fmt_krw
        pct = f" ({q['pct']:+.2f}%)" if q.get("pct") is not None else ""
        L = [(f"🪙 **{ent['name']} ({ent['sym']})** — **{unit(q['price'])}**{pct}" if en
              else f"🪙 **{ko_name} ({ent['sym']})** — **{unit(q['price'])}**{pct}")]
        if q.get("high") and q.get("low"):
            L.append(f"· {'Today' if en else '오늘'}: {unit(q['low'])} ~ {unit(q['high'])}")
        L.append(f"· {q['src']}" + (" · live" if en else " · 실시간"))
        return {"intent": "global_price", "reply": "\n".join(L) + trade_note}

    # ---- US stock ----
    if want_range:
        rows, src = us_daily(ent["sym"], days or 30)
        if not rows:
            return {"intent": "global_history", "reply":
                    ("⚠️ 미국 주식 데이터 서버에 연결할 수 없습니다 — 잠시 후 다시 물어봐 주세요." if not en
                     else "⚠️ The US data servers are unreachable right now — please try again shortly.")}
        hi = max(r["high"] for r in rows if r.get("high") is not None)
        lo = min(r["low"] for r in rows if r.get("low") is not None)
        newest, oldest = rows[0], rows[-1]
        n = len(rows)
        head = (f"🇺🇸 **{ent['name']} ({ent['sym']})** — last {n} trading days ({src})" if en
                else f"🇺🇸 **{ent['name']} ({ent['sym']})** — 최근 거래일 {n}일 ({src})")
        L = [head,
             (f"· Close: {_fmt_usd(oldest['close'])} → **{_fmt_usd(newest['close'])}**" if en
              else f"· 종가: {_fmt_usd(oldest['close'])} → **{_fmt_usd(newest['close'])}**"),
             (f"· High **{_fmt_usd(hi)}** · Low **{_fmt_usd(lo)}**" if en
              else f"· 최고 **{_fmt_usd(hi)}** · 최저 **{_fmt_usd(lo)}**")]
        for r in rows[:5]:
            vol = f" · vol {int(r.get('volume') or 0):,}" if r.get("volume") else ""
            L.append(f"  - {r['date']}: {_fmt_usd(r['close'])}{vol}")
        if n > 5:
            L.append("  - …")
        return {"intent": "global_history", "reply": "\n".join(L) + trade_note}
    q = us_quote(ent["sym"])
    if not q:
        return {"intent": "global_price", "reply":
                ("⚠️ 미국 주식 시세 서버에 연결할 수 없습니다 — 잠시 후 다시 물어봐 주세요." if not en
                 else "⚠️ The US quote servers are unreachable right now — please try again shortly.")}
    pct = f" ({q['pct']:+.2f}%)" if q.get("pct") is not None else ""
    nm = q.get("name") or ent["name"]
    L = [(f"🇺🇸 **{nm} ({ent['sym']})** — **{_fmt_usd(q['price'])}**{pct}" if en
          else f"🇺🇸 **{nm} ({ent['sym']})** — **{_fmt_usd(q['price'])}**{pct}"),
         f"· {q.get('exchange') or 'US'} · {q['src']}" + ("" if en else " 기준 (지연 가능)")]
    return {"intent": "global_price", "reply": "\n".join(L) + trade_note}
