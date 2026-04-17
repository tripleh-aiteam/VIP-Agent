"use client";

import { useEffect, useState, useRef } from "react";
import { api, apiPost } from "@/components/api";
import Badge from "@/components/Badge";
import StatCard from "@/components/StatCard";
import { AskVIPBar } from "@/components/AskVIP";
import { API } from "@/components/api";

type ViewMode = null | "summary" | "detailed";

export default function ReportsPage() {
  const [reports, setReports] = useState<any[]>([]);
  const [composing, setComposing] = useState(false);
  const [detail, setDetail] = useState<any>(null);
  const [viewMode, setViewMode] = useState<ViewMode>(null);
  const [activeType, setActiveType] = useState<string>("all");
  const [copied, setCopied] = useState(false);
  const [dlOpen, setDlOpen] = useState(false);
  const dlRef = useRef<HTMLDivElement>(null);

  const load = () => api<any[]>("/reports/").then(setReports).catch(() => {});
  useEffect(() => { load(); const i = setInterval(load, 10000); return () => clearInterval(i); }, []);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dlRef.current && !dlRef.current.contains(e.target as Node)) setDlOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const compose = async (type: string) => {
    setComposing(true);
    if (type === "auto-daily") {
      await apiPost("/reports/compose/auto-daily");
      // Wait for background processing
      await new Promise((r) => setTimeout(r, 15000));
    } else if (type === "cross-agent") {
      await apiPost("/reports/compose/cross-agent", {
        agent_types: ["asset", "stock"],
        report_type: "cross_agent_summary",
        trace_id: `tr-report-${Date.now()}`,
      });
    } else {
      await apiPost(`/reports/compose/${type}`, { hours_back: 48 });
    }
    load();
    setComposing(false);
  };

  const openDetail = async (id: string) => {
    const data = await api<any>(`/reports/${id}`);
    setDetail(data);
    setViewMode(null);
  };

  const closeDetail = () => { setDetail(null); setViewMode(null); };

  const dailyReports = reports.filter((r) => r.report_type === "daily_summary" || r.report_type?.startsWith("agent_daily_"));
  const weeklyReports = reports.filter((r) => r.report_type === "weekly_summary");
  const alertReports = reports.filter((r) => r.report_type === "urgent_alert_summary");
  const crossAgentReports = reports.filter((r) => r.report_type === "cross_agent_summary");

  const filteredReports = activeType === "all" ? reports
    : activeType === "daily" ? dailyReports
    : activeType === "weekly" ? weeklyReports
    : activeType === "cross" ? crossAgentReports
    : alertReports;

  // Convert UTC to KST for display
  const toKST = (utcStr: string) => {
    if (!utcStr) return "";
    const d = new Date(utcStr);
    return d.toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
  };

  const typeConfig: Record<string, { label: string; color: string; bg: string; border: string }> = {
    daily_summary: { label: "Daily", color: "text-blue-500", bg: "bg-blue-50", border: "border-blue-200" },
    weekly_summary: { label: "Weekly", color: "text-green-500", bg: "bg-green-50", border: "border-green-200" },
    urgent_alert_summary: { label: "Alert", color: "text-red-500", bg: "bg-red-50", border: "border-red-200" },
    agent_daily_asset: { label: "Asset Daily", color: "text-emerald-500", bg: "bg-emerald-50", border: "border-emerald-200" },
    agent_daily_stock: { label: "Stock Daily", color: "text-sky-500", bg: "bg-sky-50", border: "border-sky-200" },
    agent_daily_realty: { label: "Realty Daily", color: "text-orange-500", bg: "bg-orange-50", border: "border-orange-200" },
    cross_agent_summary: { label: "Cross-Agent", color: "text-purple-500", bg: "bg-purple-50", border: "border-purple-200" },
  };

  const parseSections = (sections: any[]) => {
    return (sections || []).map((s: any) => {
      const data = s.data || {};
      const metrics: { label: string; value: string }[] = [];
      Object.entries(data).forEach(([k, v]) => {
        if (v === null || v === undefined || k === "complete" || k === "missing" || k === "error" || typeof v === "object") return;
        const label = k.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase());
        let display = String(v);
        if (typeof v === "number" && v > 10000) display = v.toLocaleString();
        if (typeof v === "number" && k.includes("pct")) display = `${v}%`;
        if (typeof v === "boolean") display = v ? "Yes" : "No";
        metrics.push({ label, value: display });
      });
      return { ...s, metrics };
    });
  };

  const copyReport = () => {
    const md = detail?.content?.markdown || detail?.content?.executive_summary || "";
    navigator.clipboard.writeText(md);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const downloadFile = (content: string, filename: string, type: string) => {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const downloadDocx = () => {
    if (!detail) return;
    const title = detail.report_type === "cross_agent_summary" ? "Cross-Agent Report" :
      detail.report_type === "daily_summary" ? "Daily Executive Summary" :
      detail.report_type === "weekly_summary" ? "Weekly Executive Summary" : "Urgent Alert Report";
    const date = detail.created_at ? toKST(detail.created_at) : "";
    const sections = detail.content?.sections || [];

    let html = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">
<head><meta charset="utf-8"><title>${title}</title>
<style>body{font-family:Calibri,sans-serif;font-size:11pt;color:#333;margin:40px}
h1{font-size:20pt;color:#1a1a1a;border-bottom:2px solid #2563eb;padding-bottom:8px}
h2{font-size:14pt;color:#2563eb;margin-top:24px}
table{border-collapse:collapse;width:100%;margin:12px 0}
th{background:#f1f5f9;text-align:left;padding:8px 12px;border:1px solid #e2e8f0;font-size:10pt;color:#64748b}
td{padding:8px 12px;border:1px solid #e2e8f0;font-size:10pt}
.summary{background:#f8fafc;padding:16px;border-left:4px solid #2563eb;margin:16px 0}
.meta{font-size:9pt;color:#94a3b8}</style></head><body>`;

    html += `<h1>${title}</h1><p class="meta">Generated: ${date} | Sources: ${detail.source_run_ids?.length || 0}</p>`;
    html += `<div class="summary"><strong>Executive Summary</strong><br/>${detail.content?.executive_summary || ""}</div>`;

    // Summary table
    html += `<h2>Report Overview</h2><table><tr><th>Section</th><th>Key Finding</th><th>Status</th></tr>`;
    sections.forEach((s: any) => {
      const status = (s.data?.risk_level) || (s.data?.complete === false ? "Incomplete" : "OK");
      html += `<tr><td><strong>${s.title}</strong></td><td>${(s.content || "").slice(0, 120)}</td><td>${status}</td></tr>`;
    });
    html += `</table>`;

    sections.forEach((s: any) => {
      html += `<h2>${s.title}</h2>`;
      const lines = (s.content || "").split("\n");
      lines.forEach((line: string) => {
        const t = line.trim();
        if (!t || t.startsWith("━")) return;
        if (t.startsWith("•") || t.startsWith("- ")) {
          html += `<p style="margin-left:20px">${t}</p>`;
        } else {
          html += `<p>${t}</p>`;
        }
      });
    });

    html += `<hr/><p class="meta">VIP Agent Platform | Report ID: ${detail.id?.slice(0, 8)}</p></body></html>`;

    const blob = new Blob(['\ufeff' + html], { type: 'application/msword' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `report-${detail.report_type}-${detail.id.slice(0, 8)}.doc`;
    a.click();
    URL.revokeObjectURL(url);
    setDlOpen(false);
  };

  // Build summary table data from sections
  const buildSummaryTable = () => {
    const sections = detail?.content?.sections || [];
    return sections.map((s: any) => {
      const data = s.data || {};
      let status = "OK";
      let highlight = "";
      if (data.risk_level) { status = data.risk_level; }
      else if (data.complete === false) { status = "Incomplete"; }
      else if (data.avg_risk && data.avg_risk > 50) { status = "High Risk"; }

      // Pick the most important metric
      if (data.total_value) highlight = `${Number(data.total_value).toLocaleString()} KRW`;
      else if (data.holdings_count) highlight = `${data.holdings_count} holdings`;
      else if (data.stocks_analyzed) highlight = `${data.stocks_analyzed} stocks`;
      else if (data.total_listings) highlight = `${data.total_listings} properties`;
      else if (data.rejected) highlight = `${data.rejected} rejected`;
      else if (data.reports_count) highlight = `${data.reports_count} reports`;

      return { title: s.title, summary: (s.content || "").slice(0, 100), status, highlight };
    });
  };

  const reportTitle = (type: string) =>
    type === "cross_agent_summary" ? "Cross-Agent Report" :
    type === "daily_summary" ? "Daily Executive Summary" :
    type === "weekly_summary" ? "Weekly Executive Summary" : "Urgent Alert Report";

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-6 gap-3">
        <div>
          <h1 className="text-[28px] font-semibold tracking-tight mb-1">Reports</h1>
          <p className="text-sm text-[var(--text-muted)]">Executive summaries and alerts</p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <button onClick={() => compose("auto-daily")} disabled={composing}
            className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold transition-colors disabled:opacity-50">
            {composing ? "..." : "Auto Daily (3 Agents)"}
          </button>
          {["daily", "weekly", "alert", "cross-agent"].map((t) => (
            <button key={t} onClick={() => compose(t)} disabled={composing}
              className="px-4 py-2 rounded-lg bg-[var(--text-primary)] hover:bg-[var(--text-secondary)] text-white text-xs font-semibold transition-colors disabled:opacity-50 capitalize">
              {composing ? "..." : `Compose ${t}`}
            </button>
          ))}
        </div>
      </div>

      <div className="mb-6">
        <AskVIPBar suggestions={[
          { label: "Explain latest report", prompt: "explain today's summary" },
          { label: "What's the biggest risk?", prompt: "what is the biggest risk" },
          { label: "Compare stock & realty", prompt: "compare stock and real estate view" },
          { label: "What needs approval?", prompt: "what needs approval" },
        ]} />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatCard label="Daily Reports" value={dailyReports.length} color="blue" sub={dailyReports[0] ? `Latest: ${toKST(dailyReports[0].created_at)}` : "None yet"} />
        <StatCard label="Weekly Reports" value={weeklyReports.length} color="green" sub={weeklyReports[0] ? `Latest: ${toKST(weeklyReports[0].created_at)}` : "None yet"} />
        <StatCard label="Urgent Alerts" value={alertReports.length} color="red" sub={alertReports[0] ? `Latest: ${toKST(alertReports[0].created_at)}` : "None yet"} />
        <StatCard label="Cross-Agent" value={crossAgentReports.length} color="purple" sub={crossAgentReports[0] ? `Latest: ${toKST(crossAgentReports[0].created_at)}` : "None yet"} />
      </div>

      <div className="flex gap-1 mb-4 border-b border-[var(--border-default)]">
        {[
          { key: "all", label: `All (${reports.length})` },
          { key: "daily", label: `Daily (${dailyReports.length})` },
          { key: "weekly", label: `Weekly (${weeklyReports.length})` },
          { key: "alert", label: `Alerts (${alertReports.length})` },
          { key: "cross", label: `Cross-Agent (${crossAgentReports.length})` },
        ].map((f) => (
          <button key={f.key} onClick={() => setActiveType(f.key)}
            className={`px-4 py-2 text-xs font-medium transition-colors ${
              activeType === f.key
                ? "text-[var(--brand-blue)] border-b-2 border-[var(--border-active)]"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}>
            {f.label}
          </button>
        ))}
      </div>

      {/* Report List */}
      <div className="space-y-2">
        {filteredReports.map((r: any) => {
          const cfg = typeConfig[r.report_type] || typeConfig.daily_summary;
          const isSelected = detail?.id === r.id;
          return (
            <div key={r.id}>
              <div onClick={() => openDetail(r.id)}
                className={`border rounded-lg bg-[var(--bg-card)] cursor-pointer hover:border-[var(--brand-blue)] transition-colors ${
                  isSelected ? "border-[var(--brand-blue)] ring-1 ring-[var(--brand-blue)]" : "border-[var(--border-default)]"
                }`}>
                <div className="px-4 py-3 flex items-center justify-between">
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${cfg.color} ${cfg.bg} border ${cfg.border}`}>{cfg.label}</span>
                    <span className="text-xs text-[var(--text-secondary)]">{r.source_run_count} runs</span>
                    <span className="text-[10px] text-[var(--text-muted)]">{r.created_at ? toKST(r.created_at) : ""}</span>
                  </div>
                  <svg className={`w-4 h-4 text-[var(--text-muted)] transition-transform ${isSelected ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </div>
                <div className="px-4 pb-3">
                  <p className="text-[12px] text-[var(--text-secondary)] truncate">{r.executive_summary}</p>
                </div>
              </div>

              {isSelected && detail && viewMode === null && (
                <div className="flex gap-3 mt-2 mb-4 pl-2">
                  <button onClick={() => setViewMode("summary")} className="flex-1 py-3 rounded-lg bg-[var(--brand-blue)] text-white text-[14px] font-semibold hover:opacity-90 transition-colors">
                    Summary
                  </button>
                  <button onClick={() => setViewMode("detailed")} className="flex-1 py-3 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-default)] text-[var(--text-primary)] text-[14px] font-semibold hover:bg-[var(--bg-hover)] transition-colors">
                    Detailed Report
                  </button>
                </div>
              )}

              {/* Summary View */}
              {isSelected && viewMode === "summary" && (
                <div className="mt-2 mb-4 border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] p-5">
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-3">
                      <h3 className="text-[16px] font-semibold text-[var(--text-primary)]">Report Summary</h3>
                      <Badge text={detail.report_type} />
                    </div>
                    <div className="flex items-center gap-2">
                      <button onClick={copyReport} className="px-3 py-1.5 text-[11px] rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)] text-[var(--text-primary)] font-medium">
                        {copied ? "Copied!" : "Copy"}
                      </button>
                      <button onClick={() => setViewMode("detailed")} className="px-3 py-1.5 text-[11px] rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)] text-[var(--text-primary)] font-medium">Expand</button>
                      <button onClick={closeDetail} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] text-xs ml-2">Close</button>
                    </div>
                  </div>
                  <div className="mb-4 p-3 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-default)]">
                    <p className="text-[13px] text-[var(--text-primary)] leading-relaxed">{detail.content?.executive_summary || "No summary available."}</p>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {parseSections(detail.content?.sections).map((s: any, i: number) => (
                      <div key={i} className="p-3 rounded-lg border border-[var(--border-default)]">
                        <h4 className="text-[13px] font-semibold text-[var(--brand-blue)] mb-2">{s.title}</h4>
                        <p className="text-[12px] text-[var(--text-secondary)] leading-relaxed mb-2">{s.content}</p>
                        {s.metrics.length > 0 && (
                          <div className="flex flex-wrap gap-2">
                            {s.metrics.slice(0, 4).map((m: any, j: number) => (
                              <div key={j} className="px-2 py-1 rounded bg-[var(--bg-elevated)] text-[11px]">
                                <span className="text-[var(--text-muted)]">{m.label}: </span>
                                <span className="font-semibold text-[var(--text-primary)]">{m.value}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {filteredReports.length === 0 && (
          <p className="text-center text-[var(--text-muted)] py-10 text-sm">No {activeType === "all" ? "" : activeType} reports yet. Click compose to generate.</p>
        )}
      </div>

      {/* ========== DETAILED VIEW — Full-screen document modal ========== */}
      {viewMode === "detailed" && detail && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto"
          onClick={(e) => { if (e.target === e.currentTarget) setViewMode("summary"); }}>
          <div className="bg-white w-full max-w-[850px] my-6 mx-4 rounded-xl shadow-2xl">

            {/* Toolbar */}
            <div className="px-6 py-3 border-b border-gray-200 flex items-center justify-between sticky top-0 bg-white rounded-t-xl z-10">
              <div className="flex items-center gap-2">
                <button onClick={copyReport}
                  className="px-3 py-1.5 text-[12px] rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-700 font-medium transition-colors">
                  {copied ? "Copied!" : "Copy"}
                </button>

                {/* Download dropdown */}
                <div className="relative" ref={dlRef}>
                  <button onClick={() => setDlOpen(!dlOpen)}
                    className="px-3 py-1.5 text-[12px] rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium flex items-center gap-1.5 transition-colors">
                    Download
                    <svg className={`w-3.5 h-3.5 transition-transform ${dlOpen ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
                  </button>
                  {dlOpen && (
                    <div className="absolute top-full left-0 mt-1 bg-white rounded-lg shadow-lg border border-gray-200 py-1 w-48 z-20">
                      <button onClick={downloadDocx} className="w-full text-left px-4 py-2.5 text-[13px] text-gray-700 hover:bg-gray-50 flex items-center gap-3">
                        <span className="w-8 h-8 rounded-lg bg-blue-100 text-blue-600 flex items-center justify-center text-[10px] font-bold">W</span>
                        <div><div className="font-medium">MS Word</div><div className="text-[10px] text-gray-400">.doc format</div></div>
                      </button>
                      <button onClick={() => { downloadFile(detail.content?.markdown || "", `report-${detail.id.slice(0,8)}.md`, "text/markdown"); setDlOpen(false); }}
                        className="w-full text-left px-4 py-2.5 text-[13px] text-gray-700 hover:bg-gray-50 flex items-center gap-3">
                        <span className="w-8 h-8 rounded-lg bg-gray-100 text-gray-600 flex items-center justify-center text-[10px] font-bold">MD</span>
                        <div><div className="font-medium">Markdown</div><div className="text-[10px] text-gray-400">.md format</div></div>
                      </button>
                      <button onClick={() => { downloadFile(JSON.stringify(detail.content, null, 2), `report-${detail.id.slice(0,8)}.json`, "application/json"); setDlOpen(false); }}
                        className="w-full text-left px-4 py-2.5 text-[13px] text-gray-700 hover:bg-gray-50 flex items-center gap-3">
                        <span className="w-8 h-8 rounded-lg bg-green-100 text-green-600 flex items-center justify-center text-[10px] font-bold">{"{}"}</span>
                        <div><div className="font-medium">JSON</div><div className="text-[10px] text-gray-400">.json format</div></div>
                      </button>
                    </div>
                  )}
                </div>
              </div>
              <button onClick={() => setViewMode("summary")}
                className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 text-gray-400 hover:text-gray-700 text-[18px]">
                x
              </button>
            </div>

            {/* Document body */}
            <div className="px-10 py-8 text-gray-800">

              {/* Title block */}
              <div className="mb-6">
                <div className="flex items-center gap-3 mb-2">
                  <h1 className="text-[24px] font-bold text-gray-900">{reportTitle(detail.report_type)}</h1>
                  <span className={`text-[10px] px-2.5 py-1 rounded-full font-semibold ${typeConfig[detail.report_type]?.color || ""} ${typeConfig[detail.report_type]?.bg || ""} border ${typeConfig[detail.report_type]?.border || ""}`}>
                    {typeConfig[detail.report_type]?.label || detail.report_type}
                  </span>
                </div>
                <div className="flex items-center gap-4 text-[12px] text-gray-400">
                  <span>{detail.created_at ? toKST(detail.created_at) : ""}</span>
                  <span>{detail.source_run_ids?.length || 0} data sources</span>
                  <span>ID: {detail.id?.slice(0, 8)}</span>
                </div>
              </div>

              <hr className="border-gray-200 mb-6" />

              {/* Executive Summary */}
              <div className="mb-8 bg-gray-50 rounded-lg p-5 border border-gray-100">
                <h2 className="text-[11px] font-bold text-gray-400 uppercase tracking-widest mb-3">Executive Summary</h2>
                <p className="text-[15px] text-gray-700 leading-[1.8]">
                  {detail.content?.executive_summary || "No summary available."}
                </p>
              </div>

              {/* ===== Summary Table ===== */}
              <div className="mb-8">
                <h2 className="text-[11px] font-bold text-gray-400 uppercase tracking-widest mb-3">Report Overview</h2>
                <table className="w-full border-collapse">
                  <thead>
                    <tr className="bg-gray-50">
                      <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-gray-500 uppercase tracking-wider border border-gray-200">Section</th>
                      <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-gray-500 uppercase tracking-wider border border-gray-200">Key Finding</th>
                      <th className="text-center px-4 py-2.5 text-[11px] font-semibold text-gray-500 uppercase tracking-wider border border-gray-200">Highlight</th>
                      <th className="text-center px-4 py-2.5 text-[11px] font-semibold text-gray-500 uppercase tracking-wider border border-gray-200">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {buildSummaryTable().map((row: any, i: number) => (
                      <tr key={i} className="hover:bg-blue-50/30">
                        <td className="px-4 py-3 text-[13px] font-semibold text-gray-800 border border-gray-200">{row.title}</td>
                        <td className="px-4 py-3 text-[12px] text-gray-600 border border-gray-200 max-w-[300px]">{row.summary}...</td>
                        <td className="px-4 py-3 text-[12px] text-center font-medium text-gray-800 border border-gray-200">{row.highlight || "—"}</td>
                        <td className="px-4 py-3 text-center border border-gray-200">
                          <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${
                            row.status === "High" || row.status === "High Risk" ? "text-red-600 bg-red-50" :
                            row.status === "Medium" ? "text-amber-600 bg-amber-50" :
                            row.status === "Incomplete" ? "text-gray-500 bg-gray-100" :
                            "text-green-600 bg-green-50"
                          }`}>{row.status}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <hr className="border-gray-100 mb-8" />

              {/* ===== Detailed Sections ===== */}
              {(detail.content?.sections || []).map((s: any, i: number) => {
                const sectionContent = s.content || "";
                const data = s.data || {};

                return (
                  <div key={i} className="mb-8">
                    <h2 className="text-[17px] font-bold text-gray-900 mb-1">{s.title}</h2>
                    <div className="w-12 h-0.5 bg-blue-500 mb-4" />

                    {/* Content */}
                    <div className="text-[14px] text-gray-600 leading-[1.9]">
                      {sectionContent.split("\n").map((line: string, li: number) => {
                        const t = line.trim();
                        if (!t) return <div key={li} className="h-3" />;
                        if (t.startsWith("━━━") || t.startsWith("---")) return null;

                        if (t.startsWith("•") || t.startsWith("- ")) {
                          return (
                            <div key={li} className="flex gap-2 ml-4 mb-1.5">
                              <span className="text-blue-500 shrink-0 mt-0.5">&#8226;</span>
                              <span>{t.replace(/^[•\-]\s*/, "")}</span>
                            </div>
                          );
                        }

                        const kvMatch = t.match(/^(.+?):\s+(.+)$/);
                        if (kvMatch && kvMatch[1].length < 40 && !t.includes("|")) {
                          return (
                            <div key={li} className="grid grid-cols-[180px_1fr] gap-2 mb-1 py-0.5">
                              <span className="text-gray-400">{kvMatch[1]}</span>
                              <span className="font-medium text-gray-800">{kvMatch[2]}</span>
                            </div>
                          );
                        }

                        if (t.includes(" | ")) {
                          const parts = t.split(" | ");
                          return (
                            <div key={li} className="grid grid-cols-[180px_repeat(auto-fill,minmax(100px,1fr))] gap-2 mb-1 ml-4 text-[13px] py-0.5 border-b border-gray-50">
                              <span className="font-medium text-gray-800">{parts[0]}</span>
                              {parts.slice(1).map((p: string, pi: number) => (
                                <span key={pi} className="text-gray-500">{p}</span>
                              ))}
                            </div>
                          );
                        }

                        return <p key={li} className="mb-1.5">{t}</p>;
                      })}
                    </div>

                    {/* Data table if contracts/properties exist */}
                    {data.contracts?.list && data.contracts.list.length > 0 && (
                      <div className="mt-5">
                        <h3 className="text-[13px] font-semibold text-gray-700 mb-2">Active Contracts ({data.contracts.total})</h3>
                        <table className="w-full border-collapse text-[12px]">
                          <thead>
                            <tr className="bg-gray-50">
                              <th className="text-left px-3 py-2 border border-gray-200 text-gray-500 font-medium">Tenant</th>
                              <th className="text-right px-3 py-2 border border-gray-200 text-gray-500 font-medium">Monthly Rent</th>
                              <th className="text-right px-3 py-2 border border-gray-200 text-gray-500 font-medium">Deposit</th>
                              <th className="text-left px-3 py-2 border border-gray-200 text-gray-500 font-medium">End Date</th>
                            </tr>
                          </thead>
                          <tbody>
                            {data.contracts.list.map((c: any, ci: number) => (
                              <tr key={ci} className="hover:bg-blue-50/20">
                                <td className="px-3 py-2 border border-gray-200 font-medium text-gray-800">{c.tenant}</td>
                                <td className="px-3 py-2 border border-gray-200 text-right text-gray-600">{(c.monthly_rent || 0).toLocaleString()} KRW</td>
                                <td className="px-3 py-2 border border-gray-200 text-right text-gray-600">{(c.deposit || 0).toLocaleString()} KRW</td>
                                <td className="px-3 py-2 border border-gray-200 text-gray-500">{c.end_date}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {data.expiring_leases?.list && data.expiring_leases.list.length > 0 && (
                      <div className="mt-5">
                        <h3 className="text-[13px] font-semibold text-red-600 mb-2">Expiring Leases ({data.expiring_leases.total})</h3>
                        <table className="w-full border-collapse text-[12px]">
                          <thead>
                            <tr className="bg-red-50/50">
                              <th className="text-left px-3 py-2 border border-gray-200 text-gray-500 font-medium">Tenant</th>
                              <th className="text-right px-3 py-2 border border-gray-200 text-gray-500 font-medium">Monthly Rent</th>
                              <th className="text-left px-3 py-2 border border-gray-200 text-red-400 font-medium">Expires</th>
                            </tr>
                          </thead>
                          <tbody>
                            {data.expiring_leases.list.map((e: any, ei: number) => (
                              <tr key={ei} className="hover:bg-red-50/20">
                                <td className="px-3 py-2 border border-gray-200 font-medium text-gray-800">{e.tenant}</td>
                                <td className="px-3 py-2 border border-gray-200 text-right text-gray-600">{(e.monthly_rent || 0).toLocaleString()} KRW</td>
                                <td className="px-3 py-2 border border-gray-200 text-red-600 font-medium">{e.end_date}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                );
              })}

              {/* Traces */}
              {detail.content?.trace_references?.length > 0 && (
                <div className="mt-6 pt-4 border-t border-gray-100">
                  <h2 className="text-[11px] font-bold text-gray-400 uppercase tracking-widest mb-2">Trace References</h2>
                  <div className="flex flex-wrap gap-2">
                    {detail.content.trace_references.slice(0, 10).map((t: string) => (
                      <code key={t} className="text-[11px] px-2 py-1 bg-gray-50 rounded text-gray-500 border border-gray-100">{t}</code>
                    ))}
                  </div>
                </div>
              )}

              {/* Footer */}
              <div className="mt-8 pt-4 border-t border-gray-200 text-[11px] text-gray-400 flex items-center justify-between">
                <span>VIP Agent Platform | Report ID: {detail.id?.slice(0, 8)}</span>
                <a href={`${API}/reports/${detail.id}/markdown`} target="_blank" rel="noreferrer" className="text-purple-500 hover:underline">Raw Markdown</a>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
