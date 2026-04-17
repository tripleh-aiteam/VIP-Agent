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

    SYSTEM_PROMPT = """You are a VIP Agent Platform personal assistant. You talk like a real human — friendly, professional, helpful.

Your job: Take the raw system data below and present it as if you're a human assistant briefing your boss.

Style:
- Talk naturally, like a real person: "Here's what I found..." "Looking at the numbers..." "I noticed that..."
- Keep all numbers and facts exactly as given — never make up data
- Highlight important things: risks, warnings, things that need attention
- For reports: organize clearly with sections, use line breaks for readability
- For short answers: be concise (2-3 sentences)
- For detailed reports: be thorough, walk through each section
- If there are risks or warnings, call them out clearly
- Answer in the same language the user used (Korean/English)
- Do not use markdown formatting like ** or ## — just plain text with line breaks"""

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
            with httpx.Client(timeout=8) as client:
                resp = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": OPENAI_MODEL,
                        "messages": [
                            {"role": "system", "content": self.SYSTEM_PROMPT},
                            {"role": "user", "content": f"Original system output:\n\n{original_text}"},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 500,
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
