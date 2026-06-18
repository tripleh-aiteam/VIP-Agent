"""Canonical price-answer formatter.

IMPORTANT: this file is kept BYTE-IDENTICAL in two repos:
  - VIP:   vip-ai-platform/apps/orchestrator-api/services/price_format.py
  - Stock: stock_advisor_agent/backend/services/price_format.py
If you edit one, copy it to the other. It is the single source of truth for how
both the VIP agent and the AI Advisor (Stock) phrase price answers, so the two
surfaces ALWAYS read identically. One format, deterministic (no LLM), used for:
  - current price  -> format_current(...)
  - past close     -> format_past(...)
single & multi stock, English & Korean.

Each quote dict the callers pass:
  {"name": str, "price": float|None, "change_pct": float|None,
   "source": one of {"kiwoom","naver_nxt","naver","yahoo"}, "market": "KR"|"US"}
The caller normalizes its own internal source codes into that small vocabulary
BEFORE calling, so the wording here never drifts between repos.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

_KST = timezone(timedelta(hours=9))

_SRC_EN = {
    "kiwoom": "Kiwoom (real-time)",
    "naver_nxt": "Naver after-hours (NXT)",
    "naver": "Naver (real-time)",
    "yahoo": "Yahoo Finance",
}
_SRC_KO = {
    "kiwoom": "키움증권 실시간 시세",
    "naver_nxt": "NAVER 시간외(NXT) 시세",
    "naver": "NAVER 실시간 시세",
    "yahoo": "Yahoo Finance",
}
_PAST_SRC_EN = "Naver daily history (end-of-day)"
_PAST_SRC_KO = "네이버 일봉 시세 (종가 기준)"


def now_kst() -> datetime:
    return datetime.now(_KST)


def _is_en(lang) -> bool:
    return str(lang or "").lower().startswith("en")


def _price_str(price, market: str, english: bool) -> str:
    """₩1,234,567 for KR, $1,234.56 for US."""
    if price is None:
        return ""
    if str(market or "KR").upper() == "US":
        return f"${float(price):,.2f}"
    won = f"{int(round(float(price))):,}"
    return f"₩{won}" if english else f"{won}원"


def _chg(change_pct) -> str:
    """' (▲ +4.04%)' / ' (▼ -3.49%)' / '' — mirrors VIP's original wording."""
    if change_pct is None:
        return ""
    try:
        c = float(change_pct)
    except (TypeError, ValueError):
        return ""
    arrow = "▲" if c >= 0 else "▼"
    sign = "+" if c >= 0 else ""
    return f" ({arrow} {sign}{c}%)"


def _ts_en(dt: datetime) -> str:
    return f"{dt.year}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:{dt.minute:02d} KST"


def _ts_ko(dt: datetime) -> str:
    return f"{dt.year}년 {dt.month}월 {dt.day}일 {dt.hour:02d}:{dt.minute:02d}"


def _date_en(date: str) -> str:
    return date  # already YYYY-MM-DD


def _date_ko(date: str) -> str:
    try:
        y, m, d = date.split("-")
        return f"{int(y)}년 {int(m)}월 {int(d)}일"
    except Exception:
        return date


def _src_line(quotes, english: bool) -> str:
    table = _SRC_EN if english else _SRC_KO
    labels = []
    for q in quotes:
        lab = table.get(str(q.get("source") or "naver"))
        if lab and lab not in labels:
            labels.append(lab)
    return " / ".join(labels)


