const styles: Record<string, string> = {
  active: "text-[var(--badge-success-text)] bg-[var(--badge-success-bg)]",
  completed: "text-[var(--badge-success-text)] bg-[var(--badge-success-bg)]",
  auto_approve: "text-[var(--badge-success-text)] bg-[var(--badge-success-bg)]",
  approved: "text-[var(--badge-success-text)] bg-[var(--badge-success-bg)]",
  healthy: "text-[var(--badge-success-text)] bg-[var(--badge-success-bg)]",
  enabled: "text-[var(--badge-success-text)] bg-[var(--badge-success-bg)]",
  pending: "text-[var(--badge-warning-text)] bg-[var(--badge-warning-bg)]",
  conditional_approve: "text-[var(--badge-warning-text)] bg-[var(--badge-warning-bg)]",
  dispatched: "text-[var(--badge-blue-text)] bg-[var(--badge-blue-bg)]",
  running: "text-[var(--badge-blue-text)] bg-[var(--badge-blue-bg)]",
  sent: "text-[var(--badge-blue-text)] bg-[var(--badge-blue-bg)]",
  failed: "text-[var(--badge-error-text)] bg-[var(--badge-error-bg)]",
  rejected: "text-[var(--badge-error-text)] bg-[var(--badge-error-bg)]",
  inactive: "text-[var(--badge-error-text)] bg-[var(--badge-error-bg)]",
  error: "text-[var(--badge-error-text)] bg-[var(--badge-error-bg)]",
  disabled: "text-[var(--badge-neutral-text)] bg-[var(--badge-neutral-bg)]",
  review_required: "text-[var(--badge-warning-text)] bg-[var(--badge-warning-bg)]",
  human_review_required: "text-[var(--badge-warning-text)] bg-[var(--badge-warning-bg)]",
  risk_alert: "text-[var(--badge-error-text)] bg-[var(--badge-error-bg)]",
  escalation_request: "text-[var(--badge-warning-text)] bg-[var(--badge-warning-bg)]",
  data_request: "text-[var(--badge-blue-text)] bg-[var(--badge-blue-bg)]",
  report_request: "text-[var(--badge-purple-text)] bg-[var(--badge-purple-bg)]",
  report_response: "text-[var(--badge-purple-text)] bg-[var(--badge-purple-bg)]",
  feedback_request: "text-[var(--badge-blue-text)] bg-[var(--badge-blue-bg)]",
  mock: "text-[var(--badge-neutral-text)] bg-[var(--badge-neutral-bg)]",
  planned: "text-[var(--badge-purple-text)] bg-[var(--badge-purple-bg)]",
};

export default function Badge({ text }: { text: string }) {
  const s = styles[text] || "text-[var(--badge-neutral-text)] bg-[var(--badge-neutral-bg)]";
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium ${s}`}>
      {text}
    </span>
  );
}
