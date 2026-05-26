"""
assistant_manifest.template.py — copy to services/assistant_manifest.py
and edit PAGES + EXTERNAL_AGENTS + AGENT_IDENTITY for your agent.

This is the SINGLE SOURCE OF TRUTH for what your assistant can navigate
to and what it represents. The shared assistant_agent.py reads from this
at request time, so adding a new page = adding a dict entry here +
restart. No new intent definitions or per-page handlers needed.
"""

from __future__ import annotations

import os


# ============================================================================
#  Agent identity — what the assistant says when it introduces itself
# ============================================================================
#
# Override per-deployment via env vars so the same code can be reused
# across staging / prod / per-tenant brandings without an edit.
# ============================================================================

AGENT_IDENTITY: dict[str, str] = {
    "name":    os.getenv("ASSISTANT_AGENT_NAME",    "Your Agent Assistant"),
    "tagline": os.getenv("ASSISTANT_AGENT_TAGLINE", "your AI co-pilot for <what your platform does>"),
    "scope":   os.getenv("ASSISTANT_AGENT_SCOPE",
        "You can navigate the dashboard, fetch live data, search internal "
        "records, and perform actions on the user's behalf (with their permission)."
    ),
}


def get_agent_identity() -> dict[str, str]:
    """Read env vars at request time so changes apply without restart."""
    return {
        "name":    os.getenv("ASSISTANT_AGENT_NAME",    AGENT_IDENTITY["name"]),
        "tagline": os.getenv("ASSISTANT_AGENT_TAGLINE", AGENT_IDENTITY["tagline"]),
        "scope":   os.getenv("ASSISTANT_AGENT_SCOPE",   AGENT_IDENTITY["scope"]),
    }


# ============================================================================
#  Internal pages — every route the assistant can `navigate()` to
# ============================================================================
#
# Fields:
#   path          — the route the frontend navigates to
#   name          — human-readable display name
#   description   — what the page is FOR (the LLM uses this to decide if relevant)
#   keywords      — synonyms the user might say (EN + KO + any other lang)
#   capabilities  — high-level actions available on the page (LLM hint only)
#   sidebar       — True if visible in the left nav; False for hidden routes
#   priority      — display order (lower = more important)
#   sub_tabs      — sections within the page (the LLM can deep-link to them)
#   dynamic_routes — templated routes like /reports/{report_id}
# ============================================================================

PAGES: list[dict] = [
    {
        "path": "/",
        "name": "Dashboard",
        "description": "Home page — overview cards, alerts, latest activity.",
        "keywords": ["dashboard", "home", "overview", "메인", "홈"],
        "capabilities": ["see_briefing", "see_alerts"],
        "sidebar": True,
        "priority": 1,
    },
    # TODO — add one dict per page in your agent
    # Example with sub-tabs and dynamic routes:
    # {
    #     "path": "/reports",
    #     "name": "Reports",
    #     "description": "All generated reports — daily, weekly, custom.",
    #     "keywords": ["reports", "리포트", "보고서"],
    #     "capabilities": ["list_reports", "download_report"],
    #     "sidebar": True,
    #     "priority": 2,
    #     "sub_tabs": [
    #         {"name": "Daily",   "id": "daily",   "description": "Daily briefing reports"},
    #         {"name": "Weekly",  "id": "weekly",  "description": "Weekly summary reports"},
    #     ],
    #     "dynamic_routes": [
    #         {"pattern": "/reports/{report_id}", "description": "Single report detail view"},
    #     ],
    # },
]


# ============================================================================
#  External agent web apps — separate deployed apps the assistant can open
# ============================================================================
#
# If your agent doesn't link out to other apps, leave this empty.
# ============================================================================

EXTERNAL_AGENTS: list[dict] = [
    # Example:
    # {
    #     "name": "MyOtherAgent",
    #     "name_ko": "다른에이전트",
    #     "description": "Sister app the user might want to open from here.",
    #     "portal_url": os.getenv("OTHER_AGENT_PORTAL_URL", "https://other.vercel.app"),
    #     "backend_url": os.getenv("OTHER_AGENT_BACKEND_URL", "https://other-api.onrender.com"),
    #     "keywords": ["other", "다른"],
    # },
]


# ============================================================================
#  Accessors — used by the generic assistant_agent. Don't edit unless you
#  know what you're doing.
# ============================================================================

def get_all_pages(include_hidden: bool = True) -> list[dict]:
    if include_hidden:
        return list(PAGES)
    return [p for p in PAGES if p.get("sidebar")]


def get_page_by_path(path: str) -> dict | None:
    for p in PAGES:
        if p["path"] == path:
            return p
    return None


def is_valid_path(path: str) -> bool:
    return get_page_by_path(path) is not None


def get_external_agents() -> list[dict]:
    return list(EXTERNAL_AGENTS)


def get_agent_by_name(name: str) -> dict | None:
    name_lower = (name or "").lower().strip()
    for a in EXTERNAL_AGENTS:
        if a["name"].lower() == name_lower or a.get("name_ko", "") == name_lower:
            return a
        for kw in a.get("keywords", []):
            if kw.lower() == name_lower:
                return a
    return None


def pages_summary_for_llm() -> str:
    """Compact summary the LLM uses to pick a path."""
    lines = []
    for p in PAGES:
        kw = ", ".join(p.get("keywords", [])[:6])
        lines.append(f"- {p['path']} ({p['name']}): {p['description']} [keywords: {kw}]")
        for st in p.get("sub_tabs") or []:
            lines.append(f"    • [tab] {st['name']} (id={st['id']}): {st['description']}")
        for dr in p.get("dynamic_routes") or []:
            lines.append(f"    • [dynamic] {dr['pattern']}: {dr['description']}")
    return "\n".join(lines)


def agents_summary_for_llm() -> str:
    """Compact summary of external agent portals."""
    lines = []
    for a in EXTERNAL_AGENTS:
        kw = ", ".join(a.get("keywords", [])[:6])
        lines.append(f"- {a['name']} ({a.get('name_ko', '')}): {a['description']} [keywords: {kw}]")
    return "\n".join(lines)
