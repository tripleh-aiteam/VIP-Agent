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
import os
from typing import Any, Optional

from sqlalchemy.orm import Session

from services.logger import log
from services.llm_client import chat_completion_sync
from services.assistant_tools import (
    TOOL_REGISTRY, list_tool_schemas, execute_tool,
)
from services.assistant_manifest import (
    pages_summary_for_llm, agents_summary_for_llm, get_agent_identity,
)


# ============================================================================
#  System prompt builder
# ============================================================================

def _build_system_prompt(
    current_path: Optional[str] = None,
    selected_id: Optional[str] = None,
    pending_attachments: Optional[list[dict]] = None,
    kb_context: Optional[list[dict]] = None,
    kb_files: Optional[list[dict]] = None,
) -> str:
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

    context_lines = []
    if current_path:
        context_lines.append(f"[CURRENT PAGE] User is on: {current_path}")
    if selected_id:
        # Hint the LLM what 'this' refers to based on current page
        hint = ""
        if current_path and current_path.startswith("/chatbot"):
            hint = f' (treat as conversation_id when the user says "this conversation" / "this message")'
        elif current_path and current_path.startswith("/reports"):
            hint = f' (treat as report_id when the user says "this report")'
        elif current_path and current_path.startswith("/twins"):
            hint = f' (treat as twin_id when the user says "this twin")'
        elif current_path and current_path.startswith("/meetings"):
            hint = f' (treat as meeting_id when the user says "this meeting")'
        context_lines.append(f"[SELECTED ID] {selected_id}{hint}")
    context_block = "\n" + "\n".join(context_lines) + "\n" if context_lines else ""

    # Pending-attachments block — tells the LLM which attachment_ids it can
    # pass to send_dm/send_email/broadcast/etc. When this is non-empty the
    # user has dropped files into the chat AND used an action verb, so they
    # almost certainly want one of those write tools.
    attach_block = ""
    if pending_attachments:
        lines = ["[ATTACHED FILES] The user just attached these — pass the matching attachment_ids to send_dm / send_email / broadcast etc. if they ask to send/share/forward:"]
        for a in pending_attachments[:8]:
            lines.append(f"  - attachment_id={a.get('attachment_id')} filename={a.get('filename')} kind={a.get('kind')} mime={a.get('mime_type')}")
        attach_block = "\n" + "\n".join(lines) + "\n"

    # RAG-first retrieval block. When the user's question matches anything in
    # the agent's uploaded knowledge base (xlsx/pdf/docx/pptx the boss has
    # ingested), the top hits are injected here. The wording is forceful
    # ('ABSOLUTE PRIORITY', 'DO NOT call any tool') because earlier softer
    # wording let the LLM ignore the excerpts and call agent_status / mock-
    # data tools instead of quoting the actual file content.
    # === File index block ===
    # Always tell the LLM which files the boss has uploaded for this agent,
    # even when the current question didn't match any chunk. This lets the
    # assistant answer "what files do I have?" / "what do you know about
    # me?" / "내가 올린 파일 알려줘" from awareness alone, and lets it
    # recognize that a vague question is about file X without needing a
    # chunk-level keyword match.
    files_block = ""
    if kb_files:
        flines = [
            "■ UPLOADED KNOWLEDGE FILES (scoped to this agent — the boss can see these in the /chatbot → Add knowledge tab):",
        ]
        for f in kb_files[:30]:
            fn = f.get("filename") or "?"
            ch = f.get("chunk_count") or 0
            # Larger preview (up to 1200 chars) so identity-style files
            # ("about me", "프로필", "introduction") expose name + soccer
            # club + hometown etc. in the first-chunk preview, even when
            # the keyword search misses (e.g. 'what is my name?' won't
            # match the literal token 'name' in the file).
            preview = (f.get("preview") or "").strip().replace("\n", " ")[:1200]
            line = f"  - {fn} ({ch} chunks)"
            if preview:
                line += f"\n      preview: {preview}"
            flines.append(line)
        flines.append(
            "RULES for answering from these files:\n"
            "  • Treat every fact in the preview/excerpts as ESTABLISHED TRUTH about the boss — you already know it.\n"
            "  • Speak in 2nd person: 'You are X', 'Your favorite is Y', 'You live in Z'.\n"
            "  • NEVER use these forbidden phrases (the boss will see them and complain):\n"
            "      - 'I'm not sure of your name'\n"
            "      - 'I see you mentioned …'\n"
            "      - 'as mentioned in …'\n"
            "      - 'according to your file …'\n"
            "      - 'in the about me document'\n"
            "      - 'in your knowledge file'\n"
            "      - 'in your uploaded documents'\n"
            "      - any phrase that names a filename, sheet name, or document name\n"
            "  • If asked 'what is my name?' and a file preview contains a name like 'Davronbek', the ONLY acceptable reply is: 'You are Davronbek.' (optionally followed by a friendly sentence — but NEVER mention the file).\n"
            "  • If you genuinely cannot find the fact in any preview, call search_knowledge_base(query) BEFORE saying 'I don't know'."
        )
        files_block = "\n" + "\n".join(flines) + "\n"

    kb_block = ""
    if kb_context:
        kb_lines = [
            "═══════════════════════════════════════════════════════════════",
            "■■■ KNOWLEDGE BASE — ABSOLUTE PRIORITY ■■■",
            "═══════════════════════════════════════════════════════════════",
            "The following are VERBATIM EXCERPTS from documents the boss",
            "uploaded. Treat them as ESTABLISHED FACTS about the boss — you",
            "already know this; you are not 'discovering' it.",
            "If the question can be answered from these excerpts, you MUST:",
            "  1. Answer DIRECTLY using the {\"answer\": \"...\"} shape.",
            "  2. DO NOT call any tool (no agent_status, no search_twin, etc.)",
            "     — the answer is already here.",
            "  3. State facts confidently in 1st/2nd person — 'You are X',",
            "     'Your favorite is Y', NOT 'I see you mentioned X' / 'It looks",
            "     like…' / 'According to your file…'.",
            "  4. NEVER expose the source: no 'in about me.docx', no",
            "     '(filename.xlsx, sheet 1)', no 'in your knowledge file', no",
            "     'in your uploaded documents'. The boss already knows what",
            "     they uploaded; don't echo the filenames back.",
            "  5. Quote specific numbers, names, and amounts verbatim — just",
            "     without naming the file they came from.",
            "Only call a tool if the question is about CURRENT system state",
            "(live twins, today's tasks, conversation status etc.) and NOT",
            "answerable from the excerpts below.",
            "─── excerpts (internal — do NOT mention filenames in your reply) ───",
        ]
        for i, c in enumerate(kb_context[:8], start=1):
            sim = c.get("similarity", 0.0)
            # Deliberately DO NOT include filename or sheet name in the
            # excerpt header — the LLM tended to echo them back into the
            # reply ("as mentioned in about me.docx"). The location alone
            # is enough internal context.
            loc = c.get("location") or f"excerpt {i}"
            kb_lines.append(
                f"[{i}] {loc}  (relevance {sim:.2f})\n"
                f"{c.get('content', '').strip()[:1800]}"
            )
        kb_lines.append("═══════════════════════════════════════════════════════════════")
        kb_block = "\n" + "\n".join(kb_lines) + "\n"

    identity = get_agent_identity()
    return (
        f"You are the {identity['name']} — {identity['tagline']}. "
        f"{identity['scope']}\n\n"
        "Reply in the SAME language the user wrote in (Korean ↔ English).\n"
        "Be concise, warm, and conversational — like a smart human assistant. "
        "1-3 sentences for chat; longer only when listing data.\n\n"
        "■ TOOL CATALOG (every capability you have):\n"
        f"{tools_block}\n\n"
        "■ INTERNAL PAGES (for navigate(path)):\n"
        f"{pages_summary_for_llm()}\n\n"
        "■ EXTERNAL AGENT APPS (for open_portal(agent)):\n"
        f"{agents_summary_for_llm()}\n"
        f"{context_block}{attach_block}{files_block}{kb_block}\n"
        "■ HOW TO RESPOND\n"
        "Always respond with ONE of these JSON shapes — NOTHING ELSE:\n"
        '  A. Call ONE tool:    { "tool": "<name>", "args": { ... } }\n'
        '  B. Chain N tools:    { "steps": [ { "tool": "<name>", "args": {...} }, ... ] }\n'
        '                       The backend runs each step in order, feeds the\n'
        '                       result of step N into step N+1 (you can reference\n'
        '                       step results when the user asks compound questions).\n'
        '                       Use chains for "find X and then do Y" requests.\n'
        '  C. Answer directly:  { "answer": "<your reply>" }\n\n'
        "Rules:\n"
        "- IF the KNOWLEDGE BASE section above has the answer (any excerpt "
        "  contains the entity / number / topic the user asked about): use "
        "  the answer shape with verbatim numbers from the excerpt. DO NOT "
        "  call a tool. Speak confidently in 1st/2nd person ('You are X', "
        "  'Your favorite is Y'). NEVER mention the file name, sheet name, "
        "  or that you got the fact from an upload — just state it.\n"
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
        "- Never invent a page path not in the pages list above.\n\n"
        "■ COMPANION MODE — when the user is just chatting (no task verb, "
        "no entity to fetch, sharing feelings, telling a story, saying "
        "they're tired, lonely, curious about your day, etc.):\n"
        "  • Skip every tool. Use the {\"answer\": \"...\"} shape only.\n"
        "  • Respond like a warm friend, not a customer-service bot. Show "
        "    that you actually heard what they said — reference a specific "
        "    word or feeling from their message.\n"
        "  • Ask ONE genuine open-ended follow-up question per turn so the "
        "    conversation keeps flowing. Vary it — about their day, what "
        "    they're thinking, how something turned out, what they enjoy.\n"
        "  • Volunteer your own observations sometimes — share a thought, "
        "    a curiosity, a gentle suggestion. Not just questions.\n"
        "  • Remember what they tell you (names, feelings, plans, family) "
        "    and bring it back naturally in later turns. The KNOWLEDGE "
        "    BASE excerpts above may also contain personal details the "
        "    boss has uploaded — quote them gently when relevant.\n"
        "  • Keep replies short for voice (1-2 sentences) — long monologues "
        "    feel robotic when spoken. Save longer answers for explicit "
        "    questions.\n"
        "  • If the user message begins with '[silence]' it's a system "
        "    nudge that the user has gone quiet — gently restart the "
        "    conversation with a fresh open-ended question, don't "
        "    acknowledge the bracket text.\n"
        "  • In Korean, match their register (반말 ↔ 존댓말) — listen to "
        "    their last sentence and mirror.\n"
    )


