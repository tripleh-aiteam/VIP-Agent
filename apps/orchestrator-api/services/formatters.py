"""
VIP AI Platform — Response Formatter Abstraction Layer
StandardResponseFormatter: current plain-text responses
AIResponseFormatter: OpenAI-enhanced natural language formatting

Both receive the same action result — AI formatter just rewrites the text more naturally.
Action execution is NEVER affected by the formatter.
"""

import os
import json
from typing import Any

from services.logger import log

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


class StandardResponseFormatter:
    """Current response format — no changes."""

    def format(self, response: dict) -> dict:
        return response


class AIResponseFormatter:
    """
    AI-enhanced formatter. Takes the deterministic response and rewrites
    the text portion for better operator readability.
    NEVER changes the data, action_result_type, linked_object_ids, or trace_id.
    """

    SYSTEM_PROMPT = (
        "Rewrite this system output as a brief operator briefing. "
        "Rules: Keep all numbers exact. 2-4 sentences for simple data, 5-8 for reports. "
        "Lead with the key insight. Flag risks. No markdown. Same language as input."
    )

    def format(self, response: dict) -> dict:
        if not OPENAI_API_KEY:
            return response

        content = response.get("content", {})
        original_text = content.get("text", "")

        # Don't reformat very short responses or errors
        if len(original_text) < 30 or not original_text.strip():
            return response

        try:
            import httpx
            # Truncate long outputs to save tokens
            input_text = original_text[:800] if len(original_text) > 800 else original_text
            with httpx.Client(timeout=8) as client:
                resp = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": OPENAI_MODEL,
                        "messages": [
                            {"role": "system", "content": self.SYSTEM_PROMPT},
                            {"role": "user", "content": input_text},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 250,
                    },
                )

            if resp.status_code != 200:
                return response

            rewritten = resp.json()["choices"][0]["message"]["content"].strip()

            # Return enhanced response — data/metadata stays untouched
            return {
                "type": response.get("type"),
                "content": {
                    **content,
                    "text": rewritten,
                    "original_text": original_text,  # preserve original for audit
                    "ai_enhanced": True,
                },
            }

        except Exception as e:
            log.warning(f"ai-formatter: error: {e}", extra={"action": "formatter.error"})
            return response


def get_formatter(mode: str):
    """Factory: get the right formatter for the chat mode."""
    if mode in ("llm", "ai_assist"):
        return AIResponseFormatter()
    return StandardResponseFormatter()
