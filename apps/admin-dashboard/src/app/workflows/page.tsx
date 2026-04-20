"use client";

import { useEffect, useState } from "react";
import { api, apiPost, apiPatch } from "@/components/api";
import Badge from "@/components/Badge";

export default function WorkflowsPage() {
  const [schedules, setSchedules] = useState<any[]>([]);
  const [recentRuns, setRecentRuns] = useState<any[]>([]);

  const load = () => {
    api<any[]>("/schedules/").then(setSchedules).catch(() => {});
    api<any[]>("/runs?limit=10").then(setRecentRuns).catch(() => {});
  };

  useEffect(() => { load(); const i = setInterval(load, 5000); return () => clearInterval(i); }, []);

  const toggle = async (id: string, enabled: boolean) => {
    await apiPatch(`/schedules/${id}`, { enabled });
    load();
  };

  const runNow = async (id: string) => {
    await apiPost(`/schedules/${id}/run-now`);
    load();
  };

  const groups = [
    { key: "asset", label: "Asset Agent", color: "green", filter: "asset_summary" },
    { key: "stock", label: "Stock Agent", color: "blue", filter: "stock_analysis" },
    { key: "realty", label: "Real Estate Agent", color: "purple", filter: "realty_listing_fetch" },
    { key: "summary", label: "Weekly / Monthly", color: "yellow", filter: "__summary__" },
  ];

  const timingRank: Record<string, number> = { morning: 1, evening: 2, daily: 3, hourly: 4, weekly: 5, monthly: 6 };
  const getTiming = (n: string) => { for (const k of Object.keys(timingRank)) if (n.includes(k)) return timingRank[k]; return 99; };
  const getTimingLabel = (n: string) => {
    if (n.includes("morning")) return "Morning";
    if (n.includes("evening")) return "Evening";
    if (n.includes("daily")) return "Daily";
    if (n.includes("hourly")) return "Hourly";
    if (n.includes("weekly")) return "Weekly";
    if (n.includes("monthly")) return "Monthly";
    return "Custom";
  };

  return (
    <div>
      <h1 className="text-[28px] font-semibold tracking-tight mb-1">Workflows</h1>
      <p className="text-[14px] text-[var(--text-muted)] mb-6">Scheduled tasks and automation</p>

      {/* Auto-Report Schedule Info */}
      <div className="mb-6 p-4 rounded-xl border border-blue-200 bg-blue-50 dark:border-blue-800/30 dark:bg-blue-900/10">
        <h3 className="text-[13px] font-semibold text-blue-700 dark:text-blue-400 mb-2">Automatic Reports</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[12px]">
          <div className="flex items-center gap-2">
            <span className="text-blue-500">Daily</span>
            <span className="text-[var(--text-secondary)]">8:00 AM KST — 3 agent reports + combined summary → Telegram + Dashboard</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-blue-500">Weekly</span>
            <span className="text-[var(--text-secondary)]">Friday 18:30 KST — weekly summary → Telegram + Dashboard</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-blue-500">Health</span>
            <span className="text-[var(--text-secondary)]">Every 5 min — ping all agents, update reliability scores</span>
          </div>
        </div>
      </div>

      {/* Schedule Accordions */}
      <div className="space-y-3 mb-8">
        {groups.map((g) => {
          const items = (g.filter === "__summary__"
            ? schedules.filter((s) => s.name.includes("weekly") || s.name.includes("monthly"))
            : schedules.filter((s) => s.task_type === g.filter)
          ).sort((a, b) => getTiming(a.name) - getTiming(b.name));
          const active = items.filter((s) => s.enabled).length;

          return (
            <details key={g.key} className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] group">
              <summary className="px-5 py-3.5 flex items-center justify-between cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors select-none list-none">
                <div className="flex items-center gap-3">
                  <div className={`w-2.5 h-2.5 rounded-full bg-${g.color}-500`} />
                  <span className="font-semibold text-sm">{g.label}</span>
                  {g.filter !== "__summary__" && <Badge text={g.filter} />}
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-[10px] text-[var(--text-muted)]">{active}/{items.length} active</span>
                  <svg className="w-4 h-4 text-[var(--text-muted)] transition-transform group-open:rotate-180" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
                </div>
              </summary>
              <div className="border-t border-[var(--border-default)] divide-y divide-gray-800/50">
                {items.map((s) => (
                  <div key={s.id} className="px-5 py-3 flex items-center justify-between hover:bg-[var(--bg-hover)]">
                    <div className="flex items-center gap-4 flex-1 min-w-0">
                      <span className={`text-xs font-medium w-16 text-${g.color}-400`}>{getTimingLabel(s.name)}</span>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm truncate">{s.name}</p>
                        <p className="text-[10px] text-[var(--text-muted)] font-mono">{s.cron_expr}</p>
                      </div>
                      <Badge text={s.enabled ? "enabled" : "disabled"} />
                      <span className="text-[10px] text-[var(--text-muted)] w-40 text-right hidden md:block">
                        {s.next_fire_time ? new Date(s.next_fire_time).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "—"}
                      </span>
                    </div>
                    <div className="flex gap-1 ml-3">
                      <button onClick={() => runNow(s.id)} className="px-3 py-1.5 text-[10px] rounded bg-[var(--text-primary)] hover:bg-[var(--text-secondary)] text-white font-medium">Run Now</button>
                      <button onClick={() => toggle(s.id, !s.enabled)} className={`px-3 py-1.5 text-[10px] rounded text-[var(--text-primary)] font-medium ${s.enabled ? "bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)]" : "bg-[var(--text-primary)] hover:bg-[var(--text-secondary)]"}`}>
                        {s.enabled ? "Disable" : "Enable"}
                      </button>
                    </div>
                  </div>
                ))}
                {items.length === 0 && <div className="px-5 py-4 text-center text-[var(--text-muted)] text-xs">No schedules</div>}
              </div>
            </details>
          );
        })}
      </div>

      {/* Recent Workflow History */}
      <div className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)]">
        <div className="px-4 py-3 border-b border-[var(--border-default)]">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Recent Workflow History</h2>
        </div>
        <table className="w-full text-[13px]">
          <thead>
            <tr className="text-[var(--text-muted)] border-b border-[var(--border-default)]/50">
              <th className="text-left px-4 py-2">Task</th>
              <th className="text-left px-4 py-2">Agent</th>
              <th className="text-left px-4 py-2">Status</th>
              <th className="text-left px-4 py-2">Trace</th>
              <th className="text-left px-4 py-2">Started</th>
              <th className="text-left px-4 py-2">Finished</th>
            </tr>
          </thead>
          <tbody>
            {recentRuns.map((r: any) => (
              <tr key={r.id} className="border-b border-[var(--border-default)]/30 hover:bg-[var(--bg-hover)]">
                <td className="px-4 py-2">{r.task_type}</td>
                <td className="px-4 py-2 text-blue-400">{r.agent_name}</td>
                <td className="px-4 py-2"><Badge text={r.status} /></td>
                <td className="px-4 py-2 text-[var(--text-muted)] font-mono">{r.trace_id}</td>
                <td className="px-4 py-2 text-[var(--text-muted)]">{r.started_at ? new Date(r.started_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "-"}</td>
                <td className="px-4 py-2 text-[var(--text-muted)]">{r.finished_at ? new Date(r.finished_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
