"""
VIP AI Platform ??Multi-Provider LLM Client
Routes chat requests to the right provider based on model name.

Supported providers (all called over HTTP ??no extra SDKs needed):
- Anthropic Claude: claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5
- OpenAI: gpt-4o, gpt-4o-mini
- Google Gemini: gemini-2.0-flash, gemini-1.5-pro
- Local Ollama: llama3, qwen2.5, gemma3, phi-4 (and any ollama tag)

Env vars:
- ANTHROPIC_API_KEY (for claude-* models)
- OPENAI_API_KEY (for gpt-* models)
- GEMINI_API_KEY (for gemini-* models)
- OLLAMA_URL (default http://localhost:11434)

Default model: gpt-4o-mini (set via LLM_MODEL env var).
"""

import os
import httpx

# ---------------------------------------------------------------------------
# Provider catalog
# ---------------------------------------------------------------------------

# Maps friendly model names ??(provider, real_model_id)
MODEL_CATALOG = {
    # --- Claude ---
    "claude-opus-4-7":   ("anthropic", "claude-opus-4-5"),
    "claude-sonnet-4-6": ("anthropic", "claude-sonnet-4-5"),
    "claude-haiku-4-5":  ("anthropic", "claude-haiku-4-5"),
    # --- OpenAI ---
    "gpt-4o":      ("openai", "gpt-4o"),
    "gpt-4o-mini": ("openai", "gpt-4o-mini"),
    # --- Google Gemini ---
    "gemini-2.0-flash": ("gemini", "gemini-2.0-flash"),
    "gemini-1.5-pro":   ("gemini", "gemini-1.5-pro"),
    # --- Local Ollama ---
    "llama3":   ("ollama", "llama3"),
    "qwen2.5":  ("ollama", "qwen2.5"),
    "gemma3":   ("ollama", "gemma3"),
    "phi-4":    ("ollama", "phi4"),
}

# Read env vars at call time (not module load) so .env changes apply without process restart.
def _env(key: str, fallback: str = "") -> str:
    return os.getenv(key, fallback)

DEFAULT_MODEL_NAME = "gpt-4o-mini"

ANTHROPIC_BASE = "https://api.anthropic.com/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

_last_used = {"provider": "none", "model": "none"}  # reload tag v2


def get_last_provider() -> str:
    return f"{_last_used['provider']} ({_last_used['model']})"


def list_available_models() -> list[dict]:
    """Return catalog of models with availability flags. Reads env vars at call time."""
    has_openai    = bool(_env("OPENAI_API_KEY") or _env("LLM_API_KEY"))
    has_anthropic = bool(_env("ANTHROPIC_API_KEY"))
    has_gemini    = bool(_env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"))
    catalog = []
    for friendly, (provider, real) in MODEL_CATALOG.items():
        available = (
            (provider == "openai" and has_openai) or
            (provider == "anthropic" and has_anthropic) or
            (provider == "gemini" and has_gemini) or
            provider == "ollama"
        )
        catalog.append({
            "id": friendly, "provider": provider, "real_model": real,
            "available": available,
        })
    return catalog


# ---------------------------------------------------------------------------
# Provider call functions
# ---------------------------------------------------------------------------

def _call_openai_compatible(base_url: str, api_key: str, model: str, messages: list[dict],
                            max_tokens: int, temperature: float, timeout: float) -> tuple[bool, str]:
    """OpenAI-style /chat/completions (works for OpenAI + Ollama)."""
    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={"model": model, "messages": messages,
                      "max_tokens": max_tokens, "temperature": temperature},
            )
            if resp.status_code == 200:
                return True, resp.json()["choices"][0]["message"]["content"]
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


def _call_anthropic(model: str, system_prompt: str, messages: list[dict],
                    max_tokens: int, temperature: float, timeout: float = 300.0) -> tuple[bool, str]:
    """Claude messages API."""
    api_key = _env("ANTHROPIC_API_KEY")
    if not api_key:
        return False, "ANTHROPIC_API_KEY not set"
    # Convert OpenAI-style messages to Anthropic format
    # System messages must be passed separately; user/assistant alternation required.
    anthropic_messages = []
    for m in messages:
        if m.get("role") == "system":
            continue  # already handled via system_prompt
        anthropic_messages.append({"role": m["role"], "content": m["content"]})
    try:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{ANTHROPIC_BASE}/messages",
                headers=headers,
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "system": system_prompt,
                    "messages": anthropic_messages,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                # Response: { content: [ { type: "text", text: "..." } ] }
                text_parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
                return True, "".join(text_parts)
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


