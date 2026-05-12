"use client";

/**
 * OutboundCallForm — operator initiates a bot-driven outbound call.
 *
 * Agent-driven via `reasons`: VIP supplies rent/viewing reasons; Real
 * Estate or Health can ship a completely different catalog. The script
 * preview comes from the selected reason's `scriptTemplate`, with
 * placeholders `{name}`, `{amount}`, `{dueDate}`, etc. filled from the
 * draft. No reason labels live in this component.
 */

import { useMemo, useState } from "react";
import type { Lang } from "../types";
import type { VoiceOutboundReason } from "../types";
import type { OutboundCallDraft } from "./types";

type FormStatus = "idle" | "submitting" | "success" | "error";

interface Props {
  /** Reason catalog from `AgentConfig.voice.outboundReasons` — required. */
  reasons: VoiceOutboundReason[];
  /** Language for labels + script preview. Defaults to first language in the reason's templates. */
  language?: Lang;
  /** Fires when the operator clicks "Call now" / "Schedule" — host wires the actual API call. */
  onSubmit?: (draft: OutboundCallDraft) => Promise<void> | void;
}

export function OutboundCallForm({ reasons, language = "ko", onSubmit }: Props) {
  const defaultReasonId = reasons[0]?.id ?? "";

  const [draft, setDraft] = useState<OutboundCallDraft>({
    to: "",
    callerName: "",
    reason: defaultReasonId,
    scheduledFor: undefined,
  });
  const [whenMode, setWhenMode] = useState<"now" | "schedule">("now");
  const [scheduleAt, setScheduleAt] = useState("");
  const [status, setStatus] = useState<FormStatus>("idle");

  const scriptPreview = useMemo(
    () => buildScriptPreview(draft, reasons, language),
    [draft, reasons, language],
  );

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!isValidNumber(draft.to)) {
      setStatus("error");
      return;
    }
    setStatus("submitting");
    try {
      if (onSubmit) {
        await onSubmit({
          ...draft,
          scheduledFor: whenMode === "schedule" && scheduleAt ? scheduleAt : undefined,
        });
      } else {
        // No host handler — simulate the original mock behavior so the UI is
        // still demoable in design-time / Storybook mode.
        await new Promise((r) => setTimeout(r, 900));
      }
      setStatus("success");
      setTimeout(() => setStatus("idle"), 3500);
    } catch {
      setStatus("error");
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-2xl border border-gray-200 bg-white shadow-sm overflow-hidden"
    >
      {/* Header */}
      <div className="bg-gradient-to-r from-blue-50 to-purple-50 px-5 py-4 border-b border-gray-100">
        <div className="flex items-center gap-2">
          <span className="text-xl">📤</span>
          <h3 className="text-base font-semibold text-gray-900">Outbound Call</h3>
        </div>
        <p className="text-[12px] text-gray-500 mt-1">
          The AI will place this call. It will identify itself as an AI in the first sentence.
        </p>
      </div>

      <div className="p-5 space-y-4">
        {/* Phone number */}
        <div>
          <label className="block text-[12px] font-medium text-gray-700 mb-1.5">To</label>
          <input
            type="tel"
            placeholder="+82-10-XXXX-XXXX"
            value={draft.to}
            onChange={(e) => setDraft({ ...draft, to: e.target.value })}
            className="w-full px-3 py-2 rounded-lg border border-gray-300 text-[13px] focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono"
            required
          />
          <p className="text-[10px] text-gray-400 mt-1">
            Required format: Korean 010-XXXX-XXXX (international format +82-...)
          </p>
        </div>

        {/* Caller name (optional) */}
        <div>
          <label className="block text-[12px] font-medium text-gray-700 mb-1.5">Caller name <span className="text-gray-400 font-normal">(optional)</span></label>
          <input
            type="text"
            placeholder="김임차 (Lease #L1-040)"
            value={draft.callerName}
            onChange={(e) => setDraft({ ...draft, callerName: e.target.value })}
            className="w-full px-3 py-2 rounded-lg border border-gray-300 text-[13px] focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>

        {/* Reason */}
        <div>
          <label className="block text-[12px] font-medium text-gray-700 mb-1.5">Reason for call</label>
          <select
            value={draft.reason}
            onChange={(e) => setDraft({ ...draft, reason: e.target.value })}
            className="w-full px-3 py-2 rounded-lg border border-gray-300 text-[13px] focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white"
          >
            {reasons.map((r) => (
              <option key={r.id} value={r.id}>{labelFor(r, language)}</option>
            ))}
          </select>
        </div>

        {/* Script preview */}
        <div>
          <label className="block text-[12px] font-medium text-gray-700 mb-1.5">Script preview</label>
          <div className="px-3 py-2.5 rounded-lg bg-gray-50 border border-gray-200 text-[12px] text-gray-700 leading-relaxed whitespace-pre-line">
            {scriptPreview || <span className="italic text-gray-400">No script template configured for this reason.</span>}
          </div>
        </div>

        {/* When */}
        <div>
          <label className="block text-[12px] font-medium text-gray-700 mb-1.5">When</label>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="when"
                checked={whenMode === "now"}
                onChange={() => setWhenMode("now")}
                className="w-3.5 h-3.5"
              />
              <span className="text-[13px] text-gray-700">Now</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="when"
                checked={whenMode === "schedule"}
                onChange={() => setWhenMode("schedule")}
                className="w-3.5 h-3.5"
              />
              <span className="text-[13px] text-gray-700">Schedule</span>
            </label>
            {whenMode === "schedule" && (
              <input
                type="datetime-local"
                value={scheduleAt}
                onChange={(e) => setScheduleAt(e.target.value)}
                className="flex-1 px-3 py-1.5 rounded-lg border border-gray-300 text-[12px] focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            )}
          </div>
          <p className="text-[10px] text-gray-400 mt-1.5">
            Outbound calls are restricted to 09:00–21:00 Korea time. Hard limit: max 1 call per
            recipient per week.
          </p>
        </div>

        {/* Status messages */}
        {status === "success" && (
          <div className="p-3 rounded-lg bg-green-50 border border-green-200 text-[12px] text-green-700">
            ✓ {whenMode === "now" ? "Calling now…" : `Scheduled for ${scheduleAt}.`} You&apos;ll see it appear in the Live tab.
          </div>
        )}
        {status === "error" && (
          <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-[12px] text-red-700">
            ✗ Phone number invalid. Use Korean format like 010-1234-5678 or +82-10-1234-5678.
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="px-5 py-3 border-t border-gray-100 flex items-center justify-end gap-2 bg-gray-50/50">
        <button
          type="button"
          className="px-4 py-2 rounded-lg text-[12px] font-medium text-gray-700 hover:bg-gray-100 transition-colors"
          onClick={() => {
            setDraft({ to: "", callerName: "", reason: defaultReasonId });
            setScheduleAt("");
            setStatus("idle");
          }}
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={status === "submitting"}
          className="px-4 py-2 rounded-lg text-[12px] font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-60 flex items-center gap-1.5"
        >
          {status === "submitting" ? (
            <>⏳ Submitting…</>
          ) : whenMode === "now" ? (
            <>📞 Call now</>
          ) : (
            <>📅 Schedule</>
          )}
        </button>
      </div>
    </form>
  );
}

function isValidNumber(s: string): boolean {
  const digits = s.replace(/[^\d]/g, "");
  // Korean mobile/landline: 9-11 digits, accept international +82 too
  return digits.length >= 9 && digits.length <= 13;
}

function buildScriptPreview(
  draft: OutboundCallDraft,
  reasons: VoiceOutboundReason[],
  language: Lang,
): string {
  const reason = reasons.find((r) => r.id === draft.reason);
  if (!reason) return "";
  const template = pickTemplate(reason.scriptTemplate, language);
  if (!template) return "";
  return fillTemplate(template, draft);
}

function pickTemplate(t: { en?: string; ko?: string }, language: Lang): string | undefined {
  if (language === "ko") return t.ko ?? t.en;
  if (language === "en") return t.en ?? t.ko;
  return t.ko ?? t.en;
}

function labelFor(r: VoiceOutboundReason, language: Lang): string {
  if (language === "ko") return r.label.ko ?? r.label.en ?? r.id;
  if (language === "en") return r.label.en ?? r.label.ko ?? r.id;
  return r.label.ko ?? r.label.en ?? r.id;
}

function fillTemplate(template: string, draft: OutboundCallDraft): string {
  const name = draft.callerName || (template.includes("{name}") ? "고객님" : "");
  let out = template.replace(/\{name\}/g, name);
  if (draft.context) {
    for (const [k, v] of Object.entries(draft.context)) {
      out = out.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
    }
  }
  return out;
}
