"""
VIP AI Platform — Twin Brain Service
The core intelligence engine for digital twins.

Features:
- #23 Task execution: twins actually work on assigned tasks
- #24 Conversation memory: twins remember past chats
- #25 Context window management: smart knowledge selection for LLM prompt
- #26 Tool usage: twins can call APIs, fetch data, search
"""

import json
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import (
    DigitalTwin, TwinKnowledge, TwinActivityLog, TwinTask,
    ChatMessage, ChatSession, CoreAgent,
)
from services import twin_service
from services.llm_client import chat_completion_sync


# ---------------------------------------------------------------------------
#  #25 — Context Window Management
# ---------------------------------------------------------------------------

def _select_relevant_knowledge(
    knowledge_docs: list[TwinKnowledge],
    user_message: str,
    max_docs: int = 5,
    max_chars: int = 2000,
) -> list[TwinKnowledge]:
    """
    Smart selection with priority weighting (from colleague.skill approach).

    Priority hierarchy:
    1. CORRECTIONS (highest) — past mistakes to never repeat (score +15)
    2. HARD RULES — decision rules the worker explicitly taught (score +10)
    3. LONG DOCUMENTS — SOPs, design docs, standards (score +8)
    4. INSTRUCTIONS — procedural knowledge (score +6)
    5. RECENT CHAT LEARNING (score +4)
    6. OLDER CHAT Q&As (score +2)
    """
    if not knowledge_docs:
        return []

    message_lower = user_message.lower()
    scored = []

    for doc in knowledge_docs:
        score = 0
        title_lower = (doc.title or "").lower()
        content_lower = (doc.content or "").lower()
        content_len = len(doc.content or "")

        # === PRIORITY WEIGHTING (colleague.skill hierarchy) ===

        # 1. Corrections = HIGHEST priority (never repeat mistakes)
        if "correction" in title_lower or "pattern rule" in title_lower:
            score += 15

        # 2. Hard rules (When X → Do Y pattern)
        elif doc.source_type == "decision":
            if "rule" in title_lower or content_lower.startswith("rule:") or "when " in content_lower[:50]:
                score += 10
            else:
                score += 8  # Other decision-type knowledge

        # 3. Long documents (300+ chars = substantial content) = high priority
        elif doc.source_type == "document" and content_len > 300:
            score += 8

        # 4. Instructions
        elif doc.source_type == "instruction":
            score += 6

        # 5. Short documents (could be Q&A or casual notes)
        elif doc.source_type == "document":
            score += 4

        # 6. Style knowledge
        elif doc.source_type == "style":
            score += 5

        # === RELEVANCE BOOST (keyword matching) ===
        words = [w for w in message_lower.split() if len(w) > 2]
        title_matches = sum(1 for w in words if w in title_lower)
        content_matches = sum(1 for w in words if w in content_lower)

        score += title_matches * 4  # Title match is very important
        score += min(content_matches, 5) * 1  # Content match is a boost, capped at 5

        # === RECENCY BOOST ===
        if doc.created_at:
            days_old = (datetime.utcnow() - doc.created_at).days
            if days_old < 3:
                score += 3  # Very recent — likely current
            elif days_old < 7:
                score += 2
            elif days_old < 30:
                score += 1

        # === SIZE PENALTY (very long docs cost tokens) ===
        if content_len > 1000:
            score -= 2  # Slightly deprioritize huge docs

        scored.append((score, doc))

    # Sort by score descending, take top N with budget
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = []
    total_chars = 0

    for score, doc in scored[:max_docs]:
        if score <= 0:
            break  # Skip irrelevant knowledge
        content_len = len(doc.content or "")
        if total_chars + content_len > max_chars:
            remaining = max_chars - total_chars
            if remaining > 200:
                selected.append(doc)
                total_chars += remaining
            break
        selected.append(doc)
        total_chars += content_len

    return selected


# ---------------------------------------------------------------------------
#  #24 — Conversation Memory
# ---------------------------------------------------------------------------