# ============================================================================
#  LLM call with JSON parsing
# ============================================================================

def _pick_model_for_query(user_msg: str, history: list[dict]) -> str:
    """Smart router — picks an LLM tier based on query complexity:

      • Easy + Normal → groq-llama-3.3-70b  (free, fast, no quota worry)
      • Hard          → claude-sonnet-4-6   (paid Anthropic; cascades to
                                             Groq automatically when the
                                             paid key has no credit)

    'Hard' is detected by the same signals as before (long prompts,
    compound requests, reasoning verbs, deep conversation history).

    Override via env var `ASSISTANT_FORCE_MODEL`.
    """
    forced_env = os.getenv("ASSISTANT_FORCE_MODEL", "").strip()
    if forced_env:
        return forced_env

    q = (user_msg or "").strip()
    qlc = q.lower()

    # Signal 1 — query length
    long_query = len(q) > 200

    # Signal 2 — compound / chained request
    compound_markers = (
        " and then ", " after that ", " also ", " then ", " plus ", "; ",
        " 그리고 ", " 그다음 ", " 그런 다음 ",
    )
    is_compound = any(m in qlc or m in q for m in compound_markers)

    # Signal 3 — reasoning / synthesis verbs (these benefit from Pro)
    reasoning_markers = (
        "summarize", "summary", "explain", "why", "compare", "analyze",
        "recommend", "suggest", "draft", "write a", "rewrite", "translate",
        "요약", "왜", "비교", "분석", "추천", "초안", "다시 써", "번역",
    )
    is_reasoning = any(m in qlc for m in reasoning_markers)

    # Signal 4 — long conversation history (context-heavy follow-up)
    deep_history = len(history or []) > 6

    if long_query or is_compound or is_reasoning or deep_history:
        # Hard → paid model. llm_client cascade falls back to Groq when
        # the Anthropic key has no credit, so this never bricks.
        return "claude-sonnet-4-6"
    # Easy / Normal → free Groq Llama 3.3 70B (fast, no quota worry,
    # excellent for tool-routing + RAG answers from the KB).
    return "groq-llama-3.3-70b"


