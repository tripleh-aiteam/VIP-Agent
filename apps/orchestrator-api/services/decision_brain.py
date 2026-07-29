"""🧠 Unified decision brain — the transparent scoreboard (boss 2026-07-16).

We already fuse ~10 signals inside decision_agent.decide() (with track-record
weighting + confidence gates). What was missing: the boss couldn't SEE the
vote. This renders decide()'s `signals_breakdown` as a plain scoreboard —
verdict + confidence + who voted BUY, who voted SELL, and how heavily each
counted — and adds a TIMING layer (live candle state + overnight stats) shown
SEPARATELY from the 5-day direction, because those are shorter-horizon signals
and mixing horizons would pollute the gate (the same discipline decide() keeps
for the cycle strategy).

One call → one explainable answer, identical on both bots, KO or EN.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_VOTE_KO = {"BUY": "매수", "SELL": "매도", "HOLD": "중립"}
_VOTE_EN = {"BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD"}
_ICON = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}


def _timing_layer(db, code: str, name: str) -> dict[str, Any]:
    """Short-horizon context: live 1-min candle state + overnight gap stats.
    Not day-decision voters — shown as a separate timing note."""
    out: dict[str, Any] = {}
    try:
        from services.scalp_trader import _streaks_1m
        up, dn, n = _streaks_1m(code)
        if n:
            out["candle"] = {"up": up, "dn": dn,
                             "signal": "BUY" if up >= 3 else "SELL" if dn >= 3 else "HOLD"}
    except Exception:
        pass
    return out


# ---- live point-in-time reads of the two scalp strategies (stateless, from
#      1-min candles) so the advice can show what ALL THREE algorithms say now ---- #
def _candle_now(code: str) -> tuple[str, str, str]:
    """Algorithm 3 · Candle verdict RIGHT NOW (3 up → buy, 3 down → sell)."""
    try:
        from services.scalp_trader import _streaks_1m
        up, dn, n = _streaks_1m(code)
        if not n:
            return "WAIT", "1분봉 데이터 대기", "waiting for 1-min data"
        if up >= 3:
            return "BUY", f"1분봉 {up}연속 양봉 → 매수 신호", f"{up} up 1-min candles → BUY"
        if dn >= 3:
            return "SELL", f"1분봉 {dn}연속 음봉 → 매도 신호", f"{dn} down 1-min candles → SELL"
        return "WAIT", f"양봉 {up}·음봉 {dn} — 3연속 대기", f"up {up}·down {dn} — waiting for 3 in a row"
    except Exception:
        return "WAIT", "데이터 없음", "no data"


def _ripple_now(code: str) -> tuple[str, str, str]:
    """Algo2 · Ripple verdict RIGHT NOW (bounce off a recent low) → (signal, ko, en)."""
    try:
        from services.scalp_trader import _candles_1m
        cs = _candles_1m(code, n=12)
        closes = [b.get("close") for b in cs if b.get("close")]
        if len(closes) < 4:
            return "WAIT", "1분봉 데이터 대기", "waiting for 1-min data"
        lo = min(closes)
        if lo <= 0:
            return "WAIT", "데이터 없음", "no data"
        bounce = (closes[-1] / lo - 1) * 100
        rising = closes[-1] > closes[-2] > closes[-3]
        if rising and 0.10 <= bounce <= 0.45:
            return "BUY", f"저점 대비 +{bounce:.2f}% 반등·연속 상승 → 매수", \
                   f"+{bounce:.2f}% off the low, rising → BUY"
        if bounce > 0.45:
            return "WAIT", f"이미 +{bounce:.2f}% 상승 — 추격 금지(관망)", \
                   f"already +{bounce:.2f}% up — no chase (wait)"
        return "WAIT", f"저점 대비 +{bounce:.2f}% — 반등 시작(+0.10%~) 대기", \
               f"+{bounce:.2f}% off the low — waiting for the turn (+0.10%~)"
    except Exception:
        return "WAIT", "데이터 없음", "no data"


_DIV = "━━━━━━━━━━━━━━━━━━━━"


def _ripple_detail(code: str, lang: str) -> list[str]:
    """Ripple's strategy + current live read + what triggers a buy (boss wants detail)."""
    en = str(lang or "").lower().startswith("en")
    sig, ko, en_r = _ripple_now(code)
    try:
        from services.scalp_trader import _candles_1m
        cs = _candles_1m(code, n=12)
        closes = [b.get("close") for b in cs if b.get("close")]
        cur = closes[-1] if closes else None
        lo = min(closes) if closes else None
        bounce = round((cur / lo - 1) * 100, 2) if (cur and lo) else None
    except Exception:
        bounce = None
    if en:
        out = ["   • How it works: buys when price lifts +0.10~0.45% off a recent low with "
               "consecutive rises → LETS THE WINNER RUN: once past +0.4% it rides up and sells "
               "only when it turns down (trailing ~0.3% off the peak, never below break-even) "
               "/ −1% hard stop / flat 15:18."]
        if bounce is not None:
            out.append(f"   • Right now: {bounce:+.2f}% off the recent low — "
                       + ("in the buy window, watching for the rise to confirm." if 0.10 <= bounce <= 0.45
                          else "already past the +0.45% chase limit, so it waits for a pullback." if bounce > 0.45
                          else "not enough of a bounce yet (needs +0.10%)."))
        else:
            out.append("   • Right now: 1-min data unavailable (Kiwoom feed) — waiting.")
        out.append("   • Buys when: a fresh +0.10~0.45% bounce starts. Best on choppy, ranging tape.")
        return out
    out = ["   • 작동 방식: 최근 저점에서 +0.10~0.45% 반등하며 연속 상승하면 매수 → 승리 라이딩: "
           "+0.4%를 넘으면 바로 팔지 않고 계속 상승하는 동안 보유, 고점에서 꺾일 때(고점 대비 약 "
           "0.3% 하락, 손익분기 아래로는 안 팜) 매도 / −1% 손절 / 15:18 정리."]
    if bounce is not None:
        out.append(f"   • 지금: 최근 저점 대비 {bounce:+.2f}% — "
                   + ("매수 구간, 상승 확정 대기 중." if 0.10 <= bounce <= 0.45
                      else "이미 +0.45% 추격 한도를 넘어 눌림을 기다립니다." if bounce > 0.45
                      else "아직 반등이 부족합니다(+0.10% 필요)."))
    else:
        out.append("   • 지금: 1분봉 데이터 없음(키움 피드) — 대기 중.")
    out.append("   • 매수 조건: 새로운 +0.10~0.45% 반등 시작. 박스권·출렁이는 장에서 유리.")
    return out


