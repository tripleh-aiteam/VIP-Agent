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

def tool_navigate(path: str, **_kw) -> dict[str, Any]:
    """Validate the path against the manifest and return a navigate action."""
    if not is_valid_path(path):
        # Try to be helpful: find the closest match in the manifest
        path_lower = (path or "").lower()
        for p in get_all_pages():
            if path_lower in p["path"].lower() or path_lower in p["name"].lower():
                return {
                    "ok": True,
                    "action": {"type": "navigate", "to": p["path"]},
                    "message": f"Opening {p['name']}.",
                    "matched_path": p["path"],
                }
        return {
            "ok": False,
            "error": f"Unknown path '{path}'. See list_pages() for valid options.",
        }
    page = get_page_by_path(path)
    return {
        "ok": True,
        "action": {"type": "navigate", "to": path},
        "message": f"Opening {page['name']}.",
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
        description="Navigate the dashboard to an internal page. Use for ANY in-app page (chatbot, twins, reports, meetings, settings, etc.). Path MUST be one from the pages list (see list_pages()).",
        kind="read",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The route path, e.g. '/chatbot' or '/reports'"},
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
