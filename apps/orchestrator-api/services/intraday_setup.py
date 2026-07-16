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

from sqlalchemy import text as _sql_text
from typing import Any, Optional

# Liquid day-trade watchlist across sectors — all have live 5-min bars in our collector.
WATCHLIST: list[tuple[str, str]] = [
    ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("069500", "KODEX200"),
    ("373220", "LG에너지솔루션"), ("006400", "삼성SDI"), ("005380", "현대차"),
    ("000270", "기아"), ("035420", "NAVER"), ("035720", "카카오"),
    ("207940", "삼성바이오로직스"), ("009150", "삼성전기"), ("402340", "SK스퀘어"),
]
_EN = {"005930": "Samsung", "000660": "SK Hynix", "069500": "KODEX 200",
       "373220": "LG Energy", "006400": "Samsung SDI", "005380": "Hyundai Motor",
       "000270": "Kia", "035420": "NAVER", "035720": "Kakao",
       "207940": "Samsung Bio", "009150": "Samsung E-M", "402340": "SK Square"}

TARGET_LO, TARGET_HI = 1.5, 2.0      # default band; ADAPTIVE per-stock overrides it below
STOP_PCT = 1.0                       # default; adaptive
TIME_MIN = 60                        # time-stop (minutes)
_VOL_MIN = 0.45                      # min 1h move (1σ, %) — below this even a small target
                                     # can't clear costs reliably; lowered from 0.85 so the
                                     # target ADAPTS instead of rejecting every calm stock
_TGT_MIN, _TGT_MAX = 0.8, 2.5        # (legacy adaptive bounds — superseded by _plan_pct)
_RR = 1.6                            # reward:risk (target / stop) inside the cheap band
_REGIME_CRASH = -1.5                 # KOSPI intraday % that blocks new DIP buys
_EXTREME_CRASH = -3.0                # KOSPI intraday % that blocks EVERYTHING (panic)
# MOMENTUM setup thresholds — a stock strongly rising (bucks a down market)
_MOM_R15, _MOM_R45 = 0.35, 0.5       # min 15m / 45m rise (%)
_RSI_MOM_MIN, _RSI_MOM_MAX = 52, 78  # rising but not overbought (72→78, boss 07-15:
                                     # a +12% day was refused at RSI 74 — participate longer)
_RIDE_R45 = 2.0                      # trend-ride: a 45-min move this big IS the setup
_DEEP_SCAN = 45                      # boss 2026-07-09: deep-scan the WHOLE watchlist every
                                     # pass (was 14 most-active; he wants no stock skipped)
_CHEAP_PX = 100_000                  # boss's price tiers for the exit plan


