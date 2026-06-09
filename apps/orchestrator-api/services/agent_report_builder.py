"""
agent_report_builder — domain-specific daily reports for the 3 company agents.

VIP is the boss's cockpit; Asset / Stock / Real-Estate are the company's
operating agents. Each gets a MEANINGFUL, consistently-formatted daily report
built from that agent's OWN live data, with an AI takeaway + action items, then
delivered to Telegram + the Reports dashboard.

Design notes:
  - Asset & Stock data come from their real backends via the existing adapters
    (dispatch_task). We use `output_payload` REGARDLESS of the task's judgement
    status — the data is fetched before judgement, so a 'review_required' flag
    must NOT blank the report (that was the Stock bug).
  - Real Estate has no live backend API (the Vercel app only serves HTML), so we
    build from the REAL Triple H listing workbook (`realty_kb_loader`) plus live
    OnBid (공매) opportunities — never a hard 'Failed'.
  - Every builder returns the SAME shape so one formatter renders all of them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services.logger import log


# ---------------------------------------------------------------------------
#  Formatting helpers
# ---------------------------------------------------------------------------

def _won(v: Any) -> str:
    try:
        f = float(str(v).replace(",", "").strip())
    except Exception:
        return str(v)
    if abs(f) >= 1e8:
        return f"{f/1e8:.1f}억원"
    if abs(f) >= 1e4:
        return f"{f/1e4:,.0f}만원"
    return f"{f:,.0f}원"


def _to_num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def _ai_takeaway(name: str, domain: str, metrics: dict, highlights: list[str],
                 alerts: list[str]) -> tuple[str, list[str]]:
    """One short LLM call → (one-line summary, [action items]). Best-effort."""
    try:
        from services.llm_client import chat_completion_sync
    except Exception:
        return "", []
    facts = "\n".join(
        [f"{k}: {v}" for k, v in metrics.items()]
        + [f"highlight: {h}" for h in highlights]
        + [f"ALERT: {a}" for a in alerts]
    )
    if not facts.strip():
        return "", []
    sys = (
        f"You are the {name}. Write a 1-sentence executive takeaway for the boss "
        f"plus up to 3 concrete action items, based ONLY on these {domain} facts. "
        "Use the real numbers. Reply EXACTLY in this format:\n"
        "SUMMARY: <one sentence>\nACTIONS:\n- <item>\n- <item>\n"
        "If nothing needs action write 'ACTIONS:\n- None'."
    )
    try:
        out = chat_completion_sync(
            system_prompt=sys,
            messages=[{"role": "user", "content": facts[:1600]}],
            max_tokens=220, temperature=0.4, model="groq-llama-3.3-70b",
        ) or ""
    except Exception as e:
        log.warning(f"report takeaway failed: {e}")
        return "", []
    summary, actions = "", []
    for line in out.splitlines():
        s = line.strip()
        if s.upper().startswith("SUMMARY:"):
            summary = s.split(":", 1)[1].strip()
        elif s.startswith(("-", "•")):
            item = s.lstrip("-• ").strip()
            if item and item.lower() != "none":
                actions.append(item)
    return summary, actions[:3]


def format_telegram(rep: dict, kst: str) -> str:
    lines = [f"{rep['emoji']} <b>{rep['name']} — Daily Report</b>", f"<i>{kst}</i>", ""]
    if rep.get("metrics"):
        lines.append("<b>📊 Key Metrics</b>")
        for k, v in rep["metrics"].items():
            lines.append(f"• {k}: {v}")
        lines.append("")
    if rep.get("highlights"):
        lines.append("<b>✨ Highlights</b>")
        lines += [f"• {h}" for h in rep["highlights"][:5]]
        lines.append("")
    if rep.get("alerts"):
        lines.append("<b>⚠️ Alerts</b>")
        lines += [f"• {a}" for a in rep["alerts"][:5]]
        lines.append("")
    if rep.get("summary"):
        lines.append("<b>🤖 Summary</b>")
        lines.append(rep["summary"])
        lines.append("")
    if rep.get("actions"):
        lines.append("<b>✅ Action Items</b>")
        lines += [f"• {a}" for a in rep["actions"][:3]]
        lines.append("")
    icon = {"ok": "✅", "partial": "⚠️", "unavailable": "❌"}.get(rep.get("status"), "✅")
    lines.append(f"<i>{icon} {rep.get('source', '')}</i>")
    return "\n".join(lines)


def report_sections(rep: dict) -> list[dict]:
    body = []
    for k, v in (rep.get("metrics") or {}).items():
        body.append(f"{k}: {v}")
    if rep.get("highlights"):
        body += ["", "Highlights:"] + [f"- {h}" for h in rep["highlights"]]
    if rep.get("alerts"):
        body += ["", "Alerts:"] + [f"- {a}" for a in rep["alerts"]]
    if rep.get("actions"):
        body += ["", "Action items:"] + [f"- {a}" for a in rep["actions"]]
    return [{"title": rep["name"], "content": "\n".join(body),
             "data": rep.get("metrics", {})}]


# ---------------------------------------------------------------------------
#  Data fetch
# ---------------------------------------------------------------------------

def _dispatch(db, task_type: str, agent_type: str, trace_id: str) -> dict:
    """Run the agent task and return its output_payload regardless of the
    judgement status (data is fetched before judgement)."""
    from services.task_service import create_task, dispatch_task
    try:
        run = create_task(
            db=db, trace_id=trace_id, task_type=task_type,
            target_agent_type=agent_type, initiator_type="system_scheduler",
            initiator_id="auto-daily-report", source_channel="scheduler",
            input_payload={"auto_report": True},
        )
        run = dispatch_task(db, run.id)
        return {"output": run.output_payload or {}, "status": run.status,
                "error": run.error_message}
    except Exception as e:
        log.warning(f"report dispatch {agent_type} failed: {e}")
        return {"output": {}, "status": "failed", "error": str(e)[:200]}


# ---------------------------------------------------------------------------
#  Per-agent builders
# ---------------------------------------------------------------------------

def build_asset_report(db, trace_id: str) -> dict:
    """Company Assets: value, occupancy, contracts, cash, alerts."""
    d = _dispatch(db, "asset_summary", "asset", trace_id + "-asset")
    o = d["output"]
    pf = o.get("portfolio", {})
    cash = o.get("cash", {})
    contracts = o.get("contracts", {})
    units = pf.get("total_units", 0)
    occ = pf.get("occupied_units", 0)
    occ_rate = round(occ / units * 100, 1) if units else (100 - _to_num(pf.get("vacancy_rate", 0)))
    metrics = {
        "Properties": pf.get("total_properties", o.get("total_listings", "—")),
        "Units": f"{occ}/{units} occupied ({occ_rate:.0f}%)" if units else "—",
        "Monthly rent income": _won(pf.get("monthly_rental_income", 0)),
        "Cash balance": _won(cash.get("total_balance", 0)),
        "Contracts": contracts.get("total", "—"),
        "Risk level": o.get("risk_level", "—"),
    }
    highlights, alerts = [], []
    if pf.get("upcoming_expiries_30d"):
        alerts.append(f"{pf['upcoming_expiries_30d']} lease(s) expiring within 30 days")
    if _to_num(pf.get("total_overdue", o.get("total_overdue", 0))) > 0:
        alerts.append(f"Overdue payments: {_won(pf.get('total_overdue', 0))}")
    for r in (o.get("risk_factors") or [])[:3]:
        alerts.append(r)
    fb = o.get("fallback")
    status = "ok" if (o and not fb) else ("partial" if o else "unavailable")
    summary, actions = _ai_takeaway("Asset Agent", "real-estate asset-management",
                                    metrics, highlights, alerts)
    return {
        "agent_type": "asset", "name": "Asset Agent", "emoji": "🏢",
        "status": status, "metrics": metrics, "highlights": highlights,
        "alerts": alerts, "summary": summary, "actions": actions,
        "source": "Asset backend" + (" (fallback data)" if fb else ""),
    }


# Instruments to show in the stock report: (snapshot_key, label, kind).
_STOCK_DISPLAY: list[tuple[str, str, str]] = [
    ("kospi", "KOSPI", "idx"),
    ("kosdaq", "KOSDAQ", "idx"),
    ("samsung", "삼성전자", "won"),
    ("skhynix", "SK하이닉스", "won"),
    ("nasdaq", "NASDAQ", "idx"),
    ("sp500", "S&P 500", "idx"),
    ("usdkrw", "USD/KRW", "fx"),
]


def _latest_snapshot() -> dict | None:
    try:
        from services.stock_data_tools import tool_stock_price_history
        data = (tool_stock_price_history(limit=1) or {}).get("data")
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            for k in ("items", "snapshots", "data"):
                if isinstance(data.get(k), list) and data[k]:
                    return data[k][0]
            if "prices" in data:
                return data
    except Exception as e:
        log.warning(f"stock snapshot fetch failed: {e}")
    return None


def _fmt_inst(val: Any, chg: Any, kind: str) -> str | None:
    try:
        v = float(val)
    except Exception:
        return None
    if kind == "won":
        price = f"{v:,.0f}원"
    elif kind == "fx":
        price = f"{v:,.2f}"
    else:
        price = f"{v:,.2f}" if v < 10000 else f"{v:,.0f}"
    try:
        c = float(chg)
    except Exception:
        return price
    arrow = "▲" if c > 0 else ("▼" if c < 0 else "—")
    return f"{price}  {arrow}{abs(c):.2f}%"


def build_stock_report(db, trace_id: str) -> dict:
    """Company Stocks (auto/smart trading): real prices + up/down % per stock &
    index, from the live market snapshot, plus foreign-flow + news context."""
    metrics: dict[str, Any] = {}
    highlights, alerts = [], []

    snap = _latest_snapshot()
    captured = market_status = None
    if snap:
        pr, ch = snap.get("prices", {}) or {}, snap.get("changes", {}) or {}
        captured = snap.get("captured_at")
        market_status = snap.get("market_status")
        for key, label, kind in _STOCK_DISPLAY:
            cell = _fmt_inst(pr.get(key), ch.get(key), kind)
            if cell:
                metrics[label] = cell
        # sentiment from KOSPI direction
        kchg = ch.get("kospi")
        if kchg is not None:
            metrics["Sentiment"] = ("bullish 📈" if _to_num(kchg) > 0
                                    else "bearish 📉" if _to_num(kchg) < 0 else "flat")
        # movers / alerts among the displayed instruments
        moves = [(label, _to_num(ch.get(key)))
                 for key, label, _k in _STOCK_DISPLAY if ch.get(key) is not None]
        for label, c in sorted(moves, key=lambda x: x[1], reverse=True)[:1]:
            if c > 0:
                highlights.append(f"Top gainer: {label} ▲{c:.2f}%")
        for label, c in sorted(moves, key=lambda x: x[1])[:2]:
            if c <= -3:
                alerts.append(f"{label} dropped {c:.2f}%")
    else:
        alerts.append("Live market snapshot unavailable right now.")

    # Foreign-flow + news context (real backend data).
    try:
        from services.stock_data_tools import tool_stock_foreign_flow, tool_stock_news
        fb = (tool_stock_foreign_flow(direction="buy") or {}).get("data") or []
        names = [f.get("name") or f.get("ticker") for f in fb[:3] if isinstance(f, dict)]
        if names:
            highlights.append("외국인 순매수: " + ", ".join(n for n in names if n))
        news = (tool_stock_news() or {}).get("data") or {}
        ncount = news.get("count") or len(news.get("articles", []) or [])
        if ncount:
            metrics["Market news"] = ncount
    except Exception as e:
        log.warning(f"stock context fetch failed: {e}")

    status = "ok" if snap else "partial"
    src = ("Stock Advisor live"
           + (f" · {market_status}" if market_status else "")
           + (f" · {captured} KST" if captured else ""))
    summary, actions = _ai_takeaway("Stock Agent", "stock-market / smart-trading",
                                    metrics, highlights, alerts)
    return {
        "agent_type": "stock", "name": "Stock Agent", "emoji": "📈",
        "status": status, "metrics": metrics, "highlights": highlights,
        "alerts": alerts, "summary": summary, "actions": actions, "source": src,
    }


def build_realty_report(db, trace_id: str) -> dict:
    """Company Real Estate: listings, value, categories/regions + OnBid 공매."""
    metrics: dict[str, Any] = {}
    highlights, alerts = [], []
    source = "Triple H listing workbook"
    try:
        from services.realty_kb_loader import load_real_listings
        listings = load_real_listings() or []
    except Exception as e:
        log.warning(f"realty listings load failed: {e}")
        listings = []

    if listings:
        total_val = sum(_to_num(p.get("official_value")) for p in listings)
        cats: dict[str, int] = {}
        regions: dict[str, int] = {}
        for p in listings:
            c = (p.get("category") or "기타").strip() or "기타"
            r = (p.get("sheet") or "—").strip() or "—"
            cats[c] = cats.get(c, 0) + 1
            regions[r] = regions.get(r, 0) + 1
        top_cat = ", ".join(f"{k} {v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])[:3])
        metrics = {
            "Total listings": len(listings),
            "Total official value": _won(total_val),
            "Categories": top_cat,
            "Regions": len(regions),
        }
        big = sorted(listings, key=lambda p: _to_num(p.get("official_value")), reverse=True)[:2]
        for p in big:
            highlights.append(f"{p.get('title', '?')} — {_won(p.get('official_value'))}")
    else:
        metrics = {"Total listings": "data unavailable"}
        alerts.append("Could not load the listing workbook — check the data file.")
        source = "listing workbook (unavailable)"

    # Live OnBid (공매) opportunities — real auction data.
    try:
        from services.onbid_tools import tool_onbid_search
        ob = tool_onbid_search(category="real estate", sort="cheap", limit=3)
        items = ob.get("items") or []
        if items:
            metrics["OnBid 공매 (active)"] = ob.get("total_scanned", len(items))
            for it in items[:2]:
                highlights.append(f"공매: {it.get('address', it.get('name', '?'))[:28]} · 최저 {it.get('min_bid', '?')}")
    except Exception as e:
        log.warning(f"realty onbid enrich failed: {e}")

    status = "ok" if listings else "partial"
    summary, actions = _ai_takeaway("Real Estate Agent", "company real-estate portfolio",
                                    metrics, highlights, alerts)
    return {
        "agent_type": "realty", "name": "Real Estate Agent", "emoji": "🏠",
        "status": status, "metrics": metrics, "highlights": highlights,
        "alerts": alerts, "summary": summary, "actions": actions,
        "source": source,
    }


def load_latest_stock_close(db, hours: int = 18) -> dict | None:
    """Return the stock report captured at the most recent market close (15:30
    KST) within `hours`, so the 8 AM send delivers the close-of-day numbers."""
    try:
        from datetime import timedelta
        from db.models import OrchReport
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        row = (db.query(OrchReport)
               .filter(OrchReport.report_type == "agent_daily_stock")
               .filter(OrchReport.created_at >= cutoff)
               .order_by(OrchReport.created_at.desc())
               .first())
        if row and isinstance(row.content_json, dict) and row.content_json.get("market_close"):
            rep = row.content_json.get("report")
            if isinstance(rep, dict) and rep.get("metrics"):
                return rep
    except Exception as e:
        log.warning(f"load_latest_stock_close failed: {e}")
    return None


def _safe(fn, db, trace_id: str) -> dict:
    try:
        return fn(db, trace_id)
    except Exception as e:
        log.warning(f"report builder {fn.__name__} crashed: {e}")
        return {"agent_type": fn.__name__, "name": fn.__name__, "emoji": "🤖",
                "status": "unavailable", "metrics": {}, "highlights": [],
                "alerts": [f"Report build error: {str(e)[:120]}"],
                "summary": "", "actions": [], "source": "error"}


def build_all_reports(db, trace_id: str, prefer_saved_stock: bool = True) -> list[dict]:
    """Build all 3 agent reports (best-effort each). For Stock, prefer the
    market-close snapshot captured at 15:30 KST if one exists (so the 8 AM
    delivery shows close-of-day prices), else build fresh."""
    asset = _safe(build_asset_report, db, trace_id)
    stock = (load_latest_stock_close(db) if prefer_saved_stock else None) \
        or _safe(build_stock_report, db, trace_id)
    realty = _safe(build_realty_report, db, trace_id)
    return [asset, stock, realty]
