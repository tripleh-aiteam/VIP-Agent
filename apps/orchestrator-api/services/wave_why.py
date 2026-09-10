# -*- coding: utf-8 -*-
"""wave_why — the ladder's explanation, in the five blocks he asked for.

Boss 2026-09-10: "the buying, holding, selling reason is not detailed. Take the
idea from semi auto and fix BOTH sides with the new rule. Start like this:
  1. There is no 갭상승 — and explain it
  2. The stock's current position is not higher than 65%, according to 6 months,
     3 months, 1 month and one week — put the formula like you used in semi auto
     and calculate it
  3. Show the current volume
  4. Add 'there is no bad news'
  5. Add the result of the 100 checklist, like top 4 or top 5, and clickable.
Make a better and professional reason for buying, not-buying, holding and
selling."

WHY A SECOND EXPLANATION FILE AND NOT approval_desk._why_buy. That one is the
desk's own, written for the desk's own four gates, and it answers in the order
those gates run. His five blocks are a different order and a different question -
and two of them (the four-window position, the checklist RANK) it does not
compute at all. This builds his five, in his order, from the same underlying
data, and both lanes read it, so 반자동 and 자동 cannot tell him different stories.

Every number here is measured, and every percentage carries the division that
produced it. Nothing is rounded into a verdict without showing its arithmetic:
"120일 중 23일 = 19%" is the whole point of the block.
"""
from __future__ import annotations

from services.logger import log

# his four windows, in trading days
WINDOWS = ((120, "6개월", "6 months"), (60, "3개월", "3 months"),
           (20, "1개월", "1 month"), (5, "1주", "1 week"))
TOP_PCT = 65.0          # above this, the stock is too high to buy (his number)


def _dbtxt(items) -> list:
    """A deal-breaker is not always a string - the checklist engine returns some
    of them as dicts. join() on a dict raises, and the whole checklist line
    vanished with it (010140 lost its ⑤ block this morning while 000660 kept
    its own, which is what a silent except looks like from outside)."""
    out = []
    for x in (items or []):
        if isinstance(x, dict):
            out.append(str(x.get("q") or x.get("q_en") or x.get("ko") or x.get("en")
                           or x.get("detail") or f"#{x.get('no')}")[:60])
        else:
            out.append(str(x)[:60])
    return out


def _pos_blocks(code: str, px: float) -> tuple[list, list, bool]:
    """② WHERE IS THE PRICE, over 6 months / 3 months / 1 month / 1 week.

    One measure, four windows, and the division shown every time: of the last N
    trading days, how many CLOSED below where we stand now. 19% means the stock
    has been cheaper than this on only 19% of those days - low, and a place we
    buy. Above 65% on a window is high ground, and his rule does not buy there."""
    from services.approval_desk import _daily3
    rows = _daily3(str(code), 130) or []
    if not rows or not px:
        return ([f"② 위치 — 일봉 데이터가 없어 계산하지 못했습니다."],
                [f"② position - no daily history to measure against"], True)
    closes = []
    for r in rows:                       # newest first from the query
        c = r.get("c")
        if c is None:
            c = (r.get("h", 0) + r.get("l", 0)) / 2 if r.get("h") else None
        if c:
            closes.append(float(c))
    if not closes:
        closes = [float((r["h"] + r["l"]) / 2) for r in rows if r.get("h") and r.get("l")]
    ko, en, ok = [], [], True
    parts_ko, parts_en = [], []
    for n, kname, ename in WINDOWS:
        w = closes[:n]
        if len(w) < 3:
            continue
        below = sum(1 for c in w if c < px)
        pct = below / len(w) * 100
        ok = ok and pct <= TOP_PCT
        parts_ko.append(f"{kname} {pct:.0f}% ({len(w)}일 중 {below}일)")
        parts_en.append(f"{ename} {pct:.0f}% ({below} of {len(w)} days)")
    if not parts_ko:
        return (["② 위치 — 계산할 만큼의 일봉이 없습니다."],
                ["② position - not enough daily history"], True)
    head_ko = ("② 위치 — 네 구간 모두 " + f"{TOP_PCT:.0f}% 아래입니다. 살 수 있는 자리입니다."
               if ok else f"② 위치 — {TOP_PCT:.0f}%를 넘는 구간이 있습니다. 높은 자리입니다.")
    head_en = (f"② position - below {TOP_PCT:.0f}% on all four windows, which is where we buy"
               if ok else f"② position - one of the windows is above {TOP_PCT:.0f}%: high ground")
    ko.append(head_ko + " · " + " · ".join(parts_ko))
    en.append(head_en + " · " + " · ".join(parts_en))
    ko.append(f"    계산법 — 그 기간의 종가 중 지금 값 ₩{px:,.0f}보다 쌌던 날의 수 ÷ 전체 일수. "
              f"예: 6개월 = {sum(1 for c in closes[:120] if c < px)}일 ÷ "
              f"{len(closes[:120])}일 = {sum(1 for c in closes[:120] if c < px) / max(1, len(closes[:120])) * 100:.0f}%. "
              f"숫자가 낮을수록 싼 자리입니다 (0%면 반년 만의 최저가, 100%면 최고가).")
    en.append(f"    how it is worked out - of that window's closes, how many were CHEAPER than "
              f"today's ₩{px:,.0f}, divided by the number of days. Lower means cheaper ground "
              f"(0% = the lowest in half a year, 100% = the highest).")
    return ko, en, ok


