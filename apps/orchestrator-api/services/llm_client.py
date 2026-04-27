"""
VIP AI Platform — Smart LLM Client
Auto-switches between OpenAI and local Ollama.

Priority:
1. Try OpenAI first (better quality)
2. If OpenAI fails (429 rate limit, 402 no credits, timeout) → fallback to local Ollama
3. If both fail → return error

Set env vars:
- LLM_BASE_URL: OpenAI or custom endpoint
- LLM_API_KEY: OpenAI API key
- LLM_MODEL: OpenAI model (default: gpt-4o-mini)
- OLLAMA_URL: Local Ollama URL (default: http://localhost:11434)
- OLLAMA_MODEL: Local model (default: qwen2.5:1.5b)
"""

import os
import httpx

# Primary: OpenAI (or cloud LLM)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", ""))
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# Fallback: Local Ollama
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")

# Track which LLM was used last (for logging)
_last_used = {"provider": "none"}


def get_last_provider() -> str:
    return _last_used["provider"]


def _call_llm(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    """Call an LLM endpoint. Returns (success, response_text)."""
    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )

            if resp.status_code == 200:
                data = resp.json()
                return True, data["choices"][0]["message"]["content"]
            else:
                return False, f"HTTP {resp.status_code}"

    except Exception as e:
        return False, str(e)


def chat_completion_sync(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 500,
    temperature: float = 0.7,
) -> str:
    """
    Smart LLM call with auto-fallback.
    1. Try OpenAI first
    2. If fails → try local Ollama
    3. If both fail → return error message
    """
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    # Try 1: OpenAI (primary)
    if LLM_API_KEY:
        success, result = _call_llm(
            LLM_BASE_URL, LLM_API_KEY, LLM_MODEL,
            full_messages, max_tokens, temperature,
        )
        if success:
            _last_used["provider"] = f"openai ({LLM_MODEL})"
            return result

    # Try 2: Local Ollama (fallback)
    success, result = _call_llm(
        OLLAMA_URL, "", OLLAMA_MODEL,
        full_messages, max_tokens, temperature,
        timeout=60.0,  # local can be slower
    )
    if success:
        _last_used["provider"] = f"ollama ({OLLAMA_MODEL})"
        return result

    # Both failed
    _last_used["provider"] = "none (both failed)"
    return f"[LLM unavailable] OpenAI and local Ollama both failed. Last error: {result}"


async def chat_completion(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 500,
    temperature: float = 0.7,
) -> str:
    """Async version with same auto-fallback logic."""
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    async def _async_call(base_url, api_key, model, timeout=30.0):
        try:
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={"model": model, "messages": full_messages, "max_tokens": max_tokens, "temperature": temperature},
                )
                if resp.status_code == 200:
                    return True, resp.json()["choices"][0]["message"]["content"]
                return False, f"HTTP {resp.status_code}"
        except Exception as e:
            return False, str(e)

    # Try OpenAI first
    if LLM_API_KEY:
        success, result = await _async_call(LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)
        if success:
            _last_used["provider"] = f"openai ({LLM_MODEL})"
            return result

    # Fallback to Ollama
    success, result = await _async_call(OLLAMA_URL, "", OLLAMA_MODEL, timeout=60.0)
    if success:
        _last_used["provider"] = f"ollama ({OLLAMA_MODEL})"
        return result

    _last_used["provider"] = "none"
    return f"[LLM unavailable] Both providers failed. Last error: {result}"
