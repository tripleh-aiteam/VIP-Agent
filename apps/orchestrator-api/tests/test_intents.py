"""
VIP AI Platform — Intent Classification Tests
Tests all 9 intent categories with multiple phrase variations.
"""

from services.intent_service import classify


def test_system_status():
    assert classify("/status").intent == "system_status"
    assert classify("status").intent == "system_status"
    assert classify("show me system status").intent == "system_status"
    assert classify("how is the system").intent == "system_status"
    assert classify("health check").intent == "system_status"
    assert classify("is the system online").intent == "system_status"


def test_agent_inspection():
    assert classify("/agents").intent == "agent_inspection"
    assert classify("list agents").intent == "agent_inspection"
    assert classify("show all agents").intent == "agent_inspection"
    assert classify("which agents are failing").intent == "agent_inspection"
    assert classify("agents status").intent == "agent_inspection"
    assert classify("agent health").intent == "agent_inspection"


def test_workflow_trigger():
    assert classify("run asset summary").intent == "workflow_trigger"
    assert classify("run stock analysis").intent == "workflow_trigger"
    assert classify("run realty listing").intent == "workflow_trigger"
    assert classify("trigger asset").intent == "workflow_trigger"
    assert classify("run daily").intent == "workflow_trigger"
    assert classify("/run_daily").intent == "workflow_trigger"
    assert classify("generate report").intent == "workflow_trigger"


def test_workflow_entities():
    r = classify("run asset summary")
    assert r.entities.get("agent_type") == "asset"
    assert r.entities.get("task_type") == "asset_summary"

    r = classify("run stock analysis")
    assert r.entities.get("agent_type") == "stock"

    r = classify("run property listing")
    assert r.entities.get("agent_type") == "realty"


def test_report_request():
    assert classify("/report").intent == "report_request"
    assert classify("show latest report").intent == "report_request"
    assert classify("daily report").intent == "report_request"
    assert classify("weekly report").intent == "report_request"
    assert classify("executive summary").intent == "report_request"


def test_report_entities():
    r = classify("show daily report")
    assert r.entities.get("report_type") == "daily_summary"

    r = classify("weekly report please")
    assert r.entities.get("report_type") == "weekly_summary"


def test_approval_action():
    assert classify("/approvals").intent == "approval_action"
    assert classify("pending approvals").intent == "approval_action"
    assert classify("approve case abc12345").intent == "approval_action"
    assert classify("reject case abc12345").intent == "approval_action"
    assert classify("what needs approval").intent == "approval_action"


def test_approval_entities():
    r = classify("approve case abc12345")
    assert r.entities.get("action") == "approve"

    r = classify("reject this")
    assert r.entities.get("action") == "reject"


def test_judgement_explanation():
    assert classify("why was this rejected").intent == "judgement_explanation"
    assert classify("explain the judgement").intent == "judgement_explanation"
    assert classify("why did it fail").intent == "judgement_explanation"
    assert classify("explain the risk score").intent == "judgement_explanation"


def test_a2a_inspection():
    assert classify("show a2a messages").intent == "a2a_inspection"
    assert classify("agent to agent messages").intent == "a2a_inspection"
    assert classify("recent a2a").intent == "a2a_inspection"
    assert classify("what are agents communicating").intent == "a2a_inspection"


def test_aiglass_inspection():
    assert classify("show AI Glass sessions").intent == "aiglass_inspection"
    assert classify("glass device status").intent == "aiglass_inspection"
    assert classify("capture sessions").intent == "aiglass_inspection"
    assert classify("spatial capture").intent == "aiglass_inspection"


def test_help():
    assert classify("/help").intent == "help"
    assert classify("help").intent == "help"
    assert classify("what can you do").intent == "help"


def test_unknown():
    r = classify("tell me a joke")
    assert r.intent == "unknown"
    assert r.confidence < 0.5


def test_confidence_ordering():
    exact = classify("/status")
    phrase = classify("show me system status")
    assert exact.confidence >= phrase.confidence


def test_uuid_entity_extraction():
    r = classify("approve case a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    assert r.entities.get("case_id") == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