def _plan_pct(price: float, vol: Optional[float]) -> tuple[float, float, float]:
    """BOSS-TIERED exit plan (2026-07-09, explicit instruction — replaces the pure
    volatility-scaled plan): ≥₩100k/share → fixed target +1% / stop −1%; <₩100k →
    target +2~3% / stop −1~2% (the stock's measured 1h volatility picks the exact
    point INSIDE the boss's band). Returns (t_lo, t_hi, stop_pct)."""
    if price and float(price) >= _CHEAP_PX:
        return 1.0, 1.3, 1.0
    sig = vol if vol is not None else 1.5
    t_lo = max(2.0, min(3.0, round(sig * 1.25, 1)))
    t_hi = round(min(3.4, t_lo + 0.4), 1)
    s = max(1.0, min(2.0, round(t_lo / _RR, 1)))
    return t_lo, t_hi, s


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

    # 🔁 RE-ENTRY COOLDOWN (boss 2026-07-13 loss audit): if this stock was STOP-SOLD at
    # a loss within the last 60 minutes, NO new buy signal — the LG화학 rebuy loop
    # (buy → stop → rebuy the same falling stock → stop again) bled fees + cuts.
    # MACHINE sells only (2026-07-15): the boss selling by hand is HIS decision —
    # it must not gag the engine for an hour (SKH was muted on a +12% day by it).
    try:
        _burn = db.execute(_sql_text(
            "SELECT 1 FROM paper_desk_orders WHERE ticker=:t AND side='SELL' "
            "AND status='FILLED' AND realized_pnl < 0 "
            "AND COALESCE(source, 'manual') <> 'manual' "
            "AND COALESCE(filled_at, created_at) > now() - interval '60 minutes' LIMIT 1"),
            {"t": code}).first()
        if _burn:
            return _nothing(code, name,
                            "60분 전 이 종목에서 손절 — 재진입 금지 (같은 칼날을 두 번 잡지 않습니다)",
                            "stopped out of this stock within the last hour — no re-entry "
                            "(we don't catch the same knife twice)")
    except Exception:
        db.rollback()
    price = float(sig["price"])
    micro = micro_read(db, code) or {}
    cluster = cluster_pulse(db, code)

    # MIXED ENGINE (2026-07-08): the technical trigger sets the TIMING; the deep brain's
    # ML direction + news sets the CONVICTION. ML predicts 5-day direction (fast DB read),
    # so it's a confirmation filter, not the trigger: a SELL-rated / bad-news stock is NOT
    # scalp-bought even if the RSI bounces. Agreement boosts confidence.
    ml_advice = None
    try:
        from services import prediction_service as ps
        _ml = ps.get_ticker(db, code) or {}
        ml_advice = (_ml.get("advice") or "").upper() or None
    except Exception:
        pass
    ml_bearish = ml_advice == "SELL"
    ml_bullish = ml_advice == "BUY"
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

    # --- gates ---
    sector_down = bool(cluster and cluster.get("verdict") == "SECTOR_DOWN")
    mkt = micro.get("market_chg_pct")
    regime_bad = mkt is not None and mkt <= _REGIME_CRASH   # blocks DIP buys only
    extreme_crash = mkt is not None and mkt <= _EXTREME_CRASH  # blocks everything (panic)
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
    if ml_bullish:
        conf += 8                        # ML agrees on direction — team confirmation
    # 📉 RED-TAPE PENALTY (boss 2026-07-13 loss audit): momentum in a falling market
    # reverses fast — today's cuts mostly came from signals fired against the tape.
    # A sliding KOSPI demands much stronger proof before a signal qualifies.
    if mkt is not None:
        if mkt <= -1.0:
            conf -= 10
        elif mkt <= -0.5:
            conf -= 6
    # ⑩ 과거 패턴 (analog forecasting, boss 2026-07-13): the stock's own year of
    # movement history votes — similar-past-windows up-rate + time-of-day personality.
    # A weighted voice (±conf), never a veto; graded like everything else.
    pattern = None
    try:
        from services.pattern_layer import pattern_vote
        pattern = pattern_vote(db, code)
        if pattern:
            if pattern["up_rate"] >= 60:
                conf += 4
            elif pattern["up_rate"] <= 40:
                conf -= 4
            if pattern.get("tod_up") is not None:
                if pattern["tod_up"] >= 57:
                    conf += 2
                elif pattern["tod_up"] <= 43:
                    conf -= 2
    except Exception:
        db.rollback()
    # ⏰ BAD-HOURS PENALTY (Phase A, 2026-07-16): 3,617 graded ML calls show the
    # engine is systematically worst at 10/11/13 KST — signals in those hours
    # need stronger proof.
    try:
        from datetime import datetime as _dth
        from zoneinfo import ZoneInfo as _zih
        if _dth.now(_zih("Asia/Seoul")).hour in (10, 11, 13):
            conf -= 8
    except Exception:
        pass
    conf = max(0, min(conf, 88))

    # BOSS-TIERED target/stop (2026-07-09): ≥₩100k → +1%/−1%; <₩100k → +2~3%/−1~2%
    # (volatility picks the point inside the band). See _plan_pct.
    t_lo, t_hi, s_pct = _plan_pct(price, vol)
    entry_lo, entry_hi = round(price * 0.999), round(price * 1.003)
    tgt_lo, tgt_hi = round(price * (1 + t_lo / 100)), round(price * (1 + t_hi / 100))
    stop = round(price * (1 - s_pct / 100))
    base = {"code": code, "name": name, "en_name": _EN.get(code, name), "price": price,
            "rsi": rsi, "vol_1h_pct": round(vol, 2) if vol is not None else None,
            "cluster": cluster.get("verdict") if cluster else None,
            "pattern": pattern,
            "confidence": conf,
            "entry_zone": [entry_lo, entry_hi], "support": support,
            "target_band": [tgt_lo, tgt_hi], "target_pct": [t_lo, t_hi],
            "stop": stop, "stop_pct": s_pct, "time_min": TIME_MIN}

    # ⚡ QUICK BOUNCE (boss 2026-07-14: "even if it survived 5 minutes, buy and sell
    # immediately") — a STRONG, volume-backed 15-minute thrust trades FAST: tight
    # doors, 20-minute life, in and out. By design it bypasses the downtrend/sector
    # gates (bounces live inside falls) but NOT the panic gate, the ML/news vetoes,
    # or the re-entry cooldown. The strength bar is high on purpose: thrust ≥1.0%
    # + volume ≥1.5× + healthy RSI, and conf only clears the 65 line with extra
    # strength (≥1.5% thrust, 2× volume, or history pattern agreeing).
    _r15q = micro.get("r15")
    _rsi5q = micro.get("rsi5")
    _volrq = micro.get("vol_ratio")   # Naver 5-min bars often carry no volume → None
    _q_thrust = (_r15q is not None
                 and ((_volrq is not None and _volrq >= 1.5 and _r15q >= 1.0)
                      or (_volrq is None and _r15q >= 1.2)))   # no volume proof → stronger thrust
    _q_rsi_ok = _rsi5q is None or _rsi5q <= 82   # only block blow-off tops; the thrust IS the proof
    if _q_thrust and _q_rsi_ok and not extreme_crash and not ml_bearish:
        _qnews = 0
        try:
            from services.decision_agent import _news as _qn
            _qnews = (_qn(db, code, name) or {}).get("score") or 0
        except Exception:
            db.rollback()
        if _qnews > -2:
            _qconf = 62 + (4 if _r15q >= 1.5 else 0) \
                     + (4 if (_volrq or 0) >= 2.0 else 0) \
                     + (3 if (pattern or {}).get("up_rate", 0) >= 55 else 0)
            _qvol_ko = f" + 거래량 {_volrq:.1f}배" if _volrq else ""
            _qvol_en = f" + volume {_volrq:.1f}×" if _volrq else ""
            return {**base,
                    "state": "ACT_NOW", "setup_type": "quick_bounce",
                    "confidence": min(_qconf, 82),
                    "target_band": [round(price * 1.008), round(price * 1.012)],
                    "target_pct": [0.8, 1.2],
                    "stop": round(price * 0.993), "stop_pct": 0.7,
                    "time_min": 20,
                    "why_ko": (f"⚡ 초단타 반등: 15분 {_r15q:+.1f}% 급등{_qvol_ko}"
                               f" — 20분 승부, 먹고 바로 나옵니다"),
                    "why_en": (f"⚡ quick bounce: 15m {_r15q:+.1f}% thrust{_qvol_en}"
                               f" — a 20-minute play, in and out"),
                    "ml": ml_advice, "news_score": _qnews}

    # 🏇 TREND RIDE (boss 2026-07-15: SKH +12% / 삼성전자 +6% and no signal) — on a
    # monster trend day the 45-min move itself IS the setup. Bypasses the downtrend
    # gate BY DESIGN: after a huge gap-up the price consolidates below its spike-
    # inflated 2h average and the gate wrongly reads "downtrend". Own safeties:
    # 45m thrust ≥2%, the last 15 min not rolling over, RSI not blowing off,
    # panic gate, ML-SELL veto, bad-news veto.
    _r45t = micro.get("r45")
    _r15t = micro.get("r15")
    _rsi5t = micro.get("rsi5")
    if (_r45t is not None and _r45t >= _RIDE_R45
            and _r15t is not None and _r15t > -0.1
            and _rsi5t is not None and 50 <= _rsi5t <= 80
            and micro.get("verdict") in ("UP", "FLAT")
            and not extreme_crash and not ml_bearish):
        _tnews = 0
        try:
            from services.decision_agent import _news as _tn
            _tnews = (_tn(db, code, name) or {}).get("score") or 0
        except Exception:
            db.rollback()
        if _tnews > -2:
            _tconf = 66 + (4 if _r45t >= 4.0 else 0) \
                     + (3 if (pattern or {}).get("up_rate", 0) >= 55 else 0)
            return {**base,
                    "state": "ACT_NOW", "setup_type": "trend_ride",
                    "confidence": min(_tconf, 80),
                    "why_ko": (f"🏇 대세 상승일 올라타기 (45분 {_r45t:+.1f}% · 15분 {_r15t:+.1f}% "
                               f"· RSI {_rsi5t:.0f}) — 눌림 없이 밀어올리는 날은 추세가 셋업입니다"),
                    "why_en": (f"🏇 trend-day ride (45m {_r45t:+.1f}% · 15m {_r15t:+.1f}% "
                               f"· RSI {_rsi5t:.0f}) — on a no-pullback day the trend itself is the setup"),
                    "ml": ml_advice, "news_score": _tnews}

    # --- NOTHING gates first (safety before opportunity) ---
    if sector_down:
        return {**base, "state": "NOTHING", "gate": "sector",
                "reason_ko": "동종 그룹 동반 하락 — 떨어지는 칼날, 매수 위험",
                "reason_en": "peer group falling together — falling knife, risky to buy",
                "path_ko": "그룹 하락이 멈추고 반등 시작하면 매수 신호 가능",
                "path_en": "becomes buyable when the group stops falling and turns up"}
    if extreme_crash:
        return {**base, "state": "NOTHING", "gate": "regime",
                "reason_ko": f"시장 폭락(코스피 {mkt:+.1f}%) — 전량 관망(패닉 회피)",
                "reason_en": f"market crashing (KOSPI {mkt:+.1f}%) — sit fully out (panic)",
                "path_ko": "폭락이 진정되면 다시 스캔",
                "path_en": "re-scan once the crash calms"}
    if vol_bad:
        return {**base, "state": "NOTHING", "gate": "vol",
                "reason_ko": f"변동성 부족(예상 1시간 ±{vol:.1f}%) — 1시간 +1.5% 어려움",
                "reason_en": f"too little volatility (~±{vol:.1f}%/h) — +1.5% unlikely in 1h",
                "path_ko": "장중 큰 움직임(변동성)이 살아나면 매수 자리 가능",
                "path_en": "becomes buyable if intraday volatility picks up"}
    if downtrend:
        return {**base, "state": "NOTHING", "gate": "downtrend",
                "reason_ko": "하락 추세(2시간 평균 아래) — 눌림목 반등 실패 위험, 관망",
                "reason_en": "downtrend (below 2h average) — dip-bounce likely to fail, sit out",
                "path_ko": f"가격이 2시간 평균 위로 올라오면(≈₩{round(sma2h):,}) 매수 신호 대기",
                "path_en": f"watch for price to reclaim its 2h average (≈₩{round(sma2h):,})"}

    # confirm helper: the ML + news brain approves/vetoes an ACT_NOW candidate
    def _confirm(why_ko: str, why_en: str, stype: str):
        if ml_bearish:
            return {**base, "state": "NOTHING", "gate": "ml",
                    "reason_ko": "기술적 신호는 있으나 ML이 하락 예상 — 스캘핑 매수 보류",
                    "reason_en": "technical signal, but ML forecasts a fall — hold off scalp-buy",
                    "path_ko": "ML 전망이 매수/중립으로 바뀌면 매수 자리 가능",
                    "path_en": "becomes buyable when the ML outlook turns to buy/neutral"}
        news = {}
        try:
            from services.decision_agent import _news
            news = _news(db, code, name) or {}
        except Exception:
            pass
        nscore = news.get("score") or 0
        if nscore <= -2:
            return {**base, "state": "NOTHING", "gate": "news",
                    "reason_ko": "기술적 신호는 있으나 부정적 뉴스 — 매수 보류",
                    "reason_en": "technical signal, but negative news flow — hold off",
                    "path_ko": "뉴스 악재가 소화되면 다시 스캔",
                    "path_en": "re-scan once the negative news is digested"}
        wk, we = why_ko, why_en
        if ml_bullish:
            wk += " + ML 매수"; we += " + ML says buy"
        if nscore >= 2:
            wk += " + 긍정 뉴스"; we += " + positive news"
        return {**base, "state": "ACT_NOW", "setup_type": stype, "why_ko": wk, "why_en": we,
                "ml": ml_advice, "news_score": nscore}

    # --- SETUP 1: DIP-BOUNCE — RSI oversold turning up (in an uptrend, gates passed).
    # Skipped on a falling market (don't dip-buy into a −1.5%+ tape). ---
    if sig.get("verdict") == "BUY_NOW" and not regime_bad:
        wk = "눌림목 반등 시작(RSI 저점 상승전환)"
        we = "dip-bounce starting (RSI turning up from oversold)"
        if cluster and cluster.get("verdict") in ("CONFIRM_UP", "LAGGARD_UP"):
            wk += " + 그룹 동조"; we += " + peer group agrees"
        if micro.get("higher_lows"):
            wk += " + 저점 higher-low"; we += " + higher-lows"
        return _confirm(wk, we, "dip")

    # --- SETUP 2: MOMENTUM — strong uptrend, rising, not overbought. This catches the
    # stocks BUCKING a down market (the boss's point: something is always rising). ---
    r15, r45, rsi5, volr = micro.get("r15"), micro.get("r45"), micro.get("rsi5"), micro.get("vol_ratio")
    momentum = (r15 is not None and r45 is not None and rsi5 is not None
                and r15 >= _MOM_R15 and r45 >= _MOM_R45
                and _RSI_MOM_MIN <= rsi5 <= _RSI_MOM_MAX
                and micro.get("verdict") == "UP")
    # 🗳️ THREE-VOICE GATE (Phase A, 2026-07-16): the momentum setup lost money
    # (5 wins / 19 trades, −6.1% total) — it now needs the same agreeing voices
    # as Algorithm 2 before it may fire. Fail-open on missing data:
    #   ② order book — sellers must not dominate the queue
    #   ③ peer group — an explicitly diverging/falling group kills the signal
    if momentum:
        try:
            from services.kiwoom_rest import order_book as _obm
            _obi = (_obm(code, ttl=10) or {}).get("imbalance")
            if _obi is not None and float(_obi) < -0.05:
                momentum = False
        except Exception:
            pass
    if momentum and cluster and cluster.get("verdict") in ("SECTOR_DOWN", "HOLDING_VS_WEAK"):
        momentum = False        # rising alone while the group falls = fragile momentum
    if momentum:
        wk = f"강한 상승 흐름 (15분 {r15:+.1f}% · 45분 {r45:+.1f}% · RSI {rsi5})"
        we = f"strong uptrend (15m {r15:+.1f}% · 45m {r45:+.1f}% · RSI {rsi5})"
        if volr and volr >= 1.5:
            wk += " + 거래량 급증"; we += " + volume surge"
        if cluster and cluster.get("verdict") == "CONFIRM_UP":
            wk += " + 그룹 동조"; we += " + peer group up"
        return _confirm(wk, we, "momentum")

    # --- FORMING: dip present, waiting for the trigger ---
    if sig.get("verdict") == "WAIT":
        # if price is ALREADY at/near its recent low, "dip to support ₩X" is degenerate
        # (X == now) — the only meaningful trigger left is the RSI turn.
        at_support = support >= price * 0.997
        rsi_now = sig.get("rsi")
        if at_support:
            trig_ko = (f"5분봉 RSI({rsi_now})가 저점에서 상승 전환 — 지금 지지선(₩{support:,}) "
                       f"부근이라 반등만 확인되면 매수")
            trig_en = (f"5-min RSI ({rsi_now}) turns up from its low — already near support "
                       f"(₩{support:,}), so just needs the bounce to confirm")
        else:
            trig_ko = f"① 가격이 지지선 ₩{support:,}까지 더 눌리거나, ② 5분봉 RSI({rsi_now}) 상승 전환"
            trig_en = f"① price dips further to support ₩{support:,}, or ② 5-min RSI ({rsi_now}) turns up"
        return {**base, "state": "FORMING",
                "trigger_ko": trig_ko, "trigger_en": trig_en,
                "reason_ko": "눌림목 형성 중 — 아직 반등 신호 없음 (감시)",
                "reason_en": "pullback forming — no bounce trigger yet (watching)"}

    return {**base, "state": "NOTHING", "gate": "nosetup",
            "reason_ko": f"매수 자리 아님 — 과매도 눌림 아님 (RSI {rsi if rsi is not None else '-'})",
            "reason_en": f"no buy setup — not an oversold pullback (RSI {rsi if rsi is not None else '-'})",
            "path_ko": "가격이 눌렸다가 5분봉 RSI가 저점에서 상승 전환하면 매수 신호",
            "path_en": "buy signal when price pulls back and 5-min RSI turns up from a low"}


