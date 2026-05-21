"""
assistant_tools — registry of tools the LLM-driven assistant can call.

Each tool is:
  - a Python function that takes JSON-serializable args and returns a dict
  - registered with a name, description, parameter schema, and a "kind"
    ("read" or "write")

Read tools execute immediately. Write tools require a permission gate
(returned by the agent loop as `proposed_action`; user confirms; backend
re-runs with confirmed=True).

The registry is INTROSPECTABLE: assistant_agent passes the schemas to the
LLM verbatim so the model knows what tools exist and what arguments to
pass. Adding a new tool = define it + add it to TOOL_REGISTRY. The LLM
picks it up immediately.
"""

from __future__ import annotations

from typing import Any, Callable, Optional
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from services.logger import log
from services.assistant_manifest import (
    get_all_pages, get_page_by_path, is_valid_path,
    get_external_agents, get_agent_by_name,
    pages_summary_for_llm, agents_summary_for_llm,
)


# ============================================================================
#  Tool schema
# ============================================================================

ToolFn = Callable[..., dict[str, Any]]


class Tool:
    """A capability the assistant can invoke."""
    def __init__(
        self,
        name: str,
        description: str,
        kind: str,                  # "read" | "write"
        parameters: dict,           # JSON-schema-style parameter spec
        fn: ToolFn,
        requires_confirmation: bool = False,
    ):
        self.name = name
        self.description = description
        self.kind = kind
        self.parameters = parameters
        self.fn = fn
        self.requires_confirmation = requires_confirmation or kind == "write"

    def schema(self) -> dict:
        """Return the LLM-facing schema for this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "parameters": self.parameters,
            "requires_confirmation": self.requires_confirmation,
        }


# ============================================================================
#  Universal tools (Phase 1)
# ============================================================================

def tool_navigate(path: str, query: str = "", **_kw) -> dict[str, Any]:
    """Validate the path against the manifest and return a navigate action.

    Pass `query` to apply page-level filters via URL search params.
    Examples:
      navigate("/reports", "filter=daily")    → /reports?filter=daily
      navigate("/task-board", "status=pending&twin=Davronbek")
      navigate("/twins", "mode=active")
    The page must read these params itself (most do via useSearchParams).
    """
    target_path = path
    if not is_valid_path(path):
        path_lower = (path or "").lower()
        match = None
        for p in get_all_pages():
            if path_lower in p["path"].lower() or path_lower in p["name"].lower():
                match = p["path"]; break
        if not match:
            return {
                "ok": False,
                "error": f"Unknown path '{path}'. See list_pages() for valid options.",
            }
        target_path = match
    final_url = target_path + (f"?{query}" if query else "")
    page = get_page_by_path(target_path)
    filter_msg = f" (filter: {query})" if query else ""
    return {
        "ok": True,
        "action": {"type": "navigate", "to": final_url},
        "message": f"Opening {page['name']}{filter_msg}.",
    }


def tool_open_portal(agent: str, **_kw) -> dict[str, Any]:
    """Open one of the external agent web apps (Asset / Stock / Realty)."""
    a = get_agent_by_name(agent)
    if not a:
        names = ", ".join(x["name"] for x in get_external_agents())
        return {
            "ok": False,
            "error": f"Unknown agent '{agent}'. Available: {names}.",
        }
    return {
        "ok": True,
        "action": {"type": "navigate", "to": a["portal_url"], "external": True},
        "message": f"Opening the {a['name']} Agent in a new tab.",
        "url": a["portal_url"],
    }


def tool_list_pages(**_kw) -> dict[str, Any]:
    """Return the full menu list for the LLM to answer "what pages exist"."""
    pages = [
        {"path": p["path"], "name": p["name"], "description": p["description"]}
        for p in get_all_pages(include_hidden=False)
    ]
    return {"ok": True, "pages": pages, "count": len(pages)}


def tool_what_can_you_do(**_kw) -> dict[str, Any]:
    """Return a summary of capabilities."""
    return {
        "ok": True,
        "summary": (
            "I can navigate to any VIP Agent page, open the external Asset/Stock/Realty apps, "
            "search your data (twins, customer conversations, reports, knowledge), fetch live "
            "stock and property data, and perform actions like sending DMs, broadcasts, "
            "scheduling meetings, approving handoffs — with your confirmation."
        ),
        "examples": [
            "open my chatbot inbox",
            "show me Davronbek's recent activity",
            "what's the KOSPI today",
            "send Davronbek: 회의실 3시",
            "approve all overnight handoffs",
            "generate today's report",
            "schedule a meeting with Kim tomorrow 10 AM",
        ],
    }


# ============================================================================
#  READ tools (Phase 2)
# ============================================================================

def tool_search_twin(name: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Find a twin by name fragment. Returns profile + recent activity stats."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import DigitalTwin
        name_q = (name or "").strip().lower()
        if not name_q:
            return {"ok": False, "error": "name required"}
        twins = db.query(DigitalTwin).all()
        matches = [t for t in twins if name_q in (t.name or "").lower()
                   or name_q in (t.owner_email or "").lower()]
        if not matches:
            return {"ok": False, "error": f"No twin matching '{name}'.", "count": 0}
        out = []
        for t in matches[:5]:
            out.append({
                "twin_id": t.id,
                "name": t.name,
                "owner_email": t.owner_email,
                "mode": t.mode,
                "status": t.status,
                "knowledge_count": getattr(t, "knowledge_count", None),
            })
        return {"ok": True, "matches": out, "count": len(matches)}
    except Exception as e:
        log.warning(f"tool_search_twin error: {e}")
        return {"ok": False, "error": str(e)[:200]}


def tool_twin_activity(name: str, hours: int = 24, db: Session = None, **_kw) -> dict[str, Any]:
    """Recent activity log for a twin (default last 24h)."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import DigitalTwin, TwinActivityLog
        name_q = (name or "").strip().lower()
        twins = db.query(DigitalTwin).all()
        twin = next((t for t in twins if name_q in (t.name or "").lower()), None)
        if not twin:
            return {"ok": False, "error": f"No twin matching '{name}'"}
        cutoff = datetime.utcnow() - timedelta(hours=int(hours))
        logs = (db.query(TwinActivityLog)
                .filter(TwinActivityLog.twin_id == twin.id,
                        TwinActivityLog.created_at >= cutoff)
                .order_by(TwinActivityLog.created_at.desc())
                .limit(20).all())
        return {
            "ok": True,
            "twin_name": twin.name,
            "twin_mode": twin.mode,
            "hours_window": hours,
            "activity_count": len(logs),
            "activities": [
                {"action": l.action, "summary": (l.summary or "")[:120],
                 "ts": l.created_at.isoformat() if l.created_at else None}
                for l in logs[:10]
            ],
        }
    except Exception as e:
        log.warning(f"tool_twin_activity error: {e}")
        return {"ok": False, "error": str(e)[:200]}


