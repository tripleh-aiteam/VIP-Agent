"""
VIP AI Platform — Contract Validation Endpoints
Test endpoints for validating sample payloads against all 9 contracts.
"""

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from contracts.task import TaskRequest, TaskResponse
from contracts.a2a import A2AMessageEnvelope
from contracts.judgement import JudgementRequest, JudgementResult
from contracts.report import ReportDraft, FinalReport
from contracts.telegram import TelegramActionPayload
from contracts.ai_glass import AIGlassCaptureEvent

router = APIRouter(prefix="/contracts", tags=["contracts"])

ALL_CONTRACTS = {
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


@router.get("/")
async def list_contracts():
    """List all available contracts with their field counts."""
    return [
        {
            "name": name,
            "fields": list(model.model_fields.keys()),
            "field_count": len(model.model_fields),
            "validate_url": f"/contracts/validate/{name}",
            "schema_url": f"/contracts/schema/{name}",
        }
        for name, model in ALL_CONTRACTS.items()
    ]


@router.get("/schema/{contract_name}")
async def get_schema(contract_name: str):
    """Get the JSON Schema for a specific contract."""
    model = ALL_CONTRACTS.get(contract_name)
    if not model:
        raise HTTPException(404, f"Contract '{contract_name}' not found. Available: {list(ALL_CONTRACTS.keys())}")
    return model.model_json_schema()


@router.post("/validate/{contract_name}")
async def validate_payload(contract_name: str, payload: dict):
    """Validate a JSON payload against a specific contract. Returns validation result."""
    model = ALL_CONTRACTS.get(contract_name)
    if not model:
        raise HTTPException(404, f"Contract '{contract_name}' not found. Available: {list(ALL_CONTRACTS.keys())}")

    try:
        instance = model(**payload)
        return {
            "valid": True,
            "contract": contract_name,
            "parsed": instance.model_dump(mode="json"),
        }
    except ValidationError as e:
        return {
            "valid": False,
            "contract": contract_name,
            "errors": e.errors(),
            "error_count": e.error_count(),
        }


# --- Typed endpoints for OpenAPI doc generation ---

@router.post("/validate/task-request", response_model=dict, summary="Validate TaskRequest")
async def validate_task_request(payload: TaskRequest):
    """Validate and echo a TaskRequest payload."""
    return {"valid": True, "contract": "task-request", "parsed": payload.model_dump(mode="json")}


@router.post("/validate/task-response", response_model=dict, summary="Validate TaskResponse")
async def validate_task_response(payload: TaskResponse):
    """Validate and echo a TaskResponse payload."""
    return {"valid": True, "contract": "task-response", "parsed": payload.model_dump(mode="json")}


@router.post("/validate/a2a-message-envelope", response_model=dict, summary="Validate A2AMessageEnvelope")
async def validate_a2a(payload: A2AMessageEnvelope):
    """Validate and echo an A2AMessageEnvelope payload."""
    return {"valid": True, "contract": "a2a-message-envelope", "parsed": payload.model_dump(mode="json")}


@router.post("/validate/judgement-request", response_model=dict, summary="Validate JudgementRequest")
async def validate_judgement_request(payload: JudgementRequest):
    """Validate and echo a JudgementRequest payload."""
    return {"valid": True, "contract": "judgement-request", "parsed": payload.model_dump(mode="json")}


@router.post("/validate/judgement-result", response_model=dict, summary="Validate JudgementResult")
async def validate_judgement_result(payload: JudgementResult):
    """Validate and echo a JudgementResult payload."""
    return {"valid": True, "contract": "judgement-result", "parsed": payload.model_dump(mode="json")}


@router.post("/validate/report-draft", response_model=dict, summary="Validate ReportDraft")
async def validate_report_draft(payload: ReportDraft):
    """Validate and echo a ReportDraft payload."""
    return {"valid": True, "contract": "report-draft", "parsed": payload.model_dump(mode="json")}


@router.post("/validate/final-report", response_model=dict, summary="Validate FinalReport")
async def validate_final_report(payload: FinalReport):
    """Validate and echo a FinalReport payload."""
    return {"valid": True, "contract": "final-report", "parsed": payload.model_dump(mode="json")}


@router.post("/validate/telegram-action-payload", response_model=dict, summary="Validate TelegramActionPayload")
async def validate_telegram(payload: TelegramActionPayload):
    """Validate and echo a TelegramActionPayload."""
    return {"valid": True, "contract": "telegram-action-payload", "parsed": payload.model_dump(mode="json")}


@router.post("/validate/ai-glass-capture-event", response_model=dict, summary="Validate AIGlassCaptureEvent")
async def validate_ai_glass(payload: AIGlassCaptureEvent):
    """Validate and echo an AIGlassCaptureEvent payload."""
    return {"valid": True, "contract": "ai-glass-capture-event", "parsed": payload.model_dump(mode="json")}
