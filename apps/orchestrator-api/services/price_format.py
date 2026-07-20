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


# KRX regular session: 09:00–15:30 KST, Mon–Fri (includes the 15:20–15:30 closing
# auction). Outside this the live feeds serve an after-hours / last-close price, so
# a current-price answer must SAY the market is closed (boss 2026-07-20) — otherwise
# an after-hours Naver quote reads like a live intraday price.
_KRX_OPEN_MIN = 9 * 60          # 09:00
_KRX_CLOSE_MIN = 15 * 60 + 30   # 15:30


def _kr_market_open(dt: datetime) -> bool:
    if dt.weekday() >= 5:       # Sat/Sun
        return False
    m = dt.hour * 60 + dt.minute
    return _KRX_OPEN_MIN <= m <= _KRX_CLOSE_MIN


def _market_note(dt: datetime, english: bool) -> str:
    """One-line KR market-status note for current-price answers."""
    if _kr_market_open(dt):
        return ("🟢 Market open — this is a live intraday price."
                if english else "🟢 장중 — 실시간 장중 시세입니다.")
    if dt.weekday() >= 5:
        head_en, head_ko = "Market closed (weekend)", "휴장 (주말)"
    else:
        m = dt.hour * 60 + dt.minute
        if m < _KRX_OPEN_MIN:
            head_en, head_ko = "Market closed (before the open)", "장 시작 전"
        else:
            head_en, head_ko = "Market closed (after the close)", "장 마감 후"
    if english:
        return (f"🔴 {head_en} — KRX regular hours are 09:00–15:30 KST, Mon–Fri. "
                f"This is the latest Naver price (after-hours / last close), "
                f"not a live intraday quote.")
    return (f"🔴 {head_ko} — 정규장은 평일 09:00–15:30 (한국시간)입니다. "
            f"실시간 장중가가 아니라 네이버 시간외/종가 시세입니다.")


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


def _chg(change_pct, *, english: bool = True, basis: bool = False) -> str:
    """' (▲ +4.04%)' / ' (▼ -3.49%)' / ''. `basis=True` clarifies the % is vs the
    PREVIOUS day's close (not vs the opening), e.g. ' (▲ +1.38% vs prev close)' —
    used when the opening price is shown alongside, to avoid 'open→current' confusion."""
    if change_pct is None:
        return ""
    try:
        c = float(change_pct)
    except (TypeError, ValueError):
        return ""
    arrow = "▲" if c >= 0 else "▼"
    sign = "+" if c >= 0 else ""
    tag = (" vs prev close" if english else " 전일대비") if basis else ""
    return f" ({arrow} {sign}{c}%{tag})"


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


_FIELD_EN = {"open": "open", "high": "high", "low": "low", "price": "current", "volume": "volume"}
_FIELD_KO = {"open": "시가", "high": "고가", "low": "저가", "price": "현재가", "volume": "거래량"}


def _value_segment(q, fields, english: bool) -> str:
    """Per-stock value string. Only-price → '₩X (▲ +Y%)'. Multi-field (e.g. open +
    current + volume) → 'open ₩A, current ₩B (▲ +Y%), volume N shares' so multi-part
    questions ('current price AND volume') are answered together."""
    market = q.get("market")
    simple = (not fields) or list(fields) == ["price"]
    if not simple:
        labels = _FIELD_EN if english else _FIELD_KO
        parts = []
        for f in fields:
            v = q.get(f)
            if v is None:
                continue
            if f == "volume":
                seg = f"{labels[f]} {_int_str(v)}" + (" shares" if english else "주")
            else:
                seg = f"{labels.get(f, f)} {_price_str(v, market, english)}"
                if f == "price":
                    # 'vs prev close' so the % isn't misread as open→current change.
                    seg += _chg(q.get("change_pct"), english=english, basis=True)
            parts.append(seg)
        if parts:
            return ", ".join(parts)
    # only-price (or requested fields all missing)
    return f"{_price_str(q.get('price'), market, english)}{_chg(q.get('change_pct'))}"


def _price_table(q, english: bool, ts: str, src: str) -> str:
    """Detailed single-stock current-price TABLE — IDENTICAL structure in EN + KO so the
    same question gets the same rich answer regardless of language."""
    market = q.get("market")
    price, chg = q.get("price"), q.get("change_pct")
    prev = None
    if price is not None and chg is not None:
        try:
            prev = round(float(price) / (1 + float(chg) / 100))
        except Exception:
            prev = None
    name = q.get("name") or ("the stock" if english else "해당 종목")
    code = q.get("code")
    head = f"{name}" + (f" ({code})" if code else "")
    def P(v):
        return _price_str(v, market, english) if v is not None else "-"
    chg_cell = (_chg(chg, english=english, basis=True).strip(" ()") or "-")
    vol = q.get("volume")
    vol_cell = (f"{_int_str(vol)} " + ("shares" if english else "주")) if vol is not None else "-"
    if english:
        title = f"**{head}** is currently {P(price)}{_chg(chg, english=True)}"
        rows = [("Metric", "Value"), ("Current Price", P(price)), ("Change", chg_cell),
                ("Open", P(q.get("open"))), ("High", P(q.get("high"))), ("Low", P(q.get("low"))),
                ("Prev Close", P(prev)), ("Volume", vol_cell)]
        tail = f"*Data as of {ts}" + (f" · {src}" if src else "") + "*"
    else:
        title = f"**{head}** 현재가 {P(price)}{_chg(chg, english=False)}"
        rows = [("항목", "값"), ("현재가", P(price)), ("등락", chg_cell),
                ("시가", P(q.get("open"))), ("고가", P(q.get("high"))), ("저가", P(q.get("low"))),
                ("전일종가", P(prev)), ("거래량", vol_cell)]
        tail = f"*기준 {ts} (한국시간)" + (f" · 출처 {src}" if src else "") + "*"
    md = "| " + " | ".join(rows[0]) + " |\n| --- | --- |\n"
    for label, val in rows[1:]:
        md += f"| {label} | {val} |\n"
    return f"{title}\n\n{md}\n{tail}"


