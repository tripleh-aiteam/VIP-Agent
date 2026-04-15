export default function StatCard({
  label,
  value,
  sub,
  color = "gray",
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: "green" | "red" | "yellow" | "blue" | "purple" | "gray";
}) {
  const colorMap = {
    green: "border-[#12B76A]/20 bg-[#12B76A]/[0.04]",
    red: "border-[#F04438]/20 bg-[#F04438]/[0.04]",
    yellow: "border-[#F79009]/20 bg-[#F79009]/[0.04]",
    blue: "border-[#1B96FF]/20 bg-[#1B96FF]/[0.04]",
    purple: "border-[#7F56D9]/20 bg-[#7F56D9]/[0.04]",
    gray: "border-[var(--border-default)] bg-[var(--bg-card)]",
  };
  const valColor = {
    green: "text-[#12B76A]",
    red: "text-[#F04438]",
    yellow: "text-[#F79009]",
    blue: "text-[#1B96FF]",
    purple: "text-[#7F56D9]",
    gray: "text-[var(--text-primary)]",
  };
  return (
    <div className={`rounded-xl border p-4 ${colorMap[color]}`} style={{ boxShadow: "var(--shadow-sm)" }}>
      <p className="text-[13px] text-[var(--text-muted)] mb-1 font-medium">{label}</p>
      <p className={`text-2xl font-semibold tracking-tight ${valColor[color]}`}>{value}</p>
      {sub && <p className="text-[11px] text-[var(--text-muted)] mt-1">{sub}</p>}
    </div>
  );
}
