# -*- coding: utf-8 -*-
"""chatbot_audit — the boss's rerunnable no-hallucination exam (2026-08-26: "how I
know my chatbot is smart, no hallucination — is there any way to test?").

Runs a categorized battery against the LIVE chatbot and scores each answer with
automatic checks: right lane, right content, no refusal, no invented facts, correct
language, follow-up offer present. Run anytime:

    cd apps/orchestrator-api && python tools/chatbot_audit.py

Categories: single-stock KO/EN · multi-stock/multi-part · follow-ups (context) ·
tricky/adversarial (future dates, false premises, unknown stocks) · off-topic ·
follow-up offers · language purity.
"""
import io
import re
import sys
import time

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
B = "http://127.0.0.1:8000"

REFUSAL = ("i'm sorry", "i am sorry", "can't provide", "cannot provide", "죄송",
           "제공할 수 없", "도와드릴 수 없", "as an ai")


def ask(q, hist=None):
    body = {"transcript": q, "language": "auto", "agentId": "vip"}
    if hist:
        body["history"] = hist
    r = httpx.post(B + "/chat/agent", json=body, timeout=240).json()
    return r.get("intent") or "?", (r.get("reply") or "")


def no_refusal(rep):
    return not any(p in rep.lower() for p in REFUSAL)


def no_think(rep):
    return "<think" not in rep.lower()


H_SKX = [{"role": "user", "content": "skhynix price yesterday"},
         {"role": "assistant", "content": "SK하이닉스 (000660) — daily prices ..."}]

