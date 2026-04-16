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

      {/* Detailed View — Full-screen modal overlay */}
      {viewMode === "detailed" && detail && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-start justify-center overflow-y-auto p-4 sm:p-8"
          onClick={(e) => { if (e.target === e.currentTarget) setViewMode("summary"); }}>
          <div className="bg-[var(--bg-primary)] rounded-xl border border-[var(--border-default)] w-full max-w-[900px] my-4" style={{ boxShadow: "var(--shadow-lg, 0 25px 50px -12px rgba(0,0,0,0.25))" }}>

            {/* Modal Header */}
            <div className="px-6 py-4 border-b border-[var(--border-default)] flex items-center justify-between sticky top-0 bg-[var(--bg-primary)] rounded-t-xl z-10">
              <div className="flex items-center gap-3">
                <h2 className="text-[18px] font-semibold text-[var(--text-primary)]">Detailed Report</h2>
                <Badge text={detail.report_type} />
                <span className="text-[12px] text-[var(--text-muted)]">
                  {detail.created_at ? new Date(detail.created_at).toLocaleString() : ""}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={copyReport}
                  className="px-3 py-1.5 text-[12px] rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)] text-[var(--text-primary)] font-medium">
                  {copied ? "Copied!" : "Copy"}
                </button>
                <button onClick={() => downloadFile(detail.content?.markdown || "", `report-${detail.id.slice(0,8)}.md`, "text/markdown")}
                  className="px-3 py-1.5 text-[12px] rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)] text-[var(--text-primary)] font-medium">
                  Download .md
                </button>
                <button onClick={() => downloadFile(JSON.stringify(detail.content, null, 2), `report-${detail.id.slice(0,8)}.json`, "application/json")}
                  className="px-3 py-1.5 text-[12px] rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)] text-[var(--text-primary)] font-medium">
                  Download .json
                </button>
                <button onClick={() => setViewMode("summary")}
                  className="ml-2 w-8 h-8 flex items-center justify-center rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-muted)] hover:text-[var(--text-primary)] text-lg">
                  x
                </button>
              </div>
            </div>

            {/* Modal Body */}
            <div className="p-6 space-y-6">

              {/* Executive Summary */}
              <div className="p-4 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-default)]">
                <h3 className="text-[14px] font-semibold text-[var(--text-primary)] mb-2">Executive Summary</h3>
                <p className="text-[14px] text-[var(--text-secondary)] leading-[1.7]">
                  {detail.content?.executive_summary || "No summary available."}
                </p>
              </div>

              {/* Sections */}
              {parseSections(detail.content?.sections).map((s: any, i: number) => (
                <div key={i} className="border border-[var(--border-default)] rounded-lg overflow-hidden">
                  <div className="px-5 py-3 bg-[var(--bg-elevated)] border-b border-[var(--border-default)]">
                    <h3 className="text-[15px] font-semibold text-[var(--brand-blue)]">{s.title}</h3>
                  </div>
                  <div className="p-5">
                    {/* Content — render line by line for structured reports */}
                    <div className="text-[14px] text-[var(--text-secondary)] leading-[1.8] whitespace-pre-wrap">
                      {(s.content || "").split(/[•]/).map((line: string, li: number) => {
                        const trimmed = line.trim();
                        if (!trimmed) return null;
                        if (li === 0 && !s.content.startsWith("•")) {
                          return <p key={li} className="mb-3">{trimmed}</p>;
                        }
                        return (
                          <div key={li} className="flex gap-2 mb-1.5 pl-2">
                            <span className="text-[var(--brand-blue)] mt-0.5">&#8226;</span>
                            <span>{trimmed}</span>
                          </div>
                        );
                      })}
                    </div>

                    {/* Metrics */}
                    {s.metrics.length > 0 && (
                      <div className="mt-4 pt-3 border-t border-[var(--border-default)] flex flex-wrap gap-3">
                        {s.metrics.map((m: any, j: number) => (
                          <div key={j} className="px-3 py-2 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-default)]">
                            <p className="text-[10px] text-[var(--text-muted)] mb-0.5">{m.label}</p>
                            <p className="text-[14px] font-semibold text-[var(--text-primary)]">{m.value}</p>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Structured data for contracts, properties, etc. */}
                    {s.data?.contracts?.list && s.data.contracts.list.length > 0 && (
                      <div className="mt-4">
                        <h4 className="text-[13px] font-semibold text-[var(--text-primary)] mb-2">Contracts ({s.data.contracts.total})</h4>
                        <div className="overflow-x-auto">
                          <table className="w-full text-[12px]">
                            <thead>
                              <tr className="text-[var(--text-muted)] border-b border-[var(--border-default)]">
                                <th className="text-left py-2 pr-3">Tenant</th>
                                <th className="text-right py-2 pr-3">Monthly Rent</th>
                                <th className="text-right py-2 pr-3">Deposit</th>
                                <th className="text-left py-2 pr-3">End Date</th>
                                <th className="text-left py-2">Status</th>
                              </tr>
                            </thead>
                            <tbody>
                              {s.data.contracts.list.map((c: any, ci: number) => (
                                <tr key={ci} className="border-b border-[var(--border-default)]/50 hover:bg-[var(--bg-hover)]">
                                  <td className="py-2 pr-3 font-medium text-[var(--text-primary)]">{c.tenant}</td>
                                  <td className="py-2 pr-3 text-right">{(c.monthly_rent || 0).toLocaleString()} KRW</td>
                                  <td className="py-2 pr-3 text-right">{(c.deposit || 0).toLocaleString()} KRW</td>
                                  <td className="py-2 pr-3 text-[var(--text-muted)]">{c.end_date}</td>
                                  <td className="py-2"><Badge text={c.status || "active"} /></td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}

                    {s.data?.expiring_leases?.list && s.data.expiring_leases.list.length > 0 && (
                      <div className="mt-4">
                        <h4 className="text-[13px] font-semibold text-[var(--error)] mb-2">Expiring Leases ({s.data.expiring_leases.total})</h4>
                        <div className="space-y-1">
                          {s.data.expiring_leases.list.map((e: any, ei: number) => (
                            <div key={ei} className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-[var(--bg-hover)] text-[12px]">
                              <span className="font-medium text-[var(--text-primary)]">{e.tenant}</span>
                              <div className="flex items-center gap-4">
                                <span className="text-[var(--text-muted)]">{(e.monthly_rent || 0).toLocaleString()} KRW/mo</span>
                                <span className="text-[var(--error)] font-medium">expires {e.end_date}</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ))}

              {/* Trace References */}
              {detail.content?.trace_references?.length > 0 && (
                <div className="p-4 rounded-lg border border-[var(--border-default)]">
                  <h3 className="text-[13px] font-semibold text-[var(--text-primary)] mb-2">Trace References</h3>
                  <div className="flex flex-wrap gap-2">
                    {detail.content.trace_references.slice(0, 12).map((t: string) => (
                      <span key={t} className="text-[11px] px-2 py-1 bg-[var(--bg-elevated)] rounded font-mono text-[var(--text-muted)]">{t}</span>
                    ))}
                  </div>
                </div>
              )}

              {/* Report metadata */}
              <div className="text-[11px] text-[var(--text-muted)] flex items-center gap-4 pt-2 border-t border-[var(--border-default)]">
                <span>Report ID: {detail.id}</span>
                <span>Source runs: {detail.source_run_ids?.length || 0}</span>
                <span>Channel: {detail.delivery_channel || "web"}</span>
                <a href={`${API}/reports/${detail.id}/markdown`} target="_blank" rel="noreferrer" className="text-purple-500 hover:underline ml-auto">View Raw Markdown</a>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
