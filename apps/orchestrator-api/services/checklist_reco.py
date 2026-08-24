"""🎯 checklist_reco — "N종목 추천해줘" answered by the 100-item checklist + OUR algo.

The boss's rules (2026-08-24, two rounds):
- Recommendations come from the deployed algorithm's own judgement material — 일봉 캔들 ·
  분봉/실시간 · 거래량 · 뉴스 — plus the 100-item checklist ranking. Machine Learning
  plays no part; buy_picks (ML-led) is only the fallback when this engine has no data.
- The answer must reflect RIGHT NOW: the morning checklist score is the base, and a
  LIVE layer (intraday change, order-book pressure, year-range zone) re-ranks it — the
  same question an hour later may give different offers, and the answer says its time.
- The daily chart must say WHERE the stock stands: 매수구간 (bottom ≤15% of the year
  range), 매도구간 (≥85%, the algo's selling zone), or middle.
- LIST FIRST, PROOF ON CLICK: the answer is a compact ranked list; each stock carries
  [근거](ask:...) — clicking sends the evidence question and detail() answers with the
  full checklist-score breakdown (group scores mapped to their checklist item numbers),
  일봉/분봉/거래량/뉴스, and the chart link — so he can SEE it is the checklist, not an
  LLM's opinion. The live 36-item scorecard ("<종목> 체크리스트") is the final proof.

Ranking source = daily_pick (character × condition, checklist-weighted, ML-free).
Names are emitted as [이름](chart:code) — clickable, opens the TradingView proof panel.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.logger import log

KST = timezone(timedelta(hours=9))

# checklist group → the 100-item numbers it scores (from daily_pick's own mapping)
GROUP_ITEMS = {
    "trend": ("50", "51", "52", "58"),
    "liquidity": ("21", "46", "47", "69"),
    "flexibility": ("48",),
    "levels": ("62", "63", "67", "74"),
    "momentum": ("60", "61"),
    "flows": ("31", "32", "34", "43"),
}
GROUP_KO = {"trend": "추세", "liquidity": "유동성", "flexibility": "유연성",
            "levels": "지지저항", "momentum": "모멘텀", "flows": "수급"}
GROUP_EN = {"trend": "Trend", "liquidity": "Liquidity", "flexibility": "Flexibility",
            "levels": "Levels", "momentum": "Momentum", "flows": "Flows"}

_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


def _ranking() -> Optional[dict]:
    """Today's full checklist ranking — saved file first, else compute (cached 10 min)."""
    from services import daily_pick as dp
    day = dp._today()
    try:
        saved = json.loads(dp._PICK_FILE.read_text(encoding="utf-8"))
        if saved.get("day") == day and saved.get("rows"):
            return {"day": day, "rows": saved["rows"]}
    except Exception:
        pass
    def _compute():
        try:
            res = dp.pick(day)
            if res.get("ok"):
                return {"day": day, "rows": res["rows"]}
        except Exception as e:
            log.warning(f"checklist_reco ranking failed: {str(e)[:120]}")
        return None
    return _cached("rank", 600, _compute)


def _n_from(transcript: str, default: int = 3) -> int:
    m = re.search(r"(\d{1,2})\s*(?:개|종목|가지|stocks?|compan(?:y|ies)|picks?)", transcript or "", re.I)
    if not m:
        m = re.search(r"\b([1-9])\b", transcript or "")
    try:
        return max(1, min(int(m.group(1)), 8)) if m else default
    except Exception:
        return default


def _fmt(v) -> str:
    try:
        return f"{int(round(float(v))):,}"
    except Exception:
        return str(v)


