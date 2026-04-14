"""
VIP AI Platform — Judgement Engine
Stage 1: Deterministic rule engine
Stage 2: Weighted risk scorer (simulates contextual risk 0-100)

Decision outputs: auto_approve | conditional_approve | human_review_required | rejected
"""

from typing import Any

from services.logger import log


# ===========================================================================
# Stage 1 — Deterministic Rule Engine
# ===========================================================================

class RuleResult:
    def __init__(self, rule_name: str, passed: bool, reason: str, severity: str = "info"):
        self.rule_name = rule_name
        self.passed = passed
        self.reason = reason
        self.severity = severity  # info | warning | critical


def rule_whitelist_check(output: dict, context: dict) -> RuleResult:
    """Check if agent/action is on the whitelist."""
    whitelisted_agents = context.get("whitelisted_agents", [
        "mock-asset-agent", "mock-stock-agent", "mock-realty-agent",
    ])
    agent_id = context.get("agent_id", "")
    if agent_id in whitelisted_agents:
        return RuleResult("whitelist_check", True, f"Agent {agent_id} is whitelisted")
    return RuleResult("whitelist_check", False, f"Agent {agent_id} not in whitelist", "warning")


def rule_blacklist_check(output: dict, context: dict) -> RuleResult:
    """Check if output contains blacklisted terms or patterns."""
    blacklisted = ["unauthorized", "override_security", "bypass"]
    output_str = str(output).lower()
    for term in blacklisted:
        if term in output_str:
            return RuleResult("blacklist_check", False, f"Blacklisted term found: {term}", "critical")
    return RuleResult("blacklist_check", True, "No blacklisted content found")


def rule_amount_threshold(output: dict, context: dict) -> RuleResult:
    """Check if monetary values exceed thresholds."""
    threshold = context.get("amount_threshold", 10_000_000)  # 10M KRW default
    total_value = output.get("total_value", 0)
    if isinstance(total_value, (int, float)) and total_value > threshold:
        return RuleResult(
            "amount_threshold", False,
            f"Amount {total_value:,.0f} exceeds threshold {threshold:,.0f}",
            "warning",
        )
    # Check nested recommendations
    for stock in output.get("stocks", []):
        price = stock.get("price", 0)
        if stock.get("recommendation") == "buy" and price > 400:
            return RuleResult(
                "amount_threshold", False,
                f"High-price buy recommendation: {stock.get('symbol')} at {price}",
                "warning",
            )
    return RuleResult("amount_threshold", True, "Values within thresholds")


def rule_time_window(output: dict, context: dict) -> RuleResult:
    """Check if action is within allowed time window."""
    # Placeholder: always passes in MVP. In production, check market hours, etc.
    return RuleResult("time_window", True, "Within allowed time window")


def rule_missing_evidence(output: dict, context: dict) -> RuleResult:
    """Check if the output has sufficient evidence/data."""
    if not output:
        return RuleResult("missing_evidence", False, "Output is empty", "critical")
    if output.get("mock") or output.get("mock_fallback"):
        return RuleResult("missing_evidence", False, "Output from mock agent — no real evidence", "warning")
    required_fields = context.get("required_evidence_fields", [])
    missing = [f for f in required_fields if f not in output]
    if missing:
        return RuleResult("missing_evidence", False, f"Missing evidence fields: {missing}", "warning")
    return RuleResult("missing_evidence", True, "Evidence present")


def rule_conflicting_data(output: dict, context: dict) -> RuleResult:
    """Check for internal contradictions in the output."""
    stocks = output.get("stocks", [])
    if stocks:
        sentiments = set()
        for s in stocks:
            rec = s.get("recommendation", "")
            conf = s.get("confidence", 0)
            if conf > 0.8:
                sentiments.add(rec)
        if "buy" in sentiments and "sell" in sentiments:
            return RuleResult(
                "conflicting_data", False,
                "Conflicting high-confidence recommendations: both buy and sell",
                "warning",
            )
    risk_score = output.get("risk_score", 0)
    risk_level = output.get("risk_level", "")
    if risk_score > 0.7 and risk_level == "low":
        return RuleResult("conflicting_data", False, "Risk score high but level marked low", "warning")
    return RuleResult("conflicting_data", True, "No conflicting data detected")


ALL_RULES = [
    rule_whitelist_check,
    rule_blacklist_check,
    rule_amount_threshold,
    rule_time_window,
    rule_missing_evidence,
    rule_conflicting_data,
]


def run_rules(output: dict, context: dict) -> tuple[str, list[dict]]:
    """Run all rules and return aggregate result + details."""
    results = []
    has_critical = False
    has_warning = False

    for rule_fn in ALL_RULES:
        r = rule_fn(output, context)
        results.append({
            "rule": r.rule_name,
            "passed": r.passed,
            "reason": r.reason,
            "severity": r.severity,
        })
        if not r.passed:
            if r.severity == "critical":
                has_critical = True
            elif r.severity == "warning":
                has_warning = True

    if has_critical:
        aggregate = "fail"
    elif has_warning:
        aggregate = "warning"
    else:
        aggregate = "pass"

    return aggregate, results


