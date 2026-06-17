"""
VIP AI Platform — Watch & Learn (passive observation → knowledge).

The "sit silently and watch" engine: a worker's AI sessions (Claude Code,
ChatGPT, coding diffs, notes) are pushed to POST /twins/{id}/observe, and this
service DISTILLS the raw text into a few clean, reusable knowledge items —
decisions made, approaches/patterns, preferences/style, important facts — and
stores them (which auto-embeds them into the twin's vector memory).

Distillation matters: raw sessions are noisy. We use the LLM to keep only the
signal a future twin would need to act like the person, and drop chit-chat.

Privacy: the /observe endpoint is owner-only — only the worker feeds their own
twin; the boss never sees this content (see routers/twins.py privacy wall).
"""

import json
from typing import Optional

from sqlalchemy.orm import Session

from services import twin_service
from services.llm_client import chat_completion_sync
from services.logger import log

MAX_INPUT_CHARS = 24000   # keep distillation prompt bounded
MAX_ITEMS = 6             # per session — avoid flooding the knowledge base

_DISTILL_SYSTEM = (
    "You distill a person's work session into reusable knowledge for their digital twin. "
    "From the material, extract ONLY durable, reusable signal that would help an AI act like "
    "this person later: decisions they made and why, approaches/patterns/conventions they use, "
    "their preferences and communication/coding style, and important facts about their projects. "
    "IGNORE greetings, filler, transient debugging chatter, and anything not reusable. "
    f"Return STRICT JSON: a list of at most {MAX_ITEMS} objects, each "
    '{"title": "<short label>", "content": "<1-3 sentence reusable insight>", '
    '"type": "decision|instruction|style|document"}. '
    "If there is nothing worth keeping, return []."
)


def _source_type(t: str) -> str:
    return t if t in ("decision", "instruction", "style", "document") else "document"


# Phase 1 — supported learning sources. The capture client tags each push with
# one of these; we record it in the knowledge title ("[source] …") so origin is
# traceable and a per-source breakdown can be shown without a schema change.
SUPPORTED_SOURCES = {
    "claude-code": "Claude Code",
    "chatgpt": "ChatGPT",
    "claude-cowork": "Claude Cowork",
    "google-drive": "Google Drive",
    "google-calendar": "Google Calendar",
    "gmail": "Gmail",
    "notion": "Notion",
    "notes": "Notes",
}


def norm_source(s: str) -> str:
    """Normalize a free-form source label to a known key (defaults to 'notes')."""
    k = (s or "").strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "claude": "claude-code", "claudecode": "claude-code", "claude-code": "claude-code",
        "cowork": "claude-cowork", "claude-cowork": "claude-cowork", "claude-desktop": "claude-cowork",
        "gpt": "chatgpt", "openai": "chatgpt", "chat-gpt": "chatgpt", "chatgpt": "chatgpt",
        "gdrive": "google-drive", "drive": "google-drive", "google-drive": "google-drive",
        "calendar": "google-calendar", "gcal": "google-calendar", "google-calendar": "google-calendar",
        "mail": "gmail", "email": "gmail", "gmail": "gmail",
        "notion": "notion", "session": "claude-code", "ai_session": "claude-code",
    }
    return aliases.get(k, k if k in SUPPORTED_SOURCES else "notes")


def distill(raw_text: str, source: str = "session") -> list:
    """LLM-distill raw session text → list of {title, content, type}. [] on failure."""
    text = (raw_text or "").strip()
    if len(text) < 40:
        return []
    text = text[:MAX_INPUT_CHARS]
    user = f"Source: {source}\n\n--- WORK SESSION ---\n{text}\n--- END ---\n\nReturn the JSON list."
    try:
        out = chat_completion_sync(
            system_prompt=_DISTILL_SYSTEM,
            messages=[{"role": "user", "content": user}],
            max_tokens=900, temperature=0.2,
        )
    except Exception as e:
        log.warning(f"watch_learn.distill: LLM failed: {e}")
        return []
    return _parse_items(out)


def _parse_items(out: str) -> list:
    """Tolerantly pull a JSON list of items out of the model output."""
    if not out:
        return []
    s = out.strip()
    # Strip code fences if present.
    if s.startswith("```"):
        s = s.split("```")[1] if "```" in s[3:] else s
        s = s.lstrip("json").strip()
    # Grab the outermost [ ... ].
    a, b = s.find("["), s.rfind("]")
    if a == -1 or b == -1 or b <= a:
        return []
    try:
        data = json.loads(s[a:b + 1])
    except Exception:
        return []
    items = []
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()[:200]
        content = (it.get("content") or "").strip()
        if not content:
            continue
        items.append({
            "title": title or content[:60],
            "content": content[:2000],
            "type": _source_type((it.get("type") or "document").strip().lower()),
        })
    return items[:MAX_ITEMS]


def observe_and_learn(db: Session, twin_id, raw_text: str, source: str = "session",
                      kind: str = "ai_session") -> dict:
    """Distill a raw observation and store the resulting knowledge (auto-embedded).

    Returns {ok, learned, items:[titles]}. Best-effort; never raises.
    """
    src = norm_source(source)
    tag = f"[{src}] "
    items = distill(raw_text, source=source)
    stored = 0
    titles = []
    for it in items:
        try:
            # Prefix the origin source so it's traceable + a per-source breakdown
            # is possible (e.g. "[chatgpt] …", "[notion] …"). Keep title <= 255.
            base = it["title"]
            title = base if base.startswith(tag) else (tag + base)[:255]
            twin_service.add_knowledge(
                db, twin_id,
                title=title,
                content=it["content"],
                source_type=it["type"],
            )
            stored += 1
            titles.append(title)
        except Exception as e:
            log.warning(f"observe_and_learn: store failed: {e}")
    if stored:
        try:
            twin_service.log_activity(
                db, twin_id, "watch_learn",
                f"Learned {stored} item(s) from {source} ({kind})",
                {"source": source, "kind": kind, "learned": stored},
            )
        except Exception:
            pass
        db.commit()
    return {"ok": True, "learned": stored, "items": titles}