def _year_zone(code: str) -> Optional[dict]:
    """Where the stock stands in its ~1-year daily range (the algo's zones: selling
    zone ≥85%, buying/bottom zone ≤15%). Cached 10 min. {pos, zone, cur, chg}."""
    def _fetch():
        try:
            from services import naver_stock as ns
            rows = ns.daily_history(code, days=260)
            if len(rows) < 40:
                return None
            cur = rows[0].get("close")
            prev = rows[1].get("close") if len(rows) > 1 else None
            hi = max(r.get("high") or 0 for r in rows)
            lo = min(r.get("low") or 10 ** 12 for r in rows)
            if not cur or hi <= lo:
                return None
            pos = (cur - lo) / (hi - lo) * 100
            zone = "sell" if pos >= 85 else "buy" if pos <= 15 else "mid"
            chg = (cur - prev) / prev * 100 if prev else None
            return {"pos": round(pos), "zone": zone, "cur": cur, "chg": chg}
        except Exception:
            return None
    return _cached(f"zone:{code}", 600, _fetch)


def _live_state(db, code: str) -> dict:
    """RIGHT-NOW state: realtime snapshot (price/pressure/program) + year zone."""
    out: dict[str, Any] = {}
    try:
        from services.trading_brief import realtime_for
        out["rt"] = realtime_for(code, db=db) or {}
    except Exception:
        out["rt"] = {}
    out["zone"] = _year_zone(code)
    z, rt = out["zone"], out["rt"]
    cur = rt.get("price") or (z or {}).get("cur")
    out["cur"] = cur
    chg = None
    if z and z.get("cur") and z.get("chg") is not None and not rt.get("price"):
        chg = z["chg"]
    elif rt.get("price") and z:
        try:
            from services import naver_stock as ns
            rows = ns.daily_history(code, days=3)
            prev = rows[1]["close"] if len(rows) > 1 else None
            chg = (float(rt["price"]) - prev) / prev * 100 if prev else None
        except Exception:
            chg = None
    out["chg"] = chg
    # LIVE ADJUSTMENT — what makes the answer differ an hour later (boss 2026-08-24
    # "one hour ago also same answer": widened from ±5 to ±9 so real market movement
    # can actually reorder the list, and split into named parts so the evidence panel
    # can PROVE where the number came from):
    #   price ±4  = today's move ×1.5, capped
    #   book  ±2  = order-book pressure (bid/ask imbalance)
    #   zone  +2/-3 = year-range position — bottom zone helps a BUY, selling zone hurts
    a_px = max(-4.0, min(4.0, chg * 1.5)) if chg is not None else 0.0
    imb = rt.get("imbalance")
    a_ob = 2.0 if (imb is not None and imb > 0.15) else -2.0 if (imb is not None and imb < -0.15) else 0.0
    a_zn = 2.0 if (z and z.get("zone") == "buy") else -3.0 if (z and z.get("zone") == "sell") else 0.0
    out["adj_parts"] = {"price": round(a_px, 1), "book": a_ob, "zone": a_zn}
    out["adj"] = round(a_px + a_ob + a_zn, 1)
    return out


def _zone_str(z: Optional[dict], en: bool) -> str:
    if not z:
        return "연중 위치 미확인" if not en else "year position n/a"
    if en:
        lab = {"buy": "BUYING zone (year bottom)", "sell": "SELLING zone (year top)",
               "mid": "middle of range"}[z["zone"]]
        return f"daily chart {z['pos']}% of year range — {lab}"
    lab = {"buy": "🟢 매수구간(연중 바닥권)", "sell": "🔴 매도구간(연중 고점권)",
           "mid": "중간구간"}[z["zone"]]
    return f"일봉 연중 {z['pos']}% — {lab}"


