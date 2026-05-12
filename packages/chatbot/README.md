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

---

# 📞 Voice / Calling Agent (v1.2.0)

A second subpath, `@triple-h/chatbot/voice-ui`, adds a phone-call surface: inbound AI receptionist, outbound calls (single + batch campaigns), live transcript streaming, escalation routing. **Multi-tenant by `agent_id`** — every backend table, every API path, every WebSocket subscription is scoped to one consuming agent. Two agents share the same code, never each other's data.

## Add voice to your agent in 5 steps

### 1. Extend your `AgentConfig` with a `voice` block

```ts
import type { AgentConfig, VoiceConfig } from "@triple-h/chatbot";

export const myConfig: AgentConfig = {
  agentId: "my-agent",
  apiBase: "http://localhost:8000",
  // ...existing identity / intents / knowledge / theme...

  voice: {
    provider: "vapi",
    assistantId: "<from-vapi-console>",
    phoneNumber: "+82-70-XXXX-XXXX",
    defaultLanguage: "ko",
    escalationChannel: {
      kind: "telegram",
      chatId: "<your-bot-chat-id>",
    },
    batchPacing: 12,
    workingHours: { start: 9, end: 21, timezone: "Asia/Seoul" },
    perRecipientLimit: { maxCalls: 1, perRecipientWindowDays: 7 },
    recordingDisclosure: {
      ko: "본 통화는 녹음되며 담당자에게 전달됩니다.",
      en: "This call is being recorded and may be shared with a human agent.",
    },
    outboundReasons: [
      {
        id: "rent_reminder",
        label: { en: "Rent reminder", ko: "임대료 알림" },
        scriptTemplate: {
          ko: `"안녕하세요 {name}, 이번 달 임대료 납부 예정일이 다가오고 있습니다..."`,
          en: `"Hello {name}, your rent is due soon..."`,
        },
      },
      // ...add as many as your agent needs
    ],
  },
};
```

### 2. Mount `<VoiceDashboard />` on your `/calls` route

```tsx
"use client";

import { VoiceDashboard } from "@triple-h/chatbot/voice-ui";
import { myConfig } from "./chatbot.config";

export default function CallsPage() {
  if (!myConfig.voice) return null;
  return (
    <VoiceDashboard
      config={myConfig.voice}
      agentId={myConfig.agentId}
      agentLabel="My Agent"
      mock              // ← set to false once your backend webhook is wired
      initialTab="live"
    />
  );
}
```

That's it for the UI. The dashboard handles tabs, drawer state, mock subscription, and exposes optional callbacks (`onListenIn`, `onTakeOver`, `onPlaceOutboundCall`, …) you wire to the package's voice-client functions once `mock={false}`.

### 3. Mount the incoming-call toast globally (optional)

Floating notification when the bot picks up a call. Mount in your layout:

```tsx
import { IncomingCallToast } from "@triple-h/chatbot/voice-ui";

// Your wrapper handles framework integration (router + pathname).
// Pass `call` from your live-call WebSocket subscription, plus `onWatchLive`.
<IncomingCallToast call={activeCall} onWatchLive={() => router.push("/calls?tab=live")} />
```

The component itself has no Next.js / React Router coupling — your app provides whatever routing API you use.

### 4. Wire the backend (orchestrator-api side)

The dashboard talks to `/api/voice/{agentId}/...` and `/ws/voice/{agentId}/calls` endpoints. The reference implementation in `apps/orchestrator-api/routers/voice.py` is multi-tenant out of the box — register your agent's Vapi assistant ID and you're done:

```sql
INSERT INTO voice_provider_assistants
  (id, agent_id, provider, provider_assistant_id, phone_number)
VALUES
  (gen_random_uuid(), 'my-agent', 'vapi', '<vapi-assistant-uuid>', '+82-70-XXXX-XXXX');
```

Env vars the backend needs — pick the set matching your provider:

**ElevenLabs Conversational AI** (recommended for Korean):

