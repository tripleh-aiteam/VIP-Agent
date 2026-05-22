"""
assistant_manifest — single source of truth for what the VIP Assistant can do.

Lists every page in the dashboard, every external agent app, and the
capabilities each surface offers. The assistant_agent reads this at
runtime, hands it to the LLM as part of the tool catalog, and uses it
to validate navigate(path) / open_portal(agent) calls.

ADDING A NEW MENU LATER:
    1. Append a dict to PAGES below with path, name, description, keywords.
    2. Restart the orchestrator (or wait for cache TTL).
    3. The assistant immediately understands queries about the new page —
       no keyword list, no new intent, no per-page handler needed.

ADDING A NEW EXTERNAL AGENT:
    Append a dict to EXTERNAL_AGENTS with name, portal_url, backend_url,
    keywords. The LLM will route "open X" / "show me X" / "X 열어" to
    open_portal(agent="<name>") automatically.
"""

from __future__ import annotations

import os


# ============================================================================
#  Internal dashboard pages
# ============================================================================
#
# `path`         — the route the frontend navigates to
# `name`         — human-readable display name
# `description`  — what the page is FOR (used by LLM to decide if relevant)
# `keywords`     — comma-prone synonyms the user might say
# `capabilities` — high-level actions available on the page (informational
#                  for the LLM; actual tools are registered separately)
# `sidebar`      — True if visible in the left nav, False for hidden routes
# `priority`     — display order on the boss dashboard sidebar
# ============================================================================