def tool_twin_tasks(name: str, status: str = None, db: Session = None, **_kw) -> dict[str, Any]:
    """Tasks assigned to a twin. Optional status filter (pending/in_progress/completed)."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import DigitalTwin, TwinTask as Task
        name_q = (name or "").strip().lower()
        twins = db.query(DigitalTwin).all()
        twin = next((t for t in twins if name_q in (t.name or "").lower()), None)
        if not twin:
            return {"ok": False, "error": f"No twin matching '{name}'"}
        q = db.query(Task).filter(Task.assigned_twin_id == twin.id)
        if status:
            q = q.filter(Task.status == status)
        tasks = q.order_by(Task.created_at.desc()).limit(20).all()
        return {
            "ok": True,
            "twin_name": twin.name,
            "filter_status": status,
            "task_count": len(tasks),
            "tasks": [
                {"id": t.id, "title": (t.title or "")[:80], "status": t.status,
                 "created": t.created_at.isoformat() if t.created_at else None}
                for t in tasks[:10]
            ],
        }
    except Exception as e:
        log.warning(f"tool_twin_tasks error: {e}")
        return {"ok": False, "error": str(e)[:200]}


def tool_search_conversations(
    query: str = "",
    channel: str = None,
    status: str = None,
    db: Session = None,
    **_kw,
) -> dict[str, Any]:
    """Search customer conversations by content text. Optional channel/status filter."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import ChatbotConversation, ChatbotCustomer, ChatbotMessage
        q = db.query(ChatbotConversation)
        if channel:
            q = q.filter(ChatbotConversation.channel == channel)
        if status:
            q = q.filter(ChatbotConversation.status == status)
        convs = q.order_by(ChatbotConversation.updated_at.desc()).limit(40).all()
        if query and query.strip():
            qlower = query.lower()
            filtered = []
            for c in convs:
                # Pull last 5 message texts for fuzzy search
                msgs = (db.query(ChatbotMessage)
                        .filter(ChatbotMessage.conversation_id == c.id)
                        .order_by(ChatbotMessage.created_at.desc()).limit(5).all())
                blob = " ".join(((m.text or "") + " " + (m.voice_transcript or "")) for m in msgs)
                if qlower in blob.lower():
                    filtered.append((c, blob[:140]))
            convs_with_match = filtered[:10]
        else:
            convs_with_match = [(c, "") for c in convs[:10]]
        out = []
        for c, snippet in convs_with_match:
            cust = db.query(ChatbotCustomer).filter(ChatbotCustomer.id == c.customer_id).first()
            out.append({
                "conversation_id": c.id,
                "channel": c.channel,
                "status": c.status,
                "customer_name": cust.name if cust else None,
                "customer_phone": cust.phone if cust else None,
                "updated": c.updated_at.isoformat() if c.updated_at else None,
                "snippet": snippet[:140] if snippet else None,
            })
        return {"ok": True, "count": len(out), "matches": out}
    except Exception as e:
        log.warning(f"tool_search_conversations error: {e}")
        return {"ok": False, "error": str(e)[:200]}


