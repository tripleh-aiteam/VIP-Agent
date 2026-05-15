"use client";

/**
 * ModeToggle — Boss-IN vs Boss-OUT switcher with reason + auto-expire.
 *
 * Boss-IN: human (boss or worker) is the channel operator. Bot watches + learns.
 *          The composer is fully usable; uploading files/images works.
 * Boss-OUT: bot handles everything autonomously. Manual flip can include a
 *          reason ("In meeting", "Off day", "Vacation", "Other") and an
 *          optional auto-revert timer ("back in 2 hours").
 *
 * UI states:
 *   - Default (auto-detect): two-button pill + green "Auto" badge
 *   - Manual override active: same buttons + amber "Manual" badge + a
 *     status banner BELOW showing reason + countdown to auto-revert
 *
 * The dropdown for choosing a reason only appears when boss clicks
 * "Boss out" — letting them quickly type "back in 1 hour" or pick a
 * preset. For "Boss in" the override is just a manual pin (no reason
 * needed — they're explicitly available).
 */

import { useEffect, useState } from "react";
import type { BossMode } from "./types";

/** Reasons surfaced in the dropdown. Match server-side MODE_REASONS map. */
const REASON_OPTIONS: { value: string; label: string; defaultHours?: number }[] = [
  { value: "meeting", label: "외부 미팅 (Meeting)", defaultHours: 2 },
  { value: "lunch", label: "점심 시간 (Lunch)", defaultHours: 1 },
  { value: "off_day", label: "휴무 (Off day)", defaultHours: 24 },
  { value: "vacation", label: "휴가 (Vacation)" },
  { value: "after_hours", label: "퇴근 (After hours)" },
  { value: "other", label: "기타 (Other)" },
];

interface Props {
  mode: BossMode;
  autoDetected: boolean;
  /** Reason code when manually OUT (matches MODE_REASONS keys). */
  reason?: string;
  /** Free text when reason="other". */
  reasonNote?: string;
  /** Unix ms when override expires (auto-revert to IN). null = indefinite. */
  expiresAt?: number | null;

  /**
   * Fires when boss changes mode.
   *   - manualOverride=false → host should call setBossMode({auto: true})
   *   - manualOverride=true  → host should call setBossMode({mode, reason, expires_in_hours})
   */
  onChange: (
    mode: BossMode,
    manualOverride: boolean,
    options?: { reason?: string; reasonNote?: string; expiresInHours?: number },
  ) => void;
}

export function ModeToggle({
  mode,
  autoDetected,
  reason,
  reasonNote,
  expiresAt,
  onChange,
}: Props) {
  const [reasonModalOpen, setReasonModalOpen] = useState(false);

  const handleOutClick = () => {
    // Open the reason picker so boss can specify why + for how long
    setReasonModalOpen(true);
  };

  return (
    <div className="flex flex-col items-end gap-1.5">
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
            onClick={handleOutClick}
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
            title="Click to return to auto-detect (back-to-time-based mode)"
          >
            ● Manual
          </button>
        )}
      </div>

      {/* Status banner — only shows when manually OUT */}
      {!autoDetected && mode === "out" && (
        <StatusBanner reason={reason} reasonNote={reasonNote} expiresAt={expiresAt} />
      )}

      {/* Reason picker modal */}
      {reasonModalOpen && (
        <ReasonPickerModal
          onConfirm={(opts) => {
            setReasonModalOpen(false);
            onChange("out", true, opts);
          }}
          onCancel={() => setReasonModalOpen(false)}
        />
      )}
    </div>
  );
}

/* ------------------------- atoms ------------------------- */

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