def _load_conversation_history(db: Session, twin_id: UUID, limit: int = 20) -> list[dict]:
    """Load past conversation messages for this twin from activity logs and direct messages."""
    from db.models import DirectMessage

    messages = []

    # Load from direct messages (boss ↔ worker conversations about this twin)
    dms = (
        db.query(DirectMessage)
        .filter(DirectMessage.twin_id == twin_id)
        .order_by(DirectMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    for dm in reversed(dms):
        role = "user" if dm.sender_type == "boss" else "assistant"
        messages.append({"role": role, "content": dm.content})

    # Load from activity logs (twin's own thinking/responding)
    activities = (
        db.query(TwinActivityLog)
        .filter(
            TwinActivityLog.twin_id == twin_id,
            TwinActivityLog.action_type.in_(["thinking", "responding", "task_completed"]),
        )
        .order_by(TwinActivityLog.timestamp.desc())
        .limit(10)
        .all()
    )

    # Build memory summary from activities
    if activities:
        memory_lines = []
        for a in reversed(activities):
            if a.action_type == "task_completed" and a.metadata_json:
                memory_lines.append(f"[Completed task: {a.description}]")
            elif a.action_type == "responding" and a.metadata_json:
                preview = a.metadata_json.get("response_preview", "")
                if preview:
                    memory_lines.append(f"[Previous response: {preview}]")

        if memory_lines:
            memory_summary = "\n".join(memory_lines[-5:])
            messages.insert(0, {
                "role": "system",
                "content": f"YOUR RECENT MEMORY (things you did/said before):\n{memory_summary}",
            })

    return messages[-limit:]


# ---------------------------------------------------------------------------
#  #26 — Tool Registry
# ---------------------------------------------------------------------------

AVAILABLE_TOOLS = {
    "fetch_agent_data": {
        "description": "Fetch real data from an agent (asset, stock, realty)",
        "params": ["agent_type"],
    },
    "search_knowledge": {
        "description": "Search your knowledge base for specific information",
        "params": ["query"],
    },
    "create_task": {
        "description": "Create a task for yourself or another twin",
        "params": ["title", "description", "priority"],
    },
    "get_current_tasks": {
        "description": "Get your current task list",
        "params": [],
    },
    "write_report": {
        "description": "Generate a report based on data",
        "params": ["report_type", "data_summary"],
    },
}


def _execute_tool(db: Session, twin_id: UUID, tool_name: str, params: dict) -> str:
    """Execute a tool and return the result."""
    twin = twin_service.get_twin(db, twin_id)

    if tool_name == "fetch_agent_data":
        return _tool_fetch_agent_data(db, params.get("agent_type", "asset"))

    elif tool_name == "search_knowledge":
        return _tool_search_knowledge(db, twin_id, params.get("query", ""))

    elif tool_name == "create_task":
        return _tool_create_task(db, twin_id, params)

    elif tool_name == "get_current_tasks":
        return _tool_get_tasks(db, twin_id)

    elif tool_name == "write_report":
        return _tool_write_report(db, twin_id, params)

    return f"Unknown tool: {tool_name}"


def _tool_fetch_agent_data(db: Session, agent_type: str) -> str:
    """Fetch data from a real agent via adapter."""
    twin_service.log_activity(db, None, "tool_call", f"Fetching data from {agent_type} agent")
    try:
        agent = db.query(CoreAgent).filter(CoreAgent.type == agent_type, CoreAgent.status == "active").first()
        if not agent:
            return f"No active {agent_type} agent found."

        from adapters import get_adapter
        adapter = get_adapter(agent.type, agent.name, agent.endpoint_url or "", agent.is_mock)
        data = adapter.fetch_summary()
        return f"Data from {agent.name}:\n{json.dumps(data, indent=2, default=str)[:2000]}"
    except Exception as e:
        return f"Error fetching from {agent_type}: {str(e)}"


def _tool_search_knowledge(db: Session, twin_id: UUID, query: str) -> str:
    """Search twin's knowledge base."""
    knowledge = twin_service.get_knowledge(db, twin_id)
    query_lower = query.lower()

    matches = []
    for k in knowledge:
        if query_lower in (k.title or "").lower() or query_lower in (k.content or "").lower():
            matches.append(f"[{k.source_type}] {k.title}: {k.content[:200]}")

    if matches:
        return f"Found {len(matches)} matches:\n" + "\n".join(matches[:5])
    return f"No knowledge found matching '{query}'."


def _tool_create_task(db: Session, twin_id: UUID, params: dict) -> str:
    """Create a new task."""
    task = twin_service.create_task(
        db, twin_id,
        title=params.get("title", "Untitled task"),
        description=params.get("description"),
        priority=params.get("priority", "medium"),
    )
    db.flush()
    return f"Task created: '{task.title}' (priority: {task.priority})"


def _tool_get_tasks(db: Session, twin_id: UUID) -> str:
    """Get current tasks for this twin."""
    tasks = twin_service.get_tasks(db, twin_id)
    if not tasks:
        return "No tasks assigned."
    lines = []
    for t in tasks[:10]:
        lines.append(f"- [{t.status}] {t.title} (priority: {t.priority})")
    return f"Your tasks ({len(tasks)} total):\n" + "\n".join(lines)


def _tool_write_report(db: Session, twin_id: UUID, params: dict) -> str:
    """Generate a report structure."""
    report_type = params.get("report_type", "general")
    data = params.get("data_summary", "")
    return f"Report draft ({report_type}):\n{data}\n\n[This report needs boss review before finalizing.]"


# ---------------------------------------------------------------------------
#  System Prompt Builder (Enhanced)
# ---------------------------------------------------------------------------

def build_system_prompt(
    twin: DigitalTwin,
    knowledge_docs: list[TwinKnowledge],
    available_tools: bool = True,
) -> str:
    """
    Build a 6-layer personality system prompt.

    Layer 1: Hard Rules (always/never)
    Layer 2: Identity (who you are)
    Layer 3: Expression (how you talk)
    Layer 4: Decisions (how you choose)
    Layer 5: Interpersonal (how you work with people)
    Layer 6: Corrections (mistakes to never repeat)
    """

    # Separate knowledge into layers
    hard_rules = []
    corrections = []
    decisions = []
    documents = []

    for doc in knowledge_docs:
        content = doc.content[:250] if len(doc.content or "") > 250 else doc.content
        if doc.source_type == "decision":
            title_lower = (doc.title or "").lower()
            if "correction" in title_lower or "pattern rule" in title_lower:
                corrections.append(content)
            elif "rule" in title_lower or "when" in (doc.content or "").lower()[:30]:
                hard_rules.append(content)
            else:
                decisions.append(content)
        elif doc.source_type == "instruction":
            hard_rules.append(content)
        else:
            documents.append(content)

    # Build natural, non-labeled prompt (small models work better with flowing text)
    prompt = f"You are {twin.name}, {twin.role} at VIP company.\n\n"

    # Identity
    if twin.personality_prompt:
        prompt += f"{twin.personality_prompt[:300]}\n\n"

    # Rules (combined, no labels)
    all_rules = hard_rules[:4] + corrections[:3] + decisions[:3]
    if all_rules:
        prompt += "IMPORTANT RULES YOU MUST FOLLOW:\n"
        for r in all_rules:
            clean = r.replace("CORRECTION from worker:", "").replace("CORRECTION from vip:", "").replace("RULE:", "").strip()
            prompt += f"- {clean[:180]}\n"
        prompt += "\n"

    # Communication style
    prompt += "HOW TO COMMUNICATE:\n"
    prompt += "- Boss/CEO: short bullet points, max 5 lines, simple words\n"
    prompt += "- Developers: technical and detailed\n"
    prompt += "- Reports: use bullet points, tables when helpful, include examples\n"
    prompt += "- Always respond in the same language the user uses\n\n"

    # Knowledge
    if documents:
        prompt += "KNOWLEDGE:\n"
        for d in documents[:2]:
            prompt += f"{d[:200]}\n"
        prompt += "\n"

    # Task instruction — CRITICAL for small models
    prompt += "WHEN USER GIVES YOU A TASK:\n"
    prompt += "- DO THE TASK immediately, don't ask for clarification\n"
    prompt += "- Write the actual report/answer, not a question back\n"
    prompt += "- If asked to write a report: write it with bullet points\n"
    prompt += "- If asked to summarize: summarize directly\n\n"

    # Permission
    perm = {
        "observe": "You can only report and analyze.",
        "suggest": "Draft work, flag for boss approval.",
        "act": "Execute tasks, flag important decisions.",
        "act_unsupervised": "Execute independently.",
    }
    prompt += perm.get(twin.permission_level, perm["suggest"])

    return prompt


# ---------------------------------------------------------------------------
#  Tool Call Parser
# ---------------------------------------------------------------------------

def _parse_and_execute_tools(db: Session, twin_id: UUID, response: str) -> str:
    """Parse tool calls from LLM response and execute them."""
    import re
    tool_pattern = r'\[TOOL:\s*(\w+)(?:\s*\|([^\]]*))?\]'
    matches = re.findall(tool_pattern, response)

    if not matches:
        return response

    for tool_name, params_str in matches:
        # Parse params
        params = {}
        if params_str:
            for param in params_str.split("|"):
                param = param.strip()
                if "=" in param:
                    key, value = param.split("=", 1)
                    params[key.strip()] = value.strip()

        # Execute tool
        twin_service.log_activity(db, twin_id, "tool_call", f"Using tool: {tool_name}({params})")
        result = _execute_tool(db, twin_id, tool_name, params)

        # Replace tool call with result in response
        tool_call_str = f"[TOOL: {tool_name}"
        if params_str:
            tool_call_str += f" | {params_str}"
        tool_call_str += "]"
        response = response.replace(tool_call_str, f"\n📊 **{tool_name} result:**\n{result}\n")

    return response


# ---------------------------------------------------------------------------
#  Twin Think (Core LLM Call — Enhanced)
# ---------------------------------------------------------------------------

def think(
    db: Session,
    twin_id: UUID,
    user_message: str,
    conversation_history: Optional[list[dict]] = None,
    model: Optional[str] = None,
) -> str:
    """
    Make a twin think and respond.
    1. Load twin profile
    2. Load conversation memory (#24)
    3. Select relevant knowledge (#25)
    4. Build system prompt with tools (#26)
    5. Call LLM
    6. Parse and execute tool calls (#26)
    7. Log activity
    8. Return response
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return "Twin not found."

    # Log: thinking started
    twin_service.log_activity(db, twin_id, "thinking", f"Processing: {user_message[:80]}...")

    # #24 — Load conversation memory
    memory = _load_conversation_history(db, twin_id, limit=5)

    # #25 — Smart knowledge selection based on message content
    all_knowledge = twin_service.get_knowledge(db, twin_id)
    relevant_knowledge = _select_relevant_knowledge(all_knowledge, user_message)

    # Build system prompt with tools (#26)
    system_prompt = build_system_prompt(twin, relevant_knowledge, available_tools=True)

    # Build message history: memory + explicit history + current message
    messages = []
    messages.extend(memory)
    if conversation_history:
        messages.extend(conversation_history[-10:])
    messages.append({"role": "user", "content": user_message})

    # Deduplicate (memory and history might overlap)
    seen = set()
    unique_messages = []
    for msg in messages:
        key = f"{msg['role']}:{msg['content'][:50]}"
        if key not in seen:
            seen.add(key)
            unique_messages.append(msg)
    messages = unique_messages[-8:]  # Keep last 8 messages max for speed

    # Call LLM (model param picks provider; None uses default)
    response = chat_completion_sync(
        system_prompt=system_prompt,
        messages=messages,
        max_tokens=1500,
        temperature=0.7,
        model=model,
    )

    # #26 — Parse and execute any tool calls in the response
    response = _parse_and_execute_tools(db, twin_id, response)

    # Log: response generated
    twin_service.log_activity(
        db, twin_id, "responding",
        f"Responded to: {user_message[:50]}...",
        {"response_preview": response[:200]},
    )

    # #33 — Chat-to-knowledge extraction (auto-learn from conversation)
    _auto_extract_knowledge(db, twin_id, user_message, response)

    # Update twin status
    twin_service.set_status(db, twin_id, "idle")
    db.flush()

    return response


# ---------------------------------------------------------------------------
#  #33 — Chat-to-Knowledge Extraction
# ---------------------------------------------------------------------------

# Skip saving for trivial/short messages
_SKIP_PATTERNS = [
    "hello", "hi", "hey", "thanks", "thank you", "ok", "okay", "yes", "no",
    "bye", "good", "great", "nice", "sure", "what can you do", "who are you",
]


def _auto_extract_knowledge(db: Session, twin_id: UUID, question: str, answer: str):
    """
    Automatically save useful Q&A pairs as twin knowledge.
    Skips trivial messages (greetings, short replies).
    This is how chatting with twin = training twin.
    """
    q_lower = question.lower().strip()

    # Skip trivial messages
    if len(question) < 15:
        return
    if any(q_lower.startswith(p) or q_lower == p for p in _SKIP_PATTERNS):
        return

    # Skip if answer is an error
    if answer.startswith("[LLM Error") or answer.startswith("[LLM Connection"):
        return

    # Skip very short answers
    if len(answer) < 30:
        return

    # Check if we already have similar knowledge (avoid duplicates)
    existing = twin_service.get_knowledge(db, twin_id)
    q_words = set(w.lower() for w in question.split() if len(w) > 3)
    for k in existing:
        title_words = set(w.lower() for w in (k.title or "").split() if len(w) > 3)
        overlap = len(q_words & title_words)
        if overlap >= 3:
            return  # Similar knowledge already exists

    # Determine knowledge type
    source_type = "document"
    q_indicators_decision = ["when", "should", "how to", "what if", "rule", "policy", "threshold", "always", "never"]
    q_indicators_instruction = ["how do", "steps", "process", "procedure", "guide", "instructions"]

    if any(ind in q_lower for ind in q_indicators_decision):
        source_type = "decision"
    elif any(ind in q_lower for ind in q_indicators_instruction):
        source_type = "instruction"

    # Build concise title from question
    title = question[:80].strip()
    if not title.endswith("?"):
        title = f"Q: {title}"

    # Save Q&A as knowledge
    content = f"Question: {question}\nAnswer: {answer[:800]}"

    twin_service.add_knowledge(
        db, twin_id,
        title=title,
        content=content,
        source_type=source_type,
    )

    twin_service.log_activity(
        db, twin_id, "auto_learn",
        f"Learned from chat: {title[:60]}",
        {"source": "chat", "type": source_type},
    )


# ---------------------------------------------------------------------------
#  #23 — Task Execution
# ---------------------------------------------------------------------------

def execute_task(db: Session, twin_id: UUID, task_id: UUID, model: Optional[str] = None) -> dict:
    """
    Twin actually works on an assigned task.
    Long-form output (up to ~6000 words) — use for research/reports.
    Pass `model` to override default LLM (e.g. 'claude-sonnet-4-6' for long writing).
    """
    twin = twin_service.get_twin(db, twin_id)
    task = db.query(TwinTask).filter(TwinTask.id == task_id).first()

    if not twin or not task:
        return {"error": "Twin or task not found"}

    # Update status
    twin_service.set_status(db, twin_id, "working")
    twin.current_task_id = task_id
    twin_service.update_task_status(db, task_id, "in_progress")
    twin_service.log_activity(db, twin_id, "working", f"Started task: {task.title}")
    db.flush()

    # S5: Proactive research — check if twin has enough knowledge first
    try:
        from services.twin_self_improve import research_before_task
        research_before_task(db, twin_id, task_id)
    except Exception:
        pass  # Don't block task execution if research fails

    # Build task prompt
    task_prompt = f"""You have been assigned a task. Complete it now.

TASK: {task.title}
DESCRIPTION: {task.description or 'No additional description'}
PRIORITY: {task.priority}
DEADLINE: {task.deadline.isoformat() if task.deadline else 'No deadline'}

Instructions:
1. Analyze what needs to be done
2. Use your tools if you need data (fetch_agent_data, search_knowledge)
3. Produce a clear, complete result
4. If this is a report, write the full report content
5. If this is an analysis, provide findings with data
6. If this requires code, describe the solution

Provide your complete work output below:"""

    # Twin thinks and works — task execution uses higher token budget for long reports
    # Bypass the standard short-form `think()` and call LLM directly for full output.
    all_knowledge = twin_service.get_knowledge(db, twin_id)
    relevant_knowledge = _select_relevant_knowledge(all_knowledge, task_prompt, max_docs=8, max_chars=4000)
    system_prompt = build_system_prompt(twin, relevant_knowledge, available_tools=False)
    system_prompt += "\n\nYOU ARE WORKING ON A TASK. Produce a thorough, well-structured deliverable. Use Markdown headings (## , ### ), bullet lists, tables where helpful. Aim for at least 2000–4000 words for research reports. Cite specific examples. Do NOT summarize — produce the complete deliverable."
    result = chat_completion_sync(
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": task_prompt}],
        max_tokens=6000,
        temperature=0.5,
        model=model or "claude-sonnet-4-6",
    )

    # Log completion
    twin_service.log_activity(
        db, twin_id, "task_completed",
        f"Completed task: {task.title}",
        {"task_id": str(task_id), "result_preview": result[:200]},
    )

    # Determine review status based on permission level
    if twin.permission_level == "act_unsupervised":
        twin_service.update_task_status(db, task_id, "done", result_text=result)
    else:
        twin_service.update_task_status(db, task_id, "review", result_text=result)

    # R6: Notify worker that task is completed
    try:
        from services.twin_notifications import notify
        notify(db, twin_id, "task_completed",
            f"Task completed: {task.title}",
            f"Your twin finished '{task.title}'. Status: {'done' if twin.permission_level == 'act_unsupervised' else 'needs review'}. {result[:100]}...")
    except Exception:
        pass

    # S1: Self-reflect on completed task
    try:
        from services.twin_self_improve import self_reflect_on_task
        self_reflect_on_task(db, twin_id, task_id)
    except Exception:
        pass  # Don't block if reflection fails

    # Reset twin status
    twin.current_task_id = None
    twin_service.set_status(db, twin_id, "idle")
    db.flush()

    return {
        "task_id": str(task_id),
        "twin_name": twin.name,
        "title": task.title,
        "status": "done" if twin.permission_level == "act_unsupervised" else "review",
        "result": result,
        "needs_review": twin.permission_level != "act_unsupervised",
    }


def execute_pending_tasks(db: Session, twin_id: UUID) -> list[dict]:
    """Execute all pending (todo) tasks for a twin. Used in auto-mode."""
    tasks = (
        db.query(TwinTask)
        .filter(TwinTask.twin_id == twin_id, TwinTask.status == "todo")
        .order_by(
            # Urgent first, then high, medium, low
            TwinTask.priority.desc(),
            TwinTask.created_at.asc(),
        )
        .all()
    )

    results = []
    for task in tasks:
        result = execute_task(db, twin_id, task.id)
        results.append(result)
        db.flush()

    return results


# ---------------------------------------------------------------------------
#  Quick Think (No History — backward compatible)
# ---------------------------------------------------------------------------

def quick_think(db: Session, twin_id: UUID, question: str) -> str:
    """Quick one-shot question to a twin, no conversation history."""
    return think(db, twin_id, question)
