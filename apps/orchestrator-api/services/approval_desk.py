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
        scores = {str(r.get("code")): r.get("score") for r in rows}
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
    """The room's 'what the agent is doing' — REAL numbers, easy words."""
    steps = []
    try:
        from services.checklist_reco import _ranking, _year_zone
        rows = (_ranking() or {}).get("rows") or []
        me = next((r for r in rows if str(r.get("code")) == code), None)
        if me:
            rank = rows.index(me) + 1
            steps.append({"icon": "📋", "t": "100 체크리스트 채점",
                          "d": f"오늘 점수 {me.get('score')}점 · 전체 {len(rows)}종목 중 {rank}등"})
        else:
            steps.append({"icon": "📋", "t": "100 체크리스트 채점", "d": "오늘 점수 집계 중"})
        z = _year_zone(code)
        if z:
            zk = {"buy": "매수구간 (바닥권)", "sell": "매도구간 (고점권)", "mid": "중간 구간"}[z["zone"]]
            steps.append({"icon": "📈", "t": "1년 역사 데이터 확인",
                          "d": f"현재가는 1년 최저~최고의 {z['pos']}% 지점 → {zk}"})
    except Exception:
        pass
    try:
        from services.chat_trade import _book_offer, smart_price
        from services.paper_desk import fast_price
        px, _c, _t, _s = fast_price(code)
        ob = _book_offer(code, "BUY")
        if ob and ob.get("wall_price"):
            steps.append({"icon": "🧱", "t": "키움 호가창 읽기",
                          "d": f"가장 큰 매수벽 ₩{ob['wall_price']:,.0f} ({ob.get('wall_qty', 0):,}주) — "
                               f"그 앞줄 제안가 ₩{ob['limit']:,.0f}"})
        elif ob:
            steps.append({"icon": "🧱", "t": "키움 호가창 읽기", "d": f"호가 제안가 ₩{ob['limit']:,.0f}"})
        if px:
            sp = smart_price(code, float(px))
            steps.append({"icon": "💡", "t": "효율 가격 계산",
                          "d": f"현재가 ₩{float(px):,.0f} · 오늘 흐름 기준 추천 진입가 ₩{sp:,.0f}"})
    except Exception:
        pass
    r, tv = _vol_ratio(code)
    if r is not None:
        steps.append({"icon": "📊", "t": "거래량 비교",
                      "d": f"오늘 {int(tv):,}주 = 최근 20일 평균의 {r:.1f}배"
                           + (" — 활발" if r >= 1.2 else " — 평소 수준" if r >= 0.8 else " — 한산")})
    try:
        from services.checklist_advice import _fresh_stamps
        stmps = _fresh_stamps(code, limit=2)
        if stmps:
            s0 = stmps[-1]
            steps.append({"icon": "📰", "t": "뉴스 스탬프",
                          "d": f"[{s0.get('stamp')}] {str(s0.get('title'))[:46]}"})
        else:
            steps.append({"icon": "📰", "t": "뉴스 스탬프", "d": "최근 특이 뉴스 없음"})
    except Exception:
        pass
    return steps


def _mk_sug(st, code, name, side, reasons, price, qty, score):
    st["seq"] = int(st.get("seq") or 0) + 1
    sug = {"id": st["seq"], "ts": time.time(), "hhmm": _hhmm(), "code": code,
           "name": name, "side": side, "reasons": reasons,
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
            # ---- SELL watch on OUR held lots ----
            lot = next((h for h in st["held"] if h["code"] == code), None)
            if lot and ("SELL", code) not in pending_codes \
                    and time.time() - st["cool"].get(f"SELL:{code}", 0) > _SELL_COOLDOWN:
                entry = float(lot["price"])
                pnl = (px / entry - 1) * 100
                reasons = []
                if pnl <= -1.0:
                    reasons.append(f"매수가 ₩{entry:,.0f} 대비 {pnl:.2f}% 하락 — -1% 손절 법칙")
                try:
                    from services.checklist_reco import _year_zone
                    z = _year_zone(code)
                    if z and z["zone"] == "sell":
                        reasons.append(f"1년 범위의 {z['pos']}% 지점 — 매도구간(고점권)")
                except Exception:
                    pass
                try:
                    from services.checklist_advice import _candles
                    cn = _candles(db, code)
                    if (cn.get("blues") or 0) >= 3 and pnl > 0:
                        reasons.append(f"상승 후 파란 캔들 {cn['blues']}개 연속 — 고점 지나 하락 시작")
                except Exception:
                    pass
                if reasons:
                    _mk_sug(st, code, name, "SELL", reasons, px, lot["qty"], score)
            # ---- BUY scan (not already held here, no pending) ----
            if lot or ("BUY", code) in pending_codes:
                continue
            if time.time() - st["cool"].get(f"BUY:{code}", 0) < _BUY_COOLDOWN:
                continue
            if score is None or float(score) < 55:
                continue
            from services.checklist_reco import _year_zone
            z = _year_zone(code)
            if not z or z["zone"] == "sell":
                continue
            try:
                from services.checklist_advice import _fresh_stamps
                if any(str(s.get("stamp")) in ("위험", "악재")
                       for s in _fresh_stamps(code, limit=2)):
                    continue
            except Exception:
                pass
            reasons = [f"100 체크리스트 {score}점 — 기준(55점) 통과"]
            zk = "매수구간 (1년 중 바닥권)" if z["zone"] == "buy" else f"1년 범위의 {z['pos']}% 지점 (과열 아님)"
            reasons.append(f"역사 데이터 확인: {zk}")
            r, tv = _vol_ratio(code)
            if r is not None and r >= 1.2:
                reasons.append(f"거래량 평소의 {r:.1f}배 — 관심 몰림 (실측)")
            from services.chat_trade import advise_qty, smart_price
            sp = smart_price(code, px)
            qty = advise_qty(px)
            reasons.append(f"제안: ₩{sp:,.0f}에 {qty:,}주 (예산 ₩{10_000_000:,} 기준)")
            _mk_sug(st, code, name, "BUY", reasons, sp, qty, score)
        except Exception as e:
            log.warning(f"approval scan {code}: {str(e)[:80]}")
    _save(st)
    return st


def decide(db, sid: int, ok: bool) -> dict:
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
    from services.paper_desk import place_order
    res = place_order(db, p["code"], p["side"], int(p["qty"]), order_type="market",
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
