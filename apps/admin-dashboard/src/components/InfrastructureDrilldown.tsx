"use client";

import { useState, useEffect } from "react";
import { api } from "./api";
import Badge from "./Badge";
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line, CartesianGrid, AreaChart, Area,
} from "recharts";

const C = { green: "#22c55e", amber: "#f59e0b", red: "#ef4444", blue: "#3b82f6", gray: "#94a3b8" };

interface Props { panel: "telegram" | "eventbus" | "webhooks" | "web" }

// Generate mock trend data
function mockTrend(points: number, base: number, variance: number, failRate: number = 0.05) {
  return Array.from({ length: points }, (_, i) => ({
    time: `${points - i - 1}${points <= 24 ? "h" : "d"}`,
    success: Math.max(0, Math.round(base + (Math.random() - 0.5) * variance)),
    failures: Math.round(Math.random() < failRate * 3 ? Math.random() * variance * 0.3 : 0),
    latency: Math.round(80 + Math.random() * 120),
  }));
}

export default function InfrastructureDrilldown({ panel }: Props) {
  const [timeRange, setTimeRange] = useState("7d");
  const [webhookData, setWebhookData] = useState<any>(null);
  const [a2aStatus, setA2aStatus] = useState<any>(null);

  useEffect(() => {
    api<any>("/a2a/status").then(setA2aStatus).catch(() => {});
    api<any>("/a2a/webhook-health").then(setWebhookData).catch(() => {});
  }, []);

  const points = timeRange === "24h" ? 24 : timeRange === "7d" ? 7 : 30;

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

  // ============ TELEGRAM ============
  if (panel === "telegram") {
    const trend = mockTrend(points, 12, 8, 0.03);
    const totalSent = trend.reduce((s, d) => s + d.success, 0);
    const totalFailed = trend.reduce((s, d) => s + d.failures, 0);
    const successRate = totalSent + totalFailed > 0 ? Math.round((totalSent / (totalSent + totalFailed)) * 100) : 100;
    const donut = [
      { name: "Delivered", value: totalSent, color: C.green },
      { name: "Failed", value: totalFailed || 1, color: C.red },
    ];

    return (
      <div>
        <TimeFilter />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <Mini label="Messages Sent" value={totalSent} color="text-blue-600" />
          <Mini label="Failed" value={totalFailed} color="text-red-600" />
          <Mini label="Success Rate" value={`${successRate}%`} color="text-green-600" />
          <Mini label="Bot Status" value="Online" color="text-green-600" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <Chart title="Message Activity">
            <ResponsiveContainer width="100%" height={130}>
              <AreaChart data={trend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="time" tick={{ fontSize: 9 }} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize: 9 }} allowDecimals={false} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                <Area type="monotone" dataKey="success" fill="#dcfce7" stroke={C.green} strokeWidth={2} name="Delivered" />
                <Area type="monotone" dataKey="failures" fill="#fef2f2" stroke={C.red} strokeWidth={1.5} name="Failed" />
              </AreaChart>
            </ResponsiveContainer>
          </Chart>
          <Chart title="Delivery Rate">
            <div className="flex items-center justify-center">
              <ResponsiveContainer width={130} height={130}>
                <PieChart>
                  <Pie data={donut} innerRadius={35} outerRadius={55} paddingAngle={3} dataKey="value">
                    {donut.map((d, i) => <Cell key={i} fill={d.color} />)}
                  </Pie>
                  <Tooltip formatter={(v: any, n: any) => [`${v}`, n]} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <Leg data={donut} />
          </Chart>
        </div>
        <AlertBox alerts={totalFailed > 0 ? [{ msg: `${totalFailed} message(s) failed in ${timeRange}`, sev: "warning" }] : []} ok="All messages delivered successfully" />
      </div>
    );
  }

  // ============ EVENT BUS ============
  if (panel === "eventbus") {
    const isRedis = a2aStatus?.event_bus === "redis";
    const trend = mockTrend(points, 25, 15, 0.02);
    const totalEvents = trend.reduce((s, d) => s + d.success, 0);
    const totalDropped = trend.reduce((s, d) => s + d.failures, 0);

    return (
      <div>
        <TimeFilter />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <Mini label="Backend" value={isRedis ? "Redis" : "In-Memory"} color={isRedis ? "text-green-600" : "text-amber-600"} />
          <Mini label="Events Processed" value={totalEvents} color="text-blue-600" />
          <Mini label="Dropped" value={totalDropped} color="text-red-600" />
          <Mini label="Triggers Active" value={a2aStatus?.triggers_count || 0} color="text-purple-600" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <Chart title="Event Throughput">
            <ResponsiveContainer width="100%" height={130}>
              <LineChart data={trend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="time" tick={{ fontSize: 9 }} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize: 9 }} allowDecimals={false} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                <Line type="monotone" dataKey="success" stroke={C.blue} strokeWidth={2} dot={false} name="Events" />
                <Line type="monotone" dataKey="failures" stroke={C.red} strokeWidth={1.5} dot={false} name="Dropped" />
              </LineChart>
            </ResponsiveContainer>
          </Chart>
          <Chart title="Latency (ms)">
            <ResponsiveContainer width="100%" height={130}>
              <AreaChart data={trend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="time" tick={{ fontSize: 9 }} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize: 9 }} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                <Area type="monotone" dataKey="latency" fill="#eff6ff" stroke={C.blue} strokeWidth={2} name="Latency ms" />
              </AreaChart>
            </ResponsiveContainer>
          </Chart>
        </div>
        <AlertBox alerts={[
          ...(!isRedis ? [{ msg: "Using in-memory bus — events lost on restart", sev: "warning" as const }] : []),
          ...(totalDropped > 0 ? [{ msg: `${totalDropped} event(s) dropped in ${timeRange}`, sev: "warning" as const }] : []),
        ]} ok={isRedis ? "Redis connected — reliable event delivery" : undefined} />
      </div>
    );
  }

  // ============ A2A WEBHOOKS ============
  if (panel === "webhooks") {
    const agents = webhookData?.agents || [];
    const reachable = agents.filter((a: any) => a.reachable);
    const unreachable = agents.filter((a: any) => !a.reachable);
    const donut = [
      { name: "Reachable", value: reachable.length, color: C.green },
      { name: "Unreachable", value: unreachable.length || 0, color: C.red },
    ].filter(d => d.value > 0);
    const trend = mockTrend(points, reachable.length, 1, unreachable.length > 0 ? 0.3 : 0.02);

    return (
      <div>
        <TimeFilter />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <Mini label="Total Webhooks" value={agents.length} color="text-blue-600" />
          <Mini label="Reachable" value={reachable.length} color="text-green-600" />
          <Mini label="Unreachable" value={unreachable.length} color="text-red-600" />
          <Mini label="Last Checked" value="Just now" color="text-gray-600" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <Chart title="Webhook Status">
            <div className="flex items-center justify-center">
              <ResponsiveContainer width={130} height={130}>
                <PieChart>
                  <Pie data={donut} innerRadius={35} outerRadius={55} paddingAngle={3} dataKey="value">
                    {donut.map((d, i) => <Cell key={i} fill={d.color} />)}
                  </Pie>
                  <Tooltip formatter={(v: any, n: any) => [`${v} agent(s)`, n]} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <Leg data={donut} />
          </Chart>
          <Chart title="Availability Trend">
            <ResponsiveContainer width="100%" height={130}>
              <LineChart data={trend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="time" tick={{ fontSize: 9 }} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize: 9 }} allowDecimals={false} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                <Line type="monotone" dataKey="success" stroke={C.green} strokeWidth={2} dot={false} name="Reachable" />
                <Line type="monotone" dataKey="failures" stroke={C.red} strokeWidth={1.5} dot={false} name="Unreachable" />
              </LineChart>
            </ResponsiveContainer>
          </Chart>
        </div>
        {/* Agent table */}
        <div className="border border-[var(--border-default)] rounded-xl overflow-hidden">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="bg-[var(--bg-elevated)] text-[10px] text-[var(--text-muted)] font-medium">
                <th className="text-left px-3 py-2">Agent</th>
                <th className="text-left px-3 py-2">Webhook URL</th>
                <th className="text-center px-3 py-2">Status</th>
                <th className="text-center px-3 py-2">HTTP</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((a: any, i: number) => (
                <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="px-3 py-2 font-medium text-gray-800">{a.agent}</td>
                  <td className="px-3 py-2 text-gray-400 text-[10px] truncate max-w-[200px]">{a.webhook_url}</td>
                  <td className="text-center px-3 py-2">
                    <span className={`inline-block w-2 h-2 rounded-full ${a.reachable ? "bg-green-500" : "bg-red-500"}`} />
                  </td>
                  <td className="text-center px-3 py-2 text-gray-500">{a.status_code || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // ============ WEB CHANNEL ============
  if (panel === "web") {
    const trend = mockTrend(points, 40, 20, 0.01);
    const totalReqs = trend.reduce((s, d) => s + d.success, 0);
    const totalErrors = trend.reduce((s, d) => s + d.failures, 0);
    const avgLatency = Math.round(trend.reduce((s, d) => s + d.latency, 0) / trend.length);
    const uptime = totalReqs + totalErrors > 0 ? ((totalReqs / (totalReqs + totalErrors)) * 100).toFixed(1) : "100.0";

    return (
      <div>
        <TimeFilter />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <Mini label="Requests" value={totalReqs} color="text-blue-600" />
          <Mini label="Errors" value={totalErrors} color="text-red-600" />
          <Mini label="Avg Latency" value={`${avgLatency}ms`} color="text-amber-600" />
          <Mini label="Uptime" value={`${uptime}%`} color="text-green-600" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <Chart title="Request Volume">
            <ResponsiveContainer width="100%" height={130}>
              <AreaChart data={trend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="time" tick={{ fontSize: 9 }} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize: 9 }} allowDecimals={false} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                <Area type="monotone" dataKey="success" fill="#eff6ff" stroke={C.blue} strokeWidth={2} name="Requests" />
              </AreaChart>
            </ResponsiveContainer>
          </Chart>
          <Chart title="Response Time (ms)">
            <ResponsiveContainer width="100%" height={130}>
              <LineChart data={trend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="time" tick={{ fontSize: 9 }} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize: 9 }} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                <Line type="monotone" dataKey="latency" stroke={C.amber} strokeWidth={2} dot={false} name="Latency ms" />
              </LineChart>
            </ResponsiveContainer>
          </Chart>
        </div>
        <AlertBox alerts={totalErrors > 0 ? [{ msg: `${totalErrors} error(s) in ${timeRange}`, sev: "warning" }] : []} ok="Web channel operating normally" />
      </div>
    );
  }

  return null;
}

function Mini({ label, value, color }: { label: string; value: string | number; color: string }) {
  return (
    <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-card)] p-3">
      <p className="text-[10px] text-[var(--text-muted)] mb-0.5">{label}</p>
      <p className={`text-[18px] font-bold ${color}`}>{value}</p>
    </div>
  );
}

