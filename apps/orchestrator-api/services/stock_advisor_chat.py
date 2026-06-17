"""stock_advisor_chat — relay to the Stock-Advisor app's OWN assistant.

The Stock app's "주식 AI" chatbot is powered by the stock-advisor backend's
``/chat/agent`` endpoint. To make the answer IDENTICAL everywhere (the Stock app,
the VIP chatbot that delegates stock questions, and our own stock surface), we
treat that endpoint as the SINGLE SOURCE OF TRUTH and relay its reply verbatim.

Returns ``None`` on any failure so callers can fall back to the in-process engine.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from services.logger import log

_BASE = (os.getenv("STOCK_BACKEND_URL")
         or "https://stock-advisor-agent-9qwi.onrender.com").rstrip("/")


def ask(transcript: str, lang: str = "ko",
        history: Optional[list[dict]] = None,
        timeout: float = 60.0) -> Optional[dict[str, Any]]:
    """POST the question to the Stock-Advisor assistant and return
    ``{reply, tool_used, intent, action}`` — or ``None`` if it can't answer
    (caller then falls back to the in-process stock engine)."""
    q = (transcript or "").strip()
    if not q:
        return None
    # Tell the backend's LLM the real current date so it doesn't default to its
    # training-cutoff year and treat recent/past dates as 'the future'.
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _today = _dt.now(_tz(_td(hours=9))).strftime("%Y-%m-%d")
    q_dated = f"(오늘 날짜: {_today} KST) {q}"
    payload: dict[str, Any] = {
        "transcript": q_dated,
        "language": lang or "auto",
        "agentId": "stock",
    }
    if history:
        payload["history"] = history[-8:]
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(f"{_BASE}/chat/agent", json=payload)
        if r.status_code != 200:
            log.warning(f"stock_advisor_chat: HTTP {r.status_code}")
            return None
        d = r.json()
    except Exception as e:
        log.warning(f"stock_advisor_chat: {str(e)[:140]}")
        return None
    reply = (d.get("reply") or "").strip()
    # Guard against a raw decision-JSON leak or empty answer.
    if not reply or reply.startswith("{"):
        return None
    return {
        "reply": reply,
        "tool_used": d.get("tool_used"),
        "intent": d.get("intent"),
        "action": d.get("action"),
    }
