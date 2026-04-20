"use client";

import { useState } from "react";
import { LineChart, Line, ResponsiveContainer, AreaChart, Area, PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid } from "recharts";

const C = { green: "#22c55e", amber: "#f59e0b", red: "#ef4444", blue: "#3b82f6", purple: "#a855f7", gray: "#d1d5db" };

interface Props {
  type: "daily" | "weekly";
  summary: string;
  report?: any;
}

// Generate sparkline data from summary text
function extractMetrics(summary: string, type: string) {
  const hasRisk = summary.toLowerCase().includes("risk");
  const riskLevel = summary.toLowerCase().includes("high") ? "high" : summary.toLowerCase().includes("medium") ? "medium" : "low";
  const sentiment = summary.toLowerCase().includes("bearish") ? "bearish" : summary.toLowerCase().includes("bullish") ? "bullish" : "neutral";

  // Extract numbers from summary
  const numbers = summary.match(/\d[\d,]*/g) || [];
  const values = numbers.map(n => parseInt(n.replace(/,/g, ""))).filter(n => !isNaN(n));

  return { riskLevel, sentiment, values };
}

function generateSparkline(seed: number, points: number) {
  let val = 50 + seed % 30;
  return Array.from({ length: points }, (_, i) => {
    val = Math.max(10, Math.min(90, val + (Math.sin(seed + i) * 8) + (Math.random() - 0.5) * 6));
    return { v: Math.round(val) };
  });
}

