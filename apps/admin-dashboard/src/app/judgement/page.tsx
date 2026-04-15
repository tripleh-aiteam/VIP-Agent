"use client";

import { useEffect, useState } from "react";
import { api, apiPost } from "@/components/api";
import Badge from "@/components/Badge";
import StatCard from "@/components/StatCard";
import { AskVIPBar } from "@/components/AskVIP";

export default function JudgementPage() {
  const [cases, setCases] = useState<any[]>([]);

  const load = () => api<any[]>("/judgement/cases").then(setCases).catch(() => {});
  useEffect(() => { load(); const i = setInterval(load, 5000); return () => clearInterval(i); }, []);

  const handleAction = async (id: string, action: "approve" | "reject") => {
    await apiPost(`/judgement/cases/${id}/${action}`, { user_id: "admin", trace_id: "dashboard" });
    load();
  };

  const pending = cases.filter((c) => c.decision === "human_review_required" || c.decision === "conditional_approve");
  const approved = cases.filter((c) => c.decision === "auto_approve");
  const rejected = cases.filter((c) => c.decision === "rejected");

  return (
    <div>
      <h1 className="text-[28px] font-semibold tracking-tight mb-1">Judgement</h1>
      <p className="text-[14px] text-[var(--text-muted)] mb-6">Risk evaluation and approval workflow</p>

      <div className="mb-6">
        <AskVIPBar suggestions={[
          { label: "Pending approvals", prompt: "show pending approvals" },
          { label: "High risk cases", prompt: "show high risk cases" },
          { label: "Why was case rejected?", prompt: "why was the last case rejected" },
        ]} />
      </div>

      <div className="grid grid-cols-3 gap-4 mb-6">
        <StatCard label="Pending Review" value={pending.length} color="yellow" />
        <StatCard label="Approved" value={approved.length} color="green" />
        <StatCard label="Rejected" value={rejected.length} color="red" />
      </div>

      <div className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)]">
        <div className="px-4 py-3 border-b border-[var(--border-default)]">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">All Cases</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-[var(--text-muted)] border-b border-[var(--border-default)]/50">
                <th className="text-left px-4 py-2">Case</th>
                <th className="text-left px-4 py-2">Risk Score</th>
                <th className="text-left px-4 py-2">Rules</th>
                <th className="text-left px-4 py-2">Decision</th>
                <th className="text-left px-4 py-2">Reasoning</th>
                <th className="text-left px-4 py-2">Time</th>
                <th className="text-left px-4 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c: any) => {
                const riskPct = Math.round((c.risk_score || 0) * 100);
                const isPending = c.decision === "human_review_required" || c.decision === "conditional_approve";
                return (
                  <tr key={c.id} className="border-b border-[var(--border-default)]/30 hover:bg-[var(--bg-hover)]">
                    <td className="px-4 py-2.5 font-mono text-[var(--text-secondary)]">{c.id.slice(0, 8)}...</td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <div className="w-14 h-1.5 bg-[var(--bg-elevated)] rounded-full overflow-hidden">
                          <div className={`h-full rounded-full ${riskPct >= 70 ? "bg-red-500" : riskPct >= 40 ? "bg-[var(--brand-blue-deep)]" : "bg-green-500"}`} style={{ width: `${riskPct}%` }} />
                        </div>
                        <span className={`text-xs font-medium ${riskPct >= 70 ? "text-red-400" : riskPct >= 40 ? "text-[var(--brand-blue)]" : "text-green-400"}`}>{riskPct}%</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5"><Badge text={c.rule_result || "—"} /></td>
                    <td className="px-4 py-2.5"><Badge text={c.decision} /></td>
                    <td className="px-4 py-2.5 text-[var(--text-muted)] max-w-[200px] truncate">{c.evidence?.reasoning || "—"}</td>
                    <td className="px-4 py-2.5 text-[var(--text-muted)]">{c.created_at ? new Date(c.created_at).toLocaleTimeString() : "-"}</td>
                    <td className="px-4 py-2.5">
                      {isPending ? (
                        <div className="flex gap-1">
                          <button onClick={() => handleAction(c.id, "approve")} className="px-2 py-1 text-[10px] rounded bg-[var(--text-primary)] hover:bg-[var(--text-secondary)] text-white">Approve</button>
                          <button onClick={() => handleAction(c.id, "reject")} className="px-2 py-1 text-[10px] rounded bg-[var(--text-primary)] hover:bg-[var(--text-secondary)] text-white">Reject</button>
                        </div>
                      ) : <span className="text-[10px] text-[var(--text-muted)]">—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {cases.length === 0 && <p className="text-center text-[var(--text-muted)] py-8 text-xs">No judgement cases. Dispatch a stock_analysis task to trigger one.</p>}
        </div>
      </div>
    </div>
  );
}