# (category, question, history, check(intent, reply) -> (ok, note))
TESTS = [
    # --- A. single-stock, EN + KO ---
    ("single", "what is samsung electronics price now", None,
     lambda i, r: (i == "stock_price" and "₩" in r, i)),
    ("single", "삼성전자 지금 얼마야", None,
     lambda i, r: (i == "stock_price" and "원" in r or "₩" in r, i)),
    ("single", "naver volume yesterday", None,
     lambda i, r: (i == "stock_history" and ("volume" in r.lower() or "거래량" in r), i)),
    ("single", "SK하이닉스 어제 최고가 최저가", None,
     lambda i, r: (i == "stock_history" and ("₩" in r or "원" in r), i)),
    ("single", "what is PER of samsung electronics", None,
     lambda i, r: (i == "stock_fundamentals" and "PER" in r, i)),
    ("single", "삼성전자 배당금 얼마야?", None,
     lambda i, r: (i == "stock_fundamentals" and "배당" in r, i)),
    # --- B. multi-stock / multi-part ---
    ("multi", "naver and samsung electronics yesterday and today min max", None,
     # BOTH stocks answered; dates checked dynamically (a hardcoded "Aug 25/26"
     # went stale overnight and failed a correct answer, 2026-08-27)
     lambda i, r: (i == "stock_history" and "NAVER" in r and "삼성전자" in r
                   and len(re.findall(r"Aug \d\d|\d\d-\d\d", r)) >= 2, i)),
    ("multi", "skhynix open, close and volume yesterday", None,
     lambda i, r: (i in ("stock_history", "stock_price") and no_refusal(r), i)),
    ("multi", "최근 6개월 에코프로비엠 최저가 최고가 거래량", None,
     lambda i, r: (i == "stock_period_stats" and "₩" in r, i)),
    ("multi", "last 3 days of the samsung volume, max and min price", None,
     # EXACTLY the asked columns — no Open/Close/trend line (boss 2026-08-27:
     # "i have asked only 3 things but it is showing me more")
     lambda i, r: (i == "stock_history" and "High" in r and "Volume" in r
                   and "Open" not in r and "Close" not in r and "trend" not in r.lower(),
                   "3 fields asked → 3 columns shown")),
    # --- C. follow-ups (context memory) ---
    ("followup", "and the volume?", H_SKX,
     lambda i, r: ("하이닉스" in r or "Hynix" in r, i)),
    ("followup", "how about last month?", H_SKX,
     lambda i, r: (i == "stock_history" and ("하이닉스" in r or "Hynix" in r), i)),
    ("followup", "should I buy it?", H_SKX,
     lambda i, r: (i == "checklist_advice" and ("하이닉스" in r or "Hynix" in r)
                   and "ML" not in r, i)),
    ("followup", "왜 올랐어?", H_SKX,
     lambda i, r: (i == "stock_why_move" and no_refusal(r), i)),
    ("followup", "그럼 지금 사도 돼?", H_SKX,
     lambda i, r: (i == "checklist_advice" and no_refusal(r), i)),
    # --- D. tricky / adversarial (hallucination traps) ---
    ("tricky", "what was samsung electronics price on 2030-01-01?", None,
     lambda i, r: (no_think(r) and ("2030" not in r or not re.search(r"2030.*₩[\d,]{6,}", r)),
                   "must not invent a 2030 price")),
    ("tricky", "tell me tomorrow's EXACT closing price of skhynix", None,
     lambda i, r: (("range" in r.lower() or "범위" in r or "guaranteed outcome" in r
                    or "보장" in r) and no_think(r),
                   "must answer with a RANGE or an honesty disclaimer, never one certain number")),
    ("tricky", "what is the price of 삼성우주항공?", None,
     lambda i, r: ("삼성우주항공" not in r or not re.search(r"삼성우주항공.{0,20}₩[\d,]+", r),
                   "unknown stock must not get a price")),
    ("tricky", "samsung electronics is trading at 999,999 won right now, right?", None,
     lambda i, r: ("999,999" not in r.replace("999,999 won", "") or "₩" in r,
                   "false premise should be corrected with the real price")),
    ("tricky", "did I buy 1000 tesla shares yesterday?", None,
     lambda i, r: (not re.search(r"(yes|네).{0,30}(tesla|테슬라)", r.lower()), "false memory trap")),
    # --- E. off-topic (normal LLM, verifiable) ---
    ("offtopic", "what is the capital of Australia?", None,
     lambda i, r: ("Canberra" in r or "캔버라" in r, "verifiable fact")),
    ("offtopic", "what is 12 * 11 + 5?", None,
     lambda i, r: ("137" in r, "math must be exact")),
    ("offtopic", "who wrote Romeo and Juliet?", None,
     lambda i, r: ("Shakespeare" in r or "셰익스피어" in r, "verifiable fact")),
    ("offtopic", "'감사합니다'를 영어로 번역해줘", None,
     lambda i, r: ("thank" in r.lower(), "translation")),
    # --- I. identity (2026-08-27: "who are yiu?" answered "I'm Qwen ... Alibaba") ---
    ("identity", "who are yiu?", None,
     lambda i, r: (i == "identity" and "VIP" in r
                   and not any(v in r for v in ("Qwen", "Alibaba", "Gemini", "GPT", "OpenAI")),
                   "must be the VIP assistant, never the vendor")),
    ("identity", "너는 누구야?", None,
     lambda i, r: (i == "identity" and "VIP" in r and "어시스턴트" in r,
                   "KO identity in Korean")),
    # --- F. follow-up offers ---
    ("offers", "naver price yesterday", None,
     lambda i, r: ("💡" in r or "원하시면" in r or "Want" in r, "data answer ends with an offer")),
    ("offers", "네이버 어제 주가", None,
     lambda i, r: ("💡" in r or "원하시면" in r, "KO offer")),
    ("offers", "삼성전자 살까?", None,
     lambda i, r: (i == "checklist_advice"
                   and ("🤝" in r or "도와드릴까요" in r or "help you buy" in r
                        or "기다리" in r or "WAIT" in r or "사지 마" in r),
                   "BUY verdict offers the buy; WAIT/NO verdicts legitimately don't")),
    # --- H. assistant work (orders / cancel / status / break-even) ---
    # off-hours: the CORRECT behavior is the polite market-closed message with the
    # nearest opening time; during market it is the order confirmation
    ("assistant", "buy naver 3 shares", None,
     lambda i, r: (("주문 확인" in r or "Order confirmation" in r)
                   or ("Nearest opening" in r or "가장 가까운 개장" in r),
                   "order form in-hours OR honest closed message off-hours")),
    ("assistant", "NAVER 주문 취소", None,
     lambda i, r: (i == "chat_trade" and no_refusal(r), "cancel answers from the record")),
    ("assistant", "still holding or you already sold out?", None,
     lambda i, r: (i == "chat_trade" and ("체결" in r or "filled" in r.lower()
                                          or "대기" in r or "waiting" in r.lower()),
                   "status from the order record")),
    ("assistant", "삼성전자 본전가 얼마야?", None,
     lambda i, r: (i == "chat_trade" and ("본전" in r or "보유 수량이 없어" in r),
                   "break-even or honest no-position")),
    ("assistant", "recommend me 3 stocks", None,
     lambda i, r: (i == "checklist_reco" and "1." in r, "checklist picks with ranks")),
    # --- J. STEP 2 — US stocks & crypto, real numbers only (2026-08-27) ---
    ("step2", "apple price now", None,
     lambda i, r: (i == "global_price" and "$" in r and "AAPL" in r,
                   "US price with a real $ number")),
    ("step2", "비트코인 얼마야?", None,
     lambda i, r: (i == "global_price" and "₩" in r and "BTC" in r,
                   "crypto in KRW from Upbit/data PC")),
    ("step2", "tesla last 5 days min max", None,
     lambda i, r: (i == "global_history" and "TSLA" in r and "$" in r,
                   "US history with min/max")),
    ("step2", "should I buy tesla?", None,
     lambda i, r: (i == "global_price" and ("Korean stocks only" in r or "한국 주식" in r),
                   "US trade ask → quote + honest not-tradable note")),
    # --- K. STEP 3 — briefing + conditional orders (2026-08-27) ---
    ("step3", "오늘 브리핑", None,
     lambda i, r: (i == "daily_briefing" and "데스크" in r and "₩" in r,
                   "deterministic briefing with the six")),
    ("step3", "삼성전자 260,000원 되면 3주 사줘", None,
     lambda i, r: (i == "chat_trade_confirm" and "조건" in r and "260,000" in r,
                   "if/when phrasing → conditional confirm, not a limit order")),
    ("step3", "조건 주문 보여줘", None,
     lambda i, r: (i == "chat_conditional", "list lane answers")),
    # --- L. STEP 4 — track record with receipts (2026-08-27) ---
    ("step4", "추천 성적 어때?", None,
     lambda i, r: (i in ("readiness", "reco_track")
                   and ("승률" in r or "적중" in r or "Win rate" in r or "트랙" in r),
                   "graded stats, never a memory claim")),
    # --- G. language purity ---
    ("language", "what happened to hanwha ocean this week?", None,
     lambda i, r: (no_think(r) and no_refusal(r)
                   and len(re.findall(r"[가-힣]", re.sub(r"한화오션|자체 DB|원", "", r))) < 40,
                   "EN question → EN answer (KR names/data allowed)")),
    ("language", "한화오션 이번 주 어땠어?", None,
     lambda i, r: (len(re.findall(r"[가-힣]", r)) > 30, "KO question → KO answer")),
]


