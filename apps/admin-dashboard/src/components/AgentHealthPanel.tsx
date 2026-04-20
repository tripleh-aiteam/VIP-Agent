"use client";

import { useState, useEffect } from "react";
import { api } from "./api";
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line, CartesianGrid,
} from "recharts";

const COLORS = { healthy: "#22c55e", warning: "#f59e0b", failed: "#ef4444", offline: "#94a3b8" };

interface AgentData {
  name: string;
  type: string;
  status: string;
  reliability_score: number;
  priority_score: number;
  is_mock: boolean;
  endpoint_url: string;
}

export default function AgentHealthPanel() {
  const [agents, setAgents] = useState<AgentData[]>([]);
  const [runs, setRuns] = useState<any[]>([]);
  const [webhookHealth, setWebhookHealth] = useState<any>(null);
  const [timeRange, setTimeRange] = useState<string>("24h");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  useEffect(() => {
    api<any[]>("/registry/agents").then(setAgents).catch(() => {});
    api<any[]>("/runs?limit=100").then(setRuns).catch(() => {});
    api<any>("/a2a/status").then((d: any) => setWebhookHealth(d?.agent_webhooks)).catch(() => {});
  }, []);

  // Compute metrics
  const healthy = agents.filter(a => a.status === "active" && (a.reliability_score || 1) >= 0.7);
  const warning = agents.filter(a => a.status === "active" && (a.reliability_score || 1) < 0.7);
  const failed = agents.filter(a => a.status === "error");
  const offline = agents.filter(a => a.status === "inactive");

  // Per-agent run stats
  const agentRunStats = agents.map(a => {
    const agentRuns = runs.filter((r: any) => r.agent_name === a.name);
    const completed = agentRuns.filter((r: any) => r.status === "completed").length;
    const failedRuns = agentRuns.filter((r: any) => r.status === "failed").length;
    const total = agentRuns.length;
    const successRate = total > 0 ? Math.round((completed / total) * 100) : 100;
    const lastRun = agentRuns[0];
    return {
      name: a.name.replace(" Agent", ""),
      fullName: a.name,
      status: a.status,
      reliability: Math.round((a.reliability_score || 1) * 100),
      total,
      completed,
      failed: failedRuns,
      successRate,
      lastRun: lastRun?.started_at ? new Date(lastRun.started_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "—",
    };
  });

  // Status donut data
  const donutData = [
    { name: "Healthy", value: healthy.length, color: COLORS.healthy },
    { name: "Warning", value: warning.length, color: COLORS.warning },
    { name: "Failed", value: failed.length, color: COLORS.failed },
    { name: "Offline", value: offline.length, color: COLORS.offline },
  ].filter(d => d.value > 0);

  // Trend data (mock — based on run timestamps)
  const trendData = (() => {
    const hours = timeRange === "24h" ? 24 : timeRange === "7d" ? 7 : 30;
    const points: any[] = [];
    for (let i = hours - 1; i >= 0; i--) {
      const label = timeRange === "24h" ? `${i}h ago` : `${i}d ago`;
      const periodRuns = runs.filter((r: any) => {
        if (!r.started_at) return false;
        const diff = Date.now() - new Date(r.started_at).getTime();
        const unit = timeRange === "24h" ? 3600000 : 86400000;
        return diff >= i * unit && diff < (i + 1) * unit;
      });
      points.push({
        time: label,
        runs: periodRuns.length,
        failures: periodRuns.filter((r: any) => r.status === "failed").length,
        completed: periodRuns.filter((r: any) => r.status === "completed").length,
      });
    }
    return points;
  })();

  // Alerts
  const alerts = [
    ...failed.map(a => ({ agent: a.name, message: `${a.name} is in error state`, severity: "critical" as const })),
    ...warning.map(a => ({ agent: a.name, message: `${a.name} low reliability (${Math.round((a.reliability_score || 0) * 100)}%)`, severity: "warning" as const })),
    ...(webhookHealth?.agents || [])
      .filter((w: any) => !w.reachable)
      .map((w: any) => ({ agent: w.agent, message: `${w.agent} webhook unreachable`, severity: "warning" as const })),
  ];

  // Filtered agents for table
  const filteredAgents = statusFilter === "all" ? agentRunStats
    : statusFilter === "healthy" ? agentRunStats.filter(a => a.status === "active" && a.reliability >= 70)
    : statusFilter === "warning" ? agentRunStats.filter(a => a.reliability < 70)
    : agentRunStats.filter(a => a.status === "error" || a.status === "inactive");

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1 bg-[var(--bg-elevated)] rounded-lg p-0.5 border border-[var(--border-default)]">
          {["24h", "7d", "30d"].map(t => (
            <button key={t} onClick={() => setTimeRange(t)}
              className={`px-3 py-1 text-[11px] font-medium rounded-md transition-colors ${timeRange === t ? "bg-white text-gray-900 shadow-sm dark:bg-gray-700 dark:text-white" : "text-[var(--text-muted)]"}`}>
              {t}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1 bg-[var(--bg-elevated)] rounded-lg p-0.5 border border-[var(--border-default)]">
          {["all", "healthy", "warning", "failed"].map(s => (
            <button key={s} onClick={() => setStatusFilter(s)}
              className={`px-3 py-1 text-[11px] font-medium rounded-md capitalize transition-colors ${statusFilter === s ? "bg-white text-gray-900 shadow-sm dark:bg-gray-700 dark:text-white" : "text-[var(--text-muted)]"}`}>
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <SummaryCard label="Healthy" value={healthy.length} color="text-green-600" bg="bg-green-50" icon="✓" />
        <SummaryCard label="Warning" value={warning.length} color="text-amber-600" bg="bg-amber-50" icon="!" />
        <SummaryCard label="Failed" value={failed.length} color="text-red-600" bg="bg-red-50" icon="✕" />
        <SummaryCard label="Avg Reliability" value={`${agents.length > 0 ? Math.round(agents.reduce((s, a) => s + (a.reliability_score || 0), 0) / agents.length * 100) : 0}%`} color="text-blue-600" bg="bg-blue-50" icon="◎" />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Donut */}
        <div className="border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] p-4">
          <h4 className="text-[12px] font-semibold text-[var(--text-primary)] mb-3">Status Breakdown</h4>
          <div className="flex items-center justify-center">
            <ResponsiveContainer width={160} height={160}>
              <PieChart>
                <Pie data={donutData} innerRadius={45} outerRadius={70} paddingAngle={3} dataKey="value">
                  {donutData.map((d, i) => <Cell key={i} fill={d.color} />)}
                </Pie>
                <Tooltip formatter={(v: any, n: any) => [`${v} agent(s)`, n]} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="flex justify-center gap-3 mt-2">
            {donutData.map(d => (
              <div key={d.name} className="flex items-center gap-1 text-[10px] text-[var(--text-muted)]">
                <div className="w-2 h-2 rounded-full" style={{ backgroundColor: d.color }} />
                {d.name} ({d.value})
              </div>
            ))}
          </div>
        </div>

        {/* Bar — Performance */}
        <div className="border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] p-4">
          <h4 className="text-[12px] font-semibold text-[var(--text-primary)] mb-3">Success Rate by Agent</h4>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={agentRunStats} barSize={28}>
              <XAxis dataKey="name" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} domain={[0, 100]} />
              <Tooltip content={({ payload }) => {
                if (!payload?.length) return null;
                const d = payload[0].payload;
                return (
                  <div className="bg-white border border-gray-200 rounded-lg p-2.5 shadow-lg text-[11px]">
                    <p className="font-semibold text-gray-800 mb-1">{d.fullName}</p>
                    <p className="text-gray-500">Total runs: {d.total}</p>
                    <p className="text-green-600">Completed: {d.completed}</p>
                    <p className="text-red-500">Failed: {d.failed}</p>
                    <p className="text-gray-500">Last run: {d.lastRun}</p>
                  </div>
                );
              }} />
              <Bar dataKey="successRate" radius={[4, 4, 0, 0]}>
                {agentRunStats.map((d, i) => (
                  <Cell key={i} fill={d.successRate >= 80 ? COLORS.healthy : d.successRate >= 50 ? COLORS.warning : COLORS.failed} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Line — Trend */}
        <div className="border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] p-4">
          <h4 className="text-[12px] font-semibold text-[var(--text-primary)] mb-3">Activity Trend ({timeRange})</h4>
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={trendData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="time" tick={{ fontSize: 9 }} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
              <Line type="monotone" dataKey="completed" stroke={COLORS.healthy} strokeWidth={2} dot={false} name="Completed" />
              <Line type="monotone" dataKey="failures" stroke={COLORS.failed} strokeWidth={2} dot={false} name="Failures" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Alerts */}
      {alerts.length > 0 && (
        <div className="border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] p-4">
          <h4 className="text-[12px] font-semibold text-[var(--text-primary)] mb-3">Active Alerts ({alerts.length})</h4>
          <div className="space-y-1.5">
            {alerts.map((a, i) => (
              <div key={i} className={`flex items-center gap-2 px-3 py-2 rounded-lg text-[12px] ${
                a.severity === "critical" ? "bg-red-50 text-red-700 border border-red-100" : "bg-amber-50 text-amber-700 border border-amber-100"
              }`}>
                <span>{a.severity === "critical" ? "●" : "▲"}</span>
                <span>{a.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Agent Table */}
      <div className="border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] overflow-hidden">
        <div className="px-4 py-3 border-b border-[var(--border-default)]">
          <h4 className="text-[12px] font-semibold text-[var(--text-primary)]">Agent Details</h4>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[var(--text-muted)] text-[10px] font-medium border-b border-[var(--border-default)] bg-[var(--bg-elevated)]">
                <th className="text-left px-4 py-2">Agent</th>
                <th className="text-center px-3 py-2">Status</th>
                <th className="text-center px-3 py-2">Reliability</th>
                <th className="text-center px-3 py-2">Success Rate</th>
                <th className="text-center px-3 py-2">Runs</th>
                <th className="text-center px-3 py-2">Failed</th>
                <th className="text-left px-3 py-2">Last Run</th>
              </tr>
            </thead>
            <tbody>
              {filteredAgents.map((a, i) => (
                <tr key={i} className="border-b border-[var(--border-default)]/50 hover:bg-[var(--bg-hover)]">
                  <td className="px-4 py-2.5 font-medium text-[var(--text-primary)]">{a.fullName}</td>
                  <td className="text-center px-3 py-2.5">
                    <span className={`inline-block w-2 h-2 rounded-full ${a.status === "active" ? "bg-green-500" : a.status === "error" ? "bg-red-500" : "bg-gray-400"}`} />
                  </td>
                  <td className="text-center px-3 py-2.5">
                    <span className={`font-medium ${a.reliability >= 80 ? "text-green-600" : a.reliability >= 50 ? "text-amber-600" : "text-red-600"}`}>
                      {a.reliability}%
                    </span>
                  </td>
                  <td className="text-center px-3 py-2.5">
                    <span className={`font-medium ${a.successRate >= 80 ? "text-green-600" : a.successRate >= 50 ? "text-amber-600" : "text-red-600"}`}>
                      {a.successRate}%
                    </span>
                  </td>
                  <td className="text-center px-3 py-2.5 text-[var(--text-muted)]">{a.total}</td>
                  <td className="text-center px-3 py-2.5">
                    <span className={a.failed > 0 ? "text-red-500 font-medium" : "text-[var(--text-muted)]"}>{a.failed}</span>
                  </td>
                  <td className="px-3 py-2.5 text-[var(--text-muted)] text-[11px]">{a.lastRun}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function SummaryCard({ label, value, color, bg, icon }: { label: string; value: string | number; color: string; bg: string; icon: string }) {
  return (
    <div className={`rounded-xl border border-[var(--border-default)] p-3.5 ${bg}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] text-[var(--text-muted)] font-medium">{label}</span>
        <span className={`text-[14px] ${color}`}>{icon}</span>
      </div>
      <p className={`text-[22px] font-bold ${color}`}>{value}</p>
    </div>
  );
}