def _candle_detail(code: str, lang: str) -> list[str]:
    """Algorithm 3 candle strategy + current 1-min streak + what triggers buy/sell."""
    en = str(lang or "").lower().startswith("en")
    try:
        from services.scalp_trader import _streaks_1m
        up, dn, n = _streaks_1m(code)
    except Exception:
        up = dn = n = 0
    if en:
        out = ["   • How it works: 3 up 1-min candles → BUY · 3 down candles → SELL · "
               "−1% stop · flat 15:18. Also checks the partner stock + volume."]
        if n:
            out.append(f"   • Right now: {up} up / {dn} down candles in a row — "
                       + ("BUY signal is live." if up >= 3 else "SELL signal is live." if dn >= 3
                          else f"needs {3-up} more up candle(s) to buy." if up else "no clear streak yet."))
        else:
            out.append("   • Right now: 1-min candle data unavailable (Kiwoom feed) — waiting.")
        out.append("   • Buys when: 3 green 1-min candles in a row. Best on a clean, trending push.")
        return out
    out = ["   • 작동 방식: 1분봉 3연속 양봉 → 매수 · 3연속 음봉 → 매도 · −1% 손절 · "
           "15:18 정리. 짝꿍 종목과 거래량도 함께 확인합니다."]
    if n:
        out.append(f"   • 지금: 양봉 {up}개 / 음봉 {dn}개 연속 — "
                   + ("매수 신호 발생." if up >= 3 else "매도 신호 발생." if dn >= 3
                      else f"매수까지 양봉 {3-up}개 더 필요." if up else "뚜렷한 연속 흐름 없음."))
    else:
        out.append("   • 지금: 1분봉 데이터 없음(키움 피드) — 대기 중.")
    out.append("   • 매수 조건: 1분봉 3연속 양봉. 깔끔한 추세 상승에서 유리.")
    return out


def _crosscheck_now(a1_sig: str, rp_sig: str, cd_sig: str) -> tuple[str, str, str]:
    """Algorithm 4 · Cross-Check verdict from the other three (strict 3-agree)."""
    a1, rp, cd = (a1_sig or "").upper(), (rp_sig or "").upper(), (cd_sig or "").upper()
    n_buy = sum(1 for s in (a1, rp, cd) if s == "BUY")
    n_sell = sum(1 for s in (a1, rp, cd) if s == "SELL")
    if n_buy == 3:
        return "BUY", "3개 모두 매수 동의 → 교차검증 매수", "all 3 agree BUY → Cross-Check BUY"
    if n_sell == 3:
        return "SELL", "3개 모두 매도 동의 → 교차검증 매도", "all 3 agree SELL → Cross-Check SELL"
    return ("WAIT", f"동의 부족 (매수 {n_buy}·매도 {n_sell}) → 대기",
            f"not unanimous (buy {n_buy}·sell {n_sell}) → WAIT")


def _x4_holds(db, code: str) -> bool:
    """Does Cross-Check (Algo 4) currently hold this stock? Cheap indexed read."""
    try:
        from sqlalchemy import text as _text
        return bool(db.execute(_text(
            "SELECT 1 FROM cross_trades WHERE ticker=:t AND status='OPEN' LIMIT 1"),
            {"t": str(code).zfill(6)}).first())
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False


def _crosscheck_detail(a1_sig: str, rp_sig: str, cd_sig: str, lang: str,
                       held: bool = False) -> list[str]:
    """Algorithm 4 · Cross-Check, in plain words: the three lights on one line, the
    agreement count + what it means, whether it's holding this stock (bilingual)."""
    en = str(lang or "").lower().startswith("en")
    a1, rp, cd = (a1_sig or "WAIT").upper(), (rp_sig or "WAIT").upper(), (cd_sig or "WAIT").upper()
    ve = {"BUY": "BUY", "SELL": "SELL", "WAIT": "WAIT", "HOLD": "WAIT"}
    vk = {"BUY": "매수", "SELL": "매도", "WAIT": "대기", "HOLD": "대기"}
    _w = (lambda s: ve.get(s, "WAIT")) if en else (lambda s: vk.get(s, "대기"))
    n_buy = sum(1 for s in (a1, rp, cd) if s == "BUY")
    n_sell = sum(1 for s in (a1, rp, cd) if s == "SELL")
    out: list[str] = []
    if en:
        out.append(f"   • The three lights: 🤖 Algo1 {_w(a1)} · ⚡ Ripple {_w(rp)} · 🕯️ Candle {_w(cd)}")
        if n_buy == 3:
            out.append("   • ALL 3 AGREE to buy — the strongest signal this system produces.")
        elif n_sell == 3:
            out.append("   • ALL 3 AGREE it's weak — the strongest 'avoid / get out' signal.")
        elif n_buy > 0:
            out.append(f"   • {n_buy} of 3 agree — not enough; Cross-Check only acts when all three line up.")
        else:
            out.append("   • None of the three wants to buy — Cross-Check just watches.")
        if held:
            out.append("   • Cross-Check is currently HOLDING this stock — now watching for when to sell.")
        out.append("   • It has no opinion of its own — it acts only when all three line up (highest-conviction, rarest signal).")
    else:
        out.append(f"   • 세 개의 불빛: 🤖 알고1 {_w(a1)} · ⚡ 잔물결 {_w(rp)} · 🕯️ 캔들 {_w(cd)}")
        if n_buy == 3:
            out.append("   • 셋 다 매수에 동의 — 이 시스템이 낼 수 있는 가장 강한 신호예요.")
        elif n_sell == 3:
            out.append("   • 셋 다 약하다고 동의 — 가장 강한 '피하라 / 빠져나와라' 신호예요.")
        elif n_buy > 0:
            out.append(f"   • 셋 중 {n_buy}개만 동의 — 아직 부족해요. 교차검증은 셋이 다 맞을 때만 움직여요.")
        else:
            out.append("   • 셋 다 매수 신호가 없어요 — 교차검증은 그냥 지켜봅니다.")
        if held:
            out.append("   • 교차검증이 지금 이 종목을 보유 중이에요 — 언제 팔지 지켜보고 있어요.")
        out.append("   • 스스로 판단하지 않아요 — 셋이 다 맞을 때만 움직여요 (가장 확신 높고 드문 신호).")
    return out


def _algo1_synthesis(d: dict[str, Any], lang: str) -> str:
    """2-3 sentence plain explanation of WHY Algorithm 1 reached its decision."""
    en = str(lang or "").lower().startswith("en")
    sigs = [s for s in (d.get("signals_breakdown") or []) if s.get("vote") != "HOLD"]
    nb = sum(1 for s in sigs if s["vote"] == "BUY")
    ns = sum(1 for s in sigs if s["vote"] == "SELL")
    dec = (d.get("decision") or "HOLD").upper()
    tech = d.get("technicals") or {}
    flows = d.get("flows") or {}
    an = d.get("method2_analysis") or {}
    # Use the English order-book/box reasons in EN mode — the KO list leaked raw Korean
    # ('외국인+기관 분산매도 …') into the English decision summary (boss 2026-07-29: KO/EN parity).
    reasons = (((an.get("reasons_en") if en else an.get("reasons")) or an.get("reasons")) or [])[:3]
    tsum = tech.get("summary_en" if en else "summary_ko") or ""
    ftag = (flows.get("tag_en") if en else flows.get("tag")) or ""
    if en:
        head = (f"{nb} method(s) lean buy, {ns} lean sell. ")
        why = (f"Chart: {tsum}. " if tsum else "") + (f"Flows: {ftag}. " if ftag else "")
        if reasons:
            why += "Order-book/box: " + "; ".join(reasons) + ". "
        concl = {"BUY": "The weight of evidence supports buying.",
                 "SELL": "The weight of evidence says reduce/avoid.",
                 "HOLD": "Signals conflict, so the brain waits for a clearer setup."}[dec]
        return "📌 Summary: " + head + why + concl
    head = f"매수 성향 {nb}개 · 매도 성향 {ns}개. "
    why = (f"차트: {tsum}. " if tsum else "") + (f"수급: {ftag}. " if ftag else "")
    if reasons:
        why += "호가/박스권: " + "; ".join(reasons) + ". "
    concl = {"BUY": "증거의 무게가 매수를 지지합니다.",
             "SELL": "증거의 무게가 비중 축소/회피를 가리킵니다.",
             "HOLD": "신호가 엇갈려 더 뚜렷한 자리를 기다립니다."}[dec]
    return "📌 종합 해설: " + head + why + concl


