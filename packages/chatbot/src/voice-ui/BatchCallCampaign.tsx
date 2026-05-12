"use client";

/**
 * BatchCallCampaign — agent dials a list of recipients one-by-one.
 *
 * Real-world use case: hand the agent a list of unpaid-rent tenants;
 * it works through them respecting pacing + working hours, and records
 * each call's outcome (promised_to_pay / refused / voicemail / ...).
 *
 * Agent-driven: the reason catalog comes from `AgentConfig.voice.outboundReasons`
 * via the `reasons` prop, so the campaign's reason label is resolved per
 * agent (VIP shows "Rent reminder"; Health shows "Medication reminder").
 *
 * Mocked: empty state → "Load sample list" pre-populates the in-progress
 * campaign so all visual states render. Once backend lands, replace
 * `onLoadSample` with `onCreateCampaign` / `onPause` / `onResume`
 * callbacks the host wires to its API.
 */

import { useEffect, useMemo, useState } from "react";
import type { Lang } from "../types";
import type { VoiceOutboundReason } from "../types";
import {
  BATCH_OUTCOME_LABELS,
} from "./types";
import type {
  BatchCampaign,
  BatchOutcome,
  BatchRecipient,
  BatchRecipientStatus,
} from "./types";
import { getMockUnpaidRentCampaign } from "./mock-data";

interface Props {
  /** Reason catalog from `AgentConfig.voice.outboundReasons` — required. */
  reasons: VoiceOutboundReason[];
  /** Language for label resolution. Defaults to "ko". */
  language?: Lang;
  /** Optional: load an initial campaign instead of showing the empty state. */
  initialCampaign?: BatchCampaign | null;
  /**
   * Optional override for the empty-state "Load sample list" button —
   * host can wire it to a real campaign-import flow. If absent, the
   * button loads the demo unpaid-rent campaign from mock-data.ts.
   */
  onLoadSample?: () => BatchCampaign;
  /** Optional: fires when the operator pauses / resumes the campaign. */
  onToggleStatus?: (campaign: BatchCampaign) => void;
  /** Optional: fires when the operator stops the campaign (clears it). */
  onStop?: (campaign: BatchCampaign) => void;
}