PAGES: list[dict] = [
    {
        "path": "/chatbot",
        "name": "Chatbot",
        "description": "Customer-facing message inbox — all KakaoTalk, Phone, and SMS conversations from real customers. Boss reads them here and either lets the AI auto-reply or takes over to reply manually.",
        "keywords": ["chatbot", "inbox", "customer", "customers", "kakao", "kakaotalk",
                     "message", "messages", "chat", "고객 메시지", "카카오톡", "받은 메시지", "챗봇"],
        "capabilities": ["list_conversations", "reply_to_customer", "resolve_conversation",
                         "take_over_conversation", "set_boss_mode"],
        "sidebar": True,
        "priority": 1,
        # Sub-areas reachable from inside this page. Each entry tells the
        # assistant "if user wants this thing, the path is here + open
        # the named section." The LLM picks the right one when the user
        # asks for something specific (e.g. "show needs-reply").
        "sub_tabs": [
            {"name": "All conversations",   "id": "all",         "description": "Default list of every conversation"},
            {"name": "Needs reply",         "id": "needs_reply", "description": "Filter to only conversations awaiting a human response"},
            {"name": "Active",              "id": "active",      "description": "Currently being handled by the boss / human"},
            {"name": "Resolved",            "id": "resolved",    "description": "Closed / answered conversations"},
        ],
        # Dynamic detail routes the assistant can deep-link to
        "dynamic_routes": [
            {"pattern": "/chatbot/{conversation_id}", "description": "Single conversation detail view"},
        ],
    },
    {
        "path": "/",
        "name": "Dashboard",
        "description": "Home page — overview cards: today's situation, alerts, agent health, latest reports, quick actions. The default landing page.",
        "keywords": ["dashboard", "home", "main", "overview", "command center",
                     "메인", "홈", "대시보드"],
        "capabilities": ["see_briefing", "see_alerts", "quick_actions"],
        "sidebar": True,
        "priority": 2,
    },
    {
        "path": "/twins",
        "name": "Twins",
        "description": "List of all digital twin employees. Each twin is an AI representation of a worker. Click a twin to see activity, knowledge, mode (shadow/active/handoff), recent tasks.",
        "keywords": ["twins", "twin", "employees", "workers", "staff",
                     "트윈", "직원", "근로자"],
        "capabilities": ["list_twins", "view_twin_detail", "change_twin_mode"],
        "sidebar": True,
        "priority": 3,
        "sub_tabs": [
            {"name": "Activity",      "id": "activity",  "description": "What the twin has done recently — events, knowledge added, decisions"},
            {"name": "Knowledge",     "id": "knowledge", "description": "Per-twin knowledge base — corrections, rules, uploaded docs"},
            {"name": "Tasks",         "id": "tasks",     "description": "Tasks the twin is currently working on or has finished"},
            {"name": "Mode",          "id": "mode",      "description": "Shadow / active / handoff toggle"},
            {"name": "Reports",       "id": "reports",   "description": "Reports the twin has produced"},
        ],
        "dynamic_routes": [
            {"pattern": "/twins/{twin_id}", "description": "Single twin detail view with all sub-tabs above"},
        ],
    },
    {
        "path": "/messages",
        "name": "Messages",
        "description": "Central communication archive — Boss ↔ Twin DMs. Browse history, send replies, search messages with workers.",
        "keywords": ["messages", "dm", "direct message", "boss messages", "boss to worker",
                     "메시지", "디엠"],
        "capabilities": ["send_dm", "browse_message_history", "search_messages"],
        "sidebar": True,
        "priority": 4,
    },
    {
        "path": "/calls",
        "name": "Calls",
        "description": "Voice agent call logs — inbound and outbound phone calls made by the AI voice receptionist or the boss-triggered outbound campaigns.",
        "keywords": ["calls", "phone", "voice", "telephone", "call logs",
                     "전화", "통화", "전화 기록"],
        "capabilities": ["list_calls", "view_call_transcript", "trigger_outbound_call"],
        "sidebar": True,
        "priority": 5,
    },
    {
        "path": "/control-room",
        "name": "Control Room",
        "description": "Real-time operations view — see all agents and twins working live, currently running tasks, system health.",
        "keywords": ["control room", "control", "ops", "operations", "live", "live view",
                     "통제실", "관제실", "실시간"],
        "capabilities": ["see_live_activity", "monitor_agents", "monitor_twins"],
        "sidebar": True,
        "priority": 6,
    },
    {
        "path": "/task-board",
        "name": "Task Board",
        "description": "Kanban board of ALL tasks across the platform — pending, in progress, blocked, completed. Assign, reassign, or cancel.",
        "keywords": ["task board", "tasks", "kanban", "todo", "to do", "to-do",
                     "태스크", "할 일", "작업"],
        "capabilities": ["list_tasks", "create_task", "cancel_task", "assign_task"],
        "sidebar": True,
        "priority": 7,
    },
    {
        "path": "/agents",
        "name": "Agents",
        "description": "List of registered domain agents (Asset, Stock, Realty). Status, endpoint URL, last health check, ping. NOTE: this is the INTERNAL listing — for opening the actual functional Asset/Stock/Realty WEB APPS, use open_portal(agent=...) instead.",
        "keywords": ["agents page", "all agents", "list of agents", "registered agents",
                     "agents listing", "에이전트 페이지", "에이전트 목록", "전체 에이전트"],
        "capabilities": ["list_agents", "ping_agent", "view_agent_status"],
        "sidebar": True,
        "priority": 8,
    },
    {
        "path": "/workflows",
        "name": "Workflows",
        "description": "Scheduled jobs and cron schedules — daily report at 8 AM, weekly report Friday 6:30 PM, etc. Edit schedules here.",
        "keywords": ["workflows", "schedules", "cron", "automation", "scheduled jobs",
                     "워크플로우", "스케줄", "자동화"],
        "capabilities": ["list_workflows", "edit_schedule", "trigger_workflow"],
        "sidebar": True,
        "priority": 9,
    },
    {
        "path": "/reports",
        "name": "Reports",
        "description": "All generated daily and weekly reports. Click to read, download as DOCX, or compose a new one.",
        "keywords": ["reports", "report", "daily report", "weekly report", "briefing",
                     "리포트", "보고서", "데일리", "주간"],
        "capabilities": ["list_reports", "view_report", "download_report",
                         "trigger_daily_report", "trigger_weekly_report"],
        "sidebar": True,
        "priority": 10,
        "sub_tabs": [
            {"name": "Daily",   "id": "daily",   "description": "Daily briefing reports (auto-generated 8 AM KST)"},
            {"name": "Weekly",  "id": "weekly",  "description": "Weekly summary reports (Friday 6:30 PM KST)"},
            {"name": "Custom",  "id": "custom",  "description": "Ad-hoc reports composed manually"},
        ],
        "dynamic_routes": [
            {"pattern": "/reports/{report_id}", "description": "Single report — full text, with download as DOCX"},
        ],
    },
    {
        "path": "/judgement",
        "name": "Judgement",
        "description": "Decision queue — items needing human approval. Boss reviews, approves, or escalates.",
        "keywords": ["judgement", "approvals", "approval", "approve", "decisions",
                     "review queue", "승인", "결정", "검토"],
        "capabilities": ["list_pending_approvals", "approve", "reject", "escalate"],
        "sidebar": True,
        "priority": 11,
    },
    {
        "path": "/a2a",
        "name": "A2A Monitor",
        "description": "Agent-to-Agent communication monitor — see messages flowing between agents in real time.",
        "keywords": ["a2a", "agent to agent", "agent comms", "agent communication",
                     "에이전트 통신"],
        "capabilities": ["see_a2a_traffic"],
        "sidebar": True,
        "priority": 12,
    },
    {
        "path": "/meetings",
        "name": "Meetings",
        "description": "Multi-twin meeting rooms. Create a meeting, invite multiple twins, run a discussion. Sub-tab includes Meeting Notes (real-world voice → bilingual KR/EN summary).",
        "keywords": ["meetings", "meeting", "meeting room",
                     "미팅", "회의", "회의실"],
        "capabilities": ["list_meetings", "schedule_meeting", "join_meeting", "cancel_meeting"],
        "sidebar": True,
        "priority": 13,
        "sub_tabs": [
            {"name": "Upcoming",  "id": "upcoming",  "description": "Meetings that haven't started yet"},
            {"name": "Live",      "id": "live",      "description": "Meetings happening right now"},
            {"name": "Completed", "id": "completed", "description": "Past meetings with transcripts/summaries"},
        ],
        "dynamic_routes": [
            {"pattern": "/meetings/{meeting_id}/room", "description": "Live meeting room — multi-participant view with voice + chat"},
        ],
    },
    {
        "path": "/settings",
        "name": "Settings",
        "description": "Platform settings — user accounts, API keys, channel config, system preferences. Channels and Chatbot Health diagnostics are accessible from here.",
        "keywords": ["settings", "config", "configuration", "preferences",
                     "설정", "환경설정"],
        "capabilities": ["edit_user_account", "manage_api_keys", "configure_channels"],
        "sidebar": True,
        "priority": 14,
        "sub_tabs": [
            {"name": "Account",       "id": "account",       "description": "Boss user profile, change password, email"},
            {"name": "API Keys",      "id": "api_keys",      "description": "Manage OpenAI / Anthropic / Gemini / Groq keys + per-tenant overrides"},
            {"name": "Channels",      "id": "channels",      "description": "Telegram, email, webhook integrations — links to /channels"},
            {"name": "Diagnostics",   "id": "diagnostics",   "description": "Chatbot health checks — links to /chatbot-health"},
            {"name": "Preferences",   "id": "preferences",   "description": "Theme, language default, notification rules"},
        ],
    },
    # === Hidden admin route, not in sidebar ===
    {
        "path": "/admin/meeting-twins",
        "name": "Admin · Meeting Twins",
        "description": "Admin-only page — manage the digital-twin participants assigned to multi-twin meeting rooms. Used to seed/clean up automated meeting attendees.",
        "keywords": ["meeting twins", "admin twins", "meeting attendees",
                     "어드민 미팅"],
        "capabilities": ["list_meeting_twins", "create_meeting_twin", "delete_meeting_twin"],
        "sidebar": False,
    },
    # ===== Hidden / direct-link only =====
    {
        "path": "/meeting-notes",
        "name": "Meeting Notes",
        "description": "Real-world meeting recordings: bilingual KR/EN transcription, summary, action items extracted automatically. Accessed via the Meetings page tab bar.",
        "keywords": ["meeting notes", "meeting transcript", "transcript",
                     "회의록", "미팅 노트"],
        "capabilities": ["list_meeting_notes", "view_transcript", "extract_action_items"],
        "sidebar": False,
    },
    {
        "path": "/handoff",
        "name": "Handoff",
        "description": "Morning handoff review — boss reviews what twins did overnight, approves or rejects each handoff.",
        "keywords": ["handoff", "overnight", "morning handoff", "morning review",
                     "인계", "오늘 인계", "야간 작업"],
        "capabilities": ["list_handoffs", "approve_handoff", "reject_handoff"],
        "sidebar": False,
    },
    {
        "path": "/channels",
        "name": "Channels",
        "description": "Communication channels (Telegram bot, email, webhooks). Register and configure each. Reached via Settings → Integrations.",
        "keywords": ["channels", "telegram", "webhook", "integrations",
                     "채널", "텔레그램"],
        "capabilities": ["configure_channel", "test_channel"],
        "sidebar": False,
    },
    {
        "path": "/chatbot-health",
        "name": "Chatbot Health",
        "description": "Chatbot diagnostics — webhook status, latency, recent errors. Reached via Settings → Diagnostics.",
        "keywords": ["chatbot health", "diagnostics", "webhook status",
                     "챗봇 상태"],
        "capabilities": ["view_diagnostics"],
        "sidebar": False,
    },
    {
        "path": "/ai-glass",
        "name": "AI Glass",
        "description": "Smart-glasses integration page — for hands-free field work via AR glasses.",
        "keywords": ["ai glass", "smart glasses", "ar", "glasses"],
        "capabilities": ["pair_device"],
        "sidebar": False,
    },
    {
        "path": "/chat",
        "name": "Chat (legacy)",
        "description": "Legacy chat page — replaced by the floating Assistant overlay. Kept as a direct route for backward compatibility.",
        "keywords": ["chat", "old chat"],
        "capabilities": [],
        "sidebar": False,
    },
]


