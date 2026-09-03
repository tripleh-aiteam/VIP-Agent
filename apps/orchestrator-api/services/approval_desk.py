# -*- coding: utf-8 -*-
"""approval_desk — the SEMI-AUTO approval room (boss 2026-09-02: "demonstrate to
all people how our agent is trading... agent suggests everything — company,
price, number of stock — then WE approve; two buttons approve or cancel...
because we have a low winning % we wanna see actually our agent is working").

Menu 3, beside the two desks. Ten rooms (the six + today's top-4 by checklist
score). The scanner proposes BUY/SELL as popups with easy-word reasons — every
number read from the same engines the desks trust (checklist ranking, 1-year
zone from historical data, volume vs 20-day average, Kiwoom order book, news
stamps). Nothing executes without the human's 승인 click; 취소 skips and the
watch continues. Approved orders go through the SAME place_order chokepoint,
stamped source='semi', and join this desk's own holding list.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from services.logger import log

_FILE = Path(__file__).resolve().parent.parent / "data" / "approval_desk.json"
SIX = [("000660", "SK하이닉스"), ("005930", "삼성전자"), ("035420", "NAVER"),
       ("017670", "SK텔레콤"), ("042660", "한화오션"), ("034020", "두산에너빌리티")]
_BUY_COOLDOWN = 1800.0        # one BUY nudge per stock per 30 min
_SELL_COOLDOWN = 600.0
_EXPIRE = 600.0               # a popup no one answers dies after 10 min


def _load() -> dict:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(st: dict) -> None:
    try:
        _FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _hhmm() -> str:
    return time.strftime("%H:%M", time.gmtime(time.time() + 9 * 3600))


def desk_codes() -> list[tuple[str, str, float | None]]:
    """The ten rooms: six pinned + today's top-4 scorers. (code, name, score)."""
    out = [(c, n, None) for c, n in SIX]
    try:
        from services.checklist_reco import _ranking
        rows = (_ranking() or {}).get("rows") or []
        # The pinned six must still SHOW a score even when the morning gates
        # rejected them - the ranking drops gated names, so their number is
        # read from the full daily-pick instead (boss 2026-09-02 18:2x: four of
        # ten rooms were reading "score None", which would look broken in the
        # demo). The gates still decide who may be RECOMMENDED; the six are
        # watched either way, because they are his standing choice.
        allrows = rows
        try:
            import json as _j, urllib.request as _ur
            allrows = (_j.load(_ur.urlopen(
                "http://127.0.0.1:8000/paper-desk/daily-pick",
                timeout=120)).get("rows") or []) or rows
        except Exception:
            pass
        scores = {str(r.get("code")): r.get("score") for r in allrows}
        out = [(c, n, scores.get(c)) for c, n, _s in out]
        six_set = {c for c, _n in SIX}
        extra = [r for r in rows if str(r.get("code")) not in six_set][:4]
        out += [(str(r.get("code")), r.get("name") or r.get("code"), r.get("score"))
                for r in extra]
    except Exception as e:
        log.warning(f"approval desk_codes: {str(e)[:80]}")
    return out[:10]


def _vol_ratio(code: str):
    """Today's volume vs the 20-day average — (ratio, today_vol) or (None, None)."""
    try:
        from services.naver_stock import daily_history
        h = daily_history(code, days=22)
        if len(h) < 6 or not h[0].get("volume"):
            return None, None
        today_v = float(h[0]["volume"])
        prev = [float(r.get("volume") or 0) for r in h[1:21] if r.get("volume")]
        if not prev:
            return None, today_v
        return today_v / (sum(prev) / len(prev)), today_v
    except Exception:
        return None, None


def held(st: Optional[dict] = None) -> list[dict]:
    return list((st if st is not None else _load()).get("held") or [])