export function BatchCallCampaign({
  reasons,
  language = "ko",
  initialCampaign = null,
  onLoadSample,
  onToggleStatus,
  onStop,
}: Props) {
  const [campaign, setCampaign] = useState<BatchCampaign | null>(initialCampaign);

  // Tick every second so the "currently calling" row's elapsed timer updates.
  const [, force] = useState(0);
  useEffect(() => {
    if (campaign?.status !== "running") return;
    const id = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [campaign?.status]);

  if (!campaign) {
    return (
      <EmptyCampaign
        onLoad={() => setCampaign((onLoadSample ?? getMockUnpaidRentCampaign)())}
      />
    );
  }

  return (
    <CampaignView
      campaign={campaign}
      setCampaign={setCampaign}
      reasons={reasons}
      language={language}
      onToggleStatus={onToggleStatus}
      onStop={onStop}
    />
  );
}

/* ---------------------------- empty state --------------------------- */

function EmptyCampaign({ onLoad }: { onLoad: () => void }) {
  return (
    <div className="rounded-2xl border-2 border-dashed border-gray-200 bg-gray-50/40 p-8">
      <div className="max-w-md mx-auto text-center">
        <div className="text-3xl mb-3">📋</div>
        <h3 className="text-base font-semibold text-gray-900 mb-1">
          No active campaign
        </h3>
        <p className="text-[12px] text-gray-500 mb-5 leading-relaxed">
          Hand the agent a list of recipients — it will dial them one-by-one
          respecting pacing and working hours, and record each call&apos;s outcome.
        </p>
        <div className="flex flex-col sm:flex-row gap-2 justify-center">
          <button
            onClick={onLoad}
            className="px-4 py-2 rounded-lg text-[12px] font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors"
          >
            📋 Load sample list
          </button>
          <button
            disabled
            className="px-4 py-2 rounded-lg text-[12px] font-medium bg-white border border-gray-200 text-gray-400 cursor-not-allowed"
            title="CSV import wired once the backend is ready"
          >
            📥 Import CSV…
          </button>
        </div>
        <p className="text-[10px] text-gray-400 mt-4">
          You can also pull a list from another agent (e.g. tenants flagged &quot;rent unpaid&quot;) once cross-agent data hooks land.
        </p>
      </div>
    </div>
  );
}

/* --------------------------- campaign view -------------------------- */

function CampaignView({
  campaign,
  setCampaign,
  reasons,
  language,
  onToggleStatus,
  onStop,
}: {
  campaign: BatchCampaign;
  setCampaign: (c: BatchCampaign | null) => void;
  reasons: VoiceOutboundReason[];
  language: Lang;
  onToggleStatus?: (campaign: BatchCampaign) => void;
  onStop?: (campaign: BatchCampaign) => void;
}) {
  const stats = useMemo(() => computeStats(campaign), [campaign]);
  const reasonLabel = useMemo(
    () => labelForReasonId(campaign.reason, reasons, language),
    [campaign.reason, reasons, language],
  );

  const toggleStatus = () => {
    const next: BatchCampaign["status"] = campaign.status === "running" ? "paused" : "running";
    const updated = { ...campaign, status: next };
    setCampaign(updated);
    onToggleStatus?.(updated);
  };

  const stop = () => {
    onStop?.(campaign);
    setCampaign(null);
  };

  return (
    <div className="rounded-2xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <div className="bg-gradient-to-r from-blue-50 to-purple-50 px-5 py-4 border-b border-gray-100">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-xl">📋</span>
              <h3 className="text-base font-semibold text-gray-900 truncate">
                {campaign.name}
              </h3>
              <CampaignStatusPill status={campaign.status} />
            </div>
            <p className="text-[11px] text-gray-500 mt-1">
              {reasonLabel} · {campaign.recipients.length} recipients ·
              Pacing {campaign.pacing}/hr · Window {String(campaign.workingHours.start).padStart(2, "0")}:00–
              {String(campaign.workingHours.end).padStart(2, "0")}:00 KST
            </p>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              onClick={toggleStatus}
              className={`px-3 py-1.5 rounded-lg text-[12px] font-medium transition-colors ${
                campaign.status === "running"
                  ? "bg-amber-100 text-amber-800 hover:bg-amber-200"
                  : "bg-green-600 text-white hover:bg-green-700"
              }`}
            >
              {campaign.status === "running" ? "⏸ Pause" : "▶ Resume"}
            </button>
            <button
              onClick={stop}
              className="px-3 py-1.5 rounded-lg text-[12px] font-medium text-gray-700 bg-white border border-gray-200 hover:bg-gray-50 transition-colors"
              title="Stop the campaign and clear the queue"
            >
              ⏹ Stop
            </button>
          </div>
        </div>
      </div>

      {/* Progress strip */}
      <div className="px-5 py-3 border-b border-gray-100">
        <div className="flex items-center justify-between text-[11px] text-gray-600 mb-1.5">
          <span>
            <span className="font-semibold text-gray-900">{stats.done}</span> of{" "}
            <span className="font-semibold text-gray-900">{stats.total}</span> done
            {stats.calling > 0 && <span className="text-blue-600 ml-1.5">· 1 in progress</span>}
            {stats.queued > 0 && <span className="text-gray-500 ml-1.5">· {stats.queued} queued</span>}
          </span>
          <span className="font-medium text-gray-700">
            {stats.percent}%
            <span className="text-gray-400 font-normal ml-1.5">· ETA {stats.etaLabel}</span>
          </span>
        </div>
        <div className="h-1.5 w-full bg-gray-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-blue-500 to-purple-500 transition-[width] duration-700"
            style={{ width: `${stats.percent}%` }}
          />
        </div>
        <div className="flex flex-wrap gap-1.5 mt-2.5">
          {stats.outcomes.map((o) => (
            <span
              key={o.outcome}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-gray-50 border border-gray-200 text-[11px] text-gray-700"
            >
              {outcomeIcon(o.outcome)} {BATCH_OUTCOME_LABELS[o.outcome]}
              <span className="text-gray-400">·</span>
              <span className="font-medium text-gray-900">{o.count}</span>
            </span>
          ))}
        </div>
      </div>

      {/* Recipient table */}
      <div className="overflow-x-auto">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-100 text-left text-[10px] uppercase tracking-wider text-gray-500">
              <th className="px-5 py-2.5 font-medium w-8"></th>
              <th className="px-3 py-2.5 font-medium">Recipient</th>
              <th className="px-3 py-2.5 font-medium">Lease</th>
              <th className="px-3 py-2.5 font-medium text-right">Amount</th>
              <th className="px-3 py-2.5 font-medium">Result</th>
              <th className="px-3 py-2.5 font-medium text-right pr-5">Actions</th>
            </tr>
          </thead>
          <tbody>
            {campaign.recipients.map((r) => (
              <RecipientRow key={r.id} r={r} />
            ))}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="px-5 py-3 border-t border-gray-100 bg-gray-50/50 flex items-center justify-between">
        <p className="text-[10px] text-gray-500">
          Each call identifies itself as AI in the first sentence. Conversations recorded for review.
        </p>
        <button className="text-[11px] text-blue-600 hover:text-blue-700 font-medium">
          Export results as CSV →
        </button>
      </div>
    </div>
  );
}

/* ---------------------------- one row ------------------------------- */

function RecipientRow({ r }: { r: BatchRecipient }) {
  const isCalling = r.status === "calling";
  const elapsedSec = isCalling && r.attemptedAt ? Math.floor((Date.now() - r.attemptedAt) / 1000) : 0;

  return (
    <tr
      className={`border-b border-gray-50 last:border-0 transition-colors ${
        isCalling ? "bg-blue-50/60" : "hover:bg-gray-50/40"
      }`}
    >
      <td className="px-5 py-3 align-middle">
        <StatusDot status={r.status} />
      </td>
      <td className="px-3 py-3 align-middle">
        <div className="font-medium text-gray-900">{r.name}</div>
        <div className="text-[11px] text-gray-500 font-mono">{r.number}</div>
      </td>
      <td className="px-3 py-3 align-middle">
        <div className="text-gray-700">{r.context.lease ?? "—"}</div>
        <div className="text-[11px] text-gray-500">Due {String(r.context.dueDate ?? "—")}</div>
      </td>
      <td className="px-3 py-3 align-middle text-right">
        <div className="font-mono font-medium text-gray-900">
          ₩{r.context.amount ?? "—"}
        </div>
      </td>
      <td className="px-3 py-3 align-middle">
        {isCalling ? (
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-pulse" />
            <span className="text-blue-700 font-medium">
              Calling · {formatDuration(elapsedSec)}
            </span>
          </div>
        ) : r.status === "queued" ? (
          <span className="text-gray-400">Queued</span>
        ) : r.outcome ? (
          <div>
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-white border border-gray-200 text-[11px]">
              {outcomeIcon(r.outcome)}
              <span className="text-gray-700">{BATCH_OUTCOME_LABELS[r.outcome]}</span>
            </span>
            {r.notes && (
              <div className="text-[11px] text-gray-500 mt-1 leading-snug line-clamp-2">
                {r.notes}
              </div>
            )}
          </div>
        ) : (
          <span className="text-gray-400">—</span>
        )}
      </td>
      <td className="px-3 py-3 align-middle text-right pr-5">
        {r.status === "queued" && (
          <button className="text-[11px] text-gray-500 hover:text-red-600 font-medium">
            Skip
          </button>
        )}
        {r.status === "completed" && r.callId && (
          <button className="text-[11px] text-blue-600 hover:text-blue-700 font-medium">
            View transcript
          </button>
        )}
        {isCalling && (
          <button className="text-[11px] text-amber-700 hover:text-amber-800 font-medium">
            Take over
          </button>
        )}
      </td>
    </tr>
  );
}

/* ----------------------- visual atoms ------------------------------ */

function CampaignStatusPill({ status }: { status: BatchCampaign["status"] }) {
  const map = {
    idle: { bg: "bg-gray-100", text: "text-gray-700", dot: "bg-gray-400", label: "Idle" },
    running: { bg: "bg-green-100", text: "text-green-800", dot: "bg-green-500 animate-pulse", label: "Running" },
    paused: { bg: "bg-amber-100", text: "text-amber-800", dot: "bg-amber-500", label: "Paused" },
    completed: { bg: "bg-blue-100", text: "text-blue-800", dot: "bg-blue-500", label: "Completed" },
  } as const;
  const m = map[status];
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full ${m.bg} ${m.text} text-[10px] font-medium`}>
      <span className={`w-1.5 h-1.5 rounded-full ${m.dot}`} />
      {m.label}
    </span>
  );
}

function StatusDot({ status }: { status: BatchRecipientStatus }) {
  if (status === "calling")
    return <span className="inline-block w-2 h-2 rounded-full bg-blue-500 animate-pulse" />;
  if (status === "completed")
    return <span className="inline-block w-2 h-2 rounded-full bg-green-500" />;
  if (status === "failed")
    return <span className="inline-block w-2 h-2 rounded-full bg-red-500" />;
  if (status === "skipped")
    return <span className="inline-block w-2 h-2 rounded-full bg-gray-300" />;
  return <span className="inline-block w-2 h-2 rounded-full border border-gray-300" />;
}

function outcomeIcon(outcome: BatchOutcome): string {
  switch (outcome) {
    case "promised_to_pay":
      return "✅";
    case "refused":
      return "🚫";
    case "needs_callback":
      return "🔁";
    case "voicemail_left":
      return "📬";
    case "no_answer":
      return "📵";
    case "wrong_number":
      return "❓";
    case "technical_failure":
      return "⚠️";
  }
}

/* ----------------------- pure helpers ------------------------------ */

function computeStats(c: BatchCampaign) {
  const total = c.recipients.length;
  const done = c.recipients.filter((r) => r.status === "completed" || r.status === "failed" || r.status === "skipped").length;
  const calling = c.recipients.filter((r) => r.status === "calling").length;
  const queued = c.recipients.filter((r) => r.status === "queued").length;
  const percent = total === 0 ? 0 : Math.round((done / total) * 100);

  // ETA: queued × (60 / pacing) minutes
  const etaMinutes = Math.ceil((queued + calling) * (60 / Math.max(c.pacing, 1)));
  const etaLabel =
    etaMinutes === 0
      ? "—"
      : etaMinutes < 60
      ? `~${etaMinutes} min`
      : `~${(etaMinutes / 60).toFixed(1)} hr`;

  const outcomeCounts = new Map<BatchOutcome, number>();
  for (const r of c.recipients) {
    if (r.outcome) outcomeCounts.set(r.outcome, (outcomeCounts.get(r.outcome) ?? 0) + 1);
  }
  const outcomes = Array.from(outcomeCounts, ([outcome, count]) => ({ outcome, count })).sort(
    (a, b) => b.count - a.count,
  );

  return { total, done, calling, queued, percent, etaLabel, outcomes };
}

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function labelForReasonId(id: string, reasons: VoiceOutboundReason[], language: Lang): string {
  const r = reasons.find((rr) => rr.id === id);
  if (!r) return id;
  if (language === "ko") return r.label.ko ?? r.label.en ?? r.id;
  if (language === "en") return r.label.en ?? r.label.ko ?? r.id;
  return r.label.ko ?? r.label.en ?? r.id;
}
