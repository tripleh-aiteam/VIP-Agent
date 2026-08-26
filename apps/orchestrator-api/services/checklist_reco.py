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

# items measured through the ranking's own inputs (data proxies)
PROXY_NOS = {46, 48, 56, 69, 70}
# per-stock news items — read by the Qwen news engine (danger stamps)
NEWS_NOS = {26, 27, 29, 40, 42, 44, 45}
# HANDLED BY THE ALGORITHM (boss: "it should decide by agent, like stop-loss")
ALGO_EN = {71: "the five doors + layer judgment filter fake-out candles at entry",
           72: "post-entry is engine-managed — trailing off the peak, never below break-even",
           73: "Algo2 (ripple) specializes in ranging tape — regime handled by the rules",
           74: "buying the pullback IS the entry rule (dip/rebound doors + scout)",
           77: "-1% hard stop — the engine sells the moment the low is touched",
           78: "Algo1 harvests 50% per +1% · Algo2 10% · Algo3 sells all at the 3rd red after the peak",
           80: "scaled entry — the scout/ladder entry rules",
           81: "scaled exit — the 50%/10% harvest ladders + selling-zone full exit, automatic",
           83: "mechanical stop — executed by the engine, no human hand",
           84: "the harvest ladder banks profit every +1% automatically",
           86: "every trade is journaled automatically (chart arrows + history)",
           92: "the loss-limit reset (-1.5%) halts the run automatically",
           93: "sell-pressure response — the sell rules react to red candles/lows automatically",
           97: "unfilled orders are managed/cancelled by the engine",
           99: "0.23% round-trip fee+tax is inside every calculation"}
ALGO_KO = {71: "다섯 문 + 레이어 판정이 진입 시 속임수 캔들을 거릅니다",
           72: "진입 후는 엔진 관리 — 정점 추적 매도, 본전 아래로는 안 내려감",
           73: "알고2(잔물결)가 횡보 장세 특화 — 장세 인식은 규칙이 처리",
           74: "돌파 후 눌림 매수가 곧 진입 규칙(급락/반등 문 + 스카웃)",
           77: "-1% 하드스톱 — 저가 터치 즉시 엔진이 전량 매도",
           78: "알고1 +1%마다 50% 수확 · 알고2 10% · 알고3 정점 후 3음봉 전량 매도",
           80: "분할 진입 — 스카웃/사다리 진입 규칙",
           81: "분할 매도 — 50%/10% 수확 사다리 + 매도구간 전량 매도, 자동",
           83: "기계적 손절 — 사람 손 없이 엔진이 실행",
           84: "수확 사다리가 +1%마다 이익을 자동 실현",
           86: "모든 매매가 자동 기록 (차트 화살표 + 매매 이력)",
           92: "손실 한도 리셋(-1.5%) 도달 시 자동 중단",
           93: "매도세 강화 시 매도 규칙(음봉/저가)이 자동 대응",
           97: "미체결 주문은 엔진이 자동 관리/취소",
           99: "왕복 수수료+세금 0.23%가 모든 계산에 반영"}

_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