def _universe(db, limit: int = 45) -> list[tuple[str, str]]:
    """All stocks we have fresh intraday data for (~40 collected), names from krx_stocks.
    Falls back to the core WATCHLIST if the snapshot table is empty."""
    from sqlalchemy import text
    codes: list[str] = []
    try:
        rows = db.execute(text(
            "SELECT DISTINCT ticker FROM intraday_snapshot_history "
            "WHERE ts > now() - interval '6 hours'")).fetchall()
        codes = [str(r[0]) for r in rows if r[0]]
    except Exception:
        db.rollback()
    if not codes:
        return list(WATCHLIST)
    names: dict[str, str] = {}
    try:
        nr = db.execute(text("SELECT code, name FROM krx_stocks WHERE code = ANY(:c)"),
                        {"c": codes}).fetchall()
        names = {str(r[0]): r[1] for r in nr}
    except Exception:
        db.rollback()
    wl = dict(WATCHLIST)
    return [(c, names.get(c) or wl.get(c) or c) for c in codes][:limit]


def _prescan(db, codes: list[str]) -> list[str]:
    """Cheap bulk rank so we deep-scan only the most ACTIVE stocks: recent snapshot
    prices → |momentum| + above-2h-average → interest score, high to low. One query."""
    from collections import defaultdict

    from sqlalchemy import text
    try:
        rows = db.execute(text(
            "SELECT ticker, price FROM intraday_snapshot_history "
            "WHERE ts > now() - interval '3 hours' AND price IS NOT NULL "
            "AND ticker = ANY(:c) ORDER BY ticker, ts"), {"c": codes}).fetchall()
    except Exception:
        db.rollback()
        return codes
    ser: dict[str, list] = defaultdict(list)
    for tk, px in rows:
        ser[str(tk)].append(float(px))
    scored = []
    for c in codes:
        s = ser.get(c, [])
        if len(s) < 10:
            scored.append((c, -1.0))
            continue
        last = s[-1]
        sma = sum(s[-40:]) / min(len(s), 40)         # ~2h at ~2-3min snapshots
        mom = abs(last / s[max(0, len(s) - 20)] - 1) * 100   # recent move magnitude
        interest = mom + (1.5 if last > sma else 0.0)        # movers + uptrend = worth a look
        scored.append((c, interest))
    scored.sort(key=lambda x: -x[1])
    return [c for c, _ in scored]


