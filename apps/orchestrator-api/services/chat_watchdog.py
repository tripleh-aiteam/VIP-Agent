"""chat_watchdog — the guard who calls the boss (2026-08-27: "the same employee,
but with permission to call you").

Evaluated on demand each time the chat UI polls /chat/alerts (~8s): watches ONLY the
boss's own 💬 chat-family trades and speaks once per condition —

  ✅ an order filled                          (once per order)
  🕐 a waiting order became hopeless          (>30min old AND >1% from the market)
  🔔 3rd blue candle on a held position       (his sell law — offers the sell)
  📈 a holding crossed its break-even line    (per direction change)
  🔴 a holding entered the selling zone ≥85%  (once per day)
  📰 a fresh 위험 news stamp on a holding      (once per article)

No LLM anywhere — an alert can never be hallucinated. It never trades: sell alerts
stash the normal offer, so the boss's "네" opens the standard confirmation and money
still needs his final word. First run seeds silently (no flood of old events).
"""
from __future__ import annotations

import json
import time
from datetime import timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text

from services.logger import log

KST = timezone(timedelta(hours=9))
_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "chat_watchdog.json"
_CHAT_SRC = "(COALESCE(source,'') IN ('chat','chatbot') OR COALESCE(source,'') LIKE '%-chat')"
_MAX_KEEP = 40


def _load() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(st: dict) -> None:
    try:
        _STATE_FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _kst_hhmm(dt) -> str:
    try:
        return dt.astimezone(KST).strftime("%H:%M") if dt is not None else ""
    except Exception:
        return ""


def _emit(st: dict, key: str, icon: str, text_: str, code: str = "") -> None:
    st["seen"][key] = time.time()
    st["last_id"] = int(st.get("last_id") or 0) + 1
    st.setdefault("alerts", []).append({
        "id": st["last_id"], "ts": time.time(), "icon": icon,
        "text": text_, "code": code})
    st["alerts"] = st["alerts"][-_MAX_KEEP:]


def _held_chat_codes(db) -> list[tuple[str, str]]:
    """Codes the boss bought by chat TODAY that the desk still holds."""
    day0 = None
    try:
        rows = db.execute(text(
            f"SELECT DISTINCT o.ticker, o.name FROM paper_desk_orders o "
            f"JOIN paper_desk_positions p ON p.ticker = o.ticker AND p.qty > 0 "
            f"WHERE {_CHAT_SRC} AND o.side='BUY' AND o.status='FILLED' "
            f"AND o.created_at::date = (now() at time zone 'Asia/Seoul')::date")).fetchall()
        return [(r[0], r[1] or r[0]) for r in rows]
    except Exception as e:
        log.warning(f"watchdog held codes failed: {str(e)[:100]}")
        return []


