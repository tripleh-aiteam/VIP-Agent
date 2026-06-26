"""
realty_supabase — build the daily Real Estate digest from VIP's OWN Supabase.

A colleague (the Real Estate agent) pushes one row per land/auction investigation
into the VIP-side Supabase, table ``public.land_investigations`` (RLS enabled), and
re-runs it every morning. VIP no longer scrapes/derives this data itself — it READS
that table through the orchestrator's normal DB session (DATABASE_URL points at the
same Supabase project, ``xgiwenlkmgxtmdctpwfs``) and renders the daily 일일 현황
digest the boss already recognises, then emails it (KO + EN .docx) like the other
agents' reports.

This mirrors the YouTube-grounded pattern (read someone else's output, don't
regenerate), except the partner ships raw rows rather than a finished file, so we
template the digest from his columns. NO LLM — fast, faithful, numbers identical.

No extra configuration: it uses the existing DB connection. If the table isn't
present / the query fails, it returns status='unavailable' with a reason and the
caller skips the email — it never invents data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from services.logger import log
from services.kst import kst_now, kst_date

# 100억 = 10,000,000,000 KRW — the partner's "추적 중 100억+" threshold.
_BIG_THRESHOLD = 1.0e10
_TABLE = "land_investigations"
# Cap the listings shown so the .docx stays within ~3 pages (the rest live on the
# dashboard). Biggest deals first; a "…외 N건" note is added when truncated.
_MAX_LISTINGS = 15

# Scraper artifacts to drop: the partner's crawler sometimes ingests a trust
# company's nav/footer page as a row with a placeholder title ("물건정보") and a
# boilerplate "services" blurb as the address. These are not real listings and
# show up as identical-looking duplicates, so we filter them out (his own UI does too).
_JUNK_TITLES = {"", "물건정보", "물건 정보", "물건", "(제목 없음)"}
_JUNK_ADDR_MARKERS = ("상품약관", "신탁수수료", "신탁사업 토지신탁")


def _is_junk(p: dict) -> bool:
    t = str(p.get("title") or "").strip()
    if t in _JUNK_TITLES:
        return True
    a = str(p.get("address_text") or "")
    return any(m in a for m in _JUNK_ADDR_MARKERS)


# ---------------------------------------------------------------------------
# Data access — through VIP's existing DB session (no separate credentials)
# ---------------------------------------------------------------------------

def _session(db):
    """Use the caller's session if given, else open a fresh one."""
    if db is not None:
        return db, False
    from db.base import SessionLocal
    return SessionLocal(), True


def _rows_since(db, since_utc: datetime, limit: int = 3000) -> list[dict]:
    """All land_investigations rows first seen on/after `since_utc`, biggest first."""
    sql = text(f"""
        SELECT external_key, title, address_text, current_status, land_area_py,
               appraisal_price, minimum_bid_price, bid_end_date, first_seen_at,
               onbid_match_status, onbid_match, raw_payload
        FROM public.{_TABLE}
        WHERE first_seen_at >= :since
        ORDER BY minimum_bid_price DESC NULLS LAST
        LIMIT :lim
    """)
    return [dict(r._mapping) for r in db.execute(sql, {"since": since_utc, "lim": limit})]


def _count_big(db) -> Optional[int]:
    """The partner's "추적 중 100억+" headline figure. His own app counts his
    OnBid-tracked universe (source_kind='onbid') — that reproduces his number
    (e.g. 188/189) exactly, so we mirror it rather than recomputing a different
    ≥100억 filter (which gives ~721 and would not match what he reports)."""
    try:
        sql = text(f"SELECT count(*) FROM public.{_TABLE} WHERE source_kind = 'onbid'")
        return int(db.execute(sql).scalar() or 0)
    except Exception as e:
        log.warning(f"realty_supabase: count failed: {str(e)[:120]}")
        return None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def _eok(v: Any) -> str:
    """KRW → '377.3억원' (the partner's unit)."""
    f = _num(v)
    if f <= 0:
        return "—"
    return f"{f/1e8:,.1f}억원"


