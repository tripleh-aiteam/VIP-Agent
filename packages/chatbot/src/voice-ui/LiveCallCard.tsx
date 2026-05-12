"use client";

/**
 * LiveCallCard — UI for an in-progress call.
 *
 * Shows caller info, urgency indicator, elapsed duration, streaming
 * transcript, and the human-takeover controls. Duration ticks every
 * second while `call.status === "active"`. Transcript auto-scrolls as
 * new turns arrive.
 *
 * Agent-agnostic: this card just renders the CallEvent the dashboard
 * passes in. The dashboard is the one that subscribes to the WebSocket
 * (or to mock data) — that boundary keeps this component testable.
 */

import { useEffect, useRef, useState } from "react";
import type { CallEvent } from "./types";

interface Props {
  call: CallEvent | null;
  /** Fires when the operator clicks "Listen in." Optional — button greyed out if absent. */
  onListenIn?: (call: CallEvent) => void;
  /** Fires when the operator clicks "Mark urgent." Optional. */
  onMarkUrgent?: (call: CallEvent) => void;
  /** Fires when the operator clicks "Take over" — barges the call to a human. Optional. */
  onTakeOver?: (call: CallEvent) => void;
}

export function LiveCallCard({ call, onListenIn, onMarkUrgent, onTakeOver }: Props) {
  const [nowMs, setNowMs] = useState(Date.now());
  const transcriptRef = useRef<HTMLDivElement>(null);

  // Tick the duration display every second while the call is active
  useEffect(() => {
    if (!call || call.status !== "active") return;
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, [call]);

  // Auto-scroll transcript to bottom as new turns arrive
  useEffect(() => {
    transcriptRef.current?.scrollTo({
      top: transcriptRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [call?.transcript.length]);

  if (!call) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-12 text-center">
        <div className="text-5xl mb-3">📞</div>
        <h3 className="text-lg font-semibold text-gray-800">No active call</h3>
        <p className="text-sm text-gray-500 mt-1">
          When a customer calls, the AI assistant will pick up and you&apos;ll see the live transcript here.
        </p>
      </div>
    );
  }

  const durationSec = Math.floor((nowMs - call.startedAt) / 1000);
  const urgencyMeta = getUrgencyMeta(call.urgency);

  return (
    <div className="rounded-2xl border border-blue-200 bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <div className="bg-gradient-to-r from-blue-50 to-purple-50 px-5 py-4 flex items-start justify-between border-b border-gray-100">
        <div className="flex items-center gap-3">
          <div className="relative">
            <div className="w-12 h-12 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white text-lg font-semibold">
              {call.caller.name?.slice(0, 1) || "?"}
            </div>
            <span className="absolute -bottom-0.5 -right-0.5 w-4 h-4 rounded-full bg-red-500 animate-pulse border-2 border-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[12px] font-semibold text-red-500 uppercase tracking-wider">● LIVE CALL</span>
              <span className="text-[12px] text-gray-500">·</span>
              <span className="text-[12px] text-gray-500">{call.direction === "inbound" ? "Incoming" : "Outgoing"}</span>
            </div>
            <div className="text-base font-semibold text-gray-900 mt-0.5">
              {call.caller.name || "Unknown caller"}
            </div>
            <div className="text-[12px] text-gray-500 mt-0.5 font-mono">{call.caller.number}</div>
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-mono font-semibold text-gray-800 tabular-nums">
            {formatDuration(durationSec)}
          </div>
          <div className={`inline-flex items-center gap-1.5 mt-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${urgencyMeta.bg} ${urgencyMeta.text}`}>
            <span className="w-1.5 h-1.5 rounded-full bg-current" />
            {urgencyMeta.label}
          </div>
        </div>
      </div>

      {/* Transcript */}
      <div ref={transcriptRef} className="max-h-[360px] overflow-y-auto px-5 py-4 space-y-3 bg-gray-50/50">
        {call.transcript.map((turn) => (
          <div
            key={turn.id}
            className={`flex gap-3 ${turn.role === "bot" ? "" : "flex-row-reverse"}`}
          >
            <div
              className={`shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-[12px] ${
                turn.role === "bot"
                  ? "bg-blue-100 text-blue-700"
                  : "bg-gray-100 text-gray-700"
              }`}
            >
              {turn.role === "bot" ? "🤖" : "👤"}
            </div>
            <div className={`max-w-[80%] ${turn.role === "bot" ? "" : "text-right"}`}>
              <div
                className={`inline-block px-3 py-2 rounded-lg text-[13px] leading-snug ${
                  turn.role === "bot"
                    ? "bg-white border border-gray-200 text-gray-800"
                    : "bg-blue-600 text-white"
                } ${turn.partial ? "italic opacity-70" : ""}`}
              >
                {turn.text}
                {turn.partial && <span className="inline-block ml-1 animate-pulse">…</span>}
              </div>
              {turn.confidence !== undefined && turn.role === "user" && (
                <div className="text-[10px] text-gray-400 mt-1">
                  STT confidence: {Math.round(turn.confidence * 100)}%
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Action bar */}
      <div className="border-t border-gray-100 px-5 py-3 flex items-center gap-2 bg-white">
        <button
          onClick={() => onListenIn?.(call)}
          disabled={!onListenIn}
          className="flex-1 px-3 py-2 rounded-lg bg-gray-100 text-gray-700 text-[12px] font-medium hover:bg-gray-200 transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          🔊 Listen in
        </button>
        <button
          onClick={() => onMarkUrgent?.(call)}
          disabled={!onMarkUrgent}
          className="flex-1 px-3 py-2 rounded-lg bg-amber-50 text-amber-700 text-[12px] font-medium hover:bg-amber-100 transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          ⚠️ Mark urgent
        </button>
        <button
          onClick={() => onTakeOver?.(call)}
          disabled={!onTakeOver}
          className="flex-1 px-3 py-2 rounded-lg bg-red-600 text-white text-[12px] font-medium hover:bg-red-700 transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          ☎️ Take over
        </button>
      </div>
    </div>
  );
}

function formatDuration(totalSec: number): string {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

function getUrgencyMeta(urgency?: CallEvent["urgency"]) {
  switch (urgency) {
    case "high":
      return { label: "High urgency", bg: "bg-red-100", text: "text-red-700" };
    case "medium":
      return { label: "Medium urgency", bg: "bg-amber-100", text: "text-amber-700" };
    case "low":
      return { label: "Low urgency", bg: "bg-green-100", text: "text-green-700" };
    default:
      return { label: "Classifying…", bg: "bg-gray-100", text: "text-gray-600" };
  }
}
