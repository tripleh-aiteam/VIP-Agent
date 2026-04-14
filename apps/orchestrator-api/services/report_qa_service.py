"""
VIP AI Platform — Report QA Service
Loads report context, answers follow-up questions grounded in stored data only.
Never invents facts — only uses data from report sections.
"""

import re
from typing import Any

from sqlalchemy.orm import Session

from db.models import OrchReport
from services.logger import log


# ---------------------------------------------------------------------------
# Report context loader
# ---------------------------------------------------------------------------

def load_report_context(db: Session, report_id: str | None = None, report_type: str = "daily_summary") -> dict | None:
    """Load a report's full context for QA. Uses specific ID or latest of type."""
    from uuid import UUID

    if report_id:
        try:
            report = db.query(OrchReport).filter(OrchReport.id == UUID(report_id)).first()
        except ValueError:
            report = None
    else:
        report = db.query(OrchReport).filter(
            OrchReport.report_type == report_type
        ).order_by(OrchReport.created_at.desc()).first()

    if not report:
        return None

    content = report.content_json or {}
    sections = content.get("sections", [])

    return {
        "report_id": str(report.id),
        "report_type": report.report_type,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "executive_summary": content.get("executive_summary", ""),
        "sections": sections,
        "section_map": {s.get("title", "").lower(): s for s in sections},
        "trace_references": content.get("trace_references", []),
        "source_run_count": content.get("source_run_count", 0),
    }


# ---------------------------------------------------------------------------
# Question classification (what aspect of the report?)
# ---------------------------------------------------------------------------

QUESTION_PATTERNS = [
    ("summary", [r"explain.*summary", r"today.*summary", r"what.*report.*say", r"overview", r"summarize", r"tell.*about.*report"]),
    ("risk", [r"biggest?\s+risk", r"what.*risk", r"risk.*factor", r"danger", r"concern", r"warning", r"key\s+risk"]),
    ("agent_source", [r"which\s+agent", r"who\s+found", r"where.*come\s+from", r"source.*data", r"agent.*responsible"]),
    ("comparison", [r"compare", r"difference.*between", r"stock.*vs.*real", r"real.*vs.*stock", r"asset.*vs"]),
    ("approval_needed", [r"what\s+needs\s+approv", r"pending.*action", r"action\s+required", r"what.*do\s+next"]),
    ("asset_detail", [r"asset", r"portfolio", r"holding", r"total\s+value"]),
    ("stock_detail", [r"stock", r"market", r"sentiment", r"buy.*sell", r"bull.*bear"]),
    ("realty_detail", [r"real\s*estate", r"realty", r"property", r"listing", r"vacancy", r"yield"]),
    ("coverage", [r"missing", r"data\s+coverage", r"incomplete", r"what.*missing"]),
]


def classify_question(text: str) -> str:
    """Classify what aspect of the report the user is asking about."""
    lower = text.lower()
    for category, patterns in QUESTION_PATTERNS:
        for p in patterns:
            if re.search(p, lower):
                return category
    return "summary"  # default to summary


# ---------------------------------------------------------------------------
# Answer builder — grounded in report data only
# ---------------------------------------------------------------------------