def format_current(quotes, *, lang, used_watchlist: bool = False, as_of: datetime | None = None) -> str:
    """Current-price answer. `quotes`: list of quote dicts (see module docstring)."""
    english = _is_en(lang)
    dt = as_of or now_kst()
    avail = [q for q in quotes if q.get("price") is not None]
    src = _src_line(avail or quotes, english)

    if english:
        ts = _ts_en(dt)
        if len(quotes) == 1:
            q = quotes[0]
            name = q.get("name") or "the stock"
            if q.get("price") is None:
                return f"Couldn't fetch a quote for {name}. Please check the ticker or data availability."
            price = _price_str(q["price"], q.get("market"), True)
            tail = f" Source: {src}." if src else ""
            return f"{name} is currently {price}{_chg(q.get('change_pct'))}, as of {ts}.{tail}"
        head = "Current watchlist prices" if used_watchlist else "Current prices"
        lines = [f"{head} (as of {ts}):"]
        for q in quotes:
            name = q.get("name") or "the stock"
            if q.get("price") is None:
                lines.append(f"- {name}: quote unavailable")
            else:
                lines.append(f"- {name}: {_price_str(q['price'], q.get('market'), True)}{_chg(q.get('change_pct'))}")
        if src:
            lines.append(f"Source: {src}")
        return "\n".join(lines)

    # Korean
    ts = _ts_ko(dt)
    if len(quotes) == 1:
        q = quotes[0]
        name = q.get("name") or "해당 종목"
        if q.get("price") is None:
            return f"{name} 시세를 조회하지 못했습니다. 종목 코드나 데이터 제공 상태를 확인해 주세요."
        price = _price_str(q["price"], q.get("market"), False)
        chg = _chg(q.get("change_pct"))
        chg_txt = f", 전일 대비 {chg.strip(' ()')}" if chg else ""
        tail = f" 출처는 {src}입니다." if src else ""
        return f"{name} 현재가는 {price}{chg_txt}입니다. 기준 시각은 {ts} (한국시간)입니다.{tail}"
    head = "관심 종목 현재가입니다" if used_watchlist else "요청하신 종목 현재가입니다"
    lines = [f"{head} (기준 {ts} 한국시간):"]
    for q in quotes:
        name = q.get("name") or "해당 종목"
        if q.get("price") is None:
            lines.append(f"- {name}: 시세 조회 실패")
        else:
            lines.append(f"- {name}: {_price_str(q['price'], q.get('market'), False)}{_chg(q.get('change_pct'))}")
    if src:
        lines.append(f"출처: {src}")
    return "\n".join(lines)


def format_past(items, *, date: str, lang, intraday_note: bool = False) -> str:
    """Past daily-close answer. `items`: list of {"name","price","change_pct","market"}.
    `date`: matched trading-day YYYY-MM-DD. `intraday_note`: True if the user asked
    for an intraday time we can't serve (we then add one concise line)."""
    english = _is_en(lang)
    avail = [it for it in items if it.get("price") is not None]

    if english:
        if len(items) == 1:
            it = items[0]
            name = it.get("name") or "the stock"
            if it.get("price") is None:
                return f"No daily-close data found for {name} on {_date_en(date)}."
            price = _price_str(it["price"], it.get("market"), True)
            line = f"{name} closed at {price}{_chg(it.get('change_pct'))} on {_date_en(date)}. Source: {_PAST_SRC_EN}."
            if intraday_note:
                line += " Intraday (e.g. 13:45) isn't available — showing the daily close."
            return line
        lines = [f"Prices on {_date_en(date)} (daily close):"]
        for it in items:
            name = it.get("name") or "the stock"
            if it.get("price") is None:
                lines.append(f"- {name}: not available")
            else:
                lines.append(f"- {name}: {_price_str(it['price'], it.get('market'), True)}{_chg(it.get('change_pct'))}")
        lines.append(f"Source: {_PAST_SRC_EN}")
        if intraday_note:
            lines.append("Intraday (e.g. 13:45) isn't available — showing the daily close.")
        return "\n".join(lines)

    # Korean
    if len(items) == 1:
        it = items[0]
        name = it.get("name") or "해당 종목"
        if it.get("price") is None:
            return f"{_date_ko(date)} {name}의 일봉 종가 데이터를 찾지 못했습니다."
        price = _price_str(it["price"], it.get("market"), False)
        line = f"{name}는 {_date_ko(date)}에 {price}{_chg(it.get('change_pct'))}에 마감했습니다. 출처는 {_PAST_SRC_KO}입니다."
        if intraday_note:
            line += " 장중 시각(예: 13:45)은 제공되지 않아 종가를 표시합니다."
        return line
    lines = [f"{_date_ko(date)} 종가입니다:"]
    for it in items:
        name = it.get("name") or "해당 종목"
        if it.get("price") is None:
            lines.append(f"- {name}: 데이터 없음")
        else:
            lines.append(f"- {name}: {_price_str(it['price'], it.get('market'), False)}{_chg(it.get('change_pct'))}")
    lines.append(f"출처: {_PAST_SRC_KO}")
    if intraday_note:
        lines.append("장중 시각(예: 13:45)은 제공되지 않아 종가를 표시합니다.")
    return "\n".join(lines)