def _call_llm_for_decision(
    system: str,
    user_msg: str,
    history: list[dict],
    forced_model: Optional[str] = None,
) -> dict:
    """Ask the LLM to pick a tool or give a direct answer. Returns dict.

    Uses _pick_model_for_query to route between fast (Groq) and smart
    (Gemini Pro) tiers. If the chosen provider returns an error / the
    "[LLM unavailable]" sentinel, we re-try with the other tier so a
    single missing API key never bricks the assistant.

    `forced_model` (optional) bypasses the smart router — used by the
    in-overlay model picker dropdown to pin a specific LLM per request.
    """
    messages = [{"role": h["role"], "content": (h.get("content") or "")[:400]}
                for h in (history or []) if h.get("content")]
    messages.append({"role": "user", "content": user_msg})

    # Honor an explicit per-request model override (from the overlay's
    # model dropdown). Otherwise let the smart router pick.
    primary = ((forced_model or "").strip()
               or _pick_model_for_query(user_msg, history or []))
    # Cascade order: if Claude is rate-limited, try its other tier; if both
    # Claude tiers fail (outage, key issue), drop to Gemini Flash, then
    # OpenAI as the cross-provider safety net. Cheapest survivor wins.
    if primary == "claude-haiku-4-5":
        fallback = "claude-sonnet-4-6"
    elif primary == "claude-sonnet-4-6":
        fallback = "claude-haiku-4-5"
    elif primary.startswith("gemini"):
        fallback = "claude-haiku-4-5"
    else:
        fallback = "gpt-4o-mini"

    def _try(model: str) -> tuple[str, Optional[str]]:
        """Returns (usable_text, error_reason). usable_text is empty when
        the LLM call failed; the error_reason contains either the exception
        message OR the LLM's own '[LLM unavailable] …' sentinel so the
        caller can surface the real problem (404 model id, quota, etc.)."""
        try:
            out = chat_completion_sync(
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=400,
                temperature=0.2,
                model=model,
            )
            text = (out or "").strip()
            # llm_client returns "[LLM unavailable] <reason>" on provider
            # failure — propagate that reason instead of pretending success.
            if not text or text.startswith("[LLM unavailable") or text.startswith("["):
                return "", (text or "empty response from provider")
            return text, None
        except Exception as e:
            return "", str(e)

    # NB: _try used to reference `system` (out of scope here). Pin to
    # `system_prompt` since this nested helper closes over the caller's
    # `system` variable name. Both refer to the same string built above.
    system_prompt = system
    raw, err_primary = _try(primary)
    err_fallback = None
    if not raw:
        log.info(f"assistant_agent: primary {primary} failed ({err_primary}); cascading to {fallback}")
        raw, err_fallback = _try(fallback)

    if not raw or raw.startswith("[LLM unavailable"):
        # Surface BOTH errors so the boss can see what's actually broken.
        # The previous opaque "Sorry, unavailable" hid quota / key / model
        # issues for hours of head-scratching.
        log.warning(f"assistant_agent: both LLM tiers failed — "
                    f"primary {primary}: {err_primary} | "
                    f"fallback {fallback}: {err_fallback}")
        return {
            "answer": (
                f"Sorry — LLM unavailable. Primary ({primary}): "
                f"{err_primary or 'no output'}. Fallback ({fallback}): "
                f"{err_fallback or 'no output'}."
            )
        }

    # Stash which model decided this turn so the response can surface it
    # (useful for telemetry — the overlay can show 'groq' / 'gemini' chip).
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return {"answer": raw[:500], "_model": primary}
    parsed["_model"] = primary
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


def _run_chain(
    db: Session,
    transcript: str,
    lang: str,
    steps: list[dict],
    current_path: Optional[str],
    selected_id: Optional[str],
    system_prompt: str,
    history: list[dict],
) -> dict[str, Any]:
    """Execute a multi-step chain. If any step is a WRITE tool, halt and
    return a proposed_chain so the widget can ask for confirmation up front
    (single confirm covers the whole chain)."""
    # Validate every step's tool exists; if any write tool appears, request confirm
    validated_steps = []
    any_write = False
    for s in steps[:6]:  # cap chain length
        tname = (s.get("tool") or "").strip()
        if tname not in TOOL_REGISTRY:
            log.warning(f"chain: skip unknown tool '{tname}'")
            continue
        targs = s.get("args") or {}
        if selected_id:
            for k in ("conversation_id", "report_id", "twin_id", "meeting_id",
                      "handoff_id", "task_id", "knowledge_id"):
                if k in (TOOL_REGISTRY[tname].parameters.get("properties") or {}) and not targs.get(k):
                    targs[k] = selected_id
                    break
        validated_steps.append({"tool": tname, "args": targs})
        if TOOL_REGISTRY[tname].requires_confirmation:
            any_write = True

    if not validated_steps:
        return {
            "intent": "chain_empty", "language": lang, "reply": "I'm not sure how to do that.",
            "action": None, "speak": True, "transcript": transcript,
        }

    if any_write:
        # Compose a multi-line preview
        preview_lines = []
        for i, s in enumerate(validated_steps, 1):
            p = _compose_write_preview(s["tool"], s["args"])
            preview_lines.append(f"{i}. {p['message']}")
        return {
            "intent": "chain_proposed", "language": lang,
            "reply": "I'd like to run these steps — confirm?\n" + "\n".join(preview_lines),
            "action": None, "speak": True, "transcript": transcript,
            "tool_used": None,
            "proposed_chain": validated_steps,
        }

    # Read-only chain — execute all and compose a final answer
    step_results = []
    for s in validated_steps:
        res = execute_tool(s["tool"], s["args"], db=db)
        step_results.append({"tool": s["tool"], "result": res})

    # Compose final answer from all step results
    follow_system = (
        "You just ran the following tools sequentially. Summarize what you "
        "found for the boss in 2-4 sentences (same language as their question). "
        "Use specific names and numbers from the results. Be conversational."
    )
    import json as _json
    summary_input = _json.dumps(step_results, ensure_ascii=False)[:3000]
    try:
        reply = chat_completion_sync(
            system_prompt=follow_system,
            messages=[
                {"role": "user", "content": f"Question: {transcript}"},
                {"role": "user", "content": f"Tool chain results:\n{summary_input}"},
            ],
            max_tokens=400, temperature=0.5,
            model="groq-llama-3.3-70b",
        )
    except Exception:
        reply = "Done — checked the data."

    # If any step returned an action (navigate / open_portal), surface the LAST one
    action = None
    for s in reversed(step_results):
        a = (s.get("result") or {}).get("action")
        if a:
            action = a
            break

    # For chains, derive suggestions from the LAST tool that ran (most
    # recent intent is the one the user is likely to follow up on)
    last_tool = step_results[-1]["tool"] if step_results else None
    last_result = step_results[-1].get("result") if step_results else None
    return {
        "intent": "chain_completed",
        "language": lang,
        "reply": (reply or "Done.")[:1500],
        "action": action,
        "speak": True,
        "transcript": transcript,
        "tool_used": "[chain]",
        "tool_result": {"steps": step_results, "step_count": len(step_results)},
        "suggestions": _suggest_followups(last_tool, last_result, lang),
    }


