"use client";

import { useEffect, useState } from "react";
import { API, api, apiPost } from "./api";

interface SystemMetrics {
  total_meetings_with_twin: number;
  active_sessions_now: number;
  total_commitments: number;
  total_escalations: number;
  voice_profiles_ready: number;
}

interface AutoCreateResult {
  ok: boolean;
  meeting_id?: string;
  meeting_title?: string;
  meeting_room_url?: string;
  joined?: { twin_name: string }[];
  unmatched_names?: string[];
  message: string;
  korean_message: string;
}

/**
 * Compact embedded ops bar shown at the top of /meetings:
 * - Natural-language meeting starter (Sprint 8)
 * - 4-tile metric snapshot (Sprint 9 — replaces standalone /admin page)
 * - Collapsible details
 */
export default function MeetingOpsBar({ onMeetingCreated }: { onMeetingCreated?: (id: string) => void }) {
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [escalations, setEscalations] = useState<any[]>([]);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [autoResult, setAutoResult] = useState<AutoCreateResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchMetrics();
    const id = setInterval(fetchMetrics, 30_000);
    return () => clearInterval(id);
  }, []);

  async function fetchMetrics() {
    try {
      const d: any = await api(`/twins/admin/meeting-metrics/system?since_days=30`);
      setMetrics(d.metrics);
      setEscalations(d.recent_escalations || []);
    } catch {/* silent — endpoint optional */}
  }

  async function handleAutoCreate() {
    if (!prompt.trim()) return;
    setBusy(true);
    setError("");
    setAutoResult(null);
    try {
      const r: AutoCreateResult = await apiPost(`/twins/meetings/auto-create`, {
        text: prompt,
        authority: "answer_factual",
      });
      setAutoResult(r);
      if (r.ok && r.meeting_id && onMeetingCreated) onMeetingCreated(r.meeting_id);
    } catch (e: any) {
      setError(e.message || "Auto-create failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bg-[var(--card-bg,#fff)] rounded-xl border border-[var(--card-border,#e5e7eb)] mb-5">
      {/* Natural-language meeting starter */}
      <div className="p-4">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-sm font-semibold">Start a meeting with your assistant</span>
          <span className="text-xs text-gray-400">— Korean or English, e.g. "Let's meet with Kim and Davronbek"</span>
        </div>
        <div className="flex gap-2">
          <input
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleAutoCreate()}
            placeholder="회의하자 김현성 트윈과 다브론벡 트윈"
            className="flex-1 border rounded-lg px-3 py-2 text-sm"
            disabled={busy}
          />
          <button
            onClick={handleAutoCreate}
            disabled={busy || !prompt.trim()}
            className="bg-indigo-600 text-white rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            {busy ? "Starting…" : "Start Meeting"}
          </button>
        </div>
        {error && (
          <div className="mt-2 text-xs text-red-600">{error}</div>
        )}
        {autoResult && (
          <div className={`mt-3 p-3 rounded text-sm ${autoResult.ok ? "bg-emerald-50 border border-emerald-200" : "bg-amber-50 border border-amber-200"}`}>
            <div className="font-medium">{autoResult.message}</div>
            {autoResult.korean_message && (
              <div className="text-xs text-gray-600 mt-1">{autoResult.korean_message}</div>
            )}
            {autoResult.unmatched_names && autoResult.unmatched_names.length > 0 && (
              <div className="text-xs text-amber-700 mt-1">
                Not matched: {autoResult.unmatched_names.join(", ")}
              </div>
            )}
            {autoResult.ok && autoResult.meeting_room_url && (
              <a
                href={autoResult.meeting_room_url}
                className="inline-block mt-2 text-indigo-700 underline text-xs"
              >
                Open meeting room →
              </a>
            )}
          </div>
        )}
      </div>

      {/* Compact metrics row + collapsible details */}
      <div className="border-t border-gray-100 px-4 py-3 flex flex-wrap items-center gap-3 text-xs">
        <span className="text-gray-400 mr-1">Last 30 days</span>
        <Tile label="Meetings" value={metrics?.total_meetings_with_twin ?? "—"} />
        <Tile label="Live" value={metrics?.active_sessions_now ?? "—"} highlight={(metrics?.active_sessions_now ?? 0) > 0} />
        <Tile label="Commits" value={metrics?.total_commitments ?? "—"} />
        <Tile label="Escalations" value={metrics?.total_escalations ?? "—"} />
        <Tile label="Voices ready" value={metrics?.voice_profiles_ready ?? "—"} />
        <button
          onClick={() => setExpanded(v => !v)}
          className="ml-auto text-indigo-600 underline"
        >
          {expanded ? "Hide details" : "Show details"}
        </button>
      </div>

      {expanded && (
        <div className="border-t border-gray-100 p-4">
          <div className="text-xs font-medium text-gray-500 mb-2">Recent escalations</div>
          {escalations.length === 0 ? (
            <div className="text-xs text-gray-400">No recent escalations.</div>
          ) : (
            <table className="w-full text-xs">
              <thead className="text-gray-400">
                <tr>
                  <th className="text-left py-1">Twin</th>
                  <th className="text-left py-1">Meeting</th>
                  <th className="text-left py-1">Escalations</th>
                  <th className="text-left py-1">Authority</th>
                </tr>
              </thead>
              <tbody>
                {escalations.map((e: any) => (
                  <tr key={e.participant_id} className="border-t">
                    <td className="py-1">{e.twin_name}</td>
                    <td className="py-1">{e.meeting_title}</td>
                    <td className="py-1 text-amber-700 font-semibold">{e.escalation_count}</td>
                    <td className="py-1">{e.authority}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

function Tile({ label, value, highlight }: { label: string; value: number | string; highlight?: boolean }) {
  return (
    <div className={`px-2.5 py-1 rounded ${highlight ? "bg-emerald-50" : "bg-gray-50"}`}>
      <span className="text-gray-500">{label}: </span>
      <span className="font-semibold">{value}</span>
    </div>
  );
}
