"""
VIP AI Platform — Embedding service (semantic memory for twins).

Turns text into vectors so a twin can retrieve knowledge by MEANING, not keyword
matching. Uses OpenAI `text-embedding-3-small` (1536-dim, cheap) over plain HTTP
— the same OPENAI_API_KEY the rest of the platform already uses. Everything here
is fail-safe: if the key is missing or a call errors, functions return None and
the caller falls back to keyword selection, so chat never breaks.
"""

import os
import math
import httpx
from typing import Optional

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = 1536  # text-embedding-3-small


def _key() -> str:
    return os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or ""


def _base() -> str:
    return os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1"


def available() -> bool:
    return bool(_key())


def embed_text(text: str, timeout: float = 20.0) -> Optional[list]:
    """Embed a single string. None on any failure (caller falls back)."""
    out = embed_texts([text], timeout=timeout)
    return out[0] if out else None


def embed_texts(texts: list, timeout: float = 30.0) -> Optional[list]:
    """Embed a batch. Returns list[vector] aligned to inputs, or None on failure.

    Truncates each input to ~8000 chars (well under the model's token limit) so a
    huge document never errors the call.
    """
    key = _key()
    if not key or not texts:
        return None
    cleaned = [(t or "").strip()[:8000] or " " for t in texts]
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{_base()}/embeddings",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": EMBED_MODEL, "input": cleaned},
            )
            if resp.status_code != 200:
                return None
            data = resp.json().get("data", [])
            # API returns items with an `index`; keep input order.
            data.sort(key=lambda d: d.get("index", 0))
            return [d["embedding"] for d in data]
    except Exception:
        return None


def cosine(a: list, b: list) -> float:
    """Cosine similarity in [-1, 1]. 0 if either is empty/mismatched."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