# ============================================================================
#  External agent web apps (the actual deployed functional UIs)
# ============================================================================
#
# These are SEPARATE deployed apps that the boss opens in a new tab.
# `portal_url`  — frontend (user-facing) homepage
# `backend_url` — API the orchestrator calls for live data (for *_situation)
# Override either with env vars (REAL_<NAME>_AGENT_PORTAL_URL / _URL).
# ============================================================================

EXTERNAL_AGENTS: list[dict] = [
    {
        "name": "Asset",
        "name_ko": "자산",
        "description": "Asset Agent — manages the company's real estate portfolio: occupancy, rental income, asset valuation, yields. The boss opens this app to manage holdings.",
        "portal_url": (os.getenv("REAL_ASSET_AGENT_PORTAL_URL")
                       or os.getenv("REAL_ASSET_AGENT_URL")
                       or "https://asset-agent-s4tw.onrender.com"),
        "backend_url": (os.getenv("REAL_ASSET_AGENT_URL")
                        or "https://asset-agent-s4tw.onrender.com"),
        "keywords": ["asset", "asset agent", "asset app", "asset portal",
                     "property portfolio", "real estate holdings",
                     "자산", "자산 에이전트", "자산 앱"],
    },
    {
        "name": "Stock",
        "name_ko": "주식",
        "description": "Stock Agent — live stock market data, KOSPI/KOSDAQ, watchlist, portfolio P&L, market news. The boss opens this to view market info.",
        "portal_url": (os.getenv("REAL_STOCK_AGENT_PORTAL_URL")
                       or "https://stock-advisor-agent-ten.vercel.app"),
        "backend_url": (os.getenv("REAL_STOCK_AGENT_URL")
                        or "https://stock-advisor-agent-9qwi.onrender.com"),
        "keywords": ["stock", "stock agent", "stock app", "stock portal",
                     "stock advisor", "kospi", "kosdaq", "market",
                     "주식", "주식 에이전트", "주식 앱", "스톡", "코스피", "시장"],
    },
    {
        "name": "Realty",
        "name_ko": "부동산",
        "description": "Real Estate Agent — property listings, market data, listing search. The boss opens this for the property search/management UI.",
        "portal_url": (os.getenv("REAL_REALTY_AGENT_PORTAL_URL")
                       or "https://realestate-tripleh.vercel.app"),
        "backend_url": (os.getenv("REAL_REALTY_AGENT_URL")
                        or "https://realestate-tripleh.vercel.app"),
        "keywords": ["realty", "real estate", "realty agent", "real estate agent",
                     "realty app", "real estate app", "property", "property app",
                     "부동산", "부동산 에이전트", "부동산 앱"],
    },
]