function StatusBanner({
  reason,
  reasonNote,
  expiresAt,
}: {
  reason?: string;
  reasonNote?: string;
  expiresAt?: number | null;
}) {
  const [, force] = useState(0);

  // Tick every minute so the countdown stays accurate
  useEffect(() => {
    if (!expiresAt) return;
    const id = setInterval(() => force((n) => n + 1), 60_000);
    return () => clearInterval(id);
  }, [expiresAt]);

  const reasonLabel =
    reason && REASON_OPTIONS.find((o) => o.value === reason)?.label;
  const display = reasonLabel || (reasonNote ? `Other: ${reasonNote}` : "Manual override");
  const countdown = expiresAt ? formatCountdown(expiresAt) : null;

  return (
    <div className="text-[10px] text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-2 py-1 max-w-[280px]">
      🤖 Bot autonomous · <span className="font-medium">{display}</span>
      {countdown && <span className="text-amber-600"> · auto-back {countdown}</span>}
    </div>
  );
}

function formatCountdown(unixMs: number): string {
  const remainingMs = unixMs - Date.now();
  if (remainingMs <= 0) return "soon";
  const mins = Math.round(remainingMs / 60_000);
  if (mins < 60) return `in ${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `in ${hours}h`;
  const days = Math.round(hours / 24);
  return `in ${days}d`;
}

/* ------------------------- reason picker modal ------------------------- */

function ReasonPickerModal({
  onConfirm,
  onCancel,
}: {
  onConfirm: (opts: { reason?: string; reasonNote?: string; expiresInHours?: number }) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState<string>("meeting");
  const [reasonNote, setReasonNote] = useState("");
  const [hoursInput, setHoursInput] = useState("2");

  const reasonOption = REASON_OPTIONS.find((o) => o.value === reason);
  const defaultHours = reasonOption?.defaultHours;

  // Pre-fill hours when reason changes (use the reason's default)
  useEffect(() => {
    if (defaultHours !== undefined) setHoursInput(String(defaultHours));
  }, [defaultHours]);

  const handleConfirm = () => {
    const parsed = parseFloat(hoursInput);
    onConfirm({
      reason,
      reasonNote: reason === "other" ? reasonNote.trim() || undefined : undefined,
      expiresInHours: parsed > 0 ? parsed : undefined,
    });
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/30 z-[100]"
        onClick={onCancel}
      />
      {/* Dialog */}
      <div
        className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-[101] bg-white rounded-2xl shadow-2xl w-[400px] max-w-[90vw]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-gray-100">
          <h3 className="text-[14px] font-semibold text-gray-900">
            🤖 Switch to Boss-OUT
          </h3>
          <p className="text-[11px] text-gray-500 mt-0.5">
            Bot will handle messages + calls until you return.
          </p>
        </div>

        <div className="px-5 py-4 space-y-3">
          {/* Reason dropdown */}
          <div>
            <label className="block text-[11px] font-medium text-gray-700 mb-1">
              Reason
            </label>
            <select
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-gray-300 text-[13px] focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white"
            >
              {REASON_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

          {/* Free-text note for "other" */}
          {reason === "other" && (
            <div>
              <label className="block text-[11px] font-medium text-gray-700 mb-1">
                Custom reason
              </label>
              <input
                type="text"
                value={reasonNote}
                onChange={(e) => setReasonNote(e.target.value)}
                placeholder="e.g. Hospital visit, family emergency..."
                className="w-full px-3 py-2 rounded-lg border border-gray-300 text-[13px] focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
          )}

          {/* Hours until auto-revert */}
          <div>
            <label className="block text-[11px] font-medium text-gray-700 mb-1">
              Auto-back to Boss-IN after
            </label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min="0"
                step="0.5"
                value={hoursInput}
                onChange={(e) => setHoursInput(e.target.value)}
                className="w-24 px-3 py-2 rounded-lg border border-gray-300 text-[13px] focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
              <span className="text-[12px] text-gray-600">hours</span>
              <span className="text-[10px] text-gray-400">(0 = indefinite)</span>
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="px-5 py-3 border-t border-gray-100 flex items-center justify-end gap-2 bg-gray-50/50">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 rounded-lg text-[12px] font-medium text-gray-700 hover:bg-gray-100 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            className="px-3.5 py-1.5 rounded-lg text-[12px] font-medium bg-amber-600 text-white hover:bg-amber-700 transition-colors"
          >
            🤖 Switch to bot
          </button>
        </div>
      </div>
    </>
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