_SHORT_SRC_EN = "Kiwoom (ka10014 short-selling)"
_SHORT_SRC_KO = "키움증권 (ka10014 공매도)"


def _int_str(v) -> str:
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return "-"


def _ratio_str(v) -> str:
    if v is None:
        return ""
    try:
        return f"{float(v):g}%"
    except (TypeError, ValueError):
        s = str(v).strip()
        return s if s.endswith("%") else (s + "%" if s else "")


def format_short_selling(items, *, date: str = "", lang) -> str:
    """공매도 / short-selling answer. `items`: list of
    {"name","short_volume","short_ratio","short_value"(opt)}. `date`: data date."""
    english = _is_en(lang)
    avail = [it for it in items if it.get("short_volume") is not None]
    if not avail and len(items) <= 1:
        name = (items[0].get("name") if items else None) or ("the stock" if english else "해당 종목")
        return (f"Short-selling data for {name} isn't available right now."
                if english else f"{name} 공매도 데이터를 조회하지 못했습니다.")

    if english:
        dt = f" ({date})" if date else ""
        if len(items) == 1:
            it = items[0]
            name = it.get("name") or "the stock"
            ratio = _ratio_str(it.get("short_ratio"))
            ratio_txt = f", {ratio} of volume" if ratio else ""
            return (f"{name} short-selling{dt}: {_int_str(it.get('short_volume'))} shares"
                    f"{ratio_txt}. Source: {_SHORT_SRC_EN}.")
        lines = [f"Short-selling{dt}:"]
        for it in items:
            name = it.get("name") or "the stock"
            if it.get("short_volume") is None:
                lines.append(f"- {name}: not available")
            else:
                ratio = _ratio_str(it.get("short_ratio"))
                lines.append(f"- {name}: {_int_str(it.get('short_volume'))} shares"
                             f"{f' ({ratio} of volume)' if ratio else ''}")
        lines.append(f"Source: {_SHORT_SRC_EN}")
        return "\n".join(lines)

    dt = f" ({date} 기준)" if date else ""
    if len(items) == 1:
        it = items[0]
        name = it.get("name") or "해당 종목"
        ratio = _ratio_str(it.get("short_ratio"))
        ratio_txt = f", 거래대비 {ratio}" if ratio else ""
        return (f"{name} 공매도{dt}: 공매도량 {_int_str(it.get('short_volume'))}주"
                f"{ratio_txt}입니다. 출처는 {_SHORT_SRC_KO}입니다.")
    lines = [f"공매도 현황{dt}:"]
    for it in items:
        name = it.get("name") or "해당 종목"
        if it.get("short_volume") is None:
            lines.append(f"- {name}: 데이터 없음")
        else:
            ratio = _ratio_str(it.get("short_ratio"))
            lines.append(f"- {name}: {_int_str(it.get('short_volume'))}주"
                         f"{f' (비중 {ratio})' if ratio else ''}")
    lines.append(f"출처: {_SHORT_SRC_KO}")
    return "\n".join(lines)


__all__ = ["format_current", "format_past", "format_short_selling", "now_kst"]