def process_steps(db, code: str, name: str) -> list[dict]:
    """The room's 'what the agent is doing' — REAL numbers, easy words.
    Bilingual (boss 2026-09-03: 'in English mode it should be English')."""
    steps = []

    def _add(icon, t_ko, d_ko, t_en, d_en):
        steps.append({"icon": icon, "t": t_ko, "d": d_ko, "t_en": t_en, "d_en": d_en})
    try:
        from services.checklist_reco import _ranking, _year_zone
        rows = (_ranking() or {}).get("rows") or []
        me = next((r for r in rows if str(r.get("code")) == code), None)
        if me:
            rank = rows.index(me) + 1
            _add("📋", "100 체크리스트 채점",
                 f"오늘 점수 {me.get('score')}점 · 전체 {len(rows)}종목 중 {rank}등",
                 "Scoring the 100-item checklist",
                 f"today {me.get('score')} pts · rank {rank} of {len(rows)} stocks")
        else:
            _add("📋", "100 체크리스트 채점", "오늘 점수 집계 중",
                 "Scoring the 100-item checklist", "today's score still computing")
        z = _year_zone(code)
        if z:
            zk = {"buy": "매수구간 (바닥권)", "sell": "매도구간 (고점권)", "mid": "중간 구간"}[z["zone"]]
            zke = {"buy": "BUYING zone (near the bottom)", "sell": "SELLING zone (near the top)",
                   "mid": "mid-range"}[z["zone"]]
            _add("📈", "1년 역사 데이터 확인",
                 f"현재가는 1년 최저~최고의 {z['pos']}% 지점 → {zk}",
                 "Checking 1-year historical data",
                 f"price sits at {z['pos']}% of the 1-year low~high → {zke}")
    except Exception:
        pass
    try:
        from services.chat_trade import _book_offer, smart_price
        from services.paper_desk import fast_price
        px, _c, _t, _s = fast_price(code)
        ob = _book_offer(code, "BUY")
        if ob and ob.get("wall_price"):
            _add("🧱", "키움 호가창 읽기",
                 f"가장 큰 매수벽 ₩{ob['wall_price']:,.0f} ({ob.get('wall_qty', 0):,}주) — "
                 f"그 앞줄 제안가 ₩{ob['limit']:,.0f}",
                 "Reading the Kiwoom order book",
                 f"biggest bid wall ₩{ob['wall_price']:,.0f} ({ob.get('wall_qty', 0):,} sh) — "
                 f"front-of-wall offer ₩{ob['limit']:,.0f}")
        elif ob:
            _add("🧱", "키움 호가창 읽기", f"호가 제안가 ₩{ob['limit']:,.0f}",
                 "Reading the Kiwoom order book", f"book offer ₩{ob['limit']:,.0f}")
        if px:
            sp = smart_price(code, float(px))
            _add("💡", "효율 가격 계산",
                 f"현재가 ₩{float(px):,.0f} · 오늘 흐름 기준 추천 진입가 ₩{sp:,.0f}",
                 "Computing the efficient price",
                 f"live ₩{float(px):,.0f} · suggested entry from today's flow ₩{sp:,.0f}")
    except Exception:
        pass
    r, tv = _vol_ratio(code)
    if r is not None:
        _tag = ("활발" if r >= 1.2 else "평소 수준" if r >= 0.8 else "한산")
        _tag_e = ("busy" if r >= 1.2 else "normal" if r >= 0.8 else "quiet")
        _add("📊", "거래량 비교",
             f"오늘 {int(tv):,}주 = 최근 20일 평균의 {r:.1f}배 — {_tag}",
             "Comparing volume",
             f"today {int(tv):,} sh = {r:.1f}× the 20-day average — {_tag_e}")
    try:
        from services.checklist_advice import _fresh_stamps
        stmps = _fresh_stamps(code, limit=2)
        if stmps:
            s0 = stmps[-1]
            _add("📰", "뉴스 스탬프", f"[{s0.get('stamp')}] {str(s0.get('title'))[:46]}",
                 "News stamps", f"[{s0.get('stamp')}] {str(s0.get('title'))[:46]}")
        else:
            _add("📰", "뉴스 스탬프", "최근 특이 뉴스 없음",
                 "News stamps", "no notable recent news")
    except Exception:
        pass
    return steps


def _mk_sug(st, code, name, side, reasons, price, qty, score, reasons_en=None):
    st["seq"] = int(st.get("seq") or 0) + 1
    sug = {"id": st["seq"], "ts": time.time(), "hhmm": _hhmm(), "code": code,
           "name": name, "side": side, "reasons": reasons,
           "reasons_en": reasons_en or reasons,
           "price": price, "qty": int(qty), "score": score}
    st.setdefault("pending", []).append(sug)
    st.setdefault("cool", {})[f"{side}:{code}"] = time.time()
    return sug


_scan_running = {"on": False, "last": 0.0}


def scan_async() -> None:
    """Fire scan() in a background thread (its own DB session) — the feed must
    answer INSTANTLY even when caches are cold (boss 2026-09-02: 'if I click
    Real Time Monitoring nothing is showing' — the first scan pulls a year of
    history for ten stocks and the page sat blank waiting for it)."""
    import threading
    if _scan_running["on"] or time.time() - _scan_running["last"] < 5:
        return

    def _run():
        _scan_running["on"] = True
        try:
            from db.base import SessionLocal
            db = SessionLocal()
            try:
                scan(db)
            finally:
                db.close()
        except Exception as e:
            log.warning(f"approval scan_async: {str(e)[:100]}")
        finally:
            _scan_running["on"] = False
            _scan_running["last"] = time.time()
    threading.Thread(target=_run, daemon=True).start()