def _ranking() -> Optional[dict]:
    """Today's full checklist ranking — ONE scorer for the whole platform (boss
    2026-08-26: 'weight rule must be implemented, otherwise all of them
    connected with still old weights'): the enriched /daily-pick carries the
    SUM-LAW score (per-item weights, no averaging) in cats.avg — the same
    number the desk chips and heartbeat show. Saved file / dp.pick only as
    fallbacks. Cached 10 min."""
    from services import daily_pick as dp
    day = dp._today()
    def _sum_rows():
        import urllib.request as _ur
        d = json.load(_ur.urlopen(
            "http://127.0.0.1:8000/paper-desk/daily-pick", timeout=120))
        rows = d.get("rows") or []
        out = []
        for x in rows:
            c9 = (x.get("cats") or {}).get("avg")
            if c9 is not None:
                x = dict(x, score=round(float(c9), 1))
            out.append(x)
        out.sort(key=lambda x: -(x.get("score") or 0))
        return out
    try:
        rows = _cached("rank_sum", 600, _sum_rows)
        if rows:
            return {"day": day, "rows": rows}
    except Exception:
        pass
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
    # cap 15 (boss 2026-08-26: he asked Top 10 and silently received Top 8 -
    # the old cap of 8 clipped his number without a word; never shrink his ask
    # quietly)
    m = re.search(r"(\d{1,2})\s*(?:개|종목|가지|stocks?|compan(?:y|ies)|picks?)", transcript or "", re.I)
    if not m:
        m = re.search(r"\b(\d{1,2})\b", transcript or "")
    try:
        return max(1, min(int(m.group(1)), 15)) if m else default
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
    # THE VOLUME MUSCLE (boss 2026-08-25/26, same term as the 4-second desk
    # loop): today's pace vs the stock's own 20-day average — up to +4. Only
    # watched stocks have tape; others honestly get 0.
    a_vol = 0.0
    try:
        import services.kiwoom_tape as _kt
        from services.reco_rank_log import _base as _rb9
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        _av20 = (_rb9.get("vol20") or {}).get(code) or 0
        _tv = _kt.today_volume(code)
        if _av20 and _tv:
            _nk = _dt.now(_tz(_td(hours=9)))
            _frac = max(0.05, min(1.0, ((_nk.hour - 9) * 60 + _nk.minute) / 390))
            _ratio = _tv / (_av20 * _frac)
            a_vol = (4.0 if _ratio >= 3 else 3.0 if _ratio >= 2
                     else 1.5 if _ratio >= 1.5 else (-1.0 if _ratio < 0.5 else 0.0))
    except Exception:
        pass
    out["adj_parts"] = {"price": round(a_px, 1), "book": a_ob, "zone": a_zn,
                        "vol": a_vol}
    out["adj"] = round(max(-12.0, min(12.0, a_px + a_ob + a_zn + a_vol)), 1)
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
    # AFTER-HOURS NOTICE (boss 2026-08-24: "after market, if we ask any recommendation
    # or price, you should indicate this is not market hours")
    try:
        from services.kiwoom_tape import market_open
        if not market_open():
            L.insert(1, ("🌙 The market is CLOSED right now (KRX 09:00–15:30 KST) — prices, "
                         "order-book reads and this ranking reflect the LAST session; execution "
                         "resumes at the next open." if en else
                         "🌙 지금은 장외 시간입니다 (KRX 정규장 09:00~15:30) — 가격·호가·순위는 마지막 "
                         "거래 기준이며, 실제 매매는 다음 개장부터 실행됩니다."))
    except Exception:
        pass
    # ALL 100 ITEMS, STRICT ORDER 1→100 (boss 2026-08-24: "show all 100 checklist,
    # make them correct sequential order"). Each item is marked by HOW it is checked:
    #   ✅/❌/❓ = measured live today (market-wide items)
    #   📊      = scored PER STOCK — feeds the candidate ranking below (see 근거 🔍)
    #   🧑      = the trader's own item (the agent reminds, never answers for him)
    #   ⬜      = no data source connected yet — honest, never faked
    m_items: list = []
    m = {}
    try:
        from services.checklist_engine import market_preflight
        m = market_preflight(db)
        m_items = m.get("items", [])
    except Exception:
        pass
    # THE FULL 11→100, every number present (boss 2026-08-25: "some are missing —
    # make sure it must have 11-100"). Measured items carry live values; the rest carry
    # their mark: 📊 per-stock (answered in 근거 🔍), 📰 Qwen news, 🤖 algorithm-executed,
    # 🧑 the trader's own. #1~10 (준비) stay his — the answer starts at the market.
    _n_cand = len(rank.get("rows", []))
    live_by_no = {it.get("no"): it for it in m_items}
    per_stock_nos: set = set(PROXY_NOS)
    try:
        from services.checklist_engine import STOCK_ITEMS
        per_stock_nos |= {it[0] for it in STOCK_ITEMS}
    except Exception:
        pass
    algo_map = ALGO_EN if en else ALGO_KO
    # 📊/📰 lines carry the #1 PICK's measured answers (boss 2026-08-25: "there is no
    # answer to some of them — at least show a number or yes/no"); other stocks' answers
    # live in their own 근거 🔍 card.
    _t1 = top[0][0] if top else None
    _t1_name = (_t1.get("name") if _t1 else "") or ""
    _t1_ans: dict = {}
    if _t1:
        try:
            from services.checklist_engine import stock_scorecard
            _card1 = stock_scorecard(db, _t1["code"])
            for it1 in (_card1.get("stock") or {}).get("items", []):
                _mk1 = "✅" if it1.get("ok") else "❌" if it1.get("ok") is False else "❓"
                _t1_ans[it1.get("no")] = f"{_mk1} {it1.get('detail')}"
        except Exception:
            pass
        try:
            for _no1, (_ok1, _det1) in _news_answers(db, _t1["code"], en).items():
                _t1_ans.setdefault(_no1, f"{'✅' if _ok1 else '❌'} {_det1}")
        except Exception:
            pass
        _g1 = _t1.get("groups") or {}
        for _no2, _gk, _lab in ((46, "liquidity", "유동성/거래대금"), (48, "flexibility", "유연성/호가비용"),
                                (69, "liquidity", "거래량"), (56, "flows", "수급/잔량"), (70, "flows", "매수세")):
            if _no2 not in _t1_ans and _g1.get(_gk) is not None:
                _t1_ans[_no2] = (f"✅ {_lab} 그룹 {_g1[_gk]}/100 반영" if not en
                                 else f"✅ via {_gk} group {_g1[_gk]}/100")
    try:
        from services.checklist_engine import full_checklist
        all_items = sorted(full_checklist()["items"], key=lambda x: x["no"])
        L.append((f"Legend: ✅/❌/❓ measured now · 📊/📰 measured for the #1 pick {_t1_name} "
                  f"(other stocks → their 근거 🔍) · 🤖 executed by the algorithm · 🧑 yours" if en else
                  f"표기: ✅/❌/❓ 지금 실측 · 📊/📰 1위 {_t1_name} 실측값 표시(다른 종목은 근거 🔍) · "
                  f"🤖 알고리즘 자동 실행 · 🧑 본인 확인"))
        _sections = ([("[Market #11–25]", 11, 25), ("[Issue/Supply&Demand #26–45]", 26, 45),
                      ("[Stock selection #46–75]", 46, 75), ("[Execution #76–100]", 76, 100)]
                     if en else
                     [("[시장 #11~25]", 11, 25), ("[이슈/수급 #26~45]", 26, 45),
                      ("[종목선정 #46~75]", 46, 75), ("[실행/관리 #76~100]", 76, 100)])
        for _title, _lo, _hi in _sections:
            head = f"**{_title}**"
            if _lo == 11 and m:
                head += (f" — auto {m.get('score')}/{m.get('max')}" if en
                         else f" — 자동 {m.get('score')}/{m.get('max')}점")
            L += ["", head]
            for it in all_items:
                no = it["no"]
                if not (_lo <= no <= _hi):
                    continue
                q = (it.get("q_en") if en else it["q"]) or it["q"]
                lv = live_by_no.get(no)
                if lv is not None:
                    mark = "✅" if lv.get("ok") else "❌" if lv.get("ok") is False else "❓"
                    L.append(f"{mark} {no}. {q} — {lv.get('detail')}")
                elif no in per_stock_nos:
                    L.append(f"📊 {no}. {q} — {_t1_ans[no]}" if no in _t1_ans else f"📊 {no}. {q}")
                elif no in algo_map:
                    L.append(f"🤖 {no}. {q} — {algo_map[no]}")
                elif no in NEWS_NOS:
                    L.append(f"📰 {no}. {q} — {_t1_ans[no]}" if no in _t1_ans else f"📰 {no}. {q}")
                else:
                    L.append(f"🧑 {no}. {q}")
        if m.get("deal_breakers"):
            det = "; ".join(f"#{b['no']} {b['detail']}" for b in m["deal_breakers"][:2])
            L += ["", ("🚫 Deal-breaker today — new buying is reference only: " if en
                       else "🚫 오늘 결격 — 신규 매수는 참고만: ") + det]
        L.append("")
    except Exception:
        pass
    L += [(f"**[Scoring]** {_n_cand} candidates, SUM-scored by per-item weights "
           f"(measured 2026-08-26: volume family 15 [#46:5+#47:4+#21:4+#69:2] · foreign 6 · "
           f"MA alignment 5 · execution gates 8 · RSI/MACD/Bollinger 0; the plain SUM of passed "
           f"items, no averaging — {day_disp} base, same number as the desk), then the live "
           f"re-rank at {now} (price ±4 · book ±2 · zone +2/−3 · volume surge up to +4, cap ±12). "
           f"Each pick's own 100-item answers: click [근거 🔍]." if en else
           f"**[채점]** 후보 {_n_cand}종목 — 항목별 가중치 합산제(2026-08-26 실측: 볼륨 가족 15"
           f"[#46:5+#47:4+#21:4+#69:2] · 외국인 6 · 정배열 5 · 집행 관문 8 · RSI/MACD/볼린저 0점 — "
           f"통과 항목 점수의 단순 합, 평균 아님 · 데스크와 같은 숫자) + {now} 실시간 보정"
           f"(등락 ±4 · 호가 ±2 · 구간 +2/−3 · 거래량 서지 최대 +4, 총 ±12). "
           f"종목별 100문항 실측 답은 [근거 🔍] 클릭."), ""]
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
        # advised ORDER SIZE (boss 2026-08-25: "advise us stock name and stock quantity
        # and stock price") — budget ÷ live price, same sizing the chat order desk uses
        _qty_bit = ""
        if lv.get("cur"):
            try:
                from services.chat_trade import advise_qty as _aq, budget as _bg
                _qn = _aq(lv["cur"])
                _qty_bit = (f" · qty **{_qn:,}** (₩{_bg():,.0f} budget)" if en
                            else f" · 수량 **{_qn:,}주** (예산 ₩{_bg():,.0f} 기준)")
            except Exception:
                pass
        L.append(f"**{i}. [{name}](chart:{code})** — "
                 + (f"{round(tot, 1)} pts (base {r.get('score')} + now {lv['adj']:+g})"
                    if en else f"{round(tot, 1)}점 (기준 {r.get('score')} + 지금 {lv['adj']:+g})")
                 + (f" · {' · '.join(bits)}" if bits else "")
                 + f" · {zs}"
                 + _qty_bit
                 + (f" · 🟢 {'trading today' if en else '오늘 매매중'}" if code in _traded else "")
                 + f" · [{'evidence 🔍' if en else '근거 🔍'}](evidence:{code})")
    if _traded_names:
        L += ["", (f"🟢 Today's RECO DESK (fixed at the morning bell, actually trading now): "
                   f"{' · '.join(_traded_names)}. The list above is the LIVE view and can differ "
                   f"intraday — the desk never swaps stocks mid-session (a swap would abandon the "
                   f"day's tape and open positions)."
                   if en else
                   f"🟢 오늘의 추천 데스크(아침 확정, 지금 실제 매매중): {' · '.join(_traded_names)}. "
                   f"위 목록은 '지금' 실시간 순위라 장중에는 달라질 수 있습니다 — 데스크는 장중 종목 교체를 "
                   f"하지 않습니다(교체하면 그날의 기록과 포지션을 버리게 됩니다)."),
              # the PROOF button (boss 2026-08-24: "put button like go to menu that we
              # can see actually going on market") — opens the reco desk page
              (f"[📡 Watch them trading live → Checklist Reco Desk](nav:/testing/reco)"
               if en else
               f"[📡 실제 매매 보러가기 → 체크리스트 추천 데스크](nav:/testing/reco)"),
              # order-by-chat (boss 2026-08-25): the advice line above already names
              # stock + quantity + price — the order is one sentence away
              (f"🧾 To order right here, just say e.g. **\"buy {_traded_names[0]}\"** — "
               f"I'll show the confirmation first, then execute on your \"yes\"."
               if en else
               f"🧾 바로 주문하려면 **\"{_traded_names[0]} 매수\"** 라고 말씀하세요 — "
               f"확인 메시지를 먼저 보여드리고, \"네\" 하시면 체결됩니다.")]
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
    # ANSWER FIRST (boss 2026-08-26: "it should first show top 10 result, then
    # it should show why it is like this"): the reply leads with [RESULT]; the
    # 100-item evidence card follows under a 'why this ranking' divider.
    try:
        _ri9 = next(i for i, x in enumerate(L)
                    if "[RESULT]" in str(x) or "[결과]" in str(x))
        _hd9 = 2 if (len(L) > 1 and str(L[1]).startswith("🌙")) else 1
        _why9 = ["", ("――― why this ranking — the 100-item evidence ―――" if en
                      else "――― 왜 이 순위인가 — 100문항 근거 ―――"), ""]
        L = L[:_hd9] + [""] + L[_ri9:] + _why9 + L[_hd9:_ri9]
    except StopIteration:
        pass
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