def blocks(code: str, name: str, db=None, bars: list | None = None,
           st: dict | None = None, gap: float | None = None,
           side: str = "BUY", dec: dict | None = None) -> dict:
    """His five blocks for one stock, right now. Returns {ko, en, checklist, ...}."""
    from services import wave_rule as W
    from services import approval_desk as A
    code = str(code or "").zfill(6)
    ko: list = []
    en: list = []
    out: dict = {"code": code, "name": name}
    bars = bars if bars is not None else W.minute_bars(code)
    px = float(bars[-1]["close"]) if bars else 0.0
    if gap is None and bars:
        ref = W.prev_last(code)
        gap = ((bars[0]["open"] / ref - 1) * 100) if ref else 0.0
    gap = float(gap or 0.0)

    # ① 갭상승
    ref_px = (bars[0]["open"] / (1 + gap / 100)) if (bars and gap > -100) else 0
    if gap > W.CFG["gap_tol"]:
        lo = min(x["low"] for x in bars) if bars else 0
        back = bool(ref_px and lo <= ref_px)
        ko.append(f"① 갭상승 +{gap:.2f}% — 오늘 시가 ₩{bars[0]['open']:,.0f}가 어제 마지막 가격 "
                  f"₩{ref_px:,.0f}보다 높게 시작했습니다. "
                  + (f"이후 ₩{lo:,.0f}까지 내려와 어제 값을 되찾았으므로 규칙상 매수 가능합니다."
                     if back else
                     f"아직 어제 값까지 내려오지 않았습니다 (오늘 저가 ₩{lo:,.0f}). 내려올 때까지 사지 않습니다."))
        en.append(f"① gap-up +{gap:.2f}% - today opened at ₩{bars[0]['open']:,.0f}, above "
                  f"yesterday's last ₩{ref_px:,.0f}. "
                  + (f"It has since traded down to ₩{lo:,.0f}, so buying is open again."
                     if back else
                     f"It has not come back yet (today's low ₩{lo:,.0f}), so we do not buy."))
    else:
        ko.append(f"① 갭상승이 없습니다 — 오늘 시가 ₩{bars[0]['open']:,.0f}, 어제 마지막 가격 "
                  f"₩{ref_px:,.0f} 대비 {gap:+.2f}%입니다 (기준 +{W.CFG['gap_tol']}% 이내). "
                  f"높은 가격에 시작하지 않았으니 살 수 있는 날입니다." if bars else "① 갭상승 정보 없음")
        en.append(f"① no gap-up - today opened at ₩{bars[0]['open']:,.0f}, {gap:+.2f}% against "
                  f"yesterday's last ₩{ref_px:,.0f} (the line is +{W.CFG['gap_tol']}%). "
                  f"It did not start expensive, so this is a day we may buy." if bars else "")

    # ② 위치 — 6개월 / 3개월 / 1개월 / 1주
    pk, pe, pos_ok = _pos_blocks(code, px)
    ko += pk
    en += pe
    out["position_ok"] = pos_ok

    # ③ 거래량
    try:
        volx = W._vol_x(bars, W.CFG) if bars else 0
        vnow = int(bars[-1].get("vol") or 0) if bars else 0
        vtot = sum(int(x.get("vol") or 0) for x in bars) if bars else 0
        adv = 0.0
        rows = A._daily3(code, 20) or []
        vols = [r["v"] for r in rows if r.get("v")]
        if vols:
            adv = sum(vols) / len(vols)
        ko.append(f"③ 거래량 — 지금 이 1분에 {vnow:,}주, 최근 20분 평균의 {volx}배입니다. "
                  f"오늘 누적 {vtot:,}주"
                  + (f" (20일 하루 평균 {adv:,.0f}주의 {vtot / adv * 100:.0f}%)." if adv else "."))
        en.append(f"③ volume - {vnow:,} sh in this minute, {volx}x its own 20-minute average. "
                  f"{vtot:,} sh so far today"
                  + (f" ({vtot / adv * 100:.0f}% of the {adv:,.0f} it averages in a day)." if adv else "."))
    except Exception as e:
        log.debug(f"wave_why volume {code}: {str(e)[:60]}")

    # ④ 뉴스
    try:
        from services.checklist_advice import _fresh_stamps, danger_stamps
        stamps = _fresh_stamps(code, limit=3, max_age_min=180) or []
        bad = danger_stamps(code, stamps, name)
        if bad:
            _b = bad[0] if isinstance(bad, (list, tuple)) else bad
            ko.append(f"④ ⚠️ 나쁜 뉴스가 있습니다 — {str(_b)[:90]}. 가격을 누를 수 있는 재료입니다.")
            en.append(f"④ ⚠️ there IS bad news - {str(_b)[:90]}. It can press the price down.")
            out["news_ok"] = False
        else:
            ko.append(f"④ 나쁜 뉴스가 없습니다 — 최근 3시간 이 종목 관련 기사 {len(stamps)}건을 "
                      f"확인했고, 가격을 누를 만한 위험 뉴스는 없습니다.")
            en.append(f"④ no bad news - {len(stamps)} story/stories on this stock in the last "
                      f"three hours, none of them the kind that presses a price down.")
            out["news_ok"] = True
    except Exception as e:
        log.debug(f"wave_why news {code}: {str(e)[:60]}")

    # ⑤ 100문항 체크리스트 + 순위
    # THE RANK AND THE SCORE ARE ASKED SEPARATELY. They were in one try, so a
    # failure in the ranking (which reads a cache that can be cold) took the
    # whole checklist line with it - and the block simply did not appear on the
    # live server while it worked in a test process. Two questions, two guards.
    rank = tot = None
    try:
        rows = A._brain_rows() or []
        if not rows:
            # the brain cache can be cold; the desk's own room list carries the
            # same checklist score and is always warm
            rows = [{"code": c, "score": sc9} for c, _n, sc9 in (A.desk_codes() or [])]
        rows = [r for r in rows if r.get("score") is not None]
        if rows:
            order = sorted(rows, key=lambda r: -(r.get("score") or 0))
            rank = next((i + 1 for i, r in enumerate(order)
                         if str(r.get("code")) == code), None)
            tot = len(order)
    except Exception as e:
        log.warning(f"wave_why rank {code}: {str(e)[:90]}")
    try:
        sc = None
        if db is not None:
            from services.checklist_engine import stock_scorecard
            sc = stock_scorecard(db, code)
            out["scorecard"] = {
                "score": sc.get("score"), "max": sc.get("max"), "pct": sc.get("pct"),
                "verdict_ok": sc.get("verdict_ok"),
                "deal_breakers": sc.get("deal_breakers") or [],
                "unknown": len(sc.get("unknown") or []),
                "stock_items": (sc.get("stock") or {}).get("items") or [],
                "market_items": (sc.get("market") or {}).get("items") or [],
                "rank": rank, "of": tot,
            }
        _r = f" · 오늘 후보 {tot}개 중 {rank}위" if rank else ""
        _re = f" · ranked {rank} of {tot} today" if rank else ""
        if sc and sc.get("pct") is not None:
            ko.append(f"⑤ 100문항 체크리스트 — {sc['score']:.0f}/{sc['max']:.0f}점 "
                      f"({sc['pct']:.0f}%){_r}, "
                      + ("통과 기준을 넘었습니다." if sc.get("verdict_ok") else "통과 기준에 못 미칩니다.")
                      + (f" 탈락 사유: {', '.join(_dbtxt(sc['deal_breakers'])[:2])}."
                         if sc.get("deal_breakers") else "")
                      + " (전체 항목은 아래를 클릭하세요.)")
            en.append(f"⑤ the 100-item checklist - {sc['score']:.0f}/{sc['max']:.0f} "
                      f"({sc['pct']:.0f}%){_re}, "
                      + ("above the bar." if sc.get("verdict_ok") else "below the bar.")
                      + (f" Deal-breakers: {', '.join(_dbtxt(sc['deal_breakers'])[:2])}."
                         if sc.get("deal_breakers") else "")
                      + " (click below for every item.)")
        else:
            ko.append(f"⑤ 100문항 체크리스트{_r} — 상세 점수는 카드를 열면 계산됩니다.")
            en.append(f"⑤ the 100-item checklist{_re} - the detailed score is computed on the card.")
    except Exception as e:
        log.warning(f"wave_why checklist {code}: {type(e).__name__}: {str(e)[:120]}")
        ko.append("⑤ 100문항 체크리스트 — 지금은 점수를 읽지 못했습니다 (아래 항목은 그대로 보실 수 있습니다).")
        en.append("⑤ the 100-item checklist - the score could not be read this minute "
                  "(the items below are still there).")

    out["ko"], out["en"] = ko, en
    return out
