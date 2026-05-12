"""
chatbot_morning_report — daily aggregation + delivery of overnight activity.

What it does:
  - For each agent that had chatbot or voice activity in the last 24h:
    1. Aggregate metrics: conversations / messages / calls / escalations
    2. Pull the most important items (urgent, needs review, follow-ups)
    3. Have the LLM write a concise narrative summary
    4. Deliver via Telegram (using existing telegram_service)
    5. Future: also via AlimTalk template once Kakao approves the user's
       morning-report template

Cron schedule: 23:00 UTC daily = 08:00 KST next morning.
Registered in services/scheduler_service.py alongside the other 8 jobs.

Per-agent: respects each agent's escalationChannel from voice_escalation
registry, so VIP's report goes to VIP's boss and (future) Real Estate's
report goes to RE's Slack channel.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from db.base import SessionLocal
from db.models import (
    ChatbotConversation,
    ChatbotConversationAction,
    ChatbotCustomer,
    ChatbotMessage,
    VoiceCall,
)
from services.logger import log


# ============================================================================
#  Per-agent report generation
# ============================================================================

def generate_report_text(db: Session, agent_id: str) -> Optional[dict[str, Any]]:
    """Build the morning report payload for one agent.

    Returns None if there was no activity in the last 24h (no point sending
    an empty report; cuts notification noise).

    Returns:
      {
        "agent_id": str,
        "period": "yesterday",
        "stats": {...},
        "highlights": [...],  # items needing boss attention today
        "narrative": str,     # LLM-written paragraph summary
      }
    """
    since = datetime.utcnow() - timedelta(hours=24)

    # ── Chatbot activity ────────────────────────────────────────────────
    convs = (
        db.query(ChatbotConversation)
        .filter(
            ChatbotConversation.agent_id == agent_id,
            ChatbotConversation.last_message_at >= since,
        )
        .all()
    )
    total_convs = len(convs)
    chat_resolved = sum(1 for c in convs if c.status == "resolved")
    chat_escalated = sum(1 for c in convs if c.status == "escalated")
    chat_needs_review = sum(1 for c in convs if c.status == "needs_review")
    chat_needs_reply = sum(1 for c in convs if c.status == "needs_reply")

    # ── Voice call activity ─────────────────────────────────────────────
    calls = (
        db.query(VoiceCall)
        .filter(
            VoiceCall.agent_id == agent_id,
            VoiceCall.started_at >= since,
        )
        .all()
    )
    total_calls = len(calls)
    calls_completed = sum(1 for c in calls if c.status == "completed")
    calls_escalated = sum(1 for c in calls if c.status == "escalated")
    calls_missed = sum(1 for c in calls if c.status == "missed")

    if total_convs == 0 and total_calls == 0:
        return None

    # ── Highlights — items that need boss attention today ───────────────
    highlights: list[dict[str, Any]] = []

    # Escalated items get top priority
    for c in convs:
        if c.status == "escalated":
            cust = (
                db.query(ChatbotCustomer)
                .filter(ChatbotCustomer.id == c.customer_id)
                .first()
            )
            highlights.append({
                "priority": "high",
                "kind": "escalation",
                "channel": c.channel,
                "customer": (cust.name if cust else None) or "Unknown",
                "preview": c.preview or "",
                "reason": (c.escalation_json or {}).get("reason", ""),
            })

    # Items in needs_review (Boss-IN drafted, awaiting approval)
    for c in convs:
        if c.status == "needs_review":
            cust = (
                db.query(ChatbotCustomer)
                .filter(ChatbotCustomer.id == c.customer_id)
                .first()
            )
            draft = (c.suggested_reply_json or {}).get("text", "")
            highlights.append({
                "priority": "medium",
                "kind": "needs_review",
                "channel": c.channel,
                "customer": (cust.name if cust else None) or "Unknown",
                "preview": c.preview or "",
                "draft": draft[:200],
            })

    # Missed calls
    for c in calls:
        if c.status == "missed":
            highlights.append({
                "priority": "medium",
                "kind": "missed_call",
                "customer": c.caller_name or c.caller_number or "Unknown",
                "preview": f"Missed inbound call ({c.caller_number or 'no caller-ID'})",
            })

    # Limit to top 10 highlights
    highlights = highlights[:10]

    # ── LLM narrative summary ───────────────────────────────────────────
    narrative = _build_narrative(
        agent_id=agent_id,
        stats={
            "total_convs": total_convs,
            "chat_resolved": chat_resolved,
            "chat_escalated": chat_escalated,
            "chat_needs_review": chat_needs_review,
            "chat_needs_reply": chat_needs_reply,
            "total_calls": total_calls,
            "calls_completed": calls_completed,
            "calls_escalated": calls_escalated,
            "calls_missed": calls_missed,
        },
        highlights=highlights,
    )

    return {
        "agent_id": agent_id,
        "period": "yesterday (last 24h)",
        "stats": {
            "totalConversations": total_convs,
            "chatResolved": chat_resolved,
            "chatEscalated": chat_escalated,
            "chatNeedsReview": chat_needs_review,
            "chatNeedsReply": chat_needs_reply,
            "totalCalls": total_calls,
            "callsCompleted": calls_completed,
            "callsEscalated": calls_escalated,
            "callsMissed": calls_missed,
        },
        "highlights": highlights,
        "narrative": narrative,
    }


def _build_narrative(
    agent_id: str,
    stats: dict[str, Any],
    highlights: list[dict[str, Any]],
) -> str:
    """Generate a 2-3 sentence Korean narrative via Claude Haiku.
    Falls back to a templated summary on LLM error."""
    fallback = _fallback_narrative(stats, highlights)
    try:
        from services.llm_client import chat_completion_sync
        prompt = """You are summarizing yesterday's customer-service activity for a Korean real-estate business owner.

