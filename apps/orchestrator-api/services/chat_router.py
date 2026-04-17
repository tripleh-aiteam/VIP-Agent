"""
VIP AI Platform — Smart Chat Router
Decides whether to use deterministic rules or LLM for each user message.
Always tries the cheap path first. LLM only when necessary.

Decision flow:
  1. Classify with rule-based intent classifier (free, instant)
  2. If high confidence known intent → deterministic response
  3. If low confidence or unknown → try LLM interpretation
  4. Dangerous actions (approve/reject/workflows) → always deterministic
"""

from services.intent_service import classify, IntentResult
from services.logger import log


# ---------------------------------------------------------------------------
# Intent categories
# ---------------------------------------------------------------------------

# These intents MUST always use deterministic execution — never LLM
DETERMINISTIC_INTENTS = {
    "approval_action",      # approve/reject cases
    "workflow_trigger",     # run tasks, compose reports
    "cross_agent_analysis", # multi-agent workflows
}

# These intents are safe for LLM to enhance the response
LLM_SAFE_INTENTS = {
    "system_status",
    "agent_inspection",
    "report_request",
    "report_explainer",
    "judgement_explanation",
    "a2a_inspection",
    "aiglass_inspection",
    "help",
    "unknown",
}

# Confidence thresholds
HIGH_CONFIDENCE = 0.80    # above this → trust rules, skip LLM
LOW_CONFIDENCE = 0.50     # below this → definitely use LLM


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def is_deterministic_intent(intent: str) -> bool:
    """Check if this intent must always use deterministic execution."""
    return intent in DETERMINISTIC_INTENTS


def should_use_llm(intent_result: IntentResult) -> bool:
    """
    Decide if LLM should be used for this message.

    Returns True when:
    - Intent is unknown (rules couldn't classify)
    - Confidence is low (rules aren't sure)
    - Intent is safe for LLM enhancement

    Returns False when:
    - Intent is deterministic (approve/reject/workflow)
    - Confidence is high on a known intent
    """
    intent = intent_result.intent
    confidence = intent_result.confidence

    # Never use LLM for dangerous actions
    if is_deterministic_intent(intent):
        return False

    # Unknown intent → always use LLM
    if intent == "unknown":
        return True

    # Low confidence → use LLM for better understanding
    if confidence < LOW_CONFIDENCE:
        return True

    # Medium confidence on safe intents → use LLM to enhance response
    if confidence < HIGH_CONFIDENCE and intent in LLM_SAFE_INTENTS:
        return True

    # High confidence → rules are good enough, no LLM needed
    return False


def should_format_with_llm(intent_result: IntentResult) -> bool:
    """
    Decide if the response should be rewritten by LLM for natural language.

    Even when deterministic execution is used, LLM can rewrite the response
    to sound more natural — EXCEPT for approval actions where exact wording matters.
    """
    if intent_result.intent == "approval_action":
        return False  # approval responses must be exact

    # Format with LLM if confidence was low (user spoke naturally)
    if intent_result.confidence < HIGH_CONFIDENCE:
        return True

    # Format with LLM for explanation/report intents (user wants readable output)
    if intent_result.intent in ("report_explainer", "judgement_explanation", "report_request"):
        return True

    return False


# ---------------------------------------------------------------------------
# Main routing function
# ---------------------------------------------------------------------------

def route_message(user_text: str) -> dict:
    """
    Route a user message through the smart pipeline.

    Returns:
        {
            "intent_result": IntentResult,
            "use_llm_interpretation": bool,    # should we re-interpret with LLM?
            "use_llm_response": bool,          # should we format response with LLM?
            "use_llm_conversation": bool,      # should we use full LLM conversation?
            "deterministic": bool,             # is the action deterministic?
            "routing_reason": str,             # why this decision was made
        }
    """
    # Step 1: Rule-based classification (free, instant)
    intent_result = classify(user_text)

    intent = intent_result.intent
    confidence = intent_result.confidence
    use_llm = should_use_llm(intent_result)
    deterministic = is_deterministic_intent(intent)
    format_llm = should_format_with_llm(intent_result)

    # Determine routing reason for logging
    if intent == "unknown":
        reason = "unknown_intent → full LLM conversation"
        use_conversation = True
    elif deterministic:
        reason = f"deterministic_intent ({intent}) → rules only, confidence={confidence:.2f}"
        use_conversation = False
    elif confidence >= HIGH_CONFIDENCE:
        reason = f"high_confidence ({intent}={confidence:.2f}) → rules + optional LLM format"
        use_conversation = False
    elif use_llm:
        reason = f"low_confidence ({intent}={confidence:.2f}) → LLM re-interpretation"
        use_conversation = False
    else:
        reason = f"standard ({intent}={confidence:.2f})"
        use_conversation = False

    log.info(
        f"chat_router: {reason}",
        extra={"action": "chat_router.decision", "intent": intent, "confidence": confidence},
    )

    return {
        "intent_result": intent_result,
        "use_llm_interpretation": use_llm and not use_conversation,
        "use_llm_response": format_llm,
        "use_llm_conversation": use_conversation,
        "deterministic": deterministic,
        "routing_reason": reason,
    }