def _build_card(tool_name: str, result: dict) -> Optional[dict]:
    """Convert a read-tool's result into a structured display card the
    widget can render (Notion-AI style). Returns None when the tool
    result isn't card-worthy (just text / action)."""
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    if tool_name == "search_twin" and result.get("matches"):
        return {
            "type": "twin_list",
            "title": f"Found {len(result['matches'])} twin(s)",
            "items": result["matches"],
        }
    if tool_name == "twin_activity" and result.get("activities"):
        return {
            "type": "activity_list",
            "title": f"{result.get('twin_name', '?')} — last {result.get('hours_window', '?')}h activity",
            "items": result["activities"],
        }
    if tool_name == "twin_tasks" and result.get("tasks"):
        return {
            "type": "task_list",
            "title": f"{result.get('twin_name', '?')} — tasks",
            "items": result["tasks"],
        }
    if tool_name == "search_conversations" and result.get("matches"):
        return {
            "type": "conversation_list",
            "title": f"Found {result['count']} conversation(s)",
            "items": result["matches"],
        }
    if tool_name == "conversation_history" and result.get("messages"):
        return {
            "type": "message_thread",
            "title": f"Conversation {result.get('conversation_id', '')[:8]}",
            "items": result["messages"],
        }
    if tool_name == "latest_report":
        return {
            "type": "report_excerpt",
            "title": result.get("title") or f"{result.get('type', '?').title()} Report",
            "summary": result.get("summary"),
            "report_id": result.get("report_id"),
        }
    if tool_name == "search_reports" and result.get("matches"):
        return {
            "type": "report_list",
            "title": f"Found {result['count']} report(s)",
            "items": result["matches"],
        }
    if tool_name == "agent_status":
        return {
            "type": "agent_status_card",
            "title": f"{result.get('agent', '?')} ({result.get('type', '?')})",
            "summary": result.get("summary"),
            "data": result.get("data"),
        }
    if tool_name == "list_pending_approvals" and result.get("cases"):
        return {
            "type": "approval_list",
            "title": f"{result['count']} pending approval(s)",
            "items": result["cases"],
        }
    if tool_name == "search_knowledge" and result.get("matches"):
        return {
            "type": "knowledge_list",
            "title": f"Found {result['count']} knowledge entrie(s)",
            "items": result["matches"],
        }
    if tool_name == "count":
        return {
            "type": "stat_card",
            "label": result.get("entity"),
            "value": result.get("count"),
        }
    if tool_name == "latest_meeting_notes" and result.get("notes"):
        return {
            "type": "meeting_notes_list",
            "title": f"{result['count']} latest meeting note(s)",
            "items": result["notes"],
        }
    if tool_name == "list_pages" and result.get("pages"):
        return {
            "type": "page_list",
            "title": f"{result['count']} pages available",
            "items": result["pages"],
        }
    if tool_name == "semantic_search" and result.get("matches"):
        return {
            "type": "cross_search",
            "title": f"Found {result['count']} matches for '{result.get('query', '')}'",
            "by_source": result.get("by_source"),
            "items": result["matches"],
        }
    return None


def _compose_write_preview(tool_name: str, args: dict) -> dict[str, Any]:
    """Human-readable preview of a write action before user confirms.
    Returns {"message": str, "details": dict (optional)}."""
    if tool_name == "send_dm":
        return {
            "message": f"📩 Send DM to {args.get('twin_name', '?')}: \"{(args.get('body') or '')[:120]}\"",
            "details": {"target": args.get("twin_name"), "body": args.get("body")},
        }
    if tool_name == "send_email":
        return {
            "message": f"✉️ Send email to {args.get('to', '?')}: \"{(args.get('subject') or '')[:60]}\"",
            "details": {"to": args.get("to"), "subject": args.get("subject"),
                        "body": (args.get("body") or "")[:300]},
        }
    if tool_name == "broadcast":
        return {
            "message": f"📢 Broadcast to ALL workers: \"{(args.get('body') or '')[:120]}\"",
            "details": {"body": args.get("body")},
        }
    if tool_name == "kakao_reply":
        return {
            "message": f"💬 Reply on Kakao conversation {args.get('conversation_id', '?')[:8]}: \"{(args.get('text') or '')[:120]}\"",
            "details": {"conversation_id": args.get("conversation_id"), "text": args.get("text")},
        }
    if tool_name == "trigger_daily_report":
        return {"message": "📊 Generate today's daily report now?"}
    if tool_name == "trigger_weekly_report":
        return {"message": "📈 Generate this week's report now?"}
    if tool_name == "approve_handoff":
        return {"message": f"✅ Approve handoff {args.get('handoff_id', '?')[:12]}?"}
    if tool_name == "approve_all_pending":
        return {"message": "✅ Approve ALL pending overnight handoffs?"}
    if tool_name == "reject_handoff":
        return {"message": f"❌ Reject handoff {args.get('handoff_id', '?')[:12]}? Reason: {args.get('reason', '(none)')}"}
    if tool_name == "resolve_conversation":
        return {"message": f"✓ Mark Kakao conversation {args.get('conversation_id', '?')[:8]} as resolved?"}
    if tool_name == "take_over_conversation":
        return {"message": f"👤 Take over Kakao conversation {args.get('conversation_id', '?')[:8]} (you will reply manually)?"}
    if tool_name == "escalate_conversation":
        return {"message": f"⚠️ Escalate Kakao conversation {args.get('conversation_id', '?')[:8]} as urgent?"}
    if tool_name == "create_task":
        return {"message": f"➕ Create task '{args.get('title', '')[:60]}' assigned to {args.get('twin_name', '?')}?"}
    if tool_name == "cancel_task":
        return {"message": f"❌ Cancel task {args.get('task_id', '?')[:12]}?"}
    if tool_name == "schedule_meeting":
        return {"message": f"📅 Schedule meeting with {args.get('participants', '?')} at {args.get('when', '?')}: {args.get('agenda', '')[:60]}"}
    if tool_name == "cancel_meeting":
        return {"message": f"❌ Cancel meeting {args.get('meeting_id', '?')[:12]}?"}
    if tool_name == "add_knowledge":
        return {"message": f"📝 Add knowledge to {args.get('twin_name', '?')}: '{args.get('title', '')[:60]}'?"}
    if tool_name == "delete_knowledge":
        return {"message": f"🗑️ Delete knowledge entry {args.get('knowledge_id', '?')[:12]}?"}
    if tool_name == "set_boss_mode":
        return {"message": f"🔧 Set Boss mode to '{args.get('mode')}' for {args.get('hours', 24)} hours?"}
    if tool_name == "set_twin_mode":
        return {"message": f"🔧 Set {args.get('twin_name', '?')}'s mode to '{args.get('mode')}'?"}
    # ── New tools from the 56-tool expansion ──
    if tool_name == "create_twin":
        return {"message": f"➕ Create new twin '{args.get('name', '?')}' owned by {args.get('owner_email', '(default)')}?"}
    if tool_name == "delete_twin":
        return {"message": f"🗑️ DELETE twin '{args.get('twin_name', '?')}' and ALL its data? This cannot be undone."}
    if tool_name == "update_twin_owner":
        return {"message": f"✏️ Change {args.get('twin_name', '?')}'s owner to {args.get('owner_email', '?')}?"}
    if tool_name == "update_task_status":
        return {"message": f"✓ Move task {args.get('task_id', '?')[:12]} → status '{args.get('status', '?')}'?"}
    if tool_name == "update_task_priority":
        return {"message": f"⚑ Set task {args.get('task_id', '?')[:12]} priority → '{args.get('priority', '?')}'?"}
    if tool_name == "reassign_task":
        return {"message": f"↪️ Reassign task {args.get('task_id', '?')[:12]} → {args.get('twin_name', '?')}?"}
    if tool_name == "update_knowledge":
        return {"message": f"✏️ Edit knowledge entry {args.get('knowledge_id', '?')[:12]}?"}
    if tool_name == "trigger_cross_agent_report":
        return {"message": "📊 Generate a cross-agent summary (Asset + Stock) report now?"}
    if tool_name == "delete_report":
        return {"message": f"🗑️ DELETE report {args.get('report_id', '?')[:12]}? This cannot be undone."}
    if tool_name == "trigger_workflow":
        return {"message": f"▶️ Manually run workflow {args.get('workflow_id', '?')[:12]} now?"}
    if tool_name == "set_workflow_enabled":
        verb = "enable" if args.get("enabled") else "disable"
        return {"message": f"⚙️ {verb.capitalize()} workflow {args.get('workflow_id', '?')[:12]}?"}
    if tool_name == "unsend_dm":
        return {"message": f"↩️ Unsend DM {args.get('message_id', '?')[:12]}? This deletes the message from your records."}
    if tool_name == "unsend_last_dm":
        return {"message": f"↩️ Unsend your last DM to {args.get('twin_name', '?')}?"}
    # Generic fallback
    return {"message": f"Run {tool_name}({args})?"}


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

