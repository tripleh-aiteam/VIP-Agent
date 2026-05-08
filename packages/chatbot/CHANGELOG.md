# Changelog — @triple-h/chatbot

Versioning follows [SemVer](https://semver.org):
- **MAJOR** (X.y.z) — breaking changes to AgentConfig / TalkResponse / required endpoints
- **MINOR** (x.Y.z) — new features, backward-compatible
- **PATCH** (x.y.Z) — bug fixes, no API change

## Stable API contract (1.x)

The following are **guaranteed stable** within the 1.x line. Changes that
break them are **major-version bumps** with deprecation warnings:

- `AgentConfig` interface (all fields)
- `AgentIntent` interface
- `AgentIdentity` interface
- `AgentTheme` interface
- `AgentKnowledgeBase` interface
- `ActionDefinition` union (all variants)
- `WorkflowStep` interface
- `ProcessStep` interface
- `ConversationTurn` interface
- `TalkRequest` / `TalkResponse` shape
- `<ChatbotOverlay config={...}>` props (`config`, `speakReplies`, `onAction`, `commands`, `className`)
- Backend endpoints: `POST /chatbot/talk`, `POST /chatbot/transcribe`, `POST /chatbot/perceive`, `POST /chatbot/proactive/emit`, `GET /chatbot/health`, `GET /chatbot/skill-suggestions`, `GET /chatbot/version`
- Window globals: `window.__chatbotPush`, `window.__chatbotPerceive`

## [1.1.0] — 2026-05-08

### Streaming + multi-agent routing + protocol adapters

Backwards-compatible. v1.0 configs continue to work unchanged — every new field
is optional. The chatbot only switches to the new code paths when you opt in
via `config.streaming` and/or `config.subAgents`.

**Streaming SSE (token-by-token rendering)**
- New `askStreaming()` engine function, exported from the package root
- Three wire protocols out of the box (`config.streaming.protocol`):
  - `"openai-stream"` (default) — OpenAI-compatible chat completions SSE.
    Reads `data: {"choices":[{"delta":{"content":"..."}}]}` lines.
    Stream ends with `data: [DONE]`.
  - `"sse"` — generic Server-Sent Events. Each `data:` line's payload is
    appended to the running reply as raw text.
  - `"json"` — fully custom. Caller supplies `streaming.tokenExtractor` to
    parse each line. Use for any non-standard wire format.
- Optional `streaming.bodyBuilder` for backends that expect a different request
  shape (OpenAI chat completions, OpenClaw Gateway, etc.)
- `<ChatbotOverlay>` automatically uses streaming when `config.streaming` is
  set; falls back to `ask()` otherwise. No prop changes required.
- Stream is abortable via `AbortSignal` for "Stop generating" UI.

**Multi-agent routing (orchestrator-style platforms)**
- New `SubAgent` type — declares one of N domain agents your orchestrator
  can route to (e.g. portfolio-orchestrator → lease, legal, equity, etc.)
- New `AgentConfig.subAgents?: SubAgent[]` registry, sent inline with every
  TALK request so the backend classifier has full context to route.
- New `AgentConfig.subAgentRouting?: "passthrough" | "auto" | "explicit"`
  - `"passthrough"` (default) — single backend, no routing
  - `"auto"` — backend LLM picks the sub-agent
  - `"explicit"` — caller sets `targetAgentId` per request (UI selector lands in v1.2)
- New `TalkRequest.targetAgentId?: string` — pre-resolved sub-agent target.

**New exports**
- `askStreaming` (engine function)
- `StreamProtocol`, `StreamingConfig`, `StreamingTalkCallbacks` (types)
- `SubAgent`, `SubAgentRouting` (types)

**No breaking changes.** `MODULE_VERSION = "1.1.0"`. Stable API contract unchanged.

---

## [1.0.0] — 2026-05-08

### First stable release — all 5 pillars complete

**🧠 TALK**
- Natural-language Q&A with Claude Haiku LLM classifier
- Two-tier routing: keyword/fuzzy fast path + LLM fallback
- Conversation history (last 6 turns + currentPath) for pronoun resolution
- Per-agent `knowledgeBase` (purpose, menus, features, FAQ) for UI/structure questions
- Bilingual EN/KO with auto language detection
- Per-agent intents + knowledge sent inline → backend has zero agent-specific code

**⚡ ACTION**
- Internal navigation + external URL navigation (with optional `highlight` for scroll-and-glow)
- Triggers (POST jobs to backend endpoints)
- Multi-step workflows with LLM planner + variable passing (`{{step1.reply}}`)
- UI commands (close/scroll/refresh/back/clear-chat etc.) with built-in handlers + per-agent overrides via `commands` prop
- LLM-generated JS scripts with mandatory user confirmation (safety filter blocks fetch/eval/cookie access)
- Confirmation gate for risky intents (`requires_confirmation: true`)

**👁 PERCEPTION**
- Voice input via MediaRecorder + server-side transcription (Whisper → Gemini fallback)
- Text input
- Image input via Gemini Vision (file picker, paste, drag-drop, camera capture)
- File upload: PDF (pypdf), Excel (openpyxl), CSV (csv module), DOCX (python-docx), text/markdown
- Sensor passthrough via `window.__chatbotPerceive(data, hint)` for any host code

**📢 PROACTIVE**
- WebSocket listener for server-pushed `chatbot.proactive` events
- Severity levels: info / warning / error / critical
- Per-agent filtering via optional `agentId`
- `POST /chatbot/proactive/emit` endpoint for any backend
- `window.__chatbotPush(notification)` for any frontend code
- First-load morning briefing (VIP example)
- Scheduler `alert()` integration — every job failure pushes to chatbots

**🔁 SELF-IMPROVE**
- Auto-vocabulary expansion: LLM-classified phrasings auto-add to intent examples
- Correction detection (EN + KO patterns) with persistent storage
- Auto-FAQ: 3+ identical successful queries → cached reply, skip LLM
- Length-preference detection (terse/normal/detailed) → constrains LLM word cap
- Topic affinity tracking
- `GET /chatbot/health` dashboard with accuracy %, fallback %, by-source split, top intents, top failing queries
- `GET /chatbot/skill-suggestions` cluster of repeat fallbacks → missing intents
- `_chatbot_self_improvement` cron every 6 hours

### Architecture

- **Frontend**: React 18 component (`<ChatbotOverlay>`) + framework-agnostic engine (`ask`, `transcribe`, `detectLanguage`)
- **Backend**: FastAPI service modules (`services/chatbot_*.py`) + 7 routes under `/chatbot/*`
- **DB**: 4 new tables (`chatbot_interactions`, `chatbot_corrections`, `chatbot_auto_examples`, `chatbot_user_profiles`)
- **Reusability**: agents pass their config inline with each request — backend has zero agent-specific code (default VIP fallback only for legacy)

### Verified via end-to-end tests

- 10+ natural-language variations (TALK)
- 7 action types including 2-step workflow with variable passing (ACTION)
- 6 file types via /chatbot/perceive (PERCEPTION)
- 4 severity levels via /chatbot/proactive/emit (PROACTIVE)
- Auto-vocab + auto-FAQ swap (SELF-IMPROVE)

---

## [0.1.0] — 2026-05-07 (pre-release)

Initial structure: TALK pillar built, ACTION partial, PERCEPTION basic, PROACTIVE/SELF-IMPROVE not yet implemented. Internal milestone — not for distribution.