# ============================================================================
#  Accessors
# ============================================================================

def get_all_pages(include_hidden: bool = True) -> list[dict]:
    """All pages. If include_hidden=False, only sidebar pages."""
    if include_hidden:
        return list(PAGES)
    return [p for p in PAGES if p.get("sidebar")]


def get_page_by_path(path: str) -> dict | None:
    for p in PAGES:
        if p["path"] == path:
            return p
    return None


def is_valid_path(path: str) -> bool:
    return get_page_by_path(path) is not None


def get_external_agents() -> list[dict]:
    return list(EXTERNAL_AGENTS)


def get_agent_by_name(name: str) -> dict | None:
    name_lower = (name or "").lower().strip()
    for a in EXTERNAL_AGENTS:
        if a["name"].lower() == name_lower or a.get("name_ko", "") == name_lower:
            return a
        # Also match keywords
        for kw in a.get("keywords", []):
            if kw.lower() == name_lower:
                return a
    return None


def pages_summary_for_llm() -> str:
    """A compact summary the LLM uses to pick a path.

    Each entry includes the page path, name, description, and — when the
    page has known sub-tabs or dynamic detail routes — those are listed
    indented underneath. This gives the LLM the full nested-menu picture
    so it can answer "where is X" / "open Y inside Z" precisely.
    """
    lines = []
    for p in PAGES:
        kw = ", ".join(p.get("keywords", [])[:6])
        lines.append(f"- {p['path']} ({p['name']}): {p['description']} [keywords: {kw}]")
        # Sub-tabs (sections within the page)
        for st in p.get("sub_tabs") or []:
            lines.append(f"    • [tab] {st['name']} (id={st['id']}): {st['description']}")
        # Dynamic detail routes (one record per pattern)
        for dr in p.get("dynamic_routes") or []:
            lines.append(f"    • [dynamic] {dr['pattern']}: {dr['description']}")
    return "\n".join(lines)


