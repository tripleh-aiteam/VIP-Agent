"use client";

/**
 * Admin embed entry — lets the VIP boss dashboard open ANY twin's portal
 * without a worker password.
 *
 * The boss dashboard iframes:
 *   /embed?twin_id=<id>&as=<bossEmail>&name=<twinName>&k=<ADMIN_TOKEN>
 *
 * We validate the shared token, seed the same localStorage keys the normal
 * worker login writes, then render the real Dashboard. The backend authorizes
 * every twin because `as` is an admin/operator email (see _check_twin_access).
 *
 * Worker login (/) is untouched — workers still sign in with email + password
 * and only reach their own twin.
 *
 * NOTE: the token is a soft gate (NEXT_PUBLIC, visible client-side). It blocks
 * casual access; it is not a hard security boundary. The platform's twin access
 * is already permissive in "boss mode". Harden to a server-minted token later.
 */

import { useEffect, useState } from "react";
import { DashboardView as Dashboard } from "../dashboard/DashboardView";

// Fail closed: no hardcoded fallback. If unset, the embed refuses to render.
const EMBED_TOKEN = process.env.NEXT_PUBLIC_TWIN_EMBED_TOKEN || "";

export default function EmbedPage() {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");

  useEffect(() => {
    try {
      // Frame-only: refuse direct navigation. This page is meant to be iframed
      // by the VIP dashboard (whose origin is constrained by the CSP header).
      if (window.top === window.self) { setMessage("This page must be opened from the VIP dashboard."); setState("error"); return; }
      if (!EMBED_TOKEN) { setMessage("Embed is not configured (missing token)."); setState("error"); return; }

      const sp = new URLSearchParams(window.location.search);
      const twinId = sp.get("twin_id");
      const as = sp.get("as") || "boss@vip";
      const k = sp.get("k") || "";
      const name = sp.get("name") || "Twin";

      if (!twinId) { setMessage("Missing twin_id"); setState("error"); return; }
      if (k !== EMBED_TOKEN) { setMessage("Unauthorized embed"); setState("error"); return; }

      localStorage.setItem("twin_id", twinId);
      localStorage.setItem("twin_name", name);
      localStorage.setItem("worker_name", "VIP Boss");
      localStorage.setItem("worker_email", as);   // admin email → backend grants any twin
      localStorage.setItem("twin_token", "boss-embed");
      localStorage.setItem("vip_embed", "1");      // marks boss-embedded session
      setState("ready");
    } catch (e: any) {
      setMessage(e?.message || "Failed to initialize embed");
      setState("error");
    }
  }, []);

  if (state === "error") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg-app)] px-4">
        <div className="text-center">
          <div className="text-[40px] mb-3">🔒</div>
          <div className="text-[15px] font-semibold text-[var(--text-primary)] mb-1">Cannot open twin</div>
          <div className="text-[13px] text-[var(--text-muted)]">{message}</div>
        </div>
      </div>
    );
  }

  if (state === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg-app)]">
        <div className="text-[var(--text-muted)] text-[13px]">Opening twin portal…</div>
      </div>
    );
  }

  // Boss-embedded: render the real worker dashboard. Logout just reloads the
  // embed (the boss closes it from the dashboard, not from inside).
  return <Dashboard onLogout={() => window.location.reload()} />;
}
