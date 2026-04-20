"use client";

import React, { useEffect, useState } from "react";
import { api, apiPost } from "@/components/api";
import Badge from "@/components/Badge";
import { AskVIPFloat } from "@/components/AskVIP";
import { useRealtimeEvents } from "@/components/useRealtimeEvents";

type Tab = "messages" | "notifications" | "triggers" | "chain";

export default function A2APage() {
  const [tab, setTab] = useState<Tab>("messages");
  const [messages, setMessages] = useState<any[]>([]);
  const [notifications, setNotifications] = useState<any[]>([]);
  const [triggers, setTriggers] = useState<any[]>([]);
  const [busStatus, setBusStatus] = useState<any>(null);
  const [running, setRunning] = useState(false);
  const [demoResult, setDemoResult] = useState<string | null>(null);
  const [chainTrace, setChainTrace] = useState("");
  const [chainData, setChainData] = useState<any>(null);
  const [chainLoading, setChainLoading] = useState(false);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportResult, setReportResult] = useState<any>(null);
  const [dataReqLoading, setDataReqLoading] = useState(false);
  const [dataReqResult, setDataReqResult] = useState<any>(null);
  const [expandedMsg, setExpandedMsg] = useState<string | null>(null);

  const load = () => {
    api<any[]>("/a2a/messages?limit=30").then(setMessages).catch(() => {});
    api<any>("/a2a/status").then(setBusStatus).catch(() => {});
    api<any[]>("/a2a/notifications?limit=20").then(setNotifications).catch(() => {});
    api<any>("/a2a/triggers").then((d: any) => setTriggers(d.triggers || [])).catch(() => {});
  };

  useEffect(() => { load(); const i = setInterval(load, 15000); return () => clearInterval(i); }, []);

  // Real-time: refresh when any A2A event arrives via WebSocket
  useRealtimeEvents((event) => {
    if (event.type.includes("a2a") || event.type.includes("notification") || event.type.includes("trigger")) {
      load();
    }
  });

  const runDemo = async () => {
    setRunning(true);
    setDemoResult(null);
    const data = await apiPost<any>("/a2a/demo/risk-flow");
    setDemoResult(`${data.steps} messages sent (trace: ${data.trace_id})`);
    load();
    setRunning(false);
  };

  const loadChain = async () => {
    if (!chainTrace.trim()) return;
    setChainLoading(true);
    setChainData(null);
    try {
      const data = await api<any>(`/a2a/chain/${chainTrace.trim()}`);
      setChainData(data);
    } catch { setChainData({ error: "Failed to load chain" }); }
    setChainLoading(false);
  };

  const runCrossAgentReport = async () => {
    setReportLoading(true);
    setReportResult(null);
    try {
      const data = await apiPost<any>("/reports/compose/cross-agent", {
        agent_types: ["asset", "stock"],
        report_type: "cross_agent_summary",
        trace_id: `tr-ui-report-${Date.now()}`,
      });
      setReportResult(data);
    } catch (e: any) { setReportResult({ error: e.message || "Failed" }); }
    setReportLoading(false);
    load();
  };

  const runDataRequest = async (requester: string, targetType: string) => {
    setDataReqLoading(true);
    setDataReqResult(null);
    try {
      const data = await apiPost<any>("/a2a/request-data", {
        requester_agent_id: requester,
        target_agent_type: targetType,
        trace_id: `tr-ui-req-${Date.now()}`,
        data_request: `${targetType}_summary`,
      });
      setDataReqResult(data);
    } catch (e: any) { setDataReqResult({ error: e.message || "Failed" }); }
    setDataReqLoading(false);
    load();
  };

  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: "messages", label: "Messages", count: messages.length },
    { key: "notifications", label: "Notifications", count: notifications.length },
    { key: "triggers", label: "Triggers", count: triggers.length },
    { key: "chain", label: "Trace Chain" },
  ];

  return (
    <div>
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-6 gap-3">
        <div>
          <h1 className="text-[28px] font-semibold tracking-tight mb-1">A2A Monitor</h1>
          <p className="text-[14px] text-[var(--text-muted)]">Inter-agent communication & notifications</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {busStatus && (
            <span className="text-[10px] px-2 py-1 rounded-full bg-[var(--bg-elevated)] text-[var(--text-secondary)]">
              Bus: {busStatus.event_bus} | Triggers: {busStatus.triggers_count || 0}
            </span>
          )}
          <button onClick={runDemo} disabled={running}
            className="px-3 py-2 rounded-lg bg-[var(--error)] hover:bg-red-600 text-white text-[12px] font-semibold disabled:opacity-50 transition-colors">
            {running ? "Running..." : "Risk Alert Demo"}
          </button>
        </div>
      </div>

      {demoResult && (
        <div className="mb-4 px-4 py-2 rounded bg-[var(--bg-elevated)] border border-[var(--border-default)] text-xs text-[var(--text-primary)]">{demoResult}</div>
      )}

      {/* Action Buttons */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <button onClick={runCrossAgentReport} disabled={reportLoading}
          className="px-4 py-3 rounded-lg bg-[var(--brand-blue)] hover:opacity-90 text-white text-[13px] font-semibold disabled:opacity-50 transition-colors">
          {reportLoading ? "Generating..." : "Cross-Agent Report"}
        </button>
        <button onClick={async () => { setDataReqLoading(true); setDataReqResult(null); try { const d = await apiPost<any>("/a2a/demo/round-trip"); setDataReqResult(d); } catch (e: any) { setDataReqResult({ error: e.message }); } setDataReqLoading(false); load(); }} disabled={dataReqLoading}
          className="px-4 py-3 rounded-lg bg-green-600 hover:bg-green-700 text-white text-[13px] font-semibold disabled:opacity-50 transition-colors">
          {dataReqLoading ? "Testing..." : "Round-Trip Test"}
        </button>
        <button onClick={() => runDataRequest("Stock Agent", "asset")} disabled={dataReqLoading}
          className="px-4 py-3 rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)] text-[var(--text-primary)] text-[13px] font-semibold disabled:opacity-50 transition-colors">
          {dataReqLoading ? "..." : "Stock → Asset"}
        </button>
        <button onClick={() => runDataRequest("Asset Agent", "stock")} disabled={dataReqLoading}
          className="px-4 py-3 rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)] text-[var(--text-primary)] text-[13px] font-semibold disabled:opacity-50 transition-colors">
          {dataReqLoading ? "..." : "Asset → Stock"}
        </button>
      </div>

      {/* Report Result */}
      {reportResult && (
        <div className="mb-4 p-4 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-default)]">
          <h3 className="text-[14px] font-semibold text-[var(--text-primary)] mb-2">
            {reportResult.error ? "Report Error" : "Cross-Agent Report"}
          </h3>
          {reportResult.error ? (
            <p className="text-[12px] text-[var(--error)]">{reportResult.error}</p>
          ) : (
            <div className="text-[12px] text-[var(--text-secondary)] space-y-1">
              <p><strong>Summary:</strong> {reportResult.executive_summary}</p>
              <p><strong>Agents:</strong> {(reportResult.agent_types || []).join(", ")}</p>
              <p><strong>A2A Chain:</strong> {(reportResult.a2a_message_chain || []).length} messages</p>
              <p className="text-[var(--text-muted)]">Report ID: {reportResult.report_id}</p>
            </div>
          )}
        </div>
      )}

      {/* Data Request Result */}
      {dataReqResult && (
        <div className="mb-4 p-4 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-default)]">
          <h3 className="text-[14px] font-semibold text-[var(--text-primary)] mb-2">
            {dataReqResult.error ? "Request Error" : "Data Request Result"}
          </h3>
          {dataReqResult.error ? (
            <p className="text-[12px] text-[var(--error)]">{dataReqResult.error}</p>
          ) : (
            <div className="text-[12px] text-[var(--text-secondary)] space-y-1">
              <p><strong>{dataReqResult.requester} → {dataReqResult.target}</strong></p>
              <p><strong>Success:</strong> {dataReqResult.success ? "Yes" : "No"}</p>
              {dataReqResult.summary && <p><strong>Summary:</strong> {dataReqResult.summary}</p>}
              <p className="text-[var(--text-muted)]">Chain: {(dataReqResult.a2a_chain || []).join(" → ")}</p>
            </div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b border-[var(--border-default)]">
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-[13px] font-medium border-b-2 transition-colors ${
              tab === t.key
                ? "border-[var(--brand-blue)] text-[var(--brand-blue)]"
                : "border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}>
            {t.label}{t.count !== undefined ? ` (${t.count})` : ""}
          </button>
        ))}
      </div>

      {/* Messages Tab */}
      {tab === "messages" && (
        <div className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)]">
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-[var(--text-muted)] text-[12px] font-medium border-b border-[var(--border-default)] bg-[var(--bg-elevated)]">
                  <th className="w-6 px-2"></th>
                  <th className="text-left px-4 py-3">Type</th>
                  <th className="text-left px-4 py-3">Sender</th>
                  <th className="text-left px-4 py-3">Target</th>
                  <th className="text-left px-4 py-3">Risk</th>
                  <th className="text-left px-4 py-3">Status</th>
                  <th className="text-left px-4 py-3">Trace</th>
                  <th className="text-left px-4 py-3">Time</th>
                </tr>
              </thead>
              <tbody>
                {messages.map((m: any) => {
                  const isHighRisk = m.envelope?.is_high_risk === true;
                  const isExpanded = expandedMsg === m.id;
                  const payload = m.envelope?.payload || {};
                  const purpose = m.envelope?.purpose || "";
                  const proofReason = m.envelope?.proof_of_intent?.reason || "";

                  return (
                    <React.Fragment key={m.id}>
                      <tr className={`border-b border-[var(--border-default)] hover:bg-[var(--bg-hover)] cursor-pointer ${isHighRisk ? "bg-[var(--badge-error-bg)]" : ""} ${isExpanded ? "bg-[var(--bg-elevated)]" : ""}`}
                        onClick={() => setExpandedMsg(isExpanded ? null : m.id)}>
                        <td className="px-2 text-center">
                          <svg className={`w-3.5 h-3.5 text-[var(--text-muted)] transition-transform inline-block ${isExpanded ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                          </svg>
                        </td>
                        <td className="px-4 py-3"><Badge text={m.message_type} /></td>
                        <td className="px-4 py-3 text-[var(--brand-blue)] font-medium">{m.sender_agent}</td>
                        <td className="px-4 py-3 text-[var(--text-primary)]">{m.target_agent}</td>
                        <td className="px-4 py-3">
                          {isHighRisk
                            ? <span className="text-[12px] px-2.5 py-1 rounded-full text-[var(--error)] bg-[var(--badge-error-bg)] font-semibold">HIGH</span>
                            : <span className="text-[12px] text-[var(--text-muted)]">—</span>}
                        </td>
                        <td className="px-4 py-3"><Badge text={m.status} /></td>
                        <td className="px-4 py-3 text-[var(--text-muted)] font-mono text-[11px]">{m.trace_id}</td>
                        <td className="px-4 py-3 text-[var(--text-muted)]">{m.created_at ? new Date(m.created_at).toLocaleTimeString() : "-"}</td>
                      </tr>

                      {/* Expanded detail row */}
                      {isExpanded && (
                        <tr>
                          <td colSpan={8} className="px-6 py-4 bg-[var(--bg-elevated)] border-b border-[var(--border-default)]">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                              {/* Left: Message content */}
                              <div>
                                {proofReason && (
                                  <div className="mb-3">
                                    <p className="text-[11px] text-[var(--text-muted)] font-medium mb-1">Reason</p>
                                    <p className="text-[13px] text-[var(--text-primary)] leading-relaxed bg-[var(--bg-card)] rounded-lg p-3 border border-[var(--border-default)]">
                                      {proofReason}
                                    </p>
                                  </div>
                                )}
                                {purpose && (
                                  <div className="mb-3">
                                    <p className="text-[11px] text-[var(--text-muted)] font-medium mb-1">Purpose</p>
                                    <Badge text={purpose} />
                                  </div>
                                )}
                                <div>
                                  <p className="text-[11px] text-[var(--text-muted)] font-medium mb-1">Payload</p>
                                  <div className="bg-[var(--bg-card)] rounded-lg p-3 border border-[var(--border-default)] text-[12px] text-[var(--text-secondary)] space-y-1">
                                    {Object.entries(payload).map(([k, v]) => (
                                      <div key={k} className="flex gap-2">
                                        <span className="text-[var(--text-muted)] shrink-0 min-w-[100px]">{k}:</span>
                                        <span className="text-[var(--text-primary)] font-medium">
                                          {typeof v === "object" ? JSON.stringify(v) : String(v)}
                                        </span>
                                      </div>
                                    ))}
                                    {Object.keys(payload).length === 0 && (
                                      <span className="text-[var(--text-muted)]">No payload data</span>
                                    )}
                                  </div>
                                </div>
                              </div>

                              {/* Right: Actions */}
                              <div className="space-y-2">
                                <div>
                                  <p className="text-[11px] text-[var(--text-muted)] font-medium mb-1">Message ID</p>
                                  <code className="text-[11px] text-[var(--text-muted)] bg-[var(--bg-card)] px-2 py-1 rounded border border-[var(--border-default)]">{m.id}</code>
                                </div>
                                <div className="flex gap-2 mt-3">
                                  <button onClick={(e) => { e.stopPropagation(); setChainTrace(m.trace_id); setTab("chain"); }}
                                    className="px-3 py-1.5 text-[11px] rounded-lg bg-[var(--brand-blue)] text-white font-medium hover:opacity-90">
                                    View Full Chain
                                  </button>
                                  <button onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(JSON.stringify(m.envelope, null, 2)); }}
                                    className="px-3 py-1.5 text-[11px] rounded-lg bg-[var(--bg-card)] border border-[var(--border-default)] text-[var(--text-primary)] font-medium hover:bg-[var(--bg-hover)]">
                                    Copy JSON
                                  </button>
                                </div>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
            {messages.length === 0 && <p className="text-center text-[var(--text-muted)] py-8 text-xs">No A2A messages yet. Use the buttons above to generate.</p>}
          </div>
        </div>
      )}

      {/* Notifications Tab */}
      {tab === "notifications" && (
        <div className="space-y-3">
          {notifications.length === 0 && (
            <p className="text-center text-[var(--text-muted)] py-8 text-xs">No notifications yet. Run a Risk Alert Demo to generate.</p>
          )}
          {notifications.map((n: any) => {
            const sevColors: Record<string, string> = {
              critical: "border-l-[var(--error)] bg-[var(--badge-error-bg)]",
              warning: "border-l-[var(--warning)] bg-[var(--badge-warning-bg)]",
              info: "border-l-[var(--brand-blue)] bg-[var(--bg-elevated)]",
            };
            const sev = n.severity || "info";
            return (
              <div key={n.id} className={`p-4 rounded-lg border border-[var(--border-default)] border-l-4 ${sevColors[sev] || sevColors.info}`}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[13px] font-semibold text-[var(--text-primary)]">{n.title}</span>
                  <span className="text-[11px] text-[var(--text-muted)]">{n.timestamp ? new Date(n.timestamp).toLocaleString() : ""}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Badge text={sev} />
                  <span className="text-[11px] text-[var(--text-muted)] font-mono">{n.trace_id}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Triggers Tab */}
      {tab === "triggers" && (
        <div className="space-y-3">
          {triggers.map((t: any, i: number) => (
            <div key={i} className="p-4 rounded-lg border border-[var(--border-default)] bg-[var(--bg-card)]">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[13px] font-semibold text-[var(--text-primary)]">{t.name}</span>
                <Badge text={t.action} />
              </div>
              <p className="text-[12px] text-[var(--text-secondary)] mb-2">{t.description}</p>
              <div className="flex items-center gap-2">
                <span className="text-[11px] px-2 py-0.5 rounded bg-[var(--bg-elevated)] text-[var(--text-muted)] font-mono">{t.event_channel}</span>
                {t.action_config?.target_type && (
                  <span className="text-[11px] text-[var(--text-muted)]">Target: {t.action_config.target_type}</span>
                )}
              </div>
            </div>
          ))}
          {triggers.length === 0 && <p className="text-center text-[var(--text-muted)] py-8 text-xs">No triggers loaded.</p>}
        </div>
      )}

      {/* Chain Tab */}
      {tab === "chain" && (
        <div>
          <div className="flex gap-2 mb-4">
            <input value={chainTrace} onChange={e => setChainTrace(e.target.value)}
              placeholder="Enter trace_id (e.g., tr-risk-demo-...)"
              className="flex-1 px-3 py-2 rounded-lg border border-[var(--border-default)] bg-[var(--bg-card)] text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)]" />
            <button onClick={loadChain} disabled={chainLoading}
              className="px-4 py-2 rounded-lg bg-[var(--brand-blue)] text-white text-[13px] font-semibold disabled:opacity-50">
              {chainLoading ? "Loading..." : "Load Chain"}
            </button>
          </div>

          {chainData && !chainData.error && (
            <div>
              <div className="flex items-center gap-4 mb-4 text-[12px] text-[var(--text-muted)]">
                <span>Messages: {chainData.total_messages}</span>
                <span>Agents: {(chainData.agents_involved || []).join(", ")}</span>
                <span>Pairs: {(chainData.request_response_pairs || []).length}</span>
              </div>

              <div className="space-y-2">
                {(chainData.messages || []).map((m: any, i: number) => (
                  <div key={i} className={`flex items-center gap-3 p-3 rounded-lg border border-[var(--border-default)] ${
                    m.direction === "inbound" ? "bg-[var(--bg-elevated)]" : "bg-[var(--bg-card)]"
                  }`}>
                    <div className="text-[18px]">{m.direction === "inbound" ? "⬅" : "➡"}</div>
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-[12px] font-semibold text-[var(--brand-blue)]">{m.sender}</span>
                        <span className="text-[11px] text-[var(--text-muted)]">→</span>
                        <span className="text-[12px] font-medium text-[var(--text-primary)]">{m.target}</span>
                        <Badge text={m.message_type} />
                        <Badge text={m.status} />
                      </div>
                      <div className="text-[11px] text-[var(--text-muted)]">
                        {m.created_at ? new Date(m.created_at).toLocaleString() : ""}
                        {m.in_reply_to && <span className="ml-2">reply to: {m.in_reply_to.substring(0, 8)}...</span>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {chainData?.error && (
            <p className="text-center text-[var(--error)] py-4 text-xs">{chainData.error}</p>
          )}

          {!chainData && !chainLoading && (
            <p className="text-center text-[var(--text-muted)] py-8 text-xs">
              Enter a trace_id above or click a message row in the Messages tab to load its chain.
            </p>
          )}
        </div>
      )}

      <AskVIPFloat defaultPrompt="summarize recent A2A activity" />
    </div>
  );
}
