"use client";

import { useEffect, useState } from "react";
import { api } from "@/components/api";
import StatCard from "@/components/StatCard";
import Badge from "@/components/Badge";
import { CommandLauncher } from "@/components/AskVIP";
import { useRealtimeEvents } from "@/components/useRealtimeEvents";

export default function Dashboard() {
  const [stats, setStats] = useState({
    agents: 0, activeRuns: 0, failedRuns: 0, pendingJudgement: 0,
    latestDaily: "", latestWeekly: "", telegramStatus: "active", aiGlassStatus: "planned",
    eventBus: "in-memory", webhooksReachable: 0, webhooksTotal: 0,
  });
  const [recentRuns, setRecentRuns] = useState<any[]>([]);
  const [health, setHealth] = useState<any>(null);

  const toKST = (utcStr: string) => {
    if (!utcStr) return "";
    return new Date(utcStr).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
  };

  const load = async () => {
    try {
      const [agents, runs, cases, reports, channels, h, a2aStatus] = await Promise.all([
        api<any[]>("/registry/agents"),
        api<any[]>("/runs?limit=50"),
        api<any[]>("/judgement/cases"),
        api<any[]>("/reports/"),
        api<any[]>("/channels"),
        api<any>("/health"),
        api<any>("/a2a/status").catch(() => null),
      ]);

      const pending = cases.filter((c: any) => c.decision === "human_review_required" || c.decision === "conditional_approve");
      const daily = reports.find((r: any) => r.report_type === "daily_summary");
      const weekly = reports.find((r: any) => r.report_type === "weekly_summary");
      const telegram = channels.find((c: any) => c.type === "telegram");
      const glass = channels.find((c: any) => c.type === "ai_glass");

      const webhooks = a2aStatus?.agent_webhooks || {};
      setStats({
        agents: agents.length,
        activeRuns: runs.filter((r: any) => ["pending", "dispatched", "running"].includes(r.status)).length,
        failedRuns: runs.filter((r: any) => r.status === "failed").length,
        pendingJudgement: pending.length,
        latestDaily: daily?.executive_summary?.slice(0, 120) || "No daily report yet",
        latestWeekly: weekly?.executive_summary?.slice(0, 120) || "No weekly report yet",
        telegramStatus: telegram?.status || "unknown",
        aiGlassStatus: glass?.status || "unknown",
        eventBus: a2aStatus?.event_bus || "unknown",
        webhooksReachable: webhooks.reachable || 0,
        webhooksTotal: webhooks.total || 0,
      });
      setRecentRuns(runs.slice(0, 8));
      setHealth(h);
    } catch {}
  };

  useEffect(() => { load(); const i = setInterval(load, 15000); return () => clearInterval(i); }, []);

  // Real-time: refresh dashboard when task/report events arrive
  useRealtimeEvents((event) => {
    if (event.type.includes("task") || event.type.includes("report") || event.type.includes("a2a")) {
      load();
    }
  });

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-[28px] font-semibold tracking-tight">Command Center</h1>
          <p className="text-[14px] text-[var(--text-muted)]">VIP Agent Platform Overview</p>
        </div>
        {health && (
          <div className="flex gap-2">
            <Badge text={health.status === "ok" ? "active" : "error"} />
            <span className="text-xs text-gray-500">DB: {health.database}</span>
          </div>
        )}
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatCard label="Total Agents" value={stats.agents} color="blue" />
        <StatCard label="Active Runs" value={stats.activeRuns} color="green" />
        <StatCard label="Failed Runs" value={stats.failedRuns} color="red" />
        <StatCard label="Pending Judgement" value={stats.pendingJudgement} color="yellow" />
      </div>

      {/* Two column layout */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        {/* Latest Reports */}
        <div className="border border-[var(--border-default)] rounded-xl p-4 bg-[var(--bg-card)]">
          <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-3">Latest Daily Report</h2>
          <p className="text-xs text-[var(--text-secondary)] leading-relaxed">{stats.latestDaily}...</p>
        </div>
        <div className="border border-[var(--border-default)] rounded-xl p-4 bg-[var(--bg-card)]">
          <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-3">Latest Weekly Report</h2>
          <p className="text-xs text-[var(--text-secondary)] leading-relaxed">{stats.latestWeekly}...</p>
        </div>
      </div>

      {/* Channel & A2A Status */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
          <p className="text-[10px] text-gray-500 mb-1">Telegram</p>
          <Badge text={stats.telegramStatus} />
        </div>
        <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
          <p className="text-[10px] text-gray-500 mb-1">Event Bus</p>
          <Badge text={stats.eventBus === "redis" ? "active" : "in-memory"} />
          <span className="text-[9px] text-[var(--text-muted)] ml-1">{stats.eventBus}</span>
        </div>
        <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
          <p className="text-[10px] text-gray-500 mb-1">A2A Webhooks</p>
          <span className={`text-[13px] font-semibold ${stats.webhooksReachable === stats.webhooksTotal ? "text-green-500" : "text-amber-500"}`}>
            {stats.webhooksReachable}/{stats.webhooksTotal}
          </span>
          <span className="text-[9px] text-[var(--text-muted)] ml-1">reachable</span>
        </div>
        <div className="border border-[var(--border-default)] rounded-xl p-3 bg-[var(--bg-card)]">
          <p className="text-[10px] text-gray-500 mb-1">Web Channel</p>
          <Badge text="active" />
        </div>
      </div>

      {/* Command Launcher */}
      <div className="mb-8">
        <CommandLauncher />
      </div>

      {/* Recent Runs */}
      <div className="border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)]">
        <div className="px-4 py-3 border-b border-gray-800">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Recent Task Runs</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 border-b border-gray-800/50">
                <th className="text-left px-4 py-2">Type</th>
                <th className="text-left px-4 py-2">Agent</th>
                <th className="text-left px-4 py-2">Status</th>
                <th className="text-left px-4 py-2">Trace</th>
                <th className="text-left px-4 py-2">Time</th>
              </tr>
            </thead>
            <tbody>
              {recentRuns.map((r: any) => (
                <tr key={r.id} className="border-b border-[var(--border-default)] hover:bg-[var(--bg-elevated)]">
                  <td className="px-4 py-2">{r.task_type}</td>
                  <td className="px-4 py-2 text-[var(--brand-blue)]">{r.agent_name}</td>
                  <td className="px-4 py-2"><Badge text={r.status} /></td>
                  <td className="px-4 py-2 text-gray-500 font-mono">{r.trace_id}</td>
                  <td className="px-4 py-2 text-gray-500">{r.started_at ? toKST(r.started_at) : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