def _top_gainer_codes(skip: int = 12, take: int = 24) -> list[str]:
    """MARKET-WIDE gainers (Naver rising pages, KOSPI+KOSDAQ). The list is sorted by %
    desc — we SKIP the extreme top (small-cap +15-30% limit-up pumps, dangerous to chase)
    and take the moderate-gainer slice below, where the healthy +2-6% liquid names live."""
    import re

    import httpx
    out: list[str] = []
    for sosok in (0, 1):
        try:
            r = httpx.get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={sosok}",
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            codes = re.findall(r'/item/main\.naver\?code=(\d{6})', r.content.decode("euc-kr", "ignore"))
            out += codes[skip:skip + take]
        except Exception:
            pass
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _scan_mover(db, code: str, name: str) -> Optional[dict[str, Any]]:
    """LIGHT momentum check for a MODERATE, LIQUID market-wide gainer using the live quote
    + today's candle: up a healthy +2~6% (NOT a +15-30% small-cap limit-up pump — those
    are how people blow up), price ≥ ₩5,000, still near the day high, ML + news confirm.
    A distinct 'market momentum' setup (higher risk = chasing strength). ACT_NOW or None."""
    from services.naver_stock import daily_history, realtime_quote
    q = realtime_quote(code) or {}
    price = q.get("price")
    chg = q.get("change_pct")
    if not (price and chg is not None):
        return None
    if price < 5000:                          # liquidity floor — avoid penny/pump names
        return None
    if not (2.0 <= chg <= 6.0):               # HEALTHY momentum, not a blown-off limit-up
        return None
    hi, lo, op = q.get("high"), q.get("low"), q.get("open")
    if not (hi and lo and op):                # quote missing OHLC → today's candle
        d = (daily_history(code, days=1) or [{}])[0]
        hi, lo, op = hi or d.get("high"), lo or d.get("low"), op or d.get("open")
    if not (hi and lo and op and hi > lo):
        return None
    if price <= op:                           # up intraday, not gap-then-fade
        return None
    pos = (price - lo) / (hi - lo)
    if pos < 0.6:                             # fading from the high → momentum broken
        return None
    # ML + news confirm
    try:
        from services import prediction_service as ps
        if (ps.get_ticker(db, code) or {}).get("advice", "").upper() == "SELL":
            return None
    except Exception:
        pass
    try:
        from services.decision_agent import _news
        if (_news(db, code, name) or {}).get("score", 0) <= -2:
            return None
    except Exception:
        pass
    day_range = (hi - lo) / price * 100
    # boss-tiered plan: expensive movers +1%/−1%, cheap movers +2~3%/−1~2%
    t_lo, t_hi, s_pct = _plan_pct(float(price), day_range * 0.5)
    conf = min(78, 55 + int(pos * 12) + (5 if chg >= 4 else 0))
    return {"code": code, "name": name, "en_name": name, "price": float(price),
            "rsi": None, "vol_1h_pct": round(day_range, 2), "cluster": None,
            "confidence": conf, "setup_type": "market_momentum",
            "entry_zone": [round(price * 0.999), round(price * 1.004)],
            "support": round(op), "target_band": [round(price * (1 + t_lo / 100)),
                                                   round(price * (1 + t_hi / 100))],
            "target_pct": [t_lo, t_hi], "stop": round(price * (1 - s_pct / 100)),
            "stop_pct": s_pct, "time_min": TIME_MIN, "state": "ACT_NOW",
            "why_ko": f"오늘 시장 강세 상위 ({chg:+.1f}%) · 고가 부근({pos*100:.0f}%) · 상승 흐름 지속",
            "why_en": f"top market gainer today ({chg:+.1f}%) · near high ({pos*100:.0f}%) · momentum intact"}


