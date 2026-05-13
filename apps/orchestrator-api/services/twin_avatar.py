"""
VIP AI Platform — Twin Avatar Service (v4 Phase 3 — first pass)
Generates deterministic SVG avatars for every twin + worker, keyed on
a stable seed so the same person always gets the same face.

Uses DiceBear's free CDN — no API key, no setup. Two styles wired:
  - "personas" (illustrated human-like portrait)
  - "avataaars" (classic cartoon)

Operator picks style via DICEBEAR_STYLE env. Real photo uploads still
overwrite this when the worker uploads their own.

For Phase 3 'realistic talking-head' avatars (D-ID / HeyGen), that's a
separate paid integration tracked as a future Ruflo task.
"""

from __future__ import annotations

import hashlib
import os
import urllib.parse
from typing import Optional


_STYLE = os.getenv("DICEBEAR_STYLE", "personas")  # personas | avataaars | bottts | adventurer
_BASE = "https://api.dicebear.com/9.x"


def url_for_seed(seed: str, size: int = 200, background: Optional[str] = None) -> str:
    """Return a stable SVG URL for the given seed. Use twin.id (UUID
    string) as the seed so every twin keeps the same face.
    """
    if not seed:
        seed = "default"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]
    params = {
        "seed": digest,
        "size": str(size),
        "radius": "50",
    }
    if background:
        params["backgroundColor"] = background.lstrip("#")
    qs = urllib.parse.urlencode(params)
    return f"{_BASE}/{_STYLE}/svg?{qs}"


def url_for_twin(twin_id: str, twin_name: Optional[str] = None) -> str:
    """Convenience: combine twin id + name for a richer seed."""
    seed = f"{twin_id}-{twin_name or ''}"
    return url_for_seed(seed)


def url_for_worker(user_id: str, user_name: Optional[str] = None) -> str:
    seed = f"worker-{user_id}-{user_name or ''}"
    return url_for_seed(seed, background="EEF2FF")