def _plain_advice(dec: str, conf: str, name: str, en: bool, transcript: str = None) -> str:
    """One short, jargon-free line that answers 'so what do I actually do?' in everyday
    words — shown right under the verdict, ABOVE the technical breakdown.

    It is FRAMED TO THE USER'S ACTUAL QUESTION: someone asking 'should I SELL my X?' is a
    holder, so a BUY signal is answered as 'don't sell / hold' (not 'buy'), and a stated
    expectation ('tomorrow looks down') is acknowledged instead of ignored — the previous
    version headlined 'buying' at a holder asking to sell, which read as illogical."""
    import re as _re
    s_en, s_ko = {"high": ("strong", "확실한"), "medium": ("fair", "어느 정도"),
                  "low": ("weak", "약한")}.get((conf or "low").lower(), ("weak", "약한"))
    # correct Korean object particle (을 after a final consonant, 를 after a vowel)
    _tail = (name or "").strip()[-1:]
    _obj = ("을" if (_tail and 0xAC00 <= ord(_tail) <= 0xD7A3 and (ord(_tail) - 0xAC00) % 28)
            else "를")
    t = (transcript or "").lower()
    asked_sell = bool(_re.search(r"\bsell\b|팔|매도|익절|처분", t))
    asked_buy = bool(_re.search(r"\bbuy\b|살까|사야|사도|매수|진입|담을", t))
    exp_down = bool(_re.search(r"decreas|going down|go down|\bdown\b|drop|fall|lower|"
                               r"떨어|하락|내려|빠질|약세|하방", t))

    # ---- HOLDER framing: user already owns it and is asking whether to SELL ----
    # (a BUY/HOLD signal must be answered as 'don't sell', never 'buy'.)
    if asked_sell and not asked_buy:
        if en:
            nod = ("You expect tomorrow to fall, and the very short term does look a little soft — "
                   "but " if exp_down else "")
            if dec == "SELL":
                return (f"💬 In plain words: {nod}{'yes' if nod else 'Yes'} — trimming or selling here "
                        f"is reasonable; the signals do lean down ({s_en} conviction). If you'd rather "
                        "not exit fully, sell part and keep a stop-loss on the rest.")
            reason = ("the bigger-picture signals still lean UP (a buy signal)" if dec == "BUY"
                      else "there's no strong reason to sell")
            return (f"💬 In plain words: {nod}I would **not** sell here: {reason}, so holding makes "
                    f"more sense than selling ({s_en} conviction). If a near-term dip worries you, "
                    "sell only a portion or set a stop-loss rather than exiting everything.")
        nod = ("내일 하락을 예상하시고 아주 단기(다음 몇 시간)도 다소 약해 보이지만, " if exp_down else "")
        if dec == "SELL":
            return (f"💬 쉬운 설명: {nod}네 — 지금 일부라도 정리(매도)하는 건 합리적입니다. 신호가 약세 "
                    f"쪽입니다(확신 {s_ko}). 전량 매도가 부담되면 일부만 팔고 나머지는 손절선을 걸어두세요.")
        reason = "큰 흐름의 신호는 여전히 **상승 쪽(매수 신호)**이라" if dec == "BUY" else "뚜렷이 팔 이유가 없어서"
        return (f"💬 쉬운 설명: {nod}지금 파는 건 권하지 않습니다 — {reason} 매도보다 **보유**가 낫습니다"
                f"(확신 {s_ko}). 단기 하락이 걱정되면 전량 매도 대신 일부만 팔거나 손절선을 걸어두세요.")

    # ---- BUYER framing / neutral ----
    if dec == "BUY":
        nod_en = "Even though you're watching for a possible dip, " if exp_down else ""
        nod_ko = "내일 조정 가능성을 보고 계시지만, " if exp_down else ""
        return (f"💬 In plain words: {nod_en}the signs lean toward **buying** {name}, with {s_en} "
                "conviction. If you'll hold for a few days this is a reasonable time to buy; if "
                "you want a better price, wait for a small dip. Put in only money you can afford "
                "to lose, and set a stop-loss so a wrong call stays cheap." if en else
                f"💬 쉬운 설명: {nod_ko}지금은 {name}{_obj} **사는 쪽**으로 신호가 기울어 있고, 확신은 "
                f"{s_ko} 수준입니다. 며칠 들고 갈 거면 지금 사도 괜찮고, 더 싸게 사고 싶으면 살짝 눌릴 때를 "
                "기다리세요. 잃어도 되는 돈만 넣고, 틀렸을 때 손실이 작도록 손절선을 꼭 정해두세요.")
    if dec == "SELL":
        return (f"💬 In plain words: the signs lean toward **selling / taking profit** on {name} "
                f"({s_en} conviction). If you own it, consider trimming or stepping out; if you "
                "don't own it, just stay on the sidelines for now." if en else
                f"💬 쉬운 설명: 지금은 {name}{_obj} **파는(차익 실현) 쪽**으로 신호가 기울어 있습니다"
                f"(확신 {s_ko}). 가지고 있다면 일부라도 정리하는 걸 고려하고, 없다면 지금은 그냥 지켜보세요.")
    return (f"💬 In plain words: there's **no clear buy-or-sell signal** for {name} yet — the "
            "smart move is to **wait and watch**. Buying now would be a guess; let the signals "
            "line up first before you act." if en else
            f"💬 쉬운 설명: 아직 {name}에 대한 **뚜렷한 매수·매도 신호가 없습니다** — 지금은 "
            "**기다리며 지켜보는 것**이 현명합니다. 지금 사는 건 사실상 찍기예요. 신호가 한 방향으로 "
            "모일 때까지 기다리세요.")


