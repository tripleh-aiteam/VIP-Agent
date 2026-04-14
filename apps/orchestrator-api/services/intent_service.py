"""
VIP AI Platform — Intent Classification Service
Rule-based MVP. Modular design — swap in LLM/NLU later without changing interface.

9 intent categories, 25+ phrase patterns, entity extraction, confidence scoring.
"""

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Intent result model
# ---------------------------------------------------------------------------

@dataclass
class IntentResult:
    intent: str                           # system_status, agent_inspection, etc.
    confidence: float                     # 0.0 - 1.0
    entities: dict[str, Any] = field(default_factory=dict)  # extracted entities
    matched_pattern: str = ""             # which pattern matched
    original_text: str = ""

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "confidence": round(self.confidence, 2),
            "entities": self.entities,
            "matched_pattern": self.matched_pattern,
            "original_text": self.original_text,
        }


# ---------------------------------------------------------------------------
# Pattern definitions — 25+ patterns across 9 categories
# ---------------------------------------------------------------------------

INTENT_PATTERNS: list[tuple[str, str, float, list[str]]] = [
    # (intent, pattern_name, base_confidence, regex_patterns)

    # 1. system_status
    ("system_status", "status_direct", 0.95, [
        r"^/status$", r"^status$",
    ]),
    ("system_status", "status_phrase", 0.85, [
        r"system\s+status", r"show.*status", r"how\s+is.*system",
        r"platform\s+status", r"health\s+check", r"is.*system.*online",
        r"what.*status", r"check\s+health",
    ]),

    # 2. agent_inspection
    ("agent_inspection", "agents_direct", 0.95, [
        r"^/agents$", r"^agents$", r"^list\s+agents$",
    ]),
    ("agent_inspection", "agents_phrase", 0.85, [
        r"show.*agents", r"which\s+agents", r"agent.*status",
        r"agents?\s+(are\s+)?(failing|offline|down|active|running)",
        r"list.*registered.*agents", r"agent.*health",
        r"how\s+many\s+agents", r"agent.*reliability",
    ]),

    # 3. workflow_trigger
    ("workflow_trigger", "run_direct", 0.95, [
        r"^/run_daily$", r"^/run_weekly$",
    ]),
    ("workflow_trigger", "run_asset", 0.90, [
        r"run\s+asset", r"trigger\s+asset", r"execute\s+asset",
        r"start\s+asset", r"run\s+portfolio",
    ]),
    ("workflow_trigger", "run_stock", 0.90, [
        r"run\s+stock", r"trigger\s+stock", r"execute\s+stock",
        r"start\s+stock", r"run\s+market",
    ]),
    ("workflow_trigger", "run_realty", 0.90, [
        r"run\s+realt", r"trigger\s+realt", r"execute\s+realt",
        r"start\s+realt", r"run\s+property", r"run\s+listing",
    ]),
    ("workflow_trigger", "run_generic", 0.80, [
        r"run\s+daily", r"run\s+weekly", r"trigger\s+report",
        r"generate\s+report", r"compose\s+report",
        r"run\s+scheduled", r"execute\s+workflow",
    ]),

    # 4. report_request
    ("report_request", "report_direct", 0.95, [
        r"^/report$", r"^report$",
    ]),
    ("report_request", "report_phrase", 0.85, [
        r"show.*report", r"latest\s+report", r"daily\s+report",
        r"weekly\s+report", r"alert\s+report", r"view.*report",
        r"get.*report", r"report\s+summary", r"executive\s+summary",
    ]),

    # 4b. report_explainer (follow-up questions about reports)
    ("report_explainer", "explain_report", 0.92, [
        r"explain.*(?:today|summary|report)", r"what.*biggest?\s+risk",
        r"which\s+agent\s+found", r"where.*data.*come\s+from",
        r"what\s+needs\s+approv", r"action\s+required",
        r"compare.*(?:stock|real|asset).*(?:stock|real|asset)",
        r"what.*missing.*report", r"missing\s+data", r"any.*missing", r"data\s+coverage", r"incomplete",
    ]),
    ("report_explainer", "report_detail_ask", 0.88, [
        r"tell.*about.*(?:asset|stock|realt|portfolio|market|property)",
        r"what.*(?:sentiment|vacancy|yield|risk\s+level|holdings)",
        r"how.*(?:stock|market|portfolio).*doing",
        r"any.*(?:buy|sell)\s+signal", r"market\s+trend",
    ]),

    # 5. approval_action
    ("approval_action", "approve_direct", 0.95, [
        r"^/approve\s+", r"^approve\s+",
    ]),
    ("approval_action", "reject_direct", 0.95, [
        r"^/reject\s+", r"^reject\s+",
    ]),
    ("approval_action", "approval_phrase", 0.85, [
        r"show.*approval", r"pending\s+approval", r"approval.*queue",
        r"^/approvals$", r"^approvals$", r"what.*needs.*approv",
        r"review\s+cases", r"pending\s+reviews",
    ]),
    ("approval_action", "high_risk", 0.90, [
        r"high\s+risk\s+cases", r"show.*high.*risk", r"risky\s+cases",
        r"critical\s+cases", r"dangerous\s+cases",
    ]),

    # 6. judgement_explanation
    ("judgement_explanation", "explain_case", 0.95, [
        r"explain\s+case\s+", r"explain\s+[0-9a-f]{6,}",
        r"details?\s+(?:for|of|on)\s+case", r"what\s+happened.*case",
    ]),
    ("judgement_explanation", "why_pending", 0.90, [
        r"why\s+.*pending", r"why\s+.*task.*pending",
        r"why\s+.*waiting", r"stuck.*task",
    ]),
    ("judgement_explanation", "judgement_why", 0.90, [
        r"why\s+.*rejected", r"why\s+.*fail", r"explain.*judgement",
        r"explain.*decision", r"why\s+.*risk", r"judgement.*detail",
        r"risk.*score.*explain", r"explain.*risk", r"what.*went\s+wrong",
    ]),
    ("judgement_explanation", "judgement_list", 0.85, [
        r"show.*judgement", r"judgement\s+cases", r"list.*cases",
        r"risk\s+evaluation", r"show.*risk",
    ]),

    # 7. a2a_inspection
    ("a2a_inspection", "a2a_direct", 0.90, [
        r"a2a\s+message", r"agent.to.agent", r"inter.agent",
        r"show.*a2a", r"recent\s+a2a", r"a2a\s+monitor",
    ]),
    ("a2a_inspection", "a2a_phrase", 0.80, [
        r"agent.*communicat", r"message.*between.*agent",
        r"what.*agents.*talking", r"agent.*sent.*message",
    ]),

    # 8. aiglass_inspection
    ("aiglass_inspection", "glass_direct", 0.90, [
        r"ai\s*glass", r"glass\s+session", r"capture\s+session",
        r"show.*glass", r"spatial\s+capture",
    ]),
    ("aiglass_inspection", "glass_phrase", 0.80, [
        r"3d\s+model", r"property\s+scan", r"glass.*device",
        r"processing\s+status.*glass", r"capture.*status",
    ]),

    # 9. cross_agent_analysis (must be before help — complex multi-agent requests)
    ("cross_agent_analysis", "cross_risk", 0.95, [
        r"overall\s+risk", r"full\s+risk\s+check", r"check.*risk.*today",
        r"risk\s+across", r"cross.*risk", r"portfolio\s+exposure",
    ]),
    ("cross_agent_analysis", "cross_market_drop", 0.95, [
        r"market\s+drop.*check", r"market\s+crash", r"stock.*drop.*portfolio",
        r"market.*fell.*check", r"crash.*exposure",
    ]),
    ("cross_agent_analysis", "cross_compare", 0.90, [
        r"compare.*asset.*stock", r"compare.*stock.*asset",
        r"compare.*views", r"cross.*comparison",
        r"asset.*and.*stock.*view", r"stock.*and.*asset.*view",
    ]),
    ("cross_agent_analysis", "cross_realty_market", 0.90, [
        r"real\s*estate.*market\s*risk", r"realty.*market.*risk",
        r"property.*stock.*risk", r"summarize.*real.*estate.*market",
    ]),
    ("cross_agent_analysis", "cross_executive", 0.95, [
        r"full\s+executive\s+summary", r"executive\s+summary",
        r"complete\s+summary", r"full\s+summary",
        r"run\s+everything", r"all\s+agents?\s+report",
        r"comprehensive\s+report", r"full\s+analysis",
    ]),

    # 10. help
    ("help", "help_direct", 0.95, [
        r"^/help$", r"^help$", r"what\s+can\s+you\s+do",
        r"how\s+.*use", r"commands?$",
    ]),
]


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

