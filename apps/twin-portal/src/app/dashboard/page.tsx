"use client";

/**
 * Route entry for /dashboard.
 *
 * Next.js page components may only receive params/searchParams, so the real
 * (prop-taking) dashboard UI lives in DashboardView. This no-prop wrapper
 * renders it with a default logout. Workers who log in at the root are routed
 * through here; the boss embed (/embed) renders DashboardView directly.
 */

import { DashboardView } from "./DashboardView";

export default function DashboardPage() {
  return (
    <DashboardView
      onLogout={() => {
        try { localStorage.clear(); } catch {}
        if (typeof window !== "undefined") window.location.href = "/";
      }}
    />
  );
}
