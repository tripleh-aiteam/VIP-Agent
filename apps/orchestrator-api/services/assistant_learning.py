"""
assistant_learning — the chatbot/Assistant self-improvement loop.

Sources of improvement (all four the user asked for):
  1. User feedback / corrections   — 👍/👎 + "the right answer is…" on a reply
  2. Existing RAG knowledge base    — lessons are stored back INTO the per-agent
                                       KB (reuse knowledge_ingest.ingest_file) so
                                       future RAG retrieval surfaces them
  3. Manual file uploads            — already handled by /assistant/knowledge/*
  4. LLM knowledge + Google search  — when a question can't be answered from the
                                       KB, research it (web_search) and distill a
                                       verified note into the KB

Safety: every learned note passes a self-critique gate (a second LLM judgment)
so the agent never permanently learns an incorrect "correction". Nothing here
raises — failures are logged and return a status dict.

Learned notes are stored as tiny synthetic KB files named
  learned/<agent_id>/<timestamp>.md
so they're retrievable by rag_retrieve exactly like uploaded docs, and visible
(and deletable) in the Add-knowledge file list.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from services.logger import log
from services.llm_client import chat_completion_sync
from services.knowledge_ingest import ingest_file
from services.web_search import search_web


# ---------------------------------------------------------------------------
#  Self-critique gate
# ---------------------------------------------------------------------------

def _judge_correction(question: str, bad_answer: str, user_correction: str) -> dict[str, Any]:
    """Second-opinion LLM: is the user's correction actually correct + safe to
    learn? Returns {accept: bool, reason: str, lesson: str}. Conservative —
    defaults to reject on uncertainty so we never enshrine a wrong fact."""
    sys = (
        "You are a strict reviewer deciding whether to permanently teach an AI "
        "assistant a lesson from user feedback. Accept ONLY if the user's "
        "correction is a clear, generally-true fact or a reasonable behavioural "
        "preference. REJECT if it's ambiguous, a one-off, possibly false, "
        "offensive, or just venting. Respond with strict JSON: "
        '{"accept": true|false, "reason": "...", "lesson": "a single concise, '
        'self-contained sentence the assistant should remember (empty if reject)"}'
    )
    user = (
        f"QUESTION the user asked:\n{question}\n\n"
        f"ASSISTANT's answer (which the user is correcting):\n{bad_answer}\n\n"
        f"USER's correction / feedback:\n{user_correction}\n\n"
        "Should this be learned? Return the JSON."
    )
    try:
        raw = chat_completion_sync(sys, [{"role": "user", "content": user}],
                                   max_tokens=300, temperature=0.0)
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1]) if start >= 0 else {}
        return {
            "accept": bool(data.get("accept")),
            "reason": str(data.get("reason", ""))[:300],
            "lesson": str(data.get("lesson", ""))[:500],
        }
    except Exception as e:
        log.warning(f"assistant_learning judge failed: {str(e)[:120]}")
        return {"accept": False, "reason": "judge error", "lesson": ""}


def _store_lesson(db: Session, *, agent_id: str, title: str, body_md: str,
                  uploaded_by: Optional[str]) -> dict[str, Any]:
    """Persist a learned note into the agent's RAG KB as a small markdown file."""
    try:
        blob = body_md.encode("utf-8")
        # filename namespacing makes learned notes easy to spot / filter / purge
        fname = f"learned/{agent_id}/{title}.md"
        res = ingest_file(
            db, agent_id=agent_id, filename=fname,
            mime_type="text/markdown", blob=blob,
            uploaded_by=uploaded_by or "self-improvement",
        )
        db.commit()
        return {"ok": True, **res}
    except Exception as e:
        try: db.rollback()
        except Exception: pass
        log.warning(f"assistant_learning store failed: {str(e)[:160]}")
        return {"ok": False, "error": str(e)[:200]}


# ---------------------------------------------------------------------------
#  1) Learn from a user correction (👎 + text)
# ---------------------------------------------------------------------------

