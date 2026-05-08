"use client";

/**
 * DesktopUpdater — auto-update flow for the Tauri desktop app.
 *
 * Runs only when the app is running inside Tauri (web build = no-op).
 * On startup it checks the GitHub Releases endpoint for a newer signed
 * binary; if one exists, it shows a popup with the release notes. User
 * clicks "Update now" → downloads + verifies + installs + relaunches.
 *
 * This complements (does not replace) `<UpdateBanner>`, which announces
 * web deploys on every visit. UpdateBanner is for "the website changed",
 * DesktopUpdater is for "your installed app needs to be replaced".
 */

import { useEffect, useState } from "react";

interface UpdateState {
  status: "idle" | "checking" | "available" | "downloading" | "installing" | "error";
  version?: string;
  releaseNotes?: string;
  progress?: { downloaded: number; total: number };
  error?: string;
}

function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export default function DesktopUpdater() {
  const [state, setState] = useState<UpdateState>({ status: "idle" });
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;
    (async () => {
      try {
        setState({ status: "checking" });
        const { check } = await import("@tauri-apps/plugin-updater");
        const update = await check();
        if (cancelled) return;
        if (update) {
          setState({
            status: "available",
            version: update.version,
            releaseNotes: update.body || "(no release notes)",
          });
          // Stash the update object on window so the click handler can use it
          (window as unknown as Record<string, unknown>).__pendingUpdate = update;
        } else {
          setState({ status: "idle" });
        }
      } catch (e: unknown) {
        if (cancelled) return;
        // Silent failure — the app still works; just no update prompt this session.
        // Common causes: offline, GitHub release not yet propagated, signature mismatch.
        // eslint-disable-next-line no-console
        console.warn("[DesktopUpdater] check failed:", e);
        setState({ status: "idle" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function applyUpdate() {
    const update = (window as unknown as Record<string, unknown>).__pendingUpdate as
      | { downloadAndInstall: (cb: (event: DownloadEvent) => void) => Promise<void> }
      | undefined;
    if (!update) return;
    setState((s) => ({ ...s, status: "downloading", progress: { downloaded: 0, total: 0 } }));
    let downloaded = 0;
    let total = 0;
    try {
      await update.downloadAndInstall((event) => {
        switch (event.event) {
          case "Started":
            total = event.data?.contentLength ?? 0;
            setState((s) => ({ ...s, progress: { downloaded: 0, total } }));
            break;
          case "Progress":
            downloaded += event.data?.chunkLength ?? 0;
            setState((s) => ({ ...s, progress: { downloaded, total } }));
            break;
          case "Finished":
            setState((s) => ({ ...s, status: "installing" }));
            break;
        }
      });
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e);
      setState({ status: "error", error: message });
    }
  }

  if (!isTauri()) return null;
  if (dismissed) return null;
  if (state.status !== "available" && state.status !== "downloading" && state.status !== "installing" && state.status !== "error") {
    return null;
  }

  const progressPct =
    state.progress && state.progress.total > 0
      ? Math.min(100, Math.round((state.progress.downloaded / state.progress.total) * 100))
      : 0;

  return (
    <div className="fixed bottom-4 right-4 z-[100] w-[380px] bg-white rounded-xl shadow-2xl border border-blue-300 overflow-hidden">
      {/* Blue accent bar */}
      <div className="h-1 bg-blue-500" />

      <div className="p-4">
        <div className="flex items-start justify-between mb-2">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-blue-50 flex items-center justify-center">
              <span className="text-blue-600 text-[14px]">⬆</span>
            </div>
            <div>
              <h3 className="text-[14px] font-semibold text-gray-900">
                {state.status === "available" && "Update available"}
                {state.status === "downloading" && "Downloading update"}
                {state.status === "installing" && "Installing"}
                {state.status === "error" && "Update failed"}
              </h3>
              {state.version && (
                <p className="text-[11px] text-gray-400">VIP Agent v{state.version}</p>
              )}
            </div>
          </div>
          {state.status === "available" && (
            <button
              onClick={() => setDismissed(true)}
              className="text-gray-400 hover:text-gray-700 text-[16px]"
              aria-label="Dismiss"
            >
              ×
            </button>
          )}
        </div>

        {state.status === "available" && state.releaseNotes && (
          <div className="text-[12px] text-gray-600 max-h-[140px] overflow-y-auto whitespace-pre-line mb-3 bg-gray-50 rounded p-2">
            {state.releaseNotes}
          </div>
        )}

        {(state.status === "downloading" || state.status === "installing") && (
          <div className="mb-3">
            <div className="h-2 bg-gray-200 rounded overflow-hidden">
              <div
                className="h-full bg-blue-500 transition-all duration-200"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <p className="text-[11px] text-gray-500 mt-1">
              {state.status === "downloading"
                ? `${progressPct}%${state.progress && state.progress.total > 0 ? ` (${formatBytes(state.progress.downloaded)} / ${formatBytes(state.progress.total)})` : ""}`
                : "Restarting…"}
            </p>
          </div>
        )}

        {state.status === "error" && (
          <div className="text-[12px] text-red-600 mb-3 bg-red-50 rounded p-2">
            {state.error || "Unknown error"}
          </div>
        )}

        {state.status === "available" && (
          <div className="flex gap-2">
            <button
              onClick={() => setDismissed(true)}
              className="flex-1 py-2 rounded-lg bg-gray-100 text-gray-700 text-[12px] font-medium hover:bg-gray-200 transition-colors"
            >
              Skip
            </button>
            <button
              onClick={applyUpdate}
              className="flex-1 py-2 rounded-lg bg-blue-600 text-white text-[12px] font-medium hover:bg-blue-700 transition-colors"
            >
              Update now
            </button>
          </div>
        )}

        {state.status === "error" && (
          <button
            onClick={() => setDismissed(true)}
            className="w-full py-2 rounded-lg bg-gray-900 text-white text-[12px] font-medium hover:bg-gray-800 transition-colors"
          >
            Close
          </button>
        )}
      </div>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface DownloadEvent {
  event: "Started" | "Progress" | "Finished";
  data?: { contentLength?: number; chunkLength?: number };
}
