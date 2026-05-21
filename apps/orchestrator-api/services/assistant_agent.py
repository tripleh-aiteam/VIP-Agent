"""
assistant_agent — the tool-calling loop that powers /chat/agent.

Flow:
  1. Receive user transcript + optional page context.
  2. Pass the FULL tool catalog (from assistant_tools.TOOL_REGISTRY)
     + manifest summary to the LLM as a system prompt.
  3. LLM returns either:
       - {"tool": "<name>", "args": {...}}     ← invoke a tool
       - {"answer": "<text>"}                  ← direct answer (no tool needed)
  4. If a tool is picked, execute it server-side.
  5. Feed the tool result back to the LLM for final answer composition.
  6. Return: { reply, action, tool_used, tool_result, intent }

The frontend widget receives the same shape as /chat/voice so it can use
this endpoint as a drop-in upgrade.

This module is provider-agnostic via llm_client. Defaults to Groq Llama
3.3 70B for sub-second latency.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from services.logger import log
from services.llm_client import chat_completion_sync
from services.assistant_tools import (
    TOOL_REGISTRY, list_tool_schemas, execute_tool,
)
from services.assistant_manifest import (
    pages_summary_for_llm, agents_summary_for_llm,
)


# ============================================================================
#  System prompt builder
# ============================================================================

def _build_system_prompt(current_path: Optional[str] = None) -> str:
    """Compose the system prompt the LLM sees on every request.

    Includes:
      - Role
      - Tool catalog
      - Manifest summary (pages + external agents)
      - Output format
      - Strict rules to prevent intent hallucination
    """
    tool_lines: list[str] = []
    for s in list_tool_schemas():
        param_names = list((s.get("parameters") or {}).get("properties", {}).keys())
        param_str = ", ".join(param_names) if param_names else "(no args)"
        tool_lines.append(
            f"- {s['name']}({param_str}) [{s['kind']}]: {s['description']}"
        )
    tools_block = "\n".join(tool_lines)

    context_block = ""
    if current_path:
        context_block = f"\n[CURRENT PAGE] User is currently on: {current_path}\n"

    return (
        "You are the VIP Agent Assistant — the boss's AI co-pilot for the "
        "VIP AI Platform. You can navigate the dashboard, fetch live data "
        "from external agents (Asset/Stock/Realty), search the boss's data "
        "(twins, customer conversations, reports, knowledge), and perform "
        "actions on the boss's behalf (with their permission).\n\n"
        "Reply in the SAME language the user wrote in (Korean ↔ English).\n"
        "Be concise, warm, and conversational — like a smart human assistant. "
        "1-3 sentences for chat; longer only when listing data.\n\n"
        "■ TOOL CATALOG (every capability you have):\n"
        f"{tools_block}\n\n"
        "■ INTERNAL PAGES (for navigate(path)):\n"
        f"{pages_summary_for_llm()}\n\n"
        "■ EXTERNAL AGENT APPS (for open_portal(agent)):\n"
        f"{agents_summary_for_llm()}\n"
        f"{context_block}\n"
        "■ HOW TO RESPOND\n"
        "Always respond with ONE of these JSON shapes — NOTHING ELSE:\n"
        '  A. Call a tool: { "tool": "<name>", "args": { ... } }\n'
        '  B. Answer directly (no tool needed): { "answer": "<your reply>" }\n\n'
        "Rules:\n"
        "- For navigation queries (open X / show me X / go to X / 열어 / 보여줘): "
        "use navigate(path) for internal pages OR open_portal(agent) for "
        "external agent apps. NEVER navigate to a path not in the pages list above.\n"
        "- For 'I wanna see Asset/Stock/Realty Agent' (or their casual / Korean "
        "variants), pick open_portal — those are EXTERNAL apps.\n"
        "- For data questions ('how is X', 'what did Y do', 'find Z'): pick the "
        "matching read tool — search_twin, search_conversations, latest_report, "
        "agent_status, etc.\n"
        "- For 'what can you do' / 'help': call what_can_you_do.\n"
        "- For greetings ('hi', '안녕', 'hello'): use the answer shape with a friendly hello.\n"
        "- If unsure which tool, use the answer shape with a clarifying question.\n"
        "- Never invent a tool name not in the catalog above.\n"
        "- Never invent a page path not in the pages list above.\n"
    )


# ============================================================================
#  LLM call with JSON parsing
# ============================================================================

def _call_llm_for_decision(system: str, user_msg: str, history: list[dict]) -> dict:
    """Ask the LLM to pick a tool or give a direct answer. Returns dict."""
    messages = [{"role": h["role"], "content": h["content"][:400]} for h in (history or [])]
    messages.append({"role": "user", "content": user_msg})
    try:
        raw = chat_completion_sync(
            system_prompt=system,
            messages=messages,
            max_tokens=400,
            temperature=0.2,
            model="groq-llama-3.3-70b",
        )
    except Exception as e:
        log.warning(f"assistant_agent: LLM call failed: {e}")
        return {"answer": "Sorry, the assistant LLM is unavailable right now."}

    raw = (raw or "").strip()
    if not raw or raw.startswith("[LLM unavailable"):
        return {"answer": "Sorry, the LLM is unavailable right now."}

    # Extract JSON object from raw
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        # Treat as freeform answer
        return {"answer": raw[:500]}
    return parsed


def _extract_json(text: str) -> Any:
    """Pull the first balanced JSON object out of text, tolerant to surrounding prose."""
    try:
        return json.loads(text)
    except Exception:
        pass
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    start = -1
    return None


def _compose_final_answer(
    system: str,
    user_msg: str,
    tool_name: str,
    tool_result: dict,
    history: list[dict],
) -> str:
    """Second LLM turn: tool data → natural-language answer."""
    follow_system = (
        "You just called the tool '" + tool_name + "' and got back this result.\n"
        "Summarize it for the boss in 1-3 sentences (same language as their question).\n"
        "Be conversational. Use specific numbers/names from the tool result. "
        "If the result has ok=false or an error, apologize and explain briefly.\n"
        "Do NOT return JSON — just plain prose for the user."
    )
    summary_messages = [
        {"role": "user", "content": f"My question: {user_msg}"},
        {"role": "user", "content": f"Tool '{tool_name}' returned:\n{json.dumps(tool_result, ensure_ascii=False)[:1500]}"},
    ]
    try:
        reply = chat_completion_sync(
            system_prompt=follow_system,
            messages=summary_messages,
            max_tokens=300,
            temperature=0.5,
            model="groq-llama-3.3-70b",
        )
        return (reply or "").strip() or "(no reply)"
    except Exception as e:
        log.warning(f"assistant_agent: compose failed: {e}")
        # Fall back to the message field if the tool had one
        return tool_result.get("message") or tool_result.get("summary") or "Done."


# ============================================================================
#  Public entry point
# ============================================================================

def run_agent(
    db: Session,
    transcript: str,
    language: str = "auto",
    current_path: Optional[str] = None,
    history: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """Run one agent turn. Returns the same shape /chat/voice produces:
        {intent, language, reply, action, speak, transcript, tool_used, tool_result}
    """
    transcript = (transcript or "").strip()
    if not transcript:
        return {
            "intent": "empty",
            "language": "en",
            "reply": "I didn't hear anything.",
            "action": None,
            "speak": True,
            "transcript": transcript,
        }

    # Detect language for the output frame (LLM matches it itself)
    if language in ("ko", "en"):
        lang = language
    else:
        # Count Hangul
        hangul = sum(1 for c in transcript if 0xAC00 <= ord(c) <= 0xD7A3)
        lang = "ko" if hangul > 0 else "en"

    system = _build_system_prompt(current_path=current_path)

    # ===== Turn 1: decision =====
    decision = _call_llm_for_decision(system, transcript, history or [])

    # If the LLM chose to answer directly, return it
    if decision.get("answer") and not decision.get("tool"):
        return {
            "intent": "llm_chat",
            "language": lang,
            "reply": str(decision["answer"])[:1000],
            "action": None,
            "speak": True,
            "transcript": transcript,
            "tool_used": None,
        }

    # If the LLM chose a tool
    tool_name = (decision.get("tool") or "").strip()
    args = decision.get("args") or {}

    if not tool_name or tool_name not in TOOL_REGISTRY:
        # Hallucinated tool — degrade to answer
        log.warning(f"assistant_agent: LLM picked unknown tool '{tool_name}'")
        return {
            "intent": "llm_chat",
            "language": lang,
            "reply": (decision.get("answer") or "I'm not sure how to help with that — could you rephrase?")[:500],
            "action": None,
            "speak": True,
            "transcript": transcript,
            "tool_used": None,
        }

    tool = TOOL_REGISTRY[tool_name]

    # For Phase 1, all tools are READ tools. WRITE tools (Phase 3) will return
    # a proposed_action instead of executing — the widget shows a confirm card.
    tool_result = execute_tool(tool_name, args, db=db)

    # If the tool itself returned an action (navigate, open_portal, etc.),
    # surface it to the frontend so the widget can execute it.
    action = tool_result.get("action") if isinstance(tool_result, dict) else None

    # Compose the natural-language reply from tool data
    if action and tool_result.get("message"):
        # Navigation tools have a clean canned message — skip a 2nd LLM call
        reply = tool_result["message"]
    else:
        reply = _compose_final_answer(system, transcript, tool_name, tool_result, history or [])

    return {
        "intent": tool_name,
        "language": lang,
        "reply": reply[:1500] if reply else "",
        "action": action,
        "speak": True,
        "transcript": transcript,
        "tool_used": tool_name,
        "tool_result": tool_result if tool.kind == "read" else None,
    }
