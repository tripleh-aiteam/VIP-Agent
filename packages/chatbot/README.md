# @triple-h/chatbot

Drop-in Notion-AI-style assistant module for multi-agent platforms. Bring your own backend manifest + tool catalog; the package handles the UI, voice, drag-drop, multimodal upload, and LLM router.

## v1.3 capabilities

| Pillar | Status | Notes |
|---|---|---|
| **TALK** — Q&A | ✅ v0.1 | Tool-calling LLM picks the right capability for any natural phrasing |
| **ACTION** — execute | ✅ v0.2 | Multi-step chains, Confirm-before-write cards |
| **PERCEPTION** — files/images | ✅ v0.2 | Drag-drop, clipboard paste, multipart upload, Gemini/Claude/OpenAI vision |
| **PROACTIVE** — alerts | 🔜 v0.3 | WebSocket-pushed alerts spoken automatically |
| **VOICE / CALLING** | ✅ v1.2 | Inbound AI receptionist + outbound campaigns (separate subpath) |
| **MODEL PICKER** | ✅ v1.3 | Notion-style LLM dropdown in the overlay header |
| **AUTO FREE FALLBACK** | ✅ v1.3 | Paid LLM 429s → auto-cascade to free Groq + Gemini Flash |

## Install

Workspace-local (file: dep) — the package lives in `packages/chatbot/` of the monorepo. Each consuming agent's `tsconfig.json` adds:

```json
"paths": { "@triple-h/chatbot": ["../../packages/chatbot/src"] }
```

And `next.config.js` adds:
```js
transpilePackages: ["@triple-h/chatbot"]
```

## Use it in 3 lines

```tsx
import { ChatbotOverlay } from "@triple-h/chatbot";
import { myConfig } from "./chatbot.config";

<ChatbotOverlay config={myConfig} onAction={(a) => router.push(a.to!)} />
```

## Modern (Notion-AI-style) configuration

```ts
import type { AgentConfig } from "@triple-h/chatbot";

export const myConfig: AgentConfig = {
  agentId: "my-agent",
  apiBase: "https://my-agent.onrender.com",
  endpointMode: "agent",   // ← turns on tool-calling backend (v1.3 default for new agents)
  identity: {
    name: "My Assistant",
    greeting: {
      en: "Hi! I can navigate the app, search records, send messages, and answer questions.",
      ko: "안녕하세요! 페이지 이동, 검색, 메시지 전송, 질문 답변을 도와드릴 수 있어요.",
    },
    wakeWords: { en: ["hey assistant"], ko: ["어시스턴트"] },
    tone: "formal",
  },
  intents: [],   // ← unused when endpointMode="agent" — your backend tool catalog wins
  knowledge: [], // ← also unused in agent mode
  theme: { primaryColor: "#3B82F6", radius: "lg", panelWidth: 480, panelHeight: 640 },
};
```

## Two endpoint modes

```
endpointMode: "talk"   →  POST /chatbot/talk    (legacy keyword classifier)
endpointMode: "agent"  →  POST /chat/agent      (Notion-AI tool-calling)
```

**New agents should use `"agent"`.** It handles natural phrasing, deeply nested menus, multi-step chains, and write-action confirmation cards without any frontend intent definitions.

## Backend — what your agent needs

See `examples/backend-starter/README.md` for the full drop-in. Summary:

| Endpoint | Why |
|---|---|
| `POST /chat/agent` | Main tool-calling endpoint. Wires up to a generic `run_agent()` that reads your manifest + tools |
| `POST /chatbot/upload` | Multipart upload for images/PDFs/audio/video → returns `attachment_id` for the next /chat/agent call |
| `GET  /twins/llm/models` | Lists every LLM provider configured on the backend — powers the model dropdown in the overlay |
| `POST /chatbot/transcribe` | Optional — audio blob → text (Whisper + Gemini fallback) |

You also provide:
- `assistant_manifest.py` — pages, sub-tabs, external agents, `AGENT_IDENTITY` brand strings
- `assistant_tools.py` — your tool catalog (read + write tools with confirmation flags)

Both have copyable templates in `examples/backend-starter/`.

## How the agent picks tools

1. User sends a message (typed, transcribed, or with attached files).
2. Backend builds a system prompt with: your `AGENT_IDENTITY`, the full tool catalog, the manifest (pages + sub-tabs + external agents), and live page context (`current_path`, `selected_id`, last-6-turn history).
3. The LLM responds with one of three shapes:
   - `{tool, args}` — pick ONE tool
   - `{steps: [...]}` — chain multiple tools (max 6)
   - `{answer}` — direct answer, no tool needed
4. Read tools execute immediately; write tools return `proposed_action` for user Confirm.
5. Multi-step chains feed each tool's result to the LLM for a final composed reply.

## Multimodal flow

```
User drops file on the overlay
   ↓
ChatbotOverlay.uploadAttachment() → POST /chatbot/upload (multipart)
   ↓
Backend stores at uploads/chatbot/<uuid>.<ext>, returns attachment_id
   ↓
Next ask() includes attachment_ids in body
   ↓
assistant_agent._run_multimodal_path() short-circuits to Gemini 2.5 Pro
   ↓
Cascade on failure: Gemini → Claude Haiku vision → OpenAI gpt-4o vision
```

20 MB cap, 24 h TTL, image/PDF/text/audio/video allowed.

## Auto free-LLM fallback

If your paid LLM (Claude / OpenAI) is out of credits / rate-limited, the backend's `chat_completion_sync` automatically cascades through free providers before giving up:

```
1. Requested model (Claude / Gemini Pro / GPT-4o / etc.)
2. Groq Llama 3.3 70B       — free 30 RPM, no credit card
3. Gemini 2.5 Flash         — free 15 RPM
4. OpenAI gpt-4o-mini       — only fires if you've topped up credits
5. Local Ollama qwen2.5     — dev boxes only
```

Set `GROQ_API_KEY` and `GEMINI_API_KEY` env vars to activate the free fallbacks.

## Per-request model override (dropdown)

The overlay panel header shows a 🧠 Model dropdown (only in agent mode, only if `/twins/llm/models` returns ≥ 1 available). User picks a model → that choice is sent as `body.model` on every future request and persists in localStorage (`chatbot-<agentId>-model`). `"Auto (smart router)"` is the default and keeps the cascade behavior above.

## Identity branding

Set these env vars on your backend to rebrand the assistant for your agent:

```bash
ASSISTANT_AGENT_NAME="Asset Agent Assistant"
ASSISTANT_AGENT_TAGLINE="your AI co-pilot for the property portfolio"
ASSISTANT_AGENT_SCOPE="You can search properties, check occupancy, run rent reports, …"
```

Same code, different deployment, different branding.

## Theming

```ts
theme: {
  primaryColor: "#5B47E0",
  accentColor:  "#10B981",
  radius:       "lg",       // sharp / md / lg / xl
  panelWidth:   480,
  panelHeight:  640,
  position:     "bottom-right",
}
```

Fonts inherit from the host app — Asset Agent (Inter) and Meeting Agent (Pretendard) automatically look native to their app.

---

# 📞 Voice / Calling Agent (v1.2.0)

A second subpath `@triple-h/chatbot/voice-ui` adds a phone-call surface: inbound AI receptionist, outbound calls (single + batch campaigns), live transcript streaming, escalation routing. Multi-tenant by `agent_id` — every backend table, every API path, every WebSocket subscription is scoped to one consuming agent. Two agents share the same code, never each other's data.

See the section below for voice setup.
