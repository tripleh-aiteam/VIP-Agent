"use client";

/**
 * VoiceDashboard — the single top-level component for the Calling Agent.
 *
 * Mount this with an AgentConfig.voice block and the agent gains the
 * complete /calls UI: daily-report card, Live/History/Outbound tabs,
 * Batch vs Single sub-toggle, call-detail drawer.
 *
 * Two operating modes:
 *
 *   1. mock=true  (default while the backend is mid-build)
 *      The dashboard subscribes to mock-data.ts internally — every
 *      visual state is exercised. Useful during UI iteration.
 *
 *   2. mock=false (production)
 *      The host owns the data — pass live `activeCall`, `history`,
 *      `dailyReport` props from your WebSocket / REST client.
 *      Step 16 will flip VIP's wiring from mock=true to mock=false
 *      once `voice-client.ts` is in.
 *
 * Framework-agnostic: this component does NOT depend on Next.js or any
 * router. The host extracts ?tab= from the URL if it wants and passes
 * the result via `initialTab`.
 */

import { useEffect, useState } from "react";
import type { Lang, VoiceConfig } from "../types";
import type {
  BatchCampaign,
  CallEvent,
  OutboundCallDraft,
} from "./types";
import {
  getMockActiveCall,
  MOCK_HAS_ACTIVE_CALL,
  mockCallHistory,
  mockDailyReport,
} from "./mock-data";
import type { DailyReportSummary } from "./mock-data";
import { TabBar } from "./TabBar";
import { LiveCallCard } from "./LiveCallCard";
import { CallsHistoryList } from "./CallsHistoryList";
import { CallDetailDrawer } from "./CallDetailDrawer";
import { OutboundCallForm } from "./OutboundCallForm";
import { BatchCallCampaign } from "./BatchCallCampaign";

type CallsTab = "live" | "history" | "outbound";
type OutboundMode = "batch" | "single";

interface Props {
  /** Per-agent voice settings from AgentConfig.voice. Required. */
  config: VoiceConfig;
  /** Stable agent identifier (matches AgentConfig.agentId). Required. */
  agentId: string;
  /** Display name shown in the header. Defaults to the agentId. */
  agentLabel?: string;
  /**
   * Mock mode — when true (default), the dashboard subscribes to
   * mock-data.ts internally. Flip to false once the host wires a real
   * voice-client.ts subscription.
   */
  mock?: boolean;
  /**
   * Live data overrides. When provided, these win over mock. Pass null
   * to explicitly indicate "no active call right now" / "no history yet".
   */
  activeCall?: CallEvent | null;
  history?: CallEvent[];
  dailyReport?: DailyReportSummary | null;
  /** Initial tab. Default: "live". */
  initialTab?: CallsTab;
  /** UI label resolution. Defaults to config.defaultLanguage. */
  language?: Lang;

  /* ----- live-call action callbacks (host wires to API) ------------ */
  onListenIn?: (call: CallEvent) => void;
  onMarkUrgent?: (call: CallEvent) => void;
  onTakeOver?: (call: CallEvent) => void;

  /* ----- call-detail action callbacks ------------------------------ */
  onCallBack?: (call: CallEvent) => void;
  onAddToKnowledge?: (call: CallEvent) => void;
  onReviewFeedback?: (call: CallEvent, verdict: "correct" | "improve") => void;

  /* ----- outbound callbacks ---------------------------------------- */
  onPlaceOutboundCall?: (draft: OutboundCallDraft) => Promise<void> | void;
  onBatchCampaignToggle?: (campaign: BatchCampaign) => void;
  onBatchCampaignStop?: (campaign: BatchCampaign) => void;
}

