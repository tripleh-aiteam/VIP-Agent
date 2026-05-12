"""
Chatbot ACTION pillar — detects multi-step (workflow) requests in natural
language and decomposes them into a sequence of executable steps.

Examples that trigger workflows:
  - "Generate a daily report and then open the reports page"
  - "Send a message to Davronbek that we have a meeting at 3, and broadcast to the team"
  - "Run the weekly report, then open it"

Examples that are NOT workflows (single-step — handled by chatbot_talk.py):
  - "Open the reports page"
  - "What is my stock status"
  - "Send a message to Davronbek: come"
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from services.logger import log


# Compound-request signals — short-circuit detection before invoking LLM
_COMPOUND_PATTERNS_EN = [
    r"\band then\b", r"\bafter that\b", r"\band also\b",
    r"\bfollowed by\b", r"\bnext\b.*\b(then|after)\b",
    r"\b,\s*then\b", r"\bthen\s+(open|run|generate|send|broadcast|navigate)\b",
    r"\b(do|please)\s+\w+.*\b(and)\s+(also|then)\b",
]
_COMPOUND_PATTERNS_KO = [
    r"그\s*다음(에)?", r"그리고\s*(그)?다음", r"그런\s*다음",
    r"그\s*후에", r"이후에", r"한\s*다음", r"하고\s*나서",
    r"그리고\s+(나서|또)?",
]


def looks_like_workflow(query: str) -> bool:
    """Quick heuristic: does this query describe multiple sequential actions?"""
    q = query.lower()
    if any(re.search(p, q) for p in _COMPOUND_PATTERNS_EN):
        return True
    if any(re.search(p, q) for p in _COMPOUND_PATTERNS_KO):
        return True
    return False


def plan_workflow(query: str, lang: str, intents: list[dict], agent_id: str) -> Optional[list[dict]]:
    """
    Use the LLM to decompose a compound request into a list of steps.
    Each step is a dict { intent_name, label, params }.
    Returns None if the LLM declines (not actually a workflow).
    """
    try:
        from services.llm_client import chat_completion_sync
    except Exception:
        return None

    intent_menu = "\n".join(
        f"- {it['name']}: {it['description']}"
        for it in intents
    )

    if lang == "ko":
        system = (
            f"당신은 '{agent_id}' 에이전트의 워크플로우 플래너입니다. 사용자의 복합 요청을 "
            f"순차 실행 단계로 분해합니다.\n\n"
            "사용 가능한 인텐트:\n" + intent_menu + "\n\n"
            "JSON 응답 형식 (다른 텍스트 금지):\n"
            '{ "is_workflow": true|false,\n'
            '  "steps": [\n'
            '    { "intent": "<인텐트명>", "label": "<단계 설명 (한국어)>", "params": { "key":"value" } }\n'
            "  ]\n}\n\n"
            "규칙:\n"
            "- 단일 동작이면 is_workflow=false, steps=[]\n"
            "- 사용자가 여러 동작을 순서대로 요청하면 is_workflow=true, 각 동작을 steps에 추가\n"
            "- 위 인텐트 목록에 없는 단계는 만들지 마세요\n"
            "- params는 실행에 필요한 추가 정보 (예: 메시지 본문, 대상 이름)\n"
            "- 변수 참조: 이전 단계의 출력을 다음 단계에서 사용하려면 params에서 \"{{step1}}\" 또는 \"{{step1.reply}}\" 사용. "
            "예: 1단계가 자산 상태 조회라면 2단계의 message 파라미터를 \"{{step1.reply}}\"로 설정해 1단계 답변을 그대로 메시지로 보낼 수 있음.\n"
        )
    else:
        system = (
            f"You are the workflow planner for the '{agent_id}' agent. Decompose the user's "
            f"compound request into a sequence of executable steps.\n\n"
            "Available intents:\n" + intent_menu + "\n\n"
            "Respond ONLY with this JSON (no other text):\n"
            '{ "is_workflow": true|false,\n'
            '  "steps": [\n'
            '    { "intent": "<intent_name>", "label": "<one-line description>", "params": { "key":"value" } }\n'
            "  ]\n}\n\n"
            "Rules:\n"
            "- Single action → is_workflow=false, steps=[]\n"
            "- Multiple sequential actions → is_workflow=true, one entry per action\n"
            "- Only use intent names from the list above\n"
            "- params hold extra info per step (e.g. message body, target name)\n"
            "- Variable references: to feed an earlier step's output into a later step, write `{{step1}}` "
              "or `{{step1.reply}}` in the params. Example — user asks 'show me asset status and "
              "send the summary to Davronbek': step 1 = query_asset (no params needed), step 2 = "
              'send_twin_message with params { target: "Davronbek", message: "{{step1.reply}}" }. '
              "The substitution happens automatically before step 2 runs.\n"
        )

    try:
        raw = chat_completion_sync(
            system_prompt=system,
            messages=[{"role": "user", "content": query}],
            max_tokens=600,
            temperature=0.2,
            model="claude-haiku-4-5",
        )
    except Exception as e:
        log.warning(f"chatbot.action plan_workflow LLM error: {e}")
        return None

    raw = (raw or "").strip()
    if not raw or raw.startswith("[LLM unavailable]"):
        return None

    parsed = _try_extract_json(raw)
    if not isinstance(parsed, dict):
        return None
    if not parsed.get("is_workflow"):
        return None
    steps = parsed.get("steps") or []
    if not isinstance(steps, list) or len(steps) < 2:
        return None  # Need at least 2 steps to be a workflow

    # Sanity-check each step has a known intent name
    valid_intent_names = {it["name"] for it in intents}
    cleaned: list[dict] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        intent = s.get("intent", "")
        if intent not in valid_intent_names:
            log.info(f"chatbot.action: dropping unknown intent '{intent}' from plan")
            continue
        cleaned.append({
            "intent": intent,
            "label": s.get("label") or intent,
            "params": s.get("params") or {},
        })
    if len(cleaned) < 2:
        return None
    return cleaned


def _try_extract_json(text: str) -> Any:
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
                    return None
    return None


def execute_step_plan(
    db, plan: list[dict], lang: str, agent_id: str,
    intents: list[dict], snapshot: dict[str, str],
) -> tuple[str, list[dict], Optional[dict]]:
    """
    Execute each step in the plan sequentially. Each step's output (reply text,
    action data) is captured into a `variables` dict so subsequent steps can
    reference it via `{{step1}}` / `{{step1.reply}}` / `{{step1.action.to}}`
    placeholders in their params.

    Example: "Show me my asset status and then send the summary to Davronbek"
      Step 1: query_asset → reply = "Portfolio 263.5B KRW · ..."
      Step 2: send_twin_message  with params = { target: "Davronbek", message: "{{step1.reply}}" }
              → message body becomes the asset summary

    Returns (final_reply, process_log, last_action).
    """
    from services.chatbot_talk import _execute_intent

    process_log: list[dict] = []
    last_action: Optional[dict] = None
    replies: list[str] = []
    variables: dict[str, dict] = {}  # "step1" -> {"reply": str, "action": dict|None}

    for i, step in enumerate(plan):
        intent_name = step["intent"]
        label = step["label"]
        raw_params = step.get("params") or {}

        # Substitute {{stepN}} / {{stepN.field}} references using prior outputs
        params = _substitute_variables(raw_params, variables)

        process_log.append({
            "icon": "▶",
            "label": label,
            "status": "running",
        })

        try:
            reply, action = _execute_intent(
                db, intent_name, params.get("query") or label, lang, snapshot,
                llm_extracted=params,
            )
            process_log[-1]["status"] = "done"
            process_log[-1]["icon"] = "✓"
            if action:
                last_action = action
            if reply:
                replies.append(reply)

            # Capture step output for downstream substitution
            variables[f"step{i + 1}"] = {
                "reply": reply or "",
                "action": action,
                **{k: v for k, v in (params.items() if isinstance(params, dict) else []) if isinstance(v, (str, int, float))},
            }
        except Exception as e:
            log.warning(f"chatbot.action step {i} '{intent_name}' failed: {e}")
            process_log[-1]["status"] = "error"
            process_log[-1]["icon"] = "✗"
            replies.append(_voice_failure_msg(intent_name, lang))
            variables[f"step{i + 1}"] = {"reply": "", "action": None, "error": str(e)}

    # Combined final reply — concise summary of what got done
    if lang == "ko":
        final = f"{len(plan)}개 작업 완료. " + " · ".join(replies)[:500]
    else:
        final = f"Done — {len(plan)} steps completed. " + " · ".join(replies)[:500]
    return final, process_log, last_action


def _substitute_variables(params: dict, variables: dict[str, dict]) -> dict:
    """
    Walk through params and replace `{{stepN}}` / `{{stepN.field}}` placeholders
    with the corresponding string from a previous step's output.
    """
    import re as _re

    pattern = _re.compile(r"\{\{\s*(step\d+)(?:\.(\w+))?\s*\}\}")

    def resolve(match: _re.Match) -> str:
        step_key = match.group(1)
        field = match.group(2)
        step = variables.get(step_key)
        if not step:
            return match.group(0)  # leave placeholder as-is
        if field is None:
            # Default to the reply text
            return str(step.get("reply", ""))
        # Field access — supports nested action.to / action.endpoint etc.
        if field == "action" and isinstance(step.get("action"), dict):
            return str(step["action"])
        val = step.get(field)
        if val is None and isinstance(step.get("action"), dict):
            val = step["action"].get(field)
        return "" if val is None else str(val)

    out: dict = {}
    for k, v in params.items():
        if isinstance(v, str):
            out[k] = pattern.sub(resolve, v)
        else:
            out[k] = v
    return out


def _voice_failure_msg(intent_name: str, lang: str) -> str:
    if lang == "ko":
        return f"{intent_name} 단계 실패"
    return f"Step '{intent_name}' failed"