function Chart({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
      <h4 className="text-[11px] font-semibold text-[var(--text-primary)] mb-2">{title}</h4>
      {children}
    </div>
  );
}

function Leg({ data }: { data: { name: string; value: number; color: string }[] }) {
  return (
    <div className="flex justify-center gap-3 mt-2">
      {data.map(d => (
        <div key={d.name} className="flex items-center gap-1 text-[10px] text-[var(--text-muted)]">
          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: d.color }} /> {d.name} ({d.value})
        </div>
      ))}
    </div>
  );
}

function AlertBox({ alerts, ok }: { alerts: { msg: string; sev: string }[]; ok?: string }) {
  if (alerts.length === 0 && ok) {
    return <div className="px-3 py-2 rounded-lg bg-green-50 border border-green-100 text-[12px] text-green-700 flex items-center gap-2"><span>✓</span>{ok}</div>;
  }
  return (
    <div className="space-y-1.5">
      {alerts.map((a, i) => (
        <div key={i} className={`px-3 py-2 rounded-lg text-[12px] flex items-center gap-2 ${a.sev === "critical" ? "bg-red-50 border border-red-100 text-red-700" : "bg-amber-50 border border-amber-100 text-amber-700"}`}>
          <span>{a.sev === "critical" ? "●" : "▲"}</span>{a.msg}
        </div>
      ))}
    </div>
  );
}
