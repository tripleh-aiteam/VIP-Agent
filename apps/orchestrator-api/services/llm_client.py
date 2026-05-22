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
    "gemini-2.0-flash": ("gemini", "gemini-2.5-flash"),     # 2.0 deprecated; route to 2.5
    "gemini-1.5-pro":   ("gemini", "gemini-2.5-pro"),       # 1.5 deprecated; route to 2.5
    "gemini-2.5-flash": ("gemini", "gemini-2.5-flash"),     # current name
    "gemini-2.5-pro":   ("gemini", "gemini-2.5-pro"),       # current name (Pro plan)
    # --- Groq (LPU-based, 200-500ms latency, OpenAI-compatible API) ---
    # Free tier on console.groq.com. Fastest option for latency-sensitive
    # Kakao chatbot. Set GROQ_API_KEY env var to enable.
    "groq-llama-3.3-70b":   ("groq", "llama-3.3-70b-versatile"),
    "groq-llama-3.1-8b":    ("groq", "llama-3.1-8b-instant"),
    "groq-qwen-32b":        ("groq", "qwen-2.5-32b"),
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


def _openai_vision_fallback_sync(
    system_prompt: str,
    user_text: str,
    attachments: list[dict],
    *,
    model: str = "gpt-4o",
    max_tokens: int = 800,
    temperature: float = 0.4,
    timeout: float = 60.0,
) -> str:
    """OpenAI Vision (gpt-4o multimodal) fallback used when Gemini isn't
    available. Only images survive — gpt-4o doesn't accept arbitrary file
    types via inlineData. Returns "[LLM unavailable] ..." on any failure
    so callers can detect + report uniformly.
    """
    import base64 as _b64
    openai_key = _env("OPENAI_API_KEY") or _env("LLM_API_KEY")
    if not openai_key:
        return "[LLM unavailable] OPENAI_API_KEY not set"
    image_parts: list[dict] = []
    skipped = 0
    for a in attachments or []:
        mime = (a.get("mime_type") or "").lower()
        raw = a.get("bytes") or b""
        if not raw or not mime.startswith("image/"):
            skipped += 1
            continue
        try:
            b64 = _b64.b64encode(raw).decode("ascii")
        except Exception:
            skipped += 1
            continue
        image_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    if not image_parts:
        return "[LLM unavailable] no image attachments (gpt-4o fallback only handles images)"
    note = "" if skipped == 0 else (
        f"\n(Note: {skipped} non-image attachment(s) skipped — the current "
        "fallback model only reads images. Set GEMINI_API_KEY on the "
        "orchestrator for full PDF/audio/video support.)"
    )
    content = image_parts + [{"type": "text", "text": (user_text or "") + note}]
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}",
                         "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": content},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            if resp.status_code != 200:
                return f"[LLM unavailable] OpenAI Vision HTTP {resp.status_code}: {resp.text[:200]}"
            data = resp.json()
            return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or "[LLM unavailable] empty OpenAI response"
    except Exception as e:
        return f"[LLM unavailable] OpenAI Vision exception: {e}"


def anthropic_multimodal_sync(
    system_prompt: str,
    user_text: str,
    attachments: list[dict],
    *,
    model: str = "claude-haiku-4-5",
    max_tokens: int = 800,
    temperature: float = 0.4,
    timeout: float = 90.0,
) -> str:
    """Claude multimodal call (vision). Claude Haiku 4.5 / Sonnet 4.6 / Opus
    accept images via base64 in the messages API. Non-image attachments are
    skipped — Claude doesn't take arbitrary `inlineData` like Gemini does.
    Returns "[LLM unavailable] ..." on failure.
    """
    import base64 as _b64
    api_key = _env("ANTHROPIC_API_KEY")
    if not api_key:
        return "[LLM unavailable] ANTHROPIC_API_KEY not set"

    # Resolve friendly model id (e.g. "claude-haiku-4-5") to real one
    real_model = model
    if model in MODEL_CATALOG:
        prov, real = MODEL_CATALOG[model]
        if prov != "anthropic":
            return f"[LLM unavailable] model '{model}' is not an Anthropic model"
        real_model = real

    content_blocks: list[dict] = []
    skipped = 0
    for a in attachments or []:
        mime = (a.get("mime_type") or "").lower()
        raw = a.get("bytes") or b""
        if not raw or not mime.startswith("image/"):
            skipped += 1
            continue
        try:
            b64 = _b64.b64encode(raw).decode("ascii")
        except Exception:
            skipped += 1
            continue
        content_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": b64},
        })
    if not content_blocks:
        return "[LLM unavailable] no image attachments (Claude vision only handles images)"
    note = "" if skipped == 0 else (
        f"\n(Note: {skipped} non-image attachment(s) skipped — Claude vision only reads images.)"
    )
    content_blocks.append({"type": "text", "text": (user_text or "") + note})

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{ANTHROPIC_BASE}/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": real_model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": content_blocks}],
                },
            )
            if resp.status_code != 200:
                return f"[LLM unavailable] Claude HTTP {resp.status_code}: {resp.text[:200]}"
            data = resp.json()
            parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
            text = "".join(parts).strip()
            return text or "[LLM unavailable] Claude returned empty content"
    except Exception as e:
        return f"[LLM unavailable] Claude exception: {e}"


