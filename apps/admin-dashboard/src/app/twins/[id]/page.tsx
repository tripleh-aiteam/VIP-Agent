"use client";

/**
 * Twin "home" — opens a single twin from the VIP side.
 *
 * The body is the REAL Twin Portal (apps/twin-portal) embedded in an iframe,
 * scoped to this twin via the admin /embed entry. The boss gets the full
 * Home / Teach / Chat / Review / Messages / Reports experience with full
 * control. A thin VIP header on top provides back-nav + Activate/Shadow/Edit.
 *
 * Workers still use the portal's own email+password login for their own twin;
 * this admin path is an overlay, not a replacement.
 */

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { API, authHeaders } from "../../../components/api";

interface Twin {
  id: string;
  name: string;
  role: string;
  department: string | null;
  mode: string;
  permission_level: string;
  status: string;
}

const MODE_BADGES: Record<string, { bg: string; text: string }> = {
  shadow: { bg: "bg-gray-100 text-gray-700", text: "Shadow" },
  active: { bg: "bg-green-100 text-green-700", text: "Active" },
  handoff: { bg: "bg-amber-100 text-amber-700", text: "Handoff" },
};
const STATUS_COLORS: Record<string, string> = {
  working: "bg-green-500", online: "bg-green-400", idle: "bg-yellow-400",
  in_meeting: "bg-blue-500", offline: "bg-gray-400",
};

const AVATAR_COLORS = ["#6366f1", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#3b82f6", "#ef4444", "#14b8a6"];
function getAvatarColor(name: string) {
  let hash = 0;
  for (let i = 0; i < (name || "").length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}
function getInitials(name: string) {
  return (name || "?").split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
}

/** Resolve the Twin Portal base URL: env wins, localhost in dev, prod URL otherwise. */
function portalBase(): string {
  const env = process.env.NEXT_PUBLIC_TWIN_PORTAL_URL;
  if (env && /^https?:\/\//.test(env)) return env.replace(/\/$/, "");
  if (typeof window !== "undefined" && (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")) {
    return "http://localhost:3001";
  }
  // Prod default — updated to the deployed portal URL via env after the Vercel project exists.
  return "https://vip-twin-portal.vercel.app";
}

export default function TwinHomePage() {
  const params = useParams();
  const router = useRouter();
  const twinId = String(params?.id || "");

  const [twin, setTwin] = useState<Twin | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [embedUrl, setEmbedUrl] = useState<string>("");

  useEffect(() => {
    if (!twinId) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API}/twins/${twinId}`, { headers: authHeaders() });
        if (!res.ok) throw new Error(res.status === 404 ? "Twin not found" : `Failed (${res.status})`);
        const t: Twin = await res.json();
        if (cancelled) return;
        setTwin(t);

        // Mint a short-lived, server-signed embed token (authorized by the boss
        // session). The portal/backend verify it — nothing sensitive in the URL.
        const mint = await fetch(`${API}/auth/embed-token`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ twin_id: twinId }),
        });
        if (!mint.ok) {
          throw new Error(
            mint.status === 403 ? "Your session can't open twin portals — sign in as an admin."
            : mint.status === 503 ? "Twin portal isn't configured on the server yet (missing signing secret)."
            : `Could not authorize the twin portal (${mint.status}).`
          );
        }
        const { token } = await mint.json();
        if (cancelled) return;
        setEmbedUrl(`${portalBase()}/embed?t=${encodeURIComponent(token)}`);
      } catch (e: any) {
        if (!cancelled) setError(e.message || "Failed to load twin");
      }
    })();
    return () => { cancelled = true; };
  }, [twinId]);

  async function switchMode(mode: string) {
    if (!twin) return;
    setBusy(true);
    try {
      await fetch(`${API}/twins/${twin.id}/mode`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ mode }),
      });
      setTwin({ ...twin, mode });
    } finally { setBusy(false); }
  }

  if (error) {
    return (
      <div className="p-6 max-w-2xl mx-auto text-center">
        <div className="text-[48px] mb-3">🤖</div>
        <div className="text-[var(--text-primary)] text-[16px] font-semibold mb-2">{error}</div>
        <Link href="/twins" className="text-blue-600 text-[13px] hover:underline">← Back to all twins</Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[calc(100vh-110px)] min-h-[640px] rounded-xl overflow-hidden border border-[var(--card-border)]" style={{ boxShadow: "var(--shadow-sm)" }}>
      {/* Thin VIP header */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[var(--card-border)] bg-[var(--card-bg)] shrink-0">
        <button onClick={() => router.push("/twins")} className="text-[13px] text-[var(--text-muted)] hover:text-[var(--text-primary)] flex items-center gap-1 shrink-0">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" /></svg>
          All Twins
        </button>
        <div className="w-px h-5 bg-[var(--card-border)]" />
        {twin && (
          <>
            <div className="w-7 h-7 rounded-lg flex items-center justify-center text-white font-bold text-[11px] shrink-0" style={{ backgroundColor: getAvatarColor(twin.name) }}>
              {getInitials(twin.name)}
            </div>
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-[14px] font-semibold text-[var(--text-primary)] truncate">{twin.name}</span>
              <span className={`w-2 h-2 rounded-full shrink-0 ${STATUS_COLORS[twin.status] || "bg-gray-400"}`} title={twin.status} />
              <span className="text-[12px] text-[var(--text-muted)] hidden sm:inline">{twin.role}</span>
              <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium hidden sm:inline ${MODE_BADGES[twin.mode]?.bg || "bg-gray-100 text-gray-700"}`}>
                {MODE_BADGES[twin.mode]?.text || twin.mode}
              </span>
            </div>
            <div className="ml-auto flex items-center gap-2 shrink-0">
              {twin.mode !== "active" && (
                <button onClick={() => switchMode("active")} disabled={busy} className="px-2.5 py-1.5 bg-green-50 text-green-700 rounded-lg text-[11px] font-medium hover:bg-green-100 disabled:opacity-50">Activate</button>
              )}
              {twin.mode !== "shadow" && (
                <button onClick={() => switchMode("shadow")} disabled={busy} className="px-2.5 py-1.5 bg-gray-50 text-gray-700 rounded-lg text-[11px] font-medium hover:bg-gray-100 disabled:opacity-50">Shadow</button>
              )}
              <Link href={`/twins?edit=${twin.id}`} className="px-2.5 py-1.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] text-[var(--text-secondary)] rounded-lg text-[11px] font-medium hover:bg-[var(--bg-hover)]">Edit</Link>
            </div>
          </>
        )}
      </div>

      {/* Embedded Twin Portal */}
      <div className="flex-1 min-h-0 bg-[var(--bg-app)]">
        {embedUrl ? (
          <iframe
            src={embedUrl}
            title={twin?.name ? `${twin.name} — Twin Portal` : "Twin Portal"}
            className="w-full h-full border-0"
            allow="clipboard-write; microphone"
          />
        ) : (
          <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[13px]">Opening twin portal…</div>
        )}
      </div>
    </div>
  );
}