# ===========================================================================
# Stage 2 — Weighted Risk Scorer (MVP, no LLM)
# ===========================================================================

RISK_WEIGHTS = {
    "mock_output": 15,
    "high_value": 20,
    "high_risk_score": 25,
    "conflicting_recommendations": 20,
    "unknown_agent": 15,
    "missing_data": 10,
    "blacklisted_content": 40,
}


def compute_risk_score(output: dict, context: dict, rule_results: list[dict]) -> tuple[int, str, list[dict]]:
    """
    Compute weighted risk score 0-100.
    Returns (score, level, factors).
    """
    score = 0
    factors = []

    # Mock output penalty
    if output.get("mock") or output.get("mock_fallback"):
        score += RISK_WEIGHTS["mock_output"]
        factors.append({"factor": "mock_output", "points": RISK_WEIGHTS["mock_output"], "detail": "Output from mock agent"})

    # High value
    total_value = output.get("total_value", 0)
    if isinstance(total_value, (int, float)) and total_value > 5_000_000:
        points = min(RISK_WEIGHTS["high_value"], int(total_value / 1_000_000))
        score += points
        factors.append({"factor": "high_value", "points": points, "detail": f"Value: {total_value:,.0f}"})

    # High risk score from agent
    agent_risk = output.get("risk_score", 0)
    if isinstance(agent_risk, (int, float)) and agent_risk > 0.5:
        points = int(agent_risk * RISK_WEIGHTS["high_risk_score"])
        score += points
        factors.append({"factor": "high_risk_score", "points": points, "detail": f"Agent risk: {agent_risk}"})

    # Conflicting recommendations
    stocks = output.get("stocks", [])
    high_conf_recs = set()
    for s in stocks:
        if s.get("confidence", 0) > 0.8:
            high_conf_recs.add(s.get("recommendation"))
    if "buy" in high_conf_recs and "sell" in high_conf_recs:
        score += RISK_WEIGHTS["conflicting_recommendations"]
        factors.append({"factor": "conflicting_recs", "points": RISK_WEIGHTS["conflicting_recommendations"], "detail": "Buy and sell at high confidence"})

    # Unknown agent
    whitelisted = context.get("whitelisted_agents", ["mock-asset-agent", "mock-stock-agent", "mock-realty-agent"])
    if context.get("agent_id", "") not in whitelisted:
        score += RISK_WEIGHTS["unknown_agent"]
        factors.append({"factor": "unknown_agent", "points": RISK_WEIGHTS["unknown_agent"], "detail": f"Agent not whitelisted"})

    # Rule failures add points
    for r in rule_results:
        if not r["passed"] and r["severity"] == "critical":
            score += RISK_WEIGHTS["blacklisted_content"]
            factors.append({"factor": f"rule_fail_{r['rule']}", "points": RISK_WEIGHTS["blacklisted_content"], "detail": r["reason"]})

    score = min(score, 100)

    if score >= 70:
        level = "critical"
    elif score >= 45:
        level = "high"
    elif score >= 20:
        level = "medium"
    else:
        level = "low"

    return score, level, factors


# ===========================================================================
# Decision Logic
# ===========================================================================

def make_decision(
    output: dict[str, Any],
    context: dict[str, Any],
) -> dict:
    """
    Full judgement pipeline:
    1. Run deterministic rules
    2. Compute risk score
    3. Make decision
    """
    rule_aggregate, rule_details = run_rules(output, context)
    risk_score, risk_level, risk_factors = compute_risk_score(output, context, rule_details)

    # Decision logic
    if rule_aggregate == "fail" or risk_score >= 70:
        decision = "rejected"
    elif risk_score >= 45 or rule_aggregate == "warning":
        decision = "human_review_required"
    elif risk_score >= 20:
        decision = "conditional_approve"
    else:
        decision = "auto_approve"

    reasoning_parts = []
    if rule_aggregate != "pass":
        failed_rules = [r["rule"] for r in rule_details if not r["passed"]]
        reasoning_parts.append(f"Rules flagged: {', '.join(failed_rules)}")
    reasoning_parts.append(f"Risk score: {risk_score}/100 ({risk_level})")
    if risk_factors:
        top_factor = max(risk_factors, key=lambda f: f["points"])
        reasoning_parts.append(f"Top risk factor: {top_factor['factor']} (+{top_factor['points']})")

    log.info(
        f"judgement: {decision} (risk={risk_score}, rules={rule_aggregate})",
        extra={"action": "judgement.decision"},
    )

    return {
        "rule_result": rule_aggregate,
        "rule_details": rule_details,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "risk_factors": risk_factors,
        "decision": decision,
        "reasoning": " | ".join(reasoning_parts),
    }