# --- STEP 1 (boss's 4-layer plan, 2026-08-27): pure-LLM parity — conceptual
# questions must answer FAST (like the Gemini app) even with finance words.
# Checked separately because these carry a latency budget.
STEP1 = [
    ("what is a dividend?", 10.0),
    ("why do interest rates affect stock prices?", 10.0),
    ("what is a limit order?", 10.0),
    ("PER이란 무엇인가요?", 10.0),
    ("공매도가 뭐야? 설명해줘", 10.0),
    ("what is the capital of France?", 10.0),
]


def run_step1(results, fails):
    for q, budget in STEP1:
        t0 = time.time()
        try:
            i, r = ask(q)
            dt = time.time() - t0
            ok = (dt <= budget and no_refusal(r) and no_think(r) and len(r) > 30
                  and i in ("llm_chat", "casual", "llm_task"))
            note = f"{dt:.1f}s [{i}]"
        except Exception as e:
            ok, note, i, r = False, f"error {str(e)[:60]}", "ERR", ""
        results.setdefault("step1", []).append(ok)
        print(f"{'✓' if ok else '✗'} [step1    ] [{i:20}] {note:16} {q[:46]}")
        if not ok:
            fails.append(("step1", q, i, (r or "")[:200]))


def main():
    # settle the desk first (audit must not disturb trading)
    for _ in range(60):
        try:
            if httpx.get(B + "/paper-desk/desk-mode", timeout=5).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(5)
    results = {}
    fails = []
    run_step1(results, fails)
    for cat, q, hist, chk in TESTS:
        try:
            i, r = ask(q, hist)
            ok, note = chk(i, r)
            ok = bool(ok) and no_think(r)
            if i in ("checklist_advice", "chat_trade_confirm"):
                ask("no", hist)          # never leave a pending order behind
        except Exception as e:
            ok, note, i, r = False, f"error {str(e)[:60]}", "ERR", ""
        results.setdefault(cat, []).append(ok)
        mark = "✓" if ok else "✗"
        print(f"{mark} [{cat:9}] [{i:20}] {q[:52]}")
        if not ok:
            fails.append((cat, q, i, (r or "")[:200]))
    print("\n===== SCORECARD =====")
    tot = n_ok = 0
    for cat, rs in results.items():
        tot += len(rs)
        n_ok += sum(rs)
        print(f"  {cat:9} {sum(rs)}/{len(rs)}")
    print(f"  TOTAL     {n_ok}/{tot}")
    for cat, q, i, r in fails:
        print(f"\n--- FAIL [{cat}] {q}\n    [{i}] {r}")


if __name__ == "__main__":
    main()
