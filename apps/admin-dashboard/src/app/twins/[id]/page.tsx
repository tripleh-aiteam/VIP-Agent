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
import { useLanguage } from "../../../components/i18n";

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

type T = (ko: string, en: string) => string;
// Localized labels resolved at render with the active t(). MODE_BADGES/STATUS_COLORS
// stay as plain style maps; these provide the translated display text.
const MODE_LABELS: Record<string, (t: T) => string> = {
  shadow: (t) => t("섀도우", "Shadow"),
  active: (t) => t("활성", "Active"),
  handoff: (t) => t("핸드오프", "Handoff"),
};
const STATUS_LABELS: Record<string, (t: T) => string> = {
  working: (t) => t("작업 중", "Working"),
  online: (t) => t("온라인", "Online"),
  idle: (t) => t("대기 중", "Idle"),
  in_meeting: (t) => t("회의 중", "In meeting"),
  offline: (t) => t("오프라인", "Offline"),
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
  const { t } = useLanguage();
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
        if (!res.ok) throw new Error(res.status === 404 ? t("트윈을 찾을 수 없습니다", "Twin not found") : t(`불러오기 실패 (${res.status})`, `Failed (${res.status})`));
        const twinData: Twin = await res.json();
        if (cancelled) return;
        setTwin(twinData);

        // Mint a short-lived, server-signed embed token (authorized by the boss
        // session). The portal/backend verify it — nothing sensitive in the URL.
        const mint = await fetch(`${API}/auth/embed-token`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ twin_id: twinId }),
        });
        if (!mint.ok) {
          throw new Error(
            mint.status === 403 ? t("현재 세션으로는 트윈 포털을 열 수 없습니다 — 관리자로 로그인하세요.", "Your session can't open twin portals — sign in as an admin.")
            : mint.status === 503 ? t("서버에 트윈 포털이 아직 설정되지 않았습니다 (서명 시크릿 누락).", "Twin portal isn't configured on the server yet (missing signing secret).")
            : t(`트윈 포털을 인증할 수 없습니다 (${mint.status}).`, `Could not authorize the twin portal (${mint.status}).`)
          );
        }
        const { token } = await mint.json();
        if (cancelled) return;
        setEmbedUrl(`${portalBase()}/embed?t=${encodeURIComponent(token)}`);
      } catch (e: any) {
        if (!cancelled) setError(e.message || t("트윈을 불러오지 못했습니다", "Failed to load twin"));
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
        <Link href="/twins" className="text-blue-600 text-[13px] hover:underline">{t("← 전체 트윈으로 돌아가기", "← Back to all twins")}</Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[calc(100vh-110px)] min-h-[640px] rounded-xl overflow-hidden border border-[var(--card-border)]" style={{ boxShadow: "var(--shadow-sm)" }}>
      {/* Thin VIP header */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[var(--card-border)] bg-[var(--card-bg)] shrink-0">
        <button onClick={() => router.push("/twins")} className="text-[13px] text-[var(--text-muted)] hover:text-[var(--text-primary)] flex items-center gap-1 shrink-0">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" /></svg>
          {t("전체 트윈", "All Twins")}
        </button>
        <div className="w-px h-5 bg-[var(--card-border)]" />
        {twin && (
          <>
            <div className="w-7 h-7 rounded-lg flex items-center justify-center text-white font-bold text-[11px] shrink-0" style={{ backgroundColor: getAvatarColor(twin.name) }}>
              {getInitials(twin.name)}
            </div>
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-[14px] font-semibold text-[var(--text-primary)] truncate">{twin.name}</span>
              <span className={`w-2 h-2 rounded-full shrink-0 ${STATUS_COLORS[twin.status] || "bg-gray-400"}`} title={STATUS_LABELS[twin.status]?.(t) || twin.status} />
              <span className="text-[12px] text-[var(--text-muted)] hidden sm:inline">{twin.role}</span>
              <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium hidden sm:inline ${MODE_BADGES[twin.mode]?.bg || "bg-gray-100 text-gray-700"}`}>
                {MODE_LABELS[twin.mode]?.(t) || twin.mode}
              </span>
            </div>
            <div className="ml-auto flex items-center gap-2 shrink-0">
              {twin.mode !== "active" && (
                <button onClick={() => switchMode("active")} disabled={busy} className="px-2.5 py-1.5 bg-green-50 text-green-700 rounded-lg text-[11px] font-medium hover:bg-green-100 disabled:opacity-50">{t("활성화", "Activate")}</button>
              )}
              {twin.mode !== "shadow" && (
                <button onClick={() => switchMode("shadow")} disabled={busy} className="px-2.5 py-1.5 bg-gray-50 text-gray-700 rounded-lg text-[11px] font-medium hover:bg-gray-100 disabled:opacity-50">{t("섀도우", "Shadow")}</button>
              )}
              <Link href={`/twins?edit=${twin.id}`} className="px-2.5 py-1.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] text-[var(--text-secondary)] rounded-lg text-[11px] font-medium hover:bg-[var(--bg-hover)]">{t("편집", "Edit")}</Link>
            </div>
          </>
        )}
      </div>

      {/* Embedded Twin Portal */}
      <div className="flex-1 min-h-0 bg-[var(--bg-app)]">
        {embedUrl ? (
          <iframe
            src={embedUrl}
            title={twin?.name ? t(`${twin.name} — 트윈 포털`, `${twin.name} — Twin Portal`) : t("트윈 포털", "Twin Portal")}
            className="w-full h-full border-0"
            allow="clipboard-write; microphone"
          />
        ) : (
          <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[13px]">{t("트윈 포털을 여는 중…", "Opening twin portal…")}</div>
        )}
      </div>
    </div>
  );
}
