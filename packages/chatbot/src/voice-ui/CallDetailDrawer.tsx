"use client";

/**
 * CallDetailDrawer — right-side slide-in panel showing one call in full.
 * Transcript, audio player, urgency reasoning, follow-up actions.
 */

import type { CallEvent } from "./types";

interface Props {
  call: CallEvent | null;
  onClose: () => void;
  /** Operator clicks "Call back" — agent dials the caller. Optional. */
  onCallBack?: (call: CallEvent) => void;
  /** Operator clicks "Add to knowledge" — call summary/transcript becomes a learnable note. Optional. */
  onAddToKnowledge?: (call: CallEvent) => void;
  /** Operator clicks "Correct" / "Improve" on a needs-review call. Optional. */
  onReviewFeedback?: (call: CallEvent, verdict: "correct" | "improve") => void;
}

export function CallDetailDrawer({
  call,
  onClose,
  onCallBack,
  onAddToKnowledge,
  onReviewFeedback,
}: Props) {
  if (!call) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/30 z-40"
        onClick={onClose}
      />
      {/* Drawer */}
      <div className="fixed top-0 right-0 h-screen w-full max-w-[520px] bg-white shadow-2xl z-50 flex flex-col animate-in slide-in-from-right">
        {/* Header */}
        <div className="px-5 py-4 border-b border-gray-200 flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white text-base font-semibold">
              {call.caller.name?.slice(0, 1) || "?"}
            </div>
            <div>
              <h3 className="text-base font-semibold text-gray-900">
                {call.caller.name || "Unknown caller"}
              </h3>
              <p className="text-[12px] text-gray-500 font-mono">{call.caller.number}</p>
              {call.caller.tag && (
                <p className="text-[11px] text-gray-400 mt-0.5">{call.caller.tag}</p>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700 text-2xl leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {/* Metadata row */}
        <div className="px-5 py-3 border-b border-gray-100 bg-gray-50/50 grid grid-cols-3 gap-3 text-[12px]">
          <Meta label="Direction" value={call.direction === "inbound" ? "📥 Inbound" : "📤 Outbound"} />
          <Meta label="Status" value={statusLabel(call.status)} />
          <Meta label="Duration" value={call.durationSec ? `${Math.floor(call.durationSec / 60)}m ${call.durationSec % 60}s` : "—"} />
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {/* Summary */}
          {call.summary && (
            <Section title="Summary">
              <p className="text-[13px] text-gray-700 leading-relaxed">{call.summary}</p>
            </Section>
          )}

          {/* Escalation reasoning */}
          {call.escalation && (
            <Section title="⚠️ Escalation">
              <div className="rounded-lg bg-red-50 border border-red-200 p-3 space-y-1">
                <div className="text-[12px]">
                  <span className="text-red-700 font-medium">Sent to:</span>{" "}
                  <span className="text-gray-700">{call.escalation.to}</span>
                </div>
                <div className="text-[12px]">
                  <span className="text-red-700 font-medium">Reason:</span>{" "}
                  <span className="text-gray-700">{call.escalation.reason}</span>
                </div>
                <div className="text-[11px] text-gray-500 pt-1">
                  Fired {formatTime(call.escalation.at)}
                </div>
              </div>
            </Section>
          )}

          {/* Audio player */}
          {call.recordingUrl ? (
            <Section title="Recording">
              <div className="rounded-lg border border-gray-200 p-3 bg-gray-50">
                <audio
                  controls
                  src={call.recordingUrl}
                  className="w-full"
                  onError={(e) =>
                    ((e.target as HTMLAudioElement).style.display = "none")
                  }
                />
                <p className="text-[10px] text-gray-400 mt-2">
                  Recording available for 90 days (retention policy)
                </p>
              </div>
            </Section>
          ) : (
            <Section title="Recording">
              <p className="text-[12px] text-gray-500 italic">
                No recording available (call too short or recording skipped)
              </p>
            </Section>
          )}

          {/* Transcript */}
          {call.transcript.length > 0 ? (
            <Section title="Transcript">
              <div className="space-y-2.5">
                {call.transcript.map((turn) => (
                  <div
                    key={turn.id}
                    className={`flex gap-2 ${turn.role === "bot" ? "" : "flex-row-reverse"}`}
                  >
                    <div
                      className={`shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[11px] ${
                        turn.role === "bot"
                          ? "bg-blue-100 text-blue-700"
                          : "bg-gray-100 text-gray-700"
                      }`}
                    >
                      {turn.role === "bot" ? "🤖" : "👤"}
                    </div>
                    <div className={`max-w-[80%] ${turn.role === "bot" ? "" : "text-right"}`}>
                      <div
                        className={`inline-block px-2.5 py-1.5 rounded-lg text-[12px] leading-snug ${
                          turn.role === "bot"
                            ? "bg-blue-50 text-gray-800"
                            : "bg-gray-100 text-gray-800"
                        }`}
                      >
                        {turn.text}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </Section>
          ) : (
            <Section title="Transcript">
              <p className="text-[12px] text-gray-500 italic">No transcript available.</p>
            </Section>
          )}

          {/* Self-improve action */}
          {call.needsReview && (
            <Section title="✏️ Needs review">
              <div className="rounded-lg bg-amber-50 border border-amber-200 p-3">
                <p className="text-[12px] text-amber-900 mb-2">
                  The bot answered, but flagged this call for human review. Was the answer correct?
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => onReviewFeedback?.(call, "correct")}
                    disabled={!onReviewFeedback}
                    className="flex-1 px-3 py-1.5 rounded-lg bg-green-600 text-white text-[12px] font-medium hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    ✓ Correct
                  </button>
                  <button
                    onClick={() => onReviewFeedback?.(call, "improve")}
                    disabled={!onReviewFeedback}
                    className="flex-1 px-3 py-1.5 rounded-lg bg-amber-600 text-white text-[12px] font-medium hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    ✏ Improve
                  </button>
                </div>
              </div>
            </Section>
          )}
        </div>

        {/* Footer actions */}
        <div className="border-t border-gray-200 px-5 py-3 flex gap-2 bg-white">
          <button
            onClick={() => onCallBack?.(call)}
            disabled={!onCallBack}
            className="flex-1 px-3 py-2 rounded-lg bg-gray-100 text-gray-700 text-[12px] font-medium hover:bg-gray-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            📞 Call back
          </button>
          <button
            onClick={() => onAddToKnowledge?.(call)}
            disabled={!onAddToKnowledge}
            className="flex-1 px-3 py-2 rounded-lg bg-blue-600 text-white text-[12px] font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            💬 Add to knowledge
          </button>
        </div>
      </div>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-[11px] font-semibold text-gray-500 uppercase tracking-wider mb-2">
        {title}
      </h4>
      {children}
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] text-gray-500 uppercase tracking-wider">{label}</div>
      <div className="text-gray-900 font-medium mt-0.5">{value}</div>
    </div>
  );
}

function statusLabel(s: CallEvent["status"]) {
  switch (s) {
    case "completed":
      return "✓ Completed";
    case "escalated":
      return "⚠ Escalated";
    case "missed":
      return "○ Missed";
    case "failed":
      return "✗ Failed";
    case "active":
      return "● Active";
    case "ringing":
      return "○ Ringing";
  }
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
