"use client";

import React, { useEffect, useState } from "react";
import { api, apiPost } from "@/components/api";
import Badge from "@/components/Badge";
import { AskVIPFloat } from "@/components/AskVIP";
import { useRealtimeEvents } from "@/components/useRealtimeEvents";
import { useLanguage } from "@/components/i18n";

type Tab = "messages" | "notifications" | "triggers" | "chain";

export default function A2APage() {
  const { t } = useLanguage();
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
    setDemoResult(t(`메시지 ${data.steps}건 전송됨 (추적: ${data.trace_id})`, `${data.steps} messages sent (trace: ${data.trace_id})`));
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
    } catch { setChainData({ error: t("체인을 불러오지 못했습니다", "Failed to load chain") }); }
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
    { key: "messages", label: t("메시지", "Messages"), count: messages.length },
    { key: "notifications", label: t("알림", "Notifications"), count: notifications.length },
    { key: "triggers", label: t("트리거", "Triggers"), count: triggers.length },
    { key: "chain", label: t("추적 체인", "Trace Chain") },
  ];

  return (
    <div>
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-6 gap-3">
        <div>
          <h1 className="text-[28px] font-semibold tracking-tight mb-1">{t("A2A 모니터", "A2A Monitor")}</h1>
          <p className="text-[14px] text-[var(--text-muted)]">{t("에이전트 간 통신 및 알림", "Inter-agent communication & notifications")}</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {busStatus && (
            <span className="text-[10px] px-2 py-1 rounded-full bg-[var(--bg-elevated)] text-[var(--text-secondary)]">
              {t("버스", "Bus")}: {busStatus.event_bus} | {t("트리거", "Triggers")}: {busStatus.triggers_count || 0}
            </span>
          )}
          <button onClick={runDemo} disabled={running}
            className="px-3 py-2 rounded-lg bg-[var(--error)] hover:bg-red-600 text-white text-[12px] font-semibold disabled:opacity-50 transition-colors">
            {running ? t("실행 중...", "Running...") : t("위험 경고 데모", "Risk Alert Demo")}
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
          {reportLoading ? t("생성 중...", "Generating...") : t("크로스 에이전트 리포트", "Cross-Agent Report")}
        </button>
        <button onClick={async () => { setDataReqLoading(true); setDataReqResult(null); try { const d = await apiPost<any>("/a2a/demo/round-trip"); setDataReqResult(d); } catch (e: any) { setDataReqResult({ error: e.message }); } setDataReqLoading(false); load(); }} disabled={dataReqLoading}
          className="px-4 py-3 rounded-lg bg-green-600 hover:bg-green-700 text-white text-[13px] font-semibold disabled:opacity-50 transition-colors">
          {dataReqLoading ? t("테스트 중...", "Testing...") : t("왕복 테스트", "Round-Trip Test")}
        </button>
        <button onClick={() => runDataRequest("Stock Agent", "asset")} disabled={dataReqLoading}
          className="px-4 py-3 rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)] text-[var(--text-primary)] text-[13px] font-semibold disabled:opacity-50 transition-colors">
          {dataReqLoading ? "..." : t("주식 → 자산", "Stock → Asset")}
        </button>
        <button onClick={() => runDataRequest("Asset Agent", "stock")} disabled={dataReqLoading}
          className="px-4 py-3 rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)] text-[var(--text-primary)] text-[13px] font-semibold disabled:opacity-50 transition-colors">
          {dataReqLoading ? "..." : t("자산 → 주식", "Asset → Stock")}
        </button>
      </div>

      {/* Report Result */}
      {reportResult && (
        <div className="mb-4 p-4 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-default)]">
          <h3 className="text-[14px] font-semibold text-[var(--text-primary)] mb-2">
            {reportResult.error ? t("리포트 오류", "Report Error") : t("크로스 에이전트 리포트", "Cross-Agent Report")}
          </h3>
          {reportResult.error ? (
            <p className="text-[12px] text-[var(--error)]">{reportResult.error}</p>
          ) : (
            <div className="text-[12px] text-[var(--text-secondary)] space-y-1">
              <p><strong>{t("요약", "Summary")}:</strong> {reportResult.executive_summary}</p>
              <p><strong>{t("에이전트", "Agents")}:</strong> {(reportResult.agent_types || []).join(", ")}</p>
              <p><strong>{t("A2A 체인", "A2A Chain")}:</strong> {(reportResult.a2a_message_chain || []).length} {t("건", "messages")}</p>
              <p className="text-[var(--text-muted)]">{t("리포트 ID", "Report ID")}: {reportResult.report_id}</p>
            </div>
          )}
        </div>
      )}

      {/* Data Request Result */}
      {dataReqResult && (
        <div className="mb-4 p-4 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-default)]">
          <h3 className="text-[14px] font-semibold text-[var(--text-primary)] mb-2">
            {dataReqResult.error ? t("요청 오류", "Request Error") : t("데이터 요청 결과", "Data Request Result")}
          </h3>
          {dataReqResult.error ? (
            <p className="text-[12px] text-[var(--error)]">{dataReqResult.error}</p>
          ) : (
            <div className="text-[12px] text-[var(--text-secondary)] space-y-1">
              <p><strong>{dataReqResult.requester} → {dataReqResult.target}</strong></p>
              <p><strong>{t("성공", "Success")}:</strong> {dataReqResult.success ? t("예", "Yes") : t("아니오", "No")}</p>
              {dataReqResult.summary && <p><strong>{t("요약", "Summary")}:</strong> {dataReqResult.summary}</p>}
              <p className="text-[var(--text-muted)]">{t("체인", "Chain")}: {(dataReqResult.a2a_chain || []).join(" → ")}</p>
            </div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b border-[var(--border-default)]">
        {tabs.map(tabItem => (
          <button key={tabItem.key} onClick={() => setTab(tabItem.key)}
            className={`px-4 py-2 text-[13px] font-medium border-b-2 transition-colors ${
              tab === tabItem.key
                ? "border-[var(--brand-blue)] text-[var(--brand-blue)]"
                : "border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}>
            {tabItem.label}{tabItem.count !== undefined ? ` (${tabItem.count})` : ""}
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
                  <th className="text-left px-4 py-3">{t("유형", "Type")}</th>
                  <th className="text-left px-4 py-3">{t("발신자", "Sender")}</th>
                  <th className="text-left px-4 py-3">{t("대상", "Target")}</th>
                  <th className="text-left px-4 py-3">{t("위험도", "Risk")}</th>
                  <th className="text-left px-4 py-3">{t("상태", "Status")}</th>
                  <th className="text-left px-4 py-3">{t("추적", "Trace")}</th>
                  <th className="text-left px-4 py-3">{t("시간", "Time")}</th>
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
                            ? <span className="text-[12px] px-2.5 py-1 rounded-full text-[var(--error)] bg-[var(--badge-error-bg)] font-semibold">{t("높음", "HIGH")}</span>
                            : <span className="text-[12px] text-[var(--text-muted)]">—</span>}
                        </td>
                        <td className="px-4 py-3"><Badge text={m.status} /></td>
                        <td className="px-4 py-3 text-[var(--text-muted)] font-mono text-[11px]">{m.trace_id}</td>
                        <td className="px-4 py-3 text-[var(--text-muted)]">{m.created_at ? new Date(m.created_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "-"}</td>
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
                                    <p className="text-[11px] text-[var(--text-muted)] font-medium mb-1">{t("사유", "Reason")}</p>
                                    <p className="text-[13px] text-[var(--text-primary)] leading-relaxed bg-[var(--bg-card)] rounded-lg p-3 border border-[var(--border-default)]">
                                      {proofReason}
                                    </p>
                                  </div>
                                )}
                                {purpose && (
                                  <div className="mb-3">
                                    <p className="text-[11px] text-[var(--text-muted)] font-medium mb-1">{t("목적", "Purpose")}</p>
                                    <Badge text={purpose} />
                                  </div>
                                )}
                                <div>
                                  <p className="text-[11px] text-[var(--text-muted)] font-medium mb-1">{t("페이로드", "Payload")}</p>
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
                                      <span className="text-[var(--text-muted)]">{t("페이로드 데이터 없음", "No payload data")}</span>
                                    )}
                                  </div>
                                </div>
                              </div>

                              {/* Right: Actions */}
                              <div className="space-y-2">
                                <div>
                                  <p className="text-[11px] text-[var(--text-muted)] font-medium mb-1">{t("메시지 ID", "Message ID")}</p>
                                  <code className="text-[11px] text-[var(--text-muted)] bg-[var(--bg-card)] px-2 py-1 rounded border border-[var(--border-default)]">{m.id}</code>
                                </div>
                                <div className="flex gap-2 mt-3">
                                  <button onClick={(e) => { e.stopPropagation(); setChainTrace(m.trace_id); setTab("chain"); }}
                                    className="px-3 py-1.5 text-[11px] rounded-lg bg-[var(--brand-blue)] text-white font-medium hover:opacity-90">
                                    {t("전체 체인 보기", "View Full Chain")}
                                  </button>
                                  <button onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(JSON.stringify(m.envelope, null, 2)); }}
                                    className="px-3 py-1.5 text-[11px] rounded-lg bg-[var(--bg-card)] border border-[var(--border-default)] text-[var(--text-primary)] font-medium hover:bg-[var(--bg-hover)]">
                                    {t("JSON 복사", "Copy JSON")}
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
            {messages.length === 0 && <p className="text-center text-[var(--text-muted)] py-8 text-xs">{t("아직 A2A 메시지가 없습니다. 위 버튼을 사용해 생성하세요.", "No A2A messages yet. Use the buttons above to generate.")}</p>}
          </div>
        </div>
      )}

      {/* Notifications Tab */}
      {tab === "notifications" && (
        <div className="space-y-3">
          {notifications.length === 0 && (
            <p className="text-center text-[var(--text-muted)] py-8 text-xs">{t("아직 알림이 없습니다. 위험 경고 데모를 실행해 생성하세요.", "No notifications yet. Run a Risk Alert Demo to generate.")}</p>
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
          {triggers.map((trig: any, i: number) => (
            <div key={i} className="p-4 rounded-lg border border-[var(--border-default)] bg-[var(--bg-card)]">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[13px] font-semibold text-[var(--text-primary)]">{trig.name}</span>
                <Badge text={trig.action} />
              </div>
              <p className="text-[12px] text-[var(--text-secondary)] mb-2">{trig.description}</p>
              <div className="flex items-center gap-2">
                <span className="text-[11px] px-2 py-0.5 rounded bg-[var(--bg-elevated)] text-[var(--text-muted)] font-mono">{trig.event_channel}</span>
                {trig.action_config?.target_type && (
                  <span className="text-[11px] text-[var(--text-muted)]">{t("대상", "Target")}: {trig.action_config.target_type}</span>
                )}
              </div>
            </div>
          ))}
          {triggers.length === 0 && <p className="text-center text-[var(--text-muted)] py-8 text-xs">{t("불러온 트리거가 없습니다.", "No triggers loaded.")}</p>}
        </div>
      )}

      {/* Chain Tab */}
      {tab === "chain" && (
        <div>
          <div className="flex gap-2 mb-4">
            <input value={chainTrace} onChange={e => setChainTrace(e.target.value)}
              placeholder={t("trace_id 입력 (예: tr-risk-demo-...)", "Enter trace_id (e.g., tr-risk-demo-...)")}
              className="flex-1 px-3 py-2 rounded-lg border border-[var(--border-default)] bg-[var(--bg-card)] text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)]" />
            <button onClick={loadChain} disabled={chainLoading}
              className="px-4 py-2 rounded-lg bg-[var(--brand-blue)] text-white text-[13px] font-semibold disabled:opacity-50">
              {chainLoading ? t("불러오는 중...", "Loading...") : t("체인 불러오기", "Load Chain")}
            </button>
          </div>

          {chainData && !chainData.error && (
            <div>
              <div className="flex items-center gap-4 mb-4 text-[12px] text-[var(--text-muted)]">
                <span>{t("메시지", "Messages")}: {chainData.total_messages}</span>
                <span>{t("에이전트", "Agents")}: {(chainData.agents_involved || []).join(", ")}</span>
                <span>{t("쌍", "Pairs")}: {(chainData.request_response_pairs || []).length}</span>
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
                        {m.in_reply_to && <span className="ml-2">{t("회신 대상", "reply to")}: {m.in_reply_to.substring(0, 8)}...</span>}
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
              {t("위에 trace_id를 입력하거나 메시지 탭에서 메시지 행을 클릭하여 체인을 불러오세요.", "Enter a trace_id above or click a message row in the Messages tab to load its chain.")}
            </p>
          )}
        </div>
      )}

      <AskVIPFloat defaultPrompt={t("최근 A2A 활동을 요약해줘", "summarize recent A2A activity")} />
    </div>
  );
}
