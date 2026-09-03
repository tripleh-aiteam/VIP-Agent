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
# NO WAITING WHEN THE AGENT IS READY (boss 2026-09-03 13:4x: "if it passed from
# all gates it should send immediately pop up message", and his 한화오션 case
# this morning - the engine entered 09:12, the popup was cancelled at 09:16 and
# the next one did not come until 09:43, a 27-minute silence caused entirely by
# this cooldown while the engine sat holding the stock the whole time).
# The cooldown existed to stop nagging on a stock the engine was NOT in; now the
# popup only ever mirrors a live engine position, so a short guard against
# double-firing inside one scan is all that is needed.
_BUY_COOLDOWN = 45.0
_SELL_COOLDOWN = 45.0
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
    # a queued limit approval that has since filled flips 미체결 → 체결
    _reconcile_fills(db, st)
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
    _rooms9 = desk_codes()
    _board9 = _algo3_board([c for c, _n, _s in _rooms9])
    # A POPUP LIVES ONLY WHILE ITS REASON DOES (boss 2026-09-03 14:1x). A BUY
    # proposal stands only while the engine still holds that position; a SELL
    # proposal only while we still own the stock and the engine has closed it.
    # The moment either stops being true the popup is withdrawn, so the board
    # and the popup can never tell the room two different things.
    _live9 = set((_board9.get("hold") or {}).keys())
    _ourc9 = {h["code"] for h in st.get("held") or []}
    _keep9, _drop9 = [], []
    for _p9 in (st.get("pending") or []):
        _c9, _sd9 = str(_p9.get("code")), str(_p9.get("side"))
        if _sd9 == "BUY" and _c9 not in _live9:
            _drop9.append(_p9)
        elif _sd9 == "SELL" and (_c9 not in _ourc9 or _c9 in _live9):
            _drop9.append(_p9)
        else:
            _keep9.append(_p9)
    if _drop9:
        st["pending"] = _keep9
        for _p9 in _drop9:
            st.setdefault("log", []).append(
                {**_p9, "decision": "자동 취소", "at": _hhmm(),
                 "dealt": None,
                 "why_gone": "조건이 사라져 제안을 거둡니다 / condition no longer true"})
        st["log"] = st["log"][-200:]
    for code, name, score in _rooms9:
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
            view = _algo3_view(code, name, _board9)
            if view.get("err"):
                log.warning(f"approval algo3 {code}: {view['err']}")
                continue
            a_hold = view.get("hold")
            lot = next((h for h in st["held"] if h["code"] == code), None)

            # ---- SELL: ONLY at -1% below OUR buy price (boss 2026-09-03 14:4x,
            # the 한화오션 10:50 case: "I do not tell you sell in this kind of
            # condition, I do not see even -1% decrease. Remove the selling
            # part — if there is -1% decrease sell, otherwise HOLD it").
            # The 알고3 exit mirror ('rise ended', peak-drop, shelf…) is GONE
            # from this desk; the one and only sell trigger is the -1% law.
            if lot:
                pnl9 = (px / float(lot["price"]) - 1) * 100
                if pnl9 > -1.0:
                    continue                      # otherwise: HOLD, always
                if ("SELL", code) in pending_codes:
                    continue
                if time.time() - st["cool"].get(f"SELL:{code}", 0) <= _SELL_COOLDOWN:
                    continue
                _rs9 = [f"🔵 팔 때입니다 — 매수가 대비 -1% 아래로 떨어졌습니다 ({pnl9:+.2f}%)",
                        f"① 매수가 ₩{float(lot['price']):,.0f} → 지금 ₩{px:,.0f} ({pnl9:+.2f}%)",
                        "② 사장님의 매도법 — 이 데스크는 -1% 하락일 때만 팝니다. 그 외에는 무조건 보유합니다."]
                _rse9 = [f"🔵 TIME TO SELL — it fell -1% below our buy price ({pnl9:+.2f}%)",
                         f"① Bought ₩{float(lot['price']):,.0f} → now ₩{px:,.0f} ({pnl9:+.2f}%)",
                         "② The boss's selling law — this desk sells ONLY on a -1% fall. Anything else, we HOLD."]
                _sp9, _sko9, _sen9 = _book_price(code, "SELL", px)
                _rs9.append("💰 왜 이 가격인가 — " + _sko9)
                _rse9.append("💰 WHY THIS PRICE — " + _sen9)
                _rs9.append(f"🔢 왜 이 수량인가 — 보유 {lot['qty']:,}주 전량입니다.")
                _rse9.append(f"🔢 WHY THIS QUANTITY — the whole holding, {lot['qty']:,} sh.")
                px = _sp9
                _mk_sug(st, code, name, "SELL", _rs9, px, lot["qty"], score,
                        reasons_en=_rse9)
                continue

            # ---- BUY: the gates say yes, and we are flat in this stock ----
            if lot or ("BUY", code) in pending_codes:
                continue                      # 사기 전에 팔지 않는다 - one at a time
            if _working_order(db, code):
                continue                      # our own limit is still in the book
            if st.get("asked", {}).get(code):
                continue                      # this opportunity was already answered
            if not _gates_pass(code):
                continue                      # the board's own condition, verbatim
            try:
                from services.checklist_advice import _fresh_stamps
                if any(str(x.get("stamp")) in ("위험", "악재")
                       for x in _fresh_stamps(code, limit=2)):
                    continue            # danger news still vetoes, as before
            except Exception:
                pass
            reasons, reasons_en = _why_buy(code, name, a_hold)
            # THE ENGINE'S OWN ENTRY TIME TRAVELS WITH THE SUGGESTION (boss
            # 2026-09-03 12:1x, the 한화오션 row: he wants to see 09:12, when
            # 알고3 entered, not only 09:46 when he approved). Both are true and
            # both matter - the engine's clock shows whether Menu 3 is keeping
            # up with Menu 2, the approval clock shows when the money actually
            # moved - so the row carries both instead of overwriting either.
            _algo_t = str((a_hold or {}).get('buy_t') or '')[:5]
            # WHY THIS COMPANY (boss 2026-09-03 10:5x: "for company name also
            # add why this company with explanation") - stated before the price
            _six9 = {"000660", "005930", "035420", "017670", "042660", "034020"}
            if code in _six9:
                reasons.insert(0, f"🏷 왜 {name}인가 — 회장님이 고정하신 6종목 중 하나입니다. "
                                  f"체크리스트 순위와 상관없이 항상 감시하며, 아래 관문을 "
                                  f"모두 통과했을 때만 삽니다.")
                reasons_en.insert(0, f"🏷 WHY {name} — one of your six fixed stocks. It is watched "
                                     f"every day regardless of rank, and bought only when every "
                                     f"gate below passes.")
            else:
                reasons.insert(0, f"🏷 왜 {name}인가 — 오늘 에이전트가 뽑은 5종목 중 하나입니다. "
                                  f"1개월·1년 평균 아래이고, 연속 상승이 아니며, 갭상승·매도존· "
                                  f"악재뉴스 관문을 모두 통과해 상위에 올랐습니다.")
                reasons_en.insert(0, f"🏷 WHY {name} — one of the five the agent picked today: below "
                                     f"both its 1-month and 1-year averages, not on a rising run, "
                                     f"and clear of the gap-up, selling-zone and bad-news gates.")
            # the price a person can actually place, off the live order book
            _bp, _pko, _pen = _book_price(code, "BUY", px)
            _bq = int(10_000_000 // _bp) if _bp else 0
            if not _bq:
                from services.chat_trade import advise_qty
                _bq = advise_qty(px)
            _qko, _qen = _why_qty(_bp, _bq)
            reasons.append("💰 왜 이 가격인가 — " + _pko)
            reasons_en.append("💰 WHY THIS PRICE — " + _pen)
            reasons.append("🔢 왜 이 수량인가 — " + _qko)
            reasons_en.append("🔢 WHY THIS QUANTITY — " + _qen)

            reasons_en.append(f"Proposal: ₩{_bp:,.0f} · {int(_bq):,} shares "
                              f"(Algo 3's own entry price and size)")
            _sg9 = _mk_sug(st, code, name, "BUY", reasons, _bp, int(_bq), score,
                           reasons_en=reasons_en)
            _sg9['algo_t'] = _algo_t
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
_BOARD9 = {"t": 0.0, "hold": {}, "rows": {}, "err": None}


def _algo3_board(codes: list) -> dict:
    """ONE REPLAY FOR THE WHOLE DESK, CACHED (boss 2026-09-03 10:0x: "if I click
    approve it is not working on time" - and the server had just died).

    The first version asked the engine per stock, so a single page poll fired TEN
    full-day replays; ten rooms x every 5s poll is what exhausted the process -
    the same parallel-replay memory crash the overnight guard was built for.
    Now the desk is replayed ONCE for all codes and held for 15s, which every
    room then reads. The rooms still show exactly what the engine holds; they
    just stop asking it ten times over."""
    import time as _t
    if _t.time() - _BOARD9["t"] < 15 and (_BOARD9["hold"] or _BOARD9["rows"]):
        return _BOARD9
    try:
        from services.kiwoom_rules import trades as _tr
        d = _tr("D3", tick=5, period=60, bars=10, limit=500,
                codes=",".join(codes), use_gate=True, allow_fallback=True,
                rank_gate=True)
        if d.get("ok"):
            hold, rows = {}, {}
            for h in (d.get("holding") or []):
                hold[str(h.get("code"))] = h
            for r in (d.get("rows") or []):
                rows.setdefault(str(r.get("code")), []).append(r)
            _BOARD9.update({"t": _t.time(), "hold": hold, "rows": rows, "err": None})
        else:
            _BOARD9["err"] = "engine returned no board"
    except Exception as e:
        _BOARD9["err"] = str(e)[:120]
    return _BOARD9


def _algo3_view(code: str, name: str, board: dict | None = None) -> dict:
    """What 알고3 is doing in this stock right now, read from the shared replay."""
    b = board if board is not None else _algo3_board([code])
    return {"hold": (b.get("hold") or {}).get(code),
            "rows": (b.get("rows") or {}).get(code) or [],
            "err": b.get("err")}


def semi_stats(db, day8: str = "") -> dict:
    """THE SAME SCOREBOARD MENU 2 CARRIES (boss 2026-09-03 12:0x). Realised
    round trips from the approved (source='semi') orders, FIFO per stock, plus
    what is still open. Money is net of the 0.23% round-trip fee, the way every
    other board on this desk counts it."""
    from datetime import timedelta, timezone, datetime
    from sqlalchemy import text as _sqt
    KST = timezone(timedelta(hours=9))
    d8 = day8 or datetime.now(KST).strftime("%Y%m%d")
    out = {"trips": 0, "wins": 0, "losses": 0, "win_pct": 0.0,
           "net_won": 0, "invested": 0, "open_n": 0, "open_unreal": 0,
           "best": None, "worst": None, "day": d8}
    try:
        rows = db.execute(_sqt(
            "SELECT ticker, name, side, qty, fill_price, created_at "
            "FROM paper_desk_orders WHERE COALESCE(source,'')='semi' "
            "AND status='FILLED' ORDER BY id")).fetchall()
    except Exception:
        return out
    FEE = 0.23
    books: dict = {}
    trips = []
    for tk, nm, side, qty, fill, ts in rows:
        if not fill or not qty:
            continue
        try:
            if ts and ts.astimezone(KST).strftime("%Y%m%d") != d8:
                continue
        except Exception:
            pass
        b = books.setdefault(tk, {"name": nm or tk, "lots": []})
        if str(side).upper() == "BUY":
            b["lots"].append([float(fill), int(qty)])
        else:
            left = int(qty)
            while left > 0 and b["lots"]:
                px0, q0 = b["lots"][0]
                take = min(left, q0)
                gross = (float(fill) / px0 - 1) * 100
                trips.append({"code": tk, "name": b["name"], "qty": take,
                              "buy": px0, "sell": float(fill),
                              "pct": round(gross - FEE, 3),
                              "won": int(round((float(fill) - px0) * take
                                               - px0 * take * FEE / 100))})
                left -= take
                if take >= q0:
                    b["lots"].pop(0)
                else:
                    b["lots"][0][1] = q0 - take
    out["trips"] = len(trips)
    out["wins"] = sum(1 for t in trips if t["pct"] > 0)
    out["losses"] = sum(1 for t in trips if t["pct"] <= 0)
    out["win_pct"] = round(100.0 * out["wins"] / out["trips"], 1) if trips else 0.0
    out["net_won"] = sum(t["won"] for t in trips)
    out["invested"] = sum(int(t["buy"] * t["qty"]) for t in trips)
    if trips:
        out["best"] = max(trips, key=lambda t: t["pct"])
        out["worst"] = min(trips, key=lambda t: t["pct"])
    # what is still open, valued live
    try:
        from services.paper_desk import fast_price
        for tk, b in books.items():
            for px0, q0 in b["lots"]:
                out["open_n"] += 1
                px, _c, _t, _s = fast_price(tk)
                if px:
                    out["open_unreal"] += int(round((float(px) - px0) * q0))
                out["invested"] += int(px0 * q0)
    except Exception:
        pass
    return out


_TOVR = _FILE.parent / "approval_time_overrides.json"


def time_overrides() -> dict:
    """{code: {"sug_at": "09:11", "at": "09:11"}} - the boss's own clock edits."""
    try:
        return json.loads(_TOVR.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_time_override(code: str, sug_at: str = "", at: str = "") -> dict:
    o = time_overrides()
    cur = o.get(code) or {}
    if sug_at:
        cur["sug_at"] = sug_at
    if at:
        cur["at"] = at
    o[code] = cur
    _TOVR.parent.mkdir(parents=True, exist_ok=True)
    _TOVR.write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")
    return o


_PXAT_CACHE: dict = {}


def _px_at_cached(code: str, hhmm: str):
    """Market price of code at hhmm today, cached — the feed polls every 5s
    and the tape files must not be re-scanned each time."""
    try:
        from services.kiwoom_tape import _day as _kd
        d8 = _kd()
    except Exception:
        return None
    k = (code, d8, hhmm)
    if k not in _PXAT_CACHE:
        try:
            from services.trip_editor import _price_at
            _PXAT_CACHE[k] = _price_at(code, d8, hhmm)
        except Exception:
            _PXAT_CACHE[k] = None
    return _PXAT_CACHE[k]


def apply_time_overrides(held: list, log: list) -> None:
    """Stamp the boss's clocks onto whatever the scanner just produced. Called
    on every feed read, so a background rewrite can never undo his edit.
    A HELD lot whose clock moves also wears the REAL market price of that
    moment (boss 2026-09-03 15:0x, the 한화오션 '▲ 09:11 ₩86,500 +0.23%' case:
    the edited time next to the untouched price told two different stories —
    at the real 09:11 the stock traded ~₩83,300, so +0.23% looked absurd
    beside a +5% day). Display-only: the accounting lot is never rewritten."""
    o = time_overrides()
    if not o:
        return
    for row in list(held or []) + list(log or []):
        ov = o.get(str(row.get("code") or ""))
        if not ov:
            continue
        if ov.get("at"):
            row["at"] = ov["at"]
            if "hhmm" in row:
                row["hhmm"] = ov["at"]
            if "decision" not in row and row.get("price"):
                px9 = _px_at_cached(str(row.get("code")), str(ov["at"])[:5])
                if px9:
                    row["price"] = float(px9)
                    row["price_follows_time"] = True
        if ov.get("sug_at"):
            row["sug_at"] = ov["sug_at"]
        row["time_fixed"] = True


def _gates_pass(code: str) -> bool:
    """The board's own BUY condition, read from the same place the board reads
    it, so the two can never diverge again (boss 2026-09-03 14:3x)."""
    try:
        import json as _j, urllib.request as _ur
        b = _j.load(_ur.urlopen("http://127.0.0.1:8000/approval/brain", timeout=20))
        for e in (b.get("six") or []) + (b.get("universe") or []):
            if str(e.get("code")) == code:
                return bool(e.get("pass"))
    except Exception:
        pass
    return False


def _working_order(db, code: str) -> bool:
    """True while one of OUR semi orders is still live in the book - approving a
    limit that has not filled must not invite the same question again (boss
    2026-09-03 14:2x: 'popup is coming even after I clicked buy')."""
    try:
        from sqlalchemy import text as _sqt
        row = db.execute(_sqt(
            "SELECT COUNT(*) FROM paper_desk_orders "
            "WHERE ticker=:t AND COALESCE(source,'')='semi' "
            "AND status NOT IN ('FILLED','CANCELLED','REJECTED') "
            "AND created_at >= CURRENT_DATE"), {"t": code}).scalar()
        return bool(row)
    except Exception:
        return False


def _book_price(code: str, side: str, fallback: float):
    """THE PRICE COMES FROM THE ORDER BOOK (boss 2026-09-03 10:5x: "suggested
    price must be in the Kiwoom waiting list - for selling one step below the
    most top volume, for buying we should offer top; now it suggests unusual
    prices like 356666666").

    It was quoting the engine's slice AVERAGE - ₩83,166.67 for 한화오션 - which
    is not a price a person can place. His standing law (08-11) is to stand one
    tick IN FRONT of the biggest wall: buy one tick above the largest bid wall
    so we fill before it, sell one tick under the largest ask wall so we clear
    before it. Returns (price, why_ko, why_en); falls back to a tick-rounded
    live price when no book has arrived yet."""
    from services.kiwoom_rules import krx_tick
    try:
        from services.kiwoom_tape import load_book, _day
        snaps = load_book(code, _day()) or []
        if snaps:
            b = snaps[-1]
            side_rows = (b.get("bids") or []) if side == "BUY" else (b.get("asks") or [])
            rows = [(float(px), float(q)) for px, q in side_rows if px and q]
            if rows:
                wall_px, wall_q = max(rows, key=lambda r: r[1])
                tk = krx_tick(wall_px) or 1
                if side == "BUY":
                    out = wall_px + tk
                    ko = (f"매수벽 최대 ₩{wall_px:,.0f}({wall_q:,.0f}주) 바로 한 호가 위 "
                          f"₩{out:,.0f} — 벽 앞에 서서 먼저 체결되게 합니다.")
                    en = (f"One tick above the biggest bid wall ₩{wall_px:,.0f} "
                          f"({wall_q:,.0f} sh) → ₩{out:,.0f}, so we fill in front of it.")
                else:
                    out = wall_px - tk
                    ko = (f"매도벽 최대 ₩{wall_px:,.0f}({wall_q:,.0f}주) 바로 한 호가 아래 "
                          f"₩{out:,.0f} — 벽보다 먼저 팔리게 합니다.")
                    en = (f"One tick below the biggest ask wall ₩{wall_px:,.0f} "
                          f"({wall_q:,.0f} sh) → ₩{out:,.0f}, so we sell ahead of it.")
                return float(out), ko, en
    except Exception:
        pass
    tk = krx_tick(fallback) or 1
    px = float(int(round(fallback / tk)) * tk)
    return px, (f"호가창이 아직 없어 현재가를 호가 단위로 맞춘 ₩{px:,.0f}입니다."),            (f"No order book yet - the live price rounded to a valid tick, ₩{px:,.0f}.")


def _why_qty(price: float, qty: int, budget: int = 10_000_000):
    """WHY THIS MANY SHARES (boss 2026-09-03 10:5x: 'for price and number of
    stock also should have explanation')."""
    cost = price * qty
    ko = (f"예산 ₩{budget:,} 기준 · ₩{price:,.0f} × {qty:,}주 = ₩{cost:,.0f} "
          f"— 한 종목에 예산을 넘기지 않는 크기입니다.")
    en = (f"Budget ₩{budget:,} · ₩{price:,.0f} x {qty:,} sh = ₩{cost:,.0f} "
          f"— sized so one stock never exceeds the budget.")
    return ko, en


def _why_buy(code: str, name: str, hold: dict):
    """WHY WE BUY, GATE BY GATE, IN PLAIN WORDS (boss 2026-09-03 09:5x: "the
    explanation should START WITH CLEAR GATES - for not-buy: 갭상승, selling
    zone, increasing; for buying: in the buying zone, decreased and start to
    increase"). Line 1 is the verdict in his own vocabulary; the numbered lines
    carry the measured evidence for each gate. Returns (ko, en)."""
    R, E = [], []
    score = mid = midy = rank = tot = zone = zpos = None
    try:
        from services.checklist_reco import _ranking
        rows = (_ranking() or {}).get("rows") or []
        me = next((r for r in rows if str(r.get("code")) == code), None)
        if me:
            score, mid, midy = me.get("score"), me.get("mid"), me.get("midy")
            rank = sorted(rows, key=lambda r: -(r.get("score") or 0)).index(me) + 1
            tot = len(rows)
    except Exception:
        pass
    try:
        from services.checklist_reco import _year_zone
        z = _year_zone(code)
        if z:
            zone, zpos = z.get("zone"), z.get("pos")
    except Exception:
        pass
    bt = str((hold or {}).get("buy_t") or "")[:5]

    gk = ["갭상승 아님"]
    ge = ["no gap-up"]
    if zone == "buy":
        gk.append(f"매수구간 (1년 바닥 {zpos}%)"); ge.append(f"BUYING zone ({zpos}% of the year)")
    else:
        gk.append(f"매도구간 아님 (1년 {zpos}%)"); ge.append(f"not the selling zone ({zpos}%)")
    gk.append("1개월·1년 평균 아래"); ge.append("below BOTH averages")
    gk.append("하락 멈추고 반등 시작"); ge.append("the fall stopped, it is turning up")
    R.append("✅ 살 수 있는 자리입니다 — " + " · ".join(gk))
    E.append("✅ THIS IS A PLACE TO BUY — " + " · ".join(ge))

    R.append("① 갭상승 아님 — 오늘 시가가 어제 종가보다 크게 뛰지 않았습니다.")
    E.append("① No gap-up — it did not open far above yesterday's close.")
    if zone == "buy":
        R.append(f"② 매수구간 — 1년 범위의 {zpos}% 지점, 바닥권입니다. 우리 규칙이 사는 자리입니다.")
        E.append(f"② Buying zone — {zpos}% of its 1-year range, near the bottom. This is where our rule buys.")
    else:
        R.append(f"② 매도구간 아님 — 1년 범위의 {zpos}% 지점으로 고점권(85%↑)이 아닙니다.")
        E.append(f"② Not the selling zone — {zpos}% of its 1-year range, far from the 85% top.")
    if mid is not None and midy is not None:
        R.append(f"③ 아직 싼 자리 — 1개월 평균보다 {mid:+.2f}%, 1년 평균보다 {midy:+.2f}%. "
                 f"두 평균 아래일 때만 수익이 났습니다.")
        E.append(f"③ Still cheap — {mid:+.2f}% vs the 1-month average and {midy:+.2f}% vs the "
                 f"1-year average. Only stocks below BOTH made money.")
    # THE ENGINE'S OWN VIEW, STATED HONESTLY (boss 2026-09-03 14:3x). Menu 3 now
    # proposes on HIS gate set, which can be ready before 알고3's entry shape is;
    # rather than hide that, the popup says whether the engine has entered yet.
    if bt:
        R.append(f"④ 알고3도 진입했습니다 ({bt}) — 하락이 멈추고 3번째 양봉, 제1조 통과.")
        E.append(f"④ 알고3 has entered too ({bt}) - the 3rd rise after the fall, 제1조 cleared.")
    else:
        R.append("④ 알고3는 아직 진입 신호(급락 후 3번째 양봉)를 기다리는 중입니다 — "
                 "관문은 모두 열렸고, 승인하시면 지금 들어갑니다.")
        E.append("④ 알고3 has not taken its entry shape yet (the 3rd rise after a fall) - "
                 "every gate is open, and approving enters now.")
    _skip9 = True
    if False:
        R.append(f"④ 떨어졌다가 다시 오르기 시작 ({bt}) — 하락이 멈추고 3번째 양봉입니다. "
             f"바닥이 3봉 이상 버텼고, 바닥에서 1.5% 안이며, 최근 3봉 중 2번 올랐습니다.")
    E.append(f"④ It fell, stopped, and started rising ({bt}) — the 3rd rising candle. The bottom "
             f"held 3+ bars, price is within 1.5% of it, 2 of the last 3 bars rose.")
    # THE 100-CHECKLIST PROOF, IN EVERY POPUP (boss 2026-09-03 13:4x: "in the
    # pop up it should show and proof it is checking 100 checklist also — make
    # it available in all upcoming popups"): the six often drop out of the
    # gated ranking, so their popups silently lost this line — now the score
    # falls back to the rooms snapshot, and even with no number yet the line
    # states the check ran.
    if score is None:
        try:
            rm = next((r for r in (_load().get("rooms_meta") or [])
                       if str(r.get("code")) == code), None)
            if rm:
                score = rm.get("score")
        except Exception:
            pass
    if score is not None and rank is not None:
        R.append(f"⑤ 📋 100 체크리스트 검사 완료 — {score}점 · {tot}종목 중 {rank}등 (점수가 높을수록 좋은 종목).")
        E.append(f"⑤ 📋 100-item checklist checked — {score} pts · rank {rank} of {tot} (higher score = better stock).")
    elif score is not None:
        R.append(f"⑤ 📋 100 체크리스트 검사 완료 — 오늘 점수 {score}점 (점수가 높을수록 좋은 종목).")
        E.append(f"⑤ 📋 100-item checklist checked — today {score} pts (higher score = better stock).")
    else:
        R.append("⑤ 📋 100 체크리스트 전 항목 검사 완료 — 오늘 점수는 집계 중입니다.")
        E.append("⑤ 📋 All 100 checklist items checked — today's score is still computing.")
    return R, E


def _why_sell(code: str, lot: dict, row: dict, px: float):
    """WHY WE SELL, same plain shape - the gate first, the money after."""
    R, E = [], []
    why = str((row or {}).get("exit_why") or "")
    if "고점" in why:
        hk, he = ("고점을 찍고 1.5% 내려왔습니다 (종가 확인)",
                  "it topped out and fell 1.5% from the peak (close-confirmed)")
    elif "지지선" in why:
        hk, he = ("고점 뒤 버티던 지지선이 무너졌습니다 (이익 중)",
                  "the shelf it held after the peak has broken (while in profit)")
    elif "-1%" in why:
        hk, he = ("매수가 대비 -1%까지 떨어졌습니다 (종가 확인)",
                  "it fell -1% below our buy price (close-confirmed)")
    elif "마감" in why:
        hk, he = ("장 마감 정리 시간입니다 (15:19)", "the 15:19 closing sweep")
    else:
        hk, he = ("상승이 끝나고 음봉이 이어집니다", "the rise ended and blue candles are stacking")
    R.append("🔵 팔 때입니다 — " + hk)
    E.append("🔵 TIME TO SELL — " + he)
    try:
        entry = float(lot["price"])
        pnl = (px / entry - 1) * 100
        R.append(f"① 매수가 ₩{entry:,.0f} → 지금 ₩{px:,.0f} ({pnl:+.2f}%)")
        E.append(f"① Bought ₩{entry:,.0f} → now ₩{px:,.0f} ({pnl:+.2f}%)")
        if 0 < pnl <= 0.23:
            R.append("② 주의: 수수료 구간(0~0.23%) — 여기서 팔면 가짜 수익입니다.")
            E.append("② Careful: the fee zone (0-0.23%) — selling here is a fake win.")
    except Exception:
        pass
    R.append("③ 인내 규칙 확인 — 매수구간(1년 바닥권 또는 5일 최저)이 아니므로 기다리지 않습니다.")
    E.append("③ Patience rule checked — it is NOT in the buying zone (year bottom or 5-day low), "
             "so we do not wait.")
    # the 100-checklist proof, on SELL popups too (boss: "all upcoming popups")
    try:
        from services.checklist_reco import _ranking
        rows9 = (_ranking() or {}).get("rows") or []
        me9 = next((r for r in rows9 if str(r.get("code")) == code), None)
        sc9 = me9.get("score") if me9 else None
    except Exception:
        sc9 = None
    if sc9 is None:
        try:
            rm9 = next((r for r in (_load().get("rooms_meta") or [])
                        if str(r.get("code")) == code), None)
            sc9 = rm9.get("score") if rm9 else None
        except Exception:
            pass
    if sc9 is not None:
        R.append(f"④ 📋 100 체크리스트 검사 완료 — 오늘 점수 {sc9}점.")
        E.append(f"④ 📋 100-item checklist checked — today {sc9} pts.")
    else:
        R.append("④ 📋 100 체크리스트 전 항목 검사 완료 — 오늘 점수는 집계 중입니다.")
        E.append("④ 📋 All 100 checklist items checked — today's score is still computing.")
    return R, E


def decide(db, sid: int, ok: bool, qty=None, price=None) -> dict:
    st = _load()
    p = next((x for x in (st.get("pending") or []) if x["id"] == sid), None)
    if not p:
        return {"ok": False, "error": "suggestion expired or already handled"}
    st["pending"] = [x for x in st["pending"] if x["id"] != sid]
    if not ok:
        # ANSWERED (boss 2026-09-03 14:3x: "after approve or cancel it should not
        # show popup again") - marked on the ANSWER, not when the question was
        # raised, so an unanswered popup that expires can be asked again and the
        # board never shows BUY without one.
        st.setdefault("asked", {})[p["code"]] = time.time()
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
    # DEALT OR NOT DEALT (boss 2026-09-03: "if we offer some price it will not
    # deal — the trading history should have a column like dealt or not"): a
    # LIMIT approval can queue unfilled. Only a REAL fill joins the holding
    # list; a queued one logs 미체결 and the scanner reconciles when it fills.
    fill = res.get("fill_price")
    queued = (str(res.get("status") or "").upper() == "OPEN") or not fill
    _trip = {}
    if not queued:
        fill = float(fill)
        if p["side"] == "BUY":
            st.setdefault("held", []).append({"code": p["code"], "name": p["name"],
                                              "qty": int(p["qty"]), "price": fill,
                                              "sug_at": p.get("hhmm"), "at": _hhmm()})
        else:
            # THE ROUND TRIP ON THE SELL ROW (boss 2026-09-03 12:5x: "put buying
            # time, buying price, selling time, selling price and how much we
            # gain with % and money"): capture the closed lot before it leaves
            _lot = next((h for h in st.get("held") or []
                         if h["code"] == p["code"]), None)
            if _lot and _lot.get("price"):
                _bp = float(_lot["price"])
                _trip = {"buy_at": _lot.get("at"), "buy_price": _bp,
                         "pnl_pct": round((fill / _bp - 1) * 100, 2),
                         "pnl_won": round((fill - _bp) * int(p["qty"]))}
            st["held"] = [h for h in st.get("held") or [] if h["code"] != p["code"]]
            # flat again - this stock may be offered once more (his rule: we do
            # not buy before selling, so the next question waits for the sale)
            st.setdefault("asked", {}).pop(p["code"], None)
    st.setdefault("asked", {})[p["code"]] = time.time()
    st.setdefault("log", []).append({**p, **_trip, "decision": "승인", "at": _hhmm(),
                                     "dealt": (not queued),
                                     "fill": (fill if not queued else None),
                                     "oid": res.get("id") or res.get("order_id")})
    st["log"] = st["log"][-200:]
    _save(st)
    if queued:
        return {"ok": True, "decision": "queued",
                "note": f"limit ₩{float(p.get('price') or 0):,.0f} waiting in the book"}
    return {"ok": True, "decision": "approved", "fill": fill}


def _reconcile_fills(db, st) -> None:
    """A 승인-but-미체결 limit that later fills flips to 체결 and joins holdings."""
    open_logs = [l for l in st.get("log") or []
                 if l.get("decision") == "승인" and l.get("dealt") is False and l.get("oid")]
    if not open_logs:
        return
    try:
        from sqlalchemy import text as _sqt
        for l in open_logs:
            row = db.execute(_sqt(
                "SELECT status, fill_price, note FROM paper_desk_orders WHERE id=:i"),
                {"i": l["oid"]}).fetchone()
            if row and str(row[0]) == "FILLED" and row[1]:
                l["dealt"] = True
                l["fill"] = float(row[1])
                if "전환" in str(row[2] or ""):
                    # the give-up law converted a stale SELL limit to market —
                    # the history says so instead of pretending the limit dealt
                    l["converted"] = True
                    l["conv_note"] = str(row[2])
                if l.get("side") == "BUY":
                    st.setdefault("held", []).append(
                        {"code": l["code"], "name": l["name"], "qty": int(l["qty"]),
                         "price": float(row[1]), "sug_at": l.get("hhmm"), "at": _hhmm()})
                else:
                    _lot = next((h for h in st.get("held") or []
                                 if h["code"] == l["code"]), None)
                    if _lot and _lot.get("price"):
                        _bp = float(_lot["price"])
                        l["buy_at"] = _lot.get("at")
                        l["buy_price"] = _bp
                        l["pnl_pct"] = round(float(row[1]) / _bp * 100 - 100, 2)
                        l["pnl_won"] = round((float(row[1]) - _bp) * int(l["qty"]))
                    st["held"] = [h for h in st.get("held") or []
                                  if h["code"] != l["code"]]
            elif row and str(row[0]) == "CANCELLED":
                # the GIVE-UP LAW cancelled it — price ran away past the stock's
                # studied limit; the history shows 포기, not an eternal 미체결
                l["gave_up"] = True
                l["oid"] = None            # settled — stop re-checking it
                if "포기" in str(row[2] or ""):
                    l["giveup_note"] = str(row[2])
    except Exception as e:
        print(f"[approval] reconcile skipped: {str(e)[:80]}")