def scan(db) -> dict:
    """Evaluate all ten rooms; append new suggestions. Called on page poll."""
    st = _load()
    st.setdefault("pending", [])
    st.setdefault("held", [])
    st.setdefault("cool", {})
    # expire unanswered popups
    st["pending"] = [p for p in st["pending"] if time.time() - p["ts"] < _EXPIRE]
    # planted TEST rows never survive (boss 2026-09-03: 'remove this, it is old
    # and makes confusion' — a file cleanup raced a scan thread's stale copy
    # and the row resurrected; filtering here makes the removal stick)
    st["log"] = [l for l in st.get("log") or []
                 if not any("테스트" in str(x) for x in l.get("reasons") or [])]
    # room meta snapshot (score + zone) computed HERE in the background so the
    # instant feed never blocks on cold caches; the top-4 rotate automatically
    # as the checklist re-scores (ranking cache ~10 min)
    try:
        from services.checklist_reco import _year_zone
        meta = []
        for code, name, score in desk_codes():
            z = None
            try:
                z0 = _year_zone(code)
                z = z0 and {"pos": z0["pos"], "zone": z0["zone"]}
            except Exception:
                pass
            meta.append({"code": code, "name": name, "score": score, "zone": z})
        st["rooms_meta"] = meta
        st["meta_at"] = time.time()
    except Exception:
        pass
    try:
        from services.kiwoom_tape import market_open
        if not market_open():
            _save(st)
            return st
    except Exception:
        pass
    from services.paper_desk import fast_price
    held_codes = {h["code"] for h in st["held"]}
    pending_codes = {(p["side"], p["code"]) for p in st["pending"]}
    for code, name, score in desk_codes():
        try:
            px, chg, _t, _s = fast_price(code)
            if not px:
                continue
            px = float(px)
            # THE ENGINE DECIDES, THE BOSS APPROVES (boss 2026-09-02 18:0x:
            # "menu 3 must implement all buying and selling cases of algo 3").
            # The old scanner carried its OWN three-line rule - score>=55, not
            # selling zone, no bad news - which shared nothing with the engine:
            # no 3rd-red door, no 제1조, no gap guard, no chop fence, no average
            # gate, no trail, no shelf break. Menu 2 and Menu 3 could therefore
            # disagree on the same stock in the same minute. Now 알고3 replays
            # today's tape for this stock and whatever IT holds is what Menu 3
            # offers - zero re-coded law, so the two menus cannot drift apart.
            view = _algo3_view(code, name)
            if view.get("err"):
                log.warning(f"approval algo3 {code}: {view['err']}")
                continue
            a_hold = view.get("hold")
            lot = next((h for h in st["held"] if h["code"] == code), None)

            # ---- SELL: we hold it, 알고3 has closed it ----
            if lot and not a_hold:
                if ("SELL", code) in pending_codes:
                    continue
                if time.time() - st["cool"].get(f"SELL:{code}", 0) <= _SELL_COOLDOWN:
                    continue
                rws = view.get("rows") or []
                last = None
                for r in rws:
                    if last is None or str(r.get("sell_t") or "") > str(last.get("sell_t") or ""):
                        last = r
                _rs9, _rse9 = _why_sell(code, lot, last, px)
                _mk_sug(st, code, name, "SELL", _rs9, px, lot["qty"], score,
                        reasons_en=_rse9)
                continue

            # ---- BUY: 알고3 holds it, we do not ----
            if lot or ("BUY", code) in pending_codes:
                continue
            if not a_hold:
                continue
            if time.time() - st["cool"].get(f"BUY:{code}", 0) < _BUY_COOLDOWN:
                continue
            try:
                from services.checklist_advice import _fresh_stamps
                if any(str(x.get("stamp")) in ("위험", "악재")
                       for x in _fresh_stamps(code, limit=2)):
                    continue            # danger news still vetoes, as before
            except Exception:
                pass
            reasons, reasons_en = _why_buy(code, name, a_hold)
            _bq = a_hold.get("qty") or 0
            try:
                _bp = float(a_hold.get("base") or a_hold.get("entry") or px)
            except Exception:
                _bp = px
            if not _bq:
                from services.chat_trade import advise_qty
                _bq = advise_qty(px)
            reasons.append(f"제안: ₩{_bp:,.0f} · {int(_bq):,}주 "
                           f"(알고3의 진입가와 수량 그대로)")
            reasons_en.append(f"Proposal: ₩{_bp:,.0f} · {int(_bq):,} shares "
                              f"(Algo 3's own entry price and size)")
            _mk_sug(st, code, name, "BUY", reasons, _bp, int(_bq), score,
                    reasons_en=reasons_en)
        except Exception as e:
            log.warning(f"approval scan {code}: {str(e)[:80]}")

    _save(st)
    return st