def tool_conversation_history(conversation_id: str, last_n: int = 10, db: Session = None, **_kw) -> dict[str, Any]:
    """Last N messages for a specific conversation."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import ChatbotMessage
        msgs = (db.query(ChatbotMessage)
                .filter(ChatbotMessage.conversation_id == conversation_id)
                .order_by(ChatbotMessage.created_at.desc())
                .limit(int(last_n)).all())
        msgs = list(reversed(msgs))
        return {
            "ok": True,
            "conversation_id": conversation_id,
            "count": len(msgs),
            "messages": [
                {"author": m.author, "text": (m.text or m.voice_transcript or "")[:200],
                 "ts": m.created_at.isoformat() if m.created_at else None}
                for m in msgs
            ],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_latest_report(type: str = "daily", db: Session = None, **_kw) -> dict[str, Any]:
    """Fetch the latest daily or weekly report summary."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import OrchReport as Report
        r = (db.query(Report)
             .filter(Report.report_type == type)
             .order_by(Report.created_at.desc()).first())
        if not r:
            return {"ok": False, "error": f"No {type} report found."}
        return {
            "ok": True,
            "type": type,
            "report_id": r.id,
            "title": r.title,
            "summary": (r.summary or "")[:600],
            "created": r.created_at.isoformat() if r.created_at else None,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_search_reports(query: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Search report titles + summaries by text."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import OrchReport as Report
        reports = db.query(Report).order_by(Report.created_at.desc()).limit(50).all()
        qlower = (query or "").lower()
        matches = []
        for r in reports:
            blob = (r.title or "") + " " + (r.summary or "")
            if qlower in blob.lower():
                matches.append({
                    "report_id": r.id,
                    "type": r.report_type,
                    "title": r.title,
                    "snippet": (r.summary or "")[:180],
                    "created": r.created_at.isoformat() if r.created_at else None,
                })
                if len(matches) >= 10:
                    break
        return {"ok": True, "count": len(matches), "matches": matches}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_agent_status(name: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Fetch live data summary from an external agent (Asset / Stock / Realty)."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import CoreAgent
        from adapters import get_adapter
        name_lower = (name or "").lower().strip()
        # Map friendly names → agent.type
        type_map = {"asset": "asset", "stock": "stock", "realty": "realty",
                    "real estate": "realty", "자산": "asset", "주식": "stock",
                    "부동산": "realty"}
        agent_type = type_map.get(name_lower)
        if not agent_type:
            return {"ok": False, "error": f"Unknown agent '{name}'. Use Asset/Stock/Realty."}
        ag = db.query(CoreAgent).filter(CoreAgent.type == agent_type,
                                        CoreAgent.status == "active").first()
        if not ag:
            return {"ok": False, "error": f"No active {agent_type} agent registered."}
        adapter = get_adapter(ag.type, ag.name, ag.endpoint_url or "", ag.is_mock)
        if not hasattr(adapter, "fetch_summary"):
            return {"ok": False, "error": "Adapter doesn't expose fetch_summary."}
        data = adapter.fetch_summary() or {}
        return {
            "ok": True,
            "agent": ag.name,
            "type": agent_type,
            "summary": (data.get("summary") or "")[:600],
            "data": {k: v for k, v in data.items() if k != "raw" and not isinstance(v, (list, dict))},
        }
    except Exception as e:
        log.warning(f"tool_agent_status error: {e}")
        return {"ok": False, "error": str(e)[:200]}


def tool_list_pending_approvals(db: Session = None, **_kw) -> dict[str, Any]:
    """Items in the judgement queue awaiting boss review."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import AuditJudgementCase as JudgementCase
        cases = (db.query(JudgementCase)
                 .filter(JudgementCase.decision == "human_review_required")
                 .order_by(JudgementCase.created_at.desc()).limit(10).all())
        return {
            "ok": True,
            "count": len(cases),
            "cases": [
                {"id": c.id, "title": (c.title or "")[:80],
                 "agent_type": c.agent_type, "severity": c.severity,
                 "created": c.created_at.isoformat() if c.created_at else None}
                for c in cases
            ],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_search_knowledge(query: str, twin: str = None, db: Session = None, **_kw) -> dict[str, Any]:
    """Search twin knowledge entries by text. Optional filter to one twin."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import TwinKnowledge, DigitalTwin
        q = db.query(TwinKnowledge)
        twin_name = None
        if twin:
            tn = (twin or "").strip().lower()
            twins = db.query(DigitalTwin).all()
            tw = next((t for t in twins if tn in (t.name or "").lower()), None)
            if tw:
                twin_name = tw.name
                q = q.filter(TwinKnowledge.twin_id == tw.id)
        entries = q.order_by(TwinKnowledge.created_at.desc()).limit(50).all()
        qlower = (query or "").lower()
        matches = []
        for e in entries:
            blob = (e.title or "") + " " + (e.body or "")
            if not qlower or qlower in blob.lower():
                matches.append({
                    "id": e.id,
                    "title": (e.title or "")[:80],
                    "body": (e.body or "")[:200],
                    "twin_id": e.twin_id,
                })
                if len(matches) >= 10:
                    break
        return {"ok": True, "count": len(matches), "twin_filter": twin_name, "matches": matches}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_count(entity: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Quick count of an entity type."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        e = (entity or "").lower().strip()
        if e in ("twins", "twin"):
            from db.models import DigitalTwin
            n = db.query(DigitalTwin).count()
            return {"ok": True, "entity": "twins", "count": n}
        if e in ("conversations", "conversation", "chats"):
            from db.models import ChatbotConversation
            n = db.query(ChatbotConversation).count()
            return {"ok": True, "entity": "conversations", "count": n}
        if e in ("needs_reply", "unread", "needs reply"):
            from db.models import ChatbotConversation
            n = db.query(ChatbotConversation).filter(
                ChatbotConversation.status == "needs_reply").count()
            return {"ok": True, "entity": "conversations_needing_reply", "count": n}
        if e in ("tasks", "task"):
            from db.models import TwinTask as Task
            n = db.query(Task).count()
            return {"ok": True, "entity": "tasks", "count": n}
        if e in ("reports", "report"):
            from db.models import OrchReport as Report
            n = db.query(Report).count()
            return {"ok": True, "entity": "reports", "count": n}
        if e in ("approvals", "approval", "pending", "judgements"):
            from db.models import AuditJudgementCase as JudgementCase
            n = db.query(JudgementCase).filter(
                JudgementCase.decision == "human_review_required").count()
            return {"ok": True, "entity": "pending_approvals", "count": n}
        if e in ("meetings", "meeting"):
            from db.models import Meeting
            n = db.query(Meeting).count()
            return {"ok": True, "entity": "meetings", "count": n}
        return {"ok": False, "error": f"Unknown entity '{entity}'. Try: twins, conversations, tasks, reports, approvals, meetings."}
    except Exception as ex:
        return {"ok": False, "error": str(ex)[:200]}


# ============================================================================
#  WRITE tools (Phase 3) — require permission gate, executed only after
#  the user confirms the proposed_action in the widget.
# ============================================================================

def _find_twin_by_name(db: Session, name: str):
    """Helper — fuzzy find a twin by partial name match."""
    from db.models import DigitalTwin
    n = (name or "").strip().lower()
    if not n:
        return None
    twins = db.query(DigitalTwin).all()
    return next((t for t in twins if n in (t.name or "").lower()), None)


# --- Communications ---

def tool_send_dm(twin_name: str, body: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Send a direct message from the boss to a specific twin."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import TwinMessage
        tw = _find_twin_by_name(db, twin_name)
        if not tw:
            return {"ok": False, "error": f"No twin matching '{twin_name}'"}
        msg = TwinMessage(
            twin_id=tw.id,
            sender="boss",
            body=body or "",
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)
        return {
            "ok": True,
            "message": f"✅ Sent DM to {tw.name}: \"{body[:100]}\"",
            "message_id": msg.id,
            "twin_name": tw.name,
        }
    except Exception as e:
        log.warning(f"tool_send_dm error: {e}")
        return {"ok": False, "error": str(e)[:200]}


def tool_send_email(to: str, subject: str, body: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Send an email via configured SMTP."""
    try:
        from services.auth_service import _send_smtp_email
        ok, err = _send_smtp_email(to, subject or "(no subject)", body or "")
        if ok:
            return {"ok": True, "message": f"✉️ Email sent to {to}"}
        return {"ok": False, "error": err or "Email failed"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_broadcast(body: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Send a message to ALL workers via the broadcast pipeline."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import DigitalTwin, TwinMessage
        twins = db.query(DigitalTwin).all()
        sent = 0
        for t in twins:
            msg = TwinMessage(twin_id=t.id, sender="boss", body=body or "")
            db.add(msg)
            sent += 1
        db.commit()
        return {
            "ok": True,
            "message": f"📢 Broadcast sent to {sent} workers",
            "recipient_count": sent,
        }
    except Exception as e:
        log.warning(f"tool_broadcast error: {e}")
        return {"ok": False, "error": str(e)[:200]}


def tool_kakao_reply(conversation_id: str, text: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Send a reply on a Kakao customer conversation (boss takes over)."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from services import chatbot_conversation_service as conv_service
        from services import kakao_client
        conv = conv_service.get_conversation(db, "vip", conversation_id)
        if not conv:
            return {"ok": False, "error": f"Conversation {conversation_id} not found"}
        customer = conv_service.get_customer(db, "vip", conv.customer_id)
        if customer and getattr(customer, "kakao_user_id", None):
            try:
                kakao_client.send_text(
                    agent_id="vip",
                    conversation_id=str(conv.id),
                    text=text,
                    receiver_uuid=customer.kakao_user_id,
                )
            except Exception as e:
                log.warning(f"kakao_reply send failed: {e}")
        conv_service.append_message(
            db, "vip", conv.id,
            author="boss", kind="text", text=text,
            bot_meta={"status": "boss-via-assistant"},
        )
        conv_service.patch_conversation(db, "vip", conv.id, status="bot_handling")
        return {"ok": True, "message": f"💬 Reply sent on conversation {str(conv.id)[:8]}"}
    except Exception as e:
        log.warning(f"tool_kakao_reply error: {e}")
        return {"ok": False, "error": str(e)[:200]}


# --- Reports ---

def tool_trigger_daily_report(db: Session = None, **_kw) -> dict[str, Any]:
    """Trigger a fresh daily report generation."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from services.twin_reports import generate_daily_report
        report = generate_daily_report(db)
        rid = getattr(report, "id", None) if report else None
        return {
            "ok": True,
            "message": "📊 Daily report generated.",
            "report_id": str(rid) if rid else None,
            "action": {"type": "navigate", "to": "/reports"},
        }
    except Exception as e:
        log.warning(f"tool_trigger_daily_report error: {e}")
        return {"ok": False, "error": str(e)[:200]}


def tool_trigger_weekly_report(db: Session = None, **_kw) -> dict[str, Any]:
    """Trigger a fresh weekly report generation."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from services.twin_reports import generate_weekly_update
        result = generate_weekly_update(db)
        return {
            "ok": True,
            "message": "📈 Weekly report generated.",
            "action": {"type": "navigate", "to": "/reports"},
        }
    except Exception as e:
        log.warning(f"tool_trigger_weekly_report error: {e}")
        return {"ok": False, "error": str(e)[:200]}


# --- Approvals ---

def tool_approve_handoff(handoff_id: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Approve a single overnight twin handoff."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import TwinHandoff
        h = db.query(TwinHandoff).filter(TwinHandoff.id == handoff_id).first()
        if not h:
            return {"ok": False, "error": f"Handoff {handoff_id} not found"}
        h.boss_decision = "approved"
        db.commit()
        return {"ok": True, "message": f"✅ Handoff {str(h.id)[:8]} approved"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_approve_all_pending(db: Session = None, **_kw) -> dict[str, Any]:
    """Approve all overnight handoffs currently pending review."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import TwinHandoff
        pending = db.query(TwinHandoff).filter(
            (TwinHandoff.boss_decision == None) | (TwinHandoff.boss_decision == "pending")
        ).all()
        n = 0
        for h in pending:
            h.boss_decision = "approved"
            n += 1
        db.commit()
        return {"ok": True, "message": f"✅ Approved {n} overnight handoffs", "count": n}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_reject_handoff(handoff_id: str, reason: str = "", db: Session = None, **_kw) -> dict[str, Any]:
    """Reject a handoff with an optional reason."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import TwinHandoff
        h = db.query(TwinHandoff).filter(TwinHandoff.id == handoff_id).first()
        if not h:
            return {"ok": False, "error": f"Handoff {handoff_id} not found"}
        h.boss_decision = "rejected"
        if reason and hasattr(h, "boss_notes"):
            h.boss_notes = reason
        db.commit()
        return {"ok": True, "message": f"❌ Handoff {str(h.id)[:8]} rejected"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Conversation management ---

def tool_resolve_conversation(conversation_id: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Mark a Kakao conversation as resolved (no more action needed)."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from services import chatbot_conversation_service as conv_service
        conv_service.patch_conversation(db, "vip", conversation_id, status="resolved")
        return {"ok": True, "message": f"✓ Conversation {conversation_id[:8]} resolved"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_take_over_conversation(conversation_id: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Take over a conversation — boss will reply manually."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from services import chatbot_conversation_service as conv_service
        conv_service.patch_conversation(db, "vip", conversation_id, status="needs_reply")
        return {
            "ok": True,
            "message": f"👤 Took over conversation {conversation_id[:8]}",
            "action": {"type": "navigate", "to": "/chatbot"},
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_escalate_conversation(conversation_id: str, reason: str = "", db: Session = None, **_kw) -> dict[str, Any]:
    """Flag a conversation as urgent."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from services import chatbot_conversation_service as conv_service
        conv_service.escalate_conversation(db, "vip", conversation_id,
                                           to="boss", reason=reason or "Manual escalation")
        return {"ok": True, "message": f"⚠️ Conversation {conversation_id[:8]} escalated"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Tasks ---

def tool_create_task(twin_name: str, title: str, body: str = "", db: Session = None, **_kw) -> dict[str, Any]:
    """Create a task and assign it to a twin."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import TwinTask
        tw = _find_twin_by_name(db, twin_name)
        if not tw:
            return {"ok": False, "error": f"No twin matching '{twin_name}'"}
        task = TwinTask(
            twin_id=tw.id,
            title=title or "(no title)",
            description=body or "",
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return {
            "ok": True,
            "message": f"➕ Task '{task.title[:60]}' assigned to {tw.name}",
            "task_id": task.id,
        }
    except Exception as e:
        log.warning(f"tool_create_task error: {e}")
        return {"ok": False, "error": str(e)[:200]}


def tool_cancel_task(task_id: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Cancel a pending task by ID."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import TwinTask
        t = db.query(TwinTask).filter(TwinTask.id == task_id).first()
        if not t:
            return {"ok": False, "error": f"Task {task_id} not found"}
        t.status = "cancelled"
        db.commit()
        return {"ok": True, "message": f"❌ Task {task_id[:8]} cancelled"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Meetings ---

def tool_schedule_meeting(participants: str, when: str = "", agenda: str = "",
                          db: Session = None, **_kw) -> dict[str, Any]:
    """Auto-create a multi-twin meeting room from natural-language participants list."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from services import twin_meeting_intent
        # Reuse the natural-language meeting creator
        free_text = f"meeting with {participants}"
        if when:
            free_text += f" at {when}"
        if agenda:
            free_text += f" about {agenda}"
        result = twin_meeting_intent.auto_create_meeting_from_text(db, free_text)
        if not result.get("ok"):
            return {"ok": False, "error": result.get("message") or "Meeting creation failed"}
        return {
            "ok": True,
            "message": result.get("message") or "📅 Meeting scheduled",
            "meeting_id": result.get("meeting_id"),
            "action": {"type": "navigate", "to": result.get("meeting_room_url", "/meetings")},
        }
    except Exception as e:
        log.warning(f"tool_schedule_meeting error: {e}")
        return {"ok": False, "error": str(e)[:200]}


def tool_cancel_meeting(meeting_id: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Cancel a meeting by ID."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import Meeting
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not m:
            return {"ok": False, "error": f"Meeting {meeting_id} not found"}
        m.status = "cancelled"
        db.commit()
        return {"ok": True, "message": f"❌ Meeting {meeting_id[:8]} cancelled"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Knowledge ---

def tool_add_knowledge(twin_name: str, title: str, body: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Add a knowledge entry to a twin's library."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import TwinKnowledge
        tw = _find_twin_by_name(db, twin_name)
        if not tw:
            return {"ok": False, "error": f"No twin matching '{twin_name}'"}
        k = TwinKnowledge(twin_id=tw.id, title=title or "(no title)", body=body or "")
        db.add(k)
        db.commit()
        db.refresh(k)
        return {
            "ok": True,
            "message": f"📝 Added knowledge '{k.title[:60]}' to {tw.name}",
            "knowledge_id": k.id,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_delete_knowledge(knowledge_id: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Delete a knowledge entry by ID."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import TwinKnowledge
        k = db.query(TwinKnowledge).filter(TwinKnowledge.id == knowledge_id).first()
        if not k:
            return {"ok": False, "error": f"Knowledge {knowledge_id} not found"}
        db.delete(k)
        db.commit()
        return {"ok": True, "message": f"🗑️ Knowledge {knowledge_id[:8]} deleted"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Modes ---

def tool_set_boss_mode(mode: str, hours: int = 24, db: Session = None, **_kw) -> dict[str, Any]:
    """Set Boss-IN / Boss-OUT mode override."""
    try:
        from services import chatbot_mode_detector
        m = (mode or "").lower().strip()
        if m not in ("in", "out", "auto"):
            return {"ok": False, "error": "mode must be 'in', 'out', or 'auto'"}
        chatbot_mode_detector.set_mode_override("vip", m, "via-assistant", expires_in_hours=int(hours))
        return {"ok": True, "message": f"🔧 Boss mode set to '{m}' for {hours}h"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_set_twin_mode(twin_name: str, mode: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Set a specific twin's mode (shadow/active/handoff)."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        m = (mode or "").lower().strip()
        if m not in ("shadow", "active", "handoff"):
            return {"ok": False, "error": "mode must be shadow/active/handoff"}
        tw = _find_twin_by_name(db, twin_name)
        if not tw:
            return {"ok": False, "error": f"No twin matching '{twin_name}'"}
        tw.mode = m
        db.commit()
        return {"ok": True, "message": f"🔧 {tw.name}'s mode set to '{m}'"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_semantic_search(query: str, db: Session = None, limit: int = 8, **_kw) -> dict[str, Any]:
    """Phase 7 — Cross-data search.

    Searches ALL relevant tables in ONE call:
      • Customer conversations (Kakao/Phone/SMS recent messages)
      • Twin knowledge entries
      • Orchestrator reports (daily/weekly)
      • Meeting minutes
      • Twin activity logs

    Returns a unified list ranked by match strength. Use this when the user
    asks vague "find anything about X" / "what was that thing we discussed"
    questions where they don't know which table to look in.

    Implementation: simple ILIKE substring search across each table. Future
    upgrade: replace with pgvector embeddings for true semantic similarity.
    """
    if not db:
        return {"ok": False, "error": "DB session required"}
    if not query or not query.strip():
        return {"ok": False, "error": "query required"}
    q = query.strip()
    qlike = f"%{q}%"
    results: list[dict] = []

    # --- Conversations ---
    try:
        from db.models import ChatbotConversation, ChatbotCustomer, ChatbotMessage
        msgs = (db.query(ChatbotMessage)
                .filter(ChatbotMessage.text.ilike(qlike))
                .order_by(ChatbotMessage.created_at.desc())
                .limit(limit).all())
        for m in msgs:
            conv = db.query(ChatbotConversation).filter(
                ChatbotConversation.id == m.conversation_id).first()
            cust = db.query(ChatbotCustomer).filter(
                ChatbotCustomer.id == conv.customer_id).first() if conv else None
            results.append({
                "source": "conversation",
                "id": str(m.conversation_id),
                "title": f"{cust.name if cust else '?'} ({conv.channel if conv else '?'})",
                "snippet": (m.text or "")[:160],
                "ts": m.created_at.isoformat() if m.created_at else None,
            })
    except Exception as e:
        log.warning(f"semantic_search conversations: {e}")

    # --- Knowledge ---
    try:
        from db.models import TwinKnowledge, DigitalTwin
        knowledge = (db.query(TwinKnowledge)
                     .filter((TwinKnowledge.title.ilike(qlike)) |
                             (TwinKnowledge.body.ilike(qlike)))
                     .order_by(TwinKnowledge.created_at.desc())
                     .limit(limit).all())
        for k in knowledge:
            tw = db.query(DigitalTwin).filter(DigitalTwin.id == k.twin_id).first()
            results.append({
                "source": "knowledge",
                "id": str(k.id),
                "title": f"{k.title or '(no title)'} [{tw.name if tw else '?'}]",
                "snippet": (k.body or "")[:160],
                "ts": k.created_at.isoformat() if k.created_at else None,
            })
    except Exception as e:
        log.warning(f"semantic_search knowledge: {e}")

    # --- Reports ---
    try:
        from db.models import OrchReport as Report
        reports = (db.query(Report)
                   .filter((Report.title.ilike(qlike)) |
                           (Report.summary.ilike(qlike)))
                   .order_by(Report.created_at.desc())
                   .limit(limit).all())
        for r in reports:
            results.append({
                "source": "report",
                "id": str(r.id),
                "title": f"{r.title} ({r.report_type})",
                "snippet": (r.summary or "")[:160],
                "ts": r.created_at.isoformat() if r.created_at else None,
            })
    except Exception as e:
        log.warning(f"semantic_search reports: {e}")

    # --- Meeting minutes ---
    try:
        from db.models import MeetingMinutes
        minutes = (db.query(MeetingMinutes)
                   .filter((MeetingMinutes.title.ilike(qlike)) |
                           (MeetingMinutes.summary.ilike(qlike)))
                   .order_by(MeetingMinutes.created_at.desc())
                   .limit(limit).all())
        for m in minutes:
            results.append({
                "source": "meeting_notes",
                "id": str(m.id),
                "title": m.title or "(untitled meeting)",
                "snippet": (m.summary or "")[:160],
                "ts": m.created_at.isoformat() if m.created_at else None,
            })
    except Exception as e:
        log.warning(f"semantic_search meeting_minutes: {e}")

    # --- Twin activity ---
    try:
        from db.models import TwinActivityLog
        activities = (db.query(TwinActivityLog)
                      .filter(TwinActivityLog.summary.ilike(qlike))
                      .order_by(TwinActivityLog.created_at.desc())
                      .limit(limit).all())
        for a in activities:
            results.append({
                "source": "twin_activity",
                "id": str(a.id),
                "title": f"{a.action} (twin)",
                "snippet": (a.summary or "")[:160],
                "ts": a.created_at.isoformat() if a.created_at else None,
            })
    except Exception as e:
        log.warning(f"semantic_search twin_activity: {e}")

    # Sort by recency desc and trim
    results.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return {
        "ok": True,
        "query": q,
        "count": len(results),
        "matches": results[:limit],
        "by_source": {
            "conversation": sum(1 for r in results if r["source"] == "conversation"),
            "knowledge": sum(1 for r in results if r["source"] == "knowledge"),
            "report": sum(1 for r in results if r["source"] == "report"),
            "meeting_notes": sum(1 for r in results if r["source"] == "meeting_notes"),
            "twin_activity": sum(1 for r in results if r["source"] == "twin_activity"),
        },
    }


def tool_latest_meeting_notes(count: int = 3, db: Session = None, **_kw) -> dict[str, Any]:
    """Latest real-world meeting notes with summaries."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import MeetingMinutes as MeetingNote
        notes = (db.query(MeetingNote)
                 .order_by(MeetingNote.created_at.desc())
                 .limit(int(count)).all())
        return {
            "ok": True,
            "count": len(notes),
            "notes": [
                {"id": n.id, "title": (n.title or "")[:80],
                 "summary": (n.summary or "")[:300],
                 "created": n.created_at.isoformat() if n.created_at else None}
                for n in notes
            ],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ============================================================================
#  Registry
# ============================================================================

TOOL_REGISTRY: dict[str, Tool] = {
    # --- Phase 1: Universal navigation tools ---
    "navigate": Tool(
        name="navigate",
        description=(
            "Navigate the dashboard to an internal page WITH OPTIONAL FILTERS. "
            "Use for ANY in-app page (chatbot, twins, reports, meetings, etc.). "
            "Pass `query` for page filters. Common filters:\n"
            "  /reports?filter=daily       (or weekly / cross / alerts / all)\n"
            "  /task-board?status=pending  (or in_progress / completed / blocked)\n"
            "  /task-board?twin=Davronbek\n"
            "  /twins?mode=active          (or shadow / handoff)\n"
            "  /chatbot?status=needs_reply (or resolved / escalated)\n"
            "  /chatbot?channel=kakao      (or phone / sms)\n"
            "  /judgement?status=pending\n"
            "If the user says 'open DAILY reports' / '주식 task board' / 'active twins', "
            "pick the right filter — don't just navigate to the base page."
        ),
        kind="read",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The route path, e.g. '/chatbot' or '/reports'"},
                "query": {"type": "string", "description": "Optional URL search params (e.g. 'filter=daily' or 'status=pending&twin=Kim')"},
            },
            "required": ["path"],
        },
        fn=tool_navigate,
    ),
    "open_portal": Tool(
        name="open_portal",
        description="Open one of the EXTERNAL agent web apps (Asset / Stock / Realty / Real Estate) in a NEW BROWSER TAB. Use for queries like 'open stock agent', 'show me asset app', 'I wanna see real estate agent', '자산 에이전트 열어', etc.",
        kind="read",
        parameters={
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent name: 'Asset', 'Stock', or 'Realty' (also accepts '자산', '주식', '부동산', 'real estate')"},
            },
            "required": ["agent"],
        },
        fn=tool_open_portal,
    ),
    "list_pages": Tool(
        name="list_pages",
        description="Return the list of all internal pages with paths, names, and descriptions. Use when the user asks 'what pages do you have' or 'what can I see'.",
        kind="read",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=tool_list_pages,
    ),
    "what_can_you_do": Tool(
        name="what_can_you_do",
        description="Describe the assistant's overall capabilities with example queries. Use for 'what can you do', 'help', 'capabilities'.",
        kind="read",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=tool_what_can_you_do,
    ),

    # --- Phase 2: READ tools (Notion-AI-style search) ---
    "search_twin": Tool(
        name="search_twin",
        description="Find a digital twin by name (partial match). Returns profile (mode, status, knowledge count).",
        kind="read",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Twin name or fragment"}},
            "required": ["name"],
        },
        fn=tool_search_twin,
    ),
    "twin_activity": Tool(
        name="twin_activity",
        description="Get a twin's recent activity log. Use for 'what did Davronbek do today', 'show Kim's overnight work'.",
        kind="read",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Twin name"},
                "hours": {"type": "integer", "description": "Look back this many hours (default 24)"},
            },
            "required": ["name"],
        },
        fn=tool_twin_activity,
    ),
    "twin_tasks": Tool(
        name="twin_tasks",
        description="Get tasks assigned to a twin. Optional status filter.",
        kind="read",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "blocked"]},
            },
            "required": ["name"],
        },
        fn=tool_twin_tasks,
    ),
    "search_conversations": Tool(
        name="search_conversations",
        description="Search customer conversations (KakaoTalk / Phone / SMS) by text or filter by channel/status. Returns matching conversations with snippets.",
        kind="read",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search in recent message content"},
                "channel": {"type": "string", "enum": ["kakao", "phone", "sms", "email"]},
                "status": {"type": "string", "enum": ["needs_reply", "bot_handling", "resolved", "escalated"]},
            },
            "required": [],
        },
        fn=tool_search_conversations,
    ),
    "conversation_history": Tool(
        name="conversation_history",
        description="Get the last N messages in a specific conversation by ID.",
        kind="read",
        parameters={
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string"},
                "last_n": {"type": "integer", "description": "How many recent messages to return (default 10)"},
            },
            "required": ["conversation_id"],
        },
        fn=tool_conversation_history,
    ),
    "latest_report": Tool(
        name="latest_report",
        description="Get the latest daily or weekly report with summary text. Use for 'what did the daily report say', 'latest weekly summary'.",
        kind="read",
        parameters={
            "type": "object",
            "properties": {"type": {"type": "string", "enum": ["daily", "weekly"], "description": "Report type"}},
            "required": [],
        },
        fn=tool_latest_report,
    ),
    "search_reports": Tool(
        name="search_reports",
        description="Search recent reports by text in title/summary.",
        kind="read",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        fn=tool_search_reports,
    ),
    "agent_status": Tool(
        name="agent_status",
        description="Fetch live data summary from an external agent (Asset / Stock / Realty). Returns market data, portfolio stats, vacancy rates, etc. Use for 'how is the stock market', 'what's the realty status'.",
        kind="read",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Agent name: Asset, Stock, or Realty"}},
            "required": ["name"],
        },
        fn=tool_agent_status,
    ),
    "list_pending_approvals": Tool(
        name="list_pending_approvals",
        description="List items in the judgement queue waiting for the boss to approve/reject.",
        kind="read",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=tool_list_pending_approvals,
    ),
    "search_knowledge": Tool(
        name="search_knowledge",
        description="Search the twin knowledge base by text. Optional filter to one twin.",
        kind="read",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "twin": {"type": "string", "description": "Optional twin name filter"},
            },
            "required": ["query"],
        },
        fn=tool_search_knowledge,
    ),
    "count": Tool(
        name="count",
        description="Quick count of entities. Pass 'twins' / 'conversations' / 'needs_reply' / 'tasks' / 'reports' / 'approvals' / 'meetings'.",
        kind="read",
        parameters={
            "type": "object",
            "properties": {"entity": {"type": "string"}},
            "required": ["entity"],
        },
        fn=tool_count,
    ),
    "latest_meeting_notes": Tool(
        name="latest_meeting_notes",
        description="Recent meeting notes (real-world recordings) with summaries.",
        kind="read",
        parameters={
            "type": "object",
            "properties": {"count": {"type": "integer", "description": "How many to return (default 3)"}},
            "required": [],
        },
        fn=tool_latest_meeting_notes,
    ),
    "semantic_search": Tool(
        name="semantic_search", kind="read",
        description=(
            "CROSS-DATA SEARCH — searches conversations, knowledge entries, "
            "reports, meeting notes, and twin activity logs ALL AT ONCE for "
            "text matching the query. Use when the user asks vague 'find "
            "anything about X' / 'what was that thing we discussed' / "
            "'어디서 봤더라' style questions where the right table isn't obvious."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for"},
                "limit": {"type": "integer", "description": "Max results per source (default 8)"},
            },
            "required": ["query"],
        },
        fn=tool_semantic_search,
    ),

    # ========================================================================
    # WRITE tools (Phase 3) — every execution requires user confirm in widget
    # ========================================================================

    "send_dm": Tool(
        name="send_dm", kind="write",
        description="Send a direct message from boss to a specific digital twin. Use when user says 'send Davronbek: ...' / 'tell Kim that ...' / '다브론벡에게 메시지 보내'.",
        parameters={
            "type": "object",
            "properties": {
                "twin_name": {"type": "string", "description": "Twin name (partial match OK)"},
                "body": {"type": "string", "description": "Message body"},
            },
            "required": ["twin_name", "body"],
        },
        fn=tool_send_dm,
    ),
    "send_email": Tool(
        name="send_email", kind="write",
        description="Send an email via SMTP. Use for 'email <recipient>: subject ...'.",
        parameters={
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "body"],
        },
        fn=tool_send_email,
    ),
    "broadcast": Tool(
        name="broadcast", kind="write",
        description="Send a message to ALL workers/twins at once. Use for 'broadcast: ...' / 'tell everyone ...' / '전체 공지 ...'.",
        parameters={
            "type": "object",
            "properties": {"body": {"type": "string"}},
            "required": ["body"],
        },
        fn=tool_broadcast,
    ),
    "kakao_reply": Tool(
        name="kakao_reply", kind="write",
        description="Send a reply on a specific Kakao customer conversation. Requires the conversation_id (the boss often gets this from current_path on /chatbot or a prior search_conversations call).",
        parameters={
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["conversation_id", "text"],
        },
        fn=tool_kakao_reply,
    ),
    "trigger_daily_report": Tool(
        name="trigger_daily_report", kind="write",
        description="Generate today's daily report on demand. Use for 'generate daily report', 'make today's report', '데일리 리포트 만들어'.",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=tool_trigger_daily_report,
    ),
    "trigger_weekly_report": Tool(
        name="trigger_weekly_report", kind="write",
        description="Generate this week's report on demand.",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=tool_trigger_weekly_report,
    ),
    "approve_handoff": Tool(
        name="approve_handoff", kind="write",
        description="Approve a single overnight handoff by ID.",
        parameters={
            "type": "object",
            "properties": {"handoff_id": {"type": "string"}},
            "required": ["handoff_id"],
        },
        fn=tool_approve_handoff,
    ),
    "approve_all_pending": Tool(
        name="approve_all_pending", kind="write",
        description="Approve ALL pending overnight handoffs at once. Use for 'approve all handoffs', 'approve everything overnight'.",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=tool_approve_all_pending,
    ),
    "reject_handoff": Tool(
        name="reject_handoff", kind="write",
        description="Reject a handoff with an optional reason.",
        parameters={
            "type": "object",
            "properties": {
                "handoff_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["handoff_id"],
        },
        fn=tool_reject_handoff,
    ),
    "resolve_conversation": Tool(
        name="resolve_conversation", kind="write",
        description="Mark a Kakao customer conversation as resolved.",
        parameters={
            "type": "object",
            "properties": {"conversation_id": {"type": "string"}},
            "required": ["conversation_id"],
        },
        fn=tool_resolve_conversation,
    ),
    "take_over_conversation": Tool(
        name="take_over_conversation", kind="write",
        description="Take over a Kakao conversation — boss will reply manually instead of AI auto-replying.",
        parameters={
            "type": "object",
            "properties": {"conversation_id": {"type": "string"}},
            "required": ["conversation_id"],
        },
        fn=tool_take_over_conversation,
    ),
    "escalate_conversation": Tool(
        name="escalate_conversation", kind="write",
        description="Flag a conversation as urgent — pings boss via configured channel.",
        parameters={
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["conversation_id"],
        },
        fn=tool_escalate_conversation,
    ),
    "create_task": Tool(
        name="create_task", kind="write",
        description="Create a new task and assign it to a twin.",
        parameters={
            "type": "object",
            "properties": {
                "twin_name": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["twin_name", "title"],
        },
        fn=tool_create_task,
    ),
    "cancel_task": Tool(
        name="cancel_task", kind="write",
        description="Cancel a pending task by ID.",
        parameters={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
        fn=tool_cancel_task,
    ),
    "schedule_meeting": Tool(
        name="schedule_meeting", kind="write",
        description="Schedule a multi-twin meeting from natural language. Pass participants (comma-separated names or text), optional when (time/date), optional agenda.",
        parameters={
            "type": "object",
            "properties": {
                "participants": {"type": "string"},
                "when": {"type": "string"},
                "agenda": {"type": "string"},
            },
            "required": ["participants"],
        },
        fn=tool_schedule_meeting,
    ),
    "cancel_meeting": Tool(
        name="cancel_meeting", kind="write",
        description="Cancel a scheduled meeting by ID.",
        parameters={
            "type": "object",
            "properties": {"meeting_id": {"type": "string"}},
            "required": ["meeting_id"],
        },
        fn=tool_cancel_meeting,
    ),
    "add_knowledge": Tool(
        name="add_knowledge", kind="write",
        description="Add a knowledge entry (something the twin should remember) to a twin's library. Use for 'teach Davronbek: ...' / 'remember for Kim: ...'.",
        parameters={
            "type": "object",
            "properties": {
                "twin_name": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["twin_name", "title", "body"],
        },
        fn=tool_add_knowledge,
    ),
    "delete_knowledge": Tool(
        name="delete_knowledge", kind="write",
        description="Delete a knowledge entry by ID.",
        parameters={
            "type": "object",
            "properties": {"knowledge_id": {"type": "string"}},
            "required": ["knowledge_id"],
        },
        fn=tool_delete_knowledge,
    ),
    "set_boss_mode": Tool(
        name="set_boss_mode", kind="write",
        description="Set Boss-IN / Boss-OUT mode override. Boss-IN = boss reviews chatbot drafts before send; Boss-OUT = AI replies autonomously.",
        parameters={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["in", "out", "auto"]},
                "hours": {"type": "integer", "description": "How long the override lasts (default 24)"},
            },
            "required": ["mode"],
        },
        fn=tool_set_boss_mode,
    ),
    "set_twin_mode": Tool(
        name="set_twin_mode", kind="write",
        description="Set a specific twin's mode (shadow=passive, active=working, handoff=preparing report).",
        parameters={
            "type": "object",
            "properties": {
                "twin_name": {"type": "string"},
                "mode": {"type": "string", "enum": ["shadow", "active", "handoff"]},
            },
            "required": ["twin_name", "mode"],
        },
        fn=tool_set_twin_mode,
    ),
}


# ============================================================================
#  Extended tools — covers the rest of the boss's manual operations
# ============================================================================

# --- Twin CRUD ---
def tool_create_twin(name: str, owner_email: str = "", db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        from db.models import DigitalTwin
        t = DigitalTwin(name=name, owner_email=owner_email or f"{name.lower()}@tripleh.co.kr",
                        mode="shadow", status="idle")
        db.add(t); db.commit(); db.refresh(t)
        return {"ok": True, "message": f"✅ Created twin '{t.name}' (id {t.id[:8]})", "twin_id": t.id}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def tool_delete_twin(twin_name: str, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        tw = _find_twin_by_name(db, twin_name)
        if not tw: return {"ok": False, "error": f"No twin matching '{twin_name}'"}
        twin_id = tw.id
        db.delete(tw); db.commit()
        return {"ok": True, "message": f"🗑️ Deleted twin {tw.name} ({twin_id[:8]})"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def tool_update_twin_owner(twin_name: str, owner_email: str, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        tw = _find_twin_by_name(db, twin_name)
        if not tw: return {"ok": False, "error": f"No twin matching '{twin_name}'"}
        tw.owner_email = owner_email; db.commit()
        return {"ok": True, "message": f"✏️ Updated {tw.name}'s owner to {owner_email}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def tool_list_twins(mode: str = None, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        from db.models import DigitalTwin
        q = db.query(DigitalTwin)
        if mode: q = q.filter(DigitalTwin.mode == mode)
        twins = q.all()
        return {"ok": True, "count": len(twins), "filter_mode": mode,
                "twins": [{"id": t.id, "name": t.name, "owner": t.owner_email,
                           "mode": t.mode, "status": t.status} for t in twins[:30]]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Task ops ---
def tool_update_task_status(task_id: str, status: str, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    valid = {"pending", "in_progress", "blocked", "completed", "cancelled"}
    if status not in valid: return {"ok": False, "error": f"status must be one of {valid}"}
    try:
        from db.models import TwinTask
        t = db.query(TwinTask).filter(TwinTask.id == task_id).first()
        if not t: return {"ok": False, "error": f"Task {task_id} not found"}
        t.status = status; db.commit()
        return {"ok": True, "message": f"✓ Task {task_id[:8]} → {status}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def tool_update_task_priority(task_id: str, priority: str, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        from db.models import TwinTask
        t = db.query(TwinTask).filter(TwinTask.id == task_id).first()
        if not t: return {"ok": False, "error": "Task not found"}
        if hasattr(t, "priority"): t.priority = priority; db.commit()
        return {"ok": True, "message": f"✓ Task {task_id[:8]} priority → {priority}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def tool_reassign_task(task_id: str, twin_name: str, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        from db.models import TwinTask
        tw = _find_twin_by_name(db, twin_name)
        if not tw: return {"ok": False, "error": f"No twin '{twin_name}'"}
        t = db.query(TwinTask).filter(TwinTask.id == task_id).first()
        if not t: return {"ok": False, "error": "Task not found"}
        t.twin_id = tw.id; db.commit()
        return {"ok": True, "message": f"↪️ Task {task_id[:8]} reassigned to {tw.name}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def tool_list_tasks_filtered(status: str = None, twin_name: str = None, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        from db.models import TwinTask
        q = db.query(TwinTask)
        if status: q = q.filter(TwinTask.status == status)
        if twin_name:
            tw = _find_twin_by_name(db, twin_name)
            if tw: q = q.filter(TwinTask.twin_id == tw.id)
        tasks = q.order_by(TwinTask.created_at.desc()).limit(30).all()
        return {"ok": True, "count": len(tasks), "filter": {"status": status, "twin": twin_name},
                "tasks": [{"id": t.id, "title": (t.title or "")[:60], "status": t.status} for t in tasks]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Knowledge ops ---
def tool_update_knowledge(knowledge_id: str, title: str = None, body: str = None, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        from db.models import TwinKnowledge
        k = db.query(TwinKnowledge).filter(TwinKnowledge.id == knowledge_id).first()
        if not k: return {"ok": False, "error": "Knowledge not found"}
        if title is not None: k.title = title
        if body is not None: k.body = body
        db.commit()
        return {"ok": True, "message": f"✏️ Knowledge {knowledge_id[:8]} updated"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def tool_list_twin_knowledge(twin_name: str, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        from db.models import TwinKnowledge
        tw = _find_twin_by_name(db, twin_name)
        if not tw: return {"ok": False, "error": f"No twin '{twin_name}'"}
        entries = (db.query(TwinKnowledge)
                   .filter(TwinKnowledge.twin_id == tw.id)
                   .order_by(TwinKnowledge.created_at.desc()).limit(50).all())
        return {"ok": True, "twin_name": tw.name, "count": len(entries),
                "entries": [{"id": e.id, "title": (e.title or "")[:60],
                             "body_preview": (e.body or "")[:120]} for e in entries]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Report ops ---
def tool_trigger_cross_agent_report(db: Session = None, **_kw) -> dict[str, Any]:
    try:
        import httpx
        r = httpx.post("http://localhost:8000/reports/compose/cross-agent",
                       json={"agent_types": ["asset", "stock"], "report_type": "cross_agent_summary"},
                       timeout=30)
        return {"ok": r.status_code < 400, "message": "📊 Cross-agent report queued.",
                "action": {"type": "navigate", "to": "/reports?filter=cross"}}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def tool_delete_report(report_id: str, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        from db.models import OrchReport as Report
        r = db.query(Report).filter(Report.id == report_id).first()
        if not r: return {"ok": False, "error": "Report not found"}
        db.delete(r); db.commit()
        return {"ok": True, "message": f"🗑️ Report {report_id[:8]} deleted"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Judgement ops ---
def tool_get_case_details(case_id: str, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        from db.models import AuditJudgementCase as JC
        c = db.query(JC).filter(JC.id == case_id).first()
        if not c: return {"ok": False, "error": "Case not found"}
        return {"ok": True, "case": {"id": c.id, "title": c.title, "severity": c.severity,
                                     "decision": c.decision, "agent_type": c.agent_type,
                                     "context": (str(getattr(c, "context_json", "") or ""))[:500]}}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Workflow ops ---
def tool_list_workflows(db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        from db.models import OrchScheduleRule
        rules = db.query(OrchScheduleRule).all()
        return {"ok": True, "count": len(rules),
                "workflows": [{"id": r.id, "name": r.name, "cron": r.cron,
                               "enabled": getattr(r, "enabled", True)} for r in rules]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def tool_trigger_workflow(workflow_id: str, db: Session = None, **_kw) -> dict[str, Any]:
    try:
        import httpx
        r = httpx.post(f"http://localhost:8000/workflows/{workflow_id}/run", timeout=10)
        return {"ok": r.status_code < 400, "message": f"▶️ Workflow {workflow_id[:8]} triggered"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def tool_set_workflow_enabled(workflow_id: str, enabled: bool, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        from db.models import OrchScheduleRule
        r = db.query(OrchScheduleRule).filter(OrchScheduleRule.id == workflow_id).first()
        if not r: return {"ok": False, "error": "Workflow not found"}
        if hasattr(r, "enabled"): r.enabled = bool(enabled); db.commit()
        verb = "enabled" if enabled else "disabled"
        return {"ok": True, "message": f"⚙️ Workflow {workflow_id[:8]} {verb}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Call ops ---
def tool_list_calls(limit: int = 10, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        # Calls are stored as conversations with channel="phone"
        from db.models import ChatbotConversation, ChatbotCustomer
        convs = (db.query(ChatbotConversation)
                 .filter(ChatbotConversation.channel == "phone")
                 .order_by(ChatbotConversation.updated_at.desc())
                 .limit(int(limit)).all())
        out = []
        for c in convs:
            cust = db.query(ChatbotCustomer).filter(ChatbotCustomer.id == c.customer_id).first()
            out.append({"id": c.id, "customer": cust.name if cust else None,
                        "phone": cust.phone if cust else None, "status": c.status,
                        "updated": c.updated_at.isoformat() if c.updated_at else None})
        return {"ok": True, "count": len(out), "calls": out}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Agent ops ---
def tool_ping_agent(agent_name: str, db: Session = None, **_kw) -> dict[str, Any]:
    if not db: return {"ok": False, "error": "DB required"}
    try:
        from db.models import CoreAgent
        a = db.query(CoreAgent).filter(CoreAgent.name.ilike(f"%{agent_name}%")).first()
        if not a: return {"ok": False, "error": f"Agent '{agent_name}' not found"}
        import httpx
        try:
            r = httpx.get(f"{a.endpoint_url}/health", timeout=8)
            return {"ok": r.status_code == 200,
                    "message": f"🏓 {a.name}: HTTP {r.status_code} from {a.endpoint_url}"}
        except Exception as e:
            return {"ok": False, "message": f"🏓 {a.name}: unreachable ({str(e)[:80]})"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --- Mode ops ---
def tool_get_current_mode(**_kw) -> dict[str, Any]:
    try:
        from services import chatbot_mode_detector
        mode, auto = chatbot_mode_detector.get_mode("vip")
        return {"ok": True, "mode": mode, "auto_detected": auto,
                "message": f"Current Boss mode: {mode}{' (auto-detected)' if auto else ' (manual override)'}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ============================================================================
#  Register the 14 new tools
# ============================================================================

TOOL_REGISTRY.update({
    "create_twin": Tool(
        name="create_twin", kind="write",
        description="Create a new digital twin (employee AI). Pass name and optional owner_email.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string"},
            "owner_email": {"type": "string"},
        }, "required": ["name"]},
        fn=tool_create_twin,
    ),
    "delete_twin": Tool(
        name="delete_twin", kind="write",
        description="Delete a digital twin by name. DESTRUCTIVE — removes all associated knowledge / tasks / activity.",
        parameters={"type": "object", "properties": {
            "twin_name": {"type": "string"},
        }, "required": ["twin_name"]},
        fn=tool_delete_twin,
    ),
    "update_twin_owner": Tool(
        name="update_twin_owner", kind="write",
        description="Change which worker owns a twin (transfer ownership).",
        parameters={"type": "object", "properties": {
            "twin_name": {"type": "string"},
            "owner_email": {"type": "string"},
        }, "required": ["twin_name", "owner_email"]},
        fn=tool_update_twin_owner,
    ),
    "list_twins": Tool(
        name="list_twins", kind="read",
        description="List all twins. Optional `mode` filter (shadow/active/handoff).",
        parameters={"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["shadow", "active", "handoff"]},
        }, "required": []},
        fn=tool_list_twins,
    ),

    "update_task_status": Tool(
        name="update_task_status", kind="write",
        description="Move a task between status columns: pending / in_progress / blocked / completed / cancelled.",
        parameters={"type": "object", "properties": {
            "task_id": {"type": "string"},
            "status": {"type": "string", "enum": ["pending", "in_progress", "blocked", "completed", "cancelled"]},
        }, "required": ["task_id", "status"]},
        fn=tool_update_task_status,
    ),
    "update_task_priority": Tool(
        name="update_task_priority", kind="write",
        description="Change a task's priority (low / normal / high / urgent).",
        parameters={"type": "object", "properties": {
            "task_id": {"type": "string"},
            "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
        }, "required": ["task_id", "priority"]},
        fn=tool_update_task_priority,
    ),
    "reassign_task": Tool(
        name="reassign_task", kind="write",
        description="Reassign a task to a different twin.",
        parameters={"type": "object", "properties": {
            "task_id": {"type": "string"},
            "twin_name": {"type": "string"},
        }, "required": ["task_id", "twin_name"]},
        fn=tool_reassign_task,
    ),
    "list_tasks_filtered": Tool(
        name="list_tasks_filtered", kind="read",
        description="List tasks with optional filters: status, twin_name. Pass either, both, or neither.",
        parameters={"type": "object", "properties": {
            "status": {"type": "string"},
            "twin_name": {"type": "string"},
        }, "required": []},
        fn=tool_list_tasks_filtered,
    ),

    "update_knowledge": Tool(
        name="update_knowledge", kind="write",
        description="Edit an existing knowledge entry. Pass new title and/or body.",
        parameters={"type": "object", "properties": {
            "knowledge_id": {"type": "string"},
            "title": {"type": "string"},
            "body": {"type": "string"},
        }, "required": ["knowledge_id"]},
        fn=tool_update_knowledge,
    ),
    "list_twin_knowledge": Tool(
        name="list_twin_knowledge", kind="read",
        description="List all knowledge entries belonging to a specific twin.",
        parameters={"type": "object", "properties": {
            "twin_name": {"type": "string"},
        }, "required": ["twin_name"]},
        fn=tool_list_twin_knowledge,
    ),

    "trigger_cross_agent_report": Tool(
        name="trigger_cross_agent_report", kind="write",
        description="Compose a fresh cross-agent summary report (Asset + Stock combined).",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=tool_trigger_cross_agent_report,
    ),
    "delete_report": Tool(
        name="delete_report", kind="write",
        description="Delete a report by ID. DESTRUCTIVE.",
        parameters={"type": "object", "properties": {
            "report_id": {"type": "string"},
        }, "required": ["report_id"]},
        fn=tool_delete_report,
    ),

    "get_case_details": Tool(
        name="get_case_details", kind="read",
        description="Fetch full details of a judgement / approval case by ID.",
        parameters={"type": "object", "properties": {
            "case_id": {"type": "string"},
        }, "required": ["case_id"]},
        fn=tool_get_case_details,
    ),

    "list_workflows": Tool(
        name="list_workflows", kind="read",
        description="List scheduled cron jobs / workflows with their cron expressions and enabled state.",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=tool_list_workflows,
    ),
    "trigger_workflow": Tool(
        name="trigger_workflow", kind="write",
        description="Manually run a workflow now (bypass cron schedule).",
        parameters={"type": "object", "properties": {
            "workflow_id": {"type": "string"},
        }, "required": ["workflow_id"]},
        fn=tool_trigger_workflow,
    ),
    "set_workflow_enabled": Tool(
        name="set_workflow_enabled", kind="write",
        description="Enable or disable a scheduled workflow.",
        parameters={"type": "object", "properties": {
            "workflow_id": {"type": "string"},
            "enabled": {"type": "boolean"},
        }, "required": ["workflow_id", "enabled"]},
        fn=tool_set_workflow_enabled,
    ),

    "list_calls": Tool(
        name="list_calls", kind="read",
        description="Recent phone calls (inbound/outbound) with customer name and status.",
        parameters={"type": "object", "properties": {
            "limit": {"type": "integer"},
        }, "required": []},
        fn=tool_list_calls,
    ),

    "ping_agent": Tool(
        name="ping_agent", kind="read",
        description="Health-check a registered domain agent (Asset / Stock / Realty) by hitting its /health endpoint.",
        parameters={"type": "object", "properties": {
            "agent_name": {"type": "string"},
        }, "required": ["agent_name"]},
        fn=tool_ping_agent,
    ),

    "get_current_mode": Tool(
        name="get_current_mode", kind="read",
        description="Show the current Boss mode (in / out / auto) for the VIP chatbot.",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=tool_get_current_mode,
    ),
})


def list_tool_schemas() -> list[dict]:
    """Return all tool schemas for the LLM."""
    return [t.schema() for t in TOOL_REGISTRY.values()]


def get_tool(name: str) -> Optional[Tool]:
    return TOOL_REGISTRY.get(name)


def execute_tool(name: str, args: dict, db: Session = None) -> dict[str, Any]:
    """Execute a tool by name with the given args. Returns the tool's
    structured result. NEVER raises — always returns a dict with 'ok' key."""
    tool = get_tool(name)
    if not tool:
        return {"ok": False, "error": f"Unknown tool '{name}'"}
    try:
        result = tool.fn(**(args or {}), db=db)
        if not isinstance(result, dict):
            return {"ok": False, "error": f"Tool '{name}' returned non-dict"}
        return result
    except TypeError as e:
        return {"ok": False, "error": f"Bad args for {name}: {e}"}
    except Exception as e:
        log.warning(f"execute_tool {name} failed: {e}")
        return {"ok": False, "error": str(e)[:300]}