| Var | Purpose |
|---|---|
| `ELEVENLABS_API_KEY` | Outbound + reconciliation calls |
| `ELEVENLABS_WEBHOOK_SECRET` | HMAC verification on `/api/voice/webhook/elevenlabs` |
| `ELEVENLABS_PHONE_NUMBER_ID_<AGENT>` | Per-agent phone number ID from the ElevenLabs console (e.g. `ELEVENLABS_PHONE_NUMBER_ID_VIP`) |

**Vapi** (alternative provider):

| Var | Purpose |
|---|---|
| `VAPI_API_KEY` | Outbound + take-over calls |
| `VAPI_WEBHOOK_SECRET` | HMAC verification on `/api/voice/webhook` |
| `VAPI_PHONE_NUMBER_ID_<AGENT>` | Per-agent phone number ID from Vapi |

**Shared (both providers)**:

| Var | Purpose |
|---|---|
| `SUPABASE_SERVICE_KEY` | Recording uploads + signed URLs |
| `VOICE_WS_TOKEN` | Optional — shared secret protecting the WebSocket |

### 5. Flip live mode in your frontend

```bash
echo 'NEXT_PUBLIC_VOICE_LIVE_MODE=true' >> .env.local
```

Mock mode (default) renders the dashboard against `mock-data.ts` so the UI demos cleanly without a backend dependency. Production = `false`.

## What you get

| Feature | Where it's wired |
|---|---|
| Inbound AI receptionist | Vapi webhook → `voice_calls` row → live WebSocket push |
| Outbound single calls | `voice-client.placeOutboundCall(config, draft)` → Vapi REST API |
| Batch campaigns (dial a list one-by-one) | `voice-client.createBatchCampaign()` + background runner (every 30s) |
| Live transcript streaming | Vapi `transcript` webhook events → `voice_call_turns` upsert → WS broadcast |
| LLM-generated call summaries on hangup | `services/voice_summary.py` — Claude Haiku 4.5 |
| Escalation per `escalationChannel` | Telegram / Slack / email / webhook dispatchers |
| Audio recording storage | Supabase Storage `/{agent_id}/{call_id}.mp3` + 30-day signed URLs |
| Per-recipient rate limits | 1 call / 7 days default; configurable via `perRecipientLimit` |
| Working-hours enforcement | Outbound + batch deferred outside `workingHours` window |
| KR PIPA compliance | First-sentence `recordingDisclosure` injected into the Vapi assistant prompt |
| CSV import for batch campaigns | `POST /api/voice/{agent_id}/campaigns/import` (UTF-8 + CP949) |

## Per-agent customization examples

**VIP** (the first consumer):

```ts
voice: {
  provider: "vapi",
  defaultLanguage: "ko",
  escalationChannel: { kind: "telegram", chatId: process.env.VIP_TG_CHAT! },
  outboundReasons: [/* rent, viewing, document, appointment, custom */],
}
```

**Real Estate** (the next consumer — drop-in, no code changes):

```ts
voice: {
  provider: "vapi",
  defaultLanguage: "ko",
  escalationChannel: { kind: "slack", channel: "#realestate" },
  outboundReasons: [/* listing inquiry, viewing schedule, offer follow-up */],
}
```

**Health** (future):

```ts
voice: {
  provider: "vapi",
  defaultLanguage: "en",
  escalationChannel: { kind: "email", to: "oncall@clinic.example" },
  outboundReasons: [
    { id: "medication_reminder", label: {...}, scriptTemplate: {...} },
    { id: "appointment_confirm", label: {...}, scriptTemplate: {...} },
  ],
  perRecipientLimit: { maxCalls: 2, perRecipientWindowDays: 1 },  // tighter
  recordingRetentionDays: 90,                                       // longer (HIPAA-ish)
}
```

Same dashboard, same backend, completely different agent.

## Voice roadmap

| | Status |
|---|---|
| Inbound + outbound on Vapi US numbers | v1.2.0 ✅ (code complete — awaiting Vapi account signup) |
| Real Estate as second consumer | Pending |
| Korean 070 via SIP trunk + KCC 발신번호 사전등록 | Pending (carrier work) |
| Live transfer to human (Vapi `transfer-call` tool integration) | v1.3 |
| Recording retention auto-cleanup | v1.2.0 ✅ (daily cron) |