def _extract_attachment_text(filename: str, mime_type: str, blob: bytes) -> Optional[str]:
    """Extract readable text from an attached file. Returns None if the
    file type is binary-only (image / audio / unknown) — caller decides
    what to do (vision fallback, transcription, etc.).

    Supported:
      .xlsx / .xls         → openpyxl (via knowledge_ingest._parse_xlsx)
      .docx                → python-docx
      .pptx                → python-pptx
      .pdf                 → pypdf
      .csv                 → built-in csv module
      .txt / .md / .json   → utf-8 decode
      .hwp                 → olefile + PrvText stream (Korean Hangul docs)
    """
    name = filename.lower()
    try:
        if name.endswith((".xlsx", ".xls", ".xlsm")):
            from services.knowledge_ingest import _parse_xlsx
            chunks = _parse_xlsx(filename, blob)
            return "\n\n".join(c.get("content", "") for c in chunks)[:60000]
        if name.endswith(".docx"):
            from services.knowledge_ingest import _parse_docx
            chunks = _parse_docx(filename, blob)
            return "\n\n".join(c.get("content", "") for c in chunks)[:60000]
        if name.endswith(".pptx"):
            from services.knowledge_ingest import _parse_pptx
            chunks = _parse_pptx(filename, blob)
            return "\n\n".join(c.get("content", "") for c in chunks)[:60000]
        if name.endswith(".pdf"):
            from services.knowledge_ingest import _parse_pdf
            chunks = _parse_pdf(filename, blob)
            return "\n\n".join(c.get("content", "") for c in chunks)[:60000]
        if name.endswith(".csv"):
            from services.knowledge_ingest import _parse_csv
            chunks = _parse_csv(filename, blob)
            return "\n\n".join(c.get("content", "") for c in chunks)[:60000]
        if name.endswith((".txt", ".md", ".json", ".log")):
            return blob.decode("utf-8", errors="replace")[:60000]
        if name.endswith(".hwp"):
            # Hangul Word Processor (Korean). HWP is a compound document
            # format; the 'PrvText' stream is a UTF-16-LE preview that's
            # readable without licensed parsers.
            try:
                import olefile, io
                ole = olefile.OleFileIO(io.BytesIO(blob))
                if ole.exists("PrvText"):
                    raw = ole.openstream("PrvText").read()
                    return raw.decode("utf-16-le", errors="replace")[:60000]
                # Fallback: BodyText sections (less reliable but worth trying)
                if ole.exists("BodyText"):
                    raw = b""
                    for s in ole.listdir():
                        if s and s[0] == "BodyText":
                            raw += ole.openstream(s).read()
                    if raw:
                        return raw.decode("utf-16-le", errors="replace")[:60000]
            except ImportError:
                return "[HWP parser not installed — install 'olefile' on the orchestrator]"
            except Exception as e:
                return f"[Could not extract HWP text: {e}]"
        # Office legacy (.doc / .xls / .ppt) — would need antiword / xlrd /
        # python-pptx old format. Skip for now; report instead of crashing.
        if name.endswith((".doc", ".xls", ".ppt")):
            return f"[Legacy Office format {name.rsplit('.', 1)[-1]} — please re-save as the modern docx/xlsx/pptx format.]"
    except Exception as e:
        log.warning(f"_extract_attachment_text({filename}) failed: {e}")
        return f"[Could not extract text from {filename}: {e}]"
    return None  # Binary / unknown — caller handles


