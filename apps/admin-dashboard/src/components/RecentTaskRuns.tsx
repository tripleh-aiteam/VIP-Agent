"use client";

import { useState } from "react";
import Badge from "./Badge";
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line, CartesianGrid,
} from "recharts";

const C = { green: "#22c55e", amber: "#f59e0b", red: "#ef4444", blue: "#3b82f6", gray: "#94a3b8", purple: "#a855f7" };
const STATUS_COLORS: Record<string, string> = { completed: C.green, failed: C.red, pending: C.amber, dispatched: C.blue, running: C.blue, review_required: C.purple };

interface Props {
  runs: any[];
}

export default function RecentTaskRuns({ runs }: Props) {
  const [view, setView] = useState<"table" | "graph">("table");
  const [timeRange, setTimeRange] = useState("7d");
  const [agentFilter, setAgentFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortAsc, setSortAsc] = useState(false);

  const toKST = (s: string) => s ? new Date(s).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "—";

  // Unique values for filters
  const allAgents = Array.from(new Set(runs.map(r => r.agent_name).filter(Boolean)));
  const allStatuses = Array.from(new Set(runs.map(r => r.status).filter(Boolean)));
  const allTypes = Array.from(new Set(runs.map(r => r.task_type).filter(Boolean)));

  // Filter runs
  const filtered = runs.filter(r => {
    if (agentFilter !== "all" && r.agent_name !== agentFilter) return false;
    if (statusFilter !== "all" && r.status !== statusFilter) return false;
    if (typeFilter !== "all" && r.task_type !== typeFilter) return false;
    if (timeRange !== "all") {
      const hours = timeRange === "24h" ? 24 : timeRange === "7d" ? 168 : 720;
      const cutoff = Date.now() - hours * 3600000;
      if (r.started_at && new Date(r.started_at).getTime() < cutoff) return false;
    }
    return true;
  });

  // Sort
  const sorted = sortCol ? [...filtered].sort((a, b) => {
    const va = a[sortCol] || "";
    const vb = b[sortCol] || "";
    return sortAsc ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
  }) : filtered;

  const toggleSort = (col: string) => {
    if (sortCol === col) { setSortAsc(!sortAsc); } else { setSortCol(col); setSortAsc(true); }
  };

  // Chart data
  const byAgent = Object.entries(
    filtered.reduce((acc: any, r) => { const n = (r.agent_name || "Unknown").replace(" Agent", ""); acc[n] = (acc[n] || 0) + 1; return acc; }, {})
  ).map(([name, value]) => ({ name, value }));

  const statusBreakdown = Object.entries(
    filtered.reduce((acc: any, r) => { acc[r.status] = (acc[r.status] || 0) + 1; return acc; }, {})
  ).map(([name, value]) => ({ name, value: value as number, color: STATUS_COLORS[name] || C.gray }));

  const byType = Object.entries(
    filtered.reduce((acc: any, r) => { acc[r.task_type || "unknown"] = (acc[r.task_type || "unknown"] || 0) + 1; return acc; }, {})
  ).map(([name, value]) => ({ name: name.replace(/_/g, " "), value }));

  const trendData = (() => {
    const periods = timeRange === "24h" ? 24 : timeRange === "7d" ? 7 : 30;
    const unit = timeRange === "24h" ? 3600000 : 86400000;
    return Array.from({ length: Math.min(periods, 14) }, (_, i) => {
      const idx = Math.min(periods, 14) - 1 - i;
      const label = timeRange === "24h" ? `${idx}h` : `${idx}d`;
      const periodRuns = filtered.filter(r => {
        if (!r.started_at) return false;
        const diff = Date.now() - new Date(r.started_at).getTime();
        return diff >= idx * unit && diff < (idx + 1) * unit;
      });
      return {
        time: label,
        total: periodRuns.length,
        completed: periodRuns.filter(r => r.status === "completed").length,
        failed: periodRuns.filter(r => r.status === "failed").length,
      };
    });
  })();

  return (
    <div className="border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)]" style={{ boxShadow: "var(--shadow-sm)" }}>
      {/* Header */}
      <div className="px-4 py-3 border-b border-[var(--border-default)] flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Recent Task Runs</h2>
        <div className="flex items-center gap-2">
          {/* Filters */}
          <select value={timeRange} onChange={e => setTimeRange(e.target.value)}
            className="text-[10px] bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-lg px-2 py-1 text-[var(--text-secondary)] focus:outline-none">
            <option value="24h">24h</option><option value="7d">7 days</option><option value="30d">30 days</option><option value="all">All</option>
          </select>
          <select value={agentFilter} onChange={e => setAgentFilter(e.target.value)}
            className="text-[10px] bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-lg px-2 py-1 text-[var(--text-secondary)] focus:outline-none">
            <option value="all">All Agents</option>
            {allAgents.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
            className="text-[10px] bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-lg px-2 py-1 text-[var(--text-secondary)] focus:outline-none">
            <option value="all">All Status</option>
            {allStatuses.map(s => <option key={s} value={s}>{s}</option>)}
          </select>

          {/* View toggle */}
          <div className="flex items-center gap-0.5 bg-[var(--bg-elevated)] rounded-lg p-0.5 border border-[var(--border-default)]">
            <button onClick={() => setView("table")}
              className={`px-2.5 py-1 text-[10px] font-medium rounded-md transition-colors ${view === "table" ? "bg-white text-gray-900 shadow-sm dark:bg-gray-700 dark:text-white" : "text-[var(--text-muted)]"}`}>
              Table
            </button>
            <button onClick={() => setView("graph")}
              className={`px-2.5 py-1 text-[10px] font-medium rounded-md transition-colors ${view === "graph" ? "bg-white text-gray-900 shadow-sm dark:bg-gray-700 dark:text-white" : "text-[var(--text-muted)]"}`}>
              Graph
            </button>
          </div>
        </div>
      </div>

      {/* Graph View */}
      {view === "graph" && (
        <div className="p-4 space-y-4">
          {/* Top charts */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Line — trend */}
            <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
              <h4 className="text-[11px] font-semibold text-[var(--text-primary)] mb-2">Runs Over Time</h4>
              <ResponsiveContainer width="100%" height={130}>
                <LineChart data={trendData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis dataKey="time" tick={{ fontSize: 9 }} interval="preserveStartEnd" />
                  <YAxis tick={{ fontSize: 9 }} allowDecimals={false} />
                  <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                  <Line type="monotone" dataKey="completed" stroke={C.green} strokeWidth={2} dot={false} name="Completed" />
                  <Line type="monotone" dataKey="failed" stroke={C.red} strokeWidth={2} dot={false} name="Failed" />
                </LineChart>
              </ResponsiveContainer>
            </div>

            {/* Bar — by agent */}
            <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
              <h4 className="text-[11px] font-semibold text-[var(--text-primary)] mb-2">By Agent</h4>
              <ResponsiveContainer width="100%" height={130}>
                <BarChart data={byAgent} barSize={24}>
                  <XAxis dataKey="name" tick={{ fontSize: 9 }} />
                  <YAxis tick={{ fontSize: 9 }} allowDecimals={false} />
                  <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                  <Bar dataKey="value" fill={C.blue} radius={[4, 4, 0, 0]} name="Runs" />
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Donut — status */}
            <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
              <h4 className="text-[11px] font-semibold text-[var(--text-primary)] mb-2">Status Breakdown</h4>
              <div className="flex items-center justify-center">
                <ResponsiveContainer width={130} height={130}>
                  <PieChart>
                    <Pie data={statusBreakdown} innerRadius={35} outerRadius={55} paddingAngle={3} dataKey="value">
                      {statusBreakdown.map((d, i) => <Cell key={i} fill={d.color} />)}
                    </Pie>
                    <Tooltip formatter={(v: any, n: any) => [`${v} runs`, n]} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="flex flex-wrap justify-center gap-2 mt-1">
                {statusBreakdown.map(d => (
                  <div key={d.name} className="flex items-center gap-1 text-[9px] text-[var(--text-muted)]">
                    <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: d.color }} />
                    {d.name} ({d.value})
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Task type breakdown */}
          {byType.length > 1 && (
            <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
              <h4 className="text-[11px] font-semibold text-[var(--text-primary)] mb-2">By Task Type</h4>
              <ResponsiveContainer width="100%" height={100}>
                <BarChart data={byType} barSize={20} layout="vertical">
                  <XAxis type="number" tick={{ fontSize: 9 }} allowDecimals={false} />
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 9 }} width={100} />
                  <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                  <Bar dataKey="value" fill={C.purple} radius={[0, 4, 4, 0]} name="Runs" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          <p className="text-[10px] text-[var(--text-muted)]">{filtered.length} runs shown</p>
        </div>
      )}

      {/* Table View */}
      {view === "table" && (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[var(--text-muted)] text-[10px] font-medium border-b border-[var(--border-default)] bg-[var(--bg-elevated)]">
                {[
                  { key: "task_type", label: "Type" },
                  { key: "agent_name", label: "Agent" },
                  { key: "status", label: "Status" },
                  { key: "trace_id", label: "Trace" },
                  { key: "started_at", label: "Started" },
                  { key: "finished_at", label: "Finished" },
                ].map(col => (
                  <th key={col.key} className="text-left px-4 py-2 cursor-pointer hover:text-[var(--text-primary)]" onClick={() => toggleSort(col.key)}>
                    {col.label} {sortCol === col.key ? (sortAsc ? "↑" : "↓") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, 20).map((r: any, i: number) => (
                <tr key={i} className="border-b border-[var(--border-default)]/30 hover:bg-[var(--bg-hover)]">
                  <td className="px-4 py-2 text-[var(--text-secondary)]">{r.task_type}</td>
                  <td className="px-4 py-2 text-[var(--brand-blue)] font-medium">{r.agent_name}</td>
                  <td className="px-4 py-2"><Badge text={r.status} /></td>
                  <td className="px-4 py-2 text-[var(--text-muted)] font-mono text-[10px]">{r.trace_id}</td>
                  <td className="px-4 py-2 text-[var(--text-muted)] text-[11px]">{toKST(r.started_at)}</td>
                  <td className="px-4 py-2 text-[var(--text-muted)] text-[11px]">{toKST(r.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {sorted.length === 0 && <p className="text-center text-[var(--text-muted)] py-6 text-xs">No runs match filters</p>}
          {sorted.length > 20 && <p className="text-center text-[var(--text-muted)] py-2 text-[10px]">Showing 20 of {sorted.length}</p>}
        </div>
      )}
    </div>
  );
}
