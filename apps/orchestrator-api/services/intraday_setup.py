"""intraday_setup.py — the short-term SETUP SCANNER (boss's 1-hour scalp system).

Goal: for a watchlist, find LOW-RISK buy setups where a +1.5~2% move within ~60 min
is favorable, and say clearly one of three things per stock:

  ✅ ACT_NOW   — a bounce trigger is firing right now → buy in the entry zone.
  ⏳ FORMING   — a dip is setting up but the trigger hasn't fired → watch; here's the
                 exact trigger (RSI turns up / price to support) + rough timing.
  😌 NOTHING   — no dip, sector falling, market crashing, or too little volatility → sit out.

Every level is a ZONE, not a fixed price (so an auto-agent isn't confused by 1.95% vs
2.00%): target = a +1.5~2% BAND (sell on first touch), stop = −1%, time-stop = 60 min.

Combines the pieces we already built + measured:
  • cycle_scalp.signal — RSI-oversold-turning-up trigger (+ its crash veto)
  • micro_trend        — 5-min dip context + market-regime read
  • peer_cluster       — sector confirmation (don't buy into a falling sector)
  • volatility gate    — is +1.5% even reachable in an hour?

UP-only for now (down/inverse trades deferred). Rule-based + transparent so it can be
backtested honestly before it ever reaches the chatbot or real money.
"""
from __future__ import annotations

import statistics
from typing import Any, Optional

WATCHLIST: list[tuple[str, str]] = [
    ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("035420", "NAVER"),
    ("009150", "삼성전기"), ("402340", "SK스퀘어"), ("091160", "KODEX반도체"),
]
_EN = {"005930": "Samsung", "000660": "SK Hynix", "035420": "NAVER",
       "009150": "Samsung E-M", "402340": "SK Square", "091160": "KODEX Semi"}

TARGET_LO, TARGET_HI = 1.5, 2.0      # take-profit band (%), sell on first touch of LO
STOP_PCT = 1.0                       # cut here (%)
TIME_MIN = 60                        # time-stop (minutes)
_VOL_MIN = 0.85                      # min expected 1h move (1σ, %) for +1.5% to be reachable
_REGIME_CRASH = -1.5                 # KOSPI intraday % that blocks new buys


def _vol_1h_pct(closes: list[float]) -> Optional[float]:
    """Expected 1-hour move (1σ) from 5-min returns — is a +1.5% target realistic?"""
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]
    if len(rets) < 6:
        return None
    return statistics.pstdev(rets) * (12 ** 0.5) * 100    # 12 five-min bars per hour


def _nothing(code: str, name: str, ko: str, en: str, **extra) -> dict[str, Any]:
    """Full-shaped NOTHING result so every payload has the same keys (UI/API safe)."""
    return {"code": code, "name": name, "en_name": _EN.get(code, name), "state": "NOTHING",
            "price": None, "rsi": None, "vol_1h_pct": None, "cluster": None,
            "confidence": 0, "entry_zone": None, "support": None,
            "target_band": None, "target_pct": [TARGET_LO, TARGET_HI],
            "stop": None, "stop_pct": STOP_PCT, "time_min": TIME_MIN,
            "reason_ko": ko, "reason_en": en, **extra}


