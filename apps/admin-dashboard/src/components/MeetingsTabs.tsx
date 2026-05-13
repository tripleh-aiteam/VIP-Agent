"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/meetings", label: "Live" },
  { href: "/meeting-notes", label: "Notes" },
];

export default function MeetingsTabs() {
  const pathname = usePathname();
  return (
    <div className="flex gap-1 mb-6 border-b border-[var(--border-default)]">
      {TABS.map((t) => {
        const active = pathname === t.href;
        return (
          <Link
            key={t.href}
            href={t.href}
            className={`px-4 py-2 text-[13px] font-medium border-b-2 -mb-px transition-colors ${
              active
                ? "border-[var(--text-primary)] text-[var(--text-primary)]"
                : "border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </div>
  );
}