def clean_recommendation(db, d: dict[str, Any], lang: str = "ko", transcript: str = None) -> str:
    """The boss's exact recommendation layout (2026-07-20): ONE-line final decision →
    Algorithm 1 (decision + 1h prediction + ML/news/YT/chart/Kiwoom/orderbook/wave
    detail) → Algorithm 2 Ripple (decision + detail) → Algorithm 2 Candle (decision
    + detail) → final answer from the 3 cases. Clean and short — nothing more."""
    en = str(lang or "").lower().startswith("en")
    code = str(d.get("ticker") or "").zfill(6)
    name = d.get("name") or code
    dec = (d.get("decision") or "HOLD").upper()
    conf = d.get("confidence") or "low"
    conf_ko = {"high": "높음", "medium": "보통", "low": "낮음"}.get(conf, "낮음")
    vk = {"BUY": "매수", "SELL": "매도", "HOLD": "보유/관망", "WAIT": "대기"}
    ve = {"BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD", "WAIT": "WAIT"}
    ic = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪", "WAIT": "⚪"}

    def _v(x):
        return (ve if en else vk).get(x, x)

    ml = d.get("method1_ml") or {}
    an = d.get("method2_analysis") or {}
    wv = d.get("method3_wave") or {}
    news = d.get("news") or {}
    flows = d.get("flows") or {}
    tech = d.get("technicals") or {}
    yt = d.get("youtube") or {}
    setup = d.get("intraday_setup") or {}
    tier = d.get("hourly_tier") or {}
    ai1h = setup.get("ai_1h_prob")
    if ai1h is None:                 # boss wants Algo-1's 1-hour prediction shown always
        try:
            from services.hourly_model import prob_up_1h
            _pu = prob_up_1h(db, code)
            if _pu is not None:
                ai1h = round(float(_pu) * 100) if _pu <= 1 else round(float(_pu))
        except Exception:
            ai1h = None

    def _pl(v):
        try:
            return f"{int(v):,}"
        except Exception:
            return "-"

    def _sc(x):
        s = x.get("score") if isinstance(x, dict) else x
        return "🟢" if (s or 0) > 0 else "🔴" if (s or 0) < 0 else "⚪"

    L: list[str] = []
    if en:
        # 1) one-line final
        L.append(f"✅ Our final decision: **{ve.get(dec, dec)}** — {name} (confidence {conf})")
        L.append(_plain_advice(dec, conf, name, True, transcript))   # plain-words lead, question-aware
        # 2) Algorithm 1
        L.append(_DIV)
        ml_call = (ml.get("call") or "HOLD").upper()
        h1 = f" · 1-hour prediction: {'UP' if (ai1h or 0) >= 50 else 'DOWN'} {ai1h}%" if ai1h is not None else ""
        L.append(f"🤖 Algorithm 1 — combined brain (ML · News · YouTube · Chart · Kiwoom · Orderbook · Wave)")
        L.append(f"Decision: {ic.get(dec,'⚪')} {ve.get(dec,dec)}{h1}")
        L.append("Detail:")
        L.append(f" • ML (M1): {_v(ml_call)} — accuracy {ml.get('accuracy_pct','n/a')}%"
                 + (f", 5-day ±{abs(ml['expected_move_pct'])}%" if ml.get('expected_move_pct') is not None else ""))
        L.append(f" • Wave (Elliott/Fibonacci): {_v((wv.get('verdict') or 'HOLD').upper())}"
                 + (f" — entry ₩{_pl(wv.get('entry'))}/stop ₩{_pl(wv.get('stop'))}/target ₩{_pl(wv.get('target'))}" if wv.get('entry') else ""))
        L.append(f" • Chart/Technicals: {_sc(tech)} " + (tech.get("summary_en") or "neutral")
                 + (f" (support ₩{_pl(tech.get('support'))} · resistance ₩{_pl(tech.get('resistance'))})" if tech.get('support') else ""))
        L.append(f" • Kiwoom supply/demand: {_sc(flows)} " + (flows.get("tag_en") or flows.get("tag") or "neutral"))
        L.append(f" • News: {_sc(news)} " + (f"{news.get('count',0)} items" if news.get('count') else "neutral"))
        L.append(f" • YouTube: {_sc(yt)} " + (f"{yt.get('count',0)} mentions" if yt.get('count') else "neutral"))
        L.append(_algo1_synthesis(d, lang))
        # 3) Ripple
        rp_sig, rp_ko, rp_en = _ripple_now(code)
        L.append(_DIV)
        L.append("⚡ Algorithm 2 · Ripple (scalp, minutes)")
        L.append(f"Decision: {ic.get(rp_sig,'⚪')} {ve.get(rp_sig,rp_sig)} — {rp_en}")
        L.extend(_ripple_detail(code, lang))
        # 4) Candle
        cd_sig, cd_ko, cd_en = _candle_now(code)
        L.append(_DIV)
        L.append("🕯️ Algorithm 3 · Candle (1-min chart)")
        L.append(f"Decision: {ic.get(cd_sig,'⚪')} {ve.get(cd_sig,cd_sig)} — {cd_en}")
        L.extend(_candle_detail(code, lang))
        # 4b) Cross-Check (Algorithm 4) — consensus of the three above
        x4_sig, _x4_ko, x4_en = _crosscheck_now(dec, rp_sig, cd_sig)
        _held4 = _x4_holds(db, code)
        L.append(_DIV)
        L.append("🔀 Algorithm 4 · Cross-Check (3-agree)")
        L.append(f"Decision: {ic.get(x4_sig,'⚪')} {ve.get(x4_sig,x4_sig)} — {x4_en}")
        L.extend(_crosscheck_detail(dec, rp_sig, cd_sig, lang, held=_held4))
        # 5) final from all 4
        L.append(_DIV)
        L.append("🎯 Final answer (from all 4 algorithms):")
        L.append(_synthesis_en(dec, rp_sig, cd_sig, name))
    else:
        L.append(f"✅ 최종 결정: **{vk.get(dec, dec)}** — {name} (신뢰도 {conf_ko})")
        L.append(_plain_advice(dec, conf, name, False, transcript))   # plain-words lead, question-aware
        L.append(_DIV)
        ml_call = (ml.get("call") or "HOLD").upper()
        h1 = f" · 1시간 예측: {'상승' if (ai1h or 0) >= 50 else '하락'} {ai1h}%" if ai1h is not None else ""
        L.append("🤖 알고리즘 1 — 종합 브레인 (ML · 뉴스 · 유튜브 · 차트 · 키움 · 호가 · 파동)")
        L.append(f"결정: {ic.get(dec,'⚪')} {vk.get(dec,dec)}{h1}")
        L.append("상세 설명:")
        L.append(f" • 머신러닝(M1): {_v(ml_call)} — 정확도 {ml.get('accuracy_pct','n/a')}%"
                 + (f", 5일 예상 ±{abs(ml['expected_move_pct'])}%" if ml.get('expected_move_pct') is not None else ""))
        L.append(f" • 파동(엘리엇/피보나치): {_v((wv.get('verdict') or 'HOLD').upper())}"
                 + (f" — 진입 ₩{_pl(wv.get('entry'))}/손절 ₩{_pl(wv.get('stop'))}/목표 ₩{_pl(wv.get('target'))}" if wv.get('entry') else ""))
        L.append(f" • 차트/기술: {_sc(tech)} " + (tech.get("summary_ko") or "중립")
                 + (f" (지지 ₩{_pl(tech.get('support'))} · 저항 ₩{_pl(tech.get('resistance'))})" if tech.get('support') else ""))
        L.append(f" • 키움 수급: {_sc(flows)} " + (flows.get("tag") or "중립"))
        L.append(f" • 뉴스: {_sc(news)} " + (f"{news.get('count',0)}건" if news.get('count') else "중립"))
        L.append(f" • 유튜브: {_sc(yt)} " + (f"{yt.get('count',0)}건 언급" if yt.get('count') else "중립"))
        L.append(_algo1_synthesis(d, lang))
        rp_sig, rp_ko, rp_en = _ripple_now(code)
        L.append(_DIV)
        L.append("⚡ 알고리즘 2 · 잔물결 (초단타·분 단위)")
        L.append(f"결정: {ic.get(rp_sig,'⚪')} {vk.get(rp_sig,rp_sig)} — {rp_ko}")
        L.extend(_ripple_detail(code, lang))
        cd_sig, cd_ko, cd_en = _candle_now(code)
        L.append(_DIV)
        L.append("🕯️ 알고리즘 3 · 캔들 (1분봉)")
        L.append(f"결정: {ic.get(cd_sig,'⚪')} {vk.get(cd_sig,cd_sig)} — {cd_ko}")
        L.extend(_candle_detail(code, lang))
        # 4b) 교차검증 (알고리즘 4) — 위 세 알고리즘의 합의
        x4_sig, x4_ko, _x4_en = _crosscheck_now(dec, rp_sig, cd_sig)
        _held4 = _x4_holds(db, code)
        L.append(_DIV)
        L.append("🔀 알고리즘 4 · 교차검증 (3개 동의)")
        L.append(f"결정: {ic.get(x4_sig,'⚪')} {vk.get(x4_sig,x4_sig)} — {x4_ko}")
        L.extend(_crosscheck_detail(dec, rp_sig, cd_sig, lang, held=_held4))
        L.append(_DIV)
        L.append("🎯 종합 최종 답변 (4가지 종합):")
        L.append(_synthesis_ko(dec, rp_sig, cd_sig, name))
    return "\n".join(L)