def _extract_entities(text: str, intent: str) -> dict[str, Any]:
    """Extract entities based on intent type."""
    entities: dict[str, Any] = {}

    # Case/approval ID (UUID fragment or short ID)
    uuid_match = re.search(r'([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})', text, re.I)
    if uuid_match:
        entities["case_id"] = uuid_match.group(1)
    else:
        short_id = re.search(r'(?:case|approve|reject)\s+([0-9a-f]{6,8})', text, re.I)
        if short_id:
            entities["case_id_prefix"] = short_id.group(1)

    # Report type
    if "daily" in text.lower():
        entities["report_type"] = "daily_summary"
    elif "weekly" in text.lower():
        entities["report_type"] = "weekly_summary"
    elif "alert" in text.lower():
        entities["report_type"] = "urgent_alert_summary"

    # Agent type
    if "asset" in text.lower() or "portfolio" in text.lower():
        entities["agent_type"] = "asset"
        entities["task_type"] = "asset_summary"
    elif "stock" in text.lower() or "market" in text.lower():
        entities["agent_type"] = "stock"
        entities["task_type"] = "stock_analysis"
    elif "realt" in text.lower() or "property" in text.lower() or "listing" in text.lower():
        entities["agent_type"] = "realty"
        entities["task_type"] = "realty_listing_fetch"

    # Agent name
    agent_match = re.search(r'agent\s+["\']?([a-zA-Z0-9_-]+)', text, re.I)
    if agent_match:
        entities["agent_name"] = agent_match.group(1)

    # Action (approve/reject) and sub-type
    if intent == "approval_action":
        if re.search(r'approve|accept|ok|yes', text, re.I):
            entities["action"] = "approve"
        elif re.search(r'reject|deny|no|decline', text, re.I):
            entities["action"] = "reject"
        if re.search(r'high\s*risk|critical|dangerous|risky', text, re.I):
            entities["filter"] = "high_risk"

    # Task ID for "why is task {id} pending"
    task_id_match = re.search(r'task\s+([0-9a-f]{6,})', text, re.I)
    if task_id_match and not uuid_match:
        entities["task_id_prefix"] = task_id_match.group(1)

    # Cross-agent workflow type
    if intent == "cross_agent_analysis":
        lower = text.lower()
        if "executive" in lower or "full summary" in lower or "everything" in lower or "comprehensive" in lower or "all agent" in lower:
            entities["workflow"] = "full_executive"
        elif "risk" in lower or "exposure" in lower or "drop" in lower or "crash" in lower:
            entities["workflow"] = "risk_check"
        elif "compare" in lower:
            entities["workflow"] = "comparison"
        elif "real estate" in lower or "realty" in lower or "property" in lower:
            entities["workflow"] = "realty_market"
        else:
            entities["workflow"] = "risk_check"

    return entities


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify(text: str) -> IntentResult:
    """
    Classify user input into an intent with confidence and entities.
    Rule-based MVP — replace this function with LLM call later.
    Interface stays the same.
    """
    normalized = text.lower().strip()
    best_intent = "unknown"
    best_confidence = 0.0
    best_pattern = ""

    for intent, pattern_name, base_confidence, regexes in INTENT_PATTERNS:
        for regex in regexes:
            if re.search(regex, normalized):
                # Boost confidence for exact matches
                confidence = base_confidence
                if re.fullmatch(regex, normalized):
                    confidence = min(confidence + 0.05, 1.0)

                if confidence > best_confidence:
                    best_intent = intent
                    best_confidence = confidence
                    best_pattern = f"{pattern_name}:{regex}"
                break  # first match in this group is enough

    entities = _extract_entities(text, best_intent)

    return IntentResult(
        intent=best_intent,
        confidence=best_confidence if best_intent != "unknown" else 0.1,
        entities=entities,
        matched_pattern=best_pattern,
        original_text=text,
    )


# ---------------------------------------------------------------------------
# Batch classification (for testing)
# ---------------------------------------------------------------------------

def classify_batch(texts: list[str]) -> list[dict]:
    return [classify(t).to_dict() for t in texts]
