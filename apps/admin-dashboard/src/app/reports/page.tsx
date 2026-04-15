"use client";

import { useEffect, useState } from "react";
import { api, apiPost } from "@/components/api";
import Badge from "@/components/Badge";
import StatCard from "@/components/StatCard";
import { AskVIPBar } from "@/components/AskVIP";

import { API } from "@/components/api";

export default function ReportsPage() {
  const [reports, setReports] = useState<any[]>([]);
  const [composing, setComposing] = useState(false);
  const [detail, setDetail] = useState<any>(null);
  const [activeType, setActiveType] = useState<string>("all");

  const load = () => api<any[]>("/reports/").then(setReports).catch(() => {});
  useEffect(() => { load(); const i = setInterval(load, 10000); return () => clearInterval(i); }, []);

  const compose = async (type: string) => {
    setComposing(true);
    await apiPost(`/reports/compose/${type}`, { hours_back: 48 });
    load();
    setComposing(false);
  };

  const openDetail = async (id: string) => {
    const data = await api<any>(`/reports/${id}`);
    setDetail(data);
  };

  const dailyReports = reports.filter((r) => r.report_type === "daily_summary");
  const weeklyReports = reports.filter((r) => r.report_type === "weekly_summary");
  const alertReports = reports.filter((r) => r.report_type === "urgent_alert_summary");

  const filteredReports = activeType === "all" ? reports
    : activeType === "daily" ? dailyReports
    : activeType === "weekly" ? weeklyReports
    : alertReports;

  const typeConfig: Record<string, { label: string; color: string; bg: string; border: string }> = {
    daily_summary: { label: "Daily", color: "text-blue-400", bg: "bg-blue-900/20", border: "border-blue-800/40" },
    weekly_summary: { label: "Weekly", color: "text-green-400", bg: "bg-green-900/20", border: "border-green-800/40" },
    urgent_alert_summary: { label: "Alert", color: "text-red-400", bg: "bg-red-900/20", border: "border-red-800/40" },
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold mb-1">Reports</h1>
          <p className="text-sm text-[var(--text-muted)]">Executive summaries and alerts</p>
        </div>
        <div className="flex gap-2">
          {["daily", "weekly", "alert"].map((t) => (
            <button key={t} onClick={() => compose(t)} disabled={composing}
              className="px-4 py-2 rounded bg-purple-600 hover:bg-purple-500 text-[var(--text-primary)] text-xs font-semibold transition-colors disabled:opacity-50 capitalize">
              {composing ? "..." : `Compose ${t}`}
            </button>
          ))}
        </div>
      </div>

      {/* Ask VIP */}
      <div className="mb-6">
        <AskVIPBar suggestions={[
          { label: "Explain latest report", prompt: "explain today's summary" },
          { label: "What's the biggest risk?", prompt: "what is the biggest risk" },
          { label: "Compare stock & realty", prompt: "compare stock and real estate view" },
          { label: "What needs approval?", prompt: "what needs approval" },
        ]} />
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <StatCard label="Daily Reports" value={dailyReports.length} color="blue" sub={dailyReports[0] ? `Latest: ${new Date(dailyReports[0].created_at).toLocaleDateString()}` : "None yet"} />
        <StatCard label="Weekly Reports" value={weeklyReports.length} color="green" sub={weeklyReports[0] ? `Latest: ${new Date(weeklyReports[0].created_at).toLocaleDateString()}` : "None yet"} />
        <StatCard label="Urgent Alerts" value={alertReports.length} color="red" sub={alertReports[0] ? `Latest: ${new Date(alertReports[0].created_at).toLocaleDateString()}` : "None yet"} />
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-1 mb-4 border-b border-[var(--border-default)]">
        {[
          { key: "all", label: `All (${reports.length})` },
          { key: "daily", label: `Daily (${dailyReports.length})` },
          { key: "weekly", label: `Weekly (${weeklyReports.length})` },
          { key: "alert", label: `Alerts (${alertReports.length})` },
        ].map((f) => (
          <button
            key={f.key}
            onClick={() => setActiveType(f.key)}
            className={`px-4 py-2 text-xs font-medium transition-colors ${
              activeType === f.key
                ? "text-[var(--brand-blue)] border-b-2 border-[var(--border-active)]"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Report List */}
        <div className="lg:col-span-2 space-y-2">
          {filteredReports.map((r: any) => {
            const cfg = typeConfig[r.report_type] || typeConfig.daily_summary;
            return (
              <div
                key={r.id}
                onClick={() => openDetail(r.id)}
                className={`border rounded-lg bg-[var(--bg-card)] cursor-pointer hover:border-gray-600 transition-colors ${
                  detail?.id === r.id ? "border-[var(--border-active)]" : "border-[var(--border-default)]"
                }`}
              >
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
                  <svg className="w-4 h-4 text-[var(--text-muted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </div>
                <div className="px-4 pb-3">
                  <p className="text-[11px] text-[var(--text-secondary)] truncate">{r.executive_summary}</p>
                </div>
              </div>
            );
          })}
          {filteredReports.length === 0 && (
            <p className="text-center text-[var(--text-muted)] py-10 text-sm">
              No {activeType === "all" ? "" : activeType} reports yet. Click compose to generate.
            </p>
          )}
        </div>

        {/* Detail Panel */}
        <div className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] h-fit sticky top-6">
          {detail ? (
            <div>
              <div className="px-4 py-3 border-b border-[var(--border-default)] flex items-center justify-between">
                <h3 className="text-sm font-semibold">Report Detail</h3>
                <button onClick={() => setDetail(null)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] text-xs">Close</button>
              </div>
              <div className="p-4 space-y-4 max-h-[70vh] overflow-y-auto">
                <div className="flex items-center gap-2">
                  <Badge text={detail.report_type} />
                  <span className="text-[10px] text-[var(--text-muted)]">
                    {detail.created_at ? new Date(detail.created_at).toLocaleString() : ""}
                  </span>
                </div>

                {(detail.content?.sections || []).map((s: any, i: number) => (
                  <div key={i} className="border-l-2 border-[var(--border-default)] pl-3">
                    <h4 className="text-xs font-medium text-[var(--brand-blue)] mb-1">{s.title}</h4>
                    <p className="text-[11px] text-[var(--text-secondary)] leading-relaxed">{s.content}</p>
                    {s.data && Object.keys(s.data).length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {Object.entries(s.data).map(([k, v]) => (
                          <span key={k} className="text-[9px] px-1.5 py-0.5 bg-[var(--bg-elevated)] rounded text-[var(--text-muted)]">
                            {k}: {String(v)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}

                {detail.content?.trace_references?.length > 0 && (
                  <div>
                    <h4 className="text-xs font-medium text-[var(--text-secondary)] mb-1">Traces</h4>
                    <div className="flex flex-wrap gap-1">
                      {detail.content.trace_references.slice(0, 8).map((t: string) => (
                        <span key={t} className="text-[9px] px-1.5 py-0.5 bg-[var(--bg-elevated)] rounded font-mono text-[var(--text-muted)]">{t}</span>
                      ))}
                    </div>
                  </div>
                )}

                <div className="pt-2 border-t border-[var(--border-default)] flex gap-3">
                  <a href={`${API}/reports/${detail.id}/markdown`} target="_blank" rel="noreferrer" className="text-[10px] text-purple-400 hover:underline">Markdown</a>
                  <a href={`${API}/reports/${detail.id}`} target="_blank" rel="noreferrer" className="text-[10px] text-blue-400 hover:underline">JSON</a>
                </div>
              </div>
            </div>
          ) : (
            <div className="p-8 text-center">
              <svg className="w-8 h-8 text-[var(--text-secondary)] mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <p className="text-xs text-[var(--text-muted)]">Click a report to view details</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