def setup_for(db, code: str, name: str) -> dict[str, Any]:
    """The 1-hour setup view for ONE stock — ANY listed stock (boss 2026-07-09: the
    per-stock chatbot answer must lead with this). Deep scan when we collect minute
    bars for it; otherwise the light live-quote momentum check (same as the market-wide
    gainer sweep), so a stock outside our ~40 still gets a real answer instead of
    'no data'. Attaches the M5.6 AI probability where available (ranking voice)."""
    code = str(code).zfill(6)
    s = scan_one(db, code, name)
    if s.get("state") == "NOTHING" and s.get("reason_en") == "not enough data":
        m = None
        try:
            m = _scan_mover(db, code, name)
        except Exception:
            db.rollback()
        if m:
            s = m
        else:
            s = {**s, "reason_ko": "지금 1시간 단타 신호 없음 (강한 상승 흐름·눌림목 패턴 아님)",
                 "reason_en": "no 1-hour setup right now (no strong momentum or dip pattern)"}
    if s.get("state") in ("ACT_NOW", "FORMING"):
        try:
            from services.hourly_model import prob_up_1h
            p = prob_up_1h(db, code)
            if p is not None:
                s["ai_1h_prob"] = round(p * 100)
        except Exception:
            pass
    return s


INVERSE_ETF = ("252670", "KODEX 200선물인버스2X")   # rises ~2× when KOSPI200 falls


