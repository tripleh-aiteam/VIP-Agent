"""chatbot_regression.py — a fixed before/after test set for the assistant.

Run BEFORE and AFTER any chatbot change to PROVE it got better, not worse. Covers the 5
categories (present / past / multiple / follow-up / future) in BOTH EN + KO, plus off-topic
and an anti-hallucination guard. Checks are presence-based (live prices change), so they're
stable across runs. Saves a JSON snapshot so two runs can be diffed.

Usage (PC, with .env + .env.supabase):
    python tests/chatbot_regression.py                 # run + report + save snapshot
    python tests/chatbot_regression.py --tag after_rag # label this snapshot
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT.parent.parent / ".env")
load_dotenv(ROOT.parent.parent / ".env.supabase", override=True)

from db.base import SessionLocal           # noqa: E402
from services.assistant_agent import run_agent  # noqa: E402

SNAP_DIR = Path(r"C:\Users\TRIPLEH\AppData\Local\Temp\claude\c--Users-TRIPLEH-Desktop-VIP-Agent\d1a48da2-f4d3-44f4-8f21-74600ee7ca8f\scratchpad")


def has(rx, text):
    return bool(re.search(rx, text or "", re.I))


def is_table(text):
    return (text or "").count("|") >= 4          # markdown table


# Each case: id, category, language, question, optional setup (prior turn for follow-ups),
# and `checks` = list of (label, predicate(reply, intent)).
def C(cid, cat, lang, q, checks, setup=None):
    return {"id": cid, "cat": cat, "lang": lang, "q": q, "setup": setup, "checks": checks}


SRC = lambda r, i: has(r"naver|kiwoom|키움|네이버|출처|source|기준|as of", r)      # cites a source/time
NUM = lambda r, i: has(r"[\d,]{4,}", r)                                            # has a real number
TABLE = lambda r, i: is_table(r)
# ≥2 of the 3 monitored stocks present (by 6-digit code OR case-insensitive name)
MULTI = lambda r, i: (len([c for c in ("000660", "005930", "009150") if c in r]) >= 2
                      or sum(x in (r or "").lower() for x in ("hynix", "samsung", "하이닉스", "삼성전기", "삼성전자")) >= 2)

CASES = [
    # ---- PRESENT ----
    C("present_single", "PRESENT", "en", "What's SK Hynix trading at right now?",
      [("table", TABLE), ("number", NUM), ("source", SRC), ("intent", lambda r, i: i == "stock_price")]),
    C("present_single", "PRESENT", "ko", "SK하이닉스 지금 얼마야?",
      [("table", TABLE), ("number", NUM), ("source", SRC), ("intent", lambda r, i: i == "stock_price")]),
    C("present_multi", "PRESENT-MULTI", "en", "Compare SK Hynix, Samsung Electronics and Samsung Electro-Mechanics now.",
      [("table", TABLE), ("3 stocks", MULTI)]),
    C("present_multi", "PRESENT-MULTI", "ko", "SK하이닉스, 삼성전자, 삼성전기 지금 비교해줘.",
      [("table", TABLE), ("3 stocks", MULTI)]),
    # follow-up (stop) — must remember the stock + give real levels, not a bare guess
    C("present_followup", "PRESENT-FU", "en", "And the stop-loss?",
      [("levels", lambda r, i: has(r"stop|target|buy zone|support|₩[\d,]+", r)), ("not bare", lambda r, i: len(r) > 30)],
      setup="How's SK Hynix today?"),
    C("present_followup", "PRESENT-FU", "ko", "손절은 어디로?",
      [("levels", lambda r, i: has(r"손절|목표|매수|지지|[\d,]{5,}원", r)), ("not bare", lambda r, i: len(r) > 30)],
      setup="SK하이닉스 오늘 어때?"),

    # ---- PAST ----
    C("past_single", "PAST", "en", "What was Samsung Electronics' closing price on June 10?",
      [("date", lambda r, i: has(r"jun.*10|06-10|6.?10", r)), ("number", NUM),
       ("not today", lambda r, i: not has(r"2026-06-29|6월 29일", r)), ("intent", lambda r, i: i == "stock_history")]),
    C("past_single", "PAST", "ko", "삼성전자 6월 10일 종가 얼마였어?",
      [("date", lambda r, i: has(r"06-10|6.?10", r)), ("number", NUM),
       ("not today", lambda r, i: not has(r"6월 29일|2026-06-29", r)), ("intent", lambda r, i: i == "stock_history")]),
    C("past_multi", "PAST-MULTI", "en", "What were SK Hynix, Samsung Electronics and Samsung Electro-Mechanics on June 10?",
      [("number", NUM), ("multi", MULTI)]),
    C("past_multi", "PAST-MULTI", "ko", "SK하이닉스, 삼성전자, 삼성전기 6월 10일 종가 알려줘.",
      [("number", NUM), ("multi", MULTI)]),

    # ---- FUTURE ----
    C("future_single", "FUTURE", "en", "What's the 5-day outlook for SK Hynix?",
      [("method1", lambda r, i: has(r"method 1|machine learning|방법 1", r)),
       ("method2", lambda r, i: has(r"method 2|analysis|방법 2", r))]),
    C("future_single", "FUTURE", "ko", "SK하이닉스 앞으로 5일 전망은?",
      [("method1", lambda r, i: has(r"방법 1|머신러닝", r)), ("method2", lambda r, i: has(r"방법 2|분석", r))]),

    # ---- SWITCH FOLLOW-UP (inherits prior intent: price -> price, not a fresh analysis) ----
    C("switch_followup", "SWITCH-FU", "en", "how about naver?",
      [("price table", lambda r, i: i == "stock_price" and is_table(r)), ("naver", lambda r, i: has(r"naver|035420", r))],
      setup="what is the current price of SK Hynix?"),
    C("switch_followup", "SWITCH-FU", "ko", "그럼 네이버는?",
      [("price table", lambda r, i: i == "stock_price" and is_table(r)), ("naver", lambda r, i: has(r"naver|네이버|035420", r))],
      setup="SK하이닉스 지금 얼마야?"),

    # ---- MARKET FLOWS (who's buying — real Naver data, identical EN/KO) ----
    C("market_flow", "MFLOW", "en", "Who is buying the market today, foreign or institutions?",
      [("routed", lambda r, i: i in ("chain_completed", "market_flows")), ("number", NUM)]),
    C("market_flow", "MFLOW", "ko", "오늘 코스피 외국인 기관 순매수 어때?",
      [("routed", lambda r, i: i in ("chain_completed", "market_flows")), ("number", NUM)]),

    # ---- FUTURE soft-phrasing KO (어떻게 될까/N일 후 must = two-method, like EN 'outlook') ----
    C("future_soft", "FUTURE-SOFT", "ko", "SK하이닉스 5일 후 어떻게 될까?",
      [("method1", lambda r, i: has(r"방법 1|머신러닝|method 1", r)), ("method2", lambda r, i: has(r"방법 2|분석|method 2", r))]),
    C("future_soft", "FUTURE-SOFT", "en", "What will SK Hynix do over the next 5 days?",
      [("method1", lambda r, i: has(r"method 1|machine learning|방법 1", r)), ("method2", lambda r, i: has(r"method 2|analysis|방법 2", r))]),

    # ---- DECISION agent (both methods + news → BUY/HOLD/SELL, advice-style) ----
    C("decide", "DECIDE", "en", "Should I buy or sell SK Hynix?",
      [("recommendation", lambda r, i: has(r"Recommendation:\s*(BUY|HOLD|SELL)", r)),
       ("both methods", lambda r, i: has(r"Method 1", r) and has(r"Method 2", r) and has(r"News", r)),
       ("final para", lambda r, i: has(r"Final call|final recommendation is", r))]),
    C("decide", "DECIDE", "ko", "SK하이닉스 사야 할까?",
      [("recommendation", lambda r, i: has(r"추천:\s*(매수|보유|매도|관망)", r)),
       ("both methods", lambda r, i: has(r"방법 1", r) and has(r"방법 2", r) and has(r"뉴스", r)),
       ("final para", lambda r, i: has(r"최종 종합 판단|최종 추천은", r))]),

    # ---- ADVICE with a HOLDING marker ('last week I bought...') = POSITION advice (not history,
    # not a fresh decide). Routes to the P&L-aware position_advice; shows the 3 methods. ----
    C("advice_histmarker", "ADVICE-HX", "en", "Last week I bought SK Hynix at -4%, should I hold or sell?",
      [("advice route", lambda r, i: i in ("position_advice", "chain_completed")),
       ("not history", lambda r, i: i != "stock_history"),
       ("methods", lambda r, i: has(r"Method 1|방법 1|머신러닝|Machine Learning", r))]),
    C("advice_histmarker", "ADVICE-HX", "ko", "지난주 SK하이닉스 샀는데 보유할까 팔까?",
      [("advice route", lambda r, i: i in ("position_advice", "chain_completed")),
       ("not history", lambda r, i: i != "stock_history"),
       ("methods", lambda r, i: has(r"방법 1|머신러닝", r))]),

    # ---- OFF-TOPIC (should answer like a normal LLM, not refuse) ----
    C("offtopic", "OFFTOPIC", "en", "What's the capital of Uzbekistan?",
      [("answers", lambda r, i: has(r"tashkent|타슈켄트", r)), ("not refuse", lambda r, i: not has(r"can't help|cannot help|don't.*stock", r))]),
    C("offtopic", "OFFTOPIC", "ko", "우즈베키스탄 수도가 어디야?",
      [("answers", lambda r, i: has(r"tashkent|타슈켄트", r))]),
]


def run(tag=""):
    db = SessionLocal()
    results = []
    for c in CASES:
        hist = []
        if c["setup"]:
            r0 = run_agent(db, c["setup"], language=c["lang"], agent_id="vip", history=[])
            hist = [{"role": "user", "content": c["setup"]},
                    {"role": "assistant", "content": r0.get("reply", "")}]
        t0 = time.time()
        try:
            res = run_agent(db, c["q"], language=c["lang"], agent_id="vip", history=hist)
            reply, intent = res.get("reply") or "", res.get("intent") or ""
            err = None
        except Exception as e:
            reply, intent, err = "", "ERROR", str(e)[:120]
        checks = [(name, bool(fn(reply, intent))) for name, fn in c["checks"]]
        ok = all(p for _, p in checks) and not err
        results.append({"id": c["id"], "cat": c["cat"], "lang": c["lang"], "q": c["q"],
                        "intent": intent, "ok": ok, "checks": checks, "err": err,
                        "ms": int((time.time() - t0) * 1000), "reply": reply[:400]})
    passed = sum(1 for r in results if r["ok"])
    print(f"\n=== CHATBOT REGRESSION {('['+tag+']') if tag else ''} — {passed}/{len(results)} passed ===")
    for r in results:
        mark = "✅" if r["ok"] else "❌"
        fails = ",".join(n for n, p in r["checks"] if not p) + (f" ERR:{r['err']}" if r["err"] else "")
        print(f"{mark} [{r['lang']}] {r['cat']:<14} {r['q'][:42]:<42} intent={r['intent']:<16} {('FAIL: '+fails) if not r['ok'] else ''}")
    snap = SNAP_DIR / f"regression_{tag or 'snapshot'}.json"
    try:
        snap.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nsnapshot -> {snap}")
    except Exception as e:
        print("snapshot save failed:", str(e)[:80])
    return passed, len(results)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    p, n = run(a.tag)
    sys.exit(0 if p == n else 1)
