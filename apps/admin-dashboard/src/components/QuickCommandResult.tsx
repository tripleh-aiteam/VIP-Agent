"use client";

import { useState, useEffect } from "react";
import { api } from "./api";
import Badge from "./Badge";
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from "recharts";

const C = { green: "#22c55e", amber: "#f59e0b", red: "#ef4444", blue: "#3b82f6", gray: "#94a3b8", purple: "#a855f7" };

interface Props {
  command: string;
  onClose: () => void;
}

export default function QuickCommandResult({ command, onClose }: Props) {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    setLoading(true);
    loadData(command).then(setData).finally(() => setLoading(false));
  }, [command]);

  if (loading) {
    return (
      <div className="mt-4 border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] p-4">
        <div className="flex items-center gap-2 text-[12px] text-[var(--text-muted)]">
          <div className="w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          Loading...
        </div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="mt-4 border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] p-4" style={{ boxShadow: "var(--shadow-sm)" }}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-[var(--text-primary)]">{data.title}</h3>
        <button onClick={onClose} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] text-xs">Close</button>
      </div>

      {/* Summary text */}
      <p className="text-[12px] text-[var(--text-secondary)] mb-3">{data.summary}</p>

      {/* Mini cards */}
      {data.cards && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
          {data.cards.map((c: any, i: number) => (
            <div key={i} className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] p-2.5">
              <p className="text-[9px] text-[var(--text-muted)]">{c.label}</p>
              <p className={`text-[16px] font-bold ${c.alert ? "text-red-500" : "text-[var(--text-primary)]"}`}>{c.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Charts */}
      {data.charts && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {data.charts.map((chart: any, i: number) => (
            <div key={i} className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
              <h4 className="text-[10px] font-semibold text-[var(--text-primary)] mb-2">{chart.title}</h4>
              {chart.type === "donut" && (
                <>
                  <div className="flex justify-center">
                    <ResponsiveContainer width={120} height={120}>
                      <PieChart>
                        <Pie data={chart.data} innerRadius={30} outerRadius={48} paddingAngle={3} dataKey="value">
                          {chart.data.map((d: any, j: number) => <Cell key={j} fill={d.color || C.blue} />)}
                        </Pie>
                        <Tooltip formatter={(v: any, n: any) => [`${v}`, n]} />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="flex flex-wrap justify-center gap-2 mt-1">
                    {chart.data.map((d: any) => (
                      <span key={d.name} className="flex items-center gap-1 text-[9px] text-[var(--text-muted)]">
                        <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ backgroundColor: d.color || C.gray }} />
                        {d.name} ({d.value})
                      </span>
                    ))}
                  </div>
                </>
              )}
              {chart.type === "bar" && (
                <ResponsiveContainer width="100%" height={120}>
                  <BarChart data={chart.data} barSize={20}>
                    <XAxis dataKey="name" tick={{ fontSize: 9 }} />
                    <YAxis tick={{ fontSize: 9 }} allowDecimals={false} />
                    <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                    <Bar dataKey="value" radius={[4, 4, 0, 0]} name={chart.barName || "Count"}>
                      {chart.data.map((d: any, j: number) => <Cell key={j} fill={d.color || chart.color || C.blue} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

async function loadData(command: string): Promise<any> {
  try {
    if (command === "status") {
      const [agents, runs, cases, health] = await Promise.all([
        api<any[]>("/registry/agents"),
        api<any[]>("/runs?limit=100"),
        api<any[]>("/judgement/cases"),
        api<any>("/health"),
      ]);
      const active = agents.filter((a: any) => a.status === "active").length;
      const completed = runs.filter((r: any) => r.status === "completed").length;
      const failed = runs.filter((r: any) => r.status === "failed").length;
      const pending = cases.filter((c: any) => c.decision === "human_review_required" || c.decision === "conditional_approve").length;

      return {
        title: "System Status",
        summary: health.status === "ok" ? `System online. ${active} agents active, ${pending} pending approvals.` : "System degraded.",
        cards: [
          { label: "Agents", value: `${active}/${agents.length}` },
          { label: "Completed", value: completed },
          { label: "Failed", value: failed, alert: failed > 0 },
          { label: "Pending", value: pending, alert: pending > 0 },
        ],
        charts: [
          { title: "Run Status", type: "donut", data: [
            { name: "Completed", value: completed, color: C.green },
            { name: "Failed", value: failed || 0, color: C.red },
            { name: "Other", value: Math.max(0, runs.length - completed - failed), color: C.gray },
          ].filter(d => d.value > 0) },
          { title: "Agents by Type", type: "bar", color: C.blue, data:
            Object.entries(agents.reduce((a: any, ag: any) => { a[ag.type] = (a[ag.type] || 0) + 1; return a; }, {}))
              .map(([name, value]) => ({ name, value }))
          },
        ],
      };
    }

    if (command === "show daily report") {
      const reports = await api<any[]>("/reports/?limit=10");
      const daily = reports.filter((r: any) => r.report_type === "daily_summary" || r.report_type?.startsWith("agent_daily_"));
      const typeCounts = reports.reduce((a: any, r: any) => { a[r.report_type] = (a[r.report_type] || 0) + 1; return a; }, {});

      return {
        title: "Latest Report",
        summary: daily[0]?.executive_summary?.slice(0, 150) || "No daily report available.",
        cards: [
          { label: "Total", value: reports.length },
          { label: "Daily", value: daily.length },
        ],
        charts: [
          { title: "Report Types", type: "donut", data:
            Object.entries(typeCounts).map(([name, value]) => ({
              name: name.replace(/_/g, " "), value,
              color: name.includes("daily") ? C.blue : name.includes("weekly") ? C.green : name.includes("cross") ? C.purple : C.amber,
            }))
          },
        ],
      };
    }

    if (command === "show agents") {
      const agents = await api<any[]>("/registry/agents");
      const active = agents.filter((a: any) => a.status === "active");
      const byType = Object.entries(agents.reduce((a: any, ag: any) => { a[ag.type] = (a[ag.type] || 0) + 1; return a; }, {}))
        .map(([name, value]) => ({ name, value }));

      return {
        title: "Agent Health",
        summary: `${active.length} of ${agents.length} agents active.`,
        cards: [
          { label: "Active", value: active.length },
          { label: "Total", value: agents.length },
          { label: "Real", value: agents.filter((a: any) => !a.is_mock).length },
          { label: "Mock", value: agents.filter((a: any) => a.is_mock).length },
        ],
        charts: [
          { title: "Status", type: "donut", data: [
            { name: "Active", value: active.length, color: C.green },
            { name: "Inactive", value: agents.length - active.length, color: C.gray },
          ].filter(d => d.value > 0) },
          { title: "By Type", type: "bar", color: C.blue, data: byType },
        ],
      };
    }

    if (command === "pending approvals") {
      const cases = await api<any[]>("/judgement/cases");
      const pending = cases.filter((c: any) => c.decision === "human_review_required" || c.decision === "conditional_approve");
      const approved = cases.filter((c: any) => c.decision === "auto_approve");
      const rejected = cases.filter((c: any) => c.decision === "rejected");

      return {
        title: "Approvals",
        summary: pending.length > 0 ? `${pending.length} case(s) need your attention.` : "No pending approvals.",
        cards: [
          { label: "Pending", value: pending.length, alert: pending.length > 0 },
          { label: "Approved", value: approved.length },
          { label: "Rejected", value: rejected.length },
        ],
        charts: [
          { title: "Decisions", type: "donut", data: [
            { name: "Pending", value: pending.length, color: C.amber },
            { name: "Approved", value: approved.length, color: C.green },
            { name: "Rejected", value: rejected.length, color: C.red },
          ].filter(d => d.value > 0) },
        ],
      };
    }

    if (command === "high risk cases") {
      const cases = await api<any[]>("/judgement/cases");
      const high = cases.filter((c: any) => (c.risk_score || 0) >= 0.5);
      const riskData = [
        { name: "Low", value: cases.filter((c: any) => (c.risk_score || 0) < 0.3).length, color: C.green },
        { name: "Medium", value: cases.filter((c: any) => (c.risk_score || 0) >= 0.3 && (c.risk_score || 0) < 0.6).length, color: C.amber },
        { name: "High", value: cases.filter((c: any) => (c.risk_score || 0) >= 0.6).length, color: C.red },
      ].filter(d => d.value > 0);

      return {
        title: "Risk Check",
        summary: high.length > 0 ? `${high.length} high-risk case(s) found.` : "No high-risk cases.",
        cards: [{ label: "High Risk", value: high.length, alert: high.length > 0 }, { label: "Total Cases", value: cases.length }],
        charts: [{ title: "Risk Distribution", type: "donut", data: riskData }],
      };
    }

    if (command === "run full executive summary") {
      const runs = await api<any[]>("/runs?limit=50");
      const byAgent = Object.entries(runs.reduce((a: any, r: any) => { const n = (r.agent_name || "?").replace(" Agent", ""); a[n] = (a[n] || 0) + 1; return a; }, {}))
        .map(([name, value]) => ({ name, value }));
      const completed = runs.filter((r: any) => r.status === "completed").length;
      const failed = runs.filter((r: any) => r.status === "failed").length;

      return {
        title: "Run All — Executive Summary",
        summary: `${runs.length} total runs. ${completed} completed, ${failed} failed.`,
        cards: [
          { label: "Total", value: runs.length },
          { label: "Completed", value: completed },
          { label: "Failed", value: failed, alert: failed > 0 },
        ],
        charts: [
          { title: "By Agent", type: "bar", color: C.blue, data: byAgent },
          { title: "Status", type: "donut", data: [
            { name: "Completed", value: completed, color: C.green },
            { name: "Failed", value: failed, color: C.red },
            { name: "Other", value: Math.max(0, runs.length - completed - failed), color: C.gray },
          ].filter(d => d.value > 0) },
        ],
      };
    }
  } catch {}

  return { title: "Result", summary: "Data loaded.", cards: [], charts: [] };
}
