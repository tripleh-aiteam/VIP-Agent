"""
VIP AI Platform — Voice Intent Router (Chatbot)
Translates voice-friendly natural-language commands into orchestrator actions.
Returns SHORT, conversational, TTS-friendly replies (no markdown, no long lists).

Supports English and Korean intents. The same handler decides which language
to reply in based on the `lang` parameter ("en", "ko", or "auto").

Use this from POST /chat/voice — designed for Web Speech API transcripts.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func


# ---------------------------------------------------------------------------
# Intent patterns — bilingual keyword sets per intent
# ---------------------------------------------------------------------------

# Each tuple: (intent_name, English keywords, Korean keywords)
# IMPORTANT: order matters — first match wins. Specific multi-word patterns
# (e.g. "open asset agent") must come BEFORE single-word patterns (e.g. "asset")
# so navigation requests don't get intercepted by data-query intents.
INTENT_PATTERNS = [
    # ========== Specific navigation: per-agent (must precede *_situation) ==========
    ("nav_asset_agent", [
        "open asset agent", "go to asset agent", "show asset agent",
        "asset agent page", "open the asset agent", "navigate to asset agent",
    ], [
        "자산 에이전트 열어", "자산 에이전트 페이지",
    ]),
    ("nav_stock_agent", [
        "open stock agent", "go to stock agent", "show stock agent",
        "stock agent page", "open the stock agent", "navigate to stock agent",
    ], [
        "주식 에이전트 열어", "주식 에이전트 페이지",
    ]),
    ("nav_realty_agent", [
        "open realty agent", "go to realty agent", "show realty agent",
        "realty agent page", "open real estate agent", "go to real estate agent",
    ], [
        "부동산 에이전트 열어", "부동산 에이전트 페이지",
    ]),
    # ========== Reports ==========
    ("daily_briefing", [
        "today", "today's report", "daily briefing", "daily report",
        "current situation", "situation now", "morning briefing",
        "what's the situation", "status today", "overview"
    ], [
        "오늘", "오늘 리포트", "오늘 보고", "현재 상황", "데일리", "상황", "오늘 상황", "브리핑"
    ]),
    ("weekly_report", [
        "weekly report", "this week", "weekly summary", "weekly update",
        "week's performance", "weekly briefing"
    ], [
        "주간 보고", "이번 주", "주간", "주간 리포트", "주간 요약"
    ]),
    ("monthly_report", [
        "monthly report", "this month", "monthly summary", "monthly comparison"
    ], [
        "월간 보고", "이번 달", "월간", "월별 리포트"
    ]),
    # ========== Agents ==========
    ("agent_status", [
        "agent status", "agents status", "how are the agents", "agent health",
        "show agents", "list agents", "agents overview"
    ], [
        "에이전트 상태", "에이전트", "에이전트 보여", "에이전트 목록"
    ]),
    ("stock_situation", [
        "stock", "stock situation", "stock report", "stock status",
        "stock summary", "kospi", "market"
    ], [
        "주식", "주식 상황", "주식 리포트", "코스피", "시장"
    ]),
    ("asset_situation", [
        "asset", "asset status", "asset report", "asset summary", "portfolio"
    ], [
        "자산", "자산 상태", "자산 리포트", "포트폴리오"
    ]),
    ("realty_situation", [
        "real estate", "realty", "property", "real estate status"
    ], [
        "부동산", "부동산 상태", "부동산 리포트"
    ]),
    # ========== Twins ==========
    ("twin_handoff", [
        "twin handoff", "today's handoff", "what did twins do", "overnight",
        "twin overnight", "morning handoff", "what happened overnight"
    ], [
        "트윈 인계", "오늘 인계", "트윈 보고", "밤사이", "야간 작업"
    ]),
    ("twin_summary", [
        "twin summary", "show twins", "list twins", "all twins",
        "twins status", "twins overview", "twins", "twin status", "my twins"
    ], [
        "트윈 요약", "트윈 목록", "모든 트윈", "트윈 상태", "트윈"
    ]),
    # ========== Approvals / Judgement ==========
    ("pending_approvals", [
        "pending approvals", "approvals", "needs review", "decisions",
        "judgement queue", "what needs approval"
    ], [
        "승인", "승인 대기", "검토", "결정 대기"
    ]),
    # ========== Absences ==========
    ("worker_absences", [
        "absences", "who's missing", "absent workers", "who hasn't logged in",
        "missing workers"
    ], [
        "결근", "안 온", "오늘 결근", "출근 안 한"
    ]),
    # ========== Broadcast ==========
    ("broadcast", [
        "broadcast", "send to all", "tell everyone", "announce", "send message to all"
    ], [
        "전체 메시지", "공지", "모두에게", "전체 공지"
    ]),
    # ========== Help / Unknown ==========
    ("help", [
        "what can you do", "help", "commands", "what do you do", "how can you help"
    ], [
        "뭐 할 수 있어", "도움", "명령어", "기능"
    ]),
    # ========== Navigation (open pages) ==========
    ("nav_reports", [
        "open reports", "go to reports", "show reports", "show me reports",
        "navigate to reports", "reports page"
    ], [
        "리포트 열어", "리포트 페이지", "보고 페이지", "보고서 열어"
    ]),
    ("nav_twins", [
        "open twins", "go to twins", "show me twins page", "twins page",
        "open twin page"
    ], [
        "트윈 페이지", "트윈 열어"
    ]),
    ("nav_agents", [
        "open agents", "go to agents", "agents page", "show agents page"
    ], [
        "에이전트 페이지", "에이전트 열어"
    ]),
    ("nav_workflows", [
        "open workflows", "go to workflows", "workflows page", "schedules page"
    ], [
        "워크플로우 열어", "스케줄 페이지"
    ]),
    ("nav_judgement", [
        "open judgement", "go to judgement", "judgement page",
        "open approvals page", "approvals page"
    ], [
        "판단 페이지", "승인 페이지", "승인 열어"
    ]),
    ("nav_a2a", [
        "open a2a", "go to a2a", "a2a monitor", "agent monitor"
    ], [
        "A2A 모니터", "에이전트 통신"
    ]),
    ("nav_meetings", [
        "open meetings", "go to meetings", "meetings page", "meeting room"
    ], [
        "미팅 페이지", "회의 페이지", "회의실 열어"
    ]),
    ("nav_meeting_notes", [
        "open meeting notes", "go to meeting notes", "meeting notes page"
    ], [
        "미팅 노트", "회의록 열어"
    ]),
    ("nav_control_room", [
        "open control room", "go to control room", "control room page"
    ], [
        "통제실", "컨트롤 룸", "통제 센터"
    ]),
    ("nav_task_board", [
        "open task board", "go to tasks", "task board", "kanban"
    ], [
        "태스크 보드", "작업 보드", "칸반"
    ]),
    ("nav_channels", [
        "open channels", "channels page", "go to channels"
    ], [
        "채널 페이지", "채널 열어"
    ]),
    ("nav_settings", [
        "open settings", "go to settings", "settings page"
    ], [
        "설정 페이지", "설정 열어"
    ]),
    ("nav_dashboard", [
        "go home", "go to dashboard", "open dashboard", "back to home",
        "main page"
    ], [
        "홈으로", "대시보드", "메인 페이지", "홈 페이지"
    ]),
    # ========== Tasks the assistant can DO ==========
    ("trigger_daily_report", [
        "generate daily report", "create daily report", "compose daily report",
        "run daily report", "make daily report"
    ], [
        "데일리 리포트 생성", "일간 보고서 만들어"
    ]),
    ("trigger_weekly_report", [
        "generate weekly report", "create weekly report", "compose weekly report",
        "run weekly report"
    ], [
        "주간 리포트 생성", "주간 보고서 만들어"
    ]),
    ("approve_all_handoffs", [
        "approve all handoffs", "mark all handoffs reviewed", "review all handoffs"
    ], [
        "모든 인계 승인", "인계 모두 승인"
    ]),
    # ========== Send message to a specific twin ==========
    ("send_twin_message", [
        "send message to", "send a message to", "message to",
        "text to", "send text to",
        "tell", "say to", "notify", "ping", "let know",
    ], [
        "메시지 보내", "메시지를 보내", "전달", "전해", "알려줘"
    ]),
    # ========== Future integrations (honest about gaps) ==========
    ("send_email", [
        "send email", "send an email", "send mail", "email to"
    ], [
        "이메일 보내", "메일 전송"
    ]),
    ("schedule_meeting", [
        "schedule meeting", "book meeting", "create meeting", "schedule a meeting"
    ], [
        "미팅 예약", "회의 예약", "회의 잡아"
    ]),
]


# ---------------------------------------------------------------------------
# Language detection (rough — Korean has Hangul block U+AC00-U+D7A3)
# ---------------------------------------------------------------------------

def detect_language(text: str) -> str:
    """Rough language detection — returns 'ko' if Hangul majority, else 'en'."""
    if not text:
        return "en"
    hangul = sum(1 for c in text if 0xAC00 <= ord(c) <= 0xD7A3)
    return "ko" if hangul > len(text) / 4 else "en"


def _fuzzy_contains(text: str, keyword: str, threshold: float = 0.86) -> bool:
    """
    True if `keyword` appears in `text` (substring) OR — for SINGLE-word keywords
    only — a word in `text` is a close fuzzy match (handles typos like 'assest'
    for 'asset'). Multi-word keywords require an exact substring match to avoid
    false-matches like 'agent' triggering 'how are the agents'.
    Hangul keywords are substring-only.
    """
    if keyword in text:
        return True
    if any(0xAC00 <= ord(c) <= 0xD7A3 for c in keyword):
        return False
    # Multi-word keyword → must be exact substring
    if " " in keyword.strip():
        return False
    if len(keyword) < 5:
        return False
    from difflib import SequenceMatcher
    for tw in re.findall(r"[a-z']+", text):
        if abs(len(tw) - len(keyword)) > 2:
            continue
        if SequenceMatcher(None, keyword, tw).ratio() >= threshold:
            return True
    return False


def classify_voice_intent(text: str) -> tuple[str, dict]:
    """
    Match transcript to one of the known voice intents.
    Returns (intent_name, extracted_entities).
    Tolerant of typos for English keywords (e.g. 'assest' → 'asset').
    """
    t = text.lower().strip()

    for intent, en_kws, ko_kws in INTENT_PATTERNS:
        for kw in en_kws + ko_kws:
            if _fuzzy_contains(t, kw.lower()):
                # Extract simple entities (e.g. message text after "broadcast:")
                entities = {}
                if intent == "broadcast":
                    # Capture text after "broadcast" / "tell everyone" / "공지"
                    m = re.search(r"(?:broadcast|tell everyone|send to all|공지|모두에게)[:\s]+(.+)", text, re.I)
                    if m:
                        entities["message"] = m.group(1).strip(" .!?")
                return intent, entities

    return "unknown", {}


# ---------------------------------------------------------------------------
# Intent handlers — each returns a SHORT, voice-friendly reply
# ---------------------------------------------------------------------------

def _voice(en: str, ko: str, lang: str = "en") -> str:
    """Return reply in chosen language."""
    return ko if lang == "ko" else en


def _parse_twin_message(text: str) -> tuple[str, str]:
    """
    Extract (target_name, message_body) from natural-language input.
    Examples:
      "send message to Davronbek Twin: come to my office"
        → ("Davronbek Twin", "come to my office")
      "tell Kim that meeting at 3"
        → ("Kim", "meeting at 3")
      "다브론벡 트윈에게 메시지 보내 회의실로 와"
        → ("다브론벡 트윈", "회의실로 와")
    """
    import re as _re
    t = text.strip()

    # English patterns: "to <NAME>: <MSG>" or "to <NAME> that/saying <MSG>" or "tell <NAME> <MSG>"
    _TRIGGER = r"(?:send (?:a )?message to|send text to|text to|message to|tell|notify|let|ping|ask)"
    patterns = [
        rf"{_TRIGGER}\s+([A-Za-z가-힣\s]+?)\s*(?:[:,]|that|saying|to)\s+(.+)",
        rf"{_TRIGGER}\s+([A-Za-z가-힣\s]+?)\s*[:,]\s*(.+)",
        r"([A-Za-z가-힣\s]+?)\s*에게(?:\s+메시지)?(?:\s+(?:보내|전해))?\s*[:,]?\s*(.+)",
    ]
    for p in patterns:
        m = _re.search(p, t, _re.IGNORECASE)
        if m:
            name = m.group(1).strip(" .,?!")
            body = m.group(2).strip(" .,?!\"'")
            # Filter trailing "twin" word so search works whether user said "Davronbek" or "Davronbek Twin"
            return name, body

    # Last-resort: pull capitalized words after "to" / "tell"
    m = _re.search(r"(?:to|tell|notify|ping)\s+([A-Z][A-Za-z\s]+?)(?:\s|$)", t)
    if m:
        return m.group(1).strip(), ""
    return "", ""


def _polite_prefix(lang: str) -> str:
    """A polite acknowledgment to prepend to fast intent replies."""
    import random
    en_options = ["Sure, Boss. ", "Of course. ", "Right away. ", "Got it. ", ""]
    ko_options = ["네 보스, ", "알겠습니다, ", "바로 처리하겠습니다. ", "네, ", ""]
    return random.choice(ko_options if lang == "ko" else en_options)


def handle_daily_briefing(db: Session, lang: str) -> str:
    from services.twin_reports import generate_boss_briefing
    try:
        d = generate_boss_briefing(db)
    except Exception:
        return _voice(
            "Sorry, I couldn't fetch today's briefing right now.",
            "죄송합니다, 지금 오늘 브리핑을 가져올 수 없습니다.",
            lang,
        )

    completed = d.get("total_tasks_completed", 0)
    review    = d.get("items_pending_review", 0)
    active    = d.get("twins_active_overnight", 0)
    total     = d.get("total_twins", 0)
    alerts    = d.get("alerts", []) or []

    if lang == "ko":
        msg = f"오늘 상황입니다. 트윈 {total}명 중 {active}명이 밤사이 작업했고, 작업 {completed}개를 완료했습니다. 검토가 필요한 항목은 {review}개입니다."
        if alerts:
            msg += f" 주의 사항이 {len(alerts)}개 있습니다."
        return msg
    else:
        msg = f"Here's today's situation. {active} out of {total} twins worked overnight, completing {completed} tasks. {review} items need your review."
        if alerts:
            msg += f" There are {len(alerts)} alerts."
        return msg


def handle_weekly_report(db: Session, lang: str) -> str:
    from services.twin_reports import generate_weekly_update
    try:
        d = generate_weekly_update(db)
        stats = d.get("company_stats", {})
        tasks = stats.get("total_tasks_completed", 0)
        progress = stats.get("average_progress", 0)
    except Exception:
        return _voice("Couldn't fetch the weekly report.", "주간 리포트를 가져올 수 없습니다.", lang)

    return _voice(
        f"This week, the team completed {tasks} tasks with average progress of {progress} percent.",
        f"이번 주 팀은 작업 {tasks}개를 완료했고, 평균 진척률은 {progress}퍼센트입니다.",
        lang,
    )


def handle_agent_status(db: Session, lang: str) -> str:
    from db.models import CoreAgent
    agents = db.query(CoreAgent).all()
    if not agents:
        return _voice("No agents are registered yet.", "등록된 에이전트가 없습니다.", lang)

    active = sum(1 for a in agents if a.status == "active")
    total = len(agents)
    names = ", ".join(a.name for a in agents[:5])

    return _voice(
        f"You have {total} agents, {active} active. They are: {names}.",
        f"에이전트는 총 {total}명이며, {active}명이 활성 상태입니다. {names}입니다.",
        lang,
    )


def handle_domain_situation(db: Session, lang: str, domain: str) -> str:
    """Query agent of given type and read summary aloud."""
    from db.models import CoreAgent
    agent = db.query(CoreAgent).filter(CoreAgent.type == domain, CoreAgent.status == "active").first()
    if not agent:
        return _voice(
            f"No active {domain} agent found.",
            f"활성 {domain} 에이전트를 찾을 수 없습니다.",
            lang,
        )
    try:
        from adapters import get_adapter
        adapter = get_adapter(agent.type, agent.name, agent.endpoint_url or "", agent.is_mock)
        data = adapter.fetch_summary()
    except Exception:
        return _voice(
            f"I couldn't reach the {domain} agent.",
            f"{domain} 에이전트에 연결할 수 없습니다.",
            lang,
        )

    summary = (data or {}).get("summary") or (data or {}).get("status") or "no summary available"
    label = {
        "stock":  ("stock market", "주식 시장"),
        "asset":  ("asset portfolio", "자산"),
        "realty": ("real estate", "부동산"),
    }.get(domain, (domain, domain))

    return _voice(
        f"Here's the {label[0]} situation. {summary}",
        f"{label[1]} 상황입니다. {summary}",
        lang,
    )


def handle_twin_handoff(db: Session, lang: str) -> str:
    from db.models import TwinHandoff
    cutoff = datetime.utcnow() - timedelta(hours=24)
    handoffs = db.query(TwinHandoff).filter(TwinHandoff.created_at >= cutoff).all()
    total_completed = sum(len(h.tasks_completed or []) for h in handoffs)
    total_review    = sum(len(h.tasks_pending_review or []) for h in handoffs)

    return _voice(
        f"In the last 24 hours, {len(handoffs)} twins reported in. They completed {total_completed} tasks total, with {total_review} items waiting for your review.",
        f"지난 24시간 동안 트윈 {len(handoffs)}명이 보고했습니다. 총 {total_completed}개 작업을 완료했고, {total_review}개 항목이 검토 대기 중입니다.",
        lang,
    )


def handle_twin_summary(db: Session, lang: str) -> str:
    from db.models import DigitalTwin
    twins = db.query(DigitalTwin).all()
    active   = sum(1 for t in twins if t.mode == "active")
    shadow   = sum(1 for t in twins if t.mode == "shadow")
    working  = sum(1 for t in twins if t.status == "working")

    return _voice(
        f"You have {len(twins)} twins. {active} are in twin mode, {shadow} in assistant mode, and {working} are currently working.",
        f"트윈은 총 {len(twins)}명입니다. 트윈 모드 {active}명, 어시스턴트 모드 {shadow}명이며, 현재 작업 중인 트윈은 {working}명입니다.",
        lang,
    )


def handle_pending_approvals(db: Session, lang: str) -> str:
    from db.models import JudgementCase
    pending = db.query(JudgementCase).filter(JudgementCase.decision == "human_review_required").count()
    conditional = db.query(JudgementCase).filter(JudgementCase.decision == "conditional_approve").count()

    if pending == 0 and conditional == 0:
        return _voice("No approvals pending. You're all clear.", "승인 대기 항목이 없습니다. 모두 처리되었습니다.", lang)

    return _voice(
        f"You have {pending} cases needing human review and {conditional} conditional approvals.",
        f"검토가 필요한 건이 {pending}개, 조건부 승인이 {conditional}개 있습니다.",
        lang,
    )


def handle_worker_absences(db: Session, lang: str) -> str:
    from services.twin_reports import check_worker_absences
    try:
        absent = check_worker_absences(db, hours_threshold=24)
    except Exception:
        return _voice("I couldn't check absences.", "결근 정보를 확인할 수 없습니다.", lang)

    if not absent:
        return _voice("Everyone's logged in within the last 24 hours.", "지난 24시간 내 모두 접속했습니다.", lang)

    names = ", ".join(a.get("name", "Unknown") for a in absent[:5])
    return _voice(
        f"{len(absent)} workers haven't logged in for over 24 hours. They are: {names}.",
        f"{len(absent)}명이 24시간 이상 접속하지 않았습니다. {names}입니다.",
        lang,
    )


def handle_broadcast(db: Session, lang: str, message: str) -> str:
    if not message:
        return _voice(
            "What message should I broadcast?",
            "어떤 메시지를 전송할까요?",
            lang,
        )
    from db.models import DirectMessage, DigitalTwin
    from services.twin_notifications import notify
    twins = db.query(DigitalTwin).all()
    for t in twins:
        db.add(DirectMessage(twin_id=t.id, sender_type="boss", content=message))
        try:
            notify(db, t.id, "boss_message", "Message from Boss", message)
        except Exception:
            pass
    db.commit()

    return _voice(
        f"Broadcast sent to {len(twins)} workers. Message: {message}",
        f"{len(twins)}명의 워커에게 전송 완료했습니다. 메시지: {message}",
        lang,
    )


def handle_help(lang: str) -> str:
    return _voice(
        "I can give you today's briefing, weekly report, agent status, stock or asset or real estate situation, twin handoffs, pending approvals, worker absences, or send a broadcast. Just ask.",
        "오늘 브리핑, 주간 리포트, 에이전트 상태, 주식 자산 부동산 상황, 트윈 인계, 승인 대기, 결근 확인, 또는 전체 공지를 도와드릴 수 있습니다. 편하게 말씀해 주세요.",
        lang,
    )


# ---------------------------------------------------------------------------
# LLM fallback — for any question that doesn't match a known intent
# ---------------------------------------------------------------------------

def _llm_fallback(db: Session, user_text: str, lang: str) -> str:
    """
    When no intent matches, ask the LLM to respond as the boss's voice assistant.
    Keeps replies SHORT (voice-friendly) and in the requested language.
    """
    try:
        from services.llm_client import chat_completion_sync
        from db.models import DigitalTwin, CoreAgent
        from datetime import datetime, timezone, timedelta
    except Exception as e:
        return _voice(
            f"I couldn't reach the language model: {str(e)[:60]}",
            f"언어 모델에 연결할 수 없습니다: {str(e)[:60]}",
            lang,
        )

    # Brief platform context — keeps replies grounded
    try:
        twins_count = db.query(DigitalTwin).count()
        agents_count = db.query(CoreAgent).count()
    except Exception:
        twins_count = agents_count = 0

    # Live agent summaries — so LLM can answer asset/stock/realty queries even
    # when keyword matching missed (e.g. typos: "Assest" instead of "Asset")
    domain_lines: list[str] = []
    try:
        from db.models import CoreAgent as _CA
        from adapters import get_adapter as _get_adapter
        for domain in ("asset", "stock", "realty"):
            agent = db.query(_CA).filter(_CA.type == domain, _CA.status == "active").first()
            if not agent:
                continue
            try:
                adapter = _get_adapter(agent.type, agent.name, agent.endpoint_url or "", agent.is_mock)
                if hasattr(adapter, "fetch_summary"):
                    summary = adapter.fetch_summary().get("summary") or ""
                    if summary:
                        domain_lines.append(f"- {domain.title()} agent: {summary[:200]}")
            except Exception:
                pass
    except Exception:
        pass
    domains_block = "\n".join(domain_lines) if domain_lines else "(no live agent summaries available)"

    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst).strftime("%Y-%m-%d %H:%M KST")

    if lang == "ko":
        system = (
            f"당신은 VIP AI 플랫폼의 음성 비서 '챗봇'입니다. "
            f"보스(VIP)에게 짧고 자연스럽게, 60단어 이내로 한국어로 답변하세요. "
            f"마크다운, 목록, 코드 블록을 사용하지 마세요 — 음성으로 읽힐 답변입니다. "
            f"현재 시각: {now_kst}. 트윈 {twins_count}개, 에이전트 {agents_count}개 등록됨.\n\n"
            f"실시간 에이전트 데이터:\n{domains_block}\n\n"
            f"위 데이터에 답이 있으면 그 숫자를 그대로 사용하세요. 없으면 짧게 인정하세요 — 추측 금지."
        )
    else:
        system = (
            f"You are 'Chatbot', the voice assistant for the VIP AI platform. "
            f"Reply to the boss (VIP) in short, natural English — under 60 words. "
            f"NO markdown, NO bullet lists, NO code blocks — your reply will be spoken aloud. "
            f"Current time: {now_kst}. Platform has {twins_count} twins and {agents_count} registered agents.\n\n"
            f"Live agent data right now:\n{domains_block}\n\n"
            f"If the user asks about asset / stock / realty / portfolio, use the numbers above. "
            f"If the data isn't there, say so briefly — never invent numbers."
        )

    try:
        reply = chat_completion_sync(
            system_prompt=system,
            messages=[{"role": "user", "content": user_text}],
            max_tokens=200,
            temperature=0.6,
            model="claude-haiku-4-5",  # fastest + cheapest for short replies
        )
    except Exception as e:
        return _voice(
            f"I couldn't think about that: {str(e)[:60]}",
            f"답변을 생성할 수 없습니다: {str(e)[:60]}",
            lang,
        )

    # Strip any markdown that slipped through (defensive)
    reply = (reply or "").strip()
    reply = reply.replace("**", "").replace("##", "").replace("# ", "")
    if reply.startswith("[LLM unavailable]"):
        return _voice(
            "I couldn't reach the language model right now.",
            "지금 언어 모델에 연결할 수 없습니다.",
            lang,
        )
    return reply or _voice(
        "I'm not sure how to answer that.",
        "어떻게 답해야 할지 모르겠습니다.",
        lang,
    )


# ---------------------------------------------------------------------------
# Main entry — dispatch to handler
# ---------------------------------------------------------------------------

def handle_voice_command(db: Session, transcript: str, lang_pref: str = "auto") -> dict:
    """
    Returns:
      {
        "intent": str,
        "language": "en" or "ko",
        "reply": str,        # short voice-friendly text
        "speak": bool,       # whether to speak the reply
        "data": {...}        # optional structured data for UI
      }
    """
    if not transcript or not transcript.strip():
        return {"intent": "empty", "language": "en", "reply": "I didn't hear anything.", "speak": True, "data": {}}

    # Decide language
    if lang_pref in ("en", "ko"):
        lang = lang_pref
    else:
        lang = detect_language(transcript)

    intent, entities = classify_voice_intent(transcript)

    # Track whether we used LLM fallback for the response intent label
    was_fallback = (intent == "unknown")

    # Multi-phase response fields (frontend uses these to feel like a real assistant)
    ack_reply: Optional[str] = None       # spoken IMMEDIATELY (polite "yes Boss, doing it now")
    process_log: list[dict] = []          # animated step-by-step progress
    action = None                         # frontend executes (navigation, etc.)

    # Navigation intents — friendlier conversational replies
    NAV_MAP = {
        "nav_asset_agent":    ("/agents",         "Sure, opening the Asset Agent for you.",                    "네, 자산 에이전트 페이지를 엽니다."),
        "nav_stock_agent":    ("/agents",         "Of course, opening the Stock Agent.",                       "네, 주식 에이전트 페이지를 엽니다."),
        "nav_realty_agent":   ("/agents",         "On it — opening the Real Estate Agent.",                    "네, 부동산 에이전트 페이지를 엽니다."),
        "nav_reports":        ("/reports",        "Sure, opening the reports page for you now.",                "네, 리포트 페이지를 열어드릴게요."),
        "nav_twins":          ("/twins",          "Of course, taking you to the twins page.",                  "네, 트윈 페이지로 이동합니다."),
        "nav_agents":         ("/agents",         "On it — opening the agents page.",                          "네, 에이전트 페이지를 열어드릴게요."),
        "nav_workflows":      ("/workflows",      "Sure, here's the workflows page.",                          "네, 워크플로우 페이지를 엽니다."),
        "nav_judgement":      ("/judgement",      "Opening the judgement page so you can review approvals.",   "네, 승인 페이지를 열어드릴게요. 검토하실 수 있습니다."),
        "nav_a2a":            ("/a2a",            "Sure, opening the A2A agent monitor for you.",              "네, A2A 에이전트 모니터를 엽니다."),
        "nav_meetings":       ("/meetings",       "Opening the meetings page now.",                            "네, 미팅 페이지로 이동합니다."),
        "nav_meeting_notes":  ("/meeting-notes",  "Sure, here are your meeting notes.",                        "네, 미팅 노트 페이지를 열어드릴게요."),
        "nav_control_room":   ("/control-room",   "Taking you to the control room.",                           "네, 통제실로 이동합니다."),
        "nav_task_board":     ("/task-board",     "Opening the task board for you.",                           "네, 태스크 보드를 엽니다."),
        "nav_channels":       ("/channels",       "Sure, opening channels.",                                   "네, 채널 페이지를 엽니다."),
        "nav_settings":       ("/settings",       "Opening settings.",                                         "네, 설정 페이지를 엽니다."),
        "nav_dashboard":      ("/",               "Sure, taking you back to the main dashboard.",              "네, 대시보드로 이동합니다."),
    }

    try:
        if intent in NAV_MAP:
            path, en_msg, ko_msg = NAV_MAP[intent]
            reply = _voice(en_msg, ko_msg, lang)
            action = {"type": "navigate", "to": path}
        elif intent == "trigger_daily_report":
            reply = _voice(
                "I'll trigger a fresh daily report. Check the reports page in a moment.",
                "데일리 리포트를 생성합니다. 잠시 후 리포트 페이지에서 확인하세요.",
                lang,
            )
            action = {"type": "trigger", "endpoint": "/reports/compose/auto-daily", "method": "POST"}
        elif intent == "trigger_weekly_report":
            reply = _voice(
                "I'll trigger a fresh weekly report.",
                "주간 리포트를 생성합니다.",
                lang,
            )
            action = {"type": "trigger", "endpoint": "/reports/compose/weekly", "method": "POST"}
        elif intent == "approve_all_handoffs":
            from db.models import TwinHandoff
            from datetime import datetime as dt, timedelta
            cutoff = dt.utcnow() - timedelta(hours=24)
            handoffs = db.query(TwinHandoff).filter(TwinHandoff.created_at >= cutoff, TwinHandoff.reviewed == False).all()
            for h in handoffs:
                h.reviewed = True
                h.reviewed_at = dt.utcnow()
            db.commit()
            reply = _voice(
                f"Approved {len(handoffs)} handoffs.",
                f"인계 {len(handoffs)}개를 승인했습니다.",
                lang,
            )
        elif intent == "send_twin_message":
            # === Multi-phase: ack → find twin → send → confirm ===
            from db.models import DigitalTwin, DirectMessage
            from services.twin_notifications import notify

            # Parse: "send message to <NAME> (saying|that|:)? <MSG>"
            target_name, msg_body = _parse_twin_message(transcript)
            if not target_name:
                reply = _voice(
                    "I didn't catch the recipient. Try saying 'send message to Davronbek Twin: come to my office'.",
                    "받는 사람을 못 들었습니다. '다브론벡 트윈에게 메시지 보내: 사무실로 와' 라고 말해주세요.",
                    lang,
                )
            else:
                # Polite ack — speak this FIRST while we find the twin and send
                ack_reply = _voice(
                    f"Of course, Boss. I'll send your message to {target_name} right away.",
                    f"네, 알겠습니다 보스. {target_name}에게 메시지를 바로 전달하겠습니다.",
                    lang,
                )
                process_log.append({"icon": "🔍", "label": _voice(f"Looking up {target_name}...", f"{target_name}을(를) 찾고 있습니다...", lang), "status": "running"})

                # Fuzzy-match twin by name — try full substring, then per-word match.
                # Strips noise words ("twin", "agent") so "Davronbek Agent" finds "Davronbek Twin".
                target_lower = target_name.lower()
                NOISE = {"twin", "agent", "the", "to", "for", "please"}
                target_words = [w for w in re.split(r"\s+", target_lower) if w and w not in NOISE]
                twins = db.query(DigitalTwin).all()
                twin = next((t for t in twins if target_lower in t.name.lower()), None)
                if not twin and target_words:
                    # Fall back: any meaningful word from target appears in twin name
                    for t in twins:
                        tname = t.name.lower()
                        if any(w in tname for w in target_words):
                            twin = t
                            break
                if not twin:
                    process_log[-1].update({"status": "error", "icon": "❌"})
                    reply = _voice(
                        f"I couldn't find a twin named {target_name}. Available twins are: " + ", ".join(t.name for t in twins[:5]),
                        f"{target_name}이라는 트윈을 찾을 수 없습니다. 사용 가능한 트윈: " + ", ".join(t.name for t in twins[:5]),
                        lang,
                    )
                else:
                    process_log[-1].update({"status": "done", "icon": "✓", "label": _voice(f"Found {twin.name}", f"{twin.name} 찾음", lang)})
                    process_log.append({"icon": "📤", "label": _voice(f"Sending message...", "메시지 전송 중...", lang), "status": "running"})

                    if not msg_body:
                        msg_body = _voice("Boss wants to see you", "보스가 보고 싶어합니다")

                    db.add(DirectMessage(twin_id=twin.id, sender_type="boss", content=msg_body))
                    process_log[-1].update({"status": "done", "icon": "✓", "label": _voice("Message saved to inbox", "메시지가 받은편지함에 저장됨", lang)})

                    process_log.append({"icon": "🔔", "label": _voice("Notifying twin...", "트윈에게 알리는 중...", lang), "status": "running"})
                    try:
                        notify(db, twin.id, "boss_message", "Message from Boss", msg_body)
                        process_log[-1].update({"status": "done", "icon": "✓", "label": _voice("Notification delivered", "알림 전송 완료", lang)})
                    except Exception:
                        process_log[-1].update({"status": "warn", "icon": "⚠", "label": _voice("Notification skipped", "알림 건너뜀", lang)})

                    db.commit()

                    reply = _voice(
                        f"All done. Your message has been delivered to {twin.name}: \"{msg_body[:80]}\"",
                        f"완료되었습니다. {twin.name}에게 메시지가 전달되었습니다: \"{msg_body[:80]}\"",
                        lang,
                    )
        elif intent == "send_email":
            reply = _voice(
                "Email integration isn't connected yet. I can broadcast a message to all workers instead — just say 'broadcast' followed by your message.",
                "이메일 연동은 아직 준비되지 않았습니다. 대신 워커 전체에게 메시지를 보낼 수 있습니다. '공지' 다음에 메시지를 말씀해 주세요.",
                lang,
            )
        elif intent == "schedule_meeting":
            reply = _voice(
                "Calendar isn't connected yet. You can create a multi-twin meeting from the meetings page — say 'open meetings'.",
                "캘린더 연동은 아직 준비되지 않았습니다. 미팅 페이지에서 다중 트윈 미팅을 만들 수 있습니다. '미팅 페이지'라고 말씀해 주세요.",
                lang,
            )
        elif intent == "daily_briefing":
            reply = handle_daily_briefing(db, lang)
        elif intent == "weekly_report":
            reply = handle_weekly_report(db, lang)
        elif intent == "monthly_report":
            reply = _voice(
                "Monthly comparison is available in the Twins page.",
                "월간 비교는 트윈 페이지에서 확인할 수 있습니다.",
                lang,
            )
        elif intent == "agent_status":
            reply = handle_agent_status(db, lang)
        elif intent == "stock_situation":
            reply = handle_domain_situation(db, lang, "stock")
        elif intent == "asset_situation":
            reply = handle_domain_situation(db, lang, "asset")
        elif intent == "realty_situation":
            reply = handle_domain_situation(db, lang, "realty")
        elif intent == "twin_handoff":
            reply = handle_twin_handoff(db, lang)
        elif intent == "twin_summary":
            reply = handle_twin_summary(db, lang)
        elif intent == "pending_approvals":
            reply = handle_pending_approvals(db, lang)
        elif intent == "worker_absences":
            reply = handle_worker_absences(db, lang)
        elif intent == "broadcast":
            reply = handle_broadcast(db, lang, entities.get("message", ""))
        elif intent == "help":
            reply = handle_help(lang)
        else:
            # === LLM Fallback ===
            # Unknown intent → ask Claude to handle as a general boss-assistant query.
            # Keeps the conversation feeling natural for ANY question.
            reply = _llm_fallback(db, transcript, lang)
    except Exception as e:
        reply = _voice(
            f"Something went wrong: {str(e)[:80]}",
            f"문제가 발생했습니다: {str(e)[:80]}",
            lang,
        )

    # Add polite acknowledgment prefix to query replies (not to LLM fallback or already-ack'd actions)
    if not was_fallback and not ack_reply and intent in (
        "daily_briefing", "weekly_report", "agent_status",
        "stock_situation", "asset_situation", "realty_situation",
        "twin_handoff", "twin_summary", "pending_approvals",
        "worker_absences", "broadcast", "approve_all_handoffs",
        "trigger_daily_report", "trigger_weekly_report",
    ):
        reply = _polite_prefix(lang) + reply

    return {
        "intent": "llm_chat" if was_fallback else intent,
        "language": lang,
        "reply": reply,
        "ack_reply": ack_reply,           # spoken first, before doing the work
        "process_log": process_log,        # animated progress steps
        "speak": True,
        "transcript": transcript,
        "action": action,
    }
