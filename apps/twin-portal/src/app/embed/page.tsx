"use client";

/**
 * Admin embed entry — lets the VIP boss dashboard open ANY twin's portal.
 *
 * The dashboard mints a short-lived, server-signed token (POST /auth/embed-token,
 * authorized by the boss's admin session) and iframes:
 *
 *     /embed?t=<signedToken>
 *
 * The token is opaque and self-describing: it is signed by the orchestrator
 * (EMBED_SIGNING_SECRET, server-only) and bound to {twin_id, principal, exp}.
 * We decode it here ONLY to know which twin to render; all real authorization
 * happens on the backend, which re-verifies the signature on every twin API
 * call (sent as the X-Embed-Token header). Nothing sensitive is trusted from
 * the client, and there is no shared secret in the browser bundle.
 *
 * Worker login (/) is untouched — workers sign in with email + password and
 * reach only their own twin.
 */

import { useEffect, useState } from "react";
import { DashboardView as Dashboard } from "../dashboard/DashboardView";

/** Decode the (unverified) payload of the signed token to learn the twin id. */
function decodePayload(token: string): { tid?: string; sub?: string } | null {
  try {
    const body = token.split(".")[0];
    const b64 = body.replace(/-/g, "+").replace(/_/g, "/");
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    return JSON.parse(atob(b64 + pad));
  } catch {
    return null;
  }
}

export default function EmbedPage() {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");

  useEffect(() => {
    try {
      // Frame-only: this page is meant to be iframed by the VIP dashboard
      // (whose origin is constrained by the CSP frame-ancestors header).
      if (window.top === window.self) {
        setMessage("This page must be opened from the VIP dashboard.");
        setState("error");
        return;
      }

      const t = new URLSearchParams(window.location.search).get("t") || "";
      if (!t) { setMessage("Missing embed token"); setState("error"); return; }

      const payload = decodePayload(t);
      if (!payload?.tid) { setMessage("Invalid embed token"); setState("error"); return; }

      // Seed the session the DashboardView expects. Authorization is the signed
      // token (sent as X-Embed-Token by the portal's api layer), NOT the email.
      localStorage.setItem("twin_id", payload.tid);
      localStorage.setItem("embed_token", t);
      localStorage.setItem("worker_email", payload.sub || "");  // attribution only
      localStorage.setItem("worker_name", "VIP Boss");
      localStorage.setItem("twin_token", "");                    // unused in embed
      localStorage.setItem("vip_embed", "1");
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

  return <Dashboard onLogout={() => window.location.reload()} />;
}