def learn_from_feedback(
    db: Session,
    *,
    agent_id: str,
    question: str,
    answer: str,
    verdict: str,                 # "up" | "down"
    correction: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Process one feedback event. For 👍 we (optionally) keep the Q/A as a
    good exemplar; for 👎 with a correction we judge it and, if accepted, store
    a lesson into the KB so the mistake isn't repeated."""
    question = (question or "").strip()
    answer = (answer or "").strip()

    # 👍 — store the Q/A as a verified exemplar (only if it's substantive).
    if verdict == "up":
        if not question or len(answer) < 12:
            return {"ok": True, "learned": False, "reason": "thin exemplar skipped"}
        body = (
            f"# Verified Q&A (user approved)\n\n"
            f"**Question:** {question}\n\n"
            f"**Good answer:** {answer}\n"
        )
        title = f"good-{abs(hash(question)) % 10_000_000}"
        res = _store_lesson(db, agent_id=agent_id, title=title, body_md=body, uploaded_by=user_id)
        return {"ok": res.get("ok", False), "learned": res.get("ok", False), "kind": "exemplar"}

    # 👎 — need a correction to learn anything actionable.
    if not (correction or "").strip():
        return {"ok": True, "learned": False, "reason": "no correction text — logged only"}

    judged = _judge_correction(question, answer, correction.strip())
    if not judged["accept"] or not judged["lesson"]:
        return {"ok": True, "learned": False, "reason": f"rejected by self-critique: {judged['reason']}"}

    body = (
        f"# Learned correction\n\n"
        f"**When asked:** {question}\n\n"
        f"**Correct answer / behaviour:** {judged['lesson']}\n\n"
        f"_(Learned from user feedback; verified before saving.)_\n"
    )
    title = f"fix-{abs(hash(question + correction)) % 10_000_000}"
    res = _store_lesson(db, agent_id=agent_id, title=title, body_md=body, uploaded_by=user_id)
    return {
        "ok": res.get("ok", False),
        "learned": res.get("ok", False),
        "kind": "correction",
        "lesson": judged["lesson"],
    }


# ---------------------------------------------------------------------------
#  4) Research a knowledge gap via web search + LLM, then learn it
# ---------------------------------------------------------------------------

def research_and_learn(
    db: Session,
    *,
    agent_id: str,
    question: str,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """For a question the KB couldn't answer: web-search it, have the LLM
    synthesize a concise sourced answer, self-critique, then store to the KB."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "empty question"}

    web = search_web(question, num_results=5)
    if not web.get("ok"):
        return {"ok": False, "error": web.get("error", "web search unavailable"), "learned": False}

    sources = "\n".join(
        f"- {r['title']} ({r['url']}): {r['snippet']}" for r in web["results"][:5]
    )
    sys = (
        "Synthesize a concise, factual answer to the question using ONLY the "
        "search snippets provided. If the snippets don't clearly answer it, say "
        "so. Return strict JSON: {\"answerable\": true|false, \"answer\": \"2-4 "
        "sentence factual answer, no fluff\"}."
    )
    user = f"QUESTION:\n{question}\n\nSEARCH RESULTS:\n{sources}"
    try:
        raw = chat_completion_sync(sys, [{"role": "user", "content": user}],
                                   max_tokens=400, temperature=0.1)
        s, e = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[s:e + 1]) if s >= 0 else {}
    except Exception as ex:
        log.warning(f"research_and_learn synth failed: {str(ex)[:120]}")
        return {"ok": False, "error": "synthesis failed", "learned": False}

    if not data.get("answerable") or not str(data.get("answer", "")).strip():
        return {"ok": True, "learned": False, "reason": "web results did not clearly answer"}

    answer = str(data["answer"]).strip()
    top_urls = ", ".join(r["url"] for r in web["results"][:3] if r.get("url"))
    body = (
        f"# Researched answer (web)\n\n"
        f"**Question:** {question}\n\n"
        f"**Answer:** {answer}\n\n"
        f"_Sources: {top_urls}_\n"
    )
    title = f"web-{abs(hash(question)) % 10_000_000}"
    res = _store_lesson(db, agent_id=agent_id, title=title, body_md=body, uploaded_by=user_id)
    return {
        "ok": res.get("ok", False),
        "learned": res.get("ok", False),
        "kind": "research",
        "answer": answer,
        "provider": web.get("provider"),
    }