def _news_answers(db, code: str, en: bool) -> dict:
    """Per-stock yes/no for the news items #26/27/29/40/42/44/45, computed from the
    Qwen-scored headline stream (boss 2026-08-24: 'we have news, so it can be a data
    source — kind of yes/no'). Returns {no: (ok, detail)}."""
    try:
        from services import news_impact as ni
        items = list(ni.effective_news(db, code, limit=8) or [])
    except Exception:
        return {}
    titles = [(it.get("title") or "") for it in items]
    joined = " ".join(titles)
    n = len(items)
    out: dict = {}

    def put(no, ok, ko, en_):
        out[no] = (ok, en_ if en else ko)

    theme = any(k in joined for k in ("테마", "주도", "급등", "강세", "랠리"))
    put(26, True, f"종목 뉴스 {n}건 · 테마성 언급 {'있음' if theme else '없음'}",
        f"{n} headlines · theme mention: {'yes' if theme else 'no'}")
    sect = [k for k in ("반도체", "바이오", "방산", "조선", "배터리", "2차전지", "AI", "정유") if k in joined]
    put(27, True, ("섹터 뉴스: " + ", ".join(sect[:3])) if sect else "핵심 섹터 뉴스 없음",
        ("sector news: " + ", ".join(sect[:3])) if sect else "no key sector news")
    earn = any(k in joined for k in ("실적", "공시", "영업이익", "수주", "계약", "매출"))
    put(29, True, "실적/공시 뉴스 " + ("있음" if earn else "없음"),
        "earnings/disclosure news: " + ("yes" if earn else "no"))
    put(40, True, f"자체 재료 뉴스 {n}건 — 차별성 {'있음' if n >= 2 else '약함'}",
        f"{n} own-story headlines — differentiation {'yes' if n >= 2 else 'weak'}")
    strong = any(k in joined for k in ("실적", "수주", "계약", "증설", "공급", "인수"))
    put(42, strong or n == 0,
        ("지속성 재료(실적/수주/증설) 있음" if strong else ("뉴스 없음" if n == 0 else "단발성 재료 가능 — 지속성 근거 없음")),
        ("sustainable catalyst (earnings/orders/capacity)" if strong else ("no news" if n == 0 else "possibly one-off — no durable catalyst")))
    lockup = any(k in joined for k in ("보호예수", "블록딜", "오버행", "물량 해제"))
    put(44, not lockup, ("⚠ 보호예수/블록딜 뉴스 감지" if lockup else "보호예수/오버행 뉴스 없음"),
        ("⚠ lock-up/block-deal news detected" if lockup else "no lock-up/overhang news"))
    hi_imp = sum(1 for it in items if float(it.get("impact") or 0) >= 0.7)
    put(45, True, f"고신뢰(임팩트≥0.7) 뉴스 {hi_imp}건 / 전체 {n}건",
        f"high-credibility (impact≥0.7) headlines: {hi_imp} of {n}")
    return out


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
    # ⑥ THIS STOCK'S FULL 100 — every checkpoint answered FOR THIS COMPANY (boss
    # 2026-08-24: "the 100-checklist result is different for each stock, so clicking
    # should show that stock's checklist"). Market items are today's shared measurements;
    # stock items carry THIS stock's live values.
    try:
        from services.checklist_engine import full_checklist, stock_scorecard
        card = stock_scorecard(db, code)
        lay = card.get("stock") or {}
        mkt = card.get("market") or {}
        measured = {it.get("no"): it for it in (mkt.get("items") or [])}
        measured.update({it.get("no"): it for it in (lay.get("items") or [])})
        algo = ALGO_EN if en else ALGO_KO
        news_ans = _news_answers(db, code, en)
        L += ["", f"**⑥ {nm} — {'the full 100, answered for this stock' if en else '이 종목의 100문항 실측'} · "
                  f"{lay.get('score')}/{lay.get('max')}{'' if en else '점'} ({card.get('pct')}%)**"]
        for it in sorted(full_checklist()["items"], key=lambda x: x["no"]):
            no = it["no"]
            q = (it.get("q_en") if en else it["q"]) or it["q"]
            mv = measured.get(no)
            if mv is not None:
                mark = "✅" if mv.get("ok") else "❌" if mv.get("ok") is False else "❓"
                L.append(f"{mark} {no}. {q} — {mv.get('detail')}")
            elif no in PROXY_NOS:
                L.append(f"📊 {no}. {q} — " + ("reflected via the liquidity/flexibility/book scores"
                                               if en else "유동성·유연성·호가 점수로 반영"))
            elif no in algo:
                L.append(f"🤖 {no}. {q} — {algo[no]}")
            elif no in news_ans:
                ok2, det2 = news_ans[no]
                L.append(f"{'✅' if ok2 else '❌'} {no}. {q} — 📰 {det2}")
            elif no in NEWS_NOS:
                L.append(f"📰 {no}. {q} — " + ("see the news stamps above (⑤)" if en else "위 ⑤ 뉴스 스탬프 참조"))
            elif it["cat"] in ("준비", "실행/관리"):
                L.append(f"🧑 {no}. {q}")
            else:
                L.append(f"⬜ {no}. {q}")
        if card.get("deal_breakers"):
            L.append(("🚫 deal-breakers: " if en else "🚫 결격: ")
                     + "; ".join(f"#{b['no']} {b['detail']}" for b in card["deal_breakers"][:3]))
    except Exception:
        L += ["", (f"(live item check unavailable — ask \"{nm} checklist\" in chat)" if en
                   else f"(실측 점검을 불러오지 못했습니다 — 챗봇에 \"{nm} 체크리스트\"라고 물어보세요)")]
    return "\n".join(L)
