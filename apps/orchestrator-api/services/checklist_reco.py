"""🎯 checklist_reco — "N종목 추천해줘" answered by the 100-item checklist + OUR algo.

The boss's instruction (2026-08-24): recommendations must come from the deployed
algorithm's own judgement material — 일봉 캔들 · 분봉/실시간 · 거래량 · 뉴스 — plus the
100-item checklist ranking. **Machine Learning plays NO part** ("please remove Machine
Learning, I am not using it anymore"); buy_picks (which leads with the ML method) is
now only the fallback when this engine has no data.

Ranking source = daily_pick (character × condition, checklist-weighted, already ML-free).
The morning job saves the full ranking to data/today_picks.json, so chat answers read it
instantly; when it's missing (weekend, fresh deploy) we compute once and cache 10 min.
Each pick is enriched live: 실시간 시세/호가 (snapshot-first), DB news (fast path only —
no live crawling in the chat hot path), and the ranking's own reasons. Stock names are
emitted as [이름](chart:code) — the dashboard renders them clickable and opens the
TradingView proof panel on the left.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

from services.logger import log

_WHY_EN = {
    "5>20>60 정배열": "5>20>60 MA aligned",
    "20일 신고가": "20-day new high",
    "MACD 골든크로스": "MACD golden cross",
    "외국인 순매수": "foreign net buying",
    "거래량 급증": "volume surge",
    "호가 비용 낮음": "low tick cost",
}

_cache: dict[str, tuple[float, Any]] = {}


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
    hit = _cache.get("rank")
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        res = dp.pick(day)
        if res.get("ok"):
            out = {"day": day, "rows": res["rows"]}
            _cache["rank"] = (time.time(), out)
            return out
    except Exception as e:
        log.warning(f"checklist_reco ranking failed: {str(e)[:120]}")
    return None


def _n_from(transcript: str, default: int = 3) -> int:
    m = re.search(r"(\d{1,2})\s*(?:개|종목|가지|stocks?|compan(?:y|ies)|picks?)", transcript or "", re.I)
    if not m:
        m = re.search(r"\b([1-9])\b", transcript or "")
    try:
        return max(1, min(int(m.group(1)), 8)) if m else default
    except Exception:
        return default


def _fmt_num(v) -> str:
    try:
        return f"{int(round(float(v))):,}"
    except Exception:
        return str(v)


def build(db, n: int = 3, transcript: str = "", lang: str = "ko") -> dict[str, Any]:
    """Compose the recommendation reply. {'ok': False} → caller falls back to buy_picks."""
    en = str(lang or "").lower().startswith("en")
    if not en and transcript and not re.search(r"[가-힣]", transcript) \
            and re.search(r"[a-zA-Z]", transcript):
        en = True
    n = _n_from(transcript, default=n)
    rank = _ranking()
    if not rank or not rank.get("rows"):
        return {"ok": False}
    rows = sorted(rank["rows"], key=lambda r: -r.get("score", 0))[:n]
    if not rows:
        return {"ok": False}
    day = rank["day"]
    day_disp = f"{day[:4]}-{day[4:6]}-{day[6:]}"

    # ---- market layer (100-item pre-flight): warn loudly on deal-breaker days
    mkt_line = ""
    try:
        from services.checklist_engine import market_preflight
        m = market_preflight(db)
        if m.get("deal_breakers"):
            _det = "; ".join(f"#{b['no']} {b['detail']}" for b in m["deal_breakers"][:2])
            mkt_line = (f"**Market (today):** {m['score']}/{m['max']} · 🚫 deal-breakers — {_det} — "
                        f"better to WAIT on new buying today; the ranking below is for reference."
                        if en else
                        f"**시장(오늘):** 체크 {m['score']}/{m['max']}점 · 🚫 결격 — {_det} — "
                        f"오늘 신규 매수는 쉬는 게 좋습니다. 아래 순위는 참고용입니다.")
        else:
            mkt_line = (f"**Market (today):** checklist {m['score']}/{m['max']} · no deal-breakers ✅"
                        if en else
                        f"**시장(오늘):** 체크 {m['score']}/{m['max']}점 · 결격 없음 ✅")
    except Exception:
        pass

    L = [(f"**🎯 Today's top {len(rows)} for trading — 100-item checklist + our algo (NO ML)**"
          if en else
          f"**🎯 오늘의 매매 추천 TOP {len(rows)} — 100문항 체크리스트 + 우리 알고 기준 (ML 미사용)**"),
         (f"📅 {day_disp} · judged on: daily candle · minute/real-time · volume · news"
          if en else
          f"📅 {day_disp} · 판단 재료: 일봉 캔들 · 분봉/실시간 · 거래량 · 뉴스")]
    if mkt_line:
        L += ["", mkt_line]

    for i, r in enumerate(rows, 1):
        code = r["code"]
        name = r.get("name") or code
        if en:
            try:
                from services.stock_resolver import display_name_en
                name = display_name_en(code) or name
            except Exception:
                pass
        g = r.get("groups") or {}
        why = r.get("why") or []
        why_disp = [(_WHY_EN.get(w, w) if en else w) for w in why]
        L += ["", f"**{i}. [{name}](chart:{code}) — {r.get('score')}"
                  + (" pts (checklist composite)**" if en else "점 (체크리스트 종합)**")]
        # 📈 daily candle
        d_bits = []
        if r.get("aligned") == 2:
            d_bits.append("5>20>60 MA aligned" if en else "5>20>60 정배열")
        elif r.get("aligned") == 1:
            d_bits.append("MA5>MA20")
        if r.get("new_high"):
            d_bits.append("20-day new high" if en else "20일 신고가")
        if r.get("rsi") is not None:
            d_bits.append(f"RSI {r['rsi']}")
        if g.get("trend") is not None:
            d_bits.append((f"trend {g['trend']}/100" if en else f"추세 {g['trend']}/100"))
        L.append(("· 📈 Daily candle: " if en else "· 📈 일봉: ") + " · ".join(d_bits or ["—"]))
        # 📊 volume / liquidity
        v_bits = []
        if g.get("liquidity") is not None:
            v_bits.append((f"liquidity {g['liquidity']}/100" if en else f"유동성 {g['liquidity']}/100"))
        for w, wd in zip(why, why_disp):
            if "거래량" in w:
                v_bits.append(wd)
        L.append(("· 📊 Volume: " if en else "· 📊 거래량: ") + " · ".join(v_bits or ["—"]))
        # ⏱ minute / real-time
        t_bits = []
        try:
            from services.trading_brief import realtime_for
            rt = realtime_for(code, db=db) or {}
            if rt.get("price"):
                t_bits.append((f"now ₩{_fmt_num(rt['price'])}" if en else f"현재가 {_fmt_num(rt['price'])}원"))
            if rt.get("pressure"):
                t_bits.append(rt.get("pressure_en") if en and rt.get("pressure_en") else rt["pressure"])
            if rt.get("program_net") is not None:
                t_bits.append((f"program {rt['program_net']:+,}" if en else f"프로그램 {rt['program_net']:+,}"))
        except Exception:
            pass
        L.append(("· ⏱ Minute/real-time: " if en else "· ⏱ 분봉·실시간: ")
                 + (" · ".join(t_bits) if t_bits else ("no live tape (market closed?)" if en else "실시간 데이터 없음(장외)")))
        # 📰 news (fast DB path only — chat latency)
        n_line = "—"
        try:
            from services import news_impact as ni
            items = list(ni.effective_news(db, code, limit=6) or [])
            if items:
                sc = 0
                for it in items:
                    d = it.get("direction")
                    sc += (1 if d in (1, "▲") else -1 if d in (-1, "▼") else 0)
                sc = max(-3, min(3, sc))
                n_line = (f"score {sc:+d} ({len(items)})" if en else f"점수 {sc:+d} ({len(items)}건)")
            else:
                n_line = "no recent stamps" if en else "최근 뉴스 스탬프 없음"
        except Exception:
            pass
        L.append(("· 📰 News: " if en else "· 📰 뉴스: ") + n_line)
        # supply-demand flavor from the ranking's own reasons
        extra = [wd for w, wd in zip(why, why_disp) if "외국인" in w or "호가" in w]
        if extra:
            L.append(("· 💰 Flows: " if en else "· 💰 수급: ") + " · ".join(extra))

    L += ["",
          ("※ Click a stock name to open its live chart on the left. "
           "No ML anywhere in this ranking — it is the 100-item checklist (trend·liquidity·"
           "flows·momentum) plus the deployed algo's own materials. "
           "For buy/sell timing ask \"should I buy <name>?\" — that answer applies the algo's "
           "buy-case and sell-case rules."
           if en else
           "※ 종목 이름을 클릭하면 왼쪽에 실시간 차트가 열립니다. "
           "이 순위에 ML은 전혀 쓰이지 않습니다 — 100문항 체크리스트(추세·유동성·수급·모멘텀)와 "
           "배포된 알고의 판단 재료 그대로입니다. "
           "매수/매도 타이밍은 \"종목명 살까?\"로 물어보면 알고의 매수 조건/매도 조건 기준으로 답합니다.")]
    return {"ok": True, "reply": "\n".join(L), "picks": [r["code"] for r in rows]}
