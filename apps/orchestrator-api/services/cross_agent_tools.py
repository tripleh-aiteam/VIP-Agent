"""
cross_agent_tools — let one agent's assistant query the OTHER agents.

All agents (VIP, Asset, Stock, Realty, AIGlass, …) are served by this same
orchestrator, scoped by `agent_id` (each has its own profile + knowledge base +
data). So from the VIP assistant we can answer a question "about the Asset agent"
by simply running the assistant pipeline with `agent_id="asset"` and returning
its answer — no HTTP hop, full reuse of that agent's own tools and KB.

`ask_agent(agent, question)`:
  - resolves `agent` (name / id / domain word, KO or EN, or "all") to one or more
    target agent_ids — dynamically from AGENT_PROFILES, so a newly-added agent is
    reachable automatically once it has a profile;
  - runs each target agent's assistant on `question`;
  - returns their answers for the calling assistant to compose into a reply/report.

A depth guard prevents an agent from recursively cross-querying (no loops).
"""

from __future__ import annotations

import contextvars
from typing import Any

from services.logger import log

_DEPTH = contextvars.ContextVar("cross_agent_depth", default=0)

# Domain words (KO/EN) that should route to each agent even if the user doesn't
# say the agent's name. Extend when you add an agent.
_DOMAIN_HINTS: dict[str, list[str]] = {
    "asset": ["asset", "assets", "portfolio", "holdings", "occupancy", "rental",
              "valuation", "yield", "lease", "tenant", "자산", "임대", "수익률",
              "점유", "임차", "보유"],
    "stock": ["stock", "stocks", "equit", "market", "kospi", "kosdaq", "share",
              "watchlist", "ticker", "주식", "증시", "코스피", "코스닥", "종목",
              "시세", "관심종목"],
    "realty": ["realty", "real estate", "listing", "listings", "property search",
               "매물", "부동산", "리스팅", "매물검색"],
    "aiglass": ["aiglass", "ai glass", "smart glass", "스마트글라스", "증강현실",
                "ar glass"],
}


def _known_agents() -> dict[str, str]:
    """short agent_id -> human label, sourced from AGENT_PROFILES (dynamic)."""
    try:
        from services.assistant_agent import AGENT_PROFILES
        return {k.lower(): (v.get("name") or k) for k, v in AGENT_PROFILES.items()}
    except Exception:
        return {"vip": "VIP", "asset": "Asset", "stock": "Stock",
                "realty": "Realty", "aiglass": "AIGlass"}


def list_queryable_agents(current: str = "vip") -> list[dict[str, str]]:
    cur = (current or "vip").lower()
    return [{"id": k, "label": v} for k, v in _known_agents().items()
            if k not in ("vip", cur)]


def _resolve_targets(agent: str, current: str) -> list[str]:
    known = _known_agents()
    cur = (current or "vip").lower()
    a = (agent or "").strip().lower()

    if a in ("all", "everyone", "every agent", "all agents", "모두", "전체", "다"):
        return [k for k in known if k not in ("vip", cur)]

    if a in known and a != cur:
        return [a]

    hits: list[str] = []
    for aid, label in known.items():
        if aid in ("vip", cur):
            continue
        hay = f"{aid} {label}".lower()
        if a and (a in hay or aid in a):
            hits.append(aid)
            continue
        if any(h in a for h in _DOMAIN_HINTS.get(aid, [])):
            hits.append(aid)

    out: list[str] = []
    for h in hits:
        if h not in out:
            out.append(h)
    return out


def tool_ask_agent(agent: str = "", question: str = "", history=None, db=None,
                   agent_id: str = "vip", **_kw) -> dict[str, Any]:
    """Ask another of your agents (Asset / Stock / Realty / AIGlass / any future
    agent) a question and get its answer, using that agent's own data & tools.
    `agent` = the target ('asset', 'stock', 'realty', '자산', or 'all'); `question`
    = what to ask it. Use for cross-agent questions/reports while staying in VIP.
    """
    q = (question or "").strip()
    if not q:
        return {"ok": False, "error": "Provide the `question` to ask the other agent."}

    targets = _resolve_targets(agent, agent_id)
    if not targets:
        avail = ", ".join(a["id"] for a in list_queryable_agents(agent_id)) or "(none)"
        return {"ok": False,
                "error": f"Couldn't match '{agent}' to one of your agents. "
                         f"Available: {avail}. Pass one of those (or 'all')."}

    if _DEPTH.get() >= 1:
        return {"ok": False,
                "error": "Nested cross-agent query blocked (loop prevention)."}

    token = _DEPTH.set(_DEPTH.get() + 1)
    answers: list[dict[str, Any]] = []
    try:
        from services.assistant_agent import run_agent
        for t in targets[:4]:
            try:
                res = run_agent(db, transcript=q, agent_id=t, language="auto",
                                history=history or None, user_id=None)
                answers.append({
                    "agent": t,
                    "agent_label": _known_agents().get(t, t),
                    "answer": (res.get("reply") or "").strip() or None,
                    "tool_used": res.get("tool_used") or res.get("intent"),
                })
            except Exception as e:
                log.warning(f"ask_agent target '{t}' failed: {e}")
                answers.append({"agent": t, "answer": None, "error": str(e)[:160]})
    finally:
        _DEPTH.reset(token)

    got = [a for a in answers if a.get("answer")]
    return {
        "ok": True,
        "question": q,
        "asked": [a["agent"] for a in answers],
        "count": len(got),
        "answers": answers,
        "note": "These answers come from your other agents' own data. Compose them "
                "into your reply" + (" / a combined report" if len(answers) > 1 else "")
                + ". Cite which agent each fact came from.",
    }