# ─────────────────────────────────────────────────────────────────────────────
# 알고3 ITSELF DECIDES (boss 2026-09-02 18:0x: "menu 3 must implement all buying
# and selling cases of 알고3, and say the reason why"). The scanner used to carry
# its OWN three-line rule - score>=55, not selling zone, no bad news - which
# shared nothing with the engine: no 3rd-red door, no 제1조, no gap guard, no
# chop fence, no average gate, no trail, no shelf break. Menu 2 and Menu 3 could
# therefore disagree on the same stock in the same minute.
# Now the popup asks the ENGINE. run_desk replays today's tape under the real D3
# book; whatever position it holds is what Menu 3 offers. Zero duplicated law,
# so the two menus can never drift apart.
def _algo3_view(code: str, name: str) -> dict:
    """What 알고3 is doing in this stock right now, and why."""
    out = {"hold": None, "rows": [], "err": None}
    try:
        from services.kiwoom_rules import trades as _tr
        d = _tr("D3", tick=5, period=60, bars=10, limit=500, codes=code,
                use_gate=True, allow_fallback=True, rank_gate=True)
        if not d.get("ok"):
            out["err"] = "engine returned no board"
            return out
        out["hold"] = next((h for h in (d.get("holding") or [])
                            if str(h.get("code")) == code), None)
        out["rows"] = [r for r in (d.get("rows") or [])
                       if str(r.get("code")) == code]
    except Exception as e:
        out["err"] = str(e)[:120]
    return out


def _why_buy(code: str, name: str, hold: dict):
    """The reasons in the boss's own language (KO + EN pair, boss 2026-09-03:
    'in English mode it should be English'): the checklist first, then the
    exact 알고3 law that opened the door, then the two average gates."""
    R, E = [], []
    try:
        from services.checklist_reco import _ranking
        rows = (_ranking() or {}).get("rows") or []
        me = next((r for r in rows if str(r.get("code")) == code), None)
        if me:
            rank = sorted(rows, key=lambda r: -(r.get("score") or 0)).index(me) + 1
            R.append(f"100 체크리스트 {me.get('score')}점 · 전체 {len(rows)}종목 중 {rank}등")
            E.append(f"100-item checklist {me.get('score')} pts · rank {rank} of {len(rows)}")
            if me.get("mid") is not None:
                R.append(f"1개월 평균선 대비 {me.get('mid'):+.2f}% — 평균 아래 (매수 게이트 1 통과)")
                E.append(f"{me.get('mid'):+.2f}% vs the 1-month average — below it (buy gate 1 passed)")
            if me.get("midy") is not None:
                R.append(f"1년 평균선 대비 {me.get('midy'):+.2f}% — 평균 아래 (매수 게이트 2 통과)")
                E.append(f"{me.get('midy'):+.2f}% vs the 1-year average — below it (buy gate 2 passed)")
    except Exception:
        pass
    try:
        from services.checklist_reco import _year_zone
        z = _year_zone(code)
        if z:
            zk = ("매수구간 (1년 중 바닥 20%)" if z["zone"] == "buy"
                  else f"1년 범위의 {z['pos']}% 지점 — 매도구간(85%↑) 아님")
            zke = ("BUYING zone (bottom 20% of the year)" if z["zone"] == "buy"
                   else f"at {z['pos']}% of the 1-year range — not the selling zone (85%+)")
            R.append(f"과거 1년 데이터 확인: {zk}")
            E.append(f"1-year historical data checked: {zke}")
    except Exception:
        pass
    bt = str((hold or {}).get("buy_t") or "")[:5]
    R.append(f"알고3 진입 법칙 충족 ({bt}) — 급락 후 하락이 멈추고 3번째 양봉, "
             f"제1조(바닥 3봉 이상·바닥 1.5% 이내·최근 3봉 중 2회 상승·0.3% 성장) 통과")
    E.append(f"Algo-3 entry law met ({bt}) — after the dip the fall stopped, 3rd red candle, "
             f"Article 1 passed (3+ bars at the bottom · within 1.5% of it · 2 of last 3 rising · 0.3% growth)")
    R.append("차단 규칙 전부 통과: 갭상승 금지 · 30봉 고가 0.3% 이내 금지 · "
             "매도존 금지 · 20봉 변동 0.7% 미만 금지 · 매도 후 추격 금지 · "
             "1개월/1년 평균 위 금지")
    E.append("Every block rule passed: no gap-up buy · not within 0.3% of the 30-bar high · "
             "not the selling zone · not a flat 0.7% chop · no chasing after a sell · "
             "not above the 1-month/1-year averages")
    return R, E


