"""
VIP AI Platform — Twin Eval harness.

Measures how well a twin actually USES what it has learned — a fidelity score you
can trust before letting a twin act. Method:

  1. Sample N of the twin's own knowledge items.
  2. For each, an LLM writes a question whose answer is that knowledge.
  3. Ask the twin (its real RAG brain) the question.
  4. An LLM judge scores 0-1 whether the twin's answer reflects the source.
  5. Aggregate → a 0-100 fidelity score + per-question detail.

Owner-only at the endpoint (the Q&A references private knowledge). The boss only
sees the headline readiness % via the monitoring endpoints, never this content.
"""

import json
import random
from typing import Optional

from sqlalchemy.orm import Session

from services import twin_service, twin_brain
from services.llm_client import chat_completion_sync
from services.logger import log


def _ask_llm(system: str, user: str, max_tokens: int = 300) -> str:
    try:
        return chat_completion_sync(system_prompt=system,
                                    messages=[{"role": "user", "content": user}],
                                    max_tokens=max_tokens, temperature=0.2) or ""
    except Exception as e:
        log.warning(f"twin_eval LLM failed: {e}")
        return ""


def _judge(source: str, question: str, answer: str) -> dict:
    out = _ask_llm(
        "You grade whether an AI twin's answer correctly reflects a source fact. "
        'Return STRICT JSON {"score": 0.0-1.0, "reason": "<one line>"}. '
        "1.0 = fully correct & grounded; 0 = wrong/unrelated/hallucinated.",
        f"SOURCE FACT:\n{source[:1500]}\n\nQUESTION:\n{question}\n\nTWIN ANSWER:\n{answer[:1500]}\n\nGrade it.",
        max_tokens=120,
    )
    a, b = out.find("{"), out.rfind("}")
    if a != -1 and b > a:
        try:
            d = json.loads(out[a:b + 1])
            return {"score": max(0.0, min(1.0, float(d.get("score", 0)))), "reason": str(d.get("reason", ""))[:200]}
        except Exception:
            pass
    return {"score": 0.0, "reason": "judge parse failed"}


def run_eval(db: Session, twin_id, n: int = 5) -> dict:
    """Run the fidelity eval. Returns {ok, score_pct, n, results, note}."""
    knowledge = twin_service.get_knowledge(db, twin_id)
    # Prefer substantive items; skip tiny/auto fragments.
    pool = [k for k in knowledge if k.content and len(k.content) > 80]
    if len(pool) < 3:
        return {"ok": True, "score_pct": 0, "n": 0,
                "note": "Not enough knowledge to evaluate yet — teach the twin more first.",
                "results": []}

    sample = random.sample(pool, min(n, len(pool)))
    results = []
    total = 0.0
    for k in sample:
        question = _ask_llm(
            "Write ONE natural question a colleague might ask whose answer is the given fact. "
            "Return only the question, no preamble.",
            f"FACT: {k.content[:1200]}",
            max_tokens=80,
        ).strip().strip('"')
        if not question:
            continue
        try:
            answer = twin_brain.think(db, twin_id, question) or ""
        except Exception as e:
            answer = f"[error: {e}]"
        verdict = _judge(k.content, question, answer)
        total += verdict["score"]
        results.append({
            "question": question,
            "answer": answer[:300],
            "score": round(verdict["score"], 2),
            "reason": verdict["reason"],
            "source_title": k.title,
        })

    score_pct = round((total / len(results)) * 100) if results else 0
    try:
        twin_service.log_activity(db, twin_id, "eval",
                                  f"Fidelity eval: {score_pct}% over {len(results)} Q",
                                  {"score_pct": score_pct, "n": len(results)})
        db.commit()
    except Exception:
        pass
    return {"ok": True, "score_pct": score_pct, "n": len(results),
            "note": _grade_note(score_pct), "results": results}


def _grade_note(p: int) -> str:
    if p >= 85: return "Excellent — reliably recalls and uses what it knows."
    if p >= 65: return "Good — mostly accurate; a few gaps to teach."
    if p >= 40: return "Developing — needs more teaching/corrections."
    return "Early — keep teaching; not ready to act unsupervised."
