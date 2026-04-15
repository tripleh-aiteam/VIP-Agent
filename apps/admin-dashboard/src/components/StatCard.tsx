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
  const valColor = {
    green: "text-[var(--brand-green)]",
    red: "text-[var(--error)]",
    yellow: "text-[var(--warning)]",
    blue: "text-[var(--brand-blue)]",
    purple: "text-[var(--brand-purple)]",
    gray: "text-[var(--text-primary)]",
  };
  return (
    <div className="rounded-xl border border-[var(--border-default)] p-4 bg-[var(--bg-card)]" style={{ boxShadow: "var(--shadow-sm)" }}>
      <p className="text-[12px] text-[var(--text-muted)] mb-1 font-medium">{label}</p>
      <p className={`text-[24px] font-semibold tracking-tight ${valColor[color]}`}>{value}</p>
      {sub && <p className="text-[11px] text-[var(--text-muted)] mt-1.5">{sub}</p>}
    </div>
  );
}