def _scan_down(db) -> Optional[dict[str, Any]]:
    """📉 DOWN setup (boss 2026-07-13): when the MARKET itself is in a confirmed
    slide, the tradeable instrument is the INVERSE ETF — buying it = earning from
    the fall, with all the normal doors (target/stop/trail/60min) unchanged.
    Detection: KODEX200 below its 2h average AND falling on 15m+45m, confirmed by
    the heavyweight AI leaning down. Never fires in an up/flat tape."""
    try:
        from services.cycle_scalp import _bars
        bars = _bars(db, "069500", limit=120)          # KODEX200 = the market
        if len(bars) < 30:
            return None
        c = [b["close"] for b in bars]
        r15 = (c[-1] / c[-4] - 1) * 100 if len(c) >= 4 and c[-4] else 0
        r45 = (c[-1] / c[-10] - 1) * 100 if len(c) >= 10 and c[-10] else 0
        sma2h = sum(c[-24:]) / min(24, len(c))
        if not (c[-1] < sma2h and r15 <= -0.15 and r45 <= -0.35):
            return None
        ai = None
        try:
            from services.hourly_model import prob_up_1h
            p = prob_up_1h(db, "000660")               # heavyweight confirmation
            ai = round(p * 100) if p is not None else None
            if ai is not None and ai >= 50:
                return None                            # heavyweight not leaning down
        except Exception:
            pass
        code, name = INVERSE_ETF
        # re-entry cooldown applies to the inverse too
        try:
            if db.execute(_sql_text(
                "SELECT 1 FROM paper_desk_orders WHERE ticker=:t AND side='SELL' "
                "AND status='FILLED' AND realized_pnl < 0 "
                "AND COALESCE(filled_at, created_at) > now() - interval '60 minutes' LIMIT 1"),
                    {"t": code}).first():
                return None
        except Exception:
            db.rollback()
        from services.paper_desk import _live_price
        px, _nm = _live_price(code)
        if not px:
            return None
        conf = 62
        if r45 <= -0.7:
            conf += 6
        if ai is not None and ai <= 40:
            conf += 4
        # plan from the INDEX's 1h volatility ×2 (the ETF is 2X-leveraged); the boss
        # price tiers don't apply to an index instrument — adaptive band, RR 1.6
        rets = [c[i] / c[i - 1] - 1 for i in range(len(c) - 30, len(c)) if c[i - 1]]
        vol2x = (statistics.pstdev(rets) * (12 ** 0.5) * 100) * 2 if len(rets) > 5 else 1.0
        t_lo = max(0.8, min(2.5, round(vol2x * 1.25, 1)))
        t_hi = round(min(2.8, t_lo + 0.4), 1)
        s_pct = max(0.5, round(t_lo / _RR, 1))
        return {"code": code, "name": name, "en_name": "KODEX 200 Futures Inverse 2X",
                "price": float(px), "rsi": None, "vol_1h_pct": round(vol2x, 2),
                "cluster": None, "pattern": None, "confidence": min(conf, 80),
                "setup_type": "inverse_down", "direction": "DOWN",
                "entry_zone": [round(px * 0.999), round(px * 1.004)],
                "support": None,
                "target_band": [round(px * (1 + t_lo / 100)), round(px * (1 + t_hi / 100))],
                "target_pct": [t_lo, t_hi], "stop": round(px * (1 - s_pct / 100)),
                "stop_pct": s_pct, "time_min": TIME_MIN, "state": "ACT_NOW", "ai_1h_prob": ai,
                "why_ko": (f"시장 확정 하락 (KODEX200 15분 {r15:+.2f}% · 45분 {r45:+.2f}% · 2시간 평균 아래"
                           + (f" · 하이닉스 AI {ai}%" if ai is not None else "")
                           + ") → 인버스 매수 = 하락에서 수익"),
                "why_en": (f"confirmed market slide (KODEX200 15m {r15:+.2f}% · 45m {r45:+.2f}% · below 2h avg"
                           + (f" · Hynix AI {ai}%" if ai is not None else "")
                           + ") → buying the inverse earns from the fall")}
    except Exception:
        db.rollback()
        return None


_scan_cache: dict[str, Any] = {"t": 0.0, "v": None}


def scan(db, use_cache: bool = True) -> dict[str, Any]:
    """Scan our ~40 collected stocks (dip + momentum, deep) PLUS the day's market-wide top
    gainers (light momentum). 45s cache so the chatbot is fast (the market-wide Naver
    fetch is slow); the cron passes use_cache=False to always compute fresh for grading."""
    import time as _t
    if use_cache and _scan_cache["v"] is not None and _t.time() - _scan_cache["t"] < 45:
        return _scan_cache["v"]
    uni = _universe(db)
    name_of = dict(uni)
    ranked = _prescan(db, [c for c, _ in uni])
    top = ranked[:_DEEP_SCAN]
    results = []
    for code in top:
        try:
            results.append(scan_one(db, code, name_of.get(code, code)))
        except Exception:
            db.rollback()
    act = [r for r in results if r["state"] == "ACT_NOW"]
    forming = [r for r in results if r["state"] == "FORMING"]
    nothing = [r for r in results if r["state"] == "NOTHING"]

    # MARKET-WIDE top gainers (the boss's point: hunt the whole market, not just our 40)
    scanned_extra = 0
    try:
        in_our_uni = {c for c, _ in uni}
        cand = [c for c in _top_gainer_codes() if c not in in_our_uni]
        gnames = {}
        if cand:
            from sqlalchemy import text
            nr = db.execute(text("SELECT code, name FROM krx_stocks WHERE code = ANY(:c)"),
                            {"c": cand}).fetchall()
            gnames = {str(r[0]): r[1] for r in nr}
        # REAL listed equities only — krx_stocks excludes ELW/ETN derivatives + odd codes
        # that dominate the raw gainer leaderboard (dangerous to chase).
        gainers = [c for c in cand if c in gnames][:12]
        for c in gainers:
            scanned_extra += 1
            m = _scan_mover(db, c, gnames[c])
            if m:
                act.append(m)
    except Exception:
        db.rollback()

    # 📉 DOWN setup — a confirmed market slide makes the INVERSE ETF a buy candidate
    try:
        d = _scan_down(db)
        if d:
            act.append(d)
    except Exception:
        db.rollback()

    # M5.6 RANKING VOICE (2026-07-08): the 1-hour model's P(up) ranks candidates and is
    # shown transparently. It does NOT gate/veto — its measured skill (+12pp over base
    # on 62 unseen days) is real but below the solo-trading bar; as a ranker it only
    # improves WHICH candidate we surface first. Promotion gate in services/hourly_model.
    for bucket in (act, forming):
        for s in bucket:
            try:
                from services.hourly_model import prob_up_1h
                p = prob_up_1h(db, s["code"])
                if p is not None:
                    s["ai_1h_prob"] = round(p * 100)
            except Exception:
                pass
    act.sort(key=lambda r: (-(r.get("ai_1h_prob") or 50), -r["confidence"]))
    forming.sort(key=lambda r: (-(r.get("ai_1h_prob") or 50), -r["confidence"]))
    out = {"act_now": act, "forming": forming, "nothing": nothing,
           "scanned": len(uni) + scanned_extra, "deep_scanned": len(results),
           "counts": {"act": len(act), "forming": len(forming), "nothing": len(nothing)}}
    _scan_cache["t"], _scan_cache["v"] = _t.time(), out
    return out


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
    r = scan(db, use_cache=False)
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