def _run_multimodal_path(
    transcript: str,
    lang: str,
    history: list[dict],
    attachment_ids: list[str],
) -> dict[str, Any]:
    """Handle Q&A about uploaded files of ANY supported type.

    Strategy:
      1. Load each attachment by id.
      2. For each:
         - text-extractable (xlsx/docx/pptx/pdf/csv/txt/md/json/hwp) →
           extract with _extract_attachment_text and inject as context.
         - image (image/*) or PDF → ALSO send raw bytes to vision so the
           LLM can see layout / charts / scanned content.
         - audio (audio/*) → transcribe via Whisper (Groq) first, then
           treat the transcript as text context.
      3. Compose final answer using the text-or-vision path with the
         best available provider (cascade).
    """
    from routers.chatbot import load_attachment
    from services.llm_client import gemini_multimodal_sync, chat_completion_sync
    import httpx as _httpx

    attachments: list[dict] = []
    for aid in attachment_ids:
        a = load_attachment(aid)
        if a:
            attachments.append(a)

    if not attachments:
        return {
            "intent": "multimodal_missing",
            "language": lang,
            "reply": ("첨부 파일을 찾을 수 없습니다 — 다시 업로드해 주세요."
                      if lang == "ko" else
                      "I couldn't find the attached file — please re-upload."),
            "action": None, "speak": True, "transcript": transcript,
            "tool_used": None,
        }

    # 1) Build a text-context block out of every attachment we can parse
    text_blocks: list[str] = []
    image_or_pdf: list[dict] = []
    for a in attachments:
        fn = a.get("filename") or ""
        mime = a.get("mime_type") or ""
        blob = a.get("bytes") or b""
        # Audio → transcribe and use the transcript as text
        if mime.startswith("audio/"):
            try:
                groq_key = os.getenv("GROQ_API_KEY", "")
                if groq_key:
                    resp = _httpx.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {groq_key}"},
                        files={"file": (fn or "audio.webm", blob, mime)},
                        data={"model": "whisper-large-v3"},
                        timeout=90,
                    )
                    if resp.status_code == 200:
                        text = (resp.json().get("text") or "").strip()
                        if text:
                            text_blocks.append(f"[{fn} — transcribed audio]\n{text}")
                            continue
            except Exception as e:
                log.warning(f"audio transcribe ({fn}) failed: {e}")
                text_blocks.append(f"[{fn}: transcription failed]")
            continue
        # Image + PDF → keep for vision pass (in addition to text extract for PDF)
        if mime.startswith("image/"):
            image_or_pdf.append({"mime_type": mime, "bytes": blob, "filename": fn})
        # Try text extraction
        extracted = _extract_attachment_text(fn, mime, blob)
        if extracted and extracted.strip():
            text_blocks.append(f"[{fn} — extracted]\n{extracted}")
        elif mime == "application/pdf":
            # No text extracted from PDF (likely scanned) → vision fallback
            image_or_pdf.append({"mime_type": mime, "bytes": blob, "filename": fn})

    # 2) Compose the prompt
    sys = (
        "You are the VIP Assistant — the boss attached one or more files "
        "and is asking about them. Read the extracted text below carefully, "
        "quote specific numbers / names verbatim, and answer concretely "
        "(no 'I see a file…' filler). Reply in the SAME language the boss "
        "wrote in (Korean ↔ English). Keep it tight — 1-4 sentences unless "
        "they explicitly asked for detail."
    )
    user_text = transcript or (
        "이 파일에 대해 알려주세요." if lang == "ko" else "Tell me what's in this."
    )
    if history:
        recent = [h for h in history[-3:] if (h.get("role") == "user")]
        if recent:
            user_text = (recent[-1].get("content") or "").strip()[:400] + "\n\n" + user_text

    context_block = ""
    if text_blocks:
        context_block = "\n\n===== ATTACHED FILE CONTENT =====\n" + "\n\n---\n".join(text_blocks) + "\n===== END =====\n"

    # 3) Choose path: vision (image + maybe text) OR pure text
    if image_or_pdf:
        # Vision path: pass image bytes alongside the extracted text. Gemini
        # is preferred but may be denied; fall back to OpenAI vision in the
        # multimodal helper itself when configured to.
        full_user = (context_block + "\n\n" if context_block else "") + user_text
        reply = gemini_multimodal_sync(
            system_prompt=sys,
            user_text=full_user,
            attachments=image_or_pdf,
            model="gemini-2.5-pro",
            max_tokens=800,
            temperature=0.4,
        )
        if reply.startswith("[LLM unavailable]") and context_block:
            # Vision dead — degrade gracefully to text-only on a working LLM
            log.warning("vision unreachable, falling back to text-only on attached extracts")
            reply = chat_completion_sync(
                system_prompt=sys + "\n\n(Note: vision is unavailable; answer from the extracted text only.)",
                messages=[{"role": "user", "content": full_user}],
                max_tokens=800,
                temperature=0.4,
            )
    else:
        # Pure text path — works on any LLM (Anthropic/OpenAI/Gemini/Groq/Ollama).
        # No vision needed, so we go through the standard cascade.
        full_user = (context_block + "\n\n" if context_block else "") + user_text
        reply = chat_completion_sync(
            system_prompt=sys,
            messages=[{"role": "user", "content": full_user}],
            max_tokens=800,
            temperature=0.4,
        )

    if isinstance(reply, str) and reply.startswith("[LLM unavailable]"):
        reason = reply.replace("[LLM unavailable]", "").strip(" :-")
        log.warning(f"assistant_agent: multimodal failed: {reply}")
        return {
            "intent": "multimodal_failed",
            "language": lang,
            "reply": (f"죄송합니다, 모델에 연결할 수 없습니다 — {reason}"
                      if lang == "ko" else
                      f"Sorry — model unreachable: {reason}"),
            "action": None, "speak": True, "transcript": transcript,
            "tool_used": None,
            "error_reason": reason,
        }

    return {
        "intent": "multimodal_answer",
        "language": lang,
        "reply": reply[:1500],
        "action": None, "speak": True, "transcript": transcript,
        "tool_used": "vision" if image_or_pdf else "file_text",
        "tool_result": {
            "attachment_count": len(attachments),
            "text_blocks": len(text_blocks),
            "images_or_pdf": len(image_or_pdf),
            "kinds": [a.get("kind") for a in attachments],
        },
    }


def _persist_assistant_turn(
    db: Session,
    user_id: str,
    user_text: str,
    assistant_reply: str,
    intent: Optional[str] = None,
    tool_used: Optional[str] = None,
) -> None:
    """Write the user-question + assistant-reply pair to the assistant's
    cross-session memory (chat_sessions + chat_messages with channel
    'assistant_overlay'). Used so the `recall_history` tool can answer
    'what did we discuss yesterday'-style questions.

    Best-effort — failures are swallowed; persistence is not on the
    critical path of returning a reply. Each user gets ONE rolling
    overlay session (channel='assistant_overlay'); messages append to it.
    """
    if not db or not user_id:
        return
    try:
        from db.models import ChatSession, ChatMessage
        session = (db.query(ChatSession)
                   .filter(ChatSession.user_id == user_id,
                           ChatSession.channel == "assistant_overlay")
                   .order_by(ChatSession.created_at.desc())
                   .first())
        if not session:
            session = ChatSession(
                user_id=user_id, channel="assistant_overlay",
                mode="llm", title="Assistant overlay history",
            )
            db.add(session)
            db.flush()  # need session.id for the messages below

        if user_text:
            db.add(ChatMessage(
                session_id=session.id, role="user", message_type="plain_text",
                content_json={"text": user_text[:2000]},
            ))
        if assistant_reply:
            db.add(ChatMessage(
                session_id=session.id, role="assistant", message_type="plain_text",
                content_json={
                    "text": assistant_reply[:2000],
                    "intent": intent,
                    "tool_used": tool_used,
                },
            ))
        db.commit()
    except Exception as e:
        log.info(f"assistant_agent: persist_turn skipped ({e})")


