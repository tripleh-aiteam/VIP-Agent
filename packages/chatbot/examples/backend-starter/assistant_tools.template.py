"""
assistant_tools.template.py — copy to services/assistant_tools.py and
register the tools your agent needs.

A tool is anything the LLM can call. Two kinds:

  * READ  — runs immediately, result is fed back to the LLM which composes
            the final natural-language reply. Use for searches, lookups,
            counts, navigation (yes, navigate is "read" because it doesn't
            mutate server state — it just returns an action for the
            frontend to execute).

  * WRITE — when the LLM picks one, the backend DOES NOT execute. Instead
            it returns a `proposed_action` so the frontend can render a
            Confirm card. The user clicks Confirm → widget re-calls
            /chat/agent with `confirmed_tool` + `confirmed_args`.

Tool schema is JSON-Schema-ish; only top-level `type: object` with
`properties` + `required` is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional
from sqlalchemy.orm import Session


@dataclass
class Tool:
    name: str
    description: str
    kind: str           # "read" | "write"
    parameters: dict    # JSON schema (object, properties, required)
    fn: Callable        # signature: (**kwargs, db=None) -> dict
    requires_confirmation: bool = False  # auto-true when kind=="write"

    def __post_init__(self):
        if self.kind == "write":
            self.requires_confirmation = True


# ============================================================================
#  Universal tools — every agent gets these. Copy verbatim.
# ============================================================================

def tool_navigate(path: str, query: str = "", db: Session = None, **_kw) -> dict[str, Any]:
    """Navigate the host UI to an internal page (optionally with query params)."""
    from services.assistant_manifest import is_valid_path
    if not is_valid_path(path):
        return {"ok": False, "error": f"Unknown path '{path}'."}
    full = path if not query else f"{path}?{query}"
    return {
        "ok": True,
        "action": {"type": "navigate", "to": full},
        "message": f"Opening {path}.",
    }


def tool_open_portal(agent: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Open one of the EXTERNAL agent apps in a new browser tab."""
    from services.assistant_manifest import get_agent_by_name
    a = get_agent_by_name(agent)
    if not a:
        return {"ok": False, "error": f"No external agent named '{agent}'."}
    return {
        "ok": True,
        "action": {"type": "navigate", "to": a["portal_url"], "external": True},
        "message": f"Opening the {a['name']} in a new tab.",
        "url": a["portal_url"],
    }


def tool_list_pages(db: Session = None, **_kw) -> dict[str, Any]:
    """Return every page the assistant can navigate to."""
    from services.assistant_manifest import get_all_pages
    return {"ok": True, "pages": get_all_pages(include_hidden=False)}


def tool_what_can_you_do(db: Session = None, **_kw) -> dict[str, Any]:
    """Self-introspection — list every tool the assistant knows."""
    return {
        "ok": True,
        "tools": [
            {"name": t.name, "kind": t.kind, "description": t.description}
            for t in TOOL_REGISTRY.values()
        ],
    }


# ============================================================================
#  Agent-specific tools — ADD YOURS HERE
# ============================================================================
#
# Pattern: write a function that takes named kwargs + db=None, returns a
# dict like {"ok": True/False, ...your fields...}. Then register it in
# TOOL_REGISTRY below.

# Example READ tool:
def tool_search_records(query: str, limit: int = 10, db: Session = None, **_kw) -> dict[str, Any]:
    """Find records matching a text query in YOUR domain table."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    # TODO — replace with your real query
    # from db.models import YourTable
    # matches = db.query(YourTable).filter(YourTable.name.ilike(f"%{query}%")).limit(limit).all()
    return {"ok": True, "query": query, "count": 0, "matches": []}


# Example WRITE tool:
def tool_send_notification(recipient: str, body: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Send a notification — returns proposed_action for user confirm first."""
    if not recipient or not body:
        return {"ok": False, "error": "Both recipient and body required."}
    # TODO — actual send logic; this fn only runs after user confirms
    return {"ok": True, "message": f"Sent to {recipient}."}


# ============================================================================
#  Registry — every tool MUST be added here for the LLM to see it
# ============================================================================

TOOL_REGISTRY: dict[str, Tool] = {
    "navigate": Tool(
        name="navigate", kind="read",
        description="Navigate the host UI to an internal page.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Route path, e.g. '/reports'"},
                "query": {"type": "string", "description": "Optional URL search params"},
            },
            "required": ["path"],
        },
        fn=tool_navigate,
    ),
    "open_portal": Tool(
        name="open_portal", kind="read",
        description="Open an EXTERNAL agent web app in a new tab.",
        parameters={
            "type": "object",
            "properties": {"agent": {"type": "string", "description": "External agent name"}},
            "required": ["agent"],
        },
        fn=tool_open_portal,
    ),
    "list_pages": Tool(
        name="list_pages", kind="read",
        description="List all pages the assistant can navigate to.",
        parameters={"type": "object", "properties": {}},
        fn=tool_list_pages,
    ),
    "what_can_you_do": Tool(
        name="what_can_you_do", kind="read",
        description="Self-introspection — what tools does the assistant have.",
        parameters={"type": "object", "properties": {}},
        fn=tool_what_can_you_do,
    ),

    # TODO — register your agent-specific tools below
    "search_records": Tool(
        name="search_records", kind="read",
        description="Find records by text query in the main domain table.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
        fn=tool_search_records,
    ),
    "send_notification": Tool(
        name="send_notification", kind="write",
        description="Send a notification to a user. Requires user confirmation.",
        parameters={
            "type": "object",
            "properties": {
                "recipient": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["recipient", "body"],
        },
        fn=tool_send_notification,
    ),
}


# ============================================================================
#  Execution shim — used by the generic assistant_agent. Don't edit.
# ============================================================================

def execute_tool(tool_name: str, args: dict, db: Session = None) -> dict[str, Any]:
    if tool_name not in TOOL_REGISTRY:
        return {"ok": False, "error": f"Unknown tool '{tool_name}'."}
    tool = TOOL_REGISTRY[tool_name]
    try:
        return tool.fn(**(args or {}), db=db)
    except TypeError as e:
        return {"ok": False, "error": f"Bad args for '{tool_name}': {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Tool '{tool_name}' raised: {e}"[:200]}


def list_tool_schemas() -> list[dict]:
    return [
        {
            "name": t.name,
            "kind": t.kind,
            "description": t.description,
            "parameters": t.parameters,
            "requires_confirmation": t.requires_confirmation,
        }
        for t in TOOL_REGISTRY.values()
    ]
