"use client";

import Link from "next/link";
import Badge from "./Badge";

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

function CardWrapper({ children, traceId, linkedIds, linkTo }: {
  children: React.ReactNode;
  traceId?: string;
  linkedIds?: Record<string, unknown>;
  linkTo?: string;
}) {
  return (
    <div className="mt-2 border border-[var(--border-default)]/50 rounded-lg bg-[var(--bg-elevated)] overflow-hidden">
      {children}
      {(traceId || linkedIds || linkTo) && (
        <div className="px-3 py-1.5 border-t border-[var(--border-default)]/30 flex items-center justify-between">
          <div className="flex gap-2">
            {traceId && <span className="text-[8px] font-mono text-[var(--text-muted)]">{traceId}</span>}
            {linkedIds && Object.entries(linkedIds).map(([k, v]) => (
              typeof v === "string" && (
                <span key={k} className="text-[8px] font-mono text-[var(--text-muted)]">{k}: {String(v).slice(0, 8)}...</span>
              )
            ))}
          </div>
          {linkTo && (
            <Link href={linkTo} className="text-[9px] text-[var(--brand-blue)] hover:underline">
              View details →
            </Link>
          )}
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, color = "white" }: { label: string; value: string | number; color?: string }) {
  const colors: Record<string, string> = { green: "text-green-400", red: "text-red-400", yellow: "text-[var(--brand-blue)]", blue: "text-blue-400", white: "text-[var(--text-primary)]" };
  return (
    <div className="text-center">
      <p className={`text-lg font-bold ${colors[color] || colors.white}`}>{value}</p>
      <p className="text-[9px] text-[var(--text-muted)]">{label}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1. Status Summary Card
// ---------------------------------------------------------------------------

export function StatusCard({ data, traceId }: { data: any; traceId?: string }) {
  return (
    <CardWrapper traceId={traceId} linkTo="/">
      <div className="px-3 py-2 border-b border-[var(--border-default)]/30 flex items-center gap-2">
        <div className="w-2 h-2 rounded-full bg-green-400" />
        <span className="text-[10px] font-medium text-[var(--text-primary)]">System Status</span>
      </div>
      <div className="px-3 py-3 grid grid-cols-4 gap-2">
        <Metric label="Agents" value={data.agents_active || data.agents || 0} color="blue" />
        <Metric label="Completed" value={data.runs_completed || 0} color="green" />
        <Metric label="Active" value={data.runs_active || 0} color="yellow" />
        <Metric label="Failed" value={data.runs_failed || 0} color="red" />
      </div>
      {(data.pending_judgement || 0) > 0 && (
        <div className="px-3 pb-2">
          <span className="text-[9px] px-2 py-0.5 rounded-full text-orange-400 bg-orange-900/30">{data.pending_judgement} pending review(s)</span>
        </div>
      )}
    </CardWrapper>
  );
}

// ---------------------------------------------------------------------------
// 2. Agent List Card
// ---------------------------------------------------------------------------

export function AgentListCard({ data, traceId }: { data: any; traceId?: string }) {
  const agents = data.agents || [];
  return (
    <CardWrapper traceId={traceId} linkTo="/agents">
      <div className="px-3 py-2 border-b border-[var(--border-default)]/30 flex items-center justify-between">
        <span className="text-[10px] font-medium text-[var(--text-primary)]">Agents ({data.count || agents.length})</span>
        {(data.unhealthy || 0) > 0 && <span className="text-[9px] text-red-400">{data.unhealthy} unhealthy</span>}
      </div>
      <div className="divide-y divide-gray-800/30">
        {agents.slice(0, 6).map((a: any) => (
          <div key={a.name} className="px-3 py-1.5 flex items-center justify-between text-[10px]">
            <div className="flex items-center gap-2">
              <div className={`w-1.5 h-1.5 rounded-full ${a.status === "active" ? "bg-green-400" : "bg-red-400"}`} />
              <span className="text-[var(--text-primary)]">{a.name}</span>
              {a.is_mock && <span className="text-[var(--text-muted)]">[mock]</span>}
            </div>
            <div className="flex gap-1">
              <span className="text-[var(--text-muted)]">{a.type}</span>
              <span className="text-[var(--text-muted)]">p={a.priority}</span>
            </div>
          </div>
        ))}
      </div>
    </CardWrapper>
  );
}

// ---------------------------------------------------------------------------
// 3. Workflow Result Card
// ---------------------------------------------------------------------------

export function WorkflowResultCard({ data, traceId, linkedIds }: { data: any; traceId?: string; linkedIds?: any }) {
  return (
    <CardWrapper traceId={traceId} linkedIds={linkedIds} linkTo="/workflows">
      <div className="px-3 py-2 border-b border-[var(--border-default)]/30 flex items-center gap-2">
        <span className="text-[10px] font-medium text-[var(--text-primary)]">Workflow Result</span>
        {data.status && <Badge text={data.status} />}
      </div>
      <div className="px-3 py-2 space-y-1 text-[10px]">
        {data.task_type && (
          <div className="flex justify-between text-[var(--text-secondary)]">
            <span>Task</span><span className="text-blue-400">{data.task_type}</span>
          </div>
        )}
        {data.agent && (
          <div className="flex justify-between text-[var(--text-secondary)]">
            <span>Agent</span><span className="text-[var(--text-primary)]">{data.agent}</span>
          </div>
        )}
        {data.source_runs !== undefined && (
          <div className="flex justify-between text-[var(--text-secondary)]">
            <span>Source Runs</span><span className="text-[var(--text-primary)]">{data.source_runs}</span>
          </div>
        )}
        {data.report_type && (
          <div className="flex justify-between text-[var(--text-secondary)]">
            <span>Report</span><span className="text-purple-400">{data.report_type}</span>
          </div>
        )}
      </div>
    </CardWrapper>
  );
}

// ---------------------------------------------------------------------------
// 4. Report Summary Card
// ---------------------------------------------------------------------------

export function ReportSummaryCard({ data, traceId, linkedIds }: { data: any; traceId?: string; linkedIds?: any }) {
  return (
    <CardWrapper traceId={traceId} linkedIds={linkedIds} linkTo="/reports">
      <div className="px-3 py-2 border-b border-[var(--border-default)]/30 flex items-center gap-2">
        <span className="text-[10px] font-medium text-[var(--text-primary)]">Report</span>
        {data.report_type && <Badge text={data.report_type} />}
      </div>
      <div className="px-3 py-2">
        {data.sections && (
          <div className="flex flex-wrap gap-1 mb-2">
            {data.sections.map((s: string) => (
              <span key={s} className="text-[8px] px-1.5 py-0.5 bg-gray-700/50 rounded text-[var(--text-secondary)]">{s}</span>
            ))}
          </div>
        )}
        {data.source_run_count !== undefined && (
          <span className="text-[9px] text-[var(--text-muted)]">{data.source_run_count} source runs</span>
        )}
      </div>
    </CardWrapper>
  );
}

// ---------------------------------------------------------------------------
// 5. Approval Result Card
// ---------------------------------------------------------------------------

export function ApprovalResultCard({ data, traceId, linkedIds, onAction }: { data: any; traceId?: string; linkedIds?: any; onAction?: (msg: string) => void }) {
  const cases = data.cases || [];
  const actionTaken = data.action_taken;

  // If this is a result of an action (approve/reject), show confirmation
  if (actionTaken) {
    return (
      <CardWrapper traceId={traceId} linkedIds={linkedIds} linkTo="/judgement">
        <div className={`px-3 py-3 flex items-center gap-2 ${actionTaken === "approve" ? "bg-green-950/20" : "bg-red-950/20"}`}>
          <span className="text-sm">{actionTaken === "approve" ? "✅" : "❌"}</span>
          <div>
            <p className="text-[10px] font-medium text-[var(--text-primary)]">Case {actionTaken === "approve" ? "Approved" : "Rejected"}</p>
            <div className="flex gap-2 mt-1">
              {data.risk_score !== undefined && <span className="text-[9px] text-[var(--text-muted)]">Risk: {data.risk_score}%</span>}
              {data.rule_result && <span className="text-[9px] text-[var(--text-muted)]">Rules: {data.rule_result}</span>}
            </div>
            {data.failed_rules?.length > 0 && (
              <p className="text-[8px] text-red-400 mt-0.5">Failed: {data.failed_rules.join(", ")}</p>
            )}
          </div>
        </div>
      </CardWrapper>
    );
  }

  return (
    <CardWrapper traceId={traceId} linkedIds={linkedIds} linkTo="/judgement">
      <div className="px-3 py-2 border-b border-[var(--border-default)]/30 flex items-center justify-between">
        <span className="text-[10px] font-medium text-[var(--text-primary)]">
          {data.filter === "high_risk" ? "High Risk Cases" : "Approvals"}
        </span>
        <span className="text-[9px] text-[var(--text-muted)]">{data.count || 0} case(s)</span>
      </div>
      {cases.length > 0 ? (
        <div className="divide-y divide-gray-800/30">
          {cases.slice(0, 5).map((c: any) => (
            <div key={c.id} className="px-3 py-2">
              <div className="flex items-center justify-between text-[10px]">
                <span className="font-mono text-[var(--text-secondary)]">{String(c.id).slice(0, 8)}...</span>
                <div className="flex items-center gap-2">
                  <div className="w-10 h-1 bg-gray-700 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${(c.risk || 0) >= 70 ? "bg-red-500" : (c.risk || 0) >= 40 ? "bg-[var(--brand-blue-deep)]" : "bg-green-500"}`}
                      style={{ width: `${c.risk || 0}%` }} />
                  </div>
                  <span className="text-[var(--text-muted)]">{c.risk || 0}%</span>
                  <Badge text={c.decision} />
                </div>
              </div>
              {c.failed_rules?.length > 0 && (
                <p className="text-[8px] text-red-400 mt-0.5">Rules: {c.failed_rules.join(", ")}</p>
              )}
              {(c.actionable || c.decision === "human_review_required" || c.decision === "conditional_approve") && onAction && (
                <div className="flex gap-1 mt-1.5">
                  <button onClick={() => onAction(`approve ${c.id}`)}
                    className="px-2 py-0.5 text-[9px] rounded bg-green-800 hover:bg-green-700 text-green-200 font-medium">
                    Approve
                  </button>
                  <button onClick={() => onAction(`reject ${c.id}`)}
                    className="px-2 py-0.5 text-[9px] rounded bg-red-800 hover:bg-red-700 text-red-200 font-medium">
                    Reject
                  </button>
                  <button onClick={() => onAction(`explain case ${c.id}`)}
                    className="px-2 py-0.5 text-[9px] rounded bg-gray-700 hover:bg-[var(--bg-hover)] text-[var(--text-primary)] font-medium">
                    Explain
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="px-3 py-3 text-[10px] text-[var(--text-muted)] text-center">No cases found</div>
      )}
    </CardWrapper>
  );
}

// ---------------------------------------------------------------------------
// 6. Judgement Explanation Card
// ---------------------------------------------------------------------------

export function JudgementCard({ data, traceId, linkedIds }: { data: any; traceId?: string; linkedIds?: any }) {
  return (
    <CardWrapper traceId={traceId} linkedIds={linkedIds} linkTo="/judgement">
      <div className="px-3 py-2 border-b border-[var(--border-default)]/30 flex items-center justify-between">
        <span className="text-[10px] font-medium text-[var(--text-primary)]">Judgement</span>
        <div className="flex items-center gap-2">
          {data.risk_score !== undefined && (
            <span className={`text-[10px] font-bold ${data.risk_score >= 70 ? "text-red-400" : data.risk_score >= 40 ? "text-[var(--brand-blue)]" : "text-green-400"}`}>
              Risk: {data.risk_score}%
            </span>
          )}
          {data.decision && <Badge text={data.decision} />}
        </div>
      </div>
      <div className="px-3 py-2 text-[10px] text-[var(--text-secondary)]">
        {data.failed_rules !== undefined && <p>Failed rules: {data.failed_rules}</p>}
        {data.factors !== undefined && <p>Risk factors: {data.factors}</p>}
      </div>
    </CardWrapper>
  );
}

// ---------------------------------------------------------------------------
// 7. A2A List Card
// ---------------------------------------------------------------------------

export function A2AListCard({ data, traceId }: { data: any; traceId?: string }) {
  return (
    <CardWrapper traceId={traceId} linkTo="/a2a">
      <div className="px-3 py-2 border-b border-[var(--border-default)]/30">
        <span className="text-[10px] font-medium text-[var(--text-primary)]">A2A Messages ({data.count || 0})</span>
      </div>
      <div className="px-3 py-1 text-[9px] text-[var(--text-muted)]">Recent inter-agent communication</div>
    </CardWrapper>
  );
}

// ---------------------------------------------------------------------------
// 8. AI Glass Status Card
// ---------------------------------------------------------------------------

export function AIGlassCard({ data, traceId }: { data: any; traceId?: string }) {
  return (
    <CardWrapper traceId={traceId} linkTo="/ai-glass">
      <div className="px-3 py-2 border-b border-[var(--border-default)]/30">
        <span className="text-[10px] font-medium text-[var(--text-primary)]">AI Glass Sessions ({data.count || 0})</span>
      </div>
      <div className="px-3 py-1 text-[9px] text-[var(--text-muted)]">Spatial capture & processing</div>
    </CardWrapper>
  );
}

// ---------------------------------------------------------------------------
// 9. Cross-Agent Analysis Card
// ---------------------------------------------------------------------------

export function CrossAgentCard({ data, traceId, linkedIds }: { data: any; traceId?: string; linkedIds?: any }) {
  const tasks = data.tasks || [];
  const completed = data.tasks_completed || 0;
  const total = data.tasks_total || 0;
  return (
    <CardWrapper traceId={traceId} linkedIds={linkedIds}>
      <div className="px-3 py-2 border-b border-[var(--border-default)]/30 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-[var(--brand-blue)]" />
          <span className="text-[10px] font-medium text-[var(--text-primary)]">{data.workflow_name || "Cross-Agent"}</span>
        </div>
        <span className={`text-[9px] font-bold ${completed === total ? "text-green-400" : "text-[var(--brand-blue)]"}`}>
          {completed}/{total} tasks
        </span>
      </div>
      <div className="divide-y divide-gray-800/30">
        {tasks.map((t: any, i: number) => (
          <div key={i} className="px-3 py-1.5 flex items-center justify-between text-[10px]">
            <div className="flex items-center gap-2">
              <span>{t.status === "completed" ? "✅" : "❌"}</span>
              <span className="text-[var(--text-primary)]">{t.label}</span>
              <span className="text-[var(--text-muted)]">→ {t.agent}</span>
            </div>
            {t.metrics && (
              <div className="flex gap-1">
                {Object.entries(t.metrics).map(([k, v]) =>
                  v !== null && v !== undefined ? (
                    <span key={k} className="text-[8px] px-1 py-0.5 bg-gray-700/50 rounded text-[var(--text-secondary)]">
                      {k.replace(/_/g, " ")}: {typeof v === "number" ? (v % 1 === 0 ? v : (v as number).toFixed(1)) : String(v)}
                    </span>
                  ) : null
                )}
              </div>
            )}
          </div>
        ))}
      </div>
      {data.a2a_count > 0 && (
        <div className="px-3 py-1 text-[9px] text-blue-400 border-t border-[var(--border-default)]/30">
          {data.a2a_count} A2A message(s) sent
        </div>
      )}
      {data.has_report && data.report_summary && (
        <div className="px-3 py-2 border-t border-[var(--border-default)]/30">
          <p className="text-[9px] text-[var(--text-muted)] mb-0.5">Report Summary:</p>
          <p className="text-[9px] text-[var(--text-secondary)] leading-relaxed">{data.report_summary.slice(0, 150)}...</p>
        </div>
      )}
    </CardWrapper>
  );
}

// ---------------------------------------------------------------------------
// Router — picks the right card based on action_result_type or message_type
// ---------------------------------------------------------------------------

export function ChatResponseCard({ message, onAction }: { message: any; onAction?: (msg: string) => void }) {
  const content = message.content || {};
  const data = content.data || {};
  const actionType = content.action_result_type || message.message_type;
  const traceId = content.trace_id;
  const linkedIds = content.linked_object_ids;

  switch (actionType) {
    case "system_status":
      return <StatusCard data={data} traceId={traceId} />;
    case "agent_inspection":
      return <AgentListCard data={data} traceId={traceId} />;
    case "workflow_trigger":
    case "workflow_result":
      return <WorkflowResultCard data={data} traceId={traceId} linkedIds={linkedIds} />;
    case "report_request":
    case "report_summary":
      return <ReportSummaryCard data={data} traceId={traceId} linkedIds={linkedIds} />;
    case "approval_action":
    case "approval_result":
      return <ApprovalResultCard data={data} traceId={traceId} linkedIds={linkedIds} onAction={onAction} />;
    case "judgement_explanation":
      return <JudgementCard data={data} traceId={traceId} linkedIds={linkedIds} />;
    case "a2a_inspection":
      return <A2AListCard data={data} traceId={traceId} />;
    case "aiglass_inspection":
      return <AIGlassCard data={data} traceId={traceId} />;
    case "cross_agent_analysis":
      return <CrossAgentCard data={data} traceId={traceId} linkedIds={linkedIds} />;
    case "report_explainer":
      return <ReportExplainerCard data={data} traceId={traceId} linkedIds={linkedIds} />;
    default:
      return null; // Plain text fallback handled by parent
  }
}

// ---------------------------------------------------------------------------
// 10. Report Explainer Card
// ---------------------------------------------------------------------------

function ReportExplainerCard({ data, traceId, linkedIds }: { data: any; traceId?: string; linkedIds?: any }) {
  return (
    <CardWrapper traceId={traceId} linkedIds={linkedIds} linkTo="/reports">
      <div className="px-3 py-2 border-b border-[var(--border-default)]/30 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-medium text-[var(--text-primary)]">Report Q&A</span>
          {data.question_category && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-900/30 text-purple-400">{data.question_category}</span>
          )}
        </div>
        {data.grounded && <span className="text-[8px] text-green-500">grounded</span>}
      </div>
      {data.sections_used?.length > 0 && (
        <div className="px-3 py-1.5 flex flex-wrap gap-1">
          {data.sections_used.map((s: string) => (
            <span key={s} className="text-[8px] px-1.5 py-0.5 bg-gray-700/50 rounded text-[var(--text-secondary)]">{s}</span>
          ))}
        </div>
      )}
    </CardWrapper>
  );
}