def scan_one(db, code: str, name: str) -> dict[str, Any]:
    code = str(code).zfill(6)
    try:
        from services.cycle_scalp import _bars, signal as m4
        from services.micro_trend import micro_read
        from services.peer_cluster import cluster_pulse
    except Exception as e:
        return _nothing(code, name, f"모듈 오류 {str(e)[:30]}", "module error")

    sig = m4(db, code)
    if not sig.get("ok"):
        return _nothing(code, name, "데이터 부족", "not enough data")
    price = float(sig["price"])
    micro = micro_read(db, code) or {}
    cluster = cluster_pulse(db, code)
    bars = _bars(db, code, limit=120)
    closes = [b["close"] for b in bars]
    vol = _vol_1h_pct(closes[-30:]) if len(closes) >= 8 else None
    support = round(min(closes[-12:])) if len(closes) >= 12 else round(price * 0.99)
    # TREND FILTER (backtest 2026-07-08 was decisive): buying RSI-dips in a DOWNtrend
    # catches knives (-96% over 13d). Requiring price above its ~2h average flips it to
    # +1.4%/57% — it makes the scanner sit out almost all falling-market dips, which is
    # exactly the low-risk behaviour we want. sma2h = 24 five-min bars.
    sma2h = sum(closes[-24:]) / 24 if len(closes) >= 24 else None
    downtrend = sma2h is not None and price < sma2h

    # --- gates (any TRUE → NOTHING, with the honest reason) ---
    sector_down = bool(cluster and cluster.get("verdict") == "SECTOR_DOWN")
    mkt = micro.get("market_chg_pct")
    regime_bad = mkt is not None and mkt <= _REGIME_CRASH
    vol_bad = vol is not None and vol < _VOL_MIN

    # --- confidence (starts 50, adds for each confirmation) ---
    conf = 50
    rsi, rsi_prev = sig.get("rsi"), sig.get("rsi_prev")
    turning_up = rsi is not None and rsi_prev is not None and rsi > rsi_prev
    if cluster and cluster.get("verdict") in ("CONFIRM_UP", "LAGGARD_UP"):
        conf += 9
    if micro.get("higher_lows"):
        conf += 7
    if micro.get("verdict") == "UP":
        conf += 6
    if turning_up:
        conf += 5
    if vol is not None and vol >= 1.2:
        conf += 3
    conf = min(conf, 80)

    entry_lo, entry_hi = round(price * 0.999), round(price * 1.003)
    tgt_lo, tgt_hi = round(price * (1 + TARGET_LO / 100)), round(price * (1 + TARGET_HI / 100))
    stop = round(price * (1 - STOP_PCT / 100))
    base = {"code": code, "name": name, "en_name": _EN.get(code, name), "price": price,
            "rsi": rsi, "vol_1h_pct": round(vol, 2) if vol is not None else None,
            "cluster": cluster.get("verdict") if cluster else None,
            "confidence": conf,
            "entry_zone": [entry_lo, entry_hi], "support": support,
            "target_band": [tgt_lo, tgt_hi], "target_pct": [TARGET_LO, TARGET_HI],
            "stop": stop, "stop_pct": STOP_PCT, "time_min": TIME_MIN}

    # --- NOTHING gates first (safety before opportunity) ---
    if sector_down:
        return {**base, "state": "NOTHING",
                "reason_ko": "동종 그룹 동반 하락 — 떨어지는 칼날, 매수 위험",
                "reason_en": "peer group falling together — falling knife, risky to buy"}
    if regime_bad:
        return {**base, "state": "NOTHING",
                "reason_ko": f"시장 급락(코스피 {mkt:+.1f}%) — 신규 매수 보류",
                "reason_en": f"market plunging (KOSPI {mkt:+.1f}%) — hold off on new buys"}
    if vol_bad:
        return {**base, "state": "NOTHING",
                "reason_ko": f"변동성 부족(예상 1시간 ±{vol:.1f}%) — 1시간 +1.5% 어려움",
                "reason_en": f"too little volatility (~±{vol:.1f}%/h) — +1.5% unlikely in 1h"}
    if downtrend:
        return {**base, "state": "NOTHING",
                "reason_ko": "하락 추세(2시간 평균 아래) — 눌림목 반등 실패 위험, 관망",
                "reason_en": "downtrend (below 2h average) — dip-bounce likely to fail, sit out"}

    # --- ACT_NOW: the RSI bounce trigger is firing ---
    if sig.get("verdict") == "BUY_NOW":
        why_ko = "5분봉 반등 시작(RSI 저점 상승전환)"
        why_en = "5-min bounce starting (RSI turning up from oversold)"
        if cluster and cluster.get("verdict") in ("CONFIRM_UP", "LAGGARD_UP"):
            why_ko += " + 그룹 동조"; why_en += " + peer group agrees"
        if micro.get("higher_lows"):
            why_ko += " + 저점 higher-low"; why_en += " + higher-lows"
        return {**base, "state": "ACT_NOW", "why_ko": why_ko, "why_en": why_en}

    # --- FORMING: dip present, waiting for the trigger ---
    if sig.get("verdict") == "WAIT":
        return {**base, "state": "FORMING",
                "trigger_ko": f"① 가격이 지지선 ₩{support:,}까지 눌림, 또는 ② 5분봉 RSI 상승전환",
                "trigger_en": f"① price dips to support ₩{support:,}, or ② 5-min RSI turns up",
                "reason_ko": "눌림목 형성 중 — 아직 반등 신호 없음 (감시)",
                "reason_en": "pullback forming — no bounce trigger yet (watching)"}

    return {**base, "state": "NOTHING",
            "reason_ko": "매수 자리 아님(과매도/눌림 아님)",
            "reason_en": "not a buy setup (no oversold pullback)"}


def scan(db) -> dict[str, Any]:
    """Scan the whole watchlist. Returns act_now / forming / nothing buckets, ranked."""
    results = []
    for code, name in WATCHLIST:
        try:
            results.append(scan_one(db, code, name))
        except Exception:
            db.rollback()
    act = sorted([r for r in results if r["state"] == "ACT_NOW"],
                 key=lambda r: -r["confidence"])
    forming = sorted([r for r in results if r["state"] == "FORMING"],
                     key=lambda r: -r["confidence"])
    nothing = [r for r in results if r["state"] == "NOTHING"]
    return {"act_now": act, "forming": forming, "nothing": nothing,
            "counts": {"act": len(act), "forming": len(forming), "nothing": len(nothing)}}


