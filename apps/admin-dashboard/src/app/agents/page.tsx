"use client";

import { useEffect, useState } from "react";
import { api } from "@/components/api";
import Badge from "@/components/Badge";
import { AskVIPBar } from "@/components/AskVIP";

export default function AgentsPage() {
  const [agents, setAgents] = useState<any[]>([]);

  useEffect(() => {
    const load = () => api<any[]>("/registry/agents").then((data) => setAgents(data.filter((a: any) => a.status === "active"))).catch(() => {});
    load();
    const i = setInterval(load, 5000);
    return () => clearInterval(i);
  }, []);

  return (
    <div>
      <h1 className="text-[28px] font-semibold tracking-tight mb-1">Agents</h1>
      <p className="text-[14px] text-[var(--text-muted)] mb-6">Registered agents — mock and real</p>

      <div className="mb-6">
        <AskVIPBar suggestions={[
          { label: "Unhealthy agents?", prompt: "which agents are unhealthy" },
          { label: "Agent reliability", prompt: "show agents" },
          { label: "Run asset summary", prompt: "run asset summary" },
        ]} />
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {agents.map((a: any) => (
          <div key={a.id} className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] hover:border-[var(--border-active)] transition-colors">
            {/* Header */}
            <div className="px-4 py-3 border-b border-[var(--border-default)]/50 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className={`w-2 h-2 rounded-full ${a.status === "active" ? "bg-green-400" : "bg-red-400"}`} />
                <h3 className="text-sm font-semibold">{a.name}</h3>
              </div>
              <div className="flex gap-1">
                <Badge text={a.status} />
                {a.is_mock && <Badge text="mock" />}
              </div>
            </div>

            {/* Details */}
            <div className="px-4 py-3 space-y-2 text-xs">
              <div className="flex justify-between text-[var(--text-muted)]">
                <span>Type</span>
                <span className="text-white font-medium">{a.type}</span>
              </div>
              <div className="flex justify-between text-[var(--text-muted)]">
                <span>Version</span>
                <span className="text-white">{a.version}</span>
              </div>
              <div className="flex justify-between text-[var(--text-muted)]">
                <span>Owner</span>
                <span className="text-white">{a.owner_team || "—"}</span>
              </div>
              <div className="flex justify-between text-[var(--text-muted)]">
                <span>Auth</span>
                <span className="text-white">{a.auth_type}</span>
              </div>
              <div className="flex justify-between text-[var(--text-muted)]">
                <span>Priority</span>
                <span className="text-white">{a.priority_score}</span>
              </div>
              <div className="flex justify-between items-center text-[var(--text-muted)]">
                <span>Reliability</span>
                <div className="flex items-center gap-2">
                  <div className="w-16 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                    <div className="h-full bg-green-500 rounded-full" style={{ width: `${(a.reliability_score || 0) * 100}%` }} />
                  </div>
                  <span className="text-white">{((a.reliability_score || 0) * 100).toFixed(0)}%</span>
                </div>
              </div>

              {/* Capabilities */}
              {a.supported_task_types?.length > 0 && (
                <div className="pt-1">
                  <span className="text-[var(--text-secondary)]">Tasks: </span>
                  {a.supported_task_types.map((t: string) => (
                    <span key={t} className="inline-block mr-1 mb-1 px-1.5 py-0.5 bg-[var(--bg-elevated)] text-[var(--text-secondary)] rounded text-[10px]">{t}</span>
                  ))}
                </div>
              )}
              {a.supported_channels?.length > 0 && (
                <div>
                  <span className="text-[var(--text-secondary)]">Channels: </span>
                  {a.supported_channels.map((c: string) => (
                    <span key={c} className="inline-block mr-1 mb-1 px-1.5 py-0.5 bg-[var(--bg-elevated)] text-[var(--text-secondary)] rounded text-[10px]">{c}</span>
                  ))}
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="px-4 py-2 border-t border-[var(--border-default)]/50 text-[10px] text-[var(--text-muted)] truncate">
              {a.endpoint_url}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