def gemini_multimodal_sync(
    system_prompt: str,
    user_text: str,
    attachments: list[dict],
    *,
    model: str = "gemini-2.5-pro",
    max_tokens: int = 800,
    temperature: float = 0.4,
    timeout: float = 90.0,
) -> str:
    """Send a multimodal request to Gemini (text + image/pdf/audio bytes).

    `attachments`: list of dicts shaped like
        {"mime_type": "image/png", "bytes": b"...", "filename": "x.png"}

    Returns the LLM's text response, or "[LLM unavailable] <reason>" on
    failure. Use this from assistant_agent when the user uploaded files —
    Gemini's inlineData accepts images directly without intermediate hosting.

    Model defaults to gemini-2.5-pro because the cheap Flash tier
    frequently refuses to describe images of UI/data; Pro reliably returns
    a useful response. Pass `model="gemini-2.5-flash"` to override for
    cost/latency-sensitive use cases.
    """
    import base64 as _b64
    gemini_key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
    if not gemini_key:
        # No Gemini configured — fall back to OpenAI Vision so the user
        # still gets an answer (images only; PDF/audio/video lose support).
        return _openai_vision_fallback_sync(
            system_prompt, user_text, attachments,
            max_tokens=max_tokens, temperature=temperature,
        )

    # Resolve friendly model name to real one
    real_model = model
    if model in MODEL_CATALOG:
        prov, real = MODEL_CATALOG[model]
        if prov != "gemini":
            return f"[LLM unavailable] model '{model}' is not a Gemini model"
        real_model = real

    parts: list[dict] = []
    for a in attachments or []:
        mime = (a.get("mime_type") or "application/octet-stream")[:80]
        raw = a.get("bytes") or b""
        if not raw:
            continue
        try:
            b64 = _b64.b64encode(raw).decode("ascii")
        except Exception:
            continue
        parts.append({"inlineData": {"mimeType": mime, "data": b64}})
    if user_text:
        parts.append({"text": user_text})

    if not parts:
        return "[LLM unavailable] no input parts"

    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }
    gemini_err = ""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{GEMINI_BASE}/models/{real_model}:generateContent?key={gemini_key}",
                headers={"Content-Type": "application/json"},
                json=body,
            )
            if resp.status_code == 200:
                data = resp.json()
                cands = data.get("candidates") or []
                if cands and cands[0].get("content", {}).get("parts"):
                    return "".join(p.get("text", "") for p in cands[0]["content"]["parts"])
                finish = cands[0].get("finishReason") if cands else None
                gemini_err = f"Gemini empty response (finishReason={finish})"
            else:
                gemini_err = f"Gemini HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        gemini_err = f"Gemini exception: {e}"

    # Vision cascade: Gemini failed → try Claude Haiku (vision) →
    # then OpenAI gpt-4o. Surface all error reasons if all three fail.
    claude_reply = anthropic_multimodal_sync(
        system_prompt, user_text, attachments,
        model="claude-haiku-4-5",
        max_tokens=max_tokens, temperature=temperature,
    )
    if not claude_reply.startswith("[LLM unavailable]"):
        return claude_reply
    claude_err = claude_reply.replace("[LLM unavailable]", "").strip()

    openai_reply = _openai_vision_fallback_sync(
        system_prompt, user_text, attachments,
        max_tokens=max_tokens, temperature=temperature,
    )
    if not openai_reply.startswith("[LLM unavailable]"):
        return openai_reply
    openai_err = openai_reply.replace("[LLM unavailable]", "").strip()

    return f"[LLM unavailable] {gemini_err} | claude: {claude_err} | openai: {openai_err}"


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
    groq_key = _env("GROQ_API_KEY")
    chosen = model or _env("LLM_MODEL") or DEFAULT_MODEL_NAME
    full_messages_with_sys = [{"role": "system", "content": system_prompt}] + messages

    # Track every fallback attempt so a final failure can explain WHICH
    # providers we tried and what they said.
    attempt_log: list[str] = []

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
        elif provider == "groq":
            if groq_key:
                ok, result = _call_openai_compatible("https://api.groq.com/openai/v1", groq_key, real_model,
                                                     full_messages_with_sys, max_tokens, temperature, 15.0)
            else:
                ok, result = False, "GROQ_API_KEY not set"
        elif provider == "ollama":
            ok, result = _call_openai_compatible(f"{ollama_url}/v1", "", real_model,
                                                 full_messages_with_sys, max_tokens, temperature, 60.0)
        else:
            ok, result = False, f"Unknown provider {provider}"

        if ok:
            _last_used.update({"provider": provider, "model": chosen})
            return result
        attempt_log.append(f"{chosen} ({provider}): {str(result)[:200]}")

    # -- Free-tier fallback chain --
    # Try every free / no-credit-card-needed provider before giving up so a
    # billing issue on one paid LLM doesn't brick the assistant. Order
    # below is by latency: Groq (fastest free) → Gemini Flash (free quota) →
    # OpenAI (only paid in this list, kept as a "last paid try" before
    # going free-only) → Ollama (free, requires local install).

    # Free tier #1 — Groq Llama 3.3 70B (free, 30 RPM, no credit card)
    if groq_key:
        ok, result = _call_openai_compatible("https://api.groq.com/openai/v1", groq_key,
                                             "llama-3.3-70b-versatile",
                                             full_messages_with_sys, max_tokens, temperature, 15.0)
        if ok:
            _last_used.update({"provider": "groq", "model": "llama-3.3-70b (free fallback)"})
            return result
        attempt_log.append(f"llama-3.3-70b (groq free fallback): {str(result)[:200]}")

    # Free tier #2 — Gemini 2.5 Flash (free, 15 RPM / 1.5M TPM)
    if _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"):
        ok, result = _call_gemini("gemini-2.5-flash", system_prompt, messages,
                                  max_tokens, temperature)
        if ok:
            _last_used.update({"provider": "gemini", "model": "gemini-2.5-flash (free fallback)"})
            return result
        attempt_log.append(f"gemini-2.5-flash (free fallback): {str(result)[:200]}")

    # Paid tier — OpenAI gpt-4o-mini (only fires if you've topped up credits)
    if openai_key:
        ok, result = _call_openai_compatible(openai_base, openai_key, "gpt-4o-mini",
                                             full_messages_with_sys, max_tokens, temperature, 30.0)
        if ok:
            _last_used.update({"provider": "openai", "model": "gpt-4o-mini (fallback)"})
            return result
        attempt_log.append(f"gpt-4o-mini (openai fallback): {str(result)[:200]}")

    # Free tier #3 — local Ollama (only useful when running on a dev box
    # with Ollama installed; on Render this always 'Connection refused'd)
    ok, result = _call_openai_compatible(f"{ollama_url}/v1", "", "qwen2.5",
                                         full_messages_with_sys, max_tokens, temperature, 60.0)
    if ok:
        _last_used.update({"provider": "ollama", "model": "qwen2.5 (fallback)"})
        return result
    attempt_log.append(f"qwen2.5 (ollama fallback): {str(result)[:200]}")

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
        hint = "Cannot reach LLM service. Check internet connection or local Ollama."
    else:
        hint = "Check your API keys in .env or try a different model from the picker."
    # Surface every provider attempt so the boss sees the FIRST error (the
    # one that matters), not just the last fallback. Previously only the
    # final Ollama 'Connection refused' was shown, hiding Anthropic/OpenAI
    # 404/401/429 errors that were the actual cause.
    attempts_block = " || ".join(attempt_log) if attempt_log else "(no attempts logged)"
    return f"[LLM unavailable] {hint} | attempts: {attempts_block}"


async def chat_completion(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 500,
    temperature: float = 0.7,
    model: str | None = None,
) -> str:
    """Async wrapper ??currently delegates to sync (good enough for now)."""
    return chat_completion_sync(system_prompt, messages, max_tokens, temperature, model)