# ===================================================================
#  🔮 PREDICTION VIEW — the boss (2026-07-20) wants a "predict at time"
#  question to show each algorithm's DIRECTIONAL FORECAST (up/down/flat +
#  why), with NO buy/sell/hold wording. Buy/sell/hold belongs ONLY to an
#  advice question ("should I buy?"). Same raw signals, different framing.
# ===================================================================

def _dir_label(direction: str, en: bool) -> str:
    return {"UP": "📈 UP" if en else "📈 상승",
            "DOWN": "📉 DOWN" if en else "📉 하락",
            "FLAT": "➡️ Sideways" if en else "➡️ 보합"}.get(direction, direction)


def _won(v) -> str:
    try:
        return f"₩{int(round(float(v))):,}"
    except Exception:
        return "-"


def _round_tick(v) -> int:
    """Round to a clean KRX-ish tick so the interval reads nicely."""
    v = float(v)
    step = 1000 if v >= 100000 else 100 if v >= 10000 else 10 if v >= 1000 else 1
    return int(round(v / step) * step)


def _price_band(price, pct, direction):
    """Predicted price interval (low, high): a ±pct% band around `price`, SKEWED
    toward the predicted direction (mostly upside if UP, mostly downside if DOWN).
    Returns (low, high) rounded to clean ticks, or None."""
    try:
        price = float(price); d = abs(float(pct))
    except (TypeError, ValueError):
        return None
    if not price or not d:
        return None
    if direction == "UP":
        lo, hi = price * (1 - d / 3 / 100), price * (1 + d / 100)
    elif direction == "DOWN":
        lo, hi = price * (1 - d / 100), price * (1 + d / 3 / 100)
    else:
        lo, hi = price * (1 - d / 100), price * (1 + d / 100)
    return (_round_tick(lo), _round_tick(hi))


def _band_str(band, en: bool) -> str:
    if not band:
        return ""
    return f"{_won(band[0])} – {_won(band[1])}"


def _band_pct(band, price) -> str:
    """' (−4.2% ~ +1.4%)' — the interval expressed as % vs current price (boss wants %)."""
    try:
        price = float(price)
        lo_p = (float(band[0]) / price - 1) * 100
        hi_p = (float(band[1]) / price - 1) * 100
        return f" ({lo_p:+.1f}% ~ {hi_p:+.1f}%)"
    except Exception:
        return ""


def _band_full(band, price, en: bool) -> str:
    """Price interval WITH the % range: '₩1,690,000 – ₩1,789,000 (−4.2% ~ +1.4%)'."""
    if not band:
        return ""
    return _band_str(band, en) + _band_pct(band, price)


def _algo1_pred(d: dict[str, Any], db, code: str, en: bool):
    """Algorithm 1 directional prediction from the 1-hour up-probability + ML
    expected move + chart read. Returns (direction, ai1h, lines, daily_pct).
    NO buy/sell language — pure forecast. Includes a price interval."""
    ml = d.get("method1_ml") or {}
    setup = d.get("intraday_setup") or {}
    tech = d.get("technicals") or {}
    price = d.get("price")
    ai1h = setup.get("ai_1h_prob")
    if ai1h is None:
        try:
            from services.hourly_model import prob_up_1h
            _pu = prob_up_1h(db, code)
            if _pu is not None:
                ai1h = round(float(_pu) * 100) if _pu <= 1 else round(float(_pu))
        except Exception:
            ai1h = None
    exp = ml.get("expected_move_pct")
    if ai1h is not None:
        direction = "UP" if ai1h >= 53 else "DOWN" if ai1h <= 47 else "FLAT"
    elif exp is not None:
        direction = "UP" if exp > 0.3 else "DOWN" if exp < -0.3 else "FLAT"
    else:
        sc = (tech.get("score") or 0)
        direction = "UP" if sc > 0 else "DOWN" if sc < 0 else "FLAT"
    # near-term (≈1-day) magnitude: scale the 5-day ML move down by √5, clamp sane.
    daily_pct = None
    if exp is not None:
        daily_pct = min(6.0, max(0.6, abs(exp) / (5 ** 0.5)))
    lines = []
    if ai1h is not None:
        lines.append(f"1-hour prediction: {'UP' if ai1h >= 50 else 'DOWN'} — {ai1h}% chance of rising."
                     if en else f"1시간 예측: {'상승' if ai1h >= 50 else '하락'} — 상승확률 {ai1h}%.")
    if exp is not None:
        ml_band = _price_band(price, abs(exp), "FLAT")   # ML gives a symmetric ± band
        rng = f" → {_band_str(ml_band, en)}" if ml_band else ""
        lines.append(f"5-day ML forecast: expected move ±{abs(exp)}%{rng} (accuracy {ml.get('accuracy_pct','n/a')}%)."
                     if en else f"5일 ML 예측: 예상 변동폭 ±{abs(exp)}%{rng} (정확도 {ml.get('accuracy_pct','n/a')}%).")
    tsum = tech.get("summary_en" if en else "summary_ko")
    if tsum:
        lines.append(f"Chart read: {tsum}." if en else f"차트 판독: {tsum}.")
    if not lines:
        lines.append("no directional data available." if en else "방향성 데이터 없음.")
    return direction, ai1h, lines, daily_pct


def _ripple_pred(code: str, en: bool):
    """Ripple's short-term directional forecast (no buy/sell)."""
    try:
        from services.scalp_trader import _candles_1m
        cs = _candles_1m(code, n=12)
        closes = [b.get("close") for b in cs if b.get("close")]
        if len(closes) < 4:
            return "FLAT", ("1-min data unavailable — no short-term read." if en
                            else "1분봉 데이터 없음 — 단기 예측 불가.")
        lo = min(closes); cur = closes[-1]
        bounce = (cur / lo - 1) * 100 if lo > 0 else 0
        rising = closes[-1] > closes[-2] > closes[-3]
        if rising and bounce <= 0.45:
            return "UP", (f"rising off the recent low (+{bounce:.2f}%), a small upward drift is likely next few minutes."
                          if en else f"최근 저점 대비 +{bounce:.2f}% 반등·연속 상승 → 향후 수 분 소폭 상승 예상.")
        if bounce > 0.45:
            return "DOWN", (f"already +{bounce:.2f}% up and extended — a short pullback is more likely next."
                            if en else f"이미 +{bounce:.2f}% 상승·과열 — 단기 눌림(하락) 가능성이 큼.")
        return "FLAT", (f"only +{bounce:.2f}% off the low, no clear micro-trend — sideways for now."
                        if en else f"저점 대비 +{bounce:.2f}%뿐 — 뚜렷한 단기 흐름 없음(보합).")
    except Exception:
        return "FLAT", ("no data" if en else "데이터 없음")


