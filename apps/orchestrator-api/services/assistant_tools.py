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

def tool_navigate(path: str, query: Any = "", **_kw) -> dict[str, Any]:
    """Validate the path against the manifest and return a navigate action.

    Pass `query` to apply page-level filters. Accepts either:
      - string:  "filter=daily" or "status=pending&twin=Kim"
      - dict:    {"filter": "daily"} or {"status": "pending", "twin": "Kim"}
    Examples:
      navigate("/reports", "filter=daily")    → /reports?filter=daily
      navigate("/task-board", {"status": "pending"})
    """
    # Coerce dict → URL-encoded string. LLMs sometimes return JSON object.
    from urllib.parse import urlencode
    query_str = ""
    if isinstance(query, dict):
        query_str = urlencode({k: str(v) for k, v in query.items() if v is not None})
    elif isinstance(query, str):
        query_str = query.lstrip("?").strip()

    target_path = path
    if not is_valid_path(path):
        path_lower = (path or "").lower()
        match = None
        for p in get_all_pages():
            if path_lower in p["path"].lower() or path_lower in p["name"].lower():
                match = p["path"]; break
        if match:
            target_path = match
        elif isinstance(path, str) and path.startswith("/") and len(path) <= 200:
            # Permissive fallback — accept any well-formed path. The
            # global manifest is VIP-centric; Stock / Realty / Asset /
            # AIGlass have their own routes (see AGENT_PROFILES in
            # assistant_agent.py). Rather than centralize per-agent
            # route validation here, we trust the LLM's choice when the
            # path starts with / and is short, and let the frontend
            # router decide if it resolves (real page) or 404s.
            target_path = path
        else:
            return {
                "ok": False,
                "error": f"Unknown path '{path}'. See list_pages() for valid options.",
            }
    final_url = target_path + (f"?{query_str}" if query_str else "")
    page = get_page_by_path(target_path)
    page_label = page["name"] if page else target_path
    filter_msg = f" (filter: {query_str})" if query_str else ""
    return {
        "ok": True,
        "action": {"type": "navigate", "to": final_url},
        "message": f"Opening {page_label}{filter_msg}.",
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


def _agent_profile(agent_id: str | None) -> dict | None:
    """Look up the per-agent profile (name / pages / role) without a circular
    import — AGENT_PROFILES lives in assistant_agent, which imports this module,
    so we import it lazily at call time."""
    if not agent_id or agent_id.lower() == "vip":
        return None
    try:
        from services.assistant_agent import AGENT_PROFILES
        return AGENT_PROFILES.get(agent_id.lower())
    except Exception:
        return None


def tool_list_pages(agent_id: str = "vip", **_kw) -> dict[str, Any]:
    """Return THIS agent's menu/pages so the LLM can answer "what menu do I have".
    For non-VIP agents (Stock / Realty / Asset / AIGlass) we return that agent's
    own page list — NOT the VIP platform's pages."""
    prof = _agent_profile(agent_id)
    if prof:
        # profile pages are strings like "/recommendations — 추천 기록 (…)"
        pages = []
        for entry in prof.get("pages", []):
            path, _, label = str(entry).partition(" — ")
            pages.append({"path": path.strip(), "name": (label or path).strip()})
        return {"ok": True, "agent": prof.get("name"), "pages": pages, "count": len(pages)}
    pages = [
        {"path": p["path"], "name": p["name"], "description": p["description"]}
        for p in get_all_pages(include_hidden=False)
    ]
    return {"ok": True, "pages": pages, "count": len(pages)}


def tool_what_can_you_do(agent_id: str = "vip", **_kw) -> dict[str, Any]:
    """Return a summary of capabilities — scoped to the CURRENT agent."""
    prof = _agent_profile(agent_id)
    if prof:
        page_names = [str(e).split(" — ")[-1].split(" (")[0].strip() for e in prof.get("pages", [])]
        return {
            "ok": True,
            "agent": prof.get("name"),
            "summary": (
                f"I'm the assistant for {prof.get('name')}. {prof.get('tagline','')} "
                "I know every page and its data here, I remember our conversation, and I can "
                "fetch LIVE data and analyze it for you — plus do things you'd otherwise do by "
                "hand: open/navigate to any page, summarize what's on screen, and run actions "
                "(with your confirmation)."
            ),
            "menus": page_names,
            "examples": _agent_examples(agent_id),
        }
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


def _agent_examples(agent_id: str) -> list[str]:
    aid = (agent_id or "").lower()
    if aid == "stock":
        return [
            "what are today's recommendations?",
            "오늘 외국인 순매수 상위 종목",
            "any intraday signals right now?",
            "open the 투자자 수급 page",
            "summarize my trade journal",
        ]
    if aid == "realty":
        return ["show me the market dashboard", "향남 시세 알려줘", "open the cashflow builder"]
    if aid == "asset":
        return ["open 자산현황", "this month's rent income", "which leases expire soon?"]
    if aid == "aiglass":
        return ["open property listings", "show my A-grade leads", "open the contracts page"]
    return ["what can you do?", "open the dashboard"]


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
        # Resolve friendly names → agent.type by SUBSTRING (so "Asset Agent",
        # "the asset agent", "자산 에이전트" all work — not just exact "asset").
        agent_type = None
        for key, t in (
            ("asset", "asset"), ("자산", "asset"),
            ("stock", "stock"), ("주식", "stock"),
            ("real estate", "realty"), ("real-estate", "realty"),
            ("realty", "realty"), ("부동산", "realty"),
        ):
            if key in name_lower:
                agent_type = t
                break
        if not agent_type:
            # Fall back to matching a registered agent by its name.
            rows = db.query(CoreAgent).filter(CoreAgent.status == "active").all()
            m = next(
                (a for a in rows
                 if name_lower and ((a.name or "").lower() in name_lower
                                    or name_lower in (a.name or "").lower())),
                None,
            )
            if m:
                agent_type = m.type
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
        if e in ("agents", "agent", "core_agents"):
            from db.models import CoreAgent
            all_rows = db.query(CoreAgent).all()
            # Exclude seed/test junk (mock OR removed) entirely — the user only
            # counts their REAL agents. Headline count = active agents.
            real = [a for a in all_rows
                    if not bool(getattr(a, "is_mock", False)) and a.status != "removed"]
            active = [a for a in real if a.status == "active"]
            errored = [a for a in real if a.status == "error"]
            return {
                "ok": True, "entity": "agents",
                "count": len(active),               # what "how many agents" should report
                "active_count": len(active),
                "error_count": len(errored),
                "list": [
                    {"name": a.name, "type": a.type, "status": a.status}
                    for a in active
                ],
                "errored": [a.name for a in errored],
                "note": "count = your active agents (mock/removed seed agents excluded)",
            }
        return {"ok": False, "error": f"Unknown entity '{entity}'. Try: twins, conversations, tasks, reports, approvals, meetings, agents."}
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

def tool_send_dm(
    twin_name: str,
    body: str = "",
    attachment_ids: Optional[list] = None,
    db: Session = None,
    **_kw,
) -> dict[str, Any]:
    """Send a direct message (optionally with attached files/images) from the
    boss to a specific twin.

    `attachment_ids` are returned by POST /chatbot/upload — when present, we
    persist a JSON list of {filename, mime_type, kind, url} alongside the
    text body so the twin sees the attachment. Either body OR attachments
    must be non-empty (sending an empty DM is rejected)."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    body = (body or "").strip()
    attachment_ids = attachment_ids or []
    if not body and not attachment_ids:
        return {"ok": False, "error": "Message body or at least one attachment required."}

    # Resolve attachments — store metadata + a static URL the twin UI can render
    attachments_meta: list[dict] = []
    if attachment_ids:
        from routers.chatbot import load_attachment
        for aid in attachment_ids[:8]:  # cap so a wild LLM can't blast 100s
            a = load_attachment(aid)
            if not a:
                log.info(f"tool_send_dm: attachment_id '{aid}' not found (expired or wrong)")
                continue
            attachments_meta.append({
                "attachment_id": aid,
                "filename": a.get("filename"),
                "mime_type": a.get("mime_type"),
                "kind": a.get("kind"),
                # The frontend rendering side can do GET /chatbot/attachments/<id>
                # once that read endpoint exists; for now the id alone is enough.
            })

    try:
        from db.models import DirectMessage
        tw = _find_twin_by_name(db, twin_name)
        if not tw:
            return {"ok": False, "error": f"No twin matching '{twin_name}'"}
        # Embed attachments as JSON in the message metadata. We use a magic
        # "[ATTACH] " prefix in the content for backward-compat with the
        # legacy reader; the next reader iteration should look at a real
        # `attachments_json` column once a migration lands.
        meta_marker = ""
        if attachments_meta:
            import json as _json
            meta_marker = "\n[ATTACH] " + _json.dumps(attachments_meta, ensure_ascii=False)
        msg = DirectMessage(
            twin_id=tw.id,
            sender_type="boss",
            content=(body or "") + meta_marker,
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)
        # Friendly summary for the assistant reply
        if attachments_meta and body:
            summary = f"✅ Sent DM to {tw.name} with {len(attachments_meta)} attachment(s): \"{body[:80]}\""
        elif attachments_meta:
            kinds = ", ".join(sorted({a["kind"] for a in attachments_meta}))
            summary = f"✅ Sent {len(attachments_meta)} {kinds} attachment(s) to {tw.name}."
        else:
            summary = f"✅ Sent DM to {tw.name}: \"{body[:100]}\""
        return {
            "ok": True,
            "message": summary,
            "message_id": msg.id,
            "twin_name": tw.name,
            "attachment_count": len(attachments_meta),
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
        from db.models import DigitalTwin, DirectMessage
        twins = db.query(DigitalTwin).all()
        sent = 0
        for t in twins:
            msg = DirectMessage(twin_id=t.id, sender_type="boss", content=body or "")
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


def tool_find_page(query: str, db: Session = None, limit: int = 3, **_kw) -> dict[str, Any]:
    """Fuzzy lookup of dashboard pages + sub-tabs + external agents by name.

    Use when the user asks "where is X" / "how do I get to Y" / "어디에 있어"
    and you need to TELL them the path rather than navigate there yourself.
    Scores each manifest entry by token overlap against name, description,
    and keywords; returns top-N matches with a relative confidence score
    (0..1) and the deep-link the boss can click.

    Faster than asking the LLM to scan the page list — and deterministic,
    so the same query always returns the same top result.
    """
    from services.assistant_manifest import PAGES, EXTERNAL_AGENTS
    q = (query or "").strip().lower()
    if not q:
        return {"ok": False, "error": "query required"}
    q_tokens = {t for t in q.replace("?", " ").replace(",", " ").split() if len(t) >= 2}

    def _score(haystack: str) -> int:
        if not haystack:
            return 0
        h = haystack.lower()
        # Exact phrase = strongest signal
        score = 50 if q in h else 0
        # Token overlap = secondary
        score += sum(8 for t in q_tokens if t in h)
        return score

    candidates: list[dict] = []
    for p in PAGES:
        s = (_score(p.get("name", ""))
             + _score(p.get("description", ""))
             + _score(" ".join(p.get("keywords") or []))) // 1
        if s > 0:
            candidates.append({
                "kind": "page", "path": p["path"], "name": p["name"],
                "description": p.get("description", "")[:200], "score": s,
            })
        # Score each sub-tab too — let the LLM say "Settings → API Keys"
        for st in p.get("sub_tabs") or []:
            tab_s = (_score(st.get("name", "")) * 2
                     + _score(st.get("description", "")))
            if tab_s > 0:
                candidates.append({
                    "kind": "sub_tab",
                    "path": f"{p['path']}#{st['id']}",
                    "name": f"{p['name']} → {st['name']}",
                    "description": st.get("description", "")[:200],
                    "score": tab_s,
                })
    # External agents (highest weight on exact name match)
    for a in EXTERNAL_AGENTS:
        s = (_score(a.get("name", "")) * 3
             + _score(a.get("name_ko", "")) * 3
             + _score(a.get("description", ""))
             + _score(" ".join(a.get("keywords") or [])) * 2)
        if s > 0:
            candidates.append({
                "kind": "external_agent",
                "agent": a["name"], "path": a["portal_url"],
                "name": a["name"], "description": a.get("description", "")[:200],
                "score": s, "external": True,
            })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    top = candidates[:max(1, int(limit))]
    if not top:
        return {"ok": True, "query": q, "count": 0, "matches": []}
    max_score = top[0]["score"] or 1
    for c in top:
        c["confidence"] = round(c["score"] / max_score, 2)
        c.pop("score", None)
    return {"ok": True, "query": q, "count": len(top), "matches": top}


def tool_list_connections(db: Session = None, **_kw) -> dict[str, Any]:
    """Introspect every channel / API / integration the agent is wired to —
    reports whether the credentials / hosts are configured and (best-effort)
    whether they actually respond. Use when the user asks 'what's connected
    to my agent', 'is Telegram set up?', 'are my API keys configured', etc.

    Buckets returned:
      * LLM providers (env var presence — not live ping, to avoid quota burn)
      * Channels       (Telegram bot token, Kakao webhook secret, SMTP)
      * External agent backends (live HTTP HEAD)
      * Database + Redis (connection echo)
    """
    import os
    from services.llm_client import list_available_models

    out: dict[str, Any] = {"ok": True, "buckets": {}}

    # ── LLM providers ─────────────────────────────────────────────────
    try:
        models = list_available_models()
        by_prov: dict[str, list[dict]] = {}
        for m in models:
            by_prov.setdefault(m["provider"], []).append(m)
        out["buckets"]["llm_providers"] = {
            prov: {
                "available": any(m["available"] for m in ms),
                "models": [m["id"] for m in ms if m["available"]],
            }
            for prov, ms in by_prov.items()
        }
    except Exception as e:
        out["buckets"]["llm_providers"] = {"error": str(e)[:120]}

    # ── Channels ──────────────────────────────────────────────────────
    out["buckets"]["channels"] = {
        "telegram_bot":   {"configured": bool(os.getenv("TELEGRAM_BOT_TOKEN"))},
        "kakao_webhook":  {"configured": bool(os.getenv("KAKAO_WEBHOOK_SECRET") or os.getenv("KAKAO_REST_API_KEY"))},
        "smtp_email":     {
            "configured": bool(os.getenv("SMTP_EMAIL") and os.getenv("SMTP_PASSWORD")),
            "from": os.getenv("SMTP_EMAIL", "(unset)"),
        },
        "supabase":       {"configured": bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY"))},
    }

    # ── External agent backends (HEAD probe with short timeout) ─────
    import httpx
    agent_status: dict[str, dict] = {}
    try:
        from services.assistant_manifest import get_external_agents
        for a in get_external_agents():
            url = a.get("backend_url") or a.get("portal_url")
            if not url:
                agent_status[a["name"]] = {"configured": False}
                continue
            entry: dict[str, Any] = {"url": url}
            try:
                with httpx.Client(timeout=5.0) as c:
                    r = c.get(f"{url.rstrip('/')}/health")
                    entry["http_status"] = r.status_code
                    entry["reachable"] = r.status_code < 500
            except Exception as e:
                entry["reachable"] = False
                entry["error"] = str(e)[:80]
            agent_status[a["name"]] = entry
    except Exception as e:
        agent_status["_error"] = str(e)[:120]
    out["buckets"]["external_agents"] = agent_status

    # ── Infrastructure ────────────────────────────────────────────────
    infra: dict[str, Any] = {}
    try:
        if db:
            from sqlalchemy import text as _text
            db.execute(_text("SELECT 1"))
            infra["database"] = "connected"
        else:
            infra["database"] = "no db session"
    except Exception as e:
        infra["database"] = f"error: {str(e)[:80]}"
    infra["redis_url_set"] = bool(os.getenv("REDIS_URL"))
    infra["render_host"] = os.getenv("RENDER", "false") == "true"
    infra["vercel_host"] = bool(os.getenv("VERCEL"))
    out["buckets"]["infra"] = infra

    # ── Summary one-liner the LLM can quote ──────────────────────────
    llm = out["buckets"]["llm_providers"]
    live_llms = [p for p, v in llm.items() if isinstance(v, dict) and v.get("available")]
    chans = out["buckets"]["channels"]
    live_chans = [k for k, v in chans.items() if isinstance(v, dict) and v.get("configured")]
    out["summary"] = (
        f"LLMs available: {', '.join(live_llms) or 'none'}. "
        f"Channels configured: {', '.join(live_chans) or 'none'}. "
        f"External agents reachable: "
        f"{sum(1 for v in agent_status.values() if isinstance(v, dict) and v.get('reachable'))}"
        f"/{len([k for k in agent_status if not k.startswith('_')])}."
    )
    return out


def tool_recall_history(query: str, days: int = 7, limit: int = 8, user_id: str = "boss",
                        db: Session = None, **_kw) -> dict[str, Any]:
    """Cross-session memory — search the boss's prior Assistant turns.

    Use when the user asks 'what did we discuss yesterday', 'remember when
    I asked about X', '어제 우리 뭐 얘기했어'. Searches the rolling
    `channel='assistant_overlay'` chat session by ILIKE over the saved
    content. Empty query → return the last N turns chronologically (so
    the LLM can say 'last thing we discussed was…').
    """
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from db.models import ChatSession, ChatMessage
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(days=max(1, int(days)))
        sessions = (db.query(ChatSession)
                    .filter(ChatSession.user_id == user_id,
                            ChatSession.channel == "assistant_overlay")
                    .all())
        if not sessions:
            return {"ok": True, "query": query, "count": 0, "matches": [],
                    "note": "No prior Assistant history saved for this user yet."}
        session_ids = [s.id for s in sessions]
        q = db.query(ChatMessage).filter(
            ChatMessage.session_id.in_(session_ids),
            ChatMessage.created_at >= cutoff,
        )
        # ILIKE on the text inside content_json (Postgres JSONB ->> 'text')
        if query and query.strip():
            try:
                q = q.filter(ChatMessage.content_json.op("->>")("text").ilike(f"%{query}%"))
            except Exception:
                # SQLite fallback — content_json may be stored as Text
                q = q.filter(ChatMessage.content_json.cast(__import__("sqlalchemy").Text).ilike(f"%{query}%"))
        msgs = q.order_by(ChatMessage.created_at.desc()).limit(int(limit)).all()
        return {
            "ok": True,
            "query": query or "(recent)",
            "days": days,
            "count": len(msgs),
            "matches": [
                {
                    "ts": m.created_at.isoformat() if m.created_at else None,
                    "role": m.role,
                    "text": ((m.content_json or {}).get("text") or "")[:240],
                    "intent": (m.content_json or {}).get("intent"),
                    "tool_used": (m.content_json or {}).get("tool_used"),
                }
                for m in msgs
            ],
        }
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

def tool_asset_summary(db: Session = None, **_kw) -> dict[str, Any]:
    """Company real-estate asset PORTFOLIO overview from the real workbook
    (자산관리.xlsx → Supabase): total value, rent, deposits, occupancy, by category."""
    from services import asset_data as _ad
    d = _ad.load_asset_data(db)
    if not d.get("available"):
        return {"ok": False, "error": "자산 데이터가 아직 로드되지 않았습니다 (asset workbook not imported yet)."}
    t, o = d["totals"], d["occupancy"]

    def f(x):
        try:
            return round(float(x))
        except Exception:
            return x
    return {
        "ok": True,
        "총자산가치_원": f(t.get("value")), "총자산가치_억": round(float(t.get("value", 0)) / 1e8, 1),
        "총보증금_원": f(t.get("deposit")), "월임대수입_원": f(t.get("monthly_rent")),
        "포트폴리오_항목수": t.get("items"), "세부자산수": t.get("units"),
        "점유": o.get("occupied"), "공실": o.get("vacant"), "공실률_퍼센트": o.get("vacancy_rate"),
        "구분별": [{"구분": c["category"], "건수": c["count"],
                  "자산가치_억": round(c["value"] / 1e8, 1), "월세_원": f(c["monthly_rent"])}
                 for c in d.get("by_category", [])],
    }


def tool_asset_search(query: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Look up specific company assets/units by keyword — building name, 호수(unit),
    주소(address), category (구분), status (상태) or tenant (임차인). Multi-word
    queries ('의정부 상가 B1') match when EACH word appears somewhere in the row."""
    import re as _re
    from sqlalchemy import text as _text
    # Strip natural-language filler + Korean particle suffixes so queries like
    # '공실인 자산 알려줘' reduce to the meaningful keyword '공실'.
    _STOP = {"자산", "정보", "알려줘", "알려", "보여줘", "보여", "얼마", "얼마야", "현황", "대해",
             "관해", "대한", "좀", "해줘", "뭐야", "어때", "목록", "리스트", "전부", "모두", "관련",
             "property", "asset", "assets", "tell", "me", "about", "the", "is", "what", "show", "of", "all"}

    def _stem(t: str) -> str:
        for suf in ("인", "은", "는", "이", "가", "을", "를", "의", "에서", "에", "과", "와", "도", "만"):
            if len(t) > len(suf) + 1 and t.endswith(suf):
                return t[:-len(suf)]
        return t

    toks = []
    for t in _re.split(r"\s+", (query or "").strip()):
        t = t.strip()
        if not t or t.lower() in _STOP:
            continue
        toks.append(_stem(t))
    if not toks:
        return {"ok": True, "matches": 0, "note": "검색어를 구체적으로 주세요 (예: '의정부 B1', '공실', '낙하리', '상가')."}
    units, pf = [], []
    # Concatenate all searchable fields into one blob; require EVERY token to
    # appear in it (token-AND) so '의정부 상가 B1' matches 의정부상가 + B1.
    u_blob = ("(coalesce(property,'')||' '||coalesce(unit_no,'')||' '||coalesce(address,'')"
              "||' '||coalesce(category,'')||' '||coalesce(status,'')||' '||coalesce(tenant,''))")
    p_blob = "(coalesce(category,'')||' '||coalesce(description,''))"
    params = {f"t{i}": f"%{t}%" for i, t in enumerate(toks)}
    u_where = " AND ".join(f"{u_blob} ILIKE :t{i}" for i in range(len(toks)))
    p_where = " AND ".join(f"{p_blob} ILIKE :t{i}" for i in range(len(toks)))
    try:
        for r in db.execute(_text(
            "SELECT property, category, unit_no, address, area_pyeong, price, market_value, "
            "deposit, monthly_rent, status, tenant FROM asset_units "
            f"WHERE {u_where} ORDER BY monthly_rent DESC NULLS LAST LIMIT 25"), params):
            units.append(dict(r._mapping))
        for r in db.execute(_text(
            "SELECT category, description, sale_price, deposit, monthly_rent FROM asset_portfolio "
            f"WHERE {p_where} ORDER BY sale_price DESC NULLS LAST LIMIT 15"), params):
            pf.append(dict(r._mapping))
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)[:150]}

    def f(x):
        try:
            return round(float(x))
        except Exception:
            return x
    for r in units:
        for k in ("area_pyeong", "price", "market_value", "deposit", "monthly_rent"):
            if r.get(k) is not None:
                r[k] = f(r[k])
    for r in pf:
        for k in ("sale_price", "deposit", "monthly_rent"):
            if r.get(k) is not None:
                r[k] = f(r[k])
    if not units and not pf:
        return {"ok": True, "matches": 0, "note": f"'{query}'에 해당하는 자산을 찾지 못했습니다."}
    return {"ok": True, "matches": len(units) + len(pf), "units": units, "portfolio_items": pf}