def build(db, n: int = 3, transcript: str = "", lang: str = "ko") -> dict[str, Any]:
    """Compact ranked LIST (proof on click). {'ok': False} → caller falls back."""
    en = str(lang or "").lower().startswith("en")
    if not en and transcript and not re.search(r"[가-힣]", transcript) \
            and re.search(r"[a-zA-Z]", transcript):
        en = True
    n = _n_from(transcript, default=n)
    rank = _ranking()
    if not rank or not rank.get("rows"):
        return {"ok": False}
    base = sorted(rank["rows"], key=lambda r: -(r.get("score") or 0))[:max(n + 4, 8)]
    # live layer over the morning base → the RIGHT-NOW order
    scored = []
    for r in base:
        lv = _live_state(db, r["code"])
        scored.append((r, lv, (r.get("score") or 0) + lv["adj"]))
    scored.sort(key=lambda x: -x[2])
    top = scored[:n]
    now = datetime.now(KST).strftime("%H:%M")
    day = rank["day"]
    day_disp = f"{day[:4]}-{day[4:6]}-{day[6:]}"

    # THE PROCESS, SHOWN (boss 2026-08-24: "it should show like checklist and for each
    # checkpoint yes or no ... like people made, step by step, then finally give us
    # whatever we want"): step 1 = the market checklist item by item with live values,
    # step 2 = the candidate scan, step 3 = the live re-rank, then the final list.
    L = [(f"**🎯 Top {len(top)} for trading right now — 100-item checklist + our algo · as of {now}**"
          if en else
          f"**🎯 지금 매매 추천 TOP {len(top)} — 100문항 체크리스트 + 우리 알고 · {now} 기준**"), ""]
    # [0단계] 준비 #1~10 — the boss's own items come FIRST, in order (2026-08-24 round 10)
    try:
        from services.checklist_engine import full_checklist
        prep = [i for i in full_checklist()["items"] if i["cat"] == "준비"]
        L.append(f"**{'[STEP 0] Preparation (checklist #1–10 — your own check, the agent reminds)' if en else '[0단계] 준비 (체크리스트 #1~10 — 본인 확인, 에이전트는 상기만)'}**")
        for i2 in sorted(prep, key=lambda x: x["no"]):
            L.append(f"🧑 {i2['no']}. {(i2.get('q_en') if en else i2['q']) or i2['q']}")
        L.append("")
    except Exception:
        pass
    m_items: list = []
    try:
        from services.checklist_engine import market_preflight
        m = market_preflight(db)
        m_items = sorted(m.get("items", []), key=lambda x: x.get("no", 0))
        L.append(f"**{'[STEP 1] Market check (checklist #11–25, auto items)' if en else '[1단계] 시장 체크 (체크리스트 #11~25 자동 항목)'} — "
                 f"{m['score']}/{m['max']}{'점' if not en else ''}**")
        for it in m_items:                      # sequential order: #11, #12, #16, #17 …
            mark = "✅" if it.get("ok") else "❌" if it.get("ok") is False else "❓"
            q = (it.get("q_en") if en else it.get("q")) or it.get("q")
            L.append(f"{mark} #{it['no']} {q} — {it.get('detail')}")
        if m.get("deal_breakers"):
            det = "; ".join(f"#{b['no']} {b['detail']}" for b in m["deal_breakers"][:2])
            L.append(("🚫 Deal-breaker today — new buying is reference only: " if en
                      else "🚫 오늘 결격 — 신규 매수는 참고만: ") + det)
    except Exception:
        pass
    _n_cand = len(rank.get("rows", []))
    L += ["", (f"**[STEP 2] Stock scoring (checklist #26–75)** — {_n_cand} candidates scored on "
               f"trend·liquidity·flexibility·levels·momentum·flows ({day_disp} morning, weighted); "
               f"top of the base: " if en else
               f"**[2단계] 종목 채점 (체크리스트 #26~75)** — 후보 {_n_cand}종목을 추세·유동성·유연성·"
               f"지지저항·모멘텀·수급 가중 채점 ({day_disp} 아침 기준) · 상위: ")
          + " · ".join(f"{r.get('name')} {r.get('score')}" for r in base[:5])]
    L += ["", (f"**[STEP 3] Live re-rank (right now, {now})** — price move ×1.5 (±4) + "
               f"order book (±2) + year zone (+2 bottom / −3 selling zone); "
               f"ask again later and the order can change." if en else
               f"**[3단계] 실시간 보정 ({now} 현재)** — 등락×1.5(±4) + 호가(±2) + 연중구간(바닥 +2 / 매도구간 −3) · "
               f"시간이 지나면 순위가 달라질 수 있습니다.")]
    L += ["", f"**{'[RESULT] TOP ' + str(len(top)) if en else '[결과] 추천 TOP ' + str(len(top))}**"]
    # which recommendations are ACTUALLY TRADING today (the reco desk's five, fixed at
    # the morning bell — the desk never swaps mid-session, so the live list can differ)
    _traded: set = set()
    _traded_names: list = []
    try:
        from services.daily_pick import score_five
        for _c, _nm2 in score_five():
            _traded.add(_c)
            _traded_names.append(_nm2)
    except Exception:
        pass
    for i, (r, lv, tot) in enumerate(top, 1):
        code = r["code"]
        name = r.get("name") or code
        if name == code:
            try:
                from services.stock_resolver import display_name
                name = display_name(code) or code
            except Exception:
                pass
        if en:
            try:
                from services.stock_resolver import display_name_en
                name = display_name_en(code) or name
            except Exception:
                pass
        bits = []
        if lv.get("cur"):
            bits.append(f"₩{_fmt(lv['cur'])}")
        if lv.get("chg") is not None:
            bits.append(f"{lv['chg']:+.1f}%")
        rt = lv.get("rt") or {}
        if rt.get("pressure"):
            bits.append(rt.get("pressure_en") if en and rt.get("pressure_en") else rt["pressure"])
        zs = _zone_str(lv.get("zone"), en)
        # evidence:CODE — the chat opens a RIGHT-side proof panel (chart + the data),
        # no round-trip through name resolution (boss 2026-08-24: the ask-link version
        # re-asked "which stock do you mean?" on English names).
        L.append(f"**{i}. [{name}](chart:{code})** — "
                 + (f"{round(tot, 1)} pts (base {r.get('score')} + now {lv['adj']:+g})"
                    if en else f"{round(tot, 1)}점 (기준 {r.get('score')} + 지금 {lv['adj']:+g})")
                 + (f" · {' · '.join(bits)}" if bits else "")
                 + f" · {zs}"
                 + (f" · 🟢 {'trading today' if en else '오늘 매매중'}" if code in _traded else "")
                 + f" · [{'evidence 🔍' if en else '근거 🔍'}](evidence:{code})")
    if _traded_names:
        L += ["", (f"🟢 Today's RECO DESK (fixed at the morning bell, actually trading now): "
                   f"{' · '.join(_traded_names)}. The list above is the LIVE view and can differ "
                   f"intraday — the desk never swaps stocks mid-session (a swap would abandon the "
                   f"day's tape and open positions). Watch them trade: Paper Trading → 체크리스트 추천 데스크."
                   if en else
                   f"🟢 오늘의 추천 데스크(아침 확정, 지금 실제 매매중): {' · '.join(_traded_names)}. "
                   f"위 목록은 '지금' 실시간 순위라 장중에는 달라질 수 있습니다 — 데스크는 장중 종목 교체를 "
                   f"하지 않습니다(교체하면 그날의 기록과 포지션을 버리게 됩니다). "
                   f"매매 화면: 모의투자 → 체크리스트 추천 데스크.")]
    L += ["",
          ("Click a NAME to open its live chart on the left · click 근거/evidence to see exactly "
           "how the 100-item checklist, daily chart, minute/real-time, volume and news scored it — "
           "this ranking is computed from those numbers, not an LLM's opinion."
           if en else
           "종목 이름 클릭 = 왼쪽에 실시간 차트 · [근거 🔍] 클릭 = 100문항 체크리스트·일봉·분봉·거래량·뉴스가 "
           "어떻게 점수를 만들었는지 그대로 보여줍니다 — 이 순위는 LLM 의견이 아니라 그 숫자들로 계산됩니다.")]
    # PROCESS payload for the frontend's live checking simulation (boss 2026-08-24:
    # "I wanna see like simulation process to proof that our agent is using the
    # checklist to decide") — every candidate's real scores, in rank order.
    proc = {"market": [{"no": it.get("no"), "ok": it.get("ok"), "q": it.get("q"),
                        "q_en": it.get("q_en"), "detail": it.get("detail")} for it in m_items],
            "candidates": [{"code": r["code"], "name": r.get("name"), "score": r.get("score"),
                            "groups": r.get("groups")}
                           for r in sorted(rank["rows"], key=lambda r: -(r.get("score") or 0))],
            "picked": [r["code"] for r, _l, _t in top], "n": len(top)}
    return {"ok": True, "reply": "\n".join(L), "picks": [r["code"] for r, _l, _t in top],
            "process": proc}