def poll(db) -> dict:
    """Evaluate all watches now; return {last_id, alerts:[...]} (recent list —
    the frontend filters by id it has already shown)."""
    st = _load()
    first_run = "seen" not in st
    st.setdefault("seen", {})
    st.setdefault("alerts", [])
    st.setdefault("last_id", 0)
    day8 = time.strftime("%Y%m%d", time.gmtime(time.time() + 9 * 3600))

    # ---- a) FILLS (chat-family orders) ----
    try:
        rows = db.execute(text(
            f"SELECT id, name, ticker, side, qty, fill_price, filled_at "
            f"FROM paper_desk_orders WHERE {_CHAT_SRC} AND status='FILLED' "
            f"ORDER BY id DESC LIMIT 25")).fetchall()
        for r in rows:
            key = f"fill:{r[0]}"
            if key in st["seen"]:
                continue
            if first_run:
                st["seen"][key] = time.time()      # seed silently
                continue
            side_ko = "매수" if r[3] == "BUY" else "매도"
            _emit(st, key, "✅",
                  f"✅ **{side_ko} 체결** — {r[1]} {int(r[4] or 0):,}주 @ ₩{float(r[5] or 0):,.0f} "
                  f"({_kst_hhmm(r[6])})", r[2])
    except Exception as e:
        log.warning(f"watchdog fills failed: {str(e)[:100]}")

    # ---- b) STUCK waiting orders (>30min and >1% away) ----
    try:
        rows = db.execute(text(
            f"SELECT id, name, ticker, side, qty, limit_price, created_at "
            f"FROM paper_desk_orders WHERE {_CHAT_SRC} AND status='OPEN' "
            f"AND order_type='limit'")).fetchall()
        for r in rows:
            key = f"stuck:{r[0]}"
            if key in st["seen"]:
                continue
            try:
                age_min = (time.time() - r[6].timestamp()) / 60 if r[6] else 0
            except Exception:
                age_min = 0
            if age_min < 30:
                continue
            px = None
            try:
                from services.paper_desk import fast_price
                px, _c, _t, _s = fast_price(r[2])
            except Exception:
                pass
            if not px or not r[5]:
                continue
            gap = abs(float(px) - float(r[5])) / float(px) * 100
            if gap < 1.0:
                continue
            side_ko = "매수" if r[3] == "BUY" else "매도"
            _emit(st, key, "🕐",
                  f"🕐 **대기 주문 정체** — {side_ko} {r[1]} {int(r[4] or 0):,}주가 {age_min:.0f}분째 "
                  f"미체결 (지정가 ₩{float(r[5]):,.0f}, 현재가 ₩{float(px):,.0f} — {gap:.1f}% 차이). "
                  f"취소하려면 \"{r[1]} 주문 취소\"라고 말씀하세요.", r[2])
    except Exception as e:
        log.warning(f"watchdog stuck failed: {str(e)[:100]}")

    # ---- c~f) held-position watches (market hours only) ----
    try:
        from services.kiwoom_tape import market_open
        in_session = market_open()
    except Exception:
        in_session = False
    if in_session:
        for code, name in _held_chat_codes(db)[:8]:
            # c) 3rd blue — his sell law
            try:
                from services.checklist_advice import _candles
                cn = _candles(db, code)
                if cn.get("blues", 0) >= 3:
                    key = f"blue3:{day8}:{code}"
                    if key not in st["seen"]:
                        _emit(st, key, "🔔",
                              f"🔔 **{name} 파란 캔들 3개째** — 법칙상 매도 시점입니다. "
                              f"\"네\"라고 하시면 매도 확인을 띄워드립니다.", code)
                        try:
                            from services.chat_trade import stash_offer
                            stash_offer(code, name, False, side="SELL")
                        except Exception:
                            pass
            except Exception:
                pass
            # d) break-even cross
            try:
                r2 = db.execute(text(
                    "SELECT avg_price FROM paper_desk_positions WHERE ticker=:t"),
                    {"t": code}).fetchone()
                from services.paper_desk import fast_price
                px, _c, _t, _s = fast_price(code)
                if r2 and r2[0] and px:
                    be = float(r2[0]) * 1.0023
                    rel = "above" if float(px) >= be else "below"
                    prev = st.setdefault("be_rel", {}).get(code)
                    st["be_rel"][code] = rel
                    if prev and prev != rel:
                        if rel == "above":
                            _emit(st, f"beup:{code}:{int(time.time())}", "📈",
                                  f"📈 **{name}이(가) 본전선을 넘었습니다** (본전 ₩{be:,.0f}, "
                                  f"현재 ₩{float(px):,.0f}) — 지금 팔면 이익입니다.", code)
                        else:
                            _emit(st, f"bedn:{code}:{int(time.time())}", "📉",
                                  f"📉 **{name}이(가) 본전선 아래로 내려왔습니다** (본전 ₩{be:,.0f}, "
                                  f"현재 ₩{float(px):,.0f}) — 지금 팔면 손해입니다.", code)
            except Exception:
                pass
            # e) selling-zone entry
            try:
                from services.checklist_reco import _year_zone
                z = _year_zone(code)
                if z and z.get("zone") == "sell":
                    key = f"zone:{day8}:{code}"
                    if key not in st["seen"]:
                        _emit(st, key, "🔴",
                              f"🔴 **{name}이(가) 매도구간에 진입** (연중 {z['pos']}%) — "
                              f"법칙: 3번째 파란 캔들에 전량 매도.", code)
            except Exception:
                pass
            # f) fresh 위험 news
            try:
                from services.checklist_advice import _fresh_stamps
                for s0 in _fresh_stamps(code, limit=2):
                    if str(s0.get("stamp") or "") not in ("위험", "악재"):
                        continue
                    key = f"news:{code}:{s0.get('ts')}"
                    if key in st["seen"]:
                        continue
                    if first_run:
                        st["seen"][key] = time.time()
                        continue
                    _lk = s0.get("link") or ""
                    _ttl = str(s0.get("title") or "")[:60]
                    if _lk.startswith("http"):
                        _ttl = f"[{_ttl}]({_lk})"
                    _emit(st, key, "📰",
                          f"📰 **[위험] 보유 종목 뉴스** — {name}: {_ttl} · "
                          f"팔지 판단이 필요하시면 \"{name} 팔까?\"라고 물어보세요.", code)
            except Exception:
                pass

    if first_run:
        st["first_seeded"] = time.time()
    _save(st)
    return {"last_id": st.get("last_id", 0), "alerts": st.get("alerts", [])[-15:]}
