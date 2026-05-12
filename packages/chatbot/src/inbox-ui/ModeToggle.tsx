"use client";

/**
 * ModeToggle — Boss-IN vs Boss-OUT switcher.
 *
 * Boss-IN: chatbot drafts replies, boss approves/sends. Bot learns from corrections.
 * Boss-OUT: chatbot replies autonomously; escalates urgent items via Telegram.
 *
 * Auto-detect (default): time-of-day rule based on Korean working hours
 *   - 09:00-18:00 weekdays → IN
 *   - else → OUT
 * Manual override pins the mode regardless of time.
 */

import type { BossMode } from "./types";

interface Props {
  mode: BossMode;
  autoDetected: boolean;
  onChange: (mode: BossMode, manualOverride: boolean) => void;
}

export function ModeToggle({ mode, autoDetected, onChange }: Props) {
  return (
    <div className="inline-flex items-center gap-2">
      {/* Mode switcher pill */}
      <div className="inline-flex bg-gray-100 rounded-full p-0.5">
        <ModeButton
          active={mode === "in"}
          onClick={() => onChange("in", true)}
          icon="👔"
          label="Boss in"
        />
        <ModeButton
          active={mode === "out"}
          onClick={() => onChange("out", true)}
          icon="🤖"
          label="Boss out"
        />
      </div>

      {/* Auto-detect indicator */}
      {autoDetected ? (
        <span
          className="text-[10px] text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full"
          title="Auto-detected by time (09:00-18:00 weekdays = IN)"
        >
          ● Auto
        </span>
      ) : (
        <button
          onClick={() => onChange(mode, false)}
          className="text-[10px] text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full hover:bg-amber-100"
          title="Switch back to auto-detect"
        >
          ● Manual
        </button>
      )}
    </div>
  );
}

function ModeButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: string;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-medium transition-all ${
        active
          ? "bg-white text-gray-900 shadow-sm"
          : "text-gray-500 hover:text-gray-900"
      }`}
    >
      <span>{icon}</span>
      {label}
    </button>
  );
}

/**
 * Compute the auto-detected mode based on Korean working hours.
 * Mon-Fri 09:00-18:00 KST = IN, otherwise OUT.
 */
export function autoDetectMode(now: Date = new Date()): BossMode {
  // Convert to KST (UTC+9)
  const utcHour = now.getUTCHours();
  const utcDay = now.getUTCDay(); // 0=Sun, 6=Sat
  const kstHour = (utcHour + 9) % 24;
  // Day shift: if UTC hour is 15+, we're already next day in KST
  const kstDay = utcHour >= 15 ? (utcDay + 1) % 7 : utcDay;
  const isWeekday = kstDay >= 1 && kstDay <= 5;
  const isWorkingHour = kstHour >= 9 && kstHour < 18;
  return isWeekday && isWorkingHour ? "in" : "out";
}
