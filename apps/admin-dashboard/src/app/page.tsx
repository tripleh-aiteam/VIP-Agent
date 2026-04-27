"use client";

import { useEffect, useState } from "react";
import { api, API } from "@/components/api";
import Badge from "@/components/Badge";
import { useRealtimeEvents } from "@/components/useRealtimeEvents";
import { apiPost } from "@/components/api";
import dynamic from "next/dynamic";
import Link from "next/link";

const AgentHealthPanel = dynamic(() => import("@/components/AgentHealthPanel"), { ssr: false });
const SummaryDrilldown = dynamic(() => import("@/components/SummaryDrilldown"), { ssr: false });
const RecentTaskRuns = dynamic(() => import("@/components/RecentTaskRuns"), { ssr: false });
const InfrastructureDrilldown = dynamic(() => import("@/components/InfrastructureDrilldown"), { ssr: false });
// ReportsDashboardPanel available at /reports page
const QuickCommandResult = dynamic(() => import("@/components/QuickCommandResult"), { ssr: false });
const ReportCard = dynamic(() => import("@/components/ReportCard"), { ssr: false });

export default function Dashboard() {
  const [stats, setStats] = useState({
    agents: 0, activeRuns: 0, failedRuns: 0, pendingJudgement: 0,
    latestDaily: "", latestWeekly: "", telegramStatus: "active", aiGlassStatus: "planned",
    eventBus: "in-memory", webhooksReachable: 0, webhooksTotal: 0,
  });
  const [recentRuns, setRecentRuns] = useState<any[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [quickResult, setQuickResult] = useState<{ title: string; text: string; loading: boolean } | null>(null);
  const [healthExpanded, setHealthExpanded] = useState(false);
  const [drilldown, setDrilldown] = useState<"agents" | "active" | "failed" | "judgement" | null>(null);
  const [infraPanel, setInfraPanel] = useState<"telegram" | "eventbus" | "webhooks" | "web" | null>(null);
  const [handoff, setHandoff] = useState<{ stats: { twins_worked: number; tasks_completed: number; items_need_review: number; unreviewed_handoffs: number }; handoffs: any[] } | null>(null);
  const [bossBriefing, setBossBriefing] = useState<any>(null);
  const [absences, setAbsences] = useState<any>(null);
  const [showBroadcast, setShowBroadcast] = useState(false);
  const [broadcastMsg, setBroadcastMsg] = useState("");
  const [broadcastPriority, setBroadcastPriority] = useState("normal");
  const [broadcasting, setBroadcasting] = useState(false);

  const runQuickCommand = async (label: string, prompt: string) => {
    setQuickResult({ title: label, text: "", loading: true });
    try {
      // Create a temp session and send the command
      const session = await apiPost<any>("/chat/sessions", { user_id: "dashboard", channel: "web" });
      const result = await apiPost<any>(`/chat/sessions/${session.id}/messages`, { content: prompt });
      const text = result?.assistant_message?.content?.text || "No response";
      setQuickResult({ title: label, text, loading: false });
    } catch {
      setQuickResult({ title: label, text: "Failed to fetch. Please try again.", loading: false });
    }
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
      setRecentRuns(runs.slice(0, 50));
      setHealth(h);
    } catch {}

    // Fetch twin handoffs (separate try to not block main dashboard)
    try {
      const res = await fetch(`${API}/twins/handoff/today`);
      if (res.ok) setHandoff(await res.json());
    } catch {}

    // Fetch boss daily briefing
    try {
      const res = await fetch(`${API}/twins/reports/boss-briefing`);
      if (res.ok) setBossBriefing(await res.json());
    } catch {}

    // Fetch worker absences
    try {
      const res = await fetch(`${API}/twins/reports/absences?hours=24`);
      if (res.ok) setAbsences(await res.json());
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
        <div className="flex items-center gap-2">
          <button onClick={() => setShowBroadcast(true)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-[12px] font-medium hover:bg-blue-700 flex items-center gap-1.5">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M11 5.882V19.24a1.76 1.76 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h1.832c4.1 0 7.625-1.234 9.168-3v14c-1.543-1.766-5.067-3-9.168-3H7a3.988 3.988 0 01-1.564-.317z" /></svg>
            Broadcast
          </button>
          {health && (
            <div className="flex gap-2">
              <Badge text={health.status === "ok" ? "active" : "error"} />
            </div>
          )}
        </div>
      </div>

      {/* Broadcast Modal */}
      {showBroadcast && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" onClick={() => setShowBroadcast(false)}>
          <div className="absolute inset-0 bg-black/50" />
          <div className="relative bg-white rounded-2xl border border-gray-200 w-full max-w-md" style={{ boxShadow: "0 20px 60px rgba(0,0,0,0.2)" }} onClick={e => e.stopPropagation()}>
            <div className="p-5 border-b border-gray-200">
              <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">Broadcast to All Workers</h2>
              <p className="text-[12px] text-[var(--text-muted)] mt-1">Send a message to all 10 workers at once</p>
            </div>
            <div className="p-5 space-y-4">
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Priority</label>
                <div className="flex gap-2">
                  <button onClick={() => setBroadcastPriority("normal")}
                    className={`flex-1 py-2 rounded-lg text-[12px] font-medium ${broadcastPriority === "normal" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600"}`}>
                    Normal
                  </button>
                  <button onClick={() => setBroadcastPriority("urgent")}
                    className={`flex-1 py-2 rounded-lg text-[12px] font-medium ${broadcastPriority === "urgent" ? "bg-red-500 text-white" : "bg-gray-100 text-gray-600"}`}>
                    🚨 Urgent
                  </button>
                </div>
              </div>
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Message</label>
                <textarea value={broadcastMsg} onChange={e => setBroadcastMsg(e.target.value)} rows={4}
                  placeholder="e.g. Team meeting at 3 PM today. Please prepare your weekly updates."
                  className="w-full px-4 py-3 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none" />
              </div>
            </div>
            <div className="p-5 border-t border-gray-200 flex gap-3 justify-end">
              <button onClick={() => setShowBroadcast(false)} className="px-4 py-2.5 text-[13px] text-[var(--text-muted)]">Cancel</button>
              <button onClick={async () => {
                if (!broadcastMsg.trim()) return;
                setBroadcasting(true);
                try {
                  const res = await fetch(`${API}/twins/broadcast`, {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ message: broadcastMsg, priority: broadcastPriority }),
                  });
                  const data = await res.json();
                  setShowBroadcast(false); setBroadcastMsg(""); setBroadcastPriority("normal");
                  alert(`Message sent to ${data.twins_notified} workers!`);
                } catch {} finally { setBroadcasting(false); }
              }} disabled={!broadcastMsg.trim() || broadcasting}
                className={`px-5 py-2.5 rounded-lg text-[13px] font-medium disabled:opacity-50 ${broadcastPriority === "urgent" ? "bg-red-500 text-white hover:bg-red-600" : "bg-blue-600 text-white hover:bg-blue-700"}`}>
                {broadcasting ? "Sending..." : `Send to All Workers`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Morning Handoff Banner */}
      {handoff && handoff.stats.twins_worked > 0 && handoff.stats.unreviewed_handoffs > 0 && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-[24px]">🌅</span>
            <div>
              <div className="text-[14px] font-semibold text-amber-900">Morning Handoff</div>
              <div className="text-[12px] text-amber-700">
                {handoff.stats.twins_worked} twins worked overnight — {handoff.stats.tasks_completed} tasks completed, {handoff.stats.items_need_review} items need review
              </div>
            </div>
          </div>
          <Link href="/handoff" className="px-4 py-2 bg-amber-600 text-white rounded-lg text-[12px] font-medium hover:bg-amber-700 transition-colors">
            Review Now →
          </Link>
        </div>
      )}

      {/* Boss Daily Briefing */}
      {bossBriefing && (bossBriefing.summary?.total_completed > 0 || bossBriefing.alerts?.length > 0) && (
        <div className="mb-4 bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-[15px] font-semibold text-[var(--text-primary)] flex items-center gap-2">
              <span>📊</span> Daily Twin Briefing
            </h2>
            <span className="text-[10px] text-[var(--text-muted)]">
              {new Date().toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" })}
            </span>
          </div>

          {/* Summary Stats */}
          <div className="grid grid-cols-4 gap-3 mb-3">
            <div className="bg-blue-50 rounded-lg px-3 py-2 text-center">
              <div className="text-[18px] font-bold text-blue-600">{bossBriefing.summary?.twins_worked || 0}</div>
              <div className="text-[9px] text-[var(--text-muted)]">Twins Worked</div>
            </div>
            <div className="bg-green-50 rounded-lg px-3 py-2 text-center">
              <div className="text-[18px] font-bold text-green-600">{bossBriefing.summary?.total_completed || 0}</div>
              <div className="text-[9px] text-[var(--text-muted)]">Completed</div>
            </div>
            <div className="bg-amber-50 rounded-lg px-3 py-2 text-center">
              <div className="text-[18px] font-bold text-amber-600">{bossBriefing.summary?.total_review || 0}</div>
              <div className="text-[9px] text-[var(--text-muted)]">Need Review</div>
            </div>
            <div className={`rounded-lg px-3 py-2 text-center ${(bossBriefing.summary?.total_failed || 0) > 0 ? "bg-red-50" : "bg-green-50"}`}>
              <div className={`text-[18px] font-bold ${(bossBriefing.summary?.total_failed || 0) > 0 ? "text-red-600" : "text-green-600"}`}>{bossBriefing.summary?.total_failed || 0}</div>
              <div className="text-[9px] text-[var(--text-muted)]">Failed</div>
            </div>
          </div>

          {/* Alerts */}
          {bossBriefing.alerts?.length > 0 && (
            <div className="mb-3">
              {bossBriefing.alerts.map((a: any, i: number) => (
                <div key={i} className={`flex items-center gap-2 text-[11px] mb-1 ${a.type === "failed_tasks" ? "text-red-600" : "text-blue-600"}`}>
                  <span>{a.type === "failed_tasks" ? "⚠️" : "💬"}</span>
                  {a.message}
                </div>
              ))}
            </div>
          )}

          {/* Top twins (compact) */}
          <div className="flex gap-2 flex-wrap">
            {bossBriefing.twins?.filter((t: any) => t.tasks_done > 0).slice(0, 5).map((t: any) => (
              <span key={t.twin_id} className="px-2.5 py-1 bg-[var(--bg-secondary)] rounded-full text-[10px] text-[var(--text-secondary)]">
                {t.name}: {t.tasks_done} done
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Worker Absence Alert */}
      {absences && absences.count > 0 && (
        <div className="mb-4 bg-red-50 rounded-xl border border-red-200 p-4">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[16px]">⚠️</span>
            <span className="text-[13px] font-semibold text-red-800">{absences.count} worker{absences.count > 1 ? "s" : ""} absent (no login for 24h+)</span>
          </div>
          <div className="space-y-1.5">
            {absences.absent_workers.map((w: any) => (
              <div key={w.worker_id} className="flex items-center justify-between text-[12px]">
                <div className="flex items-center gap-2">
                  <span className="text-red-500">•</span>
                  <span className="font-medium text-red-900">{w.worker_name}</span>
                  <span className="text-red-600">— {w.days_absent === 999 ? "never logged in" : `${w.days_absent} day${w.days_absent > 1 ? "s" : ""} absent`}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-red-500">Twin: {w.twin_name} ({w.twin_mode})</span>
                  {w.twin_tasks_done_while_absent > 0 && (
                    <span className="px-2 py-0.5 bg-green-100 text-green-700 rounded text-[9px]">{w.twin_tasks_done_while_absent} tasks done by twin</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stats Grid — clickable for drilldown */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
        {([
          { key: "agents" as const, label: "Total Agents", value: stats.agents, color: "blue" as const },
          { key: "active" as const, label: "Active Runs", value: stats.activeRuns, color: "green" as const },
          { key: "failed" as const, label: "Failed Runs", value: stats.failedRuns, color: "red" as const },
          { key: "judgement" as const, label: "Pending Judgement", value: stats.pendingJudgement, color: "yellow" as const },
        ]).map((card) => {
          const valColor: Record<string, string> = { blue: "text-[var(--brand-blue)]", green: "text-[var(--brand-green)]", red: "text-[var(--error)]", yellow: "text-[var(--warning)]" };
          const isActive = drilldown === card.key;
          return (
            <button key={card.key} onClick={() => setDrilldown(isActive ? null : card.key)}
              className={`text-left rounded-xl border p-4 transition-all cursor-pointer ${
                isActive ? "border-[var(--brand-blue)] ring-1 ring-[var(--brand-blue)] bg-blue-50/50 dark:bg-blue-900/10" : "border-[var(--border-default)] bg-[var(--bg-card)] hover:border-[var(--brand-blue)]"
              }`} style={{ boxShadow: "var(--shadow-sm)" }}>
              <p className="text-[12px] text-[var(--text-muted)] mb-1 font-medium">{card.label}</p>
              <p className={`text-[24px] font-semibold tracking-tight ${valColor[card.color]}`}>{card.value}</p>
              <p className="text-[9px] text-[var(--text-muted)] mt-1">{isActive ? "Click to close" : "Click to explore"}</p>
            </button>
          );
        })}
      </div>

      {/* Drilldown Panel */}
      {drilldown && (
        <div className="mb-8 border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-[14px] font-semibold text-[var(--text-primary)] capitalize">{drilldown === "judgement" ? "Pending Judgement" : drilldown === "agents" ? "Total Agents" : drilldown === "active" ? "Active Runs" : "Failed Runs"} — Analytics</h3>
            <button onClick={() => setDrilldown(null)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] text-xs">Close</button>
          </div>
          <SummaryDrilldown panel={drilldown} />
        </div>
      )}

      {/* Agent Health — expandable */}
      <div className="mb-8">
        <button onClick={() => setHealthExpanded(!healthExpanded)}
          className="w-full flex items-center justify-between px-4 py-3 rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] hover:border-[var(--brand-blue)] transition-colors"
          style={{ boxShadow: "var(--shadow-sm)" }}>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-green-50 flex items-center justify-center">
              <svg className="w-4 h-4 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <div className="text-left">
              <h3 className="text-[14px] font-semibold text-[var(--text-primary)]">Agent Health</h3>
              <p className="text-[11px] text-[var(--text-muted)]">
                {stats.agents} agents | {stats.webhooksReachable}/{stats.webhooksTotal} webhooks | {stats.eventBus}
              </p>
            </div>
          </div>
          <svg className={`w-4 h-4 text-[var(--text-muted)] transition-transform duration-200 ${healthExpanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {healthExpanded && (
          <div className="mt-3 border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
            <AgentHealthPanel />
          </div>
        )}
      </div>

      {/* Report Cards — with sparklines and expandable analytics */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-8">
        <ReportCard type="daily" summary={stats.latestDaily} />
        <ReportCard type="weekly" summary={stats.latestWeekly} />
      </div>

      {/* Infrastructure — clickable for drilldown */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
        {([
          { key: "telegram" as const, label: "Telegram", status: stats.telegramStatus, detail: "" },
          { key: "eventbus" as const, label: "Event Bus", status: stats.eventBus === "redis" ? "active" : "in-memory", detail: stats.eventBus },
          { key: "webhooks" as const, label: "A2A Webhooks", status: stats.webhooksReachable === stats.webhooksTotal ? "active" : "warning", detail: `${stats.webhooksReachable}/${stats.webhooksTotal} reachable` },
          { key: "web" as const, label: "Web Channel", status: "active", detail: "" },
        ]).map(card => {
          const isActive = infraPanel === card.key;
          return (
            <button key={card.key} onClick={() => setInfraPanel(isActive ? null : card.key)}
              className={`text-left rounded-xl border p-3 transition-all cursor-pointer ${
                isActive ? "border-[var(--brand-blue)] ring-1 ring-[var(--brand-blue)] bg-blue-50/50 dark:bg-blue-900/10" : "border-[var(--border-default)] bg-[var(--bg-card)] hover:border-[var(--brand-blue)]"
              }`}>
              <p className="text-[10px] text-gray-500 mb-1">{card.label}</p>
              <Badge text={card.status} />
              {card.detail && <span className="text-[9px] text-[var(--text-muted)] ml-1">{card.detail}</span>}
              <p className="text-[8px] text-[var(--text-muted)] mt-1">{isActive ? "Click to close" : "Click to explore"}</p>
            </button>
          );
        })}
      </div>

      {/* Infrastructure Drilldown */}
      {infraPanel && (
        <div className="mb-8 border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-[14px] font-semibold text-[var(--text-primary)]">
              {infraPanel === "telegram" ? "Telegram" : infraPanel === "eventbus" ? "Event Bus" : infraPanel === "webhooks" ? "A2A Webhooks" : "Web Channel"} — Analytics
            </h3>
            <button onClick={() => setInfraPanel(null)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] text-xs">Close</button>
          </div>
          <InfrastructureDrilldown panel={infraPanel} />
        </div>
      )}

      {/* Quick Commands — results show inline */}
      <div className="mb-8">
        <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3">Quick Commands</h2>
        <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
          {[
            { label: "System Status", prompt: "status", icon: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" },
            { label: "Latest Report", prompt: "show daily report", icon: "M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" },
            { label: "Agent Health", prompt: "show agents", icon: "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" },
            { label: "Approvals", prompt: "pending approvals", icon: "M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1m-3 1l-3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 16V5" },
            { label: "Check Risk", prompt: "high risk cases", icon: "M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" },
            { label: "Run All", prompt: "run full executive summary", icon: "M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" },
          ].map((cmd) => (
            <button key={cmd.label} onClick={() => runQuickCommand(cmd.label, cmd.prompt)}
              disabled={quickResult?.loading}
              className="flex flex-col items-center gap-1.5 p-3 rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] hover:border-[var(--brand-blue)] hover:bg-[var(--bg-elevated)] transition-colors group disabled:opacity-50"
              style={{ boxShadow: "var(--shadow-sm)" }}>
              <svg className="w-5 h-5 text-[var(--text-muted)] group-hover:text-[var(--brand-blue)] transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d={cmd.icon} />
              </svg>
              <span className="text-[11px] text-[var(--text-secondary)] group-hover:text-[var(--brand-blue)] font-medium">{cmd.label}</span>
            </button>
          ))}
        </div>

        {/* Inline result with charts */}
        {quickResult && (
          <QuickCommandResult command={quickResult.title === "System Status" ? "status" : quickResult.title === "Latest Report" ? "show daily report" : quickResult.title === "Agent Health" ? "show agents" : quickResult.title === "Approvals" ? "pending approvals" : quickResult.title === "Check Risk" ? "high risk cases" : "run full executive summary"} onClose={() => setQuickResult(null)} />
        )}
      </div>

      {/* Recent Runs — Table + Graph views */}
      <RecentTaskRuns runs={recentRuns} />
    </div>
  );
}