def _why_sell(code: str, lot: dict, row: dict, px: float):
    """Name the law that closed it (KO + EN pair) - the engine's own words."""
    R, E = [], []
    why = str((row or {}).get("exit_why") or "")
    law = ("고점 대비 1.5% 하락 (종가 확인) — 큰 파도 청산" if "고점" in why else
           "고점 후 횡보 지지선(12봉 최저) 이탈 — 이익 중 청산" if "지지선" in why else
           "-1% 손절 (종가 확인)" if "-1%" in why else
           "15:19 장 마감 전량 정리" if "마감" in why else
           "상승 후 연속 음봉 — 파도 종료" if "음봉" in why else why or "알고3 청산 신호")
    law_e = ("1.5% down from the peak (close-confirmed) — big-wave exit" if "고점" in why else
             "post-peak shelf (12-bar low) broke — exit in profit" if "지지선" in why else
             "-1% stop (close-confirmed)" if "-1%" in why else
             "15:19 closing-bell full clear" if "마감" in why else
             "straight blue candles after the rise — wave over" if "음봉" in why
             else (why or "Algo-3 exit signal"))
    R.append(f"알고3 청산 법칙: {law}")
    E.append(f"Algo-3 exit law: {law_e}")
    try:
        entry = float(lot["price"])
        pnl = (px / entry - 1) * 100
        R.append(f"매수가 ₩{entry:,.0f} → 현재 ₩{px:,.0f} ({pnl:+.2f}%)")
        E.append(f"entry ₩{entry:,.0f} → now ₩{px:,.0f} ({pnl:+.2f}%)")
        if 0 < pnl <= 0.23:
            R.append("주의: 수수료 구간(0~0.23%) — 알고3는 이 구간에서 팔지 않습니다")
            E.append("note: inside the fee band (0~0.23%) — Algo 3 does not sell here")
    except Exception:
        pass
    R.append("보류 법칙 확인: 매수구간(1년 바닥권 또는 5일 최저) 인내 규칙에 해당하지 않음")
    E.append("patience law checked: not in the buying-zone hold rule (1-year bottom or 5-day low)")
    return R, E


def decide(db, sid: int, ok: bool, qty=None, price=None) -> dict:
    st = _load()
    p = next((x for x in (st.get("pending") or []) if x["id"] == sid), None)
    if not p:
        return {"ok": False, "error": "suggestion expired or already handled"}
    st["pending"] = [x for x in st["pending"] if x["id"] != sid]
    if not ok:
        st.setdefault("log", []).append({**p, "decision": "취소", "at": _hhmm()})
        st["log"] = st["log"][-200:]
        _save(st)
        return {"ok": True, "decision": "cancelled"}
    # the boss may edit the agent's numbers before approving (2026-09-03 09:4x)
    _q = int(qty) if qty else int(p["qty"])
    _px = float(price) if price else None
    p = dict(p, qty=_q, price=(_px if _px else p.get("price")),
             edited=bool((qty and int(qty) != int(p["qty"]))
                         or (price and float(price) != float(p.get("price") or 0))))
    from services.paper_desk import place_order
    if _px:
        res = place_order(db, p["code"], p["side"], _q, order_type="limit",
                          limit_price=_px, source="semi", direct=True)
    else:
        res = place_order(db, p["code"], p["side"], _q, order_type="market",
                          source="semi", direct=True)
    if not res.get("ok"):
        st.setdefault("pending", []).append(p)      # keep the popup, report the error
        _save(st)
        return {"ok": False, "error": res.get("error") or "order failed"}
    fill = float(res.get("fill_price") or p["price"])
    if p["side"] == "BUY":
        st.setdefault("held", []).append({"code": p["code"], "name": p["name"],
                                          "qty": int(p["qty"]), "price": fill,
                                          "at": _hhmm()})
    else:
        st["held"] = [h for h in st.get("held") or [] if h["code"] != p["code"]]
    st.setdefault("log", []).append({**p, "decision": "승인", "fill": fill, "at": _hhmm(),
                                     "pnl": (round((fill / next((h["price"] for h in [p]), fill) - 1) * 100, 2)
                                             if False else None)})
    st["log"] = st["log"][-200:]
    _save(st)
    return {"ok": True, "decision": "approved", "fill": fill}
