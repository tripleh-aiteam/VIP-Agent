"use client";

import { useEffect, useState } from "react";
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

  const load = () => api<any[]>("/reports/").then(setReports).catch(() => {});
  useEffect(() => { load(); const i = setInterval(load, 10000); return () => clearInterval(i); }, []);

  const compose = async (type: string) => {
    setComposing(true);
    if (type === "cross-agent") {
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
    setViewMode(null); // show buttons first
  };

  const closeDetail = () => { setDetail(null); setViewMode(null); };

  const dailyReports = reports.filter((r) => r.report_type === "daily_summary");
  const weeklyReports = reports.filter((r) => r.report_type === "weekly_summary");
  const alertReports = reports.filter((r) => r.report_type === "urgent_alert_summary");
  const crossAgentReports = reports.filter((r) => r.report_type === "cross_agent_summary");

  const filteredReports = activeType === "all" ? reports
    : activeType === "daily" ? dailyReports
    : activeType === "weekly" ? weeklyReports
    : activeType === "cross" ? crossAgentReports
    : alertReports;

  const typeConfig: Record<string, { label: string; color: string; bg: string; border: string }> = {
    daily_summary: { label: "Daily", color: "text-blue-500", bg: "bg-blue-50", border: "border-blue-200" },
    weekly_summary: { label: "Weekly", color: "text-green-500", bg: "bg-green-50", border: "border-green-200" },
    urgent_alert_summary: { label: "Alert", color: "text-red-500", bg: "bg-red-50", border: "border-red-200" },
    cross_agent_summary: { label: "Cross-Agent", color: "text-purple-500", bg: "bg-purple-50", border: "border-purple-200" },
  };

  // Parse sections into structured data
  const parseSections = (sections: any[]) => {
    return (sections || []).map((s: any) => {
      const data = s.data || {};
      const metrics: { label: string; value: string }[] = [];

      // Extract meaningful metrics
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

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-6 gap-3">
        <div>
          <h1 className="text-[28px] font-semibold tracking-tight mb-1">Reports</h1>
          <p className="text-sm text-[var(--text-muted)]">Executive summaries and alerts</p>
        </div>
        <div className="flex gap-2 flex-wrap">
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
        <StatCard label="Daily Reports" value={dailyReports.length} color="blue" sub={dailyReports[0] ? `Latest: ${new Date(dailyReports[0].created_at).toLocaleDateString()}` : "None yet"} />
        <StatCard label="Weekly Reports" value={weeklyReports.length} color="green" sub={weeklyReports[0] ? `Latest: ${new Date(weeklyReports[0].created_at).toLocaleDateString()}` : "None yet"} />
        <StatCard label="Urgent Alerts" value={alertReports.length} color="red" sub={alertReports[0] ? `Latest: ${new Date(alertReports[0].created_at).toLocaleDateString()}` : "None yet"} />
        <StatCard label="Cross-Agent" value={crossAgentReports.length} color="purple" sub={crossAgentReports[0] ? `Latest: ${new Date(crossAgentReports[0].created_at).toLocaleDateString()}` : "None yet"} />
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
                    <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${cfg.color} ${cfg.bg} border ${cfg.border}`}>
                      {cfg.label}
                    </span>
                    <span className="text-xs text-[var(--text-secondary)]">{r.source_run_count} runs</span>
                    <span className="text-[10px] text-[var(--text-muted)]">
                      {r.created_at ? new Date(r.created_at).toLocaleString() : ""}
                    </span>
                  </div>
                  <svg className={`w-4 h-4 text-[var(--text-muted)] transition-transform ${isSelected ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </div>
                <div className="px-4 pb-3">
                  <p className="text-[12px] text-[var(--text-secondary)] truncate">{r.executive_summary}</p>
                </div>
              </div>

              {/* Inline buttons — show when this report is selected */}
              {isSelected && detail && viewMode === null && (
                <div className="flex gap-3 mt-2 mb-4 pl-2">
                  <button onClick={() => setViewMode("summary")}
                    className="flex-1 py-3 rounded-lg bg-[var(--brand-blue)] text-white text-[14px] font-semibold hover:opacity-90 transition-colors">
                    Summary View
                  </button>
                  <button onClick={() => setViewMode("detailed")}
                    className="flex-1 py-3 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-default)] text-[var(--text-primary)] text-[14px] font-semibold hover:bg-[var(--bg-hover)] transition-colors">
                    Detailed View
                  </button>
                </div>
              )}

              {/* Summary View — inline, clean format */}
              {isSelected && viewMode === "summary" && (
                <div className="mt-2 mb-4 border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] p-5">
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-3">
                      <h3 className="text-[16px] font-semibold text-[var(--text-primary)]">Report Summary</h3>
                      <Badge text={detail.report_type} />
                    </div>
                    <div className="flex items-center gap-2">
                      <button onClick={copyReport}
                        className="px-3 py-1.5 text-[11px] rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)] text-[var(--text-primary)] font-medium">
                        {copied ? "Copied!" : "Copy"}
                      </button>
                      <button onClick={() => setViewMode("detailed")}
                        className="px-3 py-1.5 text-[11px] rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)] text-[var(--text-primary)] font-medium">
                        Expand
                      </button>
                      <button onClick={closeDetail} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] text-xs ml-2">Close</button>
                    </div>
                  </div>

                  {/* Executive Summary */}
                  <div className="mb-4 p-3 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-default)]">
                    <p className="text-[13px] text-[var(--text-primary)] leading-relaxed">
                      {detail.content?.executive_summary || "No summary available."}
                    </p>
                  </div>

                  {/* Key Sections */}
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
          <p className="text-center text-[var(--text-muted)] py-10 text-sm">
            No {activeType === "all" ? "" : activeType} reports yet. Click compose to generate.
          </p>
        )}
      </div>

      {/* Detailed View — Full-screen document-style modal */}
      {viewMode === "detailed" && detail && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto"
          onClick={(e) => { if (e.target === e.currentTarget) setViewMode("summary"); }}>
          <div className="bg-white w-full max-w-[800px] my-6 mx-4 rounded-xl shadow-2xl min-h-[60vh]">

            {/* Toolbar */}
            <div className="px-6 py-3 border-b border-gray-200 flex items-center justify-between sticky top-0 bg-white rounded-t-xl z-10">
              <div className="flex items-center gap-2">
                <button onClick={copyReport}
                  className="px-3 py-1.5 text-[12px] rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-700 font-medium">
                  {copied ? "Copied!" : "Copy"}
                </button>
                <button onClick={() => downloadFile(detail.content?.markdown || "", `report-${detail.id.slice(0,8)}.md`, "text/markdown")}
                  className="px-3 py-1.5 text-[12px] rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-700 font-medium">
                  .md
                </button>
                <button onClick={() => downloadFile(JSON.stringify(detail.content, null, 2), `report-${detail.id.slice(0,8)}.json`, "application/json")}
                  className="px-3 py-1.5 text-[12px] rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-700 font-medium">
                  .json
                </button>
              </div>
              <button onClick={() => setViewMode("summary")}
                className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 text-gray-400 hover:text-gray-700 text-[18px] font-light">
                x
              </button>
            </div>

            {/* Document body — clean report style */}
            <div className="px-10 py-8 text-gray-800">

              {/* Title */}
              <h1 className="text-[22px] font-bold text-gray-900 mb-1">
                {detail.report_type === "cross_agent_summary" ? "Cross-Agent Report" :
                 detail.report_type === "daily_summary" ? "Daily Executive Summary" :
                 detail.report_type === "weekly_summary" ? "Weekly Executive Summary" :
                 "Urgent Alert Report"}
              </h1>
              <p className="text-[13px] text-gray-400 mb-6">
                Generated {detail.created_at ? new Date(detail.created_at).toLocaleString() : ""} | {detail.source_run_ids?.length || 0} data sources
              </p>

              <hr className="border-gray-200 mb-6" />

              {/* Executive Summary */}
              <div className="mb-8">
                <h2 className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-2">Executive Summary</h2>
                <p className="text-[15px] text-gray-700 leading-[1.8]">
                  {detail.content?.executive_summary || "No summary available."}
                </p>
              </div>

              {/* Sections — rendered as document paragraphs */}
              {(detail.content?.sections || []).map((s: any, i: number) => {
                const sectionContent = s.content || "";

                return (
                  <div key={i} className="mb-8">
                    <h2 className="text-[16px] font-semibold text-gray-900 mb-3 pb-2 border-b border-gray-100">
                      {s.title}
                    </h2>

                    {/* Render content as readable paragraphs */}
                    <div className="text-[14px] text-gray-600 leading-[1.8]">
                      {sectionContent.split("\n").map((line: string, li: number) => {
                        const trimmed = line.trim();
                        if (!trimmed) return <div key={li} className="h-2" />;

                        // Section dividers
                        if (trimmed.startsWith("━━━") || trimmed.startsWith("---")) return null;

                        // Bullet points
                        if (trimmed.startsWith("•") || trimmed.startsWith("- ")) {
                          return (
                            <div key={li} className="flex gap-2 ml-4 mb-1">
                              <span className="text-blue-500 shrink-0">&#8226;</span>
                              <span>{trimmed.replace(/^[•\-]\s*/, "")}</span>
                            </div>
                          );
                        }

                        // Key-value lines (e.g., "Total Contracts: 117")
                        const kvMatch = trimmed.match(/^(.+?):\s+(.+)$/);
                        if (kvMatch && kvMatch[1].length < 40 && !trimmed.includes("|")) {
                          return (
                            <div key={li} className="flex gap-2 mb-1">
                              <span className="text-gray-500 shrink-0">{kvMatch[1]}:</span>
                              <span className="font-medium text-gray-800">{kvMatch[2]}</span>
                            </div>
                          );
                        }

                        // Table-like lines with pipes
                        if (trimmed.includes(" | ")) {
                          const parts = trimmed.split(" | ");
                          return (
                            <div key={li} className="flex gap-4 mb-1 ml-4 text-[13px]">
                              <span className="font-medium text-gray-800 min-w-[120px]">{parts[0]}</span>
                              {parts.slice(1).map((p: string, pi: number) => (
                                <span key={pi} className="text-gray-500">{p}</span>
                              ))}
                            </div>
                          );
                        }

                        // Regular text
                        return <p key={li} className="mb-1">{trimmed}</p>;
                      })}
                    </div>
                  </div>
                );
              })}

              {/* Trace references */}
              {detail.content?.trace_references?.length > 0 && (
                <div className="mt-6 pt-4 border-t border-gray-100">
                  <h2 className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-2">Trace References</h2>
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
