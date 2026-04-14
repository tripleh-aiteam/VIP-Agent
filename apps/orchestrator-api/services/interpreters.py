"""
VIP AI Platform — Interpreter Abstraction Layer
RuleBasedInterpreter: current deterministic system
OpenAIInterpreter: AI-assisted intent + entity extraction

Both return the same IntentResult interface — action execution stays deterministic.
"""

import os
import json
from typing import Any

from services.intent_service import classify, IntentResult
from services.logger import log

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_MODE_ENABLED = os.getenv("LLM_MODE_ENABLED", os.getenv("LLM_MODE_ENABLED", "true")).lower() == "true"

# Intents that MUST remain deterministic — OpenAI cannot override these
DETERMINISTIC_INTENTS = {
    "approval_action", "workflow_trigger", "cross_agent_analysis",
}

# Intents where AI can safely enhance interpretation
AI_SAFE_INTENTS = {
    "system_status", "agent_inspection", "report_request", "report_explainer",
    "judgement_explanation", "a2a_inspection", "aiglass_inspection", "help", "unknown",
}


class RuleBasedInterpreter:
    """Current deterministic rule-based interpreter. No changes."""

    def interpret(self, text: str) -> IntentResult:
        return classify(text)


class OpenAIInterpreter:
    """
    AI-assisted interpreter. Uses OpenAI for flexible understanding,
    but falls back to rules for deterministic intents.
    """

    SYSTEM_PROMPT = """You are a VIP Agent Platform intent classifier. Given a user message, classify it into one of these intents:

- system_status: checking system health, agent counts, run counts
- agent_inspection: listing agents, checking agent health/reliability
- workflow_trigger: running tasks (asset summary, stock analysis, realty listing, daily/weekly report)
- report_request: viewing/showing existing reports
- report_explainer: asking questions about report content (risks, comparisons, details)
- approval_action: approving/rejecting cases, showing pending approvals, high risk cases
- judgement_explanation: explaining why something was rejected/failed, case details
- a2a_inspection: viewing agent-to-agent messages
- aiglass_inspection: viewing AI Glass capture sessions
- cross_agent_analysis: multi-agent workflows (overall risk check, full executive summary, comparisons)
- help: asking what the system can do
- unknown: cannot classify

Also extract entities:
- agent_type: asset, stock, realty
- task_type: asset_summary, stock_analysis, realty_listing_fetch
- report_type: daily_summary, weekly_summary, urgent_alert_summary
- case_id: any UUID or UUID prefix
- action: approve or reject
- workflow: risk_check, full_executive, comparison, realty_market

Respond ONLY with valid JSON:
{"intent": "...", "confidence": 0.0-1.0, "entities": {...}}"""

    def interpret(self, text: str) -> IntentResult:
        # First try rules — if high confidence, use rules (especially for deterministic intents)
        rule_result = classify(text)
        if rule_result.confidence >= 0.90 and rule_result.intent in DETERMINISTIC_INTENTS:
            log.info(f"ai-interpreter: using rules for deterministic intent {rule_result.intent}", extra={"action": "interpreter.rules_forced"})
            return rule_result

        # Try OpenAI
        if not OPENAI_API_KEY or not LLM_MODE_ENABLED:
            return rule_result

        try:
            import httpx
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": OPENAI_MODEL,
                        "messages": [
                            {"role": "system", "content": self.SYSTEM_PROMPT},
                            {"role": "user", "content": text},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 200,
                    },
                )

            if resp.status_code != 200:
                log.warning(f"ai-interpreter: OpenAI error {resp.status_code}", extra={"action": "interpreter.openai_error"})
                return rule_result

            content = resp.json()["choices"][0]["message"]["content"].strip()
            # Parse JSON from response
            parsed = json.loads(content)

            ai_intent = parsed.get("intent", "unknown")
            ai_confidence = float(parsed.get("confidence", 0.5))
            ai_entities = parsed.get("entities", {})

            # Safety: never let AI override deterministic intents if rules already matched
            if rule_result.intent in DETERMINISTIC_INTENTS and rule_result.confidence >= 0.8:
                final_intent = rule_result.intent
                final_entities = {**ai_entities, **rule_result.entities}  # merge, rules take priority
            else:
                final_intent = ai_intent
                final_entities = {**rule_result.entities, **ai_entities}

            log.info(
                f"ai-interpreter: rule={rule_result.intent}({rule_result.confidence:.2f}) ai={ai_intent}({ai_confidence:.2f}) final={final_intent}",
                extra={"action": "interpreter.ai_result"},
            )

            return IntentResult(
                intent=final_intent,
                confidence=max(ai_confidence, rule_result.confidence),
                entities=final_entities,
                matched_pattern=f"openai:{OPENAI_MODEL}",
                original_text=text,
            )

        except json.JSONDecodeError:
            log.warning("ai-interpreter: failed to parse OpenAI response", extra={"action": "interpreter.parse_error"})
            return rule_result
        except Exception as e:
            log.warning(f"ai-interpreter: error: {e}", extra={"action": "interpreter.error"})
            return rule_result


def get_interpreter(mode: str):
    """Factory: get the right interpreter for the chat mode."""
    if mode in ("llm", "ai_assist"):
        return OpenAIInterpreter()
    return RuleBasedInterpreter()