def _suggest_followups(
    tool_used: Optional[str],
    tool_result: Optional[dict],
    lang: str,
) -> list[str]:
    """Generate 2-3 short follow-up questions the user might want to ask
    next, based on the tool that just fired. Templated (deterministic, free).

    Returns up to 3 user-facing strings. The overlay renders these as
    clickable chips under the assistant bubble — clicking sends the chip
    text as the next query. Empty list → no chips shown.
    """
    if not tool_used:
        return []
    en = lang != "ko"
    # Pull useful entities out of the tool result for personalised chips
    name = None
    if isinstance(tool_result, dict):
        name = (
            tool_result.get("twin_name")
            or tool_result.get("name")
            or (tool_result.get("matches") or [{}])[0].get("name")
            if tool_result.get("matches") else tool_result.get("twin_name")
        )

    def en_or_ko(en_text: str, ko_text: str) -> str:
        return en_text if en else ko_text

    # Tool-specific templates ---------------------------------------------
    if tool_used == "send_dm":
        return [
            en_or_ko(f"Show {name}'s recent activity", f"{name}의 최근 활동 보여줘") if name else en_or_ko("Show recent DMs", "최근 메시지 보여줘"),
            en_or_ko(f"What tasks does {name} have?", f"{name}의 작업은?") if name else en_or_ko("Unsend that message", "그 메시지 취소"),
            en_or_ko("Broadcast something to everyone", "전체에게 공지"),
        ]
    if tool_used == "search_twin" or tool_used == "list_twins":
        return [
            en_or_ko(f"Show {name}'s activity today" if name else "Show twin activity today",
                     f"오늘 {name} 활동" if name else "오늘 트윈 활동"),
            en_or_ko(f"List {name}'s tasks" if name else "List tasks",
                     f"{name} 작업 목록" if name else "작업 목록"),
            en_or_ko("Send a message to a twin", "트윈에게 메시지 보내"),
        ]
    if tool_used == "open_portal":
        portal = (tool_result or {}).get("agent") or "the agent"
        return [
            en_or_ko(f"What's the status of {portal}?", f"{portal} 상태는?"),
            en_or_ko("Show agent health", "에이전트 상태 보여줘"),
            en_or_ko("Open the agents list", "에이전트 목록 열어"),
        ]
    if tool_used == "navigate":
        return [
            en_or_ko("What can I do on this page?", "이 페이지에서 뭘 할 수 있어?"),
            en_or_ko("Go back", "뒤로"),
            en_or_ko("Show me the dashboard", "대시보드 보여줘"),
        ]
    if tool_used == "count":
        return [
            en_or_ko("List them with details", "자세한 목록"),
            en_or_ko("Which are active right now?", "지금 활성 상태?"),
            en_or_ko("Show today's activity", "오늘 활동 보여줘"),
        ]
    if tool_used in ("search_conversations", "conversation_history"):
        return [
            en_or_ko("Reply to this conversation", "이 대화에 답장"),
            en_or_ko("Mark as resolved", "해결됨으로 표시"),
            en_or_ko("Escalate it as urgent", "긴급으로 에스컬레이트"),
        ]
    if tool_used in ("latest_report", "search_reports", "trigger_daily_report"):
        return [
            en_or_ko("Compose a weekly report", "주간 리포트 생성"),
            en_or_ko("Email this to the team", "팀에게 이메일"),
            en_or_ko("Show the next report due", "다음 리포트 일정"),
        ]
    if tool_used == "agent_status":
        return [
            en_or_ko("Show all three agents' status", "세 에이전트 모두 상태"),
            en_or_ko("Ping every agent now", "모든 에이전트 핑"),
            en_or_ko("Open this agent's app", "이 에이전트 앱 열어"),
        ]
    if tool_used == "find_page":
        return [
            en_or_ko("Open it", "열어"),
            en_or_ko("What can I do there?", "거기서 뭐 할 수 있어?"),
            en_or_ko("Find something else", "다른 거 찾기"),
        ]
    if tool_used == "broadcast":
        return [
            en_or_ko("Show the broadcast history", "공지 기록 보기"),
            en_or_ko("Send a different message", "다른 메시지 보내"),
            en_or_ko("Schedule a daily summary", "일일 요약 예약"),
        ]
    if tool_used == "what_can_you_do":
        return [
            en_or_ko("Show today's situation", "오늘 상황 보여줘"),
            en_or_ko("How many twins do I have?", "트윈 몇 명?"),
            en_or_ko("Open the reports page", "리포트 페이지 열어"),
        ]
    # Generic fallback — works for any other read tool
    if tool_used != "[chain]":
        return [
            en_or_ko("Show me more detail", "자세히 보여줘"),
            en_or_ko("What else can you do?", "또 뭘 할 수 있어?"),
        ]
    return []


def run_agent(
    db: Session,
    transcript: str,
    language: str = "auto",
    current_path: Optional[str] = None,
    selected_id: Optional[str] = None,
    history: Optional[list[dict]] = None,
    confirmed_tool: Optional[str] = None,
    confirmed_args: Optional[dict] = None,
    attachment_ids: Optional[list[str]] = None,
    forced_model: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_id: str = "vip",
) -> dict[str, Any]:
    """Public entry — wraps the actual implementation with cross-session
    memory persistence (writes each turn to chat_sessions/chat_messages
    under channel='assistant_overlay' so recall_history can find it
    later). Persistence is best-effort and never blocks the response."""
    result = _run_agent_impl(
        db, transcript=transcript, language=language,
        current_path=current_path, selected_id=selected_id, history=history,
        confirmed_tool=confirmed_tool, confirmed_args=confirmed_args,
        attachment_ids=attachment_ids, forced_model=forced_model,
        user_id=user_id, agent_id=agent_id,
    )
    # Persist meaningful turns only — skip empty / multimodal_failed / errors
    skip_intents = {"empty", "multimodal_failed", "multimodal_missing", "chain_empty"}
    if user_id and result.get("intent") not in skip_intents and result.get("reply"):
        _persist_assistant_turn(
            db,
            user_id=user_id,
            user_text=transcript or "",
            assistant_reply=str(result.get("reply") or ""),
            intent=result.get("intent"),
            tool_used=result.get("tool_used"),
        )
    return result


