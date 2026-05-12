"use client";

interface Tab<T extends string> {
  value: T;
  label: string;
  badge?: string;
  badgeColor?: "red" | "blue" | "gray";
}

interface Props<T extends string> {
  value: T;
  onChange: (v: T) => void;
  options: Tab<T>[];
}

export function TabBar<T extends string>({ value, onChange, options }: Props<T>) {
  return (
    <div className="border-b border-gray-200">
      <div className="flex gap-1">
        {options.map((tab) => {
          const active = tab.value === value;
          return (
            <button
              key={tab.value}
              onClick={() => onChange(tab.value)}
              className={`px-4 py-2.5 text-[13px] font-medium border-b-2 transition-colors -mb-px flex items-center gap-2 ${
                active
                  ? "border-blue-600 text-blue-600"
                  : "border-transparent text-gray-600 hover:text-gray-900"
              }`}
            >
              {tab.label}
              {tab.badge && (
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded-full font-semibold ${getBadgeColor(
                    tab.badgeColor,
                    active,
                  )}`}
                >
                  {tab.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function getBadgeColor(color: "red" | "blue" | "gray" | undefined, active: boolean) {
  if (color === "red") return "bg-red-100 text-red-700";
  if (color === "blue") return active ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-600";
  return "bg-gray-100 text-gray-600";
}