def agents_summary_for_llm() -> str:
    """Compact summary of external agent portals for the LLM."""
    lines = []
    for a in EXTERNAL_AGENTS:
        kw = ", ".join(a.get("keywords", [])[:6])
        lines.append(f"- {a['name']} ({a.get('name_ko', '')}): {a['description']} [keywords: {kw}]")
    return "\n".join(lines)


# ============================================================================
#  Phase 8 — Auto-discovery: detect drift between Sidebar.tsx and PAGES
# ============================================================================

import re
from pathlib import Path


def detect_sidebar_drift() -> dict:
    """Parse the admin-dashboard Sidebar.tsx and compare its hrefs/labels
    against PAGES. Returns:

        {
          "in_sidebar_not_manifest": [{href, label}, ...],   # missing from manifest
          "in_manifest_not_sidebar": [{path, name}, ...],    # hidden routes only
          "ok": bool,
        }

    Use this as a CI check or a startup warning when devs add new menus
    to the sidebar without updating the manifest. Doesn't auto-edit;
    just reports — that keeps the manifest under human control.
    """
    # Try a few candidate paths since Render's working dir layout may
    # differ from local. If none found, return a skipped result rather
    # than crashing the manifest endpoint.
    candidates = []
    here = Path(__file__).resolve()
    for parent_depth in (2, 3, 4, 5):
        try:
            root = here.parents[parent_depth]
            candidates.append(root / "apps" / "admin-dashboard" / "src" / "components" / "Sidebar.tsx")
        except IndexError:
            continue
    sidebar_file = next((c for c in candidates if c.exists()), None)
    if not sidebar_file:
        return {
            "ok": True,
            "skipped": True,
            "message": "Sidebar.tsx not reachable from orchestrator runtime — drift check skipped.",
            "checked_paths": [str(c) for c in candidates],
        }

    try:
        text = sidebar_file.read_text(encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"Couldn't read Sidebar.tsx: {e}"}

    # Match patterns like:  { href: "/chatbot", label: "Chatbot", ... }
    sidebar_entries = []
    pattern = re.compile(r'\{\s*href:\s*"([^"]+)"\s*,\s*label:\s*"([^"]+)"')
    for m in pattern.finditer(text):
        href, label = m.group(1), m.group(2)
        sidebar_entries.append({"href": href, "label": label})

    manifest_paths = {p["path"] for p in PAGES}
    sidebar_paths = {e["href"] for e in sidebar_entries}

    in_sidebar_not_manifest = [
        e for e in sidebar_entries if e["href"] not in manifest_paths
    ]
    in_manifest_not_sidebar = [
        {"path": p["path"], "name": p["name"], "sidebar": p.get("sidebar", False)}
        for p in PAGES
        if p["path"] not in sidebar_paths and p.get("sidebar")
    ]

    return {
        "ok": not in_sidebar_not_manifest,  # success if no missing entries
        "sidebar_count": len(sidebar_entries),
        "manifest_count": len(PAGES),
        "in_sidebar_not_manifest": in_sidebar_not_manifest,
        "in_manifest_not_sidebar": in_manifest_not_sidebar,
        "message": (
            f"All {len(sidebar_entries)} sidebar entries are in the manifest."
            if not in_sidebar_not_manifest
            else f"⚠️ {len(in_sidebar_not_manifest)} sidebar entries are NOT in "
                 "the manifest — add them so the assistant can navigate to them."
        ),
    }
