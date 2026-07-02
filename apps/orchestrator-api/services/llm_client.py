"""
VIP AI Platform ??Multi-Provider LLM Client
Routes chat requests to the right provider based on model name.

Supported providers (all called over HTTP ??no extra SDKs needed):
- Anthropic Claude: claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5
- OpenAI: gpt-5.5, gpt-5.4, gpt-5.4-mini, gpt-5.4-nano
- Google Gemini: gemini-3.5-flash, gemini-3.1-pro, gemini-3.1-flash-lite
- Local Ollama: llama3, qwen2.5, gemma3, phi-4 (and any ollama tag)

Env vars:
- ANTHROPIC_API_KEY (for claude-* models)
- OPENAI_API_KEY (for gpt-* models)
- GEMINI_API_KEY (for gemini-* models)
- OLLAMA_URL (default http://localhost:11434)

Default model: gpt-5.4-mini (set via LLM_MODEL env var).
"""

import os
import re
import time
import httpx

# ---------------------------------------------------------------------------
# Provider catalog
# ---------------------------------------------------------------------------

# Maps friendly model names ??(provider, real_model_id)
MODEL_CATALOG = {
    # --- Claude (newest first) — auto-discovery below also adds any NEWER Claude models ---
    "claude-fable-5":    ("anthropic", "claude-fable-5"),
    "claude-opus-4-8":   ("anthropic", "claude-opus-4-8"),
    "claude-opus-4-7":   ("anthropic", "claude-opus-4-5"),
    "claude-sonnet-4-6": ("anthropic", "claude-sonnet-4-5"),
    "claude-haiku-4-5":  ("anthropic", "claude-haiku-4-5"),
    # --- OpenAI (5.x lineup — newest, updated 2026-06-16) ---
    "gpt-5.5":      ("openai", "gpt-5.5"),         # flagship: complex multi-domain reasoning/coding, 1M context
    "gpt-5.4":      ("openai", "gpt-5.4"),         # prev-gen frontier
    "gpt-5.4-mini": ("openai", "gpt-5.4-mini"),    # fast/low-cost, multimodal, 400k context
    "gpt-5.4-nano": ("openai", "gpt-5.4-nano"),    # smallest/cheapest, classification/extraction
    # --- Google Gemini (3.x lineup — newest, updated 2026-06-16) ---
    "gemini-3.5-flash":          ("gemini", "gemini-3.5-flash"),           # GA: token-efficient, high intelligence/$, multi-turn agentic
    "gemini-3.1-pro":            ("gemini", "gemini-3.1-pro-preview"),     # most capable: complex reasoning/coding (Preview)
    "gemini-3.1-flash-lite":     ("gemini", "gemini-3.1-flash-lite"),      # high-volume, max speed, very low cost
    "gemini-3.1-flash-image":    ("gemini", "gemini-3.1-flash-image"),     # native fast multimodal / image understanding
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

DEFAULT_MODEL_NAME = "gpt-5.4-mini"

ANTHROPIC_BASE = "https://api.anthropic.com/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

_last_used = {"provider": "none", "model": "none"}  # reload tag v2

# Budget-aware "smart-first" pipeline state. When OpenAI (the paid, smarter
# model) reports it's out of money/quota, we set a cooldown and automatically
# use Groq (free) until the cooldown expires — then we retry OpenAI (money may
# have been topped up). This makes the switch fully automatic in both directions.
_paid_state = {"cooldown_until": 0.0, "reason": ""}


def get_last_provider() -> str:
    return f"{_last_used['provider']} ({_last_used['model']})"


def _smart_model_name() -> str:
    """The paid, smarter model to prefer. Configurable via SMART_LLM_MODEL on
    Render (e.g. 'gpt-5.5', 'gpt-5.4') — defaults to gpt-5.5 (flagship)."""
    return _env("SMART_LLM_MODEL") or "gpt-5.5"


def _smart_enabled() -> bool:
    """Smart-first ON when SMART_LLM_ENABLED is truthy, or (default) whenever an
    OpenAI key is present. Set SMART_LLM_ENABLED=0 to force Groq-only."""
    v = _env("SMART_LLM_ENABLED").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return bool(_env("OPENAI_API_KEY") or _env("LLM_API_KEY"))


def _is_no_money(text: str) -> bool:
    """Detect 'budget finished' style failures from OpenAI."""
    t = (text or "").lower()
    return any(k in t for k in (
        "insufficient_quota", "exceeded your current quota", "quota",
        "billing", "insufficient funds", "payment required", " 402",
    ))


def get_budget_status() -> dict:
    """For diagnostics: is the paid model active, or are we in Groq-fallback?"""
    now = time.time()
    in_cooldown = now < _paid_state["cooldown_until"]
    return {
        "smart_enabled": _smart_enabled(),
        "smart_model": _smart_model_name(),
        "paid_active": _smart_enabled() and not in_cooldown,
        "using": "groq (budget cooldown)" if in_cooldown else ("openai (paid)" if _smart_enabled() else "default"),
        "cooldown_seconds_left": max(0, int(_paid_state["cooldown_until"] - now)),
        "reason": _paid_state.get("reason", ""),
    }


# ---- Auto-discovery: query EACH provider's /models API so a newly-launched model appears in the
# picker + routes WITHOUT any code change. Cached ~1h per provider; silent on missing key/error.
# Each discover_* returns (friendly, real) pairs, filtered to CHAT models (no embeddings/tts/etc.)
# and skipping dated snapshots (…-2024-08-06 / …-20251101) to keep the list clean. ----
_discovery_cache: dict[str, dict] = {}
_MODEL_YEAR: dict[str, int] = {}     # friendly id → release year (from each provider's /models API)
# Skip dated/snapshot suffixes (…-2024-08-06, …-20251101, …-1106, …-0125, …-001) — clutter/dupes.
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|-\d{3,8}$")


def _year_from_ts(ts) -> int | None:
    try:
        import datetime as _dt
        return _dt.datetime.utcfromtimestamp(int(ts)).year
    except Exception:
        return None


def _base_id(mid: str) -> str:
    """Strip a trailing date/snapshot (…-2026-03-17, …-20251001) → the undated base id, so an
    API-dated variant also dates the static friendly name (e.g. claude-haiku-4-5-20251001 →
    claude-haiku-4-5)."""
    return re.sub(r"-\d{4}-\d{2}-\d{2}$|-\d{6,8}$", "", mid)


def _record_year(mid: str, year: int | None) -> None:
    if year:
        _MODEL_YEAR[mid] = year
        _MODEL_YEAR.setdefault(_base_id(mid), year)


def _disc_get(provider: str):
    c = _discovery_cache.get(provider)
    return c["ids"] if c and (time.time() - c["ts"] < 3600) else None


def _disc_set(provider: str, pairs: list):
    _discovery_cache[provider] = {"ts": time.time(), "ids": pairs}
    return pairs


def _disc_stale(provider: str) -> list:
    return (_discovery_cache.get(provider) or {}).get("ids", [])


def _discover_anthropic() -> list:
    key = _env("ANTHROPIC_API_KEY")
    if not key:
        return []
    if (c := _disc_get("anthropic")) is not None:
        return c
    try:
        r = httpx.get(f"{ANTHROPIC_BASE}/models",
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=8.0)
        r.raise_for_status()
        pairs = []
        for m in (r.json().get("data") or []):
            mid = m.get("id")
            if not mid:
                continue
            ca = (m.get("created_at") or "")[:4]
            _record_year(mid, int(ca) if ca.isdigit() else None)   # dates the base too
            if _DATE_RE.search(mid):
                continue                                            # don't show dated snapshots
            pairs.append((mid, mid))
        return _disc_set("anthropic", pairs)
    except Exception:
        return _disc_stale("anthropic")


def _discover_openai() -> list:
    key = _env("OPENAI_API_KEY") or _env("LLM_API_KEY")
    if not key:
        return []
    if (c := _disc_get("openai")) is not None:
        return c
    try:
        base = (_env("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        r = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=8.0)
        r.raise_for_status()
        pairs = []
        for m in (r.json().get("data") or []):
            mid = m.get("id") or ""
            if not re.match(r"^(gpt-|o\d|chatgpt)", mid):
                continue
            if re.search(r"(embed|whisper|tts|audio|realtime|image|dall-e|moderation|transcribe|search|instruct)", mid):
                continue
            _record_year(mid, _year_from_ts(m.get("created")))     # dates the base too
            if _DATE_RE.search(mid):
                continue
            pairs.append((mid, mid))
        return _disc_set("openai", pairs)
    except Exception:
        return _disc_stale("openai")


def _discover_gemini() -> list:
    key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
    if not key:
        return []
    if (c := _disc_get("gemini")) is not None:
        return c
    try:
        r = httpx.get(f"{GEMINI_BASE}/models?key={key}&pageSize=200", timeout=8.0)
        r.raise_for_status()
        pairs = []
        for m in (r.json().get("models") or []):
            name = (m.get("name") or "").replace("models/", "")
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            if (not name.startswith("gemini")
                    or re.search(r"(embed|aqa|imagen|tts|robotics|computer-use|omni)", name)
                    or _DATE_RE.search(name)):
                continue
            pairs.append((name, name))
        return _disc_set("gemini", pairs)
    except Exception:
        return _disc_stale("gemini")


def _discover_groq() -> list:
    key = _env("GROQ_API_KEY")
    if not key:
        return []
    if (c := _disc_get("groq")) is not None:
        return c
    try:
        r = httpx.get("https://api.groq.com/openai/v1/models",
                      headers={"Authorization": f"Bearer {key}"}, timeout=8.0)
        r.raise_for_status()
        pairs = []
        for m in (r.json().get("data") or []):
            mid = m.get("id") or ""
            if not mid or re.search(r"(whisper|tts|guard|embed|prompt|orpheus|playai)", mid):
                continue
            fr = mid if mid.startswith("groq-") else f"groq-{mid}"
            pairs.append((fr, mid))
            yv = _year_from_ts(m.get("created"))
            _record_year(fr, yv); _record_year(mid, yv)     # date friendly AND real id
        return _disc_set("groq", pairs)
    except Exception:
        return _disc_stale("groq")


def _discover_and_register_all() -> None:
    """Query every provider's /models API and self-register new CHAT models into MODEL_CATALOG
    (so they show in the picker AND route correctly). Newly-launched models appear with ZERO
    code change. Cached per provider; a missing key or error for one provider never blocks others."""
    known_real = {real for (_p, real) in MODEL_CATALOG.values()}
    for provider, discover in (("anthropic", _discover_anthropic), ("openai", _discover_openai),
                               ("gemini", _discover_gemini), ("groq", _discover_groq)):
        try:
            for friendly, real in discover():
                if friendly and friendly not in MODEL_CATALOG and real not in known_real:
                    MODEL_CATALOG[friendly] = (provider, real)
                    known_real.add(real)
        except Exception:
            continue


_FAM_STOP = {"preview", "latest", "exp", "experimental", "beta", "stable"}


def _family_version(mid: str) -> tuple:
    """(family_key, version_tuple) for a model id. Family = tier words (mini/pro/flash/opus/
    sonnet/haiku + sizes like 70b); version = the numbers. So 'claude-sonnet-4-6' and
    'claude-sonnet-5' share family 'claude-sonnet' (keep higher version), while 'llama-70b'
    vs 'llama-8b' stay separate tiers. 'preview'/'latest' are ignored (version modifiers)."""
    s = mid.lower().replace("/", "-")
    fam, ver = [], []
    for t in re.split(r"[-_.]", s):
        if not t or t in _FAM_STOP:
            continue
        if t.isdigit():
            ver.append(int(t)); continue
        m = re.fullmatch(r"(\d+)([a-z]+)", t)           # 4o / 70b / 16k
        if m:
            n, suf = int(m.group(1)), m.group(2)
            if suf in ("b", "x"):
                fam.append(t)                           # size tier → keep whole
            else:
                ver.append(n); fam.append(suf)          # 4o → version 4 + 'o'
            continue
        m = re.fullmatch(r"([a-z]+)(\d+)", t)           # o1 / o3 / qwen3
        if m:
            fam.append(m.group(1)); ver.append(int(m.group(2))); continue
        fam.append(t)
    return "-".join(fam), tuple(ver)


def _collapse_to_latest(catalog: list[dict]) -> list[dict]:
    """Keep only the NEWEST version per (provider, family): Sonnet 5 replaces Sonnet 4.x,
    Gemini 3.1 Pro replaces 2.5 Pro. Capability/size tiers (pro/flash/mini/70b…) stay separate."""
    best: dict = {}
    for m in catalog:
        fam, ver = _family_version(m["id"])
        key = (m["provider"], fam)
        cur = best.get(key)
        if cur is None or ver > cur[1] or (ver == cur[1] and len(m["id"]) < len(cur[0]["id"])):
            best[key] = (m, ver)
    kept = {id(b[0]) for b in best.values()}
    return [m for m in catalog if id(m) in kept]         # keep original order


def _min_year() -> int:
    try:
        return int(_env("MIN_MODEL_YEAR", "2026"))       # bump next year via env, no redeploy
    except Exception:
        return 2026


def _keep_recent(m: dict) -> bool:
    """Show only current-year+ models (default 2026). Local ollama always kept. Uses the
    provider-reported release year; Gemini (no API date) → major version >= 3 counts as 2026;
    unknown year → kept (never hide a model we can't date)."""
    if m["provider"] == "ollama":
        return False                                     # local 2024-era open models → hide (not 2026)
    y = _MODEL_YEAR.get(m["id"])
    if y is None:
        y = _MODEL_YEAR.get(m.get("real_model") or "")   # groq: friendly≠real, date by real too
    if y is not None:
        return y >= _min_year()
    if m["provider"] == "gemini":                        # no API date → version rule (3.x = 2026)
        mm = re.search(r"gemini-(\d+)", m["id"])
        return (int(mm.group(1)) >= 3) if mm else True
    if m["provider"] == "groq":                          # groq IS datable via API → undatable = stale/old
        return False
    return True


def list_available_models() -> list[dict]:
    """Return catalog of models with availability flags. Reads env vars at call time."""
    _discover_and_register_all()                        # auto-add newly-launched models (all providers)
    has_openai    = bool(_env("OPENAI_API_KEY") or _env("LLM_API_KEY"))
    has_anthropic = bool(_env("ANTHROPIC_API_KEY"))
    has_gemini    = bool(_env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"))
    has_groq      = bool(_env("GROQ_API_KEY"))
    catalog = []
    for friendly, (provider, real) in MODEL_CATALOG.items():
        available = (
            (provider == "openai" and has_openai) or
            (provider == "anthropic" and has_anthropic) or
            (provider == "gemini" and has_gemini) or
            (provider == "groq" and has_groq) or
            provider == "ollama"
        )
        catalog.append({
            "id": friendly, "provider": provider, "real_model": real,
            "available": available, "year": _MODEL_YEAR.get(friendly),
        })
    collapsed = _collapse_to_latest(catalog)            # newest per family
    return [m for m in collapsed if _keep_recent(m)]    # then keep only 2026+ (MIN_MODEL_YEAR)


def ping_model(model: str, timeout: float = 45.0) -> dict:
    """Call ONE model directly with NO fallback, to verify it actually works.

    Used by the /twins/llm/ping diagnostic. Unlike chat_completion_sync, this
    never falls back to another provider — so a broken/typo'd model id surfaces
    as ok=False instead of being masked by a fallback response.
    """
    # Only catalog models may be pinged — never forward an arbitrary, caller-
    # supplied model id to a paid provider API.
    if model not in MODEL_CATALOG:
        return {"model": model, "provider": None, "real_model": None, "ok": False,
                "latency_ms": 0, "sample": None, "error": "unknown model (not in catalog)"}
    provider, real = MODEL_CATALOG[model]

    sys = "You are a health check. Reply with exactly: OK"
    msgs = [{"role": "user", "content": "Reply with the single word: OK"}]
    t0 = time.monotonic()
    ok, result = False, "unsupported provider"
    try:
        if provider == "openai":
            key = _env("OPENAI_API_KEY") or _env("LLM_API_KEY")
            base = _env("LLM_BASE_URL") or "https://api.openai.com/v1"
            if not key:
                ok, result = False, "OPENAI_API_KEY not set"
            else:
                ok, result = _call_openai_compatible(
                    base, key, real, [{"role": "system", "content": sys}] + msgs, 16, 0.0, timeout)
        elif provider == "gemini":
            ok, result = _call_gemini(real, sys, msgs, 16, 0.0, timeout)
        elif provider == "anthropic":
            ok, result = _call_anthropic(real, sys, msgs, 16, 0.0, timeout)
        elif provider == "groq":
            key = _env("GROQ_API_KEY")
            if not key:
                ok, result = False, "GROQ_API_KEY not set"
            else:
                ok, result = _call_openai_compatible(
                    "https://api.groq.com/openai/v1", key, real,
                    [{"role": "system", "content": sys}] + msgs, 16, 0.0, timeout)
        elif provider == "ollama":
            ok, result = False, "ollama not reachable from server diagnostic"
    except Exception as e:
        ok, result = False, str(e)

    return {
        "model": model, "provider": provider, "real_model": real, "ok": bool(ok),
        "latency_ms": int((time.monotonic() - t0) * 1000),
        "sample": (str(result)[:80] if ok else None),
        "error": (None if ok else str(result)[:300]),
    }


# ---------------------------------------------------------------------------
# Provider call functions
# ---------------------------------------------------------------------------

def _call_openai_compatible(base_url: str, api_key: str, model: str, messages: list[dict],
                            max_tokens: int, temperature: float, timeout: float) -> tuple[bool, str]:
    """OpenAI-style /chat/completions (works for OpenAI + Groq + Ollama)."""
    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload: dict = {"model": model, "messages": messages}
        # OpenAI's gpt-5.x / o-series reasoning models renamed the token cap to
        # `max_completion_tokens` and only accept the default temperature. Older
        # models (gpt-4o) and Groq/Ollama still use `max_tokens` + temperature.
        if model.startswith(("gpt-5", "o1", "o3", "o4")):
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens
            payload["temperature"] = temperature
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
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
    model: str = "gpt-5.4-mini",
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
    model: str = "gemini-3.5-flash",   # cost guard: Flash reads images fine; Pro-preview is ~10x pricier
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

    Model defaults to gemini-3.1-pro-preview because the cheap Flash tier
    frequently refuses to describe images of UI/data; Pro reliably returns
    a useful response. Pass `model="gemini-3.5-flash"` to override for
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
    # Thinking models (gemini *pro* / *preview*) spend output budget on internal
    # reasoning, so a low ceiling yields an empty text part. Give them headroom.
    out_tokens = max_tokens
    if "pro" in model or "preview" in model:
        out_tokens = max(max_tokens, 8192)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{GEMINI_BASE}/models/{model}:generateContent?key={gemini_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "contents": contents,
                    "generationConfig": {
                        "maxOutputTokens": out_tokens,
                        "temperature": temperature,
                    },
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                cands = data.get("candidates") or []
                # Extract any text parts (thinking models may also include
                # non-text 'thought' parts which we skip).
                if cands:
                    parts = (cands[0].get("content", {}) or {}).get("parts") or []
                    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text"))
                    if text.strip():
                        return True, text
                    finish = cands[0].get("finishReason")
                    return False, f"Empty Gemini response (finishReason={finish})"
                return False, "Empty Gemini response (no candidates)"
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
    prefer_paid: bool = False,
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

    # === BUDGET-AWARE SMART PIPELINE (REPORTS ONLY) ===
    # Only when the caller opts in with prefer_paid=True (the daily reports do;
    # the realtime chatbot/assistant stays on fast Groq). If we have money
    # (OpenAI key + not in budget cooldown), use the SMARTER paid model FIRST.
    # When OpenAI is out of quota/budget, set a 1-hour cooldown and fall through
    # to Groq — and automatically retry OpenAI after the cooldown.
    if prefer_paid and _smart_enabled() and openai_key and time.time() >= _paid_state["cooldown_until"]:
        smart = _smart_model_name()
        ok, result = _call_openai_compatible(openai_base, openai_key, smart,
                                             full_messages_with_sys, max_tokens, temperature, 90.0)
        if ok:
            _last_used.update({"provider": "openai", "model": f"{smart} (smart)"})
            _paid_state["reason"] = ""
            return result
        if _is_no_money(result):
            _paid_state["cooldown_until"] = time.time() + 3600   # 1h on Groq, then retry OpenAI
            _paid_state["reason"] = f"budget finished → Groq ({str(result)[:80]})"
        else:
            _paid_state["cooldown_until"] = time.time() + 300    # 5min for transient/other errors
            _paid_state["reason"] = f"OpenAI error → Groq briefly ({str(result)[:80]})"
        attempt_log.append(f"{smart} (smart primary): {str(result)[:180]}")

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
        ok, result = _call_gemini("gemini-3.5-flash", system_prompt, messages,
                                  max_tokens, temperature)
        if ok:
            _last_used.update({"provider": "gemini", "model": "gemini-3.5-flash (free fallback)"})
            return result
        attempt_log.append(f"gemini-3.5-flash (free fallback): {str(result)[:200]}")

    # Paid tier — OpenAI gpt-5.4-mini (only fires if you've topped up credits)
    if openai_key:
        ok, result = _call_openai_compatible(openai_base, openai_key, "gpt-5.4-mini",
                                             full_messages_with_sys, max_tokens, temperature, 30.0)
        if ok:
            _last_used.update({"provider": "openai", "model": "gpt-5.4-mini (fallback)"})
            return result
        attempt_log.append(f"gpt-5.4-mini (openai fallback): {str(result)[:200]}")

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
