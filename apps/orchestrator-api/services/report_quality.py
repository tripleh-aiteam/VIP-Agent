"""
VIP AI Platform — Phase 3 Report Quality Scorer

Scores generated reports 0-100 based on:
- Length (too short = suspicious)
- Numeric content (real numbers, not just "$0" / "N/A")
- Section completeness
- Narrative coherence (rough heuristic without re-calling LLM)

Reports below threshold get auto-flagged in the OrchEvent log so the user
can spot bad outputs in the health dashboard.
"""

from __future__ import annotations

import re
from typing import Any

from services.logger import log


# ---------------------------------------------------------------------------
# Scoring heuristics
# ---------------------------------------------------------------------------

def _length_score(content: str) -> tuple[int, str]:
    """Reports should be 100-5000 chars. Penalize too short or way too long."""
    n = len(content or "")
    if n < 60:    return 0,  "Empty or near-empty"
    if n < 150:   return 30, "Very short"
    if n < 400:   return 60, "Short"
    if n < 5000:  return 100, "Healthy length"
    return 80, "Very long"


def _number_score(content: str) -> tuple[int, str]:
    """Real reports cite numbers. Returns score based on numeric density."""
    content = content or ""
    # Count numbers ≥ 2 digits (skip "0%" or single "1")
    numbers = re.findall(r"\b\d{2,}\b", content)
    # Also count percentages and KRW figures
    pcts = re.findall(r"\d+\.?\d*\s*%", content)
    krw  = re.findall(r"\d+\.?\d*\s*(?:KRW|krw|원|만원|억원|trillion|billion|million)", content, re.IGNORECASE)
    distinct_numbers = len(set(numbers))

    # Detect "N/A everywhere" pattern (bad sign)
    na_count = len(re.findall(r"\bN/?A\b|\bnone\b|\bunknown\b", content, re.IGNORECASE))

    score = min(100, distinct_numbers * 10 + len(pcts) * 5 + len(krw) * 5)
    if na_count >= 5:
        score = max(0, score - 50)
        note = f"{distinct_numbers} numbers, {len(pcts)} %, {len(krw)} KRW figs — but {na_count} N/A markers"
    else:
        note = f"{distinct_numbers} numbers, {len(pcts)} %, {len(krw)} KRW figures"
    return score, note


def _section_score(report: dict) -> tuple[int, str]:
    """Reports should have an executive summary + sections."""
    has_summary = bool((report.get("executive_summary") or "").strip()) and len(report["executive_summary"]) > 30
    sections = report.get("sections") or report.get("content_json", {}).get("sections", [])
    n_sections = len(sections)

    if has_summary and n_sections >= 2:
        return 100, f"Has summary + {n_sections} sections"
    if has_summary and n_sections >= 1:
        return 70,  f"Has summary + {n_sections} section"
    if has_summary:
        return 40,  "Has summary, no sections"
    if n_sections >= 1:
        return 30,  f"{n_sections} sections, NO summary"
    return 10, "No summary, no sections"


def _coherence_score(content: str) -> tuple[int, str]:
    """Cheap LLM-output sanity heuristics (no LLM call — keep scorer cheap)."""
    content = content or ""
    issues = []

    # Repeated phrases (LLM hallucination tell)
    words = content.split()
    if len(words) > 50:
        bigrams = [" ".join(words[i:i+2]) for i in range(len(words)-1)]
        from collections import Counter
        common = Counter(bigrams).most_common(3)
        if common and common[0][1] > 5:
            issues.append(f"repeated bigram '{common[0][0]}' x{common[0][1]}")

    # Empty placeholder text
    if re.search(r"\b(lorem ipsum|placeholder|TODO|TBD|XXX)\b", content, re.IGNORECASE):
        issues.append("placeholder text")

    # Truncated / cutoff signals
    if content.endswith("...") and len(content) < 300:
        issues.append("looks truncated")

    if not issues:
        return 100, "No coherence issues"
    score = max(20, 100 - len(issues) * 30)
    return score, "; ".join(issues)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def score_report(report: dict) -> dict:
    """
    Score a generated report 0-100. `report` is the dict that compose_report returns.
    Returns: {score, grade, breakdown, flags, summary}
    """
    content = (report.get("executive_summary") or "") + "\n" + str(report.get("sections") or "")

    length_s, length_note = _length_score(content)
    number_s, number_note = _number_score(content)
    section_s, section_note = _section_score(report)
    coherence_s, coherence_note = _coherence_score(content)

    # Weighted score
    weights = {"length": 0.15, "numbers": 0.30, "sections": 0.30, "coherence": 0.25}
    score = int(
        length_s    * weights["length"]
        + number_s  * weights["numbers"]
        + section_s * weights["sections"]
        + coherence_s * weights["coherence"]
    )

    # Grade
    if score >= 85:   grade = "A"
    elif score >= 70: grade = "B"
    elif score >= 55: grade = "C"
    elif score >= 40: grade = "D"
    else:             grade = "F"

    flags = []
    if length_s < 50:    flags.append("too_short")
    if number_s < 30:    flags.append("no_numbers")
    if section_s < 50:   flags.append("missing_sections")
    if coherence_s < 70: flags.append("coherence_issues")

    return {
        "score": score,
        "grade": grade,
        "breakdown": {
            "length":     {"score": length_s,    "note": length_note},
            "numbers":    {"score": number_s,    "note": number_note},
            "sections":   {"score": section_s,   "note": section_note},
            "coherence":  {"score": coherence_s, "note": coherence_note},
        },
        "flags": flags,
        "summary": f"{grade} ({score}/100) — {len(flags)} flag(s)" if flags else f"{grade} ({score}/100) — clean",
    }


def log_report_quality(report_id: str, report: dict) -> dict:
    """Score and persist quality assessment for a report."""
    from services.resilience import alert
    quality = score_report(report)

    log.info(
        f"report-quality: {quality['summary']} for report {report_id}",
        extra={"action": "report.quality", "score": quality["score"], "report_id": report_id, "flags": quality["flags"]},
    )

    # Auto-alert on low quality (below C)
    if quality["score"] < 55 and report.get("report_type") != "alert":
        try:
            alert(
                kind="low_quality_report",
                title=f"⚠️ Low-quality report flagged: {quality['grade']} ({quality['score']}/100)",
                body=f"Report ID: {report_id}\nFlags: {', '.join(quality['flags']) or 'none'}\nType: {report.get('report_type', '?')}",
                severity="warning",
            )
        except Exception:
            pass
    return quality
