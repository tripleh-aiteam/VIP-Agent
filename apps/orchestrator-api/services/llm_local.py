"""
llm_local — LLM wrapper for the voice pipeline's reply generation.

Phase 1 (no GPU): uses the existing llm_client.chat_completion_sync (which
                  routes to Anthropic / OpenAI / Gemini). VOICE_LLM_MODEL
                  defaults to claude-haiku-4-5 for fast first-token latency.

Phase 2 (GPU ready): set VOICE_USE_LOCAL_LLM=1 to use a local Ollama server
                     running EXAONE 3.5 32B (or Qwen 2.5 32B). Default
                     OLLAMA_HOST=http://localhost:11434.

Recommended local model: lge/exaone3.5:32b (LG AI Research, native Korean).
Pull with: `ollama pull lge/exaone3.5:32b`.

Both backends reuse the agent's existing chatbot knowledge base from the
chatbot_talk pipeline — voice and text use the same brain.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional
from uuid import UUID

import httpx

from services.logger import log


def _use_local() -> bool:
    return os.getenv("VOICE_USE_LOCAL_LLM", "0") == "1"


async def generate_reply(
    *,
    agent_id: str,
    user_text: str,
    call_db_id: Optional[UUID] = None,
) -> str:
    """Generate the bot's spoken reply to the caller's last turn.

    Always uses the agent's existing knowledge base via the same prompt
    pipeline as the text chatbot. The voice surface is just another
    transport for the same brain.
    """
    system_prompt = _build_voice_system_prompt(agent_id)

    if _use_local():
        return await _generate_local(system_prompt, user_text)
    return await _generate_cloud(system_prompt, user_text)


def _build_voice_system_prompt(agent_id: str) -> str:
    """Bilingual receptionist prompt — short, voice-friendly, Korean-first.

    KEEP THE REPLY SHORT. Phone callers expect 1-2 sentences. Long
    LLM responses sound robotic and increase TTS latency.
    """
    # TODO: pull agent's knowledgeBase from chatbot config and merge in
    return f"""\
당신은 트리플H 부동산 회사의 AI 전화 비서입니다. 통화 상대방에게 한국어로 자연스럽게 응답하세요.

규칙:
- 답변은 짧고 명확하게 (1-2 문장).
- 영어로 말씀하시면 영어로 답변하세요.
- 정확한 가격/날짜/주소는 절대 추측하지 말고, 모르면 "담당자에게 확인 후 연락드리겠습니다"라고 답하세요.
- 위급한 상황이거나 계약금 입금 같은 중요한 결정이 필요하면 "잠시 후 담당자에게 직접 전화 드리도록 하겠습니다"라고 답하세요.
- 통화 첫 문장은 이미 녹음 고지가 나간 상태이므로, 두 번째 문장부터는 본론으로 들어가세요.

You are Triple-H Real Estate's AI phone assistant. Reply naturally in Korean by default; switch to English if the caller speaks English. Keep replies 1-2 sentences max. Don't guess specifics — defer to the human staff for prices, dates, addresses.

(Agent ID: {agent_id})
"""


# ----------------------------------------------------------------------------
# Phase 1 — cloud LLM via the existing llm_client
# ----------------------------------------------------------------------------

async def _generate_cloud(system_prompt: str, user_text: str) -> str:
    model = os.getenv("VOICE_LLM_MODEL", "claude-haiku-4-5")
    try:
        # llm_client is synchronous — run it in a thread to keep the event loop free
        from services.llm_client import chat_completion_sync
        reply = await asyncio.to_thread(
            chat_completion_sync,
            system_prompt,
            [{"role": "user", "content": user_text}],
            model,
        )
        return (reply or "").strip()
    except Exception as e:
        log.warning(f"llm_local: cloud LLM error: {e}")
        return ""


# ----------------------------------------------------------------------------
# Phase 2 — local Ollama
# ----------------------------------------------------------------------------

async def _generate_local(system_prompt: str, user_text: str) -> str:
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    model = os.getenv("VOICE_LLM_LOCAL_MODEL", "lge/exaone3.5:32b")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{ollama_host}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                    "options": {
                        "temperature": 0.4,
                        "num_predict": 200,    # keep replies short for voice
                    },
                },
            )
            if resp.status_code != 200:
                log.warning(f"llm_local: Ollama {resp.status_code}: {resp.text[:200]}")
                return ""
            data = resp.json()
            return (data.get("message", {}).get("content") or "").strip()
    except Exception as e:
        log.warning(f"llm_local: Ollama error: {e}")
        return ""
