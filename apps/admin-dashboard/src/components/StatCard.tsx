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
    green: "border-green-800/50 bg-green-950/20",
    red: "border-red-800/50 bg-red-950/20",
    yellow: "border-yellow-800/50 bg-yellow-950/20",
    blue: "border-blue-800/50 bg-blue-950/20",
    purple: "border-purple-800/50 bg-purple-950/20",
    gray: "border-gray-800 bg-gray-900/50",
  };
  const valColor = {
    green: "text-green-400",
    red: "text-red-400",
    yellow: "text-yellow-400",
    blue: "text-blue-400",
    purple: "text-purple-400",
    gray: "text-white",
  };
  return (
    <div className={`rounded-lg border p-4 ${colorMap[color]}`}>
      <p className="text-xs text-gray-400 mb-1">{label}</p>
      <p className={`text-2xl font-bold ${valColor[color]}`}>{value}</p>
      {sub && <p className="text-[10px] text-gray-500 mt-1">{sub}</p>}
    </div>
  );
}
