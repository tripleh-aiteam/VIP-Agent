"use client";

/**
 * CallsHistoryList — sortable, filterable table of past calls.
 * Each row clickable → opens CallDetailDrawer.
 */

import { useMemo, useState } from "react";
import type { CallEvent, CallStatus } from "./types";

interface Props {
  calls: CallEvent[];
  onCallClick: (call: CallEvent) => void;
}

type StatusFilter = "all" | "escalated" | "needsReview" | "missed";

export function CallsHistoryList({ calls, onCallClick }: Props) {
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    let result = calls;
    if (filter === "escalated") {
      result = result.filter((c) => c.status === "escalated");
    } else if (filter === "needsReview") {
      result = result.filter((c) => c.needsReview);
    } else if (filter === "missed") {
      result = result.filter((c) => c.status === "missed");
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (c) =>
          c.caller.number.toLowerCase().includes(q) ||
          c.caller.name?.toLowerCase().includes(q) ||
          c.summary?.toLowerCase().includes(q),
      );
    }
    return result;
  }, [calls, filter, search]);

  const stats = useMemo(() => {
    return {
      total: calls.length,
      escalated: calls.filter((c) => c.status === "escalated").length,
      needsReview: calls.filter((c) => c.needsReview).length,
      missed: calls.filter((c) => c.status === "missed").length,
    };
  }, [calls]);

  return (
    <div className="space-y-4">
      {/* Filter pills + search */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <FilterPill active={filter === "all"} onClick={() => setFilter("all")}>
            All <span className="ml-1 opacity-60">{stats.total}</span>
          </FilterPill>
          <FilterPill
            active={filter === "escalated"}
            onClick={() => setFilter("escalated")}
            color="red"
          >
            Escalated <span className="ml-1 opacity-60">{stats.escalated}</span>
          </FilterPill>
          <FilterPill
            active={filter === "needsReview"}
            onClick={() => setFilter("needsReview")}
            color="amber"
          >
            Needs review <span className="ml-1 opacity-60">{stats.needsReview}</span>
          </FilterPill>
          <FilterPill
            active={filter === "missed"}
            onClick={() => setFilter("missed")}
            color="gray"
          >
            Missed <span className="ml-1 opacity-60">{stats.missed}</span>
          </FilterPill>
        </div>
        <input
          type="search"
          placeholder="Search caller, number, summary…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-[260px] px-3 py-1.5 rounded-lg border border-gray-300 text-[12px] focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      {/* Table */}
      <div className="rounded-2xl border border-gray-200 bg-white overflow-hidden">
        {filtered.length === 0 ? (
          <div className="p-12 text-center text-gray-500">
            <div className="text-3xl mb-2">📋</div>
            <div className="text-[13px]">No calls match the current filter.</div>
          </div>
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left px-4 py-2 font-medium text-gray-600 text-[11px] uppercase tracking-wider">Caller</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600 text-[11px] uppercase tracking-wider">Direction</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600 text-[11px] uppercase tracking-wider">Duration</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600 text-[11px] uppercase tracking-wider">When</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600 text-[11px] uppercase tracking-wider">Result</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => onCallClick(c)}
                  className="border-b border-gray-100 last:border-b-0 hover:bg-gray-50 cursor-pointer transition-colors"
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <StatusDot status={c.status} />
                      <div>
                        <div className="font-medium text-gray-900">
                          {c.caller.name || "Unknown"}
                        </div>
                        <div className="text-[11px] text-gray-500 font-mono">{c.caller.number}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="inline-flex items-center gap-1 text-gray-700">
                      {c.direction === "inbound" ? "📥 In" : "📤 Out"}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-gray-700">
                    {c.durationSec !== undefined ? formatDuration(c.durationSec) : "—"}
                  </td>
                  <td className="px-4 py-3 text-gray-600">{formatRelative(c.startedAt)}</td>
                  <td className="px-4 py-3">
                    <div className="text-gray-700 line-clamp-2 max-w-[480px]">
                      {c.summary || (c.status === "missed" ? "Missed — no transcript" : "—")}
                    </div>
                    {c.escalation && (
                      <div className="mt-1 inline-flex items-center gap-1 text-[11px] text-red-700 bg-red-50 px-1.5 py-0.5 rounded">
                        ⚠ escalated — {c.escalation.reason}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function FilterPill({
  active,
  onClick,
  children,
  color = "blue",
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  color?: "blue" | "red" | "amber" | "gray";
}) {
  const colors = {
    blue: active ? "bg-blue-600 text-white" : "bg-white text-gray-700 hover:bg-blue-50 border-gray-200",
    red: active ? "bg-red-600 text-white" : "bg-white text-gray-700 hover:bg-red-50 border-gray-200",
    amber: active ? "bg-amber-600 text-white" : "bg-white text-gray-700 hover:bg-amber-50 border-gray-200",
    gray: active ? "bg-gray-600 text-white" : "bg-white text-gray-700 hover:bg-gray-50 border-gray-200",
  };
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 rounded-full text-[12px] font-medium border transition-colors ${colors[color]} ${
        active ? "border-transparent" : ""
      }`}
    >
      {children}
    </button>
  );
}

function StatusDot({ status }: { status: CallStatus }) {
  const colors: Record<CallStatus, string> = {
    active: "bg-red-500 animate-pulse",
    ringing: "bg-yellow-500 animate-pulse",
    completed: "bg-green-500",
    escalated: "bg-red-500",
    missed: "bg-gray-400",
    failed: "bg-gray-400",
  };
  return <span className={`shrink-0 w-2.5 h-2.5 rounded-full ${colors[status]}`} />;
}

function formatDuration(totalSec: number): string {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatRelative(ts: number): string {
  const diff = Date.now() - ts;
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}
