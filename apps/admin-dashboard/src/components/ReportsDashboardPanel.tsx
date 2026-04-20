"use client";

import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line, CartesianGrid,
} from "recharts";

const C = { blue: "#3b82f6", green: "#22c55e", red: "#ef4444", purple: "#a855f7", amber: "#f59e0b", emerald: "#10b981", sky: "#0ea5e9", orange: "#f97316", gray: "#94a3b8" };

const TYPE_COLORS: Record<string, string> = {
  daily_summary: C.blue, weekly_summary: C.green, urgent_alert_summary: C.red,
  cross_agent_summary: C.purple, agent_daily_asset: C.emerald, agent_daily_stock: C.sky, agent_daily_realty: C.orange,
};

interface Props {
  reports: any[];
  dailySummary: string;
  weeklySummary: string;
}

export default function ReportsDashboardPanel({ reports, dailySummary, weeklySummary }: Props) {
  const toKST = (s: string) => s ? new Date(s).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "—";

  // Report type counts
  const typeCounts = reports.reduce((acc: any, r: any) => {
    const t = r.report_type || "unknown";
    const label = t.replace(/_/g, " ").replace("agent daily ", "").replace("summary", "").trim() || t;
    acc[label] = (acc[label] || 0) + 1;
    return acc;
  }, {});
  const typeData = Object.entries(typeCounts).map(([name, value]) => ({
    name: name.charAt(0).toUpperCase() + name.slice(1),
    value: value as number,
  }));

  // Donut data
  const donutData = reports.reduce((acc: any, r: any) => {
    const t = r.report_type || "unknown";
    const existing = acc.find((d: any) => d.type === t);
    if (existing) { existing.value++; }
    else { acc.push({ name: t.replace(/_/g, " "), type: t, value: 1, color: TYPE_COLORS[t] || C.gray }); }
    return acc;
  }, [] as any[]);

  // Timeline — reports per day (last 7 days)
  const timelineData = Array.from({ length: 7 }, (_, i) => {
    const idx = 6 - i;
    const dayStart = new Date(); dayStart.setDate(dayStart.getDate() - idx); dayStart.setHours(0, 0, 0, 0);
    const dayEnd = new Date(dayStart); dayEnd.setDate(dayEnd.getDate() + 1);
    const label = dayStart.toLocaleDateString("ko-KR", { month: "short", day: "numeric" });
    const count = reports.filter(r => {
      if (!r.created_at) return false;
      const d = new Date(r.created_at);
      return d >= dayStart && d < dayEnd;
    }).length;
    return { time: label, reports: count };
  });

  // Stats
  const daily = reports.filter(r => r.report_type === "daily_summary" || r.report_type?.startsWith("agent_daily_"));
  const weekly = reports.filter(r => r.report_type === "weekly_summary");
  const crossAgent = reports.filter(r => r.report_type === "cross_agent_summary");
  const alerts = reports.filter(r => r.report_type === "urgent_alert_summary");

  return (
    <div className="space-y-4">
      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Mini label="Total Reports" value={reports.length} color="text-blue-600" />
        <Mini label="Daily" value={daily.length} color="text-blue-500" />
        <Mini label="Weekly" value={weekly.length} color="text-green-600" />
        <Mini label="Cross-Agent" value={crossAgent.length} color="text-purple-600" />
        <Mini label="Alerts" value={alerts.length} color="text-red-600" />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Timeline */}
        <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
          <h4 className="text-[11px] font-semibold text-[var(--text-primary)] mb-2">Reports per Day (7d)</h4>
          <ResponsiveContainer width="100%" height={130}>
            <BarChart data={timelineData} barSize={20}>
              <XAxis dataKey="time" tick={{ fontSize: 9 }} />
              <YAxis tick={{ fontSize: 9 }} allowDecimals={false} />
              <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
              <Bar dataKey="reports" fill={C.blue} radius={[4, 4, 0, 0]} name="Reports" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Type breakdown donut */}
        <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
          <h4 className="text-[11px] font-semibold text-[var(--text-primary)] mb-2">By Type</h4>
          <div className="flex items-center justify-center">
            <ResponsiveContainer width={130} height={130}>
              <PieChart>
                <Pie data={donutData} innerRadius={35} outerRadius={55} paddingAngle={3} dataKey="value">
                  {donutData.map((d: any, i: number) => <Cell key={i} fill={d.color} />)}
                </Pie>
                <Tooltip formatter={(v: any, n: any) => [`${v}`, n]} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="flex flex-wrap justify-center gap-2 mt-1">
            {donutData.slice(0, 5).map((d: any) => (
              <div key={d.name} className="flex items-center gap-1 text-[9px] text-[var(--text-muted)]">
                <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: d.color }} />
                {d.name} ({d.value})
              </div>
            ))}
          </div>
        </div>

        {/* Type bar */}
        <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
          <h4 className="text-[11px] font-semibold text-[var(--text-primary)] mb-2">Report Volume</h4>
          <ResponsiveContainer width="100%" height={130}>
            <BarChart data={typeData} barSize={16} layout="vertical">
              <XAxis type="number" tick={{ fontSize: 9 }} allowDecimals={false} />
              <YAxis type="category" dataKey="name" tick={{ fontSize: 9 }} width={70} />
              <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
              <Bar dataKey="value" fill={C.purple} radius={[0, 4, 4, 0]} name="Count" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Latest summaries */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
          <h4 className="text-[11px] font-semibold text-blue-600 mb-2">Latest Daily</h4>
          <p className="text-[12px] text-[var(--text-secondary)] leading-relaxed">{dailySummary || "No daily report yet"}</p>
        </div>
        <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
          <h4 className="text-[11px] font-semibold text-green-600 mb-2">Latest Weekly</h4>
          <p className="text-[12px] text-[var(--text-secondary)] leading-relaxed">{weeklySummary || "No weekly report yet"}</p>
        </div>
      </div>

      {/* Recent reports table */}
      <div className="border border-[var(--border-default)] rounded-xl overflow-hidden">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="bg-[var(--bg-elevated)] text-[10px] text-[var(--text-muted)] font-medium">
              <th className="text-left px-3 py-2">Type</th>
              <th className="text-left px-3 py-2">Summary</th>
              <th className="text-center px-3 py-2">Sources</th>
              <th className="text-left px-3 py-2">Created</th>
            </tr>
          </thead>
          <tbody>
            {reports.slice(0, 8).map((r: any, i: number) => (
              <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="px-3 py-2">
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium" style={{ color: TYPE_COLORS[r.report_type] || C.gray, backgroundColor: `${TYPE_COLORS[r.report_type] || C.gray}15` }}>
                    {(r.report_type || "").replace(/_/g, " ")}
                  </span>
                </td>
                <td className="px-3 py-2 text-[var(--text-secondary)] max-w-[250px] truncate">{r.executive_summary?.slice(0, 80) || "—"}</td>
                <td className="text-center px-3 py-2 text-[var(--text-muted)]">{r.source_run_count || 0}</td>
                <td className="px-3 py-2 text-[var(--text-muted)] text-[11px]">{toKST(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <a href="/reports" className="block text-center text-[12px] text-[var(--brand-blue)] hover:underline font-medium">View all reports →</a>
    </div>
  );
}

function Mini({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-card)] p-3">
      <p className="text-[10px] text-[var(--text-muted)] mb-0.5">{label}</p>
      <p className={`text-[18px] font-bold ${color}`}>{value}</p>
    </div>
  );
}
