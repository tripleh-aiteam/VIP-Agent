# VIP Agent Platform — Chatbot Architecture

## Two Modes

### Structured Mode (Default)
- Deterministic, rule-based intent classification
- Pattern matching with 30+ regex rules
- Best for strict commands and approvals
- No external API calls

### AI Assist Mode
- OpenAI-assisted interpretation and explanation
- Better natural language understanding
- Enhanced report explanations and summaries
- Falls back to rules for deterministic intents

## Processing Pipeline

```
User Input
    │
    ▼
┌──────────────┐
│ Input Channel │  (web, telegram, api)
│    Layer      │
└──────┬───────┘
       │
       ▼
┌──────────────────┐
│ Interpretation    │  ← Mode-dependent
│    Layer          │
│                   │  Structured: RuleBasedInterpreter
│                   │  AI Assist:  OpenAIInterpreter
└──────┬───────────┘
       │  IntentResult (intent + confidence + entities)
       ▼
┌──────────────────┐
│ Action Planning   │  ← Always deterministic
│    Layer          │
│                   │  Maps intent → handler function
│                   │  Same for both modes
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ Action Execution  │  ← Always deterministic
│    Layer          │
│                   │  Goes through Orchestrator
│                   │  Writes to DB via services
│                   │  Audit trail logged
└──────┬───────────┘
       │  Raw response (data + text)
       ▼
┌──────────────────┐
│ Response Rendering│  ← Mode-dependent
│    Layer          │
│                   │  Structured: StandardResponseFormatter
│                   │  AI Assist:  AIResponseFormatter
└──────┬───────────┘
       │
       ▼
   Final Response

```

## Safety: What OpenAI Can and Cannot Do

### OpenAI CAN:
- Interpret user intent more flexibly
- Extract entities from natural language
- Rewrite system output into clearer explanations
- Summarize reports in simpler words
- Help understand complex system state

### OpenAI CANNOT:
- Write to the database directly
- Approve or reject judgement cases
- Trigger workflows without going through orchestrator
- Bypass the judgement service
- Skip audit logging
- Make state-changing decisions

## Deterministic Intents (Always Rule-Based)

These intents use rule-based execution regardless of mode:
- `approval_action` — approve/reject cases
- `workflow_trigger` — run tasks and reports
- `cross_agent_analysis` — multi-agent workflows

## AI-Safe Intents

These intents can safely use AI assistance:
- `system_status` — explanation only
- `agent_inspection` — explanation only
- `report_request` — viewing/explaining reports
- `report_explainer` — follow-up questions
- `judgement_explanation` — explaining decisions
- `a2a_inspection` — viewing messages
- `aiglass_inspection` — viewing sessions

## Configuration

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
CHAT_DEFAULT_MODE=structured
AI_ASSIST_ENABLED=true
```