def answer_question(report_ctx: dict, question: str) -> dict:
    """
    Answer a question about a report using ONLY stored data.
    Never invents facts.
    Returns answer + supporting sections + linked references.
    """
    category = classify_question(question)
    section_map = report_ctx["section_map"]
    sections_used = []
    answer = ""

    if category == "summary":
        answer = report_ctx["executive_summary"]
        sections_used = [s["title"] for s in report_ctx["sections"]]

    elif category == "risk":
        risk_section = section_map.get("key risks")
        if risk_section:
            answer = risk_section["content"]
            sections_used = ["Key Risks"]
            data = risk_section.get("data", {})
            if data:
                parts = []
                if data.get("rejected"):
                    parts.append(f"{data['rejected']} task(s) rejected")
                if data.get("pending_review"):
                    parts.append(f"{data['pending_review']} pending review")
                if data.get("high_risk"):
                    parts.append(f"{data['high_risk']} high-risk evaluation(s)")
                if parts:
                    answer += "\n\nDetails: " + ", ".join(parts)
        else:
            answer = "No risk section found in this report."

    elif category == "agent_source":
        # Look at trace references and sections
        traces = report_ctx.get("trace_references", [])
        source_count = report_ctx.get("source_run_count", 0)
        section_titles = [s["title"] for s in report_ctx["sections"]]
        answer = (
            f"This report was compiled from {source_count} task run(s) across multiple agents.\n"
            f"Sections: {', '.join(section_titles)}\n"
            f"Trace references: {len(traces)}"
        )
        if traces:
            answer += f"\nSample traces: {', '.join(traces[:5])}"
        sections_used = section_titles

    elif category == "comparison":
        stock_section = section_map.get("stock market summary")
        realty_section = section_map.get("real estate summary")
        asset_section = section_map.get("asset summary")

        parts = []
        used = []
        if stock_section:
            parts.append(f"Stock: {stock_section['content']}")
            used.append("Stock Market Summary")
        if realty_section:
            parts.append(f"Real Estate: {realty_section['content']}")
            used.append("Real Estate Summary")
        if asset_section:
            parts.append(f"Asset: {asset_section['content']}")
            used.append("Asset Summary")

        if parts:
            answer = "Comparison:\n\n" + "\n\n".join(parts)
        else:
            answer = "Not enough sections available for comparison."
        sections_used = used

    elif category == "approval_needed":
        risk_section = section_map.get("key risks")
        if risk_section:
            data = risk_section.get("data", {})
            pending = data.get("pending_review", 0)
            rejected = data.get("rejected", 0)
            if pending > 0 or rejected > 0:
                answer = f"Action required:\n- {pending} task(s) pending human review\n- {rejected} task(s) were rejected\n\nUse 'show pending approvals' to see details."
            else:
                answer = "No actions required. All evaluations passed."
            sections_used = ["Key Risks"]
        else:
            answer = "No risk data available. Try 'show pending approvals' for current queue."

    elif category == "asset_detail":
        section = section_map.get("asset summary")
        if section:
            answer = section["content"]
            data = section.get("data", {})
            if data:
                answer += f"\n\nTotal value: {data.get('total_value', 'N/A'):,} KRW" if isinstance(data.get('total_value'), (int, float)) else ""
                answer += f"\nHoldings: {data.get('holdings_count', 'N/A')}"
                answer += f"\nRisk level: {data.get('risk_level', 'N/A')}"
            sections_used = ["Asset Summary"]
        else:
            answer = "No asset data in this report."

    elif category == "stock_detail":
        section = section_map.get("stock market summary")
        if section:
            answer = section["content"]
            data = section.get("data", {})
            if data:
                answer += f"\n\nSentiment: {data.get('sentiment', 'N/A')}"
                answer += f"\nAvg risk: {data.get('avg_risk', 'N/A')}"
                answer += f"\nBuy signals: {data.get('buys', 0)}, Sell signals: {data.get('sells', 0)}"
            sections_used = ["Stock Market Summary"]
        else:
            answer = "No stock data in this report."

    elif category == "realty_detail":
        section = section_map.get("real estate summary")
        if section:
            answer = section["content"]
            data = section.get("data", {})
            if data:
                answer += f"\n\nVacancy: {data.get('avg_vacancy_pct', 'N/A')}%"
                answer += f"\nYield: {data.get('avg_yield_pct', 'N/A')}%"
                answer += f"\nListings: {data.get('total_listings', 'N/A')}"
            sections_used = ["Real Estate Summary"]
        else:
            answer = "No real estate data in this report."

    elif category == "coverage":
        section = section_map.get("data coverage")
        if section:
            answer = section["content"]
            data = section.get("data", {})
            if data.get("missing"):
                answer += f"\n\nMissing: {', '.join(data['missing'])}"
            sections_used = ["Data Coverage"]
        else:
            answer = "No coverage information available."

    log.info(f"report-qa: category={category}, sections={sections_used}", extra={"action": "report_qa.answer"})

    return {
        "answer": answer,
        "question_category": category,
        "sections_used": sections_used,
        "report_id": report_ctx["report_id"],
        "report_type": report_ctx["report_type"],
        "grounded": True,  # all answers come from stored data
    }