def _candle_pred(code: str, en: bool):
    """Candle (1-min streak) short-term momentum forecast (no buy/sell)."""
    try:
        from services.scalp_trader import _streaks_1m
        up, dn, n = _streaks_1m(code)
        if not n:
            return "FLAT", ("1-min candles unavailable — no read." if en else "1분봉 데이터 없음 — 예측 불가.")
        if up >= 3:
            return "UP", (f"{up} up candles in a row — upward momentum, the short-term push likely continues."
                          if en else f"양봉 {up}연속 — 상승 모멘텀, 단기 상승 흐름 지속 가능.")
        if dn >= 3:
            return "DOWN", (f"{dn} down candles in a row — downward momentum short-term."
                            if en else f"음봉 {dn}연속 — 하락 모멘텀, 단기 하락 가능.")
        if up > dn:
            return "UP", (f"{up} up vs {dn} down candles — a mild upward tilt (not yet 3 in a row)."
                          if en else f"양봉 {up} vs 음봉 {dn} — 약한 상승 우위(아직 3연속은 아님).")
        if dn > up:
            return "DOWN", (f"{dn} down vs {up} up candles — a mild downward tilt."
                            if en else f"음봉 {dn} vs 양봉 {up} — 약한 하락 우위.")
        return "FLAT", (f"{up} up / {dn} down — balanced, sideways."
                        if en else f"양봉 {up}·음봉 {dn} — 균형, 보합.")
    except Exception:
        return "FLAT", ("no data" if en else "데이터 없음")


def prediction_view(db, d: dict[str, Any], lang: str = "ko"):
    """🔮 PREDICTION layout: each algorithm's DIRECTIONAL forecast + why. No
    buy/sell/hold — that's for advice questions. Returns (block_text, dirs)."""
    en = str(lang or "").lower().startswith("en")
    code = str(d.get("ticker") or "").zfill(6)
    name = d.get("name") or code
    price = d.get("price")
    a1_dir, ai1h, a1_lines, daily_pct = _algo1_pred(d, db, code, en)
    rp_dir, rp_txt = _ripple_pred(code, en)
    cd_dir, cd_txt = _candle_pred(code, en)

    # combined near-term predicted PRICE INTERVAL (boss 2026-07-20: "show from this to
    # this price"). Magnitude = Algo-1's ≈1-day move (ML/√5), fallback 1.5%; skewed by
    # the combined direction (Algo-1 leads, scalps confirm).
    dmag = daily_pct if daily_pct else 1.5
    combo_dir = a1_dir
    if a1_dir == "FLAT":
        combo_dir = rp_dir if rp_dir != "FLAT" else cd_dir
    combo_band = _price_band(price, dmag, combo_dir)
    # per-scalp bands (only when they have a live directional read; tiny minute moves)
    rp_band = _price_band(price, 0.4, rp_dir) if rp_dir != "FLAT" else None
    cd_band = _price_band(price, 0.6, cd_dir) if cd_dir != "FLAT" else None

    L: list[str] = []
    if en:
        L.append(f"🔮 **Prediction — {name}**" + (f" (now ₩{int(price):,})" if price else ""))
        if combo_band:
            L.append(f"📊 **Expected price range (near-term): {_band_full(combo_band, price, True)}**")
        L.append(_DIV)
        L.append("🤖 Algorithm 1 — combined brain (ML · News · YouTube · Chart · Kiwoom · Orderbook · Wave)")
        L.append(f"Predicts: {_dir_label(a1_dir, True)}"
                 + (f" · range {_band_full(combo_band, price, True)}" if combo_band else ""))
        L.extend(f"   • {ln}" for ln in a1_lines)
        L.append(_DIV)
        L.append("⚡ Algorithm 2 · Ripple (scalp · minutes)")
        L.append(f"Predicts: {_dir_label(rp_dir, True)}"
                 + (f" · next-minutes range {_band_full(rp_band, price, True)}" if rp_band else ""))
        L.append(f"   • {rp_txt}")
        L.append(_DIV)
        L.append("🕯️ Algorithm 3 · Candle (1-min chart)")
        L.append(f"Predicts: {_dir_label(cd_dir, True)}"
                 + (f" · next-minutes range {_band_full(cd_band, price, True)}" if cd_band else ""))
        L.append(f"   • {cd_txt}")
        # 🔀 Cross-Check — how much the three FORECASTS agree (conviction only;
        # PURE FORECAST language — never buy/sell/hold, boss 2026-07-20 rule).
        _n_up = sum(1 for x in (a1_dir, rp_dir, cd_dir) if x == "UP")
        _n_dn = sum(1 for x in (a1_dir, rp_dir, cd_dir) if x == "DOWN")
        L.append(_DIV)
        L.append("🔀 Cross-Check — how much the three agree")
        if _n_up == 3 or _n_dn == 3:
            _w = "up" if _n_up == 3 else "down"
            L.append(f"   • All 3 point the SAME way ({_w}) — the strongest agreement, so HIGHER confidence in this direction.")
        elif max(_n_up, _n_dn) == 2:
            _w = "up" if _n_up == 2 else "down"
            L.append(f"   • 2 of 3 lean the same way ({_w}) — some agreement, not full; treat the direction as likely, not certain.")
        else:
            L.append(f"   • The three don't agree (up {_n_up} · down {_n_dn} of 3) — mixed signals, so expect a flat, sideways, uncertain move.")
    else:
        L.append(f"🔮 **예측 — {name}**" + (f" (현재 ₩{int(price):,})" if price else ""))
        if combo_band:
            L.append(f"📊 **예상 가격 범위 (단기): {_band_full(combo_band, price, False)}**")
        L.append(_DIV)
        L.append("🤖 알고리즘 1 — 종합 브레인 (ML · 뉴스 · 유튜브 · 차트 · 키움 · 호가 · 파동)")
        L.append(f"예측: {_dir_label(a1_dir, False)}"
                 + (f" · 범위 {_band_full(combo_band, price, False)}" if combo_band else ""))
        L.extend(f"   • {ln}" for ln in a1_lines)
        L.append(_DIV)
        L.append("⚡ 알고리즘 2 · 잔물결 (초단타 · 분 단위)")
        L.append(f"예측: {_dir_label(rp_dir, False)}"
                 + (f" · 수 분 내 범위 {_band_full(rp_band, price, False)}" if rp_band else ""))
        L.append(f"   • {rp_txt}")
        L.append(_DIV)
        L.append("🕯️ 알고리즘 3 · 캔들 (1분봉)")
        L.append(f"예측: {_dir_label(cd_dir, False)}"
                 + (f" · 수 분 내 범위 {_band_full(cd_band, price, False)}" if cd_band else ""))
        L.append(f"   • {cd_txt}")
        # 🔀 교차검증 — 세 예측의 의견 일치도 (확신도만 · 순수 예측 언어, 매매어 금지)
        _n_up = sum(1 for x in (a1_dir, rp_dir, cd_dir) if x == "UP")
        _n_dn = sum(1 for x in (a1_dir, rp_dir, cd_dir) if x == "DOWN")
        L.append(_DIV)
        L.append("🔀 교차검증 — 세 의견이 얼마나 일치하나")
        if _n_up == 3 or _n_dn == 3:
            _w = "상승" if _n_up == 3 else "하락"
            L.append(f"   • 셋 다 같은 방향({_w})을 봅니다 — 의견 일치가 가장 강해서 이 방향에 대한 확신이 더 높습니다.")
        elif max(_n_up, _n_dn) == 2:
            _w = "상승" if _n_up == 2 else "하락"
            L.append(f"   • 셋 중 2개가 같은 방향({_w})입니다 — 어느 정도 일치하지만 완전하지는 않아, 그 방향을 유력하게 보되 확정은 아닙니다.")
        else:
            L.append(f"   • 세 방향이 엇갈립니다 (상승 {_n_up}·하락 {_n_dn}/3) — 신호가 섞여 있어 뚜렷한 방향 없이 옆으로 움직일 가능성이 큽니다.")
    return "\n".join(L), {"a1": a1_dir, "rp": rp_dir, "cd": cd_dir, "ai1h": ai1h,
                          "range": combo_band}


