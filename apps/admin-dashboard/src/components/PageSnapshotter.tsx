"use client";

/**
 * PageSnapshotter — captures the user's current page DOM as text and
 * persists it to localStorage under `page-ctx:<agentId>`. Mounted once
 * in the layout so EVERY page (Dashboard, Twins, Portfolio, etc.) is
 * snapshotted as the user navigates.
 *
 * Why: the floating AssistantCard could already read the current page,
 * but the full-page ChatWorkspace on /chatbot only sees the chat UI —
 * which made the two surfaces disagree (floating bar answered from
 * Dashboard's 55,972,248,247 won; ChatWorkspace answered from a stale
 * uploaded xlsx with 1,437,175,541 won). With this snapshotter, both
 * surfaces fall back to the same cached snapshot of the most recent
 * useful page → consistent answers across both UIs.
 *
 * Rules:
 *  - Only saves when the captured text is "meaningful" (>500 chars) so
 *    a thin page like /chatbot itself doesn't overwrite a rich snapshot
 *    of /dashboard.
 *  - Strips Assistant's own UI (anything tagged data-assistant-ui) so
 *    the snapshot doesn't include the assistant's own messages.
 *  - Caps at ~14000 chars to keep the LLM context window manageable.
 *  - 750ms debounce so we capture AFTER the page has finished rendering
 *    (React Suspense / streaming).
 */

import { useEffect } from "react";
import { usePathname } from "next/navigation";

interface Props {
  agentId: string;
}

export default function PageSnapshotter({ agentId }: Props) {
  const pathname = usePathname();

  useEffect(() => {
    if (typeof document === "undefined" || typeof window === "undefined") return;
    let cancelled = false;

    const snap = () => {
      if (cancelled) return;
      try {
        const root = (document.querySelector("main") as HTMLElement | null)
          || (document.body as HTMLElement | null);
        if (!root) return;
        const clone = root.cloneNode(true) as HTMLElement;
        clone.querySelectorAll("[data-assistant-ui], [data-llm-picker], [data-download-menu]").forEach((n) => n.remove());
        clone.querySelectorAll("script, style, svg path, noscript").forEach((n) => n.remove());
        let text = (clone.innerText || clone.textContent || "").trim();
        text = text.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n");
        if (text.length > 14000) {
          text = text.slice(0, 14000) + "\n…[truncated]";
        }
        // Skip thin pages — /chatbot itself returns very little after
        // stripping the assistant UI. We don't want to clobber a rich
        // snapshot of /dashboard with an empty one when the user
        // navigates to chatbot.
        if (text.length < 500) return;
        const payload = {
          text,
          ts: Date.now(),
          path: pathname || "",
        };
        try { window.localStorage.setItem(`page-ctx:${agentId}`, JSON.stringify(payload)); } catch {}
      } catch {}
    };

    // Capture after the page has had a chance to render
    const t1 = window.setTimeout(snap, 750);
    // And again after 2.5s to catch async data loads
    const t2 = window.setTimeout(snap, 2500);

    return () => {
      cancelled = true;
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, [pathname, agentId]);

  return null;
}
