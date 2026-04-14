"""
Generates JSON Schema files and sample JSON files from Pydantic models.
Run: python -m contracts.generate_schemas
"""

import json
from pathlib import Path

from contracts.task import TaskRequest, TaskResponse
from contracts.a2a import A2AMessageEnvelope
from contracts.judgement import JudgementRequest, JudgementResult
from contracts.report import ReportDraft, FinalReport
from contracts.telegram import TelegramActionPayload
from contracts.ai_glass import AIGlassCaptureEvent

CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "contracts"
SAMPLES_DIR = CONTRACTS_DIR / "samples"

MODELS = {
    "task-request": TaskRequest,
    "task-response": TaskResponse,
    "a2a-message-envelope": A2AMessageEnvelope,
    "judgement-request": JudgementRequest,
    "judgement-result": JudgementResult,
    "report-draft": ReportDraft,
    "final-report": FinalReport,
    "telegram-action-payload": TelegramActionPayload,
    "ai-glass-capture-event": AIGlassCaptureEvent,
}

SAMPLES = {
    "task-request": {
        "trace_id": "tr-20260413-001",
        "initiator_type": "user",
        "initiator_id": "user-001",
        "source_channel": "web",
        "target_agent_type": "asset",
        "task_type": "asset_summary",
        "priority": "medium",
        "input_payload": {"portfolio_id": "PF-1234"},
        "metadata": {"source": "dashboard"},
    },
    "task-response": {
        "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "trace_id": "tr-20260413-001",
        "agent_id": "mock-asset-agent",
        "status": "completed",
        "summary": "Portfolio PF-1234 summary generated",
        "output_payload": {"total_value": 1250000, "currency": "KRW", "change_pct": 2.3},
        "evidence_refs": ["s3://reports/2026/04/13/PF-1234.pdf"],
    },
    "a2a-message-envelope": {
        "trace_id": "tr-20260413-001",
        "sender_agent_id": "mock-asset-agent",
        "target_agent_id": "mock-stock-agent",
        "message_type": "request",
        "purpose": "delegate",
        "proof_of_intent": {"reason": "Need stock data to complete portfolio analysis"},
        "payload": {"symbols": ["AAPL", "GOOGL", "005930.KS"]},
    },
    "judgement-request": {
        "trace_id": "tr-20260413-001",
        "task_run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "task_type": "stock_analysis",
        "agent_id": "mock-stock-agent",
        "agent_output": {"recommendation": "buy", "confidence": 0.85, "symbol": "AAPL"},
        "rules": ["max_risk_threshold", "compliance_check"],
    },
    "judgement-result": {
        "judgement_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
        "trace_id": "tr-20260413-001",
        "task_run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "rule_result": "pass",
        "model_result": "low_risk",
        "risk_score": 0.15,
        "risk_level": "low",
        "decision": "approve",
        "reasoning": "Stock recommendation within risk tolerance, no compliance flags",
    },
    "report-draft": {
        "trace_id": "tr-20260413-001",
        "report_type": "daily_summary",
        "title": "Daily Portfolio Summary — 2026-04-13",
        "sections": [
            {"title": "Overview", "content": "Portfolio value increased by 2.3% today."},
            {"title": "Top Movers", "content": "AAPL +5.1%, TSLA -2.0%, 005930.KS +1.2%"},
            {"title": "Risk Assessment", "content": "All positions within tolerance."},
        ],
    },
    "final-report": {
        "trace_id": "tr-20260413-001",
        "report_type": "daily_summary",
        "title": "Daily Portfolio Summary — 2026-04-13",
        "sections": [
            {"title": "Overview", "content": "Portfolio value increased by 2.3% today."},
            {"title": "Top Movers", "content": "AAPL +5.1%, TSLA -2.0%"},
        ],
        "delivery_channels": ["web", "telegram"],
        "recipient_ids": ["user-001"],
        "approved": True,
    },
    "telegram-action-payload": {
        "telegram_user_id": "123456789",
        "chat_id": "-100987654321",
        "action_type": "command",
        "command": "/status",
        "args": ["portfolio", "PF-1234"],
    },
    "ai-glass-capture-event": {
        "trace_id": "tr-20260413-glass-001",
        "agent_id": "mock-realty-agent",
        "device_id": "glass-device-A1",
        "capture_type": "spatial_3d",
        "property_ref": "PROP-2026-0413",
        "location": {"latitude": 37.5665, "longitude": 126.9780},
        "processing_status": "initiated",
        "metadata": {"fps": 30, "resolution": "4K", "stereo": True},
    },
}


def main():
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    for name, model_cls in MODELS.items():
        # JSON Schema
        schema_dir = CONTRACTS_DIR / name.replace("-", "/").rsplit("/", 1)[0] if "/" in name.replace("-", "/") else CONTRACTS_DIR / name
        schema_dir.mkdir(parents=True, exist_ok=True)
        schema_path = schema_dir / "schema.json"
        schema = model_cls.model_json_schema()
        schema_path.write_text(json.dumps(schema, indent=2, default=str))
        print(f"  + Schema: {schema_path.relative_to(CONTRACTS_DIR)}")

        # Sample JSON
        sample_path = SAMPLES_DIR / f"{name}.sample.json"
        sample_path.write_text(json.dumps(SAMPLES[name], indent=2, default=str))
        print(f"  + Sample: {sample_path.relative_to(CONTRACTS_DIR)}")

    print(f"\nGenerated {len(MODELS)} schemas and {len(SAMPLES)} samples.")


if __name__ == "__main__":
    main()