def _agree_clause_ko(a1: str, rp: str, cd: str) -> str:
    """Cross-Check agreement count as a conviction note (boss 2026-07-22)."""
    n_buy = sum(1 for s in (a1, rp, cd) if s == "BUY")
    n_sell = sum(1 for s in (a1, rp, cd) if s == "SELL")
    if n_buy == 3:
        return " 게다가 교차검증도 3/3 모두 매수에 동의 — 가장 강한 확인이라 이 판단의 확신이 크게 높아집니다."
    if n_sell == 3:
        return " 게다가 교차검증도 3/3 모두 약하다고 봐서 — 가장 강한 경고입니다."
    if n_buy == 2:
        return " 교차검증은 3개 중 2개가 매수 쪽 — 같은 방향으로 기울지만 완전한 확인은 아직입니다."
    if n_buy == 1:
        return " 교차검증은 3개 중 1개만 매수라 확신은 약한 편이에요 (셋이 다 맞아야 가장 강합니다)."
    return " 교차검증 기준으로는 셋 다 매수 신호가 없어 확신은 낮습니다."


def _agree_clause_en(a1: str, rp: str, cd: str) -> str:
    n_buy = sum(1 for s in (a1, rp, cd) if s == "BUY")
    n_sell = sum(1 for s in (a1, rp, cd) if s == "SELL")
    if n_buy == 3:
        return " On top of that, the cross-check agrees 3/3 — the strongest confirmation, which makes this the highest-conviction case."
    if n_sell == 3:
        return " On top of that, the cross-check agrees 3/3 that it's weak — the strongest warning."
    if n_buy == 2:
        return " The cross-check has 2 of 3 leaning to buy — same direction, but not full confirmation yet."
    if n_buy == 1:
        return " The cross-check has only 1 of 3 to buy, so conviction is on the weaker side (all three lining up is the strongest)."
    return " By the cross-check, none of the three wants to buy, so conviction is low."


def _synthesis_ko(a1: str, rp: str, cd: str, name: str) -> str:
    return _synthesis_ko_base(a1, rp, cd, name) + _agree_clause_ko(a1, rp, cd)


def _synthesis_ko_base(a1: str, rp: str, cd: str, name: str) -> str:
    scalp_buy = rp == "BUY" or cd == "BUY"
    scalp_sell = rp == "SELL" or cd == "SELL"
    if a1 == "BUY" and scalp_buy:
        return f"중기(알고1)와 단기 신호가 모두 매수 → 지금 진입 자리로 좋습니다."
    if a1 == "BUY" and not scalp_buy:
        return f"중기(알고1)는 매수지만 단기 진입 타이밍은 대기 중 → 며칠 보유 관점이면 지금 매수 가능, 초단타면 눌림 후 진입하세요."
    if a1 == "SELL" and scalp_sell:
        return f"중기·단기 모두 매도/하락 신호 → 보유 중이면 정리, 신규 매수는 피하세요."
    if a1 == "SELL":
        return f"중기(알고1)는 매도 우세 → 신규 매수는 권하지 않습니다. 단기 반등이 있어도 리스크가 큽니다."
    # HOLD anchor
    if scalp_buy:
        return f"중기(알고1)는 관망이나 단기 전략에서 매수 신호 → 초단타 진입은 가능하되 짧게, 손절 -1% 지키세요."
    return f"세 방법 모두 뚜렷한 신호가 없어 관망이 최종 결론입니다 — 자리가 잡히면 다시 물어보세요."


def _synthesis_en(a1: str, rp: str, cd: str, name: str) -> str:
    return _synthesis_en_base(a1, rp, cd, name) + _agree_clause_en(a1, rp, cd)


def _synthesis_en_base(a1: str, rp: str, cd: str, name: str) -> str:
    scalp_buy = rp == "BUY" or cd == "BUY"
    scalp_sell = rp == "SELL" or cd == "SELL"
    if a1 == "BUY" and scalp_buy:
        return "Mid-term (Algo 1) and short-term signals both say BUY → a good entry right now."
    if a1 == "BUY" and not scalp_buy:
        return "Algo 1 (mid-term) says BUY but the short-term timing is still waiting → buy now if you'll hold days; if scalping, wait for a dip to enter."
    if a1 == "SELL" and scalp_sell:
        return "Both mid- and short-term point down → trim if you hold, avoid new buys."
    if a1 == "SELL":
        return "Algo 1 (mid-term) leans SELL → a new buy isn't advised; short-term bounces carry real risk."
    if scalp_buy:
        return "Algo 1 is neutral but a short-term strategy fires BUY → a quick scalp is possible, keep it small with a −1% stop."
    return "No clear signal from any of the three — HOLD is the conclusion. Ask again once a setup forms."