def _eok_en(v: Any) -> str:
    f = _num(v)
    if f <= 0:
        return "—"
    return f"{f/1e8:,.1f} 억 KRW"


def _kst_day(ts: Any) -> Optional[str]:
    """timestamptz (datetime or ISO string) → KST calendar date 'YYYY-MM-DD'."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        dt = ts
    else:
        s = str(ts).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return str(ts)[:10] or None
    if dt.tzinfo is not None:
        dt_utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        dt_utc = dt  # naive → assume UTC (Supabase stores UTC)
    return (dt_utc + timedelta(hours=9)).strftime("%Y-%m-%d")


def _discount_pct(appraisal: Any, bid: Any) -> int:
    a, b = _num(appraisal), _num(bid)
    if a > 0 and 0 < b < a:
        return int(round((1 - b / a) * 100))
    return 0


def _fail_count(raw: Any) -> Optional[int]:
    """Best-effort '유찰 N회' from raw_payload — only if a clear key is present."""
    if not isinstance(raw, dict):
        return None
    for k in ("fail_count", "failCount", "유찰횟수", "유찰", "retry_count", "bid_fail_count"):
        if k in raw:
            try:
                n = int(raw[k])
                if n > 0:
                    return n
            except Exception:
                pass
    return None


def _bid_line_ko(p: dict) -> str:
    pct = _discount_pct(p.get("appraisal_price"), p.get("minimum_bid_price"))
    fc = _fail_count(p.get("raw_payload"))
    fail = f" · 유찰 {fc}회" if fc else ""
    if pct > 0:
        return (f"현재 입찰가 {_eok(p.get('minimum_bid_price'))}   "
                f"감정가 {_eok(p.get('appraisal_price'))} {pct}%↓{fail}")
    return (f"최저입찰가: {_eok(p.get('minimum_bid_price'))} "
            f"감정가 {_eok(p.get('appraisal_price'))}{fail}")


def _bid_line_en(p: dict) -> str:
    pct = _discount_pct(p.get("appraisal_price"), p.get("minimum_bid_price"))
    fc = _fail_count(p.get("raw_payload"))
    fail = f" · {fc} failed bids" if fc else ""
    if pct > 0:
        return (f"Current bid {_eok_en(p.get('minimum_bid_price'))}   "
                f"Appraisal {_eok_en(p.get('appraisal_price'))} {pct}% below{fail}")
    return (f"Min bid: {_eok_en(p.get('minimum_bid_price'))} "
            f"Appraisal {_eok_en(p.get('appraisal_price'))}{fail}")


def _title_badge_ko(p: dict) -> str:
    pct = _discount_pct(p.get("appraisal_price"), p.get("minimum_bid_price"))
    return f"  💰 감정가보다 {pct}% 저렴" if pct > 0 else ""


def _title_badge_en(p: dict) -> str:
    pct = _discount_pct(p.get("appraisal_price"), p.get("minimum_bid_price"))
    return f"  💰 {pct}% below appraisal" if pct > 0 else ""


def _link(p: dict) -> Optional[str]:
    """The listing's source URL — OnBid (www.onbid.co.kr) for source_kind='onbid',
    the trust/agency page for others. Stored in raw_payload.detailUrl/sourceUrl
    (fallback onbid_match). None when the partner didn't record a URL (e.g. manual)."""
    rp = p.get("raw_payload")
    if isinstance(rp, dict):
        u = rp.get("detailUrl") or rp.get("sourceUrl")
        if isinstance(u, str) and u.startswith("http"):
            return u.strip()
    om = p.get("onbid_match")
    if isinstance(om, dict):
        u = om.get("detailUrl") or om.get("url") or om.get("sourceUrl")
        if isinstance(u, str) and u.startswith("http"):
            return u.strip()
    return None