export function VoiceDashboard({
  config,
  agentId,
  agentLabel,
  mock = true,
  activeCall: liveActiveCall,
  history: liveHistory,
  dailyReport: liveDailyReport,
  initialTab = "live",
  language,
  onListenIn,
  onMarkUrgent,
  onTakeOver,
  onCallBack,
  onAddToKnowledge,
  onReviewFeedback,
  onPlaceOutboundCall,
  onBatchCampaignToggle,
  onBatchCampaignStop,
}: Props) {
  const lang = language ?? config.defaultLanguage ?? "ko";

  const [tab, setTab] = useState<CallsTab>(initialTab);
  const [outboundMode, setOutboundMode] = useState<OutboundMode>("batch");
  const [selectedCall, setSelectedCall] = useState<CallEvent | null>(null);

  /* ---- active call: mock-mode polling vs live prop ---------------- */
  const [mockActiveCall, setMockActiveCall] = useState<CallEvent | null>(null);
  useEffect(() => {
    if (!mock || liveActiveCall !== undefined) return;
    if (!MOCK_HAS_ACTIVE_CALL) {
      setMockActiveCall(null);
      return;
    }
    setMockActiveCall(getMockActiveCall());
    const id = setInterval(() => setMockActiveCall(getMockActiveCall()), 2000);
    return () => clearInterval(id);
  }, [mock, liveActiveCall]);

  const activeCall: CallEvent | null =
    liveActiveCall !== undefined ? liveActiveCall : mockActiveCall;
  const history: CallEvent[] =
    liveHistory !== undefined ? liveHistory : mock ? mockCallHistory : [];
  const dailyReport: DailyReportSummary | null =
    liveDailyReport !== undefined ? liveDailyReport : mock ? mockDailyReport : null;

  const escalatedCount = history.filter((c) => c.status === "escalated").length;
  const displayName = agentLabel ?? agentId;

  return (
    <div className="space-y-5">
      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            📞 {displayName} Calling Agent
          </h1>
          <p className="text-[12px] text-gray-500 mt-1">
            AI receptionist — handles incoming calls during off-hours and places outbound reminders.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusPill online={true} label={`Active number: ${config.phoneNumber}`} />
        </div>
      </div>

      {/* Daily report card */}
      {dailyReport && <DailyReport report={dailyReport} />}

      {/* Tab bar */}
      <TabBar
        value={tab}
        onChange={(v) => setTab(v)}
        options={[
          {
            value: "live",
            label: "Live",
            badge: activeCall ? "●" : undefined,
            badgeColor: activeCall ? "red" : undefined,
          },
          {
            value: "history",
            label: "History",
            badge: String(history.length),
            badgeColor: "gray",
          },
          { value: "outbound", label: "Outbound" },
        ]}
      />

      {/* Tab content */}
      <div>
        {tab === "live" && (
          <LiveCallCard
            call={activeCall}
            onListenIn={onListenIn}
            onMarkUrgent={onMarkUrgent}
            onTakeOver={onTakeOver}
          />
        )}

        {tab === "history" && (
          <CallsHistoryList calls={history} onCallClick={setSelectedCall} />
        )}

        {tab === "outbound" && (
          <div className="space-y-4">
            <OutboundModeToggle value={outboundMode} onChange={setOutboundMode} />
            {outboundMode === "batch" ? (
              <BatchCallCampaign
                reasons={config.outboundReasons}
                language={lang}
                onToggleStatus={onBatchCampaignToggle}
                onStop={onBatchCampaignStop}
              />
            ) : (
              <div className="max-w-2xl">
                <OutboundCallForm
                  reasons={config.outboundReasons}
                  language={lang}
                  onSubmit={onPlaceOutboundCall}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Side drawer for call detail */}
      <CallDetailDrawer
        call={selectedCall}
        onClose={() => setSelectedCall(null)}
        onCallBack={onCallBack}
        onAddToKnowledge={onAddToKnowledge}
        onReviewFeedback={onReviewFeedback}
      />

      {/* Footnote */}
      <p className="text-[10px] text-gray-400 pt-2 border-t border-gray-100">
        {mock && liveActiveCall === undefined ? (
          <>
            Showing mock data. Backend wire-up:{" "}
            <span className="font-medium text-gray-500">{config.provider}</span> webhook + voice tables
            for <span className="font-mono">{agentId}</span> coming in next phase.
          </>
        ) : (
          <>
            Live data — <span className="font-medium text-gray-500">{config.provider}</span> webhook
            connected for <span className="font-mono">{agentId}</span>.
          </>
        )}
        {escalatedCount > 0 && (
          <>
            {" "}Escalated calls today:{" "}
            <span className="font-semibold">{escalatedCount}</span>.
          </>
        )}
      </p>
    </div>
  );
}

/* ----------------------- visual atoms ------------------------------ */

function StatusPill({ online, label }: { online: boolean; label: string }) {
  return (
    <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-gray-200 bg-white">
      <span
        className={`w-2 h-2 rounded-full ${online ? "bg-green-500 animate-pulse" : "bg-gray-400"}`}
      />
      <span className="text-[11px] text-gray-700">{label}</span>
    </div>
  );
}

function OutboundModeToggle({
  value,
  onChange,
}: {
  value: OutboundMode;
  onChange: (v: OutboundMode) => void;
}) {
  const opts: { v: OutboundMode; label: string; hint: string; icon: string }[] = [
    { v: "batch", label: "Batch campaign", hint: "Agent dials a list one-by-one", icon: "📋" },
    { v: "single", label: "Single call", hint: "Operator places one call", icon: "📞" },
  ];
  return (
    <div className="inline-flex rounded-xl bg-gray-100 p-1">
      {opts.map((o) => {
        const active = o.v === value;
        return (
          <button
            key={o.v}
            onClick={() => onChange(o.v)}
            className={`px-3.5 py-1.5 rounded-lg text-[12px] font-medium transition-all flex items-center gap-1.5 ${
              active
                ? "bg-white text-gray-900 shadow-sm"
                : "text-gray-600 hover:text-gray-900"
            }`}
            title={o.hint}
          >
            <span>{o.icon}</span>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function DailyReport({ report }: { report: DailyReportSummary }) {
  return (
    <div className="rounded-2xl border border-blue-200 bg-gradient-to-br from-blue-50 to-purple-50 p-4">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-[13px] font-semibold text-gray-900">
            🌅 Last night&apos;s call report
          </h3>
          <p className="text-[11px] text-gray-600 mt-0.5">
            What happened while you were away
          </p>
        </div>
        <button className="text-[11px] text-blue-600 hover:text-blue-700 font-medium">
          Send to email →
        </button>
      </div>
      <div className="grid grid-cols-4 gap-3">
        <Stat label="Total calls" value={report.totalCalls} />
        <Stat label="Resolved" value={report.resolved} color="green" />
        <Stat label="Escalated" value={report.escalated} color="red" />
        <Stat label="Missed" value={report.missed} color="gray" />
      </div>
      <div className="mt-3 pt-3 border-t border-blue-200/50">
        <div className="text-[11px] text-gray-600 mb-1.5 font-medium">Top topics</div>
        <div className="flex flex-wrap gap-1.5">
          {report.topTopics.map((t) => (
            <span
              key={t.topic}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-white border border-blue-200 text-[11px] text-gray-700"
            >
              {t.topic} <span className="text-gray-400">·</span>{" "}
              <span className="font-medium text-blue-700">{t.count}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color?: "green" | "red" | "gray";
}) {
  const colors = {
    green: "text-green-700",
    red: "text-red-700",
    gray: "text-gray-700",
  };
  const c = color ? colors[color] : "text-blue-700";
  return (
    <div className="bg-white rounded-lg p-2.5 border border-blue-100">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider">{label}</div>
      <div className={`text-xl font-semibold mt-0.5 ${c}`}>{value}</div>
    </div>
  );
}
