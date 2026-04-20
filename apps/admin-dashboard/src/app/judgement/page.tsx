"use client";

import { useEffect, useState } from "react";
import { api, apiPost } from "@/components/api";
import Badge from "@/components/Badge";
import StatCard from "@/components/StatCard";
import { AskVIPBar } from "@/components/AskVIP";

export default function JudgementPage() {
  const [cases, setCases] = useState<any[]>([]);
  const [selectedCase, setSelectedCase] = useState<any>(null);

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
                  <tr key={c.id} className="border-b border-[var(--border-default)]/30 hover:bg-[var(--bg-hover)] cursor-pointer" onClick={() => setSelectedCase(c)}>
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
                    <td className="px-4 py-2.5 text-[var(--text-muted)]">{c.created_at ? new Date(c.created_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "-"}</td>
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

      {/* Detail Modal */}
      {selectedCase && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setSelectedCase(null)}>
          <div className="bg-white rounded-xl w-full max-w-[600px] shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
              <h3 className="text-[16px] font-semibold text-gray-900">Case Detail</h3>
              <button onClick={() => setSelectedCase(null)} className="text-gray-400 hover:text-gray-700 text-lg">x</button>
            </div>
            <div className="px-6 py-5 space-y-4 max-h-[70vh] overflow-y-auto">
              {(() => {
                const c = selectedCase;
                const riskPct = Math.round((c.risk_score || 0) * 100);
                const evidence = c.evidence || {};
                const rules = evidence.rule_details || [];
                const factors = evidence.risk_factors || [];
                const failedRules = rules.filter((r: any) => !r.passed);

                return (
                  <>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="p-3 rounded-lg bg-gray-50 border border-gray-100">
                        <p className="text-[10px] text-gray-400 mb-1">Risk Score</p>
                        <p className={`text-[20px] font-bold ${riskPct >= 70 ? "text-red-500" : riskPct >= 40 ? "text-amber-500" : "text-green-500"}`}>{riskPct}%</p>
                      </div>
                      <div className="p-3 rounded-lg bg-gray-50 border border-gray-100">
                        <p className="text-[10px] text-gray-400 mb-1">Decision</p>
                        <Badge text={c.decision} />
                      </div>
                    </div>

                    {evidence.reasoning && (
                      <div>
                        <p className="text-[11px] text-gray-400 font-medium mb-1">Reasoning</p>
                        <p className="text-[13px] text-gray-700 bg-gray-50 rounded-lg p-3 border border-gray-100">{evidence.reasoning}</p>
                      </div>
                    )}

                    {failedRules.length > 0 && (
                      <div>
                        <p className="text-[11px] text-gray-400 font-medium mb-2">Failed Rules ({failedRules.length})</p>
                        {failedRules.map((r: any, i: number) => (
                          <div key={i} className="flex items-start gap-2 mb-1.5 text-[12px]">
                            <span className="text-red-500 mt-0.5">x</span>
                            <div>
                              <span className="font-medium text-gray-800">{r.rule}</span>
                              <span className="text-gray-500 ml-1">— {r.reason}</span>
                              <Badge text={r.severity} />
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    {factors.length > 0 && (
                      <div>
                        <p className="text-[11px] text-gray-400 font-medium mb-2">Risk Factors</p>
                        {factors.map((f: any, i: number) => (
                          <div key={i} className="flex items-center justify-between py-1.5 text-[12px] border-b border-gray-50">
                            <span className="text-gray-700">{f.factor}</span>
                            <div className="flex items-center gap-2">
                              <span className="text-gray-400">{f.detail}</span>
                              <span className="font-semibold text-red-500">+{f.points}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    <div className="text-[10px] text-gray-400 pt-2 border-t border-gray-100">
                      <p>Case ID: {c.id}</p>
                      <p>Created: {c.created_at ? new Date(c.created_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "-"}</p>
                    </div>
                  </>
                );
              })()}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
