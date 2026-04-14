"use client";

import { useEffect, useState } from "react";
import { api } from "@/components/api";
import StatCard from "@/components/StatCard";
import Badge from "@/components/Badge";
import { CommandLauncher } from "@/components/AskVIP";

export default function Dashboard() {
  const [stats, setStats] = useState({
    agents: 0, activeRuns: 0, failedRuns: 0, pendingJudgement: 0,
    latestDaily: "", latestWeekly: "", telegramStatus: "active", aiGlassStatus: "planned",
  });
  const [recentRuns, setRecentRuns] = useState<any[]>([]);
  const [health, setHealth] = useState<any>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [agents, runs, cases, reports, channels, h] = await Promise.all([
          api<any[]>("/registry/agents"),
          api<any[]>("/runs?limit=50"),
          api<any[]>("/judgement/cases"),
          api<any[]>("/reports/"),
          api<any[]>("/channels"),
          api<any>("/health"),
        ]);

        const pending = cases.filter((c: any) => c.decision === "human_review_required" || c.decision === "conditional_approve");
        const daily = reports.find((r: any) => r.report_type === "daily_summary");
        const weekly = reports.find((r: any) => r.report_type === "weekly_summary");
        const telegram = channels.find((c: any) => c.type === "telegram");
        const glass = channels.find((c: any) => c.type === "ai_glass");

        setStats({
          agents: agents.length,
          activeRuns: runs.filter((r: any) => ["pending", "dispatched", "running"].includes(r.status)).length,
          failedRuns: runs.filter((r: any) => r.status === "failed").length,
          pendingJudgement: pending.length,
          latestDaily: daily?.executive_summary?.slice(0, 120) || "No daily report yet",
          latestWeekly: weekly?.executive_summary?.slice(0, 120) || "No weekly report yet",
          telegramStatus: telegram?.status || "unknown",
          aiGlassStatus: glass?.status || "unknown",
        });
        setRecentRuns(runs.slice(0, 8));
        setHealth(h);
      } catch {}
    };
    load();
    const i = setInterval(load, 10000);
    return () => clearInterval(i);
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Command Center</h1>
          <p className="text-sm text-gray-500">VIP Agent Platform Overview</p>
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
        <div className="border border-gray-200 dark:border-[#2a3142] rounded-xl p-4 bg-white dark:bg-[#1a1f2e]">
          <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">Latest Daily Report</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">{stats.latestDaily}...</p>
        </div>
        <div className="border border-gray-200 dark:border-[#2a3142] rounded-xl p-4 bg-white dark:bg-[#1a1f2e]">
          <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">Latest Weekly Report</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">{stats.latestWeekly}...</p>
        </div>
      </div>

      {/* Channel Status */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <div className="border border-gray-200 dark:border-[#2a3142] rounded-xl p-3 bg-white dark:bg-[#1a1f2e]">
          <p className="text-[10px] text-gray-500 mb-1">Telegram</p>
          <Badge text={stats.telegramStatus} />
        </div>
        <div className="border border-gray-200 dark:border-[#2a3142] rounded-xl p-3 bg-white dark:bg-[#1a1f2e]">
          <p className="text-[10px] text-gray-500 mb-1">AI Glasses</p>
          <Badge text={stats.aiGlassStatus} />
        </div>
        <div className="border border-gray-200 dark:border-[#2a3142] rounded-xl p-3 bg-white dark:bg-[#1a1f2e]">
          <p className="text-[10px] text-gray-500 mb-1">Web Channel</p>
          <Badge text="active" />
        </div>
        <div className="border border-gray-200 dark:border-[#2a3142] rounded-xl p-3 bg-white dark:bg-[#1a1f2e]">
          <p className="text-[10px] text-gray-500 mb-1">Event Bus</p>
          <Badge text="active" />
        </div>
      </div>

      {/* Command Launcher */}
      <div className="mb-8">
        <CommandLauncher />
      </div>

      {/* Recent Runs */}
      <div className="border border-gray-200 dark:border-[#2a3142] rounded-xl bg-white dark:bg-[#1a1f2e]">
        <div className="px-4 py-3 border-b border-gray-800">
          <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">Recent Task Runs</h2>
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
                <tr key={r.id} className="border-b border-gray-100 dark:border-gray-800/30 hover:bg-gray-50 dark:hover:bg-gray-800/20">
                  <td className="px-4 py-2">{r.task_type}</td>
                  <td className="px-4 py-2 text-blue-400">{r.agent_name}</td>
                  <td className="px-4 py-2"><Badge text={r.status} /></td>
                  <td className="px-4 py-2 text-gray-500 font-mono">{r.trace_id}</td>
                  <td className="px-4 py-2 text-gray-500">{r.started_at ? new Date(r.started_at).toLocaleTimeString() : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
