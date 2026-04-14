const styles: Record<string, string> = {
  active: "text-green-400 bg-green-900/30",
  completed: "text-green-400 bg-green-900/30",
  auto_approve: "text-green-400 bg-green-900/30",
  approved: "text-green-400 bg-green-900/30",
  healthy: "text-green-400 bg-green-900/30",
  enabled: "text-green-400 bg-green-900/30",
  pending: "text-yellow-400 bg-yellow-900/30",
  conditional_approve: "text-yellow-400 bg-yellow-900/30",
  dispatched: "text-blue-400 bg-blue-900/30",
  running: "text-blue-400 bg-blue-900/30",
  sent: "text-blue-400 bg-blue-900/30",
  failed: "text-red-400 bg-red-900/30",
  rejected: "text-red-400 bg-red-900/30",
  inactive: "text-red-400 bg-red-900/30",
  error: "text-red-400 bg-red-900/30",
  disabled: "text-gray-500 bg-gray-800",
  review_required: "text-orange-400 bg-orange-900/30",
  human_review_required: "text-orange-400 bg-orange-900/30",
  risk_alert: "text-red-400 bg-red-900/30",
  escalation_request: "text-orange-400 bg-orange-900/30",
  data_request: "text-blue-400 bg-blue-900/30",
  report_request: "text-purple-400 bg-purple-900/30",
  report_response: "text-purple-400 bg-purple-900/30",
  feedback_request: "text-cyan-400 bg-cyan-900/30",
};

export default function Badge({ text }: { text: string }) {
  const s = styles[text] || "text-gray-400 bg-gray-800";
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${s}`}>
      {text}
    </span>
  );
}