def _run_agent_impl(
    db: Session,
    transcript: str,
    language: str = "auto",
    current_path: Optional[str] = None,
    selected_id: Optional[str] = None,
    history: Optional[list[dict]] = None,
    confirmed_tool: Optional[str] = None,
    confirmed_args: Optional[dict] = None,
    attachment_ids: Optional[list[str]] = None,
    forced_model: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_id: str = "vip",
) -> dict[str, Any]:
    """Run one agent turn. Returns:
        {intent, language, reply, action, speak, transcript, tool_used, tool_result,
         proposed_action?}

    confirmed_tool / confirmed_args:
        Set when the user clicked Confirm on a previously-proposed write
        action. We bypass the LLM and execute the tool directly.
    """
    # === Direct execute path (after user confirmed a proposed write) ===
    if confirmed_tool and confirmed_tool in TOOL_REGISTRY:
        tool = TOOL_REGISTRY[confirmed_tool]
        args = confirmed_args or {}
        # Carry the path through if the tool wants it
        if current_path and "current_path" not in args:
            args["current_path"] = current_path
        tool_result = execute_tool(confirmed_tool, args, db=db)
        action = tool_result.get("action") if isinstance(tool_result, dict) else None
        reply = tool_result.get("message") if isinstance(tool_result, dict) else "Done."
        if not reply:
            reply = "Done." if tool_result.get("ok") else f"Failed: {tool_result.get('error', 'unknown')}"
        return {
            "intent": confirmed_tool,
            "language": language if language in ("ko", "en") else "en",
            "reply": reply,
            "action": action,
            "speak": True,
            "transcript": transcript or f"[confirmed: {confirmed_tool}]",
            "tool_used": confirmed_tool,
            "tool_result": tool_result,
            "confirmed": True,
        }

    transcript = (transcript or "").strip()

    # Detect language for the output frame (LLM matches it itself)
    if language in ("ko", "en"):
        lang = language
    else:
        # Count Hangul
        hangul = sum(1 for c in transcript if 0xAC00 <= ord(c) <= 0xD7A3)
        lang = "ko" if hangul > 0 else "en"

    # === Multimodal handling (Slice 3) ===
    # When the user attached files, we now have TWO possible flows:
    #
    #   (a) Q&A about the file ("what's in this image?", "summarize this PDF")
    #       → short-circuit to Gemini Vision, no tool routing needed.
    #
    #   (b) ACTION on the file ("send Davronbek this image", "email this PDF
    #       to Kim", "broadcast this screenshot") → go through normal tool
    #       routing with attachment_ids exposed so the LLM passes them to
    #       send_dm / send_email / broadcast.
    #
    # We disambiguate by scanning the transcript for action verbs. If none
    # match, short-circuit to vision (cheap + fast). Otherwise tool-route.
    if attachment_ids:
        action_markers = (
            "send", "email", "broadcast", "share", "forward", "attach",
            "post", "publish", "upload to", "give it to", "give to",
            "보내", "전송", "공유", "전달", "올려",
        )
        tlow = (transcript or "").lower()
        is_action = any(m in tlow for m in action_markers)
        if not is_action:
            return _run_multimodal_path(transcript, lang, history or [], attachment_ids)
        # else: fall through to tool routing — the system prompt will tell
        # the LLM about the pending attachments so it can pass them to
        # the right write tool.
        from routers.chatbot import load_attachment
        pending: list[dict] = []
        for aid in attachment_ids:
            a = load_attachment(aid)
            if a:
                pending.append({
                    "attachment_id": aid,
                    "filename": a.get("filename"),
                    "kind": a.get("kind"),
                    "mime_type": a.get("mime_type"),
                })
        # Carry pending attachments into the system prompt below
        _pending_attachments = pending
    else:
        _pending_attachments = None

    if not transcript:
        return {
            "intent": "empty",
            "language": "en",
            "reply": "I didn't hear anything.",
            "action": None,
            "speak": True,
            "transcript": transcript,
        }

    # === RAG-first retrieval ===
    # Vector-search the agent's uploaded knowledge base BEFORE the LLM
    # decision. Top matches are injected into the system prompt as verbatim
    # excerpts with file/sheet citations. When nothing scores above the
    # similarity floor (rag_retrieve returns []), the prompt has no kb_block
    # and the LLM falls back to its own knowledge — exactly the behaviour
    # the user requested ("first search inside our DB locally, then answer
    # based on his knowledge").
    kb_hits: list[dict] = []
    rag_error: Optional[str] = None
    try:
        from services.knowledge_ingest import rag_retrieve
        kb_hits = rag_retrieve(
            db,
            agent_id=agent_id,
            query=transcript,
            top_k=8,
            min_sim=0.35,
        )
        if kb_hits:
            log.info(
                "rag: %d hits for agent=%s query=%r (top sim=%.2f)",
                len(kb_hits), agent_id, transcript[:60], kb_hits[0]["similarity"],
            )
    except Exception as e:
        rag_error = str(e)[:200]
        log.warning("rag retrieval failed (continuing without KB): %s", e)

    # Pull the file index regardless of chunk matches so the LLM always
    # knows which files the boss has uploaded. Critical for vague queries
    # like "what files do I have?" / "what do you know about me?" /
    # "내가 올린 파일 알려줘" — questions that don't keyword-match any
    # individual chunk but obviously refer to the uploaded KB.
    kb_files: list[dict] = []
    try:
        from sqlalchemy import text as _sa_text
        rows = db.execute(_sa_text("""
            SELECT f.filename,
                   f.size_bytes,
                   f.chunk_count,
                   (SELECT c.content FROM assistant_knowledge_chunks c
                    WHERE c.file_id = f.id
                    ORDER BY c.id ASC
                    LIMIT 1) AS preview
            FROM assistant_knowledge_files f
            WHERE f.agent_id = :agent_id
              AND f.status = 'indexed'
            ORDER BY f.uploaded_at DESC NULLS LAST
            LIMIT 30
        """), {"agent_id": agent_id}).fetchall()
        kb_files = [
            {
                "filename": r.filename,
                "size_bytes": r.size_bytes,
                "chunk_count": r.chunk_count,
                "preview": r.preview,
            }
            for r in rows
        ]
        if kb_files:
            log.info("file-index: %d files for agent=%s", len(kb_files), agent_id)
    except Exception as e:
        log.warning("file-index lookup failed (continuing without it): %s", e)

    system = _build_system_prompt(
        current_path=current_path,
        selected_id=selected_id,
        pending_attachments=_pending_attachments if attachment_ids else None,
        kb_context=kb_hits,
        kb_files=kb_files,
    )
    _debug_kb = {
        "agent_id": agent_id,
        "hit_count": len(kb_hits),
        "file_count": len(kb_files),
        "files": [f["filename"] for f in kb_files[:10]],
        "top_hits": [
            {"location": h.get("location"), "similarity": h.get("similarity"),
             "preview": (h.get("content") or "")[:120]}
            for h in kb_hits[:3]
        ],
        "rag_error": rag_error,
        "system_prompt_chars": len(system),
    }

    # Auto-fill ID args from selected_id when the LLM picks a tool that
    # needs an ID but the user said "this" (LLM may not include the ID).
    # Done after LLM decision, see below.

    # ===== Turn 1: decision =====
    decision = _call_llm_for_decision(system, transcript, history or [], forced_model=forced_model)

    # ===== Phase 5: Multi-step chain =====
    steps = decision.get("steps")
    if isinstance(steps, list) and len(steps) > 0:
        return _run_chain(db, transcript, lang, steps, current_path, selected_id, system, history or [])

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
            "_debug_kb": _debug_kb,
            "suggestions": [
                ("What can you do?" if lang != "ko" else "뭘 할 수 있어?"),
                ("Show today's situation" if lang != "ko" else "오늘 상황 보여줘"),
                ("Open the dashboard" if lang != "ko" else "대시보드 열어"),
            ],
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

    # === Phase 4: Page-context auto-fill ===
    # If the user said "this" and the tool needs an ID arg that wasn't
    # populated by the LLM, fill from selected_id.
    if selected_id:
        id_keys = ("conversation_id", "report_id", "twin_id", "meeting_id",
                   "handoff_id", "task_id", "knowledge_id")
        for k in id_keys:
            if k in (tool.parameters.get("properties") or {}) and not args.get(k):
                args[k] = selected_id
                break

    # === PERMISSION GATE for WRITE tools (Phase 3) ===
    # If the picked tool is a write/destructive action, DO NOT execute.
    # Instead return a proposed_action so the frontend can render a
    # confirm card. User clicks Confirm → widget re-calls /chat/agent
    # with confirmed_tool + confirmed_args.
    if tool.requires_confirmation:
        # Carry current_path so previews / re-runs have it
        preview_args = dict(args or {})
        if current_path and "current_path" not in preview_args:
            preview_args["current_path"] = current_path
        # Compose a human-readable preview
        preview = _compose_write_preview(tool_name, preview_args)
        return {
            "intent": tool_name,
            "language": lang,
            "reply": preview["message"],
            "action": None,
            "speak": True,
            "transcript": transcript,
            "tool_used": None,
            "proposed_action": {
                "tool": tool_name,
                "args": preview_args,
                "summary": preview["message"],
                "details": preview.get("details"),
                "requires_confirmation": True,
            },
        }

    # READ tools execute immediately
    # recall_history needs to know whose history to search — inject user_id
    if tool_name == "recall_history" and user_id and "user_id" not in args:
        args["user_id"] = user_id
    tool_result = execute_tool(tool_name, args, db=db)

    # === Phase 6: Build inline result card ===
    card = _build_card(tool_name, tool_result)

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
        "card": card,
        # Notion-AI-style follow-up chips (rendered by the overlay)
        "suggestions": _suggest_followups(tool_name, tool_result, lang),
    }
