"use client";

import { useState, useEffect } from "react";
import { api } from "./api";
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line, CartesianGrid,
} from "recharts";

const C = { green: "#22c55e", amber: "#f59e0b", red: "#ef4444", blue: "#3b82f6", gray: "#94a3b8", purple: "#a855f7" };

interface Props {
  panel: "agents" | "active" | "failed" | "judgement";
}

export default function SummaryDrilldown({ panel }: Props) {
  const [agents, setAgents] = useState<any[]>([]);
  const [runs, setRuns] = useState<any[]>([]);
  const [cases, setCases] = useState<any[]>([]);
  const [timeRange, setTimeRange] = useState("7d");

  useEffect(() => {
    api<any[]>("/registry/agents").then(setAgents).catch(() => {});
    api<any[]>("/runs?limit=200").then(setRuns).catch(() => {});
    api<any[]>("/judgement/cases").then(setCases).catch(() => {});
  }, []);

  const toKST = (s: string) => s ? new Date(s).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "—";

  const TimeFilter = () => (
    <div className="flex items-center gap-1 bg-[var(--bg-elevated)] rounded-lg p-0.5 border border-[var(--border-default)] mb-4">
      {["24h", "7d", "30d"].map(t => (
        <button key={t} onClick={() => setTimeRange(t)}
          className={`px-3 py-1 text-[11px] font-medium rounded-md transition-colors ${timeRange === t ? "bg-white text-gray-900 shadow-sm" : "text-[var(--text-muted)]"}`}>
          {t}
        </button>
      ))}
    </div>
  );

  // ============ TOTAL AGENTS ============
  if (panel === "agents") {
    const active = agents.filter(a => a.status === "active");
    const inactive = agents.filter(a => a.status !== "active");
    const real = agents.filter(a => !a.is_mock);
    const mock = agents.filter(a => a.is_mock);

    const typeData = Object.entries(
      agents.reduce((acc: any, a: any) => { acc[a.type] = (acc[a.type] || 0) + 1; return acc; }, {})
    ).map(([name, value]) => ({ name, value }));

    const donutData = [
      { name: "Active", value: active.length, color: C.green },
      { name: "Inactive", value: inactive.length, color: C.gray },
    ].filter(d => d.value > 0);

    return (
      <div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <MiniCard label="Active" value={active.length} color="text-green-600" />
          <MiniCard label="Inactive" value={inactive.length} color="text-gray-500" />
          <MiniCard label="Real" value={real.length} color="text-blue-600" />
          <MiniCard label="Mock" value={mock.length} color="text-amber-600" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <ChartCard title="Agent Status">
            <ResponsiveContainer width="100%" height={140}>
              <PieChart>
                <Pie data={donutData} innerRadius={35} outerRadius={55} paddingAngle={3} dataKey="value">
                  {donutData.map((d, i) => <Cell key={i} fill={d.color} />)}
                </Pie>
                <Tooltip formatter={(v: any, n: any) => [`${v}`, n]} />
              </PieChart>
            </ResponsiveContainer>
            <Legend data={donutData} />
          </ChartCard>
          <ChartCard title="By Type">
            <ResponsiveContainer width="100%" height={140}>
              <BarChart data={typeData} barSize={24}>
                <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                <Bar dataKey="value" fill={C.blue} radius={[4, 4, 0, 0]} name="Agents" />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
        <SmallTable headers={["Agent", "Type", "Status", "Priority", "Reliability"]}>
          {agents.map((a, i) => (
            <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
              <td className="px-3 py-2 font-medium text-gray-800">{a.name}</td>
              <td className="px-3 py-2 text-gray-500">{a.type}</td>
              <td className="px-3 py-2"><StatusDot status={a.status} /></td>
              <td className="px-3 py-2 text-gray-500">{a.priority_score}</td>
              <td className="px-3 py-2"><RelBar value={Math.round((a.reliability_score || 1) * 100)} /></td>
            </tr>
          ))}
        </SmallTable>
      </div>
    );
  }

  // ============ ACTIVE RUNS ============
  if (panel === "active") {
    const activeRuns = runs.filter(r => ["pending", "dispatched", "running"].includes(r.status));
    const byAgent = Object.entries(
      activeRuns.reduce((acc: any, r: any) => { const n = r.agent_name || "Unknown"; acc[n] = (acc[n] || 0) + 1; return acc; }, {})
    ).map(([name, value]) => ({ name: name.replace(" Agent", ""), value }));

    const trendData = buildTrend(runs.filter(r => ["pending", "dispatched", "running", "completed"].includes(r.status)), timeRange, "all");

    return (
      <div>
        <TimeFilter />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <ChartCard title="Active Runs by Agent">
            <ResponsiveContainer width="100%" height={140}>
              <BarChart data={byAgent} barSize={28}>
                <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                <Bar dataKey="value" fill={C.blue} radius={[4, 4, 0, 0]} name="Active" />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
          <ChartCard title={`Run Activity (${timeRange})`}>
            <ResponsiveContainer width="100%" height={140}>
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="time" tick={{ fontSize: 9 }} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                <Line type="monotone" dataKey="count" stroke={C.blue} strokeWidth={2} dot={false} name="Runs" />
              </LineChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
        {activeRuns.length > 0 ? (
          <SmallTable headers={["Task", "Agent", "Status", "Started"]}>
            {activeRuns.slice(0, 10).map((r, i) => (
              <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="px-3 py-2 text-gray-800">{r.task_type}</td>
                <td className="px-3 py-2 text-blue-600 font-medium">{r.agent_name}</td>
                <td className="px-3 py-2"><StatusDot status={r.status} /></td>
                <td className="px-3 py-2 text-gray-400 text-[11px]">{toKST(r.started_at)}</td>
              </tr>
            ))}
          </SmallTable>
        ) : (
          <p className="text-center text-[var(--text-muted)] py-6 text-[12px]">No active runs right now</p>
        )}
      </div>
    );
  }

  // ============ FAILED RUNS ============
  if (panel === "failed") {
    const failedRuns = runs.filter(r => r.status === "failed");

    const byAgent = Object.entries(
      failedRuns.reduce((acc: any, r: any) => { const n = r.agent_name || "Unknown"; acc[n] = (acc[n] || 0) + 1; return acc; }, {})
    ).map(([name, value]) => ({ name: name.replace(" Agent", ""), value }));

    const reasonCounts: any = {};
    failedRuns.forEach(r => {
      const err = r.error_message || "Unknown";
      const reason = err.includes("imeout") ? "Timeout" : err.includes("onnect") ? "Connection" : err.includes("offline") ? "Unreachable" : err.includes("circuit") ? "Circuit Breaker" : "Other";
      reasonCounts[reason] = (reasonCounts[reason] || 0) + 1;
    });
    const reasonData: { name: string; value: number; color: string }[] = Object.entries(reasonCounts).map(([name, value]) => ({ name, value: value as number, color: name === "Timeout" ? C.amber : name === "Connection" ? C.red : name === "Circuit Breaker" ? C.purple : C.gray }));

    return (
      <div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-4">
          <MiniCard label="Total Failed" value={failedRuns.length} color="text-red-600" />
          <MiniCard label="Agents Affected" value={byAgent.length} color="text-amber-600" />
          <MiniCard label="Last Failure" value={failedRuns[0] ? toKST(failedRuns[0].started_at).split(" ")[1] || "—" : "None"} color="text-gray-600" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <ChartCard title="Failures by Agent">
            <ResponsiveContainer width="100%" height={140}>
              <BarChart data={byAgent} barSize={28}>
                <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                <Bar dataKey="value" fill={C.red} radius={[4, 4, 0, 0]} name="Failures" />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
          <ChartCard title="Failure Reasons">
            <ResponsiveContainer width="100%" height={140}>
              <PieChart>
                <Pie data={reasonData} innerRadius={35} outerRadius={55} paddingAngle={3} dataKey="value">
                  {reasonData.map((d: any, i) => <Cell key={i} fill={d.color} />)}
                </Pie>
                <Tooltip formatter={(v: any, n: any) => [`${v}`, n]} />
              </PieChart>
            </ResponsiveContainer>
            <Legend data={reasonData} />
          </ChartCard>
        </div>
        {failedRuns.length > 0 && (
          <SmallTable headers={["Task", "Agent", "Error", "Time"]}>
            {failedRuns.slice(0, 8).map((r, i) => (
              <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="px-3 py-2 text-gray-800">{r.task_type}</td>
                <td className="px-3 py-2 text-red-600 font-medium">{r.agent_name}</td>
                <td className="px-3 py-2 text-gray-500 text-[11px] max-w-[200px] truncate">{r.error_message?.slice(0, 60) || "—"}</td>
                <td className="px-3 py-2 text-gray-400 text-[11px]">{toKST(r.started_at)}</td>
              </tr>
            ))}
          </SmallTable>
        )}
      </div>
    );
  }

  // ============ PENDING JUDGEMENT ============
  if (panel === "judgement") {
    const pending = cases.filter(c => c.decision === "human_review_required" || c.decision === "conditional_approve");
    const approved = cases.filter(c => c.decision === "auto_approve");
    const rejected = cases.filter(c => c.decision === "rejected");

    const decisionData = [
      { name: "Pending", value: pending.length, color: C.amber },
      { name: "Approved", value: approved.length, color: C.green },
      { name: "Rejected", value: rejected.length, color: C.red },
    ].filter(d => d.value > 0);

    const riskBuckets = [
      { name: "Low (0-30)", value: cases.filter(c => (c.risk_score || 0) < 0.3).length, color: C.green },
      { name: "Medium (30-60)", value: cases.filter(c => (c.risk_score || 0) >= 0.3 && (c.risk_score || 0) < 0.6).length, color: C.amber },
      { name: "High (60+)", value: cases.filter(c => (c.risk_score || 0) >= 0.6).length, color: C.red },
    ].filter(d => d.value > 0);

    return (
      <div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <MiniCard label="Pending" value={pending.length} color="text-amber-600" />
          <MiniCard label="Approved" value={approved.length} color="text-green-600" />
          <MiniCard label="Rejected" value={rejected.length} color="text-red-600" />
          <MiniCard label="Total Cases" value={cases.length} color="text-blue-600" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <ChartCard title="Decision Breakdown">
            <ResponsiveContainer width="100%" height={140}>
              <PieChart>
                <Pie data={decisionData} innerRadius={35} outerRadius={55} paddingAngle={3} dataKey="value">
                  {decisionData.map((d, i) => <Cell key={i} fill={d.color} />)}
                </Pie>
                <Tooltip formatter={(v: any, n: any) => [`${v}`, n]} />
              </PieChart>
            </ResponsiveContainer>
            <Legend data={decisionData} />
          </ChartCard>
          <ChartCard title="Risk Distribution">
            <ResponsiveContainer width="100%" height={140}>
              <BarChart data={riskBuckets} barSize={32}>
                <XAxis dataKey="name" tick={{ fontSize: 9 }} />
                <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                <Bar dataKey="value" radius={[4, 4, 0, 0]} name="Cases">
                  {riskBuckets.map((d, i) => <Cell key={i} fill={d.color} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
        {pending.length > 0 && (
          <>
            <p className="text-[11px] font-semibold text-[var(--text-primary)] mb-2">Oldest Pending (needs attention)</p>
            <SmallTable headers={["Case", "Risk", "Decision", "Created"]}>
              {pending.sort((a: any, b: any) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()).slice(0, 8).map((c: any, i) => (
                <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="px-3 py-2 font-mono text-gray-800 text-[11px]">{c.id?.slice(0, 8)}...</td>
                  <td className="px-3 py-2"><RelBar value={Math.round((c.risk_score || 0) * 100)} danger /></td>
                  <td className="px-3 py-2 text-amber-600 font-medium text-[11px]">{c.decision}</td>
                  <td className="px-3 py-2 text-gray-400 text-[11px]">{toKST(c.created_at)}</td>
                </tr>
              ))}
            </SmallTable>
          </>
        )}
      </div>
    );
  }

  return null;
}

// ============ HELPER COMPONENTS ============

function MiniCard({ label, value, color }: { label: string; value: string | number; color: string }) {
  return (
    <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-card)] p-3">
      <p className="text-[10px] text-[var(--text-muted)] mb-0.5">{label}</p>
      <p className={`text-[18px] font-bold ${color}`}>{value}</p>
    </div>
  );
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] p-4">
      <h4 className="text-[11px] font-semibold text-[var(--text-primary)] mb-2">{title}</h4>
      {children}
    </div>
  );
}