_SETUP_DDL = (
    "CREATE TABLE IF NOT EXISTS setup_log ("
    " id SERIAL PRIMARY KEY, ticker TEXT, name TEXT, logged_at TIMESTAMPTZ DEFAULT now(),"
    " entry DOUBLE PRECISION, target_lo DOUBLE PRECISION, stop DOUBLE PRECISION,"
    " time_min INT, confidence INT,"
    " status TEXT DEFAULT 'OPEN', outcome TEXT, exit_price DOUBLE PRECISION,"
    " ret_pct DOUBLE PRECISION, graded_at TIMESTAMPTZ)")


def log_and_grade(db) -> dict:
    """Log fresh ACT_NOW setups (2h dedup per ticker) + grade matured OPEN ones vs the
    live price: hit target band = WIN, hit stop = LOSS, past time_min = TIME (exit@live).
    Builds the honest forward track record. Idempotent — fire from the market-hours cron."""
    from sqlalchemy import text
    db.execute(text(_SETUP_DDL))
    db.commit()
    logged = 0
    r = scan(db)
    for s in r["act_now"]:
        dup = db.execute(text(
            "SELECT 1 FROM setup_log WHERE ticker=:t AND status='OPEN' "
            "AND logged_at > now() - interval '2 hours'"), {"t": s["code"]}).first()
        if dup:
            continue
        db.execute(text(
            "INSERT INTO setup_log (ticker, name, entry, target_lo, stop, time_min, confidence) "
            "VALUES (:t,:n,:e,:tl,:s,:tm,:c)"),
            {"t": s["code"], "n": s["name"], "e": s["price"],
             "tl": s["target_band"][0], "s": s["stop"],
             "tm": s["time_min"], "c": s["confidence"]})
        logged += 1
    db.commit()
    # grade OPEN setups
    graded = 0
    from services.peer_cluster import _chg_pct  # noqa: F401  (ensures module import path)
    open_rows = db.execute(text(
        "SELECT id, ticker, entry, target_lo, stop, time_min, logged_at, "
        "EXTRACT(EPOCH FROM (now()-logged_at))/60 AS age_min FROM setup_log "
        "WHERE status='OPEN'")).fetchall()
    for oid, tk, entry, tlo, stop, tmin, _lg, age in open_rows:
        px = _live_px(tk)
        if px is None:
            continue
        outcome = exit_px = None
        if px >= float(tlo):
            outcome, exit_px = "WIN", float(tlo)
        elif px <= float(stop):
            outcome, exit_px = "LOSS", float(stop)
        elif age is not None and float(age) >= float(tmin):
            outcome, exit_px = "TIME", px
        if outcome:
            ret = (exit_px / float(entry) - 1) * 100 - 0.23   # net of round-trip cost
            db.execute(text(
                "UPDATE setup_log SET status='GRADED', outcome=:o, exit_price=:x, "
                "ret_pct=:r, graded_at=now() WHERE id=:i"),
                {"o": outcome, "x": exit_px, "r": round(ret, 3), "i": oid})
            graded += 1
    db.commit()
    return {"logged": logged, "graded": graded}


def _live_px(code: str):
    try:
        from services import kiwoom_rest as kr
        q = kr.current_price(code)
        if q and q.get("price"):
            return float(q["price"])
    except Exception:
        pass
    try:
        from services.naver_stock import realtime_quote
        q = realtime_quote(code)
        if q and q.get("price"):
            return float(q["price"])
    except Exception:
        pass
    return None


def scorecard(db) -> dict:
    """Honest forward record of the scanner's ACT_NOW calls."""
    from sqlalchemy import text
    try:
        rows = db.execute(text(
            "SELECT outcome, count(*), round(avg(ret_pct)::numeric,3) FROM setup_log "
            "WHERE status='GRADED' GROUP BY outcome")).fetchall()
    except Exception:
        db.rollback()
        return {"graded": 0}
    by = {r[0]: {"n": r[1], "avg_ret": float(r[2])} for r in rows}
    n = sum(v["n"] for v in by.values())
    wins = by.get("WIN", {}).get("n", 0)
    tot = db.execute(text("SELECT round(sum(ret_pct)::numeric,2) FROM setup_log WHERE status='GRADED'")).scalar()
    return {"graded": n, "wins": wins, "win_rate": round(wins / n * 100, 1) if n else None,
            "total_ret_pct": float(tot) if tot is not None else 0.0, "by_outcome": by}


def _fmt(n) -> str:
    return f"{int(round(n)):,}" if n is not None else "-"