def three_algo_block(db, d: dict[str, Any], lang: str = "ko") -> str:
    """Show what ALL THREE approaches say (boss 2026-07-20: don't answer from Algo 1
    only). 🤖 Algorithm 1 = the daily 5-day decide() verdict; ⚡ Ripple + 🕯️ Candle
    = the two Algorithm-2 scalp strategies read live off the 1-min chart."""
    en = str(lang or "").lower().startswith("en")
    code = str(d.get("ticker") or "").zfill(6)
    a1 = (d.get("decision") or "HOLD").upper()
    a1_conf = d.get("confidence") or "low"
    rp_sig, rp_ko, rp_en = _ripple_now(code)
    cd_sig, cd_ko, cd_en = _candle_now(code)
    ic = _ICON
    if en:
        conf_e = {"high": "high", "medium": "medium", "low": "low"}.get(a1_conf, a1_conf)
        return (
            "🧭 What each algorithm says right now:\n"
            f"  🤖 Algorithm 1 (daily · 5-day, chart+수급+news+ML): {ic.get(a1,'⚪')} {a1} (confidence {conf_e})\n"
            f"  ⚡ Algorithm 2 · Ripple (scalp · minutes): {ic.get(rp_sig,'⚪')} {rp_sig} — {rp_en}\n"
            f"  🕯️ Algorithm 3 · Candle (1-min): {ic.get(cd_sig,'⚪')} {cd_sig} — {cd_en}\n"
            "  → Different horizons: Algo 1 = hold days; Ripple/Candle = in-and-out in minutes. "
            "They can disagree on purpose — pick the one that matches how long you'll hold.")
    conf_k = {"high": "높음", "medium": "보통", "low": "낮음"}.get(a1_conf, a1_conf)
    _vk = _VOTE_KO
    return (
        "🧭 지금 각 알고리즘의 판단:\n"
        f"  🤖 알고리즘 1 (일봉·5일, 차트+수급+뉴스+ML): {ic.get(a1,'⚪')} {_vk.get(a1,a1)} (신뢰도 {conf_k})\n"
        f"  ⚡ 알고리즘 2 · 잔물결 (초단타·분 단위): {ic.get(rp_sig,'⚪')} {_vk.get(rp_sig,rp_sig)} — {rp_ko}\n"
        f"  🕯️ 알고리즘 3 · 캔들 (1분봉): {ic.get(cd_sig,'⚪')} {_vk.get(cd_sig,cd_sig)} — {cd_ko}\n"
        "  → 시간 지평이 다릅니다: 알고1 = 며칠 보유 / 잔물결·캔들 = 분 단위 진입·청산. "
        "서로 다를 수 있어요 — 얼마나 들고 갈지에 맞는 걸 고르세요.")


def scoreboard(db, d: dict[str, Any], lang: str = "ko", with_timing: bool = True) -> str:
    """Render the 🧠 vote scoreboard HEADER from an already-computed decide() dict.
    No re-run — callers that already have the decide result prepend this."""
    en = str(lang or "").lower().startswith("en")
    code = str(d.get("ticker") or "").zfill(6)
    name = d.get("name") or code
    decision = d.get("decision") or "HOLD"
    conf = d.get("confidence") or "low"
    conf_ko = {"high": "높음", "medium": "보통", "low": "낮음"}.get(conf, "낮음")
    sigs = [s for s in (d.get("signals_breakdown") or []) if s.get("vote") != "HOLD"]
    buys = sorted([s for s in sigs if s["vote"] == "BUY"],
                  key=lambda s: abs(s["contribution"]), reverse=True)
    sells = sorted([s for s in sigs if s["vote"] == "SELL"],
                   key=lambda s: abs(s["contribution"]), reverse=True)
    timing = _timing_layer(db, code, name) if with_timing else {}

    def _row(s, e):
        lab = s["en"] if e else s["ko"]
        w = s["weight"]
        return f"{_ICON[s['vote']]} {lab}" + (f" ×{w}" if abs(w - 1.0) > 0.01 else "")

    L: list[str] = []
    head = _VOTE_EN[decision] if en else _VOTE_KO[decision]
    score = d.get("score")
    if en:
        L.append(f"🧠 Smart decision — {name}: **{head}** (confidence {conf}, score {score:+})")
        L.append("📈 Based on real-time chart analysis (1-min / 5-min / daily) fused across "
                 "our algorithms — not a hunch. The vote:")
        if buys:
            L.append("🟢 Buy-side — " + ", ".join(_row(s, True) for s in buys[:6]))
        if sells:
            L.append("🔴 Sell-side — " + ", ".join(_row(s, True) for s in sells[:6]))
        if not buys and not sells:
            L.append("No signal leaned either way — balanced evidence.")
        if decision == "HOLD":
            L.append(f"→ HOLD: {len(buys)} signal(s) leaned buy, {len(sells)} leaned sell — "
                     "not enough agreement to act with confidence, so the brain waits. "
                     "Weights reflect each method's measured track record.")
        else:
            _bk = buys if decision == "BUY" else sells
            _op = sells if decision == "BUY" else buys
            L.append(f"→ {head}: {len(_bk)} signal(s) backed it, {len(_op)} opposed. "
                     "Weights reflect each method's measured track record — proven-strong "
                     "evidence counts more.")
        if timing.get("candle"):
            c = timing["candle"]
            ct = ("3+ up 1-min candles (short-term rising)" if c["signal"] == "BUY"
                  else "3 down 1-min candles (short-term falling)" if c["signal"] == "SELL"
                  else "no clear 1-min streak")
            L.append(f"⏱️ Timing now: {ct} — minutes-horizon, separate from the 5-day call above.")
    else:
        L.append(f"🧠 스마트 결정 — {name}: **{head}** (신뢰도 {conf_ko}, 점수 {score:+})")
        L.append("📈 실시간 차트 분석(1분봉·5분봉·일봉)을 여러 알고리즘으로 종합한 결과입니다 "
                 "— 감이 아니라 데이터 기반. 투표 내역:")
        if buys:
            L.append("🟢 매수 쪽 — " + ", ".join(_row(s, False) for s in buys[:6]))
        if sells:
            L.append("🔴 매도 쪽 — " + ", ".join(_row(s, False) for s in sells[:6]))
        if not buys and not sells:
            L.append("어느 쪽으로도 기운 신호가 없습니다 — 증거가 균형입니다.")
        if decision == "HOLD":
            L.append(f"→ 중립(관망): 매수 {len(buys)}개 · 매도 {len(sells)}개로 "
                     "확신 있게 움직일 만큼 합의가 안 돼 기다립니다. "
                     "가중치는 각 방법의 측정된 실적을 반영합니다.")
        else:
            _bk = buys if decision == "BUY" else sells
            _op = sells if decision == "BUY" else buys
            L.append(f"→ {head}: {len(_bk)}개 신호가 지지, {len(_op)}개가 반대. "
                     "가중치는 각 방법의 측정된 실적을 반영합니다 — 입증된 강한 증거가 더 무겁게 셉니다.")
        if timing.get("candle"):
            c = timing["candle"]
            ct = ("1분봉 3연속 이상 상승 (단기 상승세)" if c["signal"] == "BUY"
                  else "1분봉 3연속 하락 (단기 하락세)" if c["signal"] == "SELL"
                  else "뚜렷한 1분봉 흐름 없음")
            L.append(f"⏱️ 지금 타이밍: {ct} — 분 단위 신호로, 위의 5일 판단과는 별개입니다.")
    return "\n".join(L)


def unified_reply(db, code: str, name: str, lang: str = "ko") -> Optional[str]:
    """Standalone: run decide(), then scoreboard header + full reasoning underneath."""
    from services.decision_agent import decide
    en = str(lang or "").lower().startswith("en")
    try:
        d = decide(db, code)
    except Exception as e:
        logger.warning("decision_brain decide failed %s: %s", code, str(e)[:120])
        return None
    if not d:
        return None
    head = scoreboard(db, d, lang)
    body = d.get("reasoning_en" if en else "reasoning_ko")
    reply = head + (("\n\n" + body) if body else "")
    try:
        from services.call_grader import log_call
        _dec = d.get("decision") or "HOLD"
        log_call(db, ticker=code, action=_dec, intent="brain",
                 ref_price=d.get("price"), horizon_min=5 * 390, name=name, lang=lang)
    except Exception:
        pass
    return reply[:9000]
