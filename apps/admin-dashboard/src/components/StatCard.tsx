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
    green: "border-green-200 dark:border-green-800/50 bg-green-50 dark:bg-green-950/20",
    red: "border-red-200 dark:border-red-800/50 bg-red-50 dark:bg-red-950/20",
    yellow: "border-amber-200 dark:border-yellow-800/50 bg-amber-50 dark:bg-yellow-950/20",
    blue: "border-blue-200 dark:border-blue-800/50 bg-blue-50 dark:bg-blue-950/20",
    purple: "border-purple-200 dark:border-purple-800/50 bg-purple-50 dark:bg-purple-950/20",
    gray: "border-gray-200 dark:border-[#2a3142] bg-gray-50 dark:bg-[#1a1f2e]",
  };
  const valColor = {
    green: "text-green-600 dark:text-green-400",
    red: "text-red-600 dark:text-red-400",
    yellow: "text-amber-600 dark:text-yellow-400",
    blue: "text-blue-600 dark:text-blue-400",
    purple: "text-purple-600 dark:text-purple-400",
    gray: "text-gray-800 dark:text-white",
  };
  return (
    <div className={`rounded-xl border p-4 ${colorMap[color]}`}>
      <p className="text-xs text-[#4a5568] dark:text-[#9aa5b4] mb-1 font-medium">{label}</p>
      <p className={`text-2xl font-bold ${valColor[color]}`}>{value}</p>
      {sub && <p className="text-[10px] text-[#9ca3af] dark:text-[#5c6a7e] mt-1">{sub}</p>}
    </div>
  );
}