Write a 2-3 sentence summary IN KOREAN that:
- Mentions total conversations + calls
- Highlights anything urgent (escalations, missed calls)
- Notes how many items need the owner's attention today

Be warm and concise. Start with the morning greeting "안녕하세요 보스님,"."""

        body = (
            f"Stats:\n"
            f"- 전체 대화: {stats['total_convs']}건 (해결: {stats['chat_resolved']}, "
            f"검토 대기: {stats['chat_needs_review']}, 긴급: {stats['chat_escalated']})\n"
            f"- 전체 통화: {stats['total_calls']}건 (완료: {stats['calls_completed']}, "
            f"긴급: {stats['calls_escalated']}, 부재중: {stats['calls_missed']})\n"
            f"\nHighlights ({len(highlights)}건):\n"
            + "\n".join(
                f"- {h.get('kind')}: {h.get('customer')} — {h.get('preview', '')[:100]}"
                for h in highlights[:5]
            )
        )

        text = chat_completion_sync(
            prompt,
            [{"role": "user", "content": body}],
            model="claude-haiku-4-5",
        )
        return (text or "").strip() or fallback
    except Exception as e:
        log.warning(f"chatbot_morning_report: LLM narrative failed: {e}")
        return fallback


def _fallback_narrative(stats: dict[str, Any], highlights: list[dict[str, Any]]) -> str:
    needs_attention = sum(1 for h in highlights if h.get("priority") == "high") + stats["chat_needs_review"]
    return (
        f"안녕하세요 보스님, 어제 활동 요약입니다. "
        f"대화 {stats['total_convs']}건, 통화 {stats['total_calls']}건 처리되었습니다. "
        f"{'오늘 확인이 필요한 항목이 ' + str(needs_attention) + '건 있습니다.' if needs_attention else '특별히 확인이 필요한 항목은 없습니다.'}"
    )


# ============================================================================
#  Delivery — Telegram first; AlimTalk later (after user's template approval)
# ============================================================================

def deliver_report(agent_id: str, report: dict[str, Any]) -> bool:
    """Send the report to the agent's escalation channel.
    Returns True on success, False on any delivery error."""
    message_body = _format_for_telegram(report)
    try:
        from services.voice_escalation import get_escalation_channel
        from services.telegram_service import send_message
    except Exception as e:
        log.warning(f"chatbot_morning_report: delivery imports failed: {e}")
        return False

    channel = get_escalation_channel(agent_id)
    kind = channel.get("kind", "none")
    if kind == "telegram":
        chat_id = channel.get("chatId", "") or os.getenv("TELEGRAM_BOSS_CHAT_ID", "")
        if not chat_id:
            log.warning(f"chatbot_morning_report: no chat_id for {agent_id}")
            return False
        return bool(send_message(chat_id, message_body, parse_mode=None))
    if kind == "none":
        # Fall back to global TELEGRAM_BOSS_CHAT_ID if set
        chat_id = os.getenv("TELEGRAM_BOSS_CHAT_ID", "")
        if chat_id:
            return bool(send_message(chat_id, message_body, parse_mode=None))
    log.info(
        f"chatbot_morning_report: skipping delivery — channel kind {kind} not supported yet",
        extra={"action": "chatbot_morning_report.skipped"},
    )
    return False


def _format_for_telegram(report: dict[str, Any]) -> str:
    """Plain-text message body for Telegram. Keeps it short and scannable."""
    stats = report.get("stats", {})
    highlights = report.get("highlights", [])
    body = [
        "🌅 모닝 리포트 — " + report.get("agent_id", "").upper(),
        "",
        report.get("narrative", ""),
        "",
        f"📊 통계 (지난 24시간):",
        f"  • 대화 {stats.get('totalConversations', 0)}건 "
        f"(해결 {stats.get('chatResolved', 0)} / 검토 {stats.get('chatNeedsReview', 0)} / 긴급 {stats.get('chatEscalated', 0)})",
        f"  • 통화 {stats.get('totalCalls', 0)}건 "
        f"(완료 {stats.get('callsCompleted', 0)} / 긴급 {stats.get('callsEscalated', 0)} / 부재중 {stats.get('callsMissed', 0)})",
    ]
    if highlights:
        body.append("")
        body.append("⚠️ 오늘 확인 필요:")
        for h in highlights[:5]:
            kind = h.get("kind", "")
            customer = h.get("customer", "")
            preview = (h.get("preview") or "")[:80]
            icon = {"escalation": "🚨", "needs_review": "✏️", "missed_call": "📵"}.get(kind, "•")
            body.append(f"  {icon} {customer}: {preview}")
        if len(highlights) > 5:
            body.append(f"  ... and {len(highlights) - 5} more (check dashboard)")
    body.append("")
    body.append("자세한 내용은 대시보드 /chatbot 에서 확인하세요.")
    return "\n".join(body)


# ============================================================================
#  Scheduler entry — runs once per day
# ============================================================================

def deliver_morning_reports_all_agents() -> dict[str, int]:
    """Cron entry. Builds + delivers reports for every agent that had
    activity in the last 24h. Returns {sent, skipped, errors} for telemetry.

    Scheduled in services/scheduler_service.py at 23:00 UTC (08:00 KST next day).
    """
    db = SessionLocal()
    sent = 0
    skipped = 0
    errors = 0
    try:
        # Find every agent_id that had any activity in last 24h
        since = datetime.utcnow() - timedelta(hours=24)
        agent_ids: set[str] = set()
        for row in (
            db.query(distinct(ChatbotConversation.agent_id))
            .filter(ChatbotConversation.last_message_at >= since)
            .all()
        ):
            agent_ids.add(row[0])
        for row in (
            db.query(distinct(VoiceCall.agent_id))
            .filter(VoiceCall.started_at >= since)
            .all()
        ):
            agent_ids.add(row[0])

        for agent_id in agent_ids:
            try:
                report = generate_report_text(db, agent_id)
                if report is None:
                    skipped += 1
                    continue
                ok = deliver_report(agent_id, report)
                if ok:
                    sent += 1
                else:
                    errors += 1
            except Exception as e:
                log.warning(
                    f"chatbot_morning_report: failed for {agent_id}: {e}",
                    extra={"action": "chatbot_morning_report.agent_error"},
                )
                errors += 1
    finally:
        db.close()

    log.info(
        f"chatbot_morning_report: cron tick — sent {sent}, skipped {skipped}, errors {errors}",
        extra={"action": "chatbot_morning_report.cron_done"},
    )
    return {"sent": sent, "skipped": skipped, "errors": errors}
