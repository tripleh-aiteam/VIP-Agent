# @triple-h/chatbot

Reusable voice + text chatbot module for multi-agent platforms. Drop it into any agent (VIP, Meeting, Asset, Smart Helmet, Health) and get a working assistant in 3 lines.

## What's in v0.1

✅ **TALK** — natural-language Q&A. User asks anything, the LLM picks the right intent or answers from live data.
🔜 ACTION (v0.2) — full action execution including multi-step workflows.
🔜 PERCEPTION (v0.2) — image + file uploads, Gemini Vision.
🔜 PROACTIVE (v0.3) — alerts spoken, scheduled briefings.

## Install (workspace-local for now)

The package lives in `packages/chatbot/` of the monorepo. Each agent's `tsconfig.json` adds:

```json
"paths": {
  "@triple-h/chatbot": ["../../packages/chatbot/src"]
}
```

And the agent's `next.config.js` adds `transpilePackages: ["@triple-h/chatbot"]`.

## Use it (3 lines)

```tsx
import { ChatbotOverlay } from "@triple-h/chatbot";
import { vipConfig } from "./chatbot.config";

<ChatbotOverlay config={vipConfig} onAction={(a) => router.push(a.to!)} />
```

## Configure your agent

```ts
import type { AgentConfig } from "@triple-h/chatbot";

export const myConfig: AgentConfig = {
  agentId: "my-agent",
  apiBase: "http://localhost:8000",   // your backend
  identity: {
    name: "My Assistant",
    greeting: { en: "Hi! How can I help?" },
    wakeWords: { en: ["hey assistant"] },
    tone: "friendly",
  },
  intents: [
    { name: "open_reports", description: "Open the reports page",
      examples: { en: ["open reports"] },
      action: { type: "navigate", to: "/reports" } },
    // ...
  ],
  knowledge: [
    { name: "portfolio", description: "Live data", endpoint: "/api/portfolio" },
  ],
  theme: { primaryColor: "#3B82F6", radius: "lg" },
};
```

## Backend — what your agent needs to implement

Two endpoints:

### `POST /chatbot/talk`
Receives `{ query, language, agentId }`, returns `{ reply, language, intent?, action?, source? }`.

Your backend's job:
1. Try keyword/fuzzy match against your intent list (fast).
2. Fall back to LLM with your live-data snapshot in the system prompt.
3. Return either an action (navigate / trigger) or a natural-language reply.

The VIP orchestrator's `services/chatbot_talk.py` is the reference implementation.

### `POST /chatbot/transcribe`
Receives multipart `file=<audio>`, returns `{ transcript, language }`.

Use OpenAI Whisper or Gemini 2.5 Flash audio. The VIP orchestrator's `routers/chatbot.py` shows both with auto-fallback.

## How natural-language understanding works

Two-tier classifier:

```
User: "What is my stock status?"
   ↓
[Tier 1] keyword/fuzzy match → finds "stock status" example → query_stock ✅ (fast)

User: "Tell me how the market is treating my portfolio"
   ↓
[Tier 1] no exact keyword match → fall through
[Tier 2] LLM sees intent menu + live data → returns query_stock ✅ (smart)

User: "What's the weather in Seoul?"
   ↓
[Tier 1] no match
[Tier 2] LLM has no relevant data → free_answer with "I don't know" ✅ (graceful)
```

Users get to phrase requests **infinite ways** without memorizing keywords.

## Theming

```ts
theme: {
  primaryColor: "#5B47E0",
  accentColor:  "#10B981",
  radius:       "lg",      // sharp / md / lg / xl
  panelWidth:   480,
  panelHeight:  640,
  position:     "bottom-right",
}
```

Fonts inherit from the host app — never set explicitly. So Asset Agent (Inter) and Meeting Agent (Pretendard) automatically look native to their app.

## Roadmap

| Pillar | Status | Notes |
|---|---|---|
| 🧠 TALK | v0.1 ✅ | Q&A with LLM-driven intent matching |
| ⚡ ACTION | v0.2 | Add multi-step workflow planner |
| 👁 PERCEPTION | v0.2 | Image upload, file drop, Gemini Vision |
| 📢 PROACTIVE | v0.3 | Alerts pushed via WebSocket, spoken automatically |

## Status

v0.1 — TALK working end-to-end with VIP. Tested with 10+ natural-language variations in EN and KO.