def detail(db, query: str, lang: str = "ko") -> Optional[str]:
    """PROOF VIEW routed from a typed question — resolves the stock, then delegates."""
    en = str(lang or "").lower().startswith("en")
    if not en and query and not re.search(r"[가-힣]", query) and re.search(r"[a-zA-Z]", query):
        en = True
    try:
        from services.stock_resolver import resolve_one
        code, name = resolve_one(query or "")
    except Exception:
        code, name = None, None
    if not code:
        return None
    return detail_by_code(db, code, "en" if en else "ko", name=name)


def detail_by_code(db, code: str, lang: str = "ko", name: Optional[str] = None) -> Optional[str]:
    """PROOF VIEW for one stock BY CODE (the right-side evidence panel fetches this):
    checklist group scores mapped to their 100-item numbers + 일봉 zone + 분봉/실시간 +
    거래량 + 뉴스 (clickable headlines) — the chart renders in the panel itself."""
    en = str(lang or "").lower().startswith("en")
    code = str(code).zfill(6)
    if name is None:
        try:
            from services.stock_resolver import display_name
            name = display_name(code)
        except Exception:
            name = code
    rank = _ranking() or {}
    row = next((r for r in rank.get("rows", []) if r["code"] == code), None)
    lv = _live_state(db, code)
    now = datetime.now(KST).strftime("%H:%M")
    nm = name or code
    if en:
        try:
            from services.stock_resolver import display_name_en
            nm = display_name_en(code) or nm
        except Exception:
            pass
    L = [(f"**🔍 {nm} ({code}) — recommendation evidence · as of {now}**"
          if en else f"**🔍 {nm} ({code}) — 추천 근거 · {now} 기준**"),
         ("Computed from the 100-item checklist + live data — not an LLM opinion."
          if en else "LLM 의견이 아니라 100문항 체크리스트 점수 + 실시간 데이터 계산입니다."), ""]
    if row:
        L.append(f"**① {'100-item checklist score' if en else '100문항 체크리스트 점수'} — "
                 f"{row.get('score')}{'점' if not en else ' pts'}**")
        g = row.get("groups") or {}
        names = GROUP_EN if en else GROUP_KO
        for k in ("trend", "liquidity", "flexibility", "levels", "momentum", "flows"):
            if k in g:
                items = "·".join("#" + x for x in GROUP_ITEMS.get(k, ()))
                L.append(f"· {names[k]} {g[k]}/100 ({items})")
        if row.get("why"):
            L.append(("· key reasons: " if en else "· 핵심 사유: ") + " · ".join(row["why"]))
    else:
        L.append("① " + ("not in today's ranking universe — live view below."
                         if en else "오늘 채점 대상에 없는 종목입니다 — 아래는 실시간 상태입니다."))
    z = lv.get("zone")
    L += ["", f"**② {'Daily chart' if en else '일봉 차트'}:** " + _zone_str(z, en)
          + ((f" ({'buy ≤15% · sell ≥85%' if en else '매수구간 ≤15% · 매도구간 ≥85%'})") if z else "")]
    rt = lv.get("rt") or {}
    t_bits = []
    if lv.get("cur"):
        t_bits.append((f"now ₩{_fmt(lv['cur'])}" if en else f"현재가 {_fmt(lv['cur'])}원"))
    if lv.get("chg") is not None:
        t_bits.append(f"{lv['chg']:+.1f}%")
    if rt.get("pressure"):
        t_bits.append(rt.get("pressure_en") if en and rt.get("pressure_en") else rt["pressure"])
    if rt.get("program_net") is not None:
        t_bits.append((f"program {rt['program_net']:+,}" if en else f"프로그램 {rt['program_net']:+,}"))
    L.append(f"**③ {'Minute/real-time' if en else '분봉·실시간'}:** "
             + (" · ".join(t_bits) if t_bits else ("no live tape" if en else "실시간 데이터 없음")))
    ap = lv.get("adj_parts") or {}
    if ap:
        L.append((f"· live adjustment **{lv.get('adj', 0):+g}** = price move {ap.get('price', 0):+g} "
                  f"+ order book {ap.get('book', 0):+g} + year zone {ap.get('zone', 0):+g} "
                  f"(this is what moves the ranking during the day)"
                  if en else
                  f"· 실시간 보정 **{lv.get('adj', 0):+g}점** = 등락 {ap.get('price', 0):+g} "
                  f"+ 호가 {ap.get('book', 0):+g} + 연중구간 {ap.get('zone', 0):+g} "
                  f"(장중에 순위를 움직이는 부분입니다)"))
    v_line = "—"
    if row and (row.get("groups") or {}).get("liquidity") is not None:
        v_line = (f"liquidity {row['groups']['liquidity']}/100" if en
                  else f"유동성 {row['groups']['liquidity']}/100")
        if any("거래량" in w for w in (row.get("why") or [])):
            v_line += " · " + ("volume surge" if en else "거래량 급증")
    L.append(f"**④ {'Volume' if en else '거래량'}:** {v_line}")
    n_line = "no recent stamps" if en else "최근 뉴스 스탬프 없음"
    n_links: list[str] = []
    try:
        from services import news_impact as ni
        items = list(ni.effective_news(db, code, limit=6) or [])
        if items:
            sc = sum(1 if it.get("direction") in (1, "▲") else -1 if it.get("direction") in (-1, "▼") else 0
                     for it in items)
            n_line = (f"score {max(-3, min(3, sc)):+d} ({len(items)})" if en
                      else f"점수 {max(-3, min(3, sc)):+d} ({len(items)}건)")
            # summary here, full article one click away (boss 2026-08-24: "news also
            # clickable, I can read if I wanna more detail")
            for it in items[:3]:
                t = (it.get("title") or "").strip()[:70].replace("[", "").replace("]", "")
                u = (it.get("url") or "").strip()
                d = it.get("direction")
                mark = "📈" if d in (1, "▲") else "📉" if d in (-1, "▼") else "•"
                if t:
                    n_links.append(f"  {mark} [{t}]({u})" if u.startswith("http") else f"  {mark} {t}")
    except Exception:
        pass
    L.append(f"**⑤ {'News' if en else '뉴스'}:** {n_line}")
    L += n_links
    # ⑥ THE FULL PER-ITEM CHECK, live (boss 2026-08-24: "for each checkpoint yes or no,
    # and if it is about some % or number it should answer") — the 27 stock items of the
    # checklist scored against live data, each with its measured value.
    try:
        from services.checklist_engine import stock_scorecard
        card = stock_scorecard(db, code)
        lay = card.get("stock") or {}
        L += ["", f"**⑥ {'Live item-by-item check' if en else '100문항 실측 (종목 항목)'} — "
                  f"{lay.get('score')}/{lay.get('max')}{'' if en else '점'} ({card.get('pct')}%)**"]
        for it in sorted(lay.get("items", []), key=lambda x: x.get("no", 0)):
            mark = "✅" if it.get("ok") else "❌" if it.get("ok") is False else "❓"
            q = (it.get("q_en") if en else it.get("q")) or it.get("q")
            L.append(f"{mark} #{it['no']} {q} — {it.get('detail')}")
        if card.get("deal_breakers"):
            L.append(("🚫 deal-breakers: " if en else "🚫 결격: ")
                     + "; ".join(f"#{b['no']} {b['detail']}" for b in card["deal_breakers"][:3]))
    except Exception:
        L += ["", (f"(live item check unavailable — ask \"{nm} checklist\" in chat)" if en
                   else f"(실측 점검을 불러오지 못했습니다 — 챗봇에 \"{nm} 체크리스트\"라고 물어보세요)")]
    return "\n".join(L)