def scan_reply(db, lang: str = "ko") -> str:
    """The chatbot answer for '지금 뭐 살까 / what should I trade now' — ACT / FORMING /
    NOTHING in the zone format. Identical structure KO/EN, both surfaces."""
    r = scan(db)
    ko = lang != "en"
    act, forming = r["act_now"], r["forming"]
    L: list[str] = []

    if act:
        s = act[0]
        tb = s["target_band"]
        if ko:
            L.append(f"🎯 **지금 좋은 자리 {len(act)}개 — {s['name']} ({s['code']})**  확신 {s['confidence']}%")
            L += ["",
                  f"· ▲ 방향: 상승 (매수)",
                  f"· 진입: 지금 ~₩{_fmt(s['price'])} (₩{_fmt(s['entry_zone'][0])}–{_fmt(s['entry_zone'][1])})",
                  f"· 목표: +{s['target_pct'][0]}%~{s['target_pct'][1]}% (₩{_fmt(tb[0])}–{_fmt(tb[1])}) → 이 구간 닿으면 매도",
                  f"· 손절: −{s['stop_pct']}% (₩{_fmt(s['stop'])})",
                  f"· 시간: 최대 {s['time_min']}분",
                  f"· 근거: {s.get('why_ko','')}",
                  "",
                  f"👉 지금 진입 구간에서 매수 → +{s['target_pct'][0]}% 닿으면 익절 → 아니면 손절선에서 정리 → 둘 다 아니면 {s['time_min']}분 후 나오기."]
            if len(act) > 1:
                L.append(f"(추가 자리: {', '.join(a['name'] for a in act[1:])})")
        else:
            L.append(f"🎯 **{len(act)} good setup(s) now — {s['en_name']} ({s['code']})**  confidence {s['confidence']}%")
            L += ["",
                  f"· ▲ Direction: UP (buy)",
                  f"· Enter: now ~₩{_fmt(s['price'])} (₩{_fmt(s['entry_zone'][0])}–{_fmt(s['entry_zone'][1])})",
                  f"· Target: +{s['target_pct'][0]}%~{s['target_pct'][1]}% (₩{_fmt(tb[0])}–{_fmt(tb[1])}) → sell on first touch",
                  f"· Stop: −{s['stop_pct']}% (₩{_fmt(s['stop'])})",
                  f"· Exit by: {s['time_min']} min",
                  f"· Why: {s.get('why_en','')}",
                  "",
                  f"👉 Buy in the entry zone → take profit at +{s['target_pct'][0]}% → else stop out → else close after {s['time_min']} min."]
            if len(act) > 1:
                L.append(f"(also: {', '.join(a['en_name'] for a in act[1:])})")
        return "\n".join(L)

    if forming:
        s = forming[0]
        if ko:
            L.append(f"⏳ **지금 살 자리는 없지만 — {len(forming)}개 준비 중 (감시)**")
            L += ["", f"**{s['name']} ({s['code']})** 현재 ₩{_fmt(s['price'])}",
                  f"· 상태: {s['reason_ko']}",
                  f"· 다음 조건 충족 시 매수 신호: {s.get('trigger_ko','')}",
                  f"· 충족되면 목표 +{s['target_pct'][0]}~{s['target_pct'][1]}% / 손절 −{s['stop_pct']}%",
                  "", "👉 지금은 기다리기. 조건 닿으면 완전한 매수 신호가 뜹니다."]
        else:
            L.append(f"⏳ **Nothing to buy right now — {len(forming)} setup(s) forming (watching)**")
            L += ["", f"**{s['en_name']} ({s['code']})** now ₩{_fmt(s['price'])}",
                  f"· Status: {s['reason_en']}",
                  f"· Becomes a BUY when: {s.get('trigger_en','')}",
                  f"· Then target +{s['target_pct'][0]}~{s['target_pct'][1]}% / stop −{s['stop_pct']}%",
                  "", "👉 Wait for now. When it triggers you'll get a full BUY signal."]
        return "\n".join(L)

    # nothing
    reasons = r["nothing"][:3]
    if ko:
        L.append("😌 **지금은 좋은 자리 없음 — 쉬어가세요**")
        L.append("")
        for s in reasons:
            L.append(f"· {s['name']}: {s['reason_ko']}")
        L += ["", "💡 억지로 사지 마세요. 오늘 안 좋으면 0번 거래가 정답입니다. 자리 나오면 다시 물어보세요."]
    else:
        L.append("😌 **No good setup right now — sit this one out**")
        L.append("")
        for s in reasons:
            L.append(f"· {s['en_name']}: {s['reason_en']}")
        L += ["", "💡 Don't force a trade. On a bad day, ZERO trades is the right answer. Ask again later."]
    return "\n".join(L)