function Legend({ data }: { data: { name: string; value: number; color?: string }[] }) {
  return (
    <div className="flex justify-center gap-3 mt-2">
      {data.map(d => (
        <div key={d.name} className="flex items-center gap-1 text-[10px] text-[var(--text-muted)]">
          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: d.color || C.gray }} />
          {d.name} ({d.value})
        </div>
      ))}
    </div>
  );
}

function SmallTable({ headers, children }: { headers: string[]; children: React.ReactNode }) {
  return (
    <div className="border border-[var(--border-default)] rounded-xl overflow-hidden">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="bg-[var(--bg-elevated)] text-[10px] text-[var(--text-muted)] font-medium">
            {headers.map(h => <th key={h} className="text-left px-3 py-2">{h}</th>)}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const color = status === "active" || status === "completed" ? "bg-green-500" : status === "error" || status === "failed" ? "bg-red-500" : status === "pending" || status === "dispatched" ? "bg-blue-500" : "bg-gray-400";
  return (
    <div className="flex items-center gap-1.5">
      <div className={`w-2 h-2 rounded-full ${color}`} />
      <span className="text-[11px] text-gray-600 capitalize">{status}</span>
    </div>
  );
}

function RelBar({ value, danger }: { value: number; danger?: boolean }) {
  const color = danger ? (value >= 60 ? "bg-red-500" : value >= 30 ? "bg-amber-500" : "bg-green-500") : (value >= 80 ? "bg-green-500" : value >= 50 ? "bg-amber-500" : "bg-red-500");
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-12 h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${value}%` }} />
      </div>
      <span className="text-[11px] text-gray-600">{value}%</span>
    </div>
  );
}

function buildTrend(runs: any[], timeRange: string, _filter: string) {
  const periods = timeRange === "24h" ? 24 : timeRange === "7d" ? 7 : 30;
  const unit = timeRange === "24h" ? 3600000 : 86400000;
  return Array.from({ length: periods }, (_, i) => {
    const idx = periods - 1 - i;
    const label = timeRange === "24h" ? `${idx}h` : `${idx}d`;
    const count = runs.filter(r => {
      if (!r.started_at) return false;
      const diff = Date.now() - new Date(r.started_at).getTime();
      return diff >= idx * unit && diff < (idx + 1) * unit;
    }).length;
    return { time: label, count };
  });
}