def format_current(quotes, *, lang, used_watchlist: bool = False,
                   as_of: datetime | None = None, fields=None) -> str:
    """Current-price answer — a DETAILED TABLE, identical in EN + KO (one stock = a
    Metric/Value table; several = a Stock/Price/Change table). Source ALWAYS shown."""
    english = _is_en(lang)
    dt = as_of or now_kst()
    avail = [q for q in quotes if q.get("price") is not None]
    src = _src_line(avail or quotes, english)
    ts = _ts_en(dt) if english else _ts_ko(dt)

    # SINGLE stock → full detailed table
    if len(quotes) == 1:
        q = quotes[0]
        if q.get("price") is None:
            name = q.get("name") or ("the stock" if english else "해당 종목")
            return (f"Couldn't fetch a quote for {name}. Please check the ticker or data availability."
                    if english else
                    f"{name} 시세를 조회하지 못했습니다. 종목 코드나 데이터 제공 상태를 확인해 주세요.")
        table = _price_table(q, english, ts, src)
        if str(q.get("market") or "KR").upper() == "KR":
            table += "\n\n" + _market_note(dt, english)
        return table

    # MULTIPLE stocks (watchlist) → compact Stock/Price/Change table
    hdr = ("Stock", "Price", "Change") if english else ("종목", "현재가", "등락")
    md = "| " + " | ".join(hdr) + " |\n| --- | --- | --- |\n"
    for q in quotes:
        name = q.get("name") or ("the stock" if english else "해당 종목")
        if q.get("price") is None:
            md += f"| {name} | - | - |\n"
        else:
            md += (f"| {name} | {_price_str(q['price'], q.get('market'), english)} "
                   f"| {_chg(q.get('change_pct'), english=english).strip(' ()') or '-'} |\n")
    if english:
        title = "Current watchlist prices" if used_watchlist else "Current prices"
        tail = f"*Data as of {ts}" + (f" · {src}" if src else "") + "*"
    else:
        title = "관심 종목 현재가" if used_watchlist else "요청하신 종목 현재가"
        tail = f"*기준 {ts} (한국시간)" + (f" · 출처 {src}" if src else "") + "*"
    out = f"**{title}**\n\n{md}\n{tail}"
    if any(str(q.get("market") or "KR").upper() == "KR" for q in quotes):
        out += "\n\n" + _market_note(dt, english)
    return out


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


_WD_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_WD_KO = ["월", "화", "수", "목", "금", "토", "일"]


def _fmt_day(date_str: str, english: bool) -> str:
    """'2026-06-18' -> 'Jun 18 (Thu)' / '06-18 (목)'."""
    try:
        y, m, d = (int(x) for x in date_str.split("-"))
        from datetime import date as _date
        wd = _date(y, m, d).weekday()
        if english:
            mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                   "Oct", "Nov", "Dec"][m - 1]
            return f"{mon} {d} ({_WD_EN[wd]})"
        return f"{m:02d}-{d:02d} ({_WD_KO[wd]})"
    except Exception:
        return date_str


def format_history(stocks, *, lang) -> str:
    """Deterministic multi-day OHLCV table(s) — one per stock — for past-date and
    range questions ('18th/17th/16th/15th of June', 'last 4 days'). `stocks`: list of
    {"name","code"(opt),"rows"} where rows are newest-first
    {date, open, high, low, close, change_pct, volume}. No LLM → VIP and the AI Advisor
    (which relays this) read IDENTICALLY."""
    english = _is_en(lang)
    stocks = [s for s in stocks if s and s.get("rows")]
    if not stocks:
        return ("No daily price data found for the requested dates."
                if english else "요청하신 날짜의 일별 시세 데이터를 찾지 못했습니다.")
    blocks = []
    for s in stocks:
        name = s.get("name") or s.get("code") or ("the stock" if english else "해당 종목")
        code = s.get("code")
        head = f"{name} ({code})" if code else f"{name}"
        if english:
            lines = [f"{head} — daily prices:",
                     "| Date | Open | High | Low | Close | Change | Volume |",
                     "|---|---|---|---|---|---|---|"]
        else:
            lines = [f"{head} 일별 시세:",
                     "| 날짜 | 시가 | 고가 | 저가 | 종가 | 등락 | 거래량 |",
                     "|---|---|---|---|---|---|---|"]
        for r in s["rows"]:
            chg = _chg(r.get("change_pct")).strip(" ()")  # '▲ +1.8%' or ''
            lines.append(
                f"| {_fmt_day(str(r.get('date') or ''), english)} "
                f"| {_price_str(r.get('open'), 'KR', english)} "
                f"| {_price_str(r.get('high'), 'KR', english)} "
                f"| {_price_str(r.get('low'), 'KR', english)} "
                f"| {_price_str(r.get('close'), 'KR', english)} "
                f"| {chg or '-'} "
                f"| {_int_str(r.get('volume'))} |")
        blocks.append("\n".join(lines))
    src = ("Source: Naver Finance (daily OHLCV)" if english
           else "출처: 네이버 금융 (일봉 OHLCV)")
    return "\n\n".join(blocks) + "\n" + src


__all__ = ["format_current", "format_past", "format_short_selling",
           "format_history", "now_kst"]
