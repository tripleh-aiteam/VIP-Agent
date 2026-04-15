const styles: Record<string, string> = {
  active: "text-[#12B76A] bg-[#12B76A]/10",
  completed: "text-[#12B76A] bg-[#12B76A]/10",
  auto_approve: "text-[#12B76A] bg-[#12B76A]/10",
  approved: "text-[#12B76A] bg-[#12B76A]/10",
  healthy: "text-[#12B76A] bg-[#12B76A]/10",
  enabled: "text-[#12B76A] bg-[#12B76A]/10",
  pending: "text-[#F79009] bg-[#F79009]/10",
  conditional_approve: "text-[#F79009] bg-[#F79009]/10",
  dispatched: "text-[#1B96FF] bg-[#1B96FF]/10",
  running: "text-[#1B96FF] bg-[#1B96FF]/10",
  sent: "text-[#1B96FF] bg-[#1B96FF]/10",
  failed: "text-[#F04438] bg-[#F04438]/10",
  rejected: "text-[#F04438] bg-[#F04438]/10",
  inactive: "text-[#F04438] bg-[#F04438]/10",
  error: "text-[#F04438] bg-[#F04438]/10",
  disabled: "text-[var(--text-muted)] bg-[var(--border-default)]",
  review_required: "text-[#F79009] bg-[#F79009]/10",
  human_review_required: "text-[#F79009] bg-[#F79009]/10",
  risk_alert: "text-[#F04438] bg-[#F04438]/10",
  escalation_request: "text-[#F79009] bg-[#F79009]/10",
  data_request: "text-[#1B96FF] bg-[#1B96FF]/10",
  report_request: "text-[#7F56D9] bg-[#7F56D9]/10",
  report_response: "text-[#7F56D9] bg-[#7F56D9]/10",
  feedback_request: "text-[#5BB0FF] bg-[#5BB0FF]/10",
  mock: "text-[var(--text-muted)] bg-[var(--border-default)]",
  planned: "text-[#7F56D9] bg-[#7F56D9]/10",
};

export default function Badge({ text }: { text: string }) {
  const s = styles[text] || "text-[var(--text-muted)] bg-[var(--border-default)]";
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium ${s}`}>
      {text}
    </span>
  );
}
