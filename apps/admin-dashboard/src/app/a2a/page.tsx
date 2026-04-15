"use client";

import { useEffect, useState } from "react";
import { api, apiPost } from "@/components/api";
import Badge from "@/components/Badge";
import { AskVIPFloat } from "@/components/AskVIP";

export default function A2APage() {
  const [messages, setMessages] = useState<any[]>([]);
  const [busStatus, setBusStatus] = useState<any>(null);
  const [running, setRunning] = useState(false);
  const [demoResult, setDemoResult] = useState<string | null>(null);

  const load = () => {
    api<any[]>("/a2a/messages?limit=30").then(setMessages).catch(() => {});
    api<any>("/a2a/status").then(setBusStatus).catch(() => {});
  };

  useEffect(() => { load(); const i = setInterval(load, 5000); return () => clearInterval(i); }, []);

  const runDemo = async () => {
    setRunning(true);
    setDemoResult(null);
    const data = await apiPost<any>("/a2a/demo/risk-flow");
    setDemoResult(`${data.steps} messages sent (trace: ${data.trace_id})`);
    load();
    setRunning(false);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold mb-1">A2A Monitor</h1>
          <p className="text-sm text-[var(--text-muted)]">Inter-agent communication</p>
        </div>
        <div className="flex items-center gap-3">
          {busStatus && (
            <span className="text-[10px] px-2 py-1 rounded-full bg-[var(--bg-elevated)] text-[var(--text-secondary)]">
              Bus: {busStatus.event_bus}
            </span>
          )}
          <button onClick={runDemo} disabled={running}
            className="px-4 py-2 rounded bg-[var(--text-primary)] hover:bg-[var(--text-secondary)] text-white text-xs font-semibold disabled:opacity-50">
            {running ? "Running..." : "Risk Alert Demo"}
          </button>
        </div>
      </div>

      {demoResult && (
        <div className="mb-4 px-4 py-2 rounded bg-[var(--bg-elevated)] border border-[var(--border-default)] text-xs text-[var(--text-primary)]">{demoResult}</div>
      )}

      <div className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)]">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-[var(--text-muted)] border-b border-[var(--border-default)]">
                <th className="text-left px-4 py-2.5">Type</th>
                <th className="text-left px-4 py-2.5">Sender</th>
                <th className="text-left px-4 py-2.5">Target</th>
                <th className="text-left px-4 py-2.5">Risk</th>
                <th className="text-left px-4 py-2.5">Status</th>
                <th className="text-left px-4 py-2.5">Trace</th>
                <th className="text-left px-4 py-2.5">Time</th>
              </tr>
            </thead>
            <tbody>
              {messages.map((m: any) => {
                const isHighRisk = m.envelope?.is_high_risk === true;
                return (
                  <tr key={m.id} className={`border-b border-[var(--border-default)]/30 hover:bg-[var(--bg-hover)] ${isHighRisk ? "bg-red-950/10" : ""}`}>
                    <td className="px-4 py-2.5"><Badge text={m.message_type} /></td>
                    <td className="px-4 py-2.5 text-cyan-400">{m.sender_agent}</td>
                    <td className="px-4 py-2.5">{m.target_agent}</td>
                    <td className="px-4 py-2.5">
                      {isHighRisk
                        ? <span className="text-[10px] px-2 py-0.5 rounded-full text-red-400 bg-red-900/30 font-medium">HIGH</span>
                        : <span className="text-[10px] text-[var(--text-muted)]">—</span>}
                    </td>
                    <td className="px-4 py-2.5"><Badge text={m.status} /></td>
                    <td className="px-4 py-2.5 text-[var(--text-muted)] font-mono">{m.trace_id}</td>
                    <td className="px-4 py-2.5 text-[var(--text-muted)]">{m.created_at ? new Date(m.created_at).toLocaleTimeString() : "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {messages.length === 0 && <p className="text-center text-[var(--text-muted)] py-8 text-xs">No A2A messages. Click Risk Alert Demo to generate.</p>}
        </div>
      </div>

      <AskVIPFloat defaultPrompt="summarize recent A2A activity" />
    </div>
  );
}