def tool_asset_top(metric: str = "area", order: str = "desc", limit: int = 5,
                   db: Session = None, **_kw) -> dict[str, Any]:
    """Rank company assets/units by a numeric field — for SUPERLATIVES (biggest /
    smallest by size, most/least valuable, highest/lowest rent). Computed from the
    structured table so the comparison is EXACT (never guessed from text)."""
    from sqlalchemy import text as _text
    col = {"area": "area_m2", "size": "area_m2", "면적": "area_m2",
           "value": "price", "price": "price", "가치": "price", "매입가": "price", "분양가": "price",
           "market": "market_value", "현시세": "market_value",
           "rent": "monthly_rent", "월세": "monthly_rent",
           "deposit": "deposit", "보증금": "deposit"}.get((metric or "area").strip().lower(), "area_m2")
    od = "ASC" if str(order or "desc").lower().startswith("a") else "DESC"
    try:
        lim = max(1, min(int(limit or 5), 20))
    except Exception:
        lim = 5
    rows = []
    try:
        for r in db.execute(_text(
            f"SELECT property, unit_no, address, category, area_m2, area_pyeong, price, "
            f"market_value, monthly_rent, deposit, status FROM asset_units "
            f"WHERE {col} IS NOT NULL ORDER BY {col} {od} LIMIT :lim"), {"lim": lim}):
            rows.append(dict(r._mapping))
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)[:150]}

    def f(x):
        try:
            return round(float(x), 2)
        except Exception:
            return x
    for r in rows:
        for k in ("area_m2", "area_pyeong", "price", "market_value", "monthly_rent", "deposit"):
            if r.get(k) is not None:
                r[k] = f(r[k])
    return {"ok": True, "ranked_by": col, "order": od, "results": rows}


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
    "asset_summary": Tool(
        name="asset_summary",
        description=(
            "Company REAL-ESTATE ASSET PORTFOLIO overview from the company's asset "
            "workbook (자산관리). Returns total portfolio value (억원), total monthly rent "
            "(월세), deposits (보증금), occupancy/vacancy (공실), and a breakdown by category "
            "(토지/상가/도생/생숙/아파트/창고/공장/태양광). Use for '총 자산 가치 얼마야', "
            "'우리 자산 얼마', 'how much are our assets worth', '공실 현황', '구분별 자산 비중', "
            "'월세 수입 합계'."
        ),
        kind="read",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=tool_asset_summary,
    ),
    "asset_search": Tool(
        name="asset_search",
        description=(
            "Look up SPECIFIC company assets/units by keyword — building name, 호수(unit no), "
            "주소(address), category(구분), status(상태) or tenant(임차인). Returns each match's "
            "분양가/매입가, 보증금, 월세 and 상태. Use for '의정부 B1 월세 얼마', '낙하리 자산', "
            "'공실인 상가 알려줘', '의정부 한양파크뷰 303호', 'rent on unit X'."
        ),
        kind="read",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword: building/unit/address/category/status/tenant, e.g. '의정부 B1', '낙하리', '공실', '상가'"},
            },
            "required": ["query"],
        },
        fn=tool_asset_search,
    ),
    "asset_top": Tool(
        name="asset_top",
        description=(
            "Rank the company's assets/units by a NUMBER to answer SUPERLATIVE / "
            "comparison questions — biggest/largest or smallest by size (면적), "
            "most/least valuable (가치/매입가), highest/lowest 월세(rent) or 보증금. "
            "ALWAYS use this (not knowledge-base text search) for 'which is biggest', "
            "'가장 큰 자산', '면적 제일 넓은', '제일 비싼 자산', '월세 가장 높은', "
            "'most expensive', 'largest property' — it computes the exact ranking from "
            "the data. metric: area|value|rent|deposit|market; order: desc (biggest/"
            "highest) or asc (smallest/lowest)."
        ),
        kind="read",
        parameters={
            "type": "object",
            "properties": {
                "metric": {"type": "string", "description": "area (size/면적) | value (가치/매입가) | rent (월세) | deposit (보증금) | market (현시세)"},
                "order": {"type": "string", "description": "'desc' for biggest/highest (default), 'asc' for smallest/lowest"},
                "limit": {"type": "integer", "description": "How many to return (1-20, default 5)"},
            },
            "required": [],
        },
        fn=tool_asset_top,
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
        description="Quick count of entities. Pass 'twins' / 'conversations' / 'needs_reply' / 'tasks' / 'reports' / 'approvals' / 'meetings' / 'agents'. For 'agents' the result includes both total + active count + a list of registered agent names and types.",
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
    "list_connections": Tool(
        name="list_connections", kind="read",
        description=(
            "List EVERYTHING connected to this agent — LLM providers, "
            "channels (Telegram/Kakao/SMTP/Supabase), external agent "
            "backends (with live HTTP probe), and infra (DB/Redis/host). "
            "Use when the user asks 'what's connected', 'are my keys set', "
            "'is Telegram configured', 'is the Asset agent reachable', "
            "'what providers do I have', '뭐가 연결되어 있어'."
        ),
        parameters={"type": "object", "properties": {}},
        fn=tool_list_connections,
    ),
    "recall_history": Tool(
        name="recall_history", kind="read",
        description=(
            "Cross-session memory — search the boss's PRIOR Assistant "
            "conversations (last N days, default 7). Use when the user "
            "asks 'what did we discuss yesterday', 'remember when I asked "
            "about X', '어제 뭐 얘기했어', 'show me my last conversation', "
            "'what were we doing'. Pass an empty query to get the most "
            "recent turns chronologically."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search past turns for (empty = recent)"},
                "days":  {"type": "integer", "description": "How far back to look (default 7)"},
                "limit": {"type": "integer", "description": "Max results (default 8)"},
            },
            "required": [],
        },
        fn=tool_recall_history,
    ),
    "find_page": Tool(
        name="find_page", kind="read",
        description=(
            "Fuzzy lookup of UI pages, sub-tabs, and external agent apps by "
            "name / description. Use when the user asks 'where is X' / "
            "'where can I find Y' / 'how do I get to Z' / '어디에 있어' — "
            "returns top-N matches with deep-link paths and a 0..1 confidence "
            "score. PREFER this over scanning the page list yourself; it "
            "covers sub-tabs (e.g. 'Settings → API Keys') that aren't first-"
            "class routes. NOT for navigating — for that use navigate(path)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What the user is looking for, e.g. 'API keys', 'needs reply', 'asset agent'"},
                "limit": {"type": "integer", "description": "Max results to return (default 3)"},
            },
            "required": ["query"],
        },
        fn=tool_find_page,
    ),

    # ========================================================================
    # WRITE tools (Phase 3) — every execution requires user confirm in widget
    # ========================================================================

    "send_dm": Tool(
        name="send_dm", kind="write",
        description=(
            "Send a direct message (and optionally attach files/images) "
            "from boss to a specific digital twin. Use when user says "
            "'send Davronbek: ...' / 'tell Kim that ...' / '다브론벡에게 메시지 보내'. "
            "If the boss attached files/images to THIS chat turn (i.e. there "
            "are pending attachment_ids), include them in attachment_ids so "
            "the twin receives them alongside the text. Either body or "
            "attachment_ids must be non-empty."
        ),
        parameters={
            "type": "object",
            "properties": {
                "twin_name":      {"type": "string", "description": "Twin name (partial match OK)"},
                "body":           {"type": "string", "description": "Message body (optional if attachments present)"},
                "attachment_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Attachment ids returned by POST /chatbot/upload — sent with the DM as files/images.",
                },
            },
            "required": ["twin_name"],
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

# --- Sub-navigation: open a SPECIFIC item inside a page ---
def tool_open_item(category: str, name_or_id: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Open a specific item inside a page. Use when the user says
    'open the Asset agent' (inside /agents), 'open Davronbek's twin'
    (inside /twins), 'open the latest daily report', etc.

    Categories supported:
      - "agent"        → /agents?highlight=<name>           (Asset/Stock/Realty card)
      - "external"     → opens the external Asset/Stock/Realty Vercel app
      - "twin"         → /twins?highlight=<name>
      - "report"       → /reports?open=<id>                 (auto-opens report detail)
      - "conversation" → /chatbot?conversation_id=<id>
      - "task"         → /task-board?highlight=<id>
      - "meeting"      → /meetings/<id>/room                (joins the meeting room)
    """
    cat = (category or "").lower().strip()
    target = (name_or_id or "").strip()
    if not cat or not target:
        return {"ok": False, "error": "Both category and name_or_id required"}

    # External agent app (opens in new tab)
    if cat in ("external", "external_agent", "portal"):
        ag = get_agent_by_name(target)
        if not ag:
            return {"ok": False, "error": f"Unknown external agent '{target}'"}
        return {
            "ok": True,
            "action": {"type": "navigate", "to": ag["portal_url"], "external": True},
            "message": f"Opening the {ag['name']} Agent in a new tab.",
        }

    # Internal agent card (within /agents)
    if cat == "agent":
        # Capitalize first letter for highlight matching
        highlight = target.title() if target.islower() else target
        return {
            "ok": True,
            "action": {"type": "navigate", "to": f"/agents?highlight={highlight}"},
            "message": f"Opening the agents page and highlighting {highlight}.",
        }

    if cat == "twin":
        if not db:
            return {"ok": False, "error": "DB required for twin lookup"}
        tw = _find_twin_by_name(db, target)
        if not tw:
            return {"ok": False, "error": f"No twin matching '{target}'"}
        return {
            "ok": True,
            "action": {"type": "navigate", "to": f"/twins?highlight={tw.name}"},
            "message": f"Opening Twins page, highlighting {tw.name}.",
        }

    if cat == "report":
        # If name_or_id looks like a UUID/hash use it directly; otherwise treat as filter
        if len(target) > 8 and "-" in target:
            return {
                "ok": True,
                "action": {"type": "navigate", "to": f"/reports?open={target}"},
                "message": f"Opening report {target[:8]}…",
            }
        # Common shortcuts
        f = target.lower()
        if f in ("daily", "weekly", "cross", "alerts"):
            return {
                "ok": True,
                "action": {"type": "navigate", "to": f"/reports?filter={f}"},
                "message": f"Opening {f} reports.",
            }
        return {"ok": False, "error": f"Unknown report identifier '{target}'"}

    if cat == "conversation":
        return {
            "ok": True,
            "action": {"type": "navigate", "to": f"/chatbot?conversation_id={target}"},
            "message": f"Opening conversation {target[:8]}…",
        }

    if cat == "task":
        return {
            "ok": True,
            "action": {"type": "navigate", "to": f"/task-board?highlight={target}"},
            "message": f"Opening Task Board, highlighting task {target[:8]}…",
        }

    if cat == "meeting":
        return {
            "ok": True,
            "action": {"type": "navigate", "to": f"/meetings/{target}/room"},
            "message": f"Joining meeting room {target[:8]}…",
        }

    return {"ok": False, "error": f"Unknown category '{cat}'. Use: agent / external / twin / report / conversation / task / meeting."}


# --- Unsend / delete a sent DM ---
def tool_unsend_dm(message_id: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Delete a previously sent boss→twin DM (or twin→boss). Use when
    the user says 'unsend that message' / 'delete what I sent to Davronbek'.
    Note: this only removes the row from our DB — does NOT recall the
    message from any external channel (Kakao etc.)."""
    if not db:
        return {"ok": False, "error": "DB required"}
    try:
        from db.models import DirectMessage
        m = db.query(DirectMessage).filter(DirectMessage.id == message_id).first()
        if not m:
            return {"ok": False, "error": f"DM {message_id} not found"}
        twin_id_short = (m.twin_id or "")[:8]
        db.delete(m)
        db.commit()
        return {
            "ok": True,
            "message": f"↩️ Unsent DM {message_id[:8]} (twin {twin_id_short})",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_unsend_last_dm(twin_name: str, db: Session = None, **_kw) -> dict[str, Any]:
    """Convenience: unsend the most recent DM the boss sent to a twin.
    Use when the user says 'unsend the last message I sent to Davronbek'."""
    if not db:
        return {"ok": False, "error": "DB required"}
    try:
        from db.models import DirectMessage
        tw = _find_twin_by_name(db, twin_name)
        if not tw:
            return {"ok": False, "error": f"No twin '{twin_name}'"}
        m = (db.query(DirectMessage)
             .filter(DirectMessage.twin_id == tw.id,
                     DirectMessage.sender_type == "boss")
             .order_by(DirectMessage.created_at.desc()).first())
        if not m:
            return {"ok": False, "error": f"No DM you sent to {tw.name}"}
        mid = m.id
        preview = (m.content or "")[:60]
        db.delete(m)
        db.commit()
        return {
            "ok": True,
            "message": f"↩️ Unsent your last DM to {tw.name} (\"{preview}\")",
            "deleted_id": mid,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


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
                "workflows": [{"id": r.id, "name": r.name,
                               "cron": getattr(r, "cron_expr", None),
                               "enabled": bool(getattr(r, "enabled", True))}
                              for r in rules]}
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
    "open_item": Tool(
        name="open_item", kind="read",
        description=(
            "Open a SPECIFIC item INSIDE a page (sub-navigation). Use when the "
            "user names a specific thing — not just a page. Examples:\n"
            "  'open the Asset agent' (inside /agents)  → open_item('agent', 'Asset')\n"
            "  'open the Stock agent app'                → open_item('external', 'Stock')\n"
            "  'open Davronbek's twin'                   → open_item('twin', 'Davronbek')\n"
            "  'open the latest daily report'            → open_item('report', 'daily')\n"
            "  'open this conversation'                  → open_item('conversation', '<id>')\n"
            "  'open task abc-123'                       → open_item('task', 'abc-123')\n"
            "  'join meeting xyz'                        → open_item('meeting', 'xyz')\n"
            "Use 'external' for the actual deployed agent apps (Vercel/Render). "
            "Use 'agent' for the internal /agents listing card."
        ),
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["agent", "external", "twin", "report", "conversation", "task", "meeting"]},
                "name_or_id": {"type": "string"},
            },
            "required": ["category", "name_or_id"],
        },
        fn=tool_open_item,
    ),
    "unsend_dm": Tool(
        name="unsend_dm", kind="write",
        description="Delete a previously sent DM by message ID. Use 'unsend that message' / 'delete the message I sent'. Removes from our DB; does NOT recall from Kakao etc.",
        parameters={
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
        fn=tool_unsend_dm,
    ),
    "unsend_last_dm": Tool(
        name="unsend_last_dm", kind="write",
        description="Convenience: unsend the LAST DM the boss sent to a specific twin. Use 'unsend my last message to Davronbek'.",
        parameters={
            "type": "object",
            "properties": {"twin_name": {"type": "string"}},
            "required": ["twin_name"],
        },
        fn=tool_unsend_last_dm,
    ),
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


# ============================================================================
#  Knowledge-base search (RAG)
#
#  The assistant ALREADY pre-fetches top-k chunks before every LLM turn (see
#  assistant_agent._run_agent_impl). This explicit tool is for cases where the
#  LLM realises mid-conversation it needs to look something else up — e.g.
#  the user says "actually search for the 향남 contract" and the original
#  retrieval didn't include it. The LLM can call this tool to fetch fresh hits.
# ============================================================================

def tool_search_knowledge_base(
    query: str,
    top_k: int = 8,
    agent_id: str = "vip",
    db: Session = None,
    **_kw,
) -> dict[str, Any]:
    """Vector-search the uploaded knowledge base for `query`."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from services.knowledge_ingest import rag_retrieve
        hits = rag_retrieve(db, agent_id=agent_id, query=query, top_k=top_k, min_sim=0.30)
        # Trim content for the tool-result envelope; the LLM already has the
        # top hits in its system prompt — this is for "deeper" queries.
        # NOTE: do NOT expose the source filename or similarity score to the LLM —
        # the user does not want answers like 'from "...xlsx" with similarity 0.92'.
        # Keep only the human-meaningful location label + the excerpt text.
        compact = [
            {
                "location": h.get("location") or "",
                "excerpt":  (h["content"] or "")[:600],
            } for h in hits
        ]
        return {"ok": True, "count": len(compact), "hits": compact}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


TOOL_REGISTRY["search_knowledge_base"] = Tool(
    name="search_knowledge_base", kind="read",
    description=(
        "Search the boss's uploaded knowledge base (xlsx/pdf/docx/pptx) for a "
        "specific topic when the pre-fetched excerpts don't cover what the user "
        "asked. Returns top file/sheet excerpts with similarity scores. Use "
        "when the user references something you suspect is in the uploaded "
        "files (a property name, a contract, a sheet label) and you don't "
        "already have a matching excerpt in the [KNOWLEDGE BASE] section above."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query":   {"type": "string", "description": "What to search for"},
            "top_k":   {"type": "integer", "description": "How many results (1-20)"},
        },
        "required": ["query"],
    },
    fn=tool_search_knowledge_base,
)


# Knowledge-file management — list / delete uploads via natural language.
# "What files do I have in my knowledge?" / "Show me my uploads"
# "Delete the asset-management spreadsheet" → matches by filename substring

def tool_list_knowledge_files(agent_id: str = "vip", db: Session = None, **_kw) -> dict[str, Any]:
    """List all files currently indexed in the agent's knowledge base."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    try:
        from services.knowledge_ingest import list_files
        files = list_files(db, agent_id=agent_id)
        compact = [
            {
                "filename":    f["filename"],
                "chunk_count": f["chunk_count"],
                "status":      f["status"],
                "size_bytes":  f["size_bytes"],
                "uploaded_at": f["uploaded_at"],
                "uploaded_by": f["uploaded_by"],
            } for f in files
        ]
        return {"ok": True, "count": len(compact), "files": compact}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


TOOL_REGISTRY["list_knowledge_files"] = Tool(
    name="list_knowledge_files", kind="read",
    description=(
        "List every file the boss has uploaded into the knowledge base "
        "(xlsx, pdf, docx, pptx, csv). Returns filename, status, chunk count "
        "and upload date. Use when the user asks 'what's in my knowledge?', "
        "'show me my uploads', 'which documents have I added?', etc."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    fn=tool_list_knowledge_files,
)


def tool_delete_knowledge_file(
    filename: str,
    agent_id: str = "vip",
    db: Session = None,
    **_kw,
) -> dict[str, Any]:
    """Delete an uploaded file (and its chunks) from the knowledge base.
    Matches by filename substring — if multiple files match, refuses and
    asks the user to be more specific."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    if not (filename or "").strip():
        return {"ok": False, "error": "filename is required"}
    try:
        from services.knowledge_ingest import list_files, delete_file
        files = list_files(db, agent_id=agent_id)
        needle = filename.strip().lower()
        matches = [f for f in files if needle in f["filename"].lower()]
        if not matches:
            return {
                "ok": False,
                "error": f"No file matching {filename!r} found.",
                "available": [f["filename"] for f in files],
            }
        if len(matches) > 1:
            return {
                "ok": False,
                "error": (
                    f"Multiple files match {filename!r} — please be more "
                    f"specific."
                ),
                "matches": [f["filename"] for f in matches],
            }
        target = matches[0]
        n = delete_file(db, agent_id=agent_id, file_id=target["id"])
        if n == 0:
            return {"ok": False, "error": "delete returned 0 rows"}
        return {
            "ok": True,
            "deleted": target["filename"],
            "chunks_removed": target["chunk_count"],
            "message": f"Removed {target['filename']!r} ({target['chunk_count']} chunks) from the knowledge base.",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ============================================================================
#  Outbound call — Assistant places a Vapi call on the boss's behalf
# ============================================================================

def tool_place_call(
    to: str,
    caller_name: str = "",
    reason: str = "custom",
    agent_id: str = "vip",
    db: Session = None,
    **_kw,
) -> dict[str, Any]:
    """Place an outbound call to `to` via the configured voice provider.
    `to` must be E.164 phone format (+821012345678). The route handles
    rate-limiting and provider routing internally."""
    if not db:
        return {"ok": False, "error": "DB session required"}
    if not (to or "").strip():
        return {"ok": False, "error": "Phone number is required (E.164 format)"}
    try:
        from services import voice_service
        from db.models import VoiceProviderAssistant

        # Rate-limit check first
        block = voice_service.check_recipient_eligibility(db, agent_id, to)
        if block:
            return {"ok": False, "error": f"Rate-limited: {block}"}

        mapping = (
            db.query(VoiceProviderAssistant)
            .filter(
                VoiceProviderAssistant.agent_id == agent_id,
                VoiceProviderAssistant.provider == "vapi",
                VoiceProviderAssistant.active.is_(True),
            )
            .first()
        )
        call = voice_service.start_call(
            db,
            agent_id,
            provider="vapi" if mapping else "intent",
            provider_call_id=None,
            direction="outbound",
            caller_number=to,
            caller_name=caller_name or None,
            reason=reason or "custom",
        )
        return {
            "ok": True,
            "call_id": str(call.id) if call else None,
            "to": to,
            "status": "ringing" if mapping else "intent_recorded",
            "message": (
                f"Placing call to {caller_name or to}…" if mapping
                else f"Call intent recorded for {caller_name or to} (no Vapi assistant configured)."
            ),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


TOOL_REGISTRY["place_call"] = Tool(
    name="place_call", kind="write",
    description=(
        "Place an outbound phone call via the agent's configured voice "
        "provider (Vapi). Use when the boss says 'call 김민호', 'phone "
        "the tenant at 010-1234-5678', etc. Requires E.164 format "
        "(+82...). Requires confirmation because real money is spent on "
        "the carrier and the recipient's phone rings immediately."
    ),
    parameters={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "E.164 phone number, e.g. +821012345678"},
            "caller_name": {"type": "string", "description": "Optional name for the recipient (shown in UI / call log)"},
            "reason": {"type": "string", "description": "Why the call is being placed (free-form tag, e.g. 'rent_reminder', 'lease_followup')"},
        },
        "required": ["to"],
    },
    fn=tool_place_call,
    requires_confirmation=True,
)


TOOL_REGISTRY["delete_knowledge_file"] = Tool(
    name="delete_knowledge_file", kind="write",
    description=(
        "Remove a file from the agent's knowledge base. The boss can refer "
        "to the file by full name OR a unique substring (e.g. 'delete the "
        "asset management spreadsheet' → matches '자산관리_ver.1_260206 (2).xlsx'). "
        "If the substring is ambiguous (multiple files match), refuses and "
        "lists the candidates. Requires confirmation because the chunks are "
        "deleted permanently."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Filename or substring of the filename to delete",
            },
        },
        "required": ["filename"],
    },
    fn=tool_delete_knowledge_file,
    requires_confirmation=True,
)


# --- Web search tool --------------------------------------------------------
# Lets the assistant look up live information on the public web when its
# knowledge base + the LLM's own knowledge don't cover the question. Powers
# part of the self-improvement loop (researching knowledge gaps).
def tool_web_search(query: str, num_results: int = 5, **_kw) -> dict[str, Any]:
    """Search the public web for `query`. Returns top results (title/url/snippet)."""
    from services.web_search import search_web
    return search_web(query, num_results=num_results)


TOOL_REGISTRY["web_search"] = Tool(
    name="web_search", kind="read",
    description=(
        "Search the public web (Google) for current information when the "
        "knowledge base and your own knowledge don't cover the question — "
        "recent news, prices, facts, definitions, 'look this up', '검색해줘'. "
        "Returns ranked results with title, url and snippet. Cite the sources "
        "in your reply."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for"},
            "num_results": {"type": "integer", "description": "How many results (1-10)"},
        },
        "required": ["query"],
    },
    fn=tool_web_search,
)


# --- NAVER (네이버) search — incl. checking if OUR property is on Naver 부동산 ------
def tool_naver_search(query: str, kind: str = "web", realestate: bool = False,
                      num_results: int = 5, **_kw) -> dict[str, Any]:
    """Search NAVER. realestate=True checks NAVER 부동산 listings for a property."""
    from services.naver_search import naver_search
    return naver_search(query, kind=kind, num_results=num_results, realestate=realestate)


TOOL_REGISTRY["naver_search"] = Tool(
    name="naver_search", kind="read",
    description=(
        "Search NAVER (네이버) — web / news / blog / local, AND NAVER 부동산 real-estate "
        "listings. Use this whenever the user asks about NAVER specifically, OR wants "
        "to know whether one of OUR properties (land / house / building / 매물) is "
        "ADVERTISED / LISTED on NAVER 부동산 — e.g. '우리 낙하리 땅 네이버에 올라와 있어?', "
        "'is our Hyangnam apartment on Naver?', '네이버 부동산에 우리 건물 매물 있는지 "
        "확인해줘'. For a property check, set realestate=true and pass the address or "
        "name (e.g. '낙하리 301-7', '향남에일린의뜰'). If listings are found, the "
        "property IS on Naver; if none, report that it doesn't appear to be listed. "
        "Always cite the result links."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "Search terms — for a property, its address or name (e.g. '낙하리 301-7')"},
            "realestate": {"type": "boolean",
                           "description": "true to check NAVER 부동산 property listings"},
            "kind": {"type": "string", "enum": ["web", "news", "blog", "local"],
                     "description": "Naver search type (ignored when realestate=true). Default web."},
            "num_results": {"type": "integer", "description": "How many results (1-10)"},
        },
        "required": ["query"],
    },
    fn=tool_naver_search,
)


# --- OnBid (온비드 / KAMCO public-auction) live-data tool -------------------
try:
    from services.onbid_tools import tool_onbid_search
    TOOL_REGISTRY["onbid_search"] = Tool(
        name="onbid_search", kind="read",
        description=(
            "Search LIVE OnBid (온비드 / 한국자산관리공사 KAMCO) public-auction "
            "listings (공매 물건). This is the data source for ANY question about "
            "buying/auctioned real estate, buildings, apartments, land, vehicles, "
            "or equipment in Korea — use it for OnBid / 온비드 / 공매 / 경매 / "
            "압류재산 / 국유재산 questions AND for general property/auction queries "
            "like 'expensive buildings in Jeju', '제주 부동산 매물', 'cars for "
            "auction', '서울 아파트 공매'. Prefer this over web_search for Korean "
            "property/auction lookups. "
            "Pass `region` (e.g. '제주'/'Jeju'/'서울'/'Seoul'), `category` "
            "(e.g. 'real estate'/'부동산', 'car'/'자동차', 'construction'), "
            "`sort` ('expensive' = most expensive first, 'cheap' = cheapest), "
            "`keyword` (item name/detail) and `limit`. Returns items with minimum "
            "bid, appraisal price, bid open/close dates and status. Present the "
            "results clearly and cite OnBid. If `note` is set, relay it honestly "
            "(OnBid only lists assets currently up for auction, not all properties)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "Province/city filter, KO or EN (제주, Jeju, 서울, Seoul, 부산…)",
                },
                "category": {
                    "type": "string",
                    "description": "Item type: 'real estate'/'부동산', 'car'/'자동차', 'construction', 'rights', 'transport'",
                },
                "sort": {
                    "type": "string",
                    "description": "'expensive' (most expensive first) or 'cheap' (cheapest first)",
                },
                "keyword": {
                    "type": "string",
                    "description": "Optional free-text filter on item name/detail (e.g. '아파트', 'E300')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max items to return (1-20, default 8)",
                },
            },
            "required": [],
        },
        fn=tool_onbid_search,
    )
    from services.onbid_tools import tool_onbid_detail
    TOOL_REGISTRY["onbid_detail"] = Tool(
        name="onbid_detail", kind="read",
        description=(
            "Get the FULL detail of ONE OnBid auction item (the 물건 상세 page) — "
            "managing agency & phone number, full jibun + road address, land & "
            "building area, location/usage description, minimum bid, payment term "
            "(대금납부기한), delivery responsibility (인도책임) and special "
            "conditions like 전입세대/임차인. Use when the user wants EVERYTHING / "
            "the full details about a specific item ('이 물건 상세', 'tell me "
            "everything about <item>', '자세히'). Pass a `query` (the item name or "
            "keyword, e.g. '마곡동 단독주택') with optional `region`, OR the exact "
            "IDs (cltr_no + cltr_hstr_no, ideally plnm_no/pbct_no) returned by a "
            "previous onbid_search."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Item name/keyword to find the item (e.g. '마곡동 단독주택')"},
                "region": {"type": "string", "description": "Optional region to disambiguate (제주, 서울…)"},
                "cltr_no": {"type": "string", "description": "물건번호 (from a prior onbid_search item)"},
                "cltr_hstr_no": {"type": "string", "description": "물건이력번호 (from a prior onbid_search item)"},
                "plnm_no": {"type": "string", "description": "공고번호 (optional, improves the lookup)"},
                "pbct_no": {"type": "string", "description": "공매번호 (optional)"},
            },
            "required": [],
        },
        fn=tool_onbid_detail,
    )
except Exception as _e:  # never let a tool-pack failure break the assistant
    log.warning(f"onbid_tools registration skipped: {_e}")


# --- MOLIT 실거래가 (real property sale prices) tool ------------------------
try:
    from services.molit_tools import tool_realprice_search
    TOOL_REGISTRY["realprice_search"] = Tool(
        name="realprice_search", kind="read",
        description=(
            "Look up ACTUAL recorded property sale prices (국토교통부 실거래가) for "
            "a Korean area — use this for 'how much is/was X property', 시세, "
            "실거래가, 얼마, 매매가, 'price of a house/apartment in <area>', or to "
            "value/compare property NOT at auction. (For auction items use "
            "onbid_search instead.) Give `region` = the 시군구 (송파구/Songpa/수원시), "
            "optional `dong` (법정동 e.g. 거여동), `property_type` "
            "('apartment'/'아파트', 'house'/'단독주택', 'villa'/'연립다세대', "
            "'officetel'/'오피스텔', 'land'/'토지'), and optional `sort` "
            "('expensive'/'cheap'). Returns recent real transactions with price, "
            "area, build year, floor and date, plus avg/min/max. Relay `note` "
            "honestly if no sales are found."
        ),
        parameters={
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "시군구 — 송파구, Songpa, 수원시 (required)"},
                "dong": {"type": "string", "description": "Optional 법정동 within the 시군구 (거여동)"},
                "property_type": {"type": "string", "description": "apartment/아파트, house/단독주택, villa/연립다세대, officetel/오피스텔, land/토지"},
                "sort": {"type": "string", "description": "'expensive' or 'cheap' (default: most recent)"},
                "months": {"type": "integer", "description": "Recent months to scan (default 4)"},
                "limit": {"type": "integer", "description": "Max transactions (1-20, default 8)"},
            },
            "required": ["region"],
        },
        fn=tool_realprice_search,
    )
except Exception as _e:
    log.warning(f"molit_tools registration skipped: {_e}")


# --- Cross-agent query (VIP hub → other agents) ----------------------------
try:
    from services.cross_agent_tools import tool_ask_agent
    TOOL_REGISTRY["ask_agent"] = Tool(
        name="ask_agent", kind="read",
        description=(
            "Ask ANOTHER of the boss's agents a question and get its answer using "
            "THAT agent's own data, knowledge base and tools — use this whenever "
            "the user (while in VIP) asks about another agent's domain instead of "
            "guessing or just navigating. Targets: 'asset' (자산 — portfolio, "
            "occupancy, rental income, valuation, leases, tenants), 'stock' (주식 — "
            "KOSPI/KOSDAQ, watchlist, P&L, market), 'realty' (부동산 — listings/매물 "
            "search), 'aiglass', or any future agent — pass 'all' to ask every "
            "agent and build a combined report. Examples: 'what's my total asset "
            "value?' → ask_agent('asset', …); 'how's my stock portfolio?' → "
            "ask_agent('stock', …); 'give me a report across all my agents about "
            "X' → ask_agent('all', 'X'). Then compose the answer/report and cite "
            "which agent each fact came from."
        ),
        parameters={
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Target agent: asset/stock/realty/aiglass, a domain word (자산/주식/부동산), or 'all'"},
                "question": {"type": "string", "description": "The question to ask that agent (in the user's language)"},
            },
            "required": ["agent", "question"],
        },
        fn=tool_ask_agent,
    )
except Exception as _e:
    log.warning(f"cross_agent_tools registration skipped: {_e}")


# --- Stock Advisor live-data tools -----------------------------------------
# Registered from a separate module (passing TOOL_REGISTRY + Tool to avoid a
# circular import). These give the OASIS Stock agent real-time access to its
# backend (recommendations, intraday signals, market flows, portfolio, …).
try:
    from services.stock_data_tools import register_stock_data_tools
    register_stock_data_tools(TOOL_REGISTRY, Tool)
except Exception as _e:  # never let a tool-pack failure break the assistant
    log.warning(f"stock_data_tools registration skipped: {_e}")


# ============================================================================
#  Per-agent tool scoping — data isolation
# ============================================================================
# Each agent's assistant must only see ITS OWN domain tools so it can't read or
# act on another agent's data. VIP is the boss hub and keeps everything.
# Everything NOT in an agent's allow-set is hidden AND blocked at execution.

# Safe, per-agent-scoped tools every agent may use (own KB, own pages, web).
_GENERIC_TOOLS = {
    "navigate", "open_portal", "find_page", "list_pages", "open_item",
    "what_can_you_do", "web_search", "naver_search",
    "search_knowledge", "search_knowledge_base", "list_knowledge_files",
    "add_knowledge", "update_knowledge", "delete_knowledge", "delete_knowledge_file",
    "semantic_search",
    # Read-only single-stock price lookup — safe cross-domain so EVERY agent's
    # chatbot quotes the SAME correct Kiwoom price (no hallucinated figures).
    "stock_quote", "stock_price_history",
}
_PROPERTY_TOOLS = {"onbid_search", "onbid_detail", "realprice_search"}


def allowed_tool_names(agent_id: Optional[str]) -> set[str]:
    """The set of tool names a given agent's assistant may see/use."""
    aid = (agent_id or "vip").lower()
    if aid == "vip":
        return set(TOOL_REGISTRY.keys())          # VIP hub = everything
    allowed = set(_GENERIC_TOOLS)
    if aid == "stock":
        allowed |= {n for n in TOOL_REGISTRY if n.startswith("stock_")}
    elif aid in ("realty", "aiglass"):
        allowed |= _PROPERTY_TOOLS
    elif aid == "asset":
        # The Asset agent owns the company real-estate portfolio tools.
        allowed |= {"asset_summary", "asset_search", "asset_top"}
    return {n for n in allowed if n in TOOL_REGISTRY}


def list_tool_schemas(agent_id: Optional[str] = None) -> list[dict]:
    """Return the tool schemas this agent may use (VIP=all; others scoped)."""
    if agent_id is None:
        return [t.schema() for t in TOOL_REGISTRY.values()]
    names = allowed_tool_names(agent_id)
    return [t.schema() for n, t in TOOL_REGISTRY.items() if n in names]


def get_tool(name: str) -> Optional[Tool]:
    return TOOL_REGISTRY.get(name)


def _fn_accepts(fn, param: str) -> bool:
    """True if `fn` declares `param` explicitly or accepts **kwargs."""
    try:
        import inspect
        sig = inspect.signature(fn)
        for p in sig.parameters.values():
            if p.name == param or p.kind == inspect.Parameter.VAR_KEYWORD:
                return True
    except (TypeError, ValueError):
        return True  # builtins / un-introspectable — let the call try
    return False


def execute_tool(name: str, args: dict, db: Session = None, agent_id: str = "vip",
                 transcript: str = "") -> dict[str, Any]:
    """Execute a tool by name with the given args. Returns the tool's
    structured result. NEVER raises — always returns a dict with 'ok' key.

    `agent_id` is injected so tools can be per-agent aware (capabilities, page
    lists, knowledge scope). It is only passed to tools that accept it, so the
    LLM cannot accidentally override agent scoping by supplying its own value.
    """
    tool = get_tool(name)
    if not tool:
        return {"ok": False, "error": f"Unknown tool '{name}'"}
    # Data isolation: block tools outside this agent's domain (defence in depth —
    # the LLM shouldn't even see them, but never execute one if it tries).
    if name not in allowed_tool_names(agent_id):
        return {"ok": False,
                "error": f"Tool '{name}' is not available to this agent."}
    try:
        call_args = dict(args or {})
        if _fn_accepts(tool.fn, "agent_id"):
            call_args["agent_id"] = agent_id  # authoritative — overrides any LLM-supplied value
        if _fn_accepts(tool.fn, "user_transcript"):
            # Original user message — lets a tool recover when the LLM passes a
            # garbage/hallucinated arg (e.g. a bad ticker-name string).
            call_args.setdefault("user_transcript", transcript)
        result = tool.fn(**call_args, db=db)
        if not isinstance(result, dict):
            return {"ok": False, "error": f"Tool '{name}' returned non-dict"}
        return result
    except TypeError as e:
        return {"ok": False, "error": f"Bad args for {name}: {e}"}
    except Exception as e:
        log.warning(f"execute_tool {name} failed: {e}")
        return {"ok": False, "error": str(e)[:300]}
