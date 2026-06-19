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
        timeout: float = 45.0) -> Optional[dict[str, Any]]:
    """POST the question to the Stock-Advisor assistant and return
    ``{reply, tool_used, intent, action}`` — or ``None`` if it can't answer
    (caller then falls back to the in-process stock engine)."""
    q = (transcript or "").strip()
    if not q:
        return None
    # NOTE: do NOT prepend a "(오늘 날짜: …)" prefix here. The Stock backend already
    # injects the current date via its own system prompt (system_prompt_with_date),
    # and a date in the transcript makes its _is_past_date_query match every message
    # — routing simple "현재가" questions to the slow historical/LLM path instead of
    # the fast direct-quote path. Send the raw question.
    payload: dict[str, Any] = {
        "transcript": q,
        "language": lang or "auto",
        "agentId": "stock",
    }
    if history:
        payload["history"] = history[-8:]
    # Retry ONCE, but only on a CONNECTION error (cold start — first request wakes a
    # spun-down peer). A read/response timeout means the peer is just slow; retrying
    # would DOUBLE the wait (and could push VIP past the proxy timeout → 502), so we
    # don't retry those. `timeout` bounds the total wait so VIP never hangs.
    d = None
    for attempt in range(2):
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=8.0)) as c:
                r = c.post(f"{_BASE}/chat/agent", json=payload)
            if r.status_code == 200:
                d = r.json()
            else:
                log.warning(f"stock_advisor_chat: HTTP {r.status_code}")
            break  # got a response (200 or not) — never retry
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            log.warning(f"stock_advisor_chat connect: {str(e)[:120]} (attempt {attempt + 1})")
            if attempt == 0:
                import time as _t
                _t.sleep(1.5)
                continue
        except Exception as e:
            log.warning(f"stock_advisor_chat: {str(e)[:140]}")
            break  # read timeout / other — slow peer, retrying won't help
    if d is None:
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