# plain-word labels for the peer-group jargon (non-technical readers)
_CLUSTER_KO = {"SECTOR_DOWN": "그룹 동반하락", "CONFIRM_UP": "그룹 동반상승",
               "LAGGARD_UP": "그룹은 오르는데 뒤처짐", "HOLDING_VS_WEAK": "그룹 약한데 버팀",
               "LONE_MOVE": "혼자만 움직임", "NEUTRAL": "그룹과 비슷"}
_CLUSTER_EN = {"SECTOR_DOWN": "sector falling together", "CONFIRM_UP": "sector rising together",
               "LAGGARD_UP": "lagging a rising group", "HOLDING_VS_WEAK": "holding up vs a weak group",
               "LONE_MOVE": "moving alone", "NEUTRAL": "in line with its group"}


def scan_reply(db, lang: str = "ko") -> str:
    """The chatbot answer for '지금 뭐 살까 / what should I trade now' — ACT / FORMING /
    NOTHING in the zone format. Identical structure KO/EN, both surfaces."""
    r = scan(db)
    ko = lang != "en"
    act, forming = r["act_now"], r["forming"]
    L: list[str] = []

    _tlabel_ko = {"dip": "눌림목 반등", "momentum": "상승추세(모멘텀)"}
    _tlabel_en = {"dip": "dip-bounce", "momentum": "momentum"}
    if act:
        s = act[0]
        tb = s["target_band"]
        _tk = _tlabel_ko.get(s.get("setup_type"), "")
        _te = _tlabel_en.get(s.get("setup_type"), "")
        if ko:
            L.append(f"🎯 **지금 좋은 자리 {len(act)}개 — {s['name']} ({s['code']}) · {_tk}**  확신 {s['confidence']}%")
            L += ["",
                  f"· ▲ 방향: 상승 (매수)",
                  f"· 진입: 지금 ~₩{_fmt(s['price'])} (₩{_fmt(s['entry_zone'][0])}–{_fmt(s['entry_zone'][1])})",
                  f"· 목표: +{s['target_pct'][0]}%~{s['target_pct'][1]}% (₩{_fmt(tb[0])}–{_fmt(tb[1])}) → 이 구간 닿으면 매도",
                  f"· 손절: −{s['stop_pct']}% (₩{_fmt(s['stop'])})",
                  f"· 시간: 최대 {s['time_min']}분",
                  *([f"· 🤖 AI 1시간 상승확률: {s['ai_1h_prob']}% (1년 학습 모델 · 참고용 — 랭킹에만 사용)"]
                    if s.get("ai_1h_prob") is not None else []),
                  f"· 근거: {s.get('why_ko','')}",
                  "",
                  f"👉 지금 진입 구간에서 매수 → +{s['target_pct'][0]}% 닿으면 익절 → 아니면 손절선에서 정리 → 둘 다 아니면 {s['time_min']}분 후 나오기."]
            if len(act) > 1:
                L.append(f"(추가 자리: {', '.join(a['name'] for a in act[1:])})")
        else:
            L.append(f"🎯 **{len(act)} good setup(s) now — {s['en_name']} ({s['code']}) · {_te}**  confidence {s['confidence']}%")
            L += ["",
                  f"· ▲ Direction: UP (buy)",
                  f"· Enter: now ~₩{_fmt(s['price'])} (₩{_fmt(s['entry_zone'][0])}–{_fmt(s['entry_zone'][1])})",
                  f"· Target: +{s['target_pct'][0]}%~{s['target_pct'][1]}% (₩{_fmt(tb[0])}–{_fmt(tb[1])}) → sell on first touch",
                  f"· Stop: −{s['stop_pct']}% (₩{_fmt(s['stop'])})",
                  f"· Exit by: {s['time_min']} min",
                  *([f"· 🤖 AI 1-hour up-probability: {s['ai_1h_prob']}% (1-year model · reference — ranking only)"]
                    if s.get("ai_1h_prob") is not None else []),
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

    # nothing — DETAILED per-stock breakdown of ALL scanned stocks (+ market context)
    all_stocks = r["nothing"]
    withpx = [s for s in all_stocks if s.get("price")]
    nodata = [s for s in all_stocks if not s.get("price")]
    scanned = r.get("deep_scanned", len(all_stocks))
    mkt = None
    try:
        from services.micro_trend import _market_chg_pct
        mkt = _market_chg_pct()
    except Exception:
        pass

    # BEST AVAILABLE candidate — the boss's ask: always show the ONE closest to buyable
    # (still NOT a buy — labeled honestly), ranked by how close it is to triggering.
    _gate_rank = {"nosetup": 4, "ml": 3, "news": 3, "vol": 2, "downtrend": 1, "sector": 0, "regime": 0}
    best = None
    if withpx:
        best = sorted(withpx, key=lambda s: (_gate_rank.get(s.get("gate"), 0), s.get("confidence", 0)),
                      reverse=True)[0]

    if ko:
        L.append("😌 **지금은 확실한 매수 자리 없음 — 관망(기다림) 권장**")
        if mkt is not None:
            _crash = " ⚠️ 급락일" if mkt <= -1.5 else ""
            L.append(f"_시장: 코스피 {mkt:+.2f}%{_crash} · 시장 전체 상위 포함 {scanned}개 스캔_")
        # PLAIN-LANGUAGE TL;DR (non-technical) — explain WHY, simply
        if mkt is not None and mkt <= -1.5:
            L += ["", f"💬 **쉽게 말하면:** 오늘은 시장 전체가 크게 떨어지는 날이에요(코스피 {mkt:+.1f}%). "
                  "이런 날에 주식을 사면 더 떨어질 확률이 높아요. 그래서 지금은 **안 사는 것**이 돈을 지키는 길이에요. "
                  "좋은 매수 자리는 시장이 안정되거나 오를 때 나옵니다."]
        else:
            L += ["", "💬 **쉽게 말하면:** 지금은 짧은 시간에 1~2% 오를 만한 **확실한 자리가 없어요.** "
                  "억지로 사면 잃기 쉬우니, 좋은 자리가 나올 때까지 기다리는 게 좋아요. "
                  "(하루에 0번 거래도 정답일 수 있어요.)"]
        if best:
            L += ["", f"🔎 **그나마 가장 가까운 후보 (아직 매수 신호 아님): {best['name']} ({best['code']}) ₩{_fmt(best['price'])}**",
                  f"   · 왜 아직 아닌가: {best.get('reason_ko','')}",
                  f"   · 매수 신호 조건: {best.get('path_ko') or best.get('trigger_ko','')}",
                  f"   ⚠️ 지금 사면 승산 낮음 — '가장 덜 나쁜' 것일 뿐, 조건 충족 전엔 관망 권장."]
        L += ["",
              "‘살 자리 없음’은 **가격이 안 움직인다는 뜻이 아니라**, 지금 +1.5~2%를 "
              "낮은 위험으로 노릴 **매수 타이밍이 아니라는 뜻**이에요. (눌림목 반등·상승추세 두 방식으로 탐색) "
              "가장 활발한 종목별 상태:"]
        for s in withpx:
            met = []
            if s.get("rsi") is not None:
                met.append(f"RSI {s['rsi']}")
            if s.get("vol_1h_pct") is not None:
                met.append(f"변동성 ±{s['vol_1h_pct']}%/h")
            if s.get("cluster"):
                met.append(_CLUSTER_KO.get(s["cluster"], s["cluster"]))
            mets = " · ".join(met)
            L.append(f"\n**· {s['name']}** ₩{_fmt(s['price'])}{('  ('+mets+')') if mets else ''}")
            L.append(f"   상태: {s['reason_ko']}")
            if s.get("path_ko"):
                L.append(f"   ▶ 매수 조건: {s['path_ko']}")
        if nodata:
            L.append(f"\n(데이터 준비 중: {', '.join(s['name'] for s in nodata)})")
        L += ["",
              "💡 **왜 지금 안 좋은가:** 활발한 종목들도 (a) 변동성이 부족하거나 (b) 하락 추세라 눌림 반등 실패 위험이 "
              "크거나 (c) 강한 상승 흐름이 아직 아니에요. 억지 매수는 손실로 이어집니다 — 지금은 쉬는 게 정답.",
              "⏱️ **다시 확인할 때:** 어떤 종목이 상승 추세로 강하게 오르거나(모멘텀), 눌렸다가 반등을 시작하면 "
              "매수 자리가 뜹니다. 보통 **30분~1시간 뒤** 다시 물어보세요 (스캐너가 자동 감지도 함)."]
    else:
        L.append("😌 **No clear BUY setup right now — better to wait (sit out)**")
        if mkt is not None:
            _crash = " ⚠️ crash day" if mkt <= -1.5 else ""
            L.append(f"_Market: KOSPI {mkt:+.2f}%{_crash} · scanned {scanned} incl. market-wide movers_")
        # PLAIN-LANGUAGE TL;DR (non-technical)
        if mkt is not None and mkt <= -1.5:
            L += ["", f"💬 **In simple words:** today the whole market is falling hard (KOSPI {mkt:+.1f}%). "
                  "On days like this, if you buy, the stock is more likely to keep dropping — so **not buying** "
                  "is how you protect your money right now. Good buy setups appear when the market steadies or rises."]
        else:
            L += ["", "💬 **In simple words:** right now there's **no solid spot** likely to rise 1–2% in a short "
                  "time. Forcing a trade tends to lose, so it's better to wait for a good setup. "
                  "(Zero trades in a day can be the right answer.)"]
        if best:
            L += ["", f"🔎 **Closest candidate (NOT a buy yet): {best['en_name']} ({best['code']}) ₩{_fmt(best['price'])}**",
                  f"   · Why not yet: {best.get('reason_en','')}",
                  f"   · Turns into a BUY when: {best.get('path_en') or best.get('trigger_en','')}",
                  f"   ⚠️ Buying now has poor odds — it's just the 'least-bad' one; wait for the condition."]
        L += ["",
              "‘No setup’ does **not** mean prices won't move — it means this isn't a "
              "**good moment to buy** for a low-risk +1.5~2% (checked both dip-bounce & "
              "momentum). Status of the most active stocks:"]
        for s in withpx:
            met = []
            if s.get("rsi") is not None:
                met.append(f"RSI {s['rsi']}")
            if s.get("vol_1h_pct") is not None:
                met.append(f"vol ±{s['vol_1h_pct']}%/h")
            if s.get("cluster"):
                met.append(_CLUSTER_EN.get(s["cluster"], s["cluster"]))
            mets = " · ".join(met)
            L.append(f"\n**· {s['en_name']}** ₩{_fmt(s['price'])}{('  ('+mets+')') if mets else ''}")
            L.append(f"   status: {s['reason_en']}")
            if s.get("path_en"):
                L.append(f"   ▶ becomes a buy when: {s['path_en']}")
        if nodata:
            L.append(f"\n(data warming up: {', '.join(s['en_name'] for s in nodata)})")
        L += ["",
              "💡 **Why it's not good now:** even the active stocks either (a) lack the volatility "
              "to reach +1.5% in an hour, (b) are in a downtrend (dip likely keeps falling), or "
              "(c) aren't yet in a strong enough uptrend. Forcing a trade loses — sitting out is right.",
              "⏱️ **When to check again:** a setup appears when a stock rises strongly (momentum) or "
              "dips then starts bouncing. Ask again in **30–60 min** (the scanner also auto-detects it)."]
    return "\n".join(L)