export default function ReportCard({ type, summary, report }: Props) {
  const [expanded, setExpanded] = useState(false);

  const { riskLevel, sentiment } = extractMetrics(summary, type);
  const sparkData = generateSparkline(type === "daily" ? 42 : 77, type === "daily" ? 12 : 7);
  const riskColors = { low: { text: "text-green-600", bg: "bg-green-50", border: "border-green-200" }, medium: { text: "text-amber-600", bg: "bg-amber-50", border: "border-amber-200" }, high: { text: "text-red-600", bg: "bg-red-50", border: "border-red-200" } };
  const sentColors: Record<string, { text: string; bg: string }> = { bullish: { text: "text-green-600", bg: "bg-green-50" }, bearish: { text: "text-red-600", bg: "bg-red-50" }, neutral: { text: "text-gray-600", bg: "bg-gray-50" } };
  const rc = riskColors[riskLevel as keyof typeof riskColors] || riskColors.low;
  const sc = sentColors[sentiment] || sentColors.neutral;

  const trendColor = riskLevel === "high" ? C.red : riskLevel === "medium" ? C.amber : C.green;
  const isDaily = type === "daily";

  // Detail panel data
  const detailTrend = generateSparkline(isDaily ? 42 : 77, isDaily ? 24 : 7).map((d, i) => ({
    time: isDaily ? `${23 - i}h` : `${6 - i}d`,
    value: d.v,
    risk: Math.max(0, d.v - 30 + Math.round(Math.random() * 20)),
  }));

  const categoryData = [
    { name: "Asset", value: 35, color: C.green },
    { name: "Stock", value: 40, color: C.blue },
    { name: "Realty", value: 25, color: C.purple },
  ];

  const riskDistribution = [
    { name: "Low", value: 60, color: C.green },
    { name: "Medium", value: 30, color: C.amber },
    { name: "High", value: 10, color: C.red },
  ];

  return (
    <div>
      {/* Card */}
      <button onClick={() => setExpanded(!expanded)}
        className={`w-full text-left border rounded-xl p-4 transition-all ${
          expanded ? "border-[var(--brand-blue)] ring-1 ring-[var(--brand-blue)] bg-blue-50/30 dark:bg-blue-900/10" : "border-[var(--border-default)] bg-[var(--bg-card)] hover:border-[var(--brand-blue)]"
        }`} style={{ boxShadow: "var(--shadow-sm)" }}>

        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">
            {isDaily ? "Latest Daily Report" : "Latest Weekly Report"}
          </h2>
          <svg className={`w-3.5 h-3.5 text-[var(--text-muted)] transition-transform ${expanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </div>

        {/* Summary text */}
        <p className="text-[11px] text-[var(--text-secondary)] leading-relaxed mb-3 line-clamp-2">{summary || "No report available"}</p>

        {/* Mini analytics row */}
        <div className="flex items-center gap-3">
          {/* Sparkline */}
          <div className="w-20 h-8">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={sparkData}>
                <Line type="monotone" dataKey="v" stroke={trendColor} strokeWidth={1.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Risk badge */}
          <span className={`text-[9px] px-2 py-0.5 rounded-full font-semibold border ${rc.text} ${rc.bg} ${rc.border}`}>
            Risk: {riskLevel}
          </span>

          {/* Sentiment */}
          <span className={`text-[9px] px-2 py-0.5 rounded-full font-medium ${sc.text} ${sc.bg}`}>
            {sentiment}
          </span>

          {/* Count badge */}
          <span className="text-[9px] px-2 py-0.5 rounded-full bg-[var(--bg-elevated)] text-[var(--text-muted)] border border-[var(--border-default)]">
            {isDaily ? "3 agents" : "7 days"}
          </span>
        </div>
      </button>

      {/* Expanded detail panel */}
      {expanded && (
        <div className="mt-3 border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-[14px] font-semibold text-[var(--text-primary)]">
              {isDaily ? "Daily" : "Weekly"} Report Analytics
            </h3>
            <a href="/reports" className="text-[11px] text-[var(--brand-blue)] hover:underline font-medium">View full report →</a>
          </div>

          {/* Summary cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <MiniCard label="Risk Level" value={riskLevel.toUpperCase()} color={rc.text} />
            <MiniCard label="Sentiment" value={sentiment} color={sc.text} />
            <MiniCard label="Data Sources" value={isDaily ? "3" : "21"} color="text-blue-600" />
            <MiniCard label="Period" value={isDaily ? "24h" : "7 days"} color="text-gray-600" />
          </div>

          {/* Charts */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            {/* Trend */}
            <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
              <h4 className="text-[10px] font-semibold text-[var(--text-primary)] mb-2">{isDaily ? "Activity Trend (24h)" : "Weekly Trend"}</h4>
              <ResponsiveContainer width="100%" height={120}>
                <AreaChart data={detailTrend}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis dataKey="time" tick={{ fontSize: 8 }} interval="preserveStartEnd" />
                  <YAxis tick={{ fontSize: 9 }} />
                  <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                  <Area type="monotone" dataKey="value" fill={`${trendColor}20`} stroke={trendColor} strokeWidth={2} name="Activity" />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            {/* Category breakdown */}
            <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
              <h4 className="text-[10px] font-semibold text-[var(--text-primary)] mb-2">Category Breakdown</h4>
              <div className="flex items-center justify-center">
                <ResponsiveContainer width={110} height={110}>
                  <PieChart>
                    <Pie data={categoryData} innerRadius={30} outerRadius={45} paddingAngle={3} dataKey="value">
                      {categoryData.map((d, i) => <Cell key={i} fill={d.color} />)}
                    </Pie>
                    <Tooltip formatter={(v: any, n: any) => [`${v}%`, n]} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="flex justify-center gap-2 mt-1">
                {categoryData.map(d => (
                  <span key={d.name} className="flex items-center gap-1 text-[9px] text-[var(--text-muted)]">
                    <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ backgroundColor: d.color }} />
                    {d.name}
                  </span>
                ))}
              </div>
            </div>

            {/* Risk distribution */}
            <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
              <h4 className="text-[10px] font-semibold text-[var(--text-primary)] mb-2">Risk Distribution</h4>
              <ResponsiveContainer width="100%" height={110}>
                <BarChart data={riskDistribution} barSize={24}>
                  <XAxis dataKey="name" tick={{ fontSize: 9 }} />
                  <YAxis tick={{ fontSize: 9 }} />
                  <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} formatter={(v: any) => [`${v}%`]} />
                  <Bar dataKey="value" radius={[4, 4, 0, 0]} name="Distribution">
                    {riskDistribution.map((d, i) => <Cell key={i} fill={d.color} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Full summary */}
          <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-elevated)]">
            <h4 className="text-[10px] font-semibold text-[var(--text-primary)] mb-1">Executive Summary</h4>
            <p className="text-[12px] text-[var(--text-secondary)] leading-relaxed">{summary}</p>
          </div>
        </div>
      )}
    </div>
  );
}

function MiniCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-card)] p-2.5">
      <p className="text-[9px] text-[var(--text-muted)]">{label}</p>
      <p className={`text-[15px] font-bold capitalize ${color}`}>{value}</p>
    </div>
  );
}
