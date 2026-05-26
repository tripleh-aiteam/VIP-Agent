# Backend starter — Notion-AI-style Assistant for any agent

Drop this folder into a new agent's FastAPI backend (Asset / Stock / Realty / Helmet / Health / …) and you get the same Notion-AI Assistant the VIP boss uses, branded for that agent.

## What the assistant gives you

- Natural-language Q&A — users phrase requests any way they want.
- Tool-calling — the LLM picks from your tool catalog, executes, returns a structured result.
- Multi-step chains — "find X and then do Y" decomposes automatically.
- Confirm-before-write — destructive tools (send message, delete, etc.) return a preview; user clicks Confirm to actually run.
- Multimodal — drag-drop images/PDFs, paste screenshots → Gemini / Claude / OpenAI vision.
- Auto free-LLM fallback — if your paid API runs out, automatically routes to free Groq + Gemini Flash so the assistant never goes silent.
- Per-request model override — overlay's dropdown lets the user pin a specific LLM.

## What you must provide

| File | Purpose |
|---|---|
| `assistant_manifest.py` | List of your agent's pages, sub-tabs, external apps, and the `AGENT_IDENTITY` brand strings. Copy `assistant_manifest.template.py` and edit. |
| `assistant_tools.py` | Your tool catalog — every capability the assistant can call (navigate, search_X, send_Y, etc.). Copy `assistant_tools.template.py` and edit. |
| Wire `/chat/agent` + `/chatbot/upload` + `/twins/llm/models` routers | Copy the four endpoints below verbatim. |

The generic `assistant_agent.py` and `llm_client.py` stay AS-IS — they read your manifest + tools at runtime.

## Required endpoints

```python
# routers/chat.py — copy verbatim
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session
from db.base import get_db

router = APIRouter(prefix="/chat", tags=["chat"])

class AgentCommandBody(BaseModel):
    transcript: str = Field("")
    language: Optional[str] = Field("auto")
    current_path: Optional[str] = Field(None)
    selected_id: Optional[str] = Field(None)
    history: Optional[list[dict]] = Field(None)
    confirmed_tool: Optional[str] = Field(None)
    confirmed_args: Optional[dict] = Field(None)
    attachment_ids: Optional[list[str]] = Field(None)
    model: Optional[str] = Field(None)

@router.post("/agent")
def agent_command(body: AgentCommandBody, db: Session = Depends(get_db)):
    from services.assistant_agent import run_agent
    return run_agent(
        db,
        transcript=body.transcript or "",
        language=body.language or "auto",
        current_path=body.current_path,
        selected_id=body.selected_id,
        history=body.history,
        confirmed_tool=body.confirmed_tool,
        confirmed_args=body.confirmed_args,
        attachment_ids=body.attachment_ids,
        forced_model=body.model,
    )
```

```python
# routers/chatbot.py — copy the /upload handler from VIP's orchestrator
# (see apps/orchestrator-api/routers/chatbot.py in the monorepo)
```

```python
# routers/twins.py — copy the /llm/models handler
@router.get("/llm/models")
def list_llm_models():
    from services.llm_client import list_available_models
    return {"models": list_available_models()}
```

## Environment variables

```bash
# Brand strings (defaults to "VIP Agent Assistant" if unset)
ASSISTANT_AGENT_NAME="Asset Agent Assistant"
ASSISTANT_AGENT_TAGLINE="your AI co-pilot for the real-estate portfolio"
ASSISTANT_AGENT_SCOPE="You can search properties, check occupancy, generate rent reports, ..."

# At least one LLM provider key (any combination works — auto-fallback handles failures)
GEMINI_API_KEY=...     # free tier
GROQ_API_KEY=...       # free tier (fastest)
ANTHROPIC_API_KEY=...  # paid
OPENAI_API_KEY=...     # paid
```

## Frontend wiring

```tsx
import { ChatbotOverlay } from "@triple-h/chatbot";
import { AgentConfig } from "@triple-h/chatbot";

const config: AgentConfig = {
  agentId: "asset",
  apiBase: "https://asset-agent.onrender.com",
  endpointMode: "agent",           // ← turns on the tool-calling backend
  identity: {
    name: "Asset Assistant",
    greeting: { en: "Hi! Ask me about properties, rentals, occupancy.", ko: "안녕하세요!" },
    wakeWords: { en: ["hey asset"], ko: ["자산"] },
    tone: "formal",
  },
  intents: [],                     // ← unused in agent mode; tools come from backend
  knowledge: [],
};

<ChatbotOverlay config={config} onAction={(a) => router.push(a.to!)} />
```

## Done. That's the whole drop-in.

The boss now has the same Notion-AI Assistant inside the Asset agent UI, scoped to Asset's data and tools.
