# -*- coding: utf-8 -*-
"""reco_track — Step 4 of the boss's 4-step plan (2026-08-27): the chatbot's
recommendations carry a TRACK RECORD, computed from records — never from memory.

Two sources of truth:
  1. data/reco_rank/YYYYMMDD.jsonl — the checklist ranking snapshots the desk
     already writes all day. The first snapshot at/after 09:00 is "that
     morning's picks" (top 3); each pick is scored open-of-that-day → now.
  2. data/reco_advice_log.jsonl — every BUY/SELL verdict the advice lane hands
     the boss is appended here as it happens (this module's log_advice), so the
     record grows by itself from today onward.

The reply lists every pick with its entry, current price and %, plus a win
rate. All prices come from the price APIs — an entry we can't price is shown as
"가격 확인 불가", never guessed.
"""
from __future__ import annotations

import json
import time
from datetime import timedelta, timezone
from pathlib import Path
from typing import Optional

from services.logger import log

KST = timezone(timedelta(hours=9))
_RANK_DIR = Path(__file__).resolve().parent.parent / "data" / "reco_rank"
_ADVICE_LOG = Path(__file__).resolve().parent.parent / "data" / "reco_advice_log.jsonl"
_TOP_N = 3


def log_advice(code: str, name: str, verdict: str, score) -> None:
    """Append one advice verdict (called by checklist_advice at answer time)."""
    try:
        px = None
        try:
            from services.paper_desk import fast_price
            px, _c, _t, _s = fast_price(code)
        except Exception:
            pass
        with _ADVICE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "date": time.strftime("%Y-%m-%d", time.gmtime(time.time() + 9 * 3600)),
                "code": code, "name": name, "verdict": verdict,
                "score": score, "price": px}, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"reco_track log_advice: {str(e)[:80]}")


def _morning_picks() -> list[dict]:
    """[{date, code, name, score}] — top N of each day's first ≥09:00 snapshot."""
    picks = []
    try:
        files = sorted(_RANK_DIR.glob("*.jsonl"))
    except Exception:
        return []
    for fp in files[-14:]:                       # cap the sweep at ~2 weeks
        day = fp.stem                            # YYYYMMDD
        try:
            chosen = None
            for line in fp.read_text(encoding="utf-8").splitlines():
                try:
                    snap = json.loads(line)
                except Exception:
                    continue
                if str(snap.get("t") or "") >= "09:00":
                    chosen = snap
                    break
                chosen = chosen or snap          # fall back to the first line
            if not chosen:
                continue
            date_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
            for row in (chosen.get("rows") or [])[:_TOP_N]:
                picks.append({"date": date_iso, "code": str(row.get("code") or ""),
                              "name": row.get("name") or row.get("code"),
                              "score": row.get("avg")})
        except Exception:
            continue
    return picks


def _entry_price(db, code: str, date_iso: str) -> Optional[float]:
    """That day's OPEN (morning pick ≈ buy at the open) from the history tiers."""
    try:
        from services.price_history import rows
        r, _src = rows(db, code, 40)
        for x in r:
            if str(x.get("date"))[:10] == date_iso:
                return float(x.get("open") or x.get("close") or 0) or None
    except Exception:
        pass
    return None


def _now_price(code: str) -> Optional[float]:
    try:
        from services.paper_desk import fast_price
        px, _c, _t, _s = fast_price(code)
        return float(px) if px else None
    except Exception:
        return None


def is_track_q(transcript: Optional[str]) -> bool:
    import re
    t = transcript or ""
    return bool(re.search(
        r"track\s*record|추천\s*(성적|성과|기록|얼마나\s*맞)|성적\s*(어때|보여)"
        r"|how\s+(accurate|good)\s+(were|are)\s+your|past\s+recommendations?"
        r"|(recommendations?|picks?).{0,16}(doing|perform|right|correct|accurate)"
        r"|네\s*추천.{0,10}(맞|성적|어땠)|추천했던.{0,8}(어떻게|어때|성과)", t, re.IGNORECASE))


def reply(db, lang: str) -> str:
    en = str(lang or "").lower().startswith("en")
    picks = _morning_picks()
    L = []
    wins = losses = 0
    pct_sum = 0.0
    if picks:
        L.append("📊 **추천 트랙 레코드** (매일 아침 체크리스트 Top 3 · 시가 → 현재가)"
                 if not en else
                 "📊 **Recommendation track record** (each morning's checklist Top 3 · open → now)")
        by_day: dict[str, list] = {}
        for p in picks:
            by_day.setdefault(p["date"], []).append(p)
        for date_iso in sorted(by_day, reverse=True):
            L.append(f"\n**{date_iso}**")
            for p in by_day[date_iso]:
                entry = _entry_price(db, p["code"], date_iso)
                now = _now_price(p["code"])
                sc = f" ({p['score']:.0f}점)" if not en and p.get("score") else \
                     (f" (score {p['score']:.0f})" if p.get("score") else "")
                if entry and now:
                    chg = (now / entry - 1) * 100
                    pct_sum += chg
                    if chg >= 0:
                        wins += 1
                    else:
                        losses += 1
                    mark = "🟢" if chg >= 0 else "🔴"
                    L.append(f"· {mark} {p['name']}{sc}: ₩{entry:,.0f} → ₩{now:,.0f} "
                             f"(**{chg:+.1f}%**)")
                else:
                    L.append(f"· ⚪ {p['name']}{sc}: " +
                             ("가격 확인 불가" if not en else "price unavailable"))
        total = wins + losses
        if total:
            wr = wins / total * 100
            L.append("")
            L.append((f"**적중률 {wins}/{total} ({wr:.0f}%) · 평균 {pct_sum / total:+.1f}%** "
                      f"— 모든 숫자는 기록과 시세에서 계산된 값입니다.") if not en else
                     (f"**Win rate {wins}/{total} ({wr:.0f}%) · avg {pct_sum / total:+.1f}%** "
                      f"— every number computed from records and live quotes."))
    else:
        L.append("아직 기록된 추천이 없습니다 — 오늘부터 아침 Top 3와 매수/매도 판단을 자동 기록합니다."
                 if not en else
                 "No recorded picks yet — from today, each morning's Top 3 and every "
                 "buy/sell verdict is logged automatically.")
    # advice verdicts (starts filling from today)
    try:
        lines = _ADVICE_LOG.read_text(encoding="utf-8").strip().splitlines()
    except Exception:
        lines = []
    if lines:
        L.append("")
        L.append("🗣️ **매수/매도 판단 기록** (최근 5건)" if not en
                 else "🗣️ **Buy/sell verdicts given** (last 5)")
        seen = set()
        shown = 0
        for line in reversed(lines):
            if shown >= 5:
                break
            try:
                a = json.loads(line)
            except Exception:
                continue
            k = (a.get("date"), a.get("code"), a.get("verdict"))
            if k in seen:
                continue
            seen.add(k)
            now = _now_price(a.get("code") or "")
            tail = ""
            if a.get("price") and now:
                chg = (now / float(a["price"]) - 1) * 100
                tail = f" → {chg:+.1f}% {'since' if en else '이후'}"
            L.append(f"· {a.get('date')} {a.get('name')}: **{a.get('verdict')}**"
                     f" @ ₩{float(a.get('price') or 0):,.0f}{tail}")
            shown += 1
    return "\n".join(L)
