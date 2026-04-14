# VIP Agent Platform — Future LLM Upgrade Guide

## Current State (MVP)

- **Structured Mode**: Rule-based, 30+ regex patterns, deterministic
- **AI Assist Mode**: OpenAI gpt-4o-mini for interpretation + formatting

## Upgrade Path

### 1. Replace OpenAI Model

Change in `.env`:
```env
OPENAI_MODEL=gpt-4o           # or gpt-4-turbo, claude-3-opus, etc.
```

No code changes needed — the interpreter and formatter use `OPENAI_MODEL` env var.

### 2. Switch to Anthropic Claude

In `services/interpreters.py`, replace the HTTP call in `OpenAIInterpreter.interpret()`:

```python
# Change from:
resp = client.post("https://api.openai.com/v1/chat/completions", ...)

# To:
resp = client.post("https://api.anthropic.com/v1/messages", ...)
```

Same for `services/formatters.py`.

Add env var:
```env
ANTHROPIC_API_KEY=sk-ant-...
LLM_PROVIDER=anthropic  # new config
```

### 3. Add Custom Fine-Tuned Model

Create a new interpreter class:

```python
class CustomLLMInterpreter:
    def interpret(self, text: str) -> IntentResult:
        # Call your fine-tuned model
        # Return same IntentResult interface
        pass
```

Register in `get_interpreter()`:
```python
def get_interpreter(mode: str):
    if mode == "custom_llm":
        return CustomLLMInterpreter()
    ...
```

### 4. Add Autonomous Planning (Future)

Currently, cross-agent workflows use deterministic mapping. To add AI planning:

```python
class AIActionPlanner:
    def plan(self, intent_result, context) -> list[ActionStep]:
        # Use LLM to decide which agents to involve
        # But EVERY step still executes through deterministic handlers
        pass
```

### 5. Add Memory / Context Window

Current session memory is limited to last 10 messages for report context. To upgrade:

- Store embedding vectors for messages
- Use RAG for retrieving relevant context
- Add `context_window` field to chat sessions

### 6. Add Multi-Modal Input

Current input is text-only. To add:
- Image analysis (AI Glass photos)
- Voice commands (Telegram voice messages)
- Document analysis (uploaded reports)

## Architecture Constraints (Do NOT Change)

1. **Action execution must remain deterministic** — LLM suggests, backend executes
2. **Audit trail must be preserved** — every action logged regardless of mode
3. **Orchestrator is the brain** — chatbot is only the interface
4. **Judgement service cannot be bypassed** — sensitive actions always go through risk evaluation
5. **IntentResult interface must be maintained** — all interpreters return the same structure

## Testing After Upgrade

1. Run existing structured mode tests — must still pass
2. Test AI mode with new model
3. Verify deterministic intents still use rules
4. Check audit logs contain mode metadata
5. Verify no direct DB writes from LLM layer
