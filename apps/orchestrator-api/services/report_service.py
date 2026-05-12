"""
VIP AI Platform — Report Composer Service
Collects drafts from multiple agents, merges into executive summary.
Outputs both JSON and Markdown versions.
"""

from datetime import datetime, timedelta
from uuid import UUID
from typing import Any

from sqlalchemy.orm import Session

from db.models import OrchReport, OrchTaskRun, AuditJudgementCase
from services.audit_service import record_event
from services.logger import log


# ---------------------------------------------------------------------------
# Draft collection — gathers data from recent task runs
# ---------------------------------------------------------------------------

def collect_drafts(
    db: Session,
    hours_back: int = 24,
    status_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Collect data from recent task runs grouped by agent type."""
    cutoff = datetime.utcnow() - timedelta(hours=hours_back)
    statuses = status_filter or ["completed", "review_required"]

    runs = (
        db.query(OrchTaskRun)
        .filter(OrchTaskRun.started_at >= cutoff, OrchTaskRun.status.in_(statuses))
        .order_by(OrchTaskRun.started_at.desc())
        .all()
    )

    drafts = {
        "asset": [],
        "stock": [],
        "realty": [],
        "other": [],
        "run_ids": [],
        "trace_ids": set(),
    }

    for run in runs:
        agent_type = run.target_agent.type if run.target_agent else "other"
        bucket = drafts.get(agent_type, drafts["other"])
        bucket.append({
            "run_id": str(run.id),
            "task_type": run.task_definition.task_type if run.task_definition else None,
            "trace_id": run.trace_id,
            "status": run.status,
            "output": run.output_payload or {},
            "agent": run.target_agent.name if run.target_agent else "unknown",
            "started_at": run.started_at.isoformat() if run.started_at else None,
        })
        drafts["run_ids"].append(str(run.id))
        if run.trace_id:
            drafts["trace_ids"].add(run.trace_id)

    # Collect judgement flags
    judgement_cases = (
        db.query(AuditJudgementCase)
        .filter(AuditJudgementCase.created_at >= cutoff)
        .all()
    )
    drafts["judgement_flags"] = [
        {
            "case_id": str(c.id),
            "task_run_id": str(c.task_run_id),
            "risk_score": c.risk_score,
            "decision": c.decision,
        }
        for c in judgement_cases
    ]

    drafts["trace_ids"] = list(drafts["trace_ids"])
    return drafts


# ---------------------------------------------------------------------------
# Merge logic — deterministic merge of drafts into sections
# ---------------------------------------------------------------------------

def _build_asset_section(drafts: list[dict]) -> dict:
    if not drafts:
        return {"title": "Asset Summary", "content": "No asset data available for this period.", "data": {}}

    total_value = 0
    holdings_count = 0
    risk_levels = []

    for d in drafts:
        out = d["output"]
        total_value += out.get("total_value", 0)
        holdings_count += out.get("asset_count", 0)
        if out.get("risk_level"):
            risk_levels.append(out["risk_level"])

    avg_risk = max(set(risk_levels), key=risk_levels.count) if risk_levels else "unknown"

    return {
        "title": "Asset Summary",
        "content": f"Total portfolio value: {total_value:,.0f} KRW across {holdings_count} holdings. Overall risk: {avg_risk}.",
        "data": {"total_value": total_value, "holdings_count": holdings_count, "risk_level": avg_risk, "reports_count": len(drafts)},
    }


def _build_stock_section(drafts: list[dict]) -> dict:
    if not drafts:
        return {"title": "Stock Market Summary", "content": "No stock data available for this period.", "data": {}}

    all_stocks = []
    sentiments = []
    risk_scores = []

    for d in drafts:
        out = d["output"]
        all_stocks.extend(out.get("stocks", []))
        if out.get("market_sentiment"):
            sentiments.append(out["market_sentiment"])
        if out.get("risk_score"):
            risk_scores.append(out["risk_score"])

    avg_risk = round(sum(risk_scores) / len(risk_scores), 2) if risk_scores else 0
    dominant_sentiment = max(set(sentiments), key=sentiments.count) if sentiments else "unknown"

    buys = [s for s in all_stocks if s.get("recommendation") == "buy"]
    sells = [s for s in all_stocks if s.get("recommendation") == "sell"]

    return {
        "title": "Stock Market Summary",
        "content": f"Analyzed {len(all_stocks)} stocks. Sentiment: {dominant_sentiment}. Avg risk: {avg_risk}. Buy signals: {len(buys)}, Sell signals: {len(sells)}.",
        "data": {"stocks_analyzed": len(all_stocks), "sentiment": dominant_sentiment, "avg_risk": avg_risk, "buys": len(buys), "sells": len(sells)},
    }


def _build_realty_section(drafts: list[dict]) -> dict:
    if not drafts:
        return {"title": "Real Estate Summary", "content": "No realty data available for this period.", "data": {}}

    total_listings = 0
    avg_vacancy = []
    avg_yield = []

    for d in drafts:
        out = d["output"]
        total_listings += out.get("total_listings", 0)
        if out.get("avg_vacancy_pct"):
            avg_vacancy.append(out["avg_vacancy_pct"])
        if out.get("avg_yield_pct"):
            avg_yield.append(out["avg_yield_pct"])

    vacancy = round(sum(avg_vacancy) / len(avg_vacancy), 1) if avg_vacancy else 0
    yield_pct = round(sum(avg_yield) / len(avg_yield), 1) if avg_yield else 0

    return {
        "title": "Real Estate Summary",
        "content": f"Found {total_listings} properties. Avg vacancy: {vacancy}%. Avg yield: {yield_pct}%.",
        "data": {"total_listings": total_listings, "avg_vacancy_pct": vacancy, "avg_yield_pct": yield_pct},
    }


def _build_risks_section(judgement_flags: list[dict]) -> dict:
    if not judgement_flags:
        return {"title": "Key Risks", "content": "No risk flags for this period.", "data": {}}

    rejected = [f for f in judgement_flags if f["decision"] == "rejected"]
    review = [f for f in judgement_flags if f["decision"] in ("human_review_required", "conditional_approve")]
    high_risk = [f for f in judgement_flags if (f.get("risk_score") or 0) > 0.5]

    parts = []
    if rejected:
        parts.append(f"{len(rejected)} task(s) rejected by judgement")
    if review:
        parts.append(f"{len(review)} task(s) pending human review")
    if high_risk:
        parts.append(f"{len(high_risk)} high-risk evaluation(s)")

    return {
        "title": "Key Risks",
        "content": ". ".join(parts) if parts else "All evaluations within acceptable risk.",
        "data": {"rejected": len(rejected), "pending_review": len(review), "high_risk": len(high_risk)},
    }


def _build_missing_data(drafts: dict) -> dict:
    missing = []
    if not drafts["asset"]:
        missing.append("Asset data")
    if not drafts["stock"]:
        missing.append("Stock data")
    if not drafts["realty"]:
        missing.append("Realty data")

    if not missing:
        return {"title": "Data Coverage", "content": "All data sources reported successfully.", "data": {"complete": True}}

    return {
        "title": "Data Coverage",
        "content": f"Missing data: {', '.join(missing)}. Report may be incomplete.",
        "data": {"complete": False, "missing": missing},
    }


def merge_sections(drafts: dict) -> list[dict]:
    """Merge all drafts into report sections."""
    return [
        _build_asset_section(drafts["asset"]),
        _build_stock_section(drafts["stock"]),
        _build_realty_section(drafts["realty"]),
        _build_risks_section(drafts.get("judgement_flags", [])),
        _build_missing_data(drafts),
    ]


# ---------------------------------------------------------------------------
# Executive summary generation
# ---------------------------------------------------------------------------

def generate_executive_summary(sections: list[dict], report_type: str) -> str:
    """
    Generate an executive summary. Tries LLM first for a richer briefing;
    falls back to a template if LLM unavailable (so reports never fail to compose).
    """
    parts = []
    for s in sections:
        if s["data"] and s["content"] != "No data available for this period.":
            parts.append(s["content"])

    period = "daily" if "daily" in report_type else "weekly" if "weekly" in report_type else "alert"
    template = f"VIP {period.title()} Report: " + " ".join(parts[:3])

    if not parts:
        return template

    # Try LLM for richer summary — degrade gracefully if it fails
    try:
        from services.llm_client import chat_completion_sync
        section_text = "\n".join(f"- {s['title']}: {s['content']}" for s in sections if s.get("content"))
        system_prompt = (
            "You are an executive briefing writer for the VIP boss. "
            "Write a concise 2-3 sentence summary of the period's data. "
            "Highlight what matters: anomalies, key numbers, action items. "
            "No markdown, no lists — just clean prose suitable for a daily briefing."
        )
        user_prompt = f"Report type: {report_type}\nPeriod: {period}\n\nSections:\n{section_text}\n\nWrite the executive summary."
        result = chat_completion_sync(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=200,
            temperature=0.5,
            model="claude-haiku-4-5",
        )
        if result and not result.startswith("[LLM unavailable]") and len(result.strip()) > 20:
            return result.strip()
    except Exception:
        pass
    return template


# ---------------------------------------------------------------------------
# Template rendering — Markdown
# ---------------------------------------------------------------------------

def render_markdown(
    report_type: str,
    executive_summary: str,
    sections: list[dict],
    trace_ids: list[str],
    generated_at: str,
) -> str:
    """Render report as Markdown."""
    lines = [
        f"# VIP Agent Platform — {report_type.replace('_', ' ').title()}",
        f"*Generated: {generated_at}*",
        "",
        "## Executive Summary",
        executive_summary,
        "",
    ]

    for s in sections:
        lines.append(f"## {s['title']}")
        lines.append(s["content"])
        lines.append("")

    if trace_ids:
        lines.append("## Trace References")
        for tid in trace_ids[:10]:
            lines.append(f"- `{tid}`")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Compose endpoints
# ---------------------------------------------------------------------------

def compose_report(
    db: Session,
    report_type: str,
    hours_back: int = 24,
    delivery_channel: str = "web",
    trace_id: str = "system",
) -> dict:
    """Full report composition pipeline."""

    # Collect
    drafts = collect_drafts(db, hours_back=hours_back)

    # Merge
    sections = merge_sections(drafts)

    # Executive summary
    exec_summary = generate_executive_summary(sections, report_type)

    # Timestamps
    generated_at = datetime.utcnow().isoformat()

    # Markdown
    markdown = render_markdown(report_type, exec_summary, sections, drafts["trace_ids"], generated_at)

    # JSON content
    content_json = {
        "report_type": report_type,
        "executive_summary": exec_summary,
        "sections": sections,
        "trace_references": drafts["trace_ids"],
        "source_run_count": len(drafts["run_ids"]),
        "generated_at": generated_at,
        "markdown": markdown,
    }

    # Persist
    report = OrchReport(
        report_type=report_type,
        source_run_ids_json=drafts["run_ids"],
        content_json=content_json,
        delivery_channel=delivery_channel,
    )
    db.add(report)
    db.flush()

    record_event(db, "report-composer", f"report.{report_type}", trace_id, {
        "report_id": str(report.id),
        "source_runs": len(drafts["run_ids"]),
        "sections": len(sections),
    })

    log.info(
        f"report composed: {report_type} ({len(drafts['run_ids'])} runs, {len(sections)} sections)",
        extra={"trace_id": trace_id, "action": f"report.{report_type}"},
    )

    db.commit()

    # Phase 3: score the generated report and flag if low quality
    try:
        from services.report_quality import log_report_quality
        quality = log_report_quality(str(report.id), {
            "report_type": report_type,
            "executive_summary": exec_summary,
            "sections": sections,
        })
    except Exception:
        quality = None

    return {
        "report_id": str(report.id),
        "report_type": report_type,
        "executive_summary": exec_summary,
        "sections": sections,
        "source_run_count": len(drafts["run_ids"]),
        "trace_references": drafts["trace_ids"],
        "delivery_channel": delivery_channel,
        "generated_at": generated_at,
        "markdown": markdown,
        "quality": quality,
    }


# ---------------------------------------------------------------------------
# Cross-agent combined report — fetches real data from multiple agents via A2A
# ---------------------------------------------------------------------------

def compose_cross_agent_report(
    db: Session,
    agent_types: list[str],
    report_type: str = "cross_agent_summary",
    trace_id: str = "system",
    delivery_channel: str = "web",
) -> dict:
    """
    Compose a report by fetching real-time data from multiple agents via A2A.
    Unlike compose_report() which uses historical task runs, this actively
    queries agents and combines their responses.
    """
    from services.a2a_service import request_data_from_agent

    generated_at = datetime.utcnow().isoformat()
    sections = []
    a2a_chain = []
    agent_results = {}

    # Fetch data from each agent type
    for agent_type in agent_types:
        try:
            data_result = request_data_from_agent(
                db=db,
                requester_agent_id=_get_report_requester(agent_types, agent_type),
                target_agent_type=agent_type,
                trace_id=trace_id,
                data_request=f"{agent_type}_summary_for_report",
                context={"report_type": report_type, "real_time": True},
            )
            agent_results[agent_type] = data_result.get("data", {})
            a2a_chain.extend(data_result.get("a2a_chain", []))

            # Build section from real data
            section = _build_section_from_a2a_data(agent_type, data_result)
            sections.append(section)

        except Exception as e:
            sections.append({
                "title": f"{agent_type.title()} Summary",
                "content": f"Failed to fetch data from {agent_type} agent: {str(e)}",
                "data": {"error": str(e), "agent_type": agent_type},
            })

    # Add cross-agent insights section
    insights = _build_cross_agent_insights(agent_results)
    if insights:
        sections.append(insights)

    # Executive summary
    exec_summary = _generate_cross_agent_summary(sections, report_type, agent_types)

    # Markdown
    markdown = render_markdown(report_type, exec_summary, sections, [trace_id], generated_at)

    # Persist
    content_json = {
        "report_type": report_type,
        "executive_summary": exec_summary,
        "sections": sections,
        "agent_types": agent_types,
        "a2a_message_chain": a2a_chain,
        "generated_at": generated_at,
        "markdown": markdown,
    }

    report = OrchReport(
        report_type=report_type,
        source_run_ids_json=a2a_chain,
        content_json=content_json,
        delivery_channel=delivery_channel,
    )
    db.add(report)
    db.flush()

    record_event(db, "report-composer", f"report.cross_agent.{report_type}", trace_id, {
        "report_id": str(report.id),
        "agent_types": agent_types,
        "sections": len(sections),
        "a2a_messages": len(a2a_chain),
    })

    log.info(
        f"cross-agent report: {report_type} from {len(agent_types)} agents, {len(a2a_chain)} A2A messages",
        extra={"trace_id": trace_id, "action": f"report.cross_agent.{report_type}"},
    )

    db.commit()

    return {
        "report_id": str(report.id),
        "report_type": report_type,
        "executive_summary": exec_summary,
        "sections": sections,
        "agent_types": agent_types,
        "a2a_message_chain": a2a_chain,
        "delivery_channel": delivery_channel,
        "generated_at": generated_at,
        "markdown": markdown,
    }


def _get_report_requester(all_types: list[str], current_type: str) -> str:
    """Pick a requester agent name (the first agent that isn't the target)."""
    type_to_name = {"asset": "Asset Agent", "stock": "Stock Agent", "realty": "Real Estate Agent"}
    for t in all_types:
        if t != current_type:
            return type_to_name.get(t, f"{t.title()} Agent")
    return type_to_name.get(current_type, f"{current_type.title()} Agent")


def _build_section_from_a2a_data(agent_type: str, data_result: dict) -> dict:
    """Build a report section from A2A fetched data."""
    data = data_result.get("data", {})
    success = data_result.get("success", False)
    summary = data_result.get("summary")
    agent_name = data_result.get("target", agent_type)

    title_map = {
        "asset": "Asset Portfolio Summary",
        "stock": "Stock Market Analysis",
        "realty": "Real Estate Overview",
    }

    if not success:
        return {
            "title": title_map.get(agent_type, f"{agent_type.title()} Summary"),
            "content": f"Data fetch from {agent_name} did not return results.",
            "data": {"success": False, "agent": agent_name},
        }

    content = summary or f"Data received from {agent_name}."

    # Extract key metrics based on agent type
    metrics = {}
    if agent_type == "asset":
        metrics = {
            "total_value": data.get("total_value"),
            "holdings": data.get("asset_count"),
            "risk_level": data.get("risk_level"),
        }
    elif agent_type == "stock":
        metrics = {
            "stocks_analyzed": data.get("symbols_analyzed"),
            "sentiment": data.get("market_sentiment"),
            "risk_score": data.get("risk_score"),
        }
    elif agent_type == "realty":
        metrics = {
            "listings": data.get("total_listings"),
            "avg_vacancy": data.get("avg_vacancy_pct"),
            "avg_yield": data.get("avg_yield_pct"),
        }

    # Clean None values
    metrics = {k: v for k, v in metrics.items() if v is not None}

    return {
        "title": title_map.get(agent_type, f"{agent_type.title()} Summary"),
        "content": content,
        "data": {"success": True, "agent": agent_name, "metrics": metrics, **data},
    }


def _build_cross_agent_insights(agent_results: dict) -> dict | None:
    """Build insights by comparing data across agents."""
    if len(agent_results) < 2:
        return None

    insights = []

    # Compare asset and stock risk if both available
    asset_data = agent_results.get("asset", {})
    stock_data = agent_results.get("stock", {})

    if asset_data and stock_data:
        asset_risk = asset_data.get("risk_level", "unknown")
        stock_risk = stock_data.get("risk_score", 0)
        if stock_risk and isinstance(stock_risk, (int, float)) and stock_risk > 50:
            insights.append(f"Stock market risk elevated ({stock_risk}/100) — portfolio risk level: {asset_risk}")
        elif asset_risk and asset_risk != "unknown":
            insights.append(f"Portfolio risk ({asset_risk}) aligned with market conditions")

    # Check realty + stock correlation
    realty_data = agent_results.get("realty", {})
    if realty_data and stock_data:
        insights.append("Real estate and equity market data available for diversification analysis")

    if not insights:
        insights.append("Cross-agent data collected successfully. No anomalies detected.")

    return {
        "title": "Cross-Agent Insights",
        "content": " | ".join(insights),
        "data": {"insight_count": len(insights), "agents_compared": list(agent_results.keys())},
    }


def _generate_cross_agent_summary(sections: list[dict], report_type: str, agent_types: list[str]) -> str:
    """Generate executive summary for cross-agent report."""
    agent_names = ", ".join([t.title() for t in agent_types])
    parts = [f"Cross-Agent Report ({agent_names}):"]

    for s in sections:
        if s.get("data", {}).get("success", True) and s["content"]:
            parts.append(s["content"][:200])

    return " ".join(parts[:4])


def get_report(db: Session, report_id: UUID) -> dict | None:
    report = db.query(OrchReport).filter(OrchReport.id == report_id).first()
    if not report:
        return None
    return {
        "id": str(report.id),
        "report_type": report.report_type,
        "content": report.content_json,
        "source_run_ids": report.source_run_ids_json,
        "delivery_channel": report.delivery_channel,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }
