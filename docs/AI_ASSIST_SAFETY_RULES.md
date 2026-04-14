# VIP Agent Platform — AI Assist Safety Rules

## Core Principle

OpenAI enhances **understanding and explanation**. It never **executes or decides**.

## Intents That MUST Remain Deterministic

These always use rule-based execution, regardless of chat mode:

| Intent | Why | Risk if AI-controlled |
|--------|-----|----------------------|
| `approval_action` | Approves/rejects judgement cases | Could approve dangerous transactions |
| `workflow_trigger` | Creates and dispatches tasks | Could trigger unintended workflows |
| `cross_agent_analysis` | Multi-agent orchestration | Could coordinate unauthorized operations |

**Even in AI Assist mode, these intents use `RuleBasedInterpreter` when confidence >= 0.90.**

## Intents That Can Safely Use AI Assistance

| Intent | AI Can Do | AI Cannot Do |
|--------|-----------|-------------|
| `system_status` | Summarize in natural language | Change system state |
| `agent_inspection` | Explain agent health | Modify agent config |
| `report_request` | Explain report content | Generate fake data |
| `report_explainer` | Answer questions about reports | Invent facts not in report |
| `judgement_explanation` | Explain why case was rejected | Change the decision |
| `a2a_inspection` | Summarize communications | Send messages |
| `aiglass_inspection` | Describe session status | Modify processing |

## What OpenAI CAN Do

1. **Interpret** user intent more flexibly ("can you check if anything looks dangerous" → `report_explainer/risk`)
2. **Extract entities** from natural language ("approve the one with high risk" → case_id extraction)
3. **Rewrite** system output into clearer operator language
4. **Summarize** complex data (reports, multi-section outputs)
5. **Explain** decisions, risk scores, and rule failures in plain English

## What OpenAI CANNOT Do

1. Write to the database directly
2. Approve or reject judgement cases
3. Create or dispatch tasks
4. Send A2A messages
5. Modify agent registry
6. Change schedule rules
7. Bypass the orchestrator
8. Bypass the judgement service
9. Skip audit logging
10. Make up data not present in platform records

## Data Flow

```
User Input → OpenAI Interpreter (classify intent + extract entities)
          → Deterministic Action Handler (same as structured mode)
          → Raw Response (grounded in real data)
          → OpenAI Formatter (rewrite text only, data untouched)
          → Final Response (natural language + real data)
```

## Audit Trail

Both modes produce identical audit records:
- `chat.message_received` with intent classification
- `chat.response_sent` with response type
- All action-specific audit events (task.created, judgement.approved, etc.)

The `mode` field is stored in every user message for traceability.

## Configuration

```env
AI_ASSIST_ENABLED=true    # Master switch — set false to disable AI mode entirely
OPENAI_API_KEY=sk-...     # Required for AI Assist mode
OPENAI_MODEL=gpt-4o-mini  # Model for interpretation and formatting
```

If `AI_ASSIST_ENABLED=false` or no API key, AI Assist mode silently falls back to Structured mode.
