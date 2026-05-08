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
    name: "Chatbot",
    greeting: {
      en: "Hi Boss. I'm your VIP assistant — speak or type naturally and I'll figure it out.",
      ko: "안녕하세요 보스. VIP 비서 챗봇입니다. 편하게 말씀해 주세요.",
    },
    wakeWords: {
      en: ["hey chatbot", "hi chatbot", "chatbot", "hey assistant"],
      ko: ["챗봇", "쳇봇", "헤이 챗봇", "안녕 챗봇"],
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
};