def _call_gemini(model: str, system_prompt: str, messages: list[dict],
                 max_tokens: int, temperature: float, timeout: float = 60.0) -> tuple[bool, str]:
    """Google Gemini generateContent API."""
    gemini_key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
    if not gemini_key:
        return False, "GEMINI_API_KEY not set"
    # Build contents array ??Gemini uses 'user' and 'model' roles
    contents = []
    for m in messages:
        if m.get("role") == "system":
            continue
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{GEMINI_BASE}/models/{model}:generateContent?key={gemini_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "contents": contents,
                    "generationConfig": {
                        "maxOutputTokens": max_tokens,
                        "temperature": temperature,
                    },
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                cands = data.get("candidates") or []
                if cands and cands[0].get("content", {}).get("parts"):
                    return True, "".join(p.get("text", "") for p in cands[0]["content"]["parts"])
                return False, "Empty Gemini response"
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Main entry ??chat_completion_sync
# ---------------------------------------------------------------------------

def chat_completion_sync(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 500,
    temperature: float = 0.7,
    model: str | None = None,
) -> str:
    """
    Smart LLM call with provider routing + fallback chain.
    Pass `model` to pick a specific one (e.g. "claude-sonnet-4-6").
    Falls back: requested model -> default OpenAI -> local Ollama.
    """
    # Read env vars now (not at module load) so .env updates apply.
    openai_key = _env("OPENAI_API_KEY") or _env("LLM_API_KEY")
    openai_base = _env("LLM_BASE_URL") or "https://api.openai.com/v1"
    ollama_url = _env("OLLAMA_URL") or "http://localhost:11434"
    chosen = model or _env("LLM_MODEL") or DEFAULT_MODEL_NAME
    full_messages_with_sys = [{"role": "system", "content": system_prompt}] + messages

    # -- Try the requested model first --
    if chosen in MODEL_CATALOG:
        provider, real_model = MODEL_CATALOG[chosen]
        if provider == "anthropic":
            ok, result = _call_anthropic(real_model, system_prompt, messages, max_tokens, temperature)
        elif provider == "gemini":
            ok, result = _call_gemini(real_model, system_prompt, messages, max_tokens, temperature)
        elif provider == "openai":
            if openai_key:
                ok, result = _call_openai_compatible(openai_base, openai_key, real_model,
                                                     full_messages_with_sys, max_tokens, temperature, 30.0)
            else:
                ok, result = False, "OPENAI_API_KEY not set"
        elif provider == "ollama":
            ok, result = _call_openai_compatible(f"{ollama_url}/v1", "", real_model,
                                                 full_messages_with_sys, max_tokens, temperature, 60.0)
        else:
            ok, result = False, f"Unknown provider {provider}"

        if ok:
            _last_used.update({"provider": provider, "model": chosen})
            return result

    # -- Fallback 1: default OpenAI --
    if openai_key:
        ok, result = _call_openai_compatible(openai_base, openai_key, "gpt-4o-mini",
                                             full_messages_with_sys, max_tokens, temperature, 30.0)
        if ok:
            _last_used.update({"provider": "openai", "model": "gpt-4o-mini (fallback)"})
            return result

    # -- Fallback 2: local Ollama --
    ok, result = _call_openai_compatible(f"{ollama_url}/v1", "", "qwen2.5",
                                         full_messages_with_sys, max_tokens, temperature, 60.0)
    if ok:
        _last_used.update({"provider": "ollama", "model": "qwen2.5 (fallback)"})
        return result

    _last_used.update({"provider": "none", "model": "none"})
    # Friendlier error messages for common failures
    last_err = str(result).lower()
    if "quota" in last_err or "exceeded" in last_err or "insufficient_quota" in last_err:
        hint = "?뮩 Your OpenAI account has no credits. Add credits at platform.openai.com/account/billing OR switch to a different model."
    elif "401" in last_err or "invalid_api_key" in last_err or "authentication" in last_err:
        hint = "?뵎 The API key is invalid. Check your .env file."
    elif "429" in last_err and "rate" in last_err:
        hint = "?깍툘 Rate limited. Wait a moment and try again."
    elif "model" in last_err and "not found" in last_err:
        hint = "?쬂 Local Ollama model not installed. Install Ollama from ollama.com and run 'ollama pull qwen2.5'."
    elif "connection" in last_err or "connect" in last_err:
        hint = "?뙋 Cannot reach LLM service. Check internet connection or local Ollama."
    else:
        hint = "Check your API keys in .env or try a different model from the picker."
    return f"[LLM unavailable] {hint}\n\nTechnical details: {result[:200]}"


async def chat_completion(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 500,
    temperature: float = 0.7,
    model: str | None = None,
) -> str:
    """Async wrapper ??currently delegates to sync (good enough for now)."""
    return chat_completion_sync(system_prompt, messages, max_tokens, temperature, model)