def _title_line(p: dict, ko: bool) -> str:
    """The listing heading — a clickable hyperlink to the source when a URL exists."""
    title = str(p.get("title") or ("(제목 없음)" if ko else "(no title)")).strip()
    title = title.replace("[", "(").replace("]", ")")  # keep markdown link text valid
    badge = _title_badge_ko(p) if ko else _title_badge_en(p)
    url = _link(p)
    # Prefix the clickable title with "LINK:" so the boss knows it opens the listing.
    return f"### [LINK: {title}]({url}){badge}" if url else f"### {title}{badge}"


# ---------------------------------------------------------------------------
# Digest builder
# ---------------------------------------------------------------------------

def _trend_7d(rows: list[dict], today: str) -> list[tuple[str, int]]:
    """[(MM-DD, count), ...] for the last 7 KST days ending today, by first_seen_at."""
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    days = [(today_dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    counts = {d: 0 for d in days}
    for p in rows:
        d = _kst_day(p.get("first_seen_at"))
        if d in counts:
            counts[d] += 1
    return [(d[5:], counts[d]) for d in days]


def build_realty_supabase_report(db=None, trace_id: str = "") -> dict:
    """Daily Real Estate 일일 현황 digest from VIP's Supabase (KO + EN).
    `db` is the orchestrator session; `trace_id` is accepted for call-site
    compatibility with the other report builders."""
    today = kst_date()
    sess, owned = _session(db)
    try:
        # Pull last ~10 days (covers today's new + the 7-day trend) and the 100억+ count.
        since_utc = ((kst_now() - timedelta(days=9))
                     .replace(hour=0, minute=0, second=0, microsecond=0)
                     - timedelta(hours=9)).replace(tzinfo=timezone.utc)
        try:
            rows = _rows_since(sess, since_utc)
        except Exception as e:
            sess.rollback()
            reason = f"land_investigations query failed: {str(e)[:140]}"
            log.warning(f"realty_supabase: {reason}", extra={"action": "realty_sb.query_failed"})
            return _empty(today, reason)

        rows = [p for p in rows if not _is_junk(p)]   # drop scraper nav/footer artifacts
        new_today = [p for p in rows if _kst_day(p.get("first_seen_at")) == today]
        new_today.sort(key=lambda p: _num(p.get("minimum_bid_price")), reverse=True)
        big_count = _count_big(sess)
        trend = _trend_7d(rows, today)
    finally:
        if owned:
            sess.close()

    trend_ko = " · ".join(f"{d}:{n}" for d, n in trend)
    big_txt = f" · 추적 중 100억+ {big_count}건" if big_count is not None else ""
    big_txt_en = f" · Tracking 100억+ {big_count}" if big_count is not None else ""

    header_ko = (f"# 부동산 일일 현황 — {today}\n\n"
                 f"**오늘 신규 {len(new_today)}건{big_txt}**\n\n"
                 f"최근 7일 신규 추이 — {trend_ko}\n")
    header_en = (f"# Real Estate Daily Status — {today}\n\n"
                 f"**Today new {len(new_today)}{big_txt_en}**\n\n"
                 f"7-day new trend — {trend_ko}\n")

    shown = new_today[:_MAX_LISTINGS]
    extra = len(new_today) - len(shown)
    blocks_ko: list[str] = [f"\n## 오늘 신규 {len(new_today)}건"
                            + (f" (상위 {len(shown)}건 표시)" if extra > 0 else "") + "\n"]
    blocks_en: list[str] = [f"\n## Today's new listings ({len(new_today)})"
                            + (f" — top {len(shown)} shown" if extra > 0 else "") + "\n"]
    if not new_today:
        blocks_ko.append("_오늘 신규 등록된 물건이 없습니다._\n")
        blocks_en.append("_No new listings registered today._\n")
    for p in shown:
        addr = str(p.get("address_text") or "").strip()
        bid_end = str(p.get("bid_end_date") or "").strip()
        blocks_ko.append(
            f"{_title_line(p, ko=True)}\n"
            + (f"- 주소: {addr}\n" if addr else "")
            + f"- {_bid_line_ko(p)}\n"
            + (f"- 입찰종료: {bid_end}\n" if bid_end else "")
        )
        blocks_en.append(
            f"{_title_line(p, ko=False)}\n"
            + (f"- Address: {addr}\n" if addr else "")
            + f"- {_bid_line_en(p)}\n"
            + (f"- Bid ends: {bid_end}\n" if bid_end else "")
        )
    if extra > 0:
        blocks_ko.append(f"\n_…외 {extra}건은 대시보드 → Reports에서 확인하세요._\n")
        blocks_en.append(f"\n_…and {extra} more — see Dashboard → Reports._\n")

    detail_ko = header_ko + "\n".join(blocks_ko)
    detail_en = header_en + "\n".join(blocks_en)
    table_ko = _digest_table(new_today, ko=True)

    sum_ko = (f"부동산 일일 현황 {today}: 오늘 신규 {len(new_today)}건"
              + (f", 추적 중 100억+ {big_count}건." if big_count is not None else "."))
    sum_en = (f"Real Estate daily status {today}: {len(new_today)} new today"
              + (f", {big_count} tracked over 100억." if big_count is not None else "."))

    return {
        "agent_type": "realty", "name": "Real Estate Agent Report", "emoji": "🏠",
        "status": "ok",  # query succeeded; 0 new today is still a valid report
        "summary_ko": sum_ko, "summary_en": sum_en,
        "detail_ko": detail_ko, "detail_en": detail_en,
        "table_ko": table_ko, "table_en": table_ko,
        "data": {"new_today": len(new_today),
                 "tracking_big": big_count,
                 "trend": trend,
                 "top": [{
                     "title": str(p.get("title") or "")[:40],
                     "min_bid": _eok(p.get("minimum_bid_price")),
                     "appraisal": _eok(p.get("appraisal_price")),
                     "discount": _discount_pct(p.get("appraisal_price"), p.get("minimum_bid_price")),
                     "bid_end": str(p.get("bid_end_date") or "")[:10],
                     "link": _link(p),
                 } for p in shown[:10]]},
        "source": f"VIP Supabase ({_TABLE})",
    }


def _digest_table(items: list[dict], ko: bool) -> str:
    if not items:
        return ""
    head = ("| 물건 | 주소 | 최저입찰가 | 감정가 | 할인 | 입찰종료 |\n|---|---|---|---|---|---|"
            if ko else
            "| Item | Address | Min bid | Appraisal | Disc. | Bid ends |\n|---|---|---|---|---|---|")
    lines = [head]
    for p in items[:30]:
        pct = _discount_pct(p.get("appraisal_price"), p.get("minimum_bid_price"))
        lines.append(
            f"| {str(p.get('title') or '')[:28]} | {str(p.get('address_text') or '')[:26]} | "
            f"{_eok(p.get('minimum_bid_price'))} | {_eok(p.get('appraisal_price'))} | "
            f"{(str(pct) + '%↓') if pct > 0 else '—'} | {str(p.get('bid_end_date') or '')[:10]} |")
    return "\n".join(lines)


def _empty(today: str, reason: str) -> dict:
    msg_ko = f"# 부동산 일일 현황 — {today}\n\n_데이터를 불러오지 못했습니다: {reason}_\n"
    msg_en = f"# Real Estate Daily Status — {today}\n\n_Data unavailable: {reason}_\n"
    return {
        "agent_type": "realty", "name": "Real Estate Agent Report", "emoji": "🏠",
        "status": "unavailable", "reason": reason,
        "summary_ko": f"부동산 데이터 불러오기 실패: {reason}",
        "summary_en": f"Real estate data unavailable: {reason}",
        "detail_ko": msg_ko, "detail_en": msg_en,
        "table_ko": "", "table_en": "",
        "data": {}, "source": f"VIP Supabase ({_TABLE})",
    }
