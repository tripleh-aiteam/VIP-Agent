"use client";

/**
 * IncomingCallToast — bottom-left floating notification that appears
 * when a real call is in progress on any page. Lets the operator
 * "watch live" or dismiss without taking action.
 *
 * FRAMEWORK-AGNOSTIC
 * ------------------
 * This component intentionally does NOT depend on Next.js (or any
 * router). The host app supplies:
 *
 *   - `call`: the active CallEvent, or null when nothing is ringing.
 *     Comes from the WebSocket subscription / mock data — managed by
 *     the dashboard or layout that mounts this toast.
 *   - `onWatchLive`: callback the host wires to its own navigation
 *     (Next.js router.push, React Router, plain window.location, etc.).
 *   - `suppressed?`: pass `true` from pages where the toast shouldn't
 *     render (typically the /calls page itself).
 */

import { useEffect, useState } from "react";
import type { CallEvent } from "./types";

interface Props {
  /** The active call. Null = nothing to show, hide the toast. */
  call: CallEvent | null;
  /** Fires when the operator clicks "Watch live →" */
  onWatchLive?: (call: CallEvent) => void;
  /** When true, the toast renders nothing — use on /calls and similar pages */
  suppressed?: boolean;
}

export function IncomingCallToast({ call, onWatchLive, suppressed }: Props) {
  const [dismissed, setDismissed] = useState(false);
  const [nowMs, setNowMs] = useState(Date.now());

  // Re-show the toast on a new call (reset dismiss state when the id changes)
  useEffect(() => {
    setDismissed(false);
  }, [call?.id]);

  // Tick the duration display every second while a call is active
  useEffect(() => {
    if (!call) return;
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, [call]);

  if (suppressed || !call || dismissed) return null;

  const durationSec = Math.floor((nowMs - call.startedAt) / 1000);
  const callerLabel = call.caller.name || "Unknown caller";

  return (
    <div className="fixed bottom-4 left-4 z-[60] w-[320px] bg-white rounded-xl shadow-2xl border border-red-200 overflow-hidden animate-in slide-in-from-left">
      {/* Pulsing red bar */}
      <div className="h-1 bg-red-500 animate-pulse" />

      <div className="p-3.5">
        <div className="flex items-start gap-3">
          <div className="relative shrink-0">
            <div className="w-10 h-10 rounded-full bg-gradient-to-br from-red-500 to-pink-500 flex items-center justify-center text-white text-base font-semibold">
              {callerLabel.slice(0, 1)}
            </div>
            <span className="absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-red-500 animate-pulse border-2 border-white" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] font-bold text-red-500 uppercase tracking-wider">● Live now</span>
            </div>
            <div className="text-[13px] font-semibold text-gray-900 truncate mt-0.5">
              {callerLabel}
            </div>
            <div className="text-[11px] text-gray-500 font-mono">{call.caller.number}</div>
            <div className="text-[10px] text-gray-400 mt-0.5">
              AI handling · {formatDuration(durationSec)}
            </div>
          </div>
          <button
            onClick={() => setDismissed(true)}
            className="text-gray-400 hover:text-gray-700 text-lg leading-none"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>

        <div className="mt-3 flex gap-2">
          <button
            onClick={() => {
              onWatchLive?.(call);
              setDismissed(true);
            }}
            disabled={!onWatchLive}
            className="flex-1 px-3 py-1.5 rounded-lg bg-red-600 text-white text-[11px] font-medium hover:bg-red-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Watch live →
          </button>
          <button
            onClick={() => setDismissed(true)}
            className="px-3 py-1.5 rounded-lg bg-gray-100 text-gray-700 text-[11px] font-medium hover:bg-gray-200 transition-colors"
          >
            Later
          </button>
        </div>
      </div>
    </div>
  );
}

function formatDuration(totalSec: number): string {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}
