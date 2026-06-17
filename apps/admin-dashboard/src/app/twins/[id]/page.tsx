"use client";

/**
 * Twin MONITORING view (boss side) — privacy-respecting.
 *
 * The boss sees WHETHER a twin is working and HOW trained it is — status, mode,
 * readiness %, knowledge COUNTS, activity heartbeat, and task output. The boss
 * does NOT see the twin's knowledge CONTENT or chats: those are private to the
 * worker (enforced by the backend owner-only "privacy wall"). Workers teach
 * their own twin in the Twin Portal with their own login.
 */

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { API, authHeaders } from "../../../components/api";
import { useLanguage } from "../../../components/i18n";

interface Twin {
  id: string; name: string; role: string; department: string | null;
  mode: string; permission_level: string; status: string;
  personality_prompt: string | null; skills: string[];
  created_at: string | null; updated_at: string | null;
}

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

export default function TwinMonitorPage() {
  const { t } = useLanguage();
  const params = useParams();
  const router = useRouter();
  const twinId = String(params?.id || "");

  const [twin, setTwin] = useState<Twin | null>(null);
  const [intel, setIntel] = useState<any>(null);
  const [activity, setActivity] = useState<any[]>([]);
  const [tasks, setTasks] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!twinId) return;
    let cancel = false;
    (async () => {
      try {
        const res = await fetch(`${API}/twins/${twinId}`, { headers: authHeaders() });
        if (!res.ok) throw new Error(res.status === 404 ? t("트윈을 찾을 수 없습니다", "Twin not found") : `Failed (${res.status})`);
        if (cancel) return;
        setTwin(await res.json());
        // Monitoring-only endpoints (counts + heartbeat — NOT content):
        fetch(`${API}/twins/${twinId}/intelligence`, { headers: authHeaders() }).then(r => r.ok && r.json()).then(d => d && !cancel && setIntel(d)).catch(() => {});
        fetch(`${API}/twins/${twinId}/activity?limit=20`, { headers: authHeaders() }).then(r => r.ok && r.json()).then(d => d && !cancel && setActivity(d)).catch(() => {});
        fetch(`${API}/twins/${twinId}/tasks`, { headers: authHeaders() }).then(r => r.ok && r.json()).then(d => d && !cancel && setTasks(d)).catch(() => {});
      } catch (e: any) { if (!cancel) setError(e.message); }
    })();
    return () => { cancel = true; };
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

  function timeAgo(iso: string | null) {
    if (!iso) return "—";
    const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
    if (m < 1) return t("방금", "just now");
    if (m < 60) return t(`${m}분 전`, `${m}m ago`);
    const h = Math.floor(m / 60);
    if (h < 24) return t(`${h}시간 전`, `${h}h ago`);
    return t(`${Math.floor(h / 24)}일 전`, `${Math.floor(h / 24)}d ago`);
  }

  if (error) return (
    <div className="p-6 max-w-2xl mx-auto text-center">
      <div className="text-[48px] mb-3">🤖</div>
      <div className="text-[16px] font-semibold mb-2 text-[var(--text-primary)]">{error}</div>
      <Link href="/twins" className="text-blue-600 text-[13px] hover:underline">{t("← 전체 트윈", "← All twins")}</Link>
    </div>
  );
  if (!twin) return <div className="p-6 text-center text-[var(--text-muted)]">{t("불러오는 중…", "Loading…")}</div>;

  const lastActive = activity[0]?.timestamp || null;
  const b = intel?.breakdown || {};

  return (
    <div className="p-2 md:p-4 max-w-[1100px] mx-auto">
      <button onClick={() => router.push("/twins")} className="text-[13px] text-[var(--text-muted)] hover:text-[var(--text-primary)] mb-3 flex items-center gap-1">
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" /></svg>
        {t("전체 트윈", "All Twins")}
      </button>

      {/* Header */}
      <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5 mb-4" style={{ boxShadow: "var(--shadow-sm)" }}>
        <div className="flex items-start gap-4 flex-wrap">
          <div className="w-14 h-14 rounded-2xl flex items-center justify-center text-white font-bold text-[20px] shrink-0" style={{ backgroundColor: getAvatarColor(twin.name) }}>{getInitials(twin.name)}</div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-[22px] font-semibold text-[var(--text-primary)]">{twin.name}</h1>
              <span className={`w-3 h-3 rounded-full ${STATUS_COLORS[twin.status] || "bg-gray-400"}`} />
              <span className="text-[12px] text-[var(--text-muted)]">{twin.status === "working" ? t("작업 중", "working") : twin.status === "idle" ? t("대기 중", "idle") : twin.status}</span>
            </div>
            <p className="text-[14px] text-[var(--text-muted)] mt-0.5">{twin.role}{twin.department ? ` · ${twin.department}` : ""}</p>
            <div className="flex items-center gap-2 mt-2 flex-wrap text-[11px]">
              <span className="px-2.5 py-0.5 rounded-full bg-gray-100 text-gray-700">{twin.mode}</span>
              <span className="px-2.5 py-0.5 rounded-full bg-gray-50 text-gray-600 border border-gray-200">{twin.permission_level}</span>
              {intel?.intelligence_pct != null && <span className="px-2.5 py-0.5 rounded-full bg-blue-50 text-blue-700">{t("준비도", "Readiness")} {intel.intelligence_pct}%</span>}
              <span className="text-[var(--text-muted)]">· {t("마지막 활동", "last active")} {timeAgo(lastActive)}</span>
            </div>
          </div>
          <div className="flex gap-2 flex-wrap">
            {twin.mode !== "active" && <button onClick={() => switchMode("active")} disabled={busy} className="px-3 py-2 bg-green-50 text-green-700 rounded-lg text-[12px] font-medium hover:bg-green-100 disabled:opacity-50">{t("활성화", "Activate")}</button>}
            {twin.mode !== "shadow" && <button onClick={() => switchMode("shadow")} disabled={busy} className="px-3 py-2 bg-gray-50 text-gray-700 rounded-lg text-[12px] font-medium hover:bg-gray-100 disabled:opacity-50">{t("섀도우", "Shadow")}</button>}
            <Link href={`/twins?edit=${twin.id}`} className="px-3 py-2 bg-[var(--bg-secondary)] border border-[var(--card-border)] text-[var(--text-secondary)] rounded-lg text-[12px] font-medium hover:bg-[var(--bg-hover)]">{t("편집", "Edit")}</Link>
          </div>
        </div>
      </div>

      {/* Privacy notice */}
      <div className="flex items-center gap-2 mb-4 px-4 py-2.5 rounded-xl bg-amber-50 border border-amber-200 text-[12px] text-amber-800">
        🔒 {t("이 트윈의 지식과 대화는 담당 직원에게만 비공개로 보입니다. 여기서는 상태와 학습 진행도만 확인할 수 있습니다.",
              "This twin's knowledge and chats are private to its worker. Here you can see status and training progress only.")}
      </div>

      {/* Counts strip (numbers only — no content) */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 mb-4">
        {[
          { label: t("문서", "Docs"), value: b.documents },
          { label: t("규칙", "Rules"), value: b.decision_rules },
          { label: t("대화 학습", "Chat learned"), value: b.chat_learned },
          { label: t("교정", "Corrections"), value: b.corrections },
          { label: t("승인", "Approvals"), value: b.approvals },
          { label: t("완료 작업", "Tasks done"), value: b.tasks_completed },
        ].map(s => (
          <div key={s.label} className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] px-3 py-3 text-center" style={{ boxShadow: "var(--shadow-sm)" }}>
            <div className="text-[20px] font-bold text-[var(--text-primary)]">{s.value ?? 0}</div>
            <div className="text-[10px] text-[var(--text-muted)]">{s.label}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Activity heartbeat (action type + time — NOT descriptions) */}
        <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
          <h3 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3">{t("활동 (학습 중인지)", "Activity (is it learning?)")}</h3>
          {activity.length === 0 ? <div className="text-[12px] text-[var(--text-muted)] py-6 text-center">{t("아직 활동 없음", "No activity yet")}</div> :
            <div className="space-y-2.5">
              {activity.slice(0, 12).map(a => (
                <div key={a.id} className="flex items-center gap-3">
                  <span className="w-2 h-2 rounded-full bg-blue-400 shrink-0" />
                  <span className="text-[12px] text-[var(--text-primary)] capitalize">{(a.action_type || "").replace(/_/g, " ")}</span>
                  <span className="text-[10px] text-[var(--text-muted)] ml-auto">{timeAgo(a.timestamp)}</span>
                </div>
              ))}
            </div>}
        </div>

        {/* Tasks (work output for the company — title + status, not content) */}
        <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
          <h3 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3">{t("작업", "Tasks")} ({tasks.length})</h3>
          {tasks.length === 0 ? <div className="text-[12px] text-[var(--text-muted)] py-6 text-center">{t("작업 없음", "No tasks")}</div> :
            <div className="space-y-2">
              {tasks.slice(0, 12).map(tk => (
                <div key={tk.id} className="flex items-center justify-between gap-2 border border-[var(--card-border)] rounded-lg px-3 py-2">
                  <span className="text-[12px] text-[var(--text-primary)] truncate">{tk.title}</span>
                  <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium shrink-0 ${
                    tk.status === "completed" ? "bg-green-50 text-green-600" : tk.status === "in_progress" ? "bg-blue-50 text-blue-600" :
                    tk.status === "blocked" ? "bg-red-50 text-red-600" : "bg-gray-50 text-gray-500"}`}>{tk.status}</span>
                </div>
              ))}
            </div>}
        </div>
      </div>
    </div>
  );
}
