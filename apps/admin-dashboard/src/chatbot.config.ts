/**
 * VIP Agent — Chatbot configuration.
 *
 * This is what the boss-dashboard pages pass to <ChatbotOverlay>. It tells
 * the reusable @triple-h/chatbot module who this agent is, what it knows,
 * and where to call.
 *
 * The intents listed here are MIRRORED on the backend (services/chatbot_talk.py)
 * — kept in sync so the LLM classifier and the executor agree on intent names.
 */

import type { AgentConfig } from "@triple-h/chatbot";
import { API } from "./components/api";

export const vipConfig: AgentConfig = {
  agentId: "vip",
  apiBase: API,

  identity: {
    // Renamed 2026-05-12: this floating overlay is the BOSS-side assistant
    // (helps you operate VIP). The customer-facing chatbot (KakaoTalk + phone)
    // is mounted separately at /chatbot via @triple-h/chatbot/inbox-ui.
    name: "Assistant",
    greeting: {
      en:
        "Hi Boss. I can:\n" +
        "• Open any page (Reports, Twins, Messages, Calls, Chatbot…)\n" +
        "• Send a message to a twin\n" +
        "• Give today's briefing, stock or asset status\n" +
        "• Answer questions about the VIP platform\n" +
        "Try: \"open reports\" or \"send a message to Alice\".",
      ko:
        "안녕하세요 보스. 제가 도와드릴 수 있는 일:\n" +
        "• 페이지 열기 (리포트, 트윈, 메시지, 콜, 챗봇 등)\n" +
        "• 트윈에게 메시지 보내기\n" +
        "• 오늘의 브리핑, 주식·자산 상황 알려드리기\n" +
        "• VIP 플랫폼 사용법 안내\n" +
        "예: \"리포트 열어줘\" 또는 \"Alice에게 메시지 보내줘\".",
    },
    wakeWords: {
      en: ["hey assistant", "hi assistant", "assistant", "hey vip"],
      ko: ["비서", "어시스턴트", "헤이 비서", "안녕 비서"],
    },
    tone: "formal",
  },

  // Quick menu shown to users — backend's full intent definitions live in chatbot_talk.py
  intents: [
    { name: "nav_reports", description: "Open the reports page",
      examples: { en: ["open reports", "show reports"], ko: ["리포트 열어"] },
      action: { type: "navigate", to: "/reports" } },
    { name: "nav_twins", description: "Open the twins page",
      examples: { en: ["open twins", "show my twins"], ko: ["트윈 페이지"] },
      action: { type: "navigate", to: "/twins" } },
    { name: "nav_messages", description: "Open the Messages hub — see and reply to all DMs with each twin",
      examples: { en: ["open messages", "show me my messages", "go to messages", "open the message hub", "see DMs", "show my inbox"],
                  ko: ["메시지 페이지", "메시지 열어", "받은 메시지"] },
      action: { type: "navigate", to: "/messages" } },
    { name: "nav_agents", description: "Open the agents page",
      examples: { en: ["open agents", "list agents"], ko: ["에이전트 페이지"] },
      action: { type: "navigate", to: "/agents" } },
    { name: "query_daily_briefing", description: "Get today's situation",
      examples: { en: ["what's today's situation", "daily briefing", "how is everything"], ko: ["오늘 상황", "오늘 어떻게 됐어"] },
      action: { type: "speak_only" } },
    { name: "query_stock", description: "Stock portfolio + KOSPI",
      examples: { en: ["how is my stock", "what is my stock status", "give me info about my stock", "kospi today"], ko: ["주식 상황", "내 주식 어때"] },
      action: { type: "speak_only" } },
    { name: "query_asset", description: "Asset portfolio + occupancy",
      examples: { en: ["how is my asset portfolio", "asset status", "give me info about my assets"], ko: ["자산 상태", "내 자산 어때"] },
      action: { type: "speak_only" } },
    { name: "send_twin_message", description: "Send a personal message to a specific twin",
      examples: { en: ["send a message to {name}", "tell {name} ...", "text {name} ..."], ko: ["{name}에게 메시지 보내"] },
      action: { type: "speak_only" } },

    // UI control intents — chatbot drives the host UI directly
    { name: "ui_go_back", description: "Go back to the previous page",
      examples: { en: ["go back", "close this menu", "close the twins menu", "previous page", "back"], ko: ["뒤로", "이전 페이지", "닫아"] },
      action: { type: "ui_command", command: "go_back" } },
    { name: "ui_refresh", description: "Refresh the current page",
      examples: { en: ["refresh", "reload page"], ko: ["새로고침"] },
      action: { type: "ui_command", command: "refresh" } },
    { name: "ui_scroll_top", description: "Scroll to top",
      examples: { en: ["scroll up", "go to top"], ko: ["맨 위로"] },
      action: { type: "ui_command", command: "scroll_top" } },
    { name: "ui_close_chatbot", description: "Close the chatbot panel",
      examples: { en: ["close chatbot", "hide chatbot"], ko: ["챗봇 닫아"] },
      action: { type: "ui_command", command: "close_chatbot" } },
    { name: "ui_clear_chat", description: "Clear chat history",
      examples: { en: ["clear chat", "reset chat"], ko: ["대화 지워"] },
      action: { type: "ui_command", command: "clear_chat" } },
  ],

  // Knowledge sources are listed for documentation; the backend reads them
  // directly from the agent's adapter registry — module just calls /chatbot/talk.
  knowledge: [
    { name: "asset_portfolio", description: "Real estate properties + monthly income + occupancy", endpoint: "/agents/health" },
    { name: "stock_portfolio", description: "KOSPI holdings + live prices + P&L", endpoint: "/agents/health" },
    { name: "twins", description: "Digital twins — count, modes, current activity", endpoint: "/twins" },
    { name: "approvals", description: "Pending judgement cases awaiting human review", endpoint: "/judgement/cases" },
  ],

  knowledgeBase: {
    purpose:
      "VIP Agent is the boss/CEO command center for the multi-agent platform. The boss " +
      "supervises digital twins (one per employee) and three domain agents (Asset for real " +
      "estate, Stock for financial portfolio, Realty for property market). The platform " +
      "automatically generates daily and weekly reports, runs overnight handoffs where twins " +
      "work while their owners sleep, and escalates anything that needs human review.",
    menus: [
      { name: "Dashboard",     path: "/",              description: "Home page — overview cards: today's situation, alerts, agent health, latest reports, quick actions." },
      { name: "Twins",         path: "/twins",         description: "List of all digital twins (one per worker). Click a twin to see its activity, knowledge, mode (shadow/active/handoff), and recent tasks." },
      { name: "Messages",      path: "/messages",      description: "Central communication hub — full conversation history with each worker's twin. Browse all DMs, see unread counts, send replies. The chatbot can also send quick messages — this is the searchable archive." },
      { name: "Control Room",  path: "/control-room",  description: "Real-time live view of all agents and twins working — like an operations dashboard with running tasks." },
      { name: "Task Board",    path: "/task-board",    description: "Kanban-style board of all tasks across the platform — pending, in progress, blocked, completed." },
      { name: "Agents",        path: "/agents",        description: "List of registered agents (Asset, Stock, Realty, etc.). Shows status, endpoint URL, last health check." },
      { name: "Workflows",     path: "/workflows",     description: "Schedules and cron jobs — daily report at 8 AM, weekly at Friday 6:30 PM, etc. Edit timing here." },
      { name: "Reports",       path: "/reports",       description: "All generated daily/weekly reports. Click to read, download as DOCX, or compose a new one." },
      { name: "Judgement",     path: "/judgement",     description: "Decision queue — items needing human approval. The boss reviews, approves, or escalates each." },
      { name: "A2A Monitor",   path: "/a2a",           description: "Agent-to-Agent communication monitor — see what messages agents are sending each other in real time." },
      { name: "Channels",      path: "/channels",      description: "Communication channels (Telegram bot, email, webhooks). Register and configure each here." },
      { name: "AI Glass",      path: "/ai-glass",      description: "Smart-glasses integration page — for hands-free field work via AR glasses." },
      { name: "Meetings",      path: "/meetings",      description: "Multi-twin meeting rooms. Create a meeting, invite multiple twins, run a discussion together." },
      { name: "Meeting Notes", path: "/meeting-notes", description: "Real-world meeting recordings: bilingual KR/EN transcription, summary, action items extracted automatically." },
      { name: "Settings",      path: "/settings",      description: "Platform settings — user accounts, API keys, channel config, system preferences." },
    ],
    features: [
      { name: "Daily Briefing", description: "Auto-generated every morning at 8 AM KST. Summarizes overnight twin activity, completed tasks, alerts.",
        how_to: "Visible at top of Dashboard — or ask Chatbot 'what's today's situation'." },
      { name: "Twin Handoff", description: "Workers submit overnight tasks before bed; twins execute autonomously; boss reviews in the morning.",
        how_to: "Workers do it from the Twin Portal. Boss reviews at /handoff page." },
      { name: "Twin Modes", description: "Each twin has a mode: shadow (passive learning), active (working), handoff (preparing morning report). Auto-switches by Korean working hours.",
        how_to: "Visible on each twin's detail page. Manual override available." },
      { name: "Voice Chatbot", description: "Always-on voice assistant in bottom-right corner. Speak naturally — no exact keywords needed.",
        how_to: "Just speak: 'Hey Chatbot, asset status' or type in the panel." },
      { name: "Multi-LLM Routing", description: "Routes between Claude (Opus/Sonnet/Haiku), OpenAI GPT-4o, Gemini, local Ollama. Falls back automatically if one fails.",
        how_to: "Configure in Settings → API Keys. Default model per request type." },
      { name: "Telegram Reports", description: "Daily and weekly reports auto-pushed to Telegram with executive summaries.",
        how_to: "Connect bot in Channels page; reports send automatically once scheduled." },
    ],
    faq: [
      { q: "How many twins do I have?",
        a: "11 twins are currently registered. Ask 'show my twins' for the full list with their modes and status." },
      { q: "How do I add a new twin?",
        a: "Twins are created when a worker registers via the Twin Portal (port 3010). They auto-link to their owner via email." },
      { q: "Where do my daily reports come from?",
        a: "Auto-generated by the scheduler at 8 AM KST every weekday. Sources: twin handoffs from previous evening + agent summaries (asset/stock/realty)." },
      { q: "Why is some data showing zero?",
        a: "Asset/Stock/Realty agents pull live data when available, fall back to your CSV upload, then to realistic mock if nothing is configured." },
    ],
    context:
      "Tech stack: Next.js (admin-dashboard, twin-portal), FastAPI orchestrator-api, Postgres on Supabase, " +
      "Redis pub/sub, multi-provider LLM client. Scheduler runs 7+ cron jobs. Korean (한국어) and English supported.",
  },

  theme: {
    primaryColor: "#3B82F6",
    accentColor:  "#6366F1",
    radius:       "lg",
    panelWidth:   480,
    panelHeight:  640,
    position:     "bottom-right",
  },

  supportedLanguages: ["auto", "en", "ko"],

  /**
   * Voice / Calling Agent — VIP's phone presence.
   *
   * Using ElevenLabs Conversational AI (the user already has an
   * ElevenLabs subscription). Backend resolves provider routing via
   * `voice_provider_assistants.provider="elevenlabs"`.
   *
   * Three placeholders below are filled in once their real-world
   * setup completes (tracked in Daily_changes.md):
   *
   *   - assistantId       ← ElevenLabs Conversational AI agent ID
   *                         (created in https://elevenlabs.io/app/conversational-ai)
   *   - phoneNumber       ← filled after SIP-trunking the company 070
   *                         (Step 22 — Korean carrier setup)
   *   - escalationChannel.chatId ← from your existing Telegram bot's chat ID
   *
   * The dashboard renders fine with the placeholders for mock-mode UI;
   * the backend is where they actually need to be real.
   */
  voice: {
    // Self-hosted voice agent on KT 070 — Asterisk SIP edge + local
    // Whisper + Ollama (EXAONE 3.5 32B) + MeloTTS. See
    // infra/asterisk/README.md for the carrier-side setup steps.
    //
    // `assistantId` here is just a logical key — the actual SIP routing
    // is in Asterisk's dialplan. We use "vip-selfhosted" as a stable id
    // that ties this row to voice_provider_assistants.
    provider: "selfhosted",
    assistantId: "vip-selfhosted",
    phoneNumber: "+82-70-XXXX-XXXX",
    defaultLanguage: "ko",
    escalationChannel: {
      kind: "telegram",
      chatId: "FILL_FROM_EXISTING_TELEGRAM_BOT",
      botEnvKey: "TELEGRAM_BOT_TOKEN",
    },
    batchPacing: 12,
    workingHours: { start: 9, end: 21, timezone: "Asia/Seoul" },
    perRecipientLimit: { maxCalls: 1, perRecipientWindowDays: 7 },
    recordingRetentionDays: 30,
    recordingDisclosure: {
      ko: "안녕하세요, 트리플H 부동산 AI 비서입니다. 본 통화는 녹음되며 담당자에게 전달됩니다.",
      en: "Hello, this is Triple-H Real Estate's AI assistant. This call is being recorded and may be shared with a human agent.",
    },
    outboundReasons: [
      {
        id: "rent_reminder",
        label: { en: "Rent reminder", ko: "임대료 알림" },
        scriptTemplate: {
          ko: `"안녕하세요 {name}, 트리플H 부동산 AI 비서입니다. 이번 달 임대료 납부 예정일이 다가오고 있어 알려드리려고 연락드렸습니다. 혹시 납부에 어려움이 있으신가요?"`,
          en: `"Hello {name}, this is Triple-H Real Estate's AI assistant. Your rent payment is due soon — is there anything I can help with to make sure it goes through on time?"`,
        },
        requiredContextKeys: ["amount", "dueDate", "lease"],
      },
      {
        id: "viewing_confirm",
        label: { en: "Viewing confirmation", ko: "방문 예약 확인" },
        scriptTemplate: {
          ko: `"안녕하세요 {name}, 트리플H 부동산 AI 비서입니다. 내일 예정된 방문 일정을 확인하려고 연락드렸습니다. 예정대로 진행하시겠어요?"`,
          en: `"Hello {name}, this is Triple-H Real Estate's AI assistant. I'm calling to confirm your viewing scheduled for tomorrow — does the time still work for you?"`,
        },
      },
      {
        id: "document_followup",
        label: { en: "Document follow-up", ko: "서류 확인" },
        scriptTemplate: {
          ko: `"안녕하세요 {name}, 트리플H 부동산 AI 비서입니다. 요청드린 서류를 아직 받지 못하여 확인차 연락드렸습니다. 언제쯤 보내주실 수 있으신가요?"`,
          en: `"Hello {name}, this is Triple-H Real Estate's AI assistant. We haven't received the documents you were going to send — when do you think you'll be able to share them?"`,
        },
      },
      {
        id: "appointment_reminder",
        label: { en: "Appointment reminder", ko: "약속 알림" },
        scriptTemplate: {
          ko: `"안녕하세요 {name}, 트리플H 부동산 AI 비서입니다. 다가오는 약속을 알려드리려고 연락드렸습니다. 일정 변경 사항이 있으신가요?"`,
          en: `"Hello {name}, this is Triple-H Real Estate's AI assistant. I'm calling about your upcoming appointment — is there anything you'd like to change?"`,
        },
      },
      {
        id: "custom",
        label: { en: "Custom", ko: "기타" },
        scriptTemplate: {
          ko: `"안녕하세요 {name}, 트리플H 부동산 AI 비서입니다. [커스텀 스크립트는 호출 직전에 LLM이 생성합니다.]"`,
          en: `"Hello {name}, this is Triple-H Real Estate's AI assistant. [Custom script generated by the LLM at call time.]"`,
        },
      },
    ],
  },
};
